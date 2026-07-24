from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from harbor.environments.base import BaseEnvironment

from evaluation.common.isolation import AGENT_UID, AGENT_USER
from evaluation.common.network_isolation import (
    INFERENCE_RELAY_HOST,
    INFERENCE_RELAY_PORT,
    NETWORK_STATE_DIR,
    gateway_hosts,
    local_relay_base_url,
)

NETWORK_TOOLCHAIN_BIN = "/opt/codemem-network/bin"


class RestrictedAgentMixin:
    """Run agent tools without egress and inference through a root-only relay."""

    _network_isolation_active = False
    _inference_relay_url: str | None = None

    def network_gateway_urls(self) -> tuple[str, ...]:
        """Return the model API base URLs this adapter needs.

        Concrete adapters must make the gateway explicit so a missing or
        malformed endpoint fails closed instead of silently allowing egress.
        """
        raise NotImplementedError

    def inference_api_key(self) -> str:
        raise NotImplementedError

    def inference_auth_mode(self) -> str:
        raise NotImplementedError

    def inference_models(self) -> tuple[str, ...]:
        raise NotImplementedError

    def agent_relay_environment(self) -> dict[str, str]:
        raise NotImplementedError

    def inference_relay_url(self) -> str:
        if self._inference_relay_url is None:
            raise RuntimeError("The inference relay has not been activated")
        return self._inference_relay_url

    async def setup(self, environment: BaseEnvironment) -> None:
        await super().setup(environment)
        environment_type = environment.type()
        type_value = getattr(environment_type, "value", str(environment_type))
        if type_value != "docker":
            raise RuntimeError(
                "CodeMem agent network isolation currently requires local Docker"
            )
        await self._activate_network_isolation(environment)
        self._network_isolation_active = True

    async def _activate_network_isolation(
        self, environment: BaseEnvironment
    ) -> None:
        gateway_urls = self.network_gateway_urls()
        gateway_hosts(gateway_urls)
        if len(gateway_urls) != 1:
            raise ValueError("Exactly one model gateway URL is required per agent")
        upstream_url = gateway_urls[0]
        api_key = self.inference_api_key()
        if not api_key:
            raise ValueError("A model API key is required by the inference relay")
        auth_mode = self.inference_auth_mode()
        if auth_mode not in {"bearer", "x-api-key"}:
            raise ValueError(f"Unsupported inference relay auth mode: {auth_mode!r}")
        models = self.inference_models()
        if not models or any(not model for model in models):
            raise ValueError("At least one non-empty inference model is required")
        self._inference_relay_url = local_relay_base_url(upstream_url)

        relay_source = (
            Path(__file__).resolve().parents[1] / "common" / "inference_relay.py"
        )
        remote_source = "/tmp/codemem-inference-relay.py"
        await environment.upload_file(relay_source, remote_source)

        model_args = " ".join(
            f"--allow-model {shlex.quote(model)}" for model in models
        )
        state = shlex.quote(NETWORK_STATE_DIR)
        chain4 = "CODEMEM_AGENT_OUT"
        chain6 = "CODEMEM_AGENT_OUT6"
        command = f"""
set -euo pipefail
test -x {NETWORK_TOOLCHAIN_BIN}/iptables
test -x {NETWORK_TOOLCHAIN_BIN}/ip6tables
install -d -m 0700 -o root -g root {state}
install -m 0700 -o root -g root {remote_source} {state}/relay.py
rm -f {state}/ready
if [ -s {state}/relay.pid ] && kill -0 "$(cat {state}/relay.pid)" 2>/dev/null; then
  kill "$(cat {state}/relay.pid)"
fi
nohup python3 {state}/relay.py \
  --listen-host {INFERENCE_RELAY_HOST} \
  --listen-port {INFERENCE_RELAY_PORT} \
  --upstream-url {shlex.quote(upstream_url)} \
  --auth-mode {shlex.quote(auth_mode)} \
  --ready-file {state}/ready \
  {model_args} >>{state}/relay.log 2>&1 </dev/null &
echo $! >{state}/relay.pid
for _ in $(seq 1 50); do
  [ -f {state}/ready ] && break
  sleep 0.1
done
[ -f {state}/ready ]

IPTABLES={NETWORK_TOOLCHAIN_BIN}/iptables
IP6TABLES={NETWORK_TOOLCHAIN_BIN}/ip6tables
$IPTABLES -w -t nat -D OUTPUT -m owner --uid-owner {AGENT_UID} \
  -j CODEMEM_AGENT_NAT 2>/dev/null || true
$IPTABLES -w -t nat -F CODEMEM_AGENT_NAT 2>/dev/null || true
$IPTABLES -w -t nat -X CODEMEM_AGENT_NAT 2>/dev/null || true
$IPTABLES -w -N {chain4} 2>/dev/null || true
$IPTABLES -w -F {chain4}
$IPTABLES -w -A {chain4} -o lo -p tcp -d {INFERENCE_RELAY_HOST} \
  --dport {INFERENCE_RELAY_PORT} -j ACCEPT
$IPTABLES -w -A {chain4} -j REJECT
$IPTABLES -w -C OUTPUT -m owner --uid-owner {AGENT_UID} -j {chain4} \
  2>/dev/null || $IPTABLES -w -I OUTPUT 1 -m owner \
  --uid-owner {AGENT_UID} -j {chain4}

if [ -e /proc/net/if_inet6 ]; then
  $IP6TABLES -w -N {chain6} 2>/dev/null || true
  $IP6TABLES -w -F {chain6}
  $IP6TABLES -w -A {chain6} -j REJECT
  $IP6TABLES -w -C OUTPUT -m owner --uid-owner {AGENT_UID} -j {chain6} \
    2>/dev/null || $IP6TABLES -w -I OUTPUT 1 -m owner \
    --uid-owner {AGENT_UID} -j {chain6}
fi
"""
        await self.exec_as_root(
            environment,
            command=command,
            env={"CODEMEM_RELAY_API_KEY": api_key},
        )

    async def exec_as_agent(
        self,
        environment: BaseEnvironment,
        command: str,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> Any:
        merged_env = dict(env or {})
        if self._network_isolation_active:
            merged_env.update(self.agent_relay_environment())
        return await self._exec(
            environment,
            command,
            user=AGENT_USER,
            env=merged_env,
            cwd=cwd,
            timeout_sec=timeout_sec,
        )
