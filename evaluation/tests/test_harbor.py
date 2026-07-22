from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evaluation.harbor import CODEX_TOOLCHAIN_PATH, write_job_config


class HarborJobConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def toolchain(self) -> Path:
        root = self.root / "toolchain"
        (root / "bin").mkdir(parents=True)
        for executable in ("codex", "node", "rg"):
            (root / "bin" / executable).touch()
        return root

    def write(self, **kwargs) -> str:
        path = self.root / "job.yaml"
        write_job_config(
            path,
            tasks_path=self.root / "tasks",
            agent=kwargs.pop("agent", "codex"),
            model="openai/gpt-5.3-codex",
            environment=kwargs.pop("environment", "docker"),
            concurrency=1,
            jobs_dir=self.root / "results",
            n_attempts=kwargs.pop("n_attempts", 1),
            **kwargs,
        )
        return path.read_text()

    def test_shared_codex_toolchain_is_read_only_and_on_path(self) -> None:
        toolchain = self.toolchain()
        content = self.write(codex_toolchain=toolchain, codex_version="0.144.6")
        self.assertIn("type: bind", content)
        self.assertIn(f'source: "{toolchain.resolve()}"', content)
        self.assertIn('target: "/opt/codemem-agent"', content)
        self.assertIn("read_only: true", content)
        self.assertIn(f'PATH: "{CODEX_TOOLCHAIN_PATH}"', content)
        self.assertIn('version: "0.144.6"', content)

    def test_n_attempts_is_configurable(self) -> None:
        self.assertIn("n_attempts: 3", self.write(n_attempts=3))

    def test_n_attempts_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 1"):
            self.write(n_attempts=0)

    def test_shared_toolchain_rejects_non_docker_environment(self) -> None:
        with self.assertRaisesRegex(ValueError, "only by local Docker"):
            self.write(codex_toolchain=self.toolchain(), environment="modal")

    def test_shared_toolchain_requires_all_executables(self) -> None:
        toolchain = self.toolchain()
        (toolchain / "bin" / "rg").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "missing"):
            self.write(codex_toolchain=toolchain)


if __name__ == "__main__":
    unittest.main()
