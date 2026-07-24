from __future__ import annotations

import asyncio
import unittest

try:
    from evaluation.agents.restricted import AGENT_USER, RestrictedAgentMixin
except ModuleNotFoundError as exc:
    if exc.name != "harbor":
        raise
    RestrictedAgentMixin = None  # type: ignore[assignment,misc]


@unittest.skipIf(
    RestrictedAgentMixin is None, "Harbor is not installed in this test environment"
)
class RestrictedAgentTest(unittest.TestCase):
    def test_exec_as_agent_selects_unprivileged_user(self) -> None:
        calls = []

        class Dummy(RestrictedAgentMixin):
            async def _exec(self, environment, command, **kwargs):
                calls.append((environment, command, kwargs))
                return "ok"

        result = asyncio.run(
            Dummy().exec_as_agent(
                "environment",
                "command",
                env={"KEY": "value"},
                cwd="/testbed",
                timeout_sec=9,
            )
        )

        self.assertEqual(result, "ok")
        self.assertEqual(calls[0][2]["user"], AGENT_USER)
        self.assertEqual(calls[0][2]["cwd"], "/testbed")
        self.assertEqual(calls[0][2]["timeout_sec"], 9)


if __name__ == "__main__":
    unittest.main()
