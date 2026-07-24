from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from evaluation.agents.restricted_claude import RestrictedClaudeCode
    from evaluation.agents.restricted_kimi import RestrictedKimiCli
except ModuleNotFoundError as exc:
    if exc.name != "harbor":
        raise
    RestrictedClaudeCode = None  # type: ignore[assignment,misc]
    RestrictedKimiCli = None  # type: ignore[assignment,misc]


@unittest.skipIf(
    RestrictedClaudeCode is None, "Harbor is not installed in this test environment"
)
class RestrictedAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.logs = Path(self.temp.name)

    def test_claude_provider_web_tools_are_always_denied(self) -> None:
        agent = RestrictedClaudeCode(
            logs_dir=self.logs,
            model_name="claude-test",
            disallowed_tools="CustomTool,WebSearch",
        )
        flags = agent.build_cli_flags()
        self.assertIn("--disallowedTools", flags)
        self.assertIn("CustomTool,WebSearch,WebFetch", flags)

    def test_claude_uses_relay_origin_without_duplicate_v1(self) -> None:
        agent = RestrictedClaudeCode(
            logs_dir=self.logs,
            model_name="claude-test",
        )
        self.assertEqual(
            agent.agent_relay_environment()["ANTHROPIC_BASE_URL"],
            "http://127.0.0.1:18080",
        )

    def test_kimi_uses_openai_gateway_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"OPENAI_BASE_URL": "https://gateway.example/v1"},
            clear=False,
        ):
            agent = RestrictedKimiCli(
                logs_dir=self.logs,
                model_name="openai/test-model",
            )
        self.assertEqual(agent._base_url, "https://gateway.example/v1")
        self.assertEqual(
            agent.network_gateway_urls(),
            ("https://gateway.example/v1",),
        )

    def test_kimi_config_uses_local_relay_after_activation(self) -> None:
        agent = RestrictedKimiCli(
            logs_dir=self.logs,
            model_name="openai/gpt-4o",
            api_key="real-secret",
            base_url="https://gateway.example/v1",
        )
        agent._inference_relay_url = "http://127.0.0.1:18080/v1"
        agent._network_isolation_active = True
        config = __import__("json").loads(
            agent._build_config_json("openai", "gpt-4o")
        )
        self.assertEqual(
            config["providers"]["harbor"]["base_url"],
            "http://127.0.0.1:18080/v1",
        )
        self.assertEqual(
            agent.agent_relay_environment()["HARBOR_KIMI_API_KEY"],
            "codemem-local-relay",
        )


if __name__ == "__main__":
    unittest.main()
