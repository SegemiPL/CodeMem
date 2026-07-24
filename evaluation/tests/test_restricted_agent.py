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

    def test_exec_as_agent_injects_relay_environment_only_after_activation(self) -> None:
        calls = []

        class Dummy(RestrictedAgentMixin):
            def agent_relay_environment(self):
                return {
                    "OPENAI_API_KEY": "dummy",
                    "OPENAI_BASE_URL": "http://127.0.0.1:18080/v1",
                }

            async def _exec(self, environment, command, **kwargs):
                calls.append(kwargs)
                return "ok"

        dummy = Dummy()
        asyncio.run(dummy.exec_as_agent("environment", "before", env={"KEEP": "1"}))
        dummy._network_isolation_active = True
        asyncio.run(dummy.exec_as_agent("environment", "after", env={"KEEP": "1"}))

        self.assertNotIn("OPENAI_BASE_URL", calls[0]["env"])
        self.assertEqual(
            calls[1]["env"]["OPENAI_BASE_URL"],
            "http://127.0.0.1:18080/v1",
        )
        self.assertEqual(calls[1]["env"]["OPENAI_API_KEY"], "dummy")
        self.assertEqual(calls[1]["env"]["KEEP"], "1")


if __name__ == "__main__":
    unittest.main()
