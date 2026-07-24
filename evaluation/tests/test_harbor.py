from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evaluation.harbor import (
    AGENT_TOOLCHAINS,
    AGENT_TOOLCHAIN_TARGET,
    PREINSTALLED_AGENT_IMPORTS,
    write_job_config,
)


class HarborJobConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def toolchain(self, agent: str) -> Path:
        root = self.root / agent
        (root / "bin").mkdir(parents=True)
        for executable in AGENT_TOOLCHAINS[agent].required_executables:
            (root / "bin" / executable).touch()
        return root

    def write(self, agent: str = "codex", **kwargs) -> str:
        path = self.root / "job.yaml"
        write_job_config(path, tasks_path=self.root / "tasks", agent=agent,
            model="test/model", environment=kwargs.pop("environment", "docker"),
            concurrency=1, jobs_dir=self.root / "results", n_attempts=kwargs.pop("n_attempts", 1), **kwargs)
        return path.read_text()

    def test_supported_agent_toolchains_are_read_only_and_on_path(self) -> None:
        for agent, definition in AGENT_TOOLCHAINS.items():
            with self.subTest(agent=agent):
                toolchain = self.toolchain(agent)
                content = self.write(agent, agent_toolchain=toolchain, agent_version="1.2.3")
                self.assertIn(f'source: "{toolchain.resolve()}"', content)
                self.assertIn(f'target: "{AGENT_TOOLCHAIN_TARGET}"', content)
                self.assertIn("read_only: true", content)
                self.assertIn(f'PATH: "{definition.path}"', content)
                self.assertIn('version: "1.2.3"', content)

    def test_n_attempts_is_configurable(self) -> None:
        self.assertIn("n_attempts: 3", self.write(n_attempts=3))

    def test_shared_kimi_toolchain_uses_preinstalled_adapter(self) -> None:
        content = self.write(
            "kimi-cli",
            agent_toolchain=self.toolchain("kimi-cli"),
            agent_version="1.49.0",
        )
        self.assertIn(
            f"name: {PREINSTALLED_AGENT_IMPORTS['kimi-cli']}",
            content,
        )

    def test_kimi_without_toolchain_keeps_builtin_adapter(self) -> None:
        content = self.write("kimi-cli")
        self.assertIn("name: kimi-cli", content)
        self.assertNotIn(PREINSTALLED_AGENT_IMPORTS["kimi-cli"], content)

    def test_n_attempts_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 1"):
            self.write(n_attempts=0)

    def test_toolchain_rejects_non_docker_environment(self) -> None:
        with self.assertRaisesRegex(ValueError, "only by local Docker"):
            self.write(agent_toolchain=self.toolchain("codex"), environment="modal")

    def test_toolchain_rejects_unknown_agent(self) -> None:
        with self.assertRaisesRegex(ValueError, "supported agent"):
            self.write("unknown", agent_toolchain=self.root)

    def test_toolchain_requires_agent_executables(self) -> None:
        toolchain = self.toolchain("claude-code")
        (toolchain / "bin" / "claude").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "claude"):
            self.write("claude-code", agent_toolchain=toolchain)


if __name__ == "__main__":
    unittest.main()
