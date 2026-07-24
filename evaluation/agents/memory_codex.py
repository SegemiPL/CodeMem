from __future__ import annotations

from typing import Any

from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment


class MemoryCodex(Codex):
    """Persist Codex's learned-memory store across Harbor steps.

    Harbor 0.20 runs Codex with ``CODEX_HOME=/tmp/codex-home``, copies only
    ``sessions/`` into the agent logs, and then deletes the temporary home.
    This compatibility adapter enables the experimental memories feature and
    copies only ``memories/`` to the retained agent logs before that cleanup.
    Authentication remains in Harbor's separate temporary secrets directory.
    """

    MEMORY_LOG_DIR = "/logs/agent/memories"

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
