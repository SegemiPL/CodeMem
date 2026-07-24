from __future__ import annotations

import re

from harbor.environments.base import BaseEnvironment

from evaluation.agents.restricted_kimi import RestrictedKimiCli


class PreinstalledKimiCli(RestrictedKimiCli):
    """Reuse a Kimi CLI toolchain already mounted on ``PATH``.

    Harbor 0.20's built-in adapter unconditionally installs curl, uv, Python,
    and kimi-cli during every task setup. CodeMem selects this subclass only
    for local-Docker jobs with ``--agent-toolchain``.
    """

    @staticmethod
    def parse_version(stdout: str) -> str:
        match = re.search(r"(\d+(?:\.\d+)+)", stdout)
        return match.group(1) if match else stdout.strip()

    async def _preinstalled_version_matches(
        self, environment: BaseEnvironment
    ) -> bool:
        result = await environment.exec(
            command="command -v kimi >/dev/null 2>&1 && kimi --version"
        )
        if result.return_code != 0:
            return False
        if self._version is None:
            return True
        return self.parse_version(result.stdout or "") == self._version

    async def install(self, environment: BaseEnvironment) -> None:
        if await self._preinstalled_version_matches(environment):
            self.logger.debug(
                "Kimi CLI is already available at the requested version"
            )
            return
        await super().install(environment)
