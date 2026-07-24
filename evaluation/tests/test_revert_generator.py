from __future__ import annotations

import tempfile
import unittest
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from evaluation.revert_eval.config import load_config
from evaluation.revert_eval.generator import RevertTaskGenerator
from evaluation.revert_eval import runtime_evaluator
from evaluation.common.isolation import AGENT_UID, REVERT_STATE_DIR
from evaluation.harbor import AGENT_IMPORTS


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT.parent / "data" / "data"
ORDERED = DATA_ROOT / "revert/ordered_revert_candidates.json"
DATASET = DATA_ROOT / "swegym/raw/train-00000-of-00001.parquet"


class RevertTaskGeneratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "tasks"
        self.generator = RevertTaskGenerator(
            ORDERED,
            DATASET,
            self.output,
            load_config(ROOT / "evaluation/revert_eval/config.toml"),
        )

    def test_uses_manifest_touched_files(self) -> None:
        target, middle = self.generator.candidate("Project-MONAI__MONAI-1010")
        self.assertEqual(target.instance_id, "Project-MONAI__MONAI-1010")
        self.assertEqual(middle.instance_id, "Project-MONAI__MONAI-1012")
        self.assertEqual(
            target.touched_files,
            (
                "monai/apps/datasets.py",
                "monai/data/__init__.py",
                "monai/data/decathlon_datalist.py",
            ),
        )

    def test_generates_checkpointed_multistep_task(self) -> None:
        task = self.generator.generate("Project-MONAI__MONAI-1010")
        task_toml = (task / "task.toml").read_text()
        self.assertIn('name = "solve_target"', task_toml)
        self.assertIn('name = "solve_middle_01"', task_toml)
        self.assertIn('name = "restore_target"', task_toml)
        self.assertNotIn('name = "compact"', task_toml)
        restore_setup = (task / "steps/restore_target/workdir/setup.sh").read_text()
        self.assertIn("session_checkpoint", restore_setup)
        self.assertIn(REVERT_STATE_DIR, restore_setup)
        self.assertIn("session_checkpoint/codex", restore_setup)
        self.assertIn("session_checkpoint/codex-memories", restore_setup)
        self.assertIn("session_checkpoint/kimi-cli", restore_setup)
        self.assertTrue((task / "tests/evaluate.py").is_file())
        dockerfile = (task / "environment/Dockerfile").read_text()
        self.assertIn("safe.directory /testbed", dockerfile)
        self.assertIn(f"useradd --uid {AGENT_UID}", dockerfile)
        self.assertIn("mode 0700", dockerfile.replace("-m 0700", "mode 0700"))
        self.assertIn("git init -q /testbed", restore_setup)
        self.assertIn("git clean -fdx", restore_setup)
        self.assertIn("find /tests", restore_setup)
        self.assertNotIn("/tmp/codemem", restore_setup)

    def test_generates_configured_number_of_middle_steps(self) -> None:
        task = self.generator.generate(
            "getmoto__moto-6176", middle_count=3
        )
        task_toml = (task / "task.toml").read_text()
        self.assertIn('name = "solve_middle_01"', task_toml)
        self.assertIn('name = "solve_middle_02"', task_toml)
        self.assertIn('name = "solve_middle_03"', task_toml)
        self.assertNotIn('name = "solve_middle_04"', task_toml)
        config = json.loads((task / "tests/config.json").read_text())
        self.assertEqual(len(config["middles"]), 3)

    def test_rejects_invalid_middle_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 1"):
            self.generator.generate("Project-MONAI__MONAI-1010", middle_count=0)

    def test_multi_middle_task_dirs_are_unique_per_combination(self) -> None:
        _, middles = self.generator.candidates("getmoto__moto-6176", middle_count=3)
        ids = [middle.instance_id for middle in middles]
        first = self.generator.generate("getmoto__moto-6176", [ids[0], ids[1]])
        second = self.generator.generate("getmoto__moto-6176", [ids[0], ids[2]])
        self.assertNotEqual(first, second)

    def test_rejects_unknown_execution_keys(self) -> None:
        config_path = Path(self.temp.name) / "bad.toml"
        config_path.write_text(
            "[execution]\nnot_a_key = 1\n"
            "[prompts]\nsolve_target = 'x'\nsolve_middle = 'x'\n"
            "revert_target = 'x'\nrestore_target = 'x'\nmanual_compact = 'x'\n"
        )
        with self.assertRaisesRegex(ValueError, "not_a_key"):
            load_config(config_path)

    def test_generates_manual_compact_step_when_enabled(self) -> None:
        from dataclasses import replace

        generator = RevertTaskGenerator(
            ORDERED,
            DATASET,
            self.output,
            replace(
                self.generator.config,
                execution=replace(
                    self.generator.config.execution,
                    manual_compact_before_final=True,
                ),
            ),
        )
        task = generator.generate("Project-MONAI__MONAI-1010")
        import tomllib

        data = tomllib.loads((task / "task.toml").read_text())
        names = [step["name"] for step in data["steps"]]
        self.assertEqual(
            names,
            ["solve_target", "solve_middle_01", "compact", "revert_target", "restore_target"],
        )
        self.assertEqual(
            (task / "steps/compact/instruction.md").read_text().strip(), "/compact"
        )
        config = json.loads((task / "tests/config.json").read_text())
        self.assertTrue(config["manual_compaction_requested"])

    def test_job_config_uses_harbor_resume_and_provider(self) -> None:
        path = Path(self.temp.name) / "job.yaml"
        self.generator.write_job_config(
            path,
            tasks_path=self.output,
            agent="codex",
            model="openai/gpt-5.3-codex",
            environment="modal",
            concurrency=8,
            jobs_dir=Path(self.temp.name) / "jobs",
        )
        content = path.read_text()
        self.assertIn("resume_trajectory: true", content)
        self.assertIn("type: modal", content)
        self.assertIn(f"name: {AGENT_IMPORTS['codex']}", content)


class RuntimeEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.state = root / "state"
        self.logs = root / "verifier"
        self.agent_sessions = root / "agent/sessions"
        self.tests = root / "tests"
        for path in (self.repo, self.state, self.logs, self.agent_sessions, self.tests):
            path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        (self.repo / "target.py").write_text("before\n")
        (self.repo / "middle.py").write_text("before\n")
        (self.repo / "middle2.py").write_text("before\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.repo, check=True)
        base_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
            text=True, stdout=subprocess.PIPE,
        ).stdout.strip()
        shutil.move(self.repo / ".git", self.state / "original.git")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "visible baseline"], cwd=self.repo, check=True)
        (self.agent_sessions / "session.jsonl").write_text(
            '{"type":"system","subtype":"compact_boundary"}\n'
        )
        config = {
            "target": {
                "instance_id": "target-1", "test_patch": "",
                "FAIL_TO_PASS": [], "PASS_TO_PASS": [],
                "base_commit": base_commit,
                "touched_files": ["target.py"],
            },
            "middles": [
                {
                    "instance_id": "middle-1", "test_patch": "",
                    "FAIL_TO_PASS": [], "PASS_TO_PASS": [],
                    "base_commit": base_commit,
                    "touched_files": ["middle.py"],
                },
                {
                    "instance_id": "middle-2", "test_patch": "",
                    "FAIL_TO_PASS": [], "PASS_TO_PASS": [],
                    "base_commit": base_commit,
                    "touched_files": ["middle2.py"],
                },
            ],
            "manual_compaction_requested": False,
        }
        (self.tests / "config.json").write_text(json.dumps(config))
        self.originals = (
            runtime_evaluator.REPO, runtime_evaluator.STATE, runtime_evaluator.LOGS,
            runtime_evaluator.CONFIG,
        )
        runtime_evaluator.REPO = self.repo
        runtime_evaluator.STATE = self.state
        runtime_evaluator.LOGS = self.logs
        runtime_evaluator.CONFIG = self.tests / "config.json"
        self.addCleanup(self._restore_globals)

    def _restore_globals(self) -> None:
        (
            runtime_evaluator.REPO, runtime_evaluator.STATE, runtime_evaluator.LOGS,
            runtime_evaluator.CONFIG,
        ) = self.originals

    def test_file_metrics_and_expected_test_directions(self) -> None:
        # Patch the absolute session paths used by the container runtime helper.
        original_path = runtime_evaluator.Path

        class RedirectedPath(type(Path())):
            def __new__(cls, value):
                if str(value) == "/logs/agent/sessions":
                    return original_path(self.agent_sessions)
                return original_path(value)

        runtime_evaluator.Path = RedirectedPath
        self.addCleanup(setattr, runtime_evaluator, "Path", original_path)

        (self.repo / "target.py").write_text("target solved\n")
        runtime_evaluator.main("solve_target")
        target_patch = self.logs / "workspace.patch"
        self.assertTrue(target_patch.is_file())
        self.assertIn("+target solved", target_patch.read_text())
        self.assertFalse((self.state / "patches").exists())
        (self.repo / "middle.py").write_text("middle solved\n")
        runtime_evaluator.main("solve_middle_01")
        self.assertFalse((self.state / "after_middles.tree").exists())
        (self.repo / "middle2.py").write_text("middle 2 solved\n")
        runtime_evaluator.main("solve_middle_02")
        self.assertFalse((self.state / "patches").exists())

        (self.repo / "target.py").write_text("before\n")
        runtime_evaluator.main("revert_target")
        metrics = json.loads((self.logs / "reward.json").read_text())
        self.assertTrue(metrics["file_revert_match"])
        self.assertTrue(metrics["session_compacted_before_final"])

        after_target = (self.state / "after_target.tree").read_text().strip()
        runtime_evaluator.restore_tree(after_target)
        (self.repo / "target.py").unlink()
        (self.repo / "target.py").write_text("target solved\n")
        runtime_evaluator.main("restore_target")
        restore_patch = self.logs / "workspace.patch"
        self.assertTrue(restore_patch.is_file())
        metrics = json.loads((self.logs / "reward.json").read_text())
        self.assertTrue(metrics["file_restore_match"])
        details = json.loads((self.logs / "metrics.json").read_text())
        self.assertEqual(
            details["workspace_patch"]["artifact_path"],
            str(restore_patch),
        )
        self.assertEqual(
            details["workspace_patch"]["workspace_tree"],
            details["workspace_tree"],
        )

    def test_pytest_uses_the_resolved_test_environment(self) -> None:
        instance = {
            "instance_id": "target-1",
            "test_patch": "",
            "FAIL_TO_PASS": ["tests/test_target.py::test_target"],
            "PASS_TO_PASS": [],
        }
        calls: list[tuple[str, ...]] = []

        def fake_run(*args: str, check: bool = True, timeout: int | None = None):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "passed")

        with (
            patch.object(runtime_evaluator, "run", side_effect=fake_run),
            patch.object(runtime_evaluator, "restore_tree"),
            patch.object(
                runtime_evaluator,
                "resolve_test_python",
                return_value="/opt/miniconda3/envs/testbed/bin/python",
            ),
        ):
            runtime_evaluator.test_instance(instance, "tree")

        self.assertIn(
            (
                "/opt/miniconda3/envs/testbed/bin/python",
                "-m",
                "pytest",
                "-q",
                "tests/test_target.py::test_target",
            ),
            calls,
        )

    def test_missing_pytest_is_an_infrastructure_error_not_a_false_pass(self) -> None:
        instance = {
            "instance_id": "target-1",
            "test_patch": "",
            "FAIL_TO_PASS": ["tests/test_target.py::test_target"],
            "PASS_TO_PASS": [],
        }
        with (
            patch.object(
                runtime_evaluator,
                "resolve_test_python",
                side_effect=RuntimeError("pytest unavailable"),
            ),
            patch.object(runtime_evaluator, "restore_tree"),
        ):
            result = runtime_evaluator.test_instance(instance, "tree")

        self.assertEqual(result["infrastructure_error"], "pytest unavailable")
        self.assertEqual(runtime_evaluator.rate(result, "FAIL_TO_PASS", "pass"), 0.0)

    def test_test_instance_resets_agent_modified_test_files(self) -> None:
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_t.py").write_text("old\n")
        runtime_evaluator.git_run("add", ".")
        runtime_evaluator.git_run("commit", "-qm", "add test")
        base = runtime_evaluator.git_run("rev-parse", "HEAD").stdout.strip()
        patch = (
            "diff --git a/tests/test_t.py b/tests/test_t.py\n"
            "--- a/tests/test_t.py\n"
            "+++ b/tests/test_t.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        # The agent clobbered the test file while solving; the evaluator must
        # reset it to base before applying the test patch, like SWE-bench does.
        (self.repo / "tests" / "test_t.py").write_text("agent garbage\n")
        tree = runtime_evaluator.snapshot_tree()
        instance = {
            "instance_id": "target-1",
            "base_commit": base,
            "test_patch": patch,
            "FAIL_TO_PASS": [],
            "PASS_TO_PASS": [],
        }
        result = runtime_evaluator.test_instance(instance, tree)
        self.assertNotIn("patch_error", result)
        self.assertEqual(
            (self.repo / "tests" / "test_t.py").read_text(), "agent garbage\n"
        )

    def test_pytest_timeout_is_an_error_not_a_hang(self) -> None:
        instance = {
            "instance_id": "target-1",
            "test_patch": "",
            "FAIL_TO_PASS": ["tests/test_target.py::test_target"],
            "PASS_TO_PASS": [],
        }
        with (
            patch.object(
                runtime_evaluator,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd=["pytest"], timeout=1),
            ),
            patch.object(runtime_evaluator, "restore_tree"),
            patch.object(
                runtime_evaluator,
                "resolve_test_python",
                return_value="/opt/miniconda3/envs/testbed/bin/python",
            ),
        ):
            result = runtime_evaluator.test_instance(instance, "tree")

        test = result["tests"]["tests/test_target.py::test_target"]
        self.assertEqual(test["status"], "error")
        self.assertIn("timed out", test["output"])
        self.assertEqual(runtime_evaluator.rate(result, "FAIL_TO_PASS", "pass"), 0.0)

    def test_checkpoint_without_session_dirs_is_degraded(self) -> None:
        root = Path(self.temp.name)
        original_path = runtime_evaluator.Path

        class RedirectedPath(type(Path())):
            def __new__(cls, value):
                if str(value) == "/logs/agent/sessions":
                    return original_path(root / "missing-sessions")
                if str(value) == "/logs/agent/kimi/share":
                    return original_path(root / "missing-kimi")
                return original_path(value)

        with patch.object(runtime_evaluator, "Path", RedirectedPath):
            copied = runtime_evaluator.checkpoint_session()

        self.assertEqual(copied, [])
        self.assertFalse((self.state / "session_checkpoint").exists())

    def test_compaction_detection_parses_events_not_embedded_text(self) -> None:
        sessions = Path(self.temp.name) / "compaction-sessions"
        sessions.mkdir()
        (sessions / "false-positive.jsonl").write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "content": 'documentation example: {"type":"compaction"}',
                    },
                }
            )
            + "\n"
        )
        result = runtime_evaluator.detect_compaction(sessions)
        self.assertFalse(result["session_compacted_before_final"])
        self.assertEqual(result["compaction_count"], 0)

        (sessions / "real.jsonl").write_text(
            json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n"
            + json.dumps({"type": "event_msg", "payload": {"type": "compacted"}})
            + "\n"
        )
        result = runtime_evaluator.detect_compaction(sessions)
        self.assertTrue(result["session_compacted_before_final"])
        self.assertEqual(result["compaction_count"], 2)

    def test_checkpoint_session_supports_kimi_cli_layout(self) -> None:
        root = Path(self.temp.name)
        kimi_share = root / "agent/kimi/share"
        kimi_share.mkdir(parents=True)
        (kimi_share / "kimi.json").write_text('{"work_dirs": []}')
        original_path = runtime_evaluator.Path

        class RedirectedPath(type(Path())):
            def __new__(cls, value):
                if str(value) == "/logs/agent/sessions":
                    return original_path(root / "missing-codex-sessions")
                if str(value) == "/logs/agent/kimi/share":
                    return original_path(kimi_share)
                return original_path(value)

        with patch.object(runtime_evaluator, "Path", RedirectedPath):
            runtime_evaluator.checkpoint_session()

        checkpoint = self.state / "session_checkpoint/kimi-cli/kimi.json"
        self.assertEqual(checkpoint.read_text(), '{"work_dirs": []}')

    def test_checkpoint_session_preserves_codex_learned_memory(self) -> None:
        root = Path(self.temp.name)
        codex_marker = root / "agent/codex.txt"
        codex_marker.parent.mkdir(parents=True, exist_ok=True)
        codex_marker.write_text("active\n")
        codex_memories = root / "agent/memories"
        codex_memories.mkdir()
        (codex_memories / "MEMORY.md").write_text("remember this\n")
        original_path = runtime_evaluator.Path

        class RedirectedPath(type(Path())):
            def __new__(cls, value):
                redirects = {
                    "/logs/agent/sessions": root / "missing-codex-sessions",
                    "/logs/agent/kimi/share": root / "missing-kimi",
                    "/logs/agent/codex.txt": codex_marker,
                    "/logs/agent/memories": codex_memories,
                }
                return original_path(redirects.get(str(value), value))

        with patch.object(runtime_evaluator, "Path", RedirectedPath):
            copied = runtime_evaluator.checkpoint_session()

        checkpoint = self.state / "session_checkpoint/codex-memories/MEMORY.md"
        self.assertIn("codex-memories", copied)
        self.assertEqual(checkpoint.read_text(), "remember this\n")

    def test_checkpoint_session_preserves_empty_codex_memory_state(self) -> None:
        root = Path(self.temp.name)
        codex_marker = root / "agent/codex.txt"
        codex_marker.parent.mkdir(parents=True, exist_ok=True)
        codex_marker.write_text("active\n")
        original_path = runtime_evaluator.Path

        class RedirectedPath(type(Path())):
            def __new__(cls, value):
                redirects = {
                    "/logs/agent/sessions": root / "missing-codex-sessions",
                    "/logs/agent/kimi/share": root / "missing-kimi",
                    "/logs/agent/codex.txt": codex_marker,
                    "/logs/agent/memories": root / "missing-memories",
                }
                return original_path(redirects.get(str(value), value))

        with patch.object(runtime_evaluator, "Path", RedirectedPath):
            copied = runtime_evaluator.checkpoint_session()

        checkpoint = self.state / "session_checkpoint/codex-memories"
        self.assertIn("codex-memories", copied)
        self.assertTrue(checkpoint.is_dir())
        self.assertEqual(list(checkpoint.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
