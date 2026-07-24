from __future__ import annotations

from typing import Any

from harbor.environments.base import BaseEnvironment

from evaluation.common.isolation import AGENT_USER


class RestrictedAgentMixin:
    """Run agent-owned commands without access to evaluator-private state."""

    async def exec_as_agent(
        self,
        environment: BaseEnvironment,
        command: str,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> Any:
        return await self._exec(
            environment,
            command,
            user=AGENT_USER,
            env=env,
            cwd=cwd,
            timeout_sec=timeout_sec,
        )
