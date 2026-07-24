from __future__ import annotations

from typing import Any

from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment

from evaluation.agents.restricted import RestrictedAgentMixin
from evaluation.common.network_isolation import (
    INFERENCE_RELAY_DUMMY_KEY,
    LOOPBACK_DIRECT_ENV,
)


class MemoryCodex(RestrictedAgentMixin, Codex):
    """Persist Codex's learned-memory store across Harbor steps.

    Harbor 0.20 runs Codex with ``CODEX_HOME=/tmp/codex-home``, copies only
    ``sessions/`` into the agent logs, and then deletes the temporary home.
    This compatibility adapter enables the experimental memories feature and
    copies only ``memories/`` to the retained agent logs before that cleanup.
    Authentication remains in Harbor's separate temporary secrets directory.
    """

    MEMORY_LOG_DIR = "/logs/agent/memories"

    def __init__(self, *args, **kwargs):
        # Provider-side web search would bypass the container firewall.
        kwargs["web_search"] = "disabled"
        super().__init__(*args, **kwargs)

    def network_gateway_urls(self) -> tuple[str, ...]:
        return (
            self._get_env("CODEMEM_MODEL_GATEWAY_URL")
            or self._get_env("OPENAI_BASE_URL")
            or "https://api.openai.com/v1",
        )

    def inference_api_key(self) -> str:
        return self._get_env("OPENAI_API_KEY") or ""

    def inference_auth_mode(self) -> str:
        return "bearer"

    def inference_models(self) -> tuple[str, ...]:
        return ((self.model_name or "").split("/")[-1],)

    def agent_relay_environment(self) -> dict[str, str]:
        return {
            **LOOPBACK_DIRECT_ENV,
            "OPENAI_API_KEY": INFERENCE_RELAY_DUMMY_KEY,
            "OPENAI_BASE_URL": self.inference_relay_url(),
        }

    @classmethod
    def augment_command(cls, command: str) -> str:
        if 'ln -sf' in command and '"$CODEX_HOME/auth.json"' in command:
            command += f"""
cat >>"$CODEX_HOME/config.toml" <<'TOML'
[features]
memories = true

[memories]
generate_memories = true
use_memories = true
disable_on_external_context = false
TOML
if [ -d {cls.MEMORY_LOG_DIR} ]; then
  rm -rf "$CODEX_HOME/memories"
  cp -R {cls.MEMORY_LOG_DIR} "$CODEX_HOME/memories"
fi"""

        if "codex exec " in command and "--enable memories" not in command:
            command = command.replace(
                "--enable unified_exec ",
                "--enable unified_exec --enable memories ",
                1,
            )

        if command.strip().startswith("rm -rf ") and '"$CODEX_HOME"' in command:
            command = f"""rm -rf {cls.MEMORY_LOG_DIR}
if [ -d "$CODEX_HOME/memories" ]; then
  cp -R "$CODEX_HOME/memories" {cls.MEMORY_LOG_DIR}
fi
{command}"""

        return command

    async def exec_as_agent(
        self,
        environment: BaseEnvironment,
        command: str,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> Any:
        return await super().exec_as_agent(
            environment,
            self.augment_command(command),
            env=env,
            cwd=cwd,
            timeout_sec=timeout_sec,
        )
