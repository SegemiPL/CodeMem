from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation.feature_eval.generator import FeatureTaskGenerator
from evaluation.feature_eval.models import CODE_FAMILY, PROCESS_FAMILY, load_task
from evaluation.feature_eval import runtime_recorder
from evaluation.common.isolation import (
    AGENT_UID,
    FEATURE_PRIVATE_GIT_DIR,
    FEATURE_STATE_DIR,
)


def task_record(family: str) -> dict:
    turns = []
    for index in range(1, 21):
        turn = {
            "turn": index,
            "base_commit": "base" if index == 1 else f"base-{index}",
            "instruction": f"instruction {index}",
            "instruction_source": "source",
            "source_instance_id": f"owner__repo-{index}",
            "role": "target" if index == 1 else "distractor",
        }
        if family == PROCESS_FAMILY:
            turn.update(
                agent_facing_instruction=f"agent instruction {index}",
                image_name=f"example/process:image-{index}",
                workspace_policy="fresh_snapshot",
                inherits_previous_working_tree=False,
            )
        turns.append(turn)
    return {
        "schema_version": "test",
        "task_id": f"{family}-task",
        "subtype": "test subtype",
        "status": "candidate",
        "repository": "owner/repo",
        "start_base_commit": "base",
        "target": {"private_oracle": "do not expose"},
        "evaluation": {"expected_answer": "secret"},
        "turns": turns,
    }


class FeatureTaskGeneratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def load(self, family: str):
        path = self.root / f"{family}.json"
        path.write_text(json.dumps(task_record(family)))
        return load_task(path, family)

    def test_code_generates_twenty_shared_steps_with_per_turn_setup(self) -> None:
        task = self.load(CODE_FAMILY)
        output = FeatureTaskGenerator(self.root / "output").generate(task)
        toml = (output / "task.toml").read_text()
        self.assertEqual(toml.count("[[steps]]"), 20)
        self.assertEqual(toml.count('environment_mode = "shared"'), 20)
        self.assertIn('name = "turn_01"', toml)
        self.assertIn('name = "turn_20"', toml)
        # Every turn checks out its own base commit before the agent starts.
        self.assertTrue((output / "steps/turn_01/workdir/setup.sh").is_file())
        self.assertTrue((output / "steps/turn_02/workdir/setup.sh").is_file())
        self.assertTrue((output / "steps/turn_20/workdir/setup.sh").is_file())
        setup = (output / "steps/turn_02/workdir/setup.sh").read_text()
        self.assertIn("git reset --hard base-2", setup)
        self.assertIn(f"GIT_DIR={FEATURE_PRIVATE_GIT_DIR}", setup)
        self.assertIn("rm -rf /testbed/.git", setup)
        self.assertIn("git init -q /testbed", setup)
        first_setup = (
            output / "steps/turn_01/workdir/setup.sh"
        ).read_text()
        self.assertIn(
            f"mv /testbed/.git {FEATURE_PRIVATE_GIT_DIR}",
            first_setup,
        )
        self.assertIn("git commit -qm 'CodeMem phase baseline'", first_setup)
        # Code instructions are wrapped with the solve directive.
        instruction = (output / "steps/turn_01/instruction.md").read_text()
        self.assertIn("Solve the following issue in the repository", instruction)
        self.assertIn("Instance: owner__repo-1", instruction)
        self.assertIn("instruction 1", instruction)
        dockerfile = (output / "environment/Dockerfile").read_text()
        self.assertIn("xingyaoww/sweb.eval.x86_64.owner_s_repo-1", dockerfile)
        self.assertIn(f"useradd --uid {AGENT_UID}", dockerfile)
        self.assertIn(FEATURE_STATE_DIR, dockerfile)
        compose = (output / "environment/docker-compose.yaml").read_text()
        self.assertIn("NET_ADMIN", compose)
        self.assertIn("find /tests", setup)

    def test_process_uses_agent_facing_instruction_and_first_image(self) -> None:
        task = self.load(PROCESS_FAMILY)
        output = FeatureTaskGenerator(self.root / "output").generate(task)
        self.assertEqual(
            (output / "steps/turn_02/instruction.md").read_text(),
            "agent instruction 2\n",
        )
        self.assertIn(
            "FROM example/process:image",
            (output / "environment/Dockerfile").read_text(),
        )
        self.assertIn("timeout_sec = 7200", (output / "task.toml").read_text())
        self.assertTrue((output / "steps/turn_01/workdir/setup.sh").is_file())
        self.assertTrue((output / "steps/turn_20/workdir/setup.sh").is_file())
        setup = (output / "steps/turn_20/workdir/setup.sh").read_text()
        self.assertIn("git reset --hard base-20", setup)
        self.assertIn("git clean -fdx", setup)
        self.assertIn(f"GIT_DIR={FEATURE_PRIVATE_GIT_DIR}", setup)
        self.assertIn("git init -q /testbed", setup)
        config = json.loads((output / "tests/config.json").read_text())
        self.assertEqual(config["runtime_image"], "example/process:image-1")
        self.assertEqual(
            config["turns"][19]["image_name"], "example/process:image-20"
        )

    def test_process_normalizes_upstream_workspace_paths(self) -> None:
        record = task_record(PROCESS_FAMILY)
        record["turns"][0]["agent_facing_instruction"] = (
            "Open /testbed/repo, edit /workspace, then cd workspace_dir_name"
        )
        path = self.root / "process-paths.json"
        path.write_text(json.dumps(record))
        task = load_task(path, PROCESS_FAMILY)
        self.assertEqual(
            task.turns[0].instruction,
            "Open /testbed, edit /testbed, then cd /testbed\n",
        )

    def test_process_rejects_inherited_working_tree(self) -> None:
        record = task_record(PROCESS_FAMILY)
        record["turns"][1]["inherits_previous_working_tree"] = True
        path = self.root / "bad-process.json"
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "fresh snapshot"):
            load_task(path, PROCESS_FAMILY)

    def test_private_construction_fields_are_not_copied(self) -> None:
        task = self.load(CODE_FAMILY)
        output = FeatureTaskGenerator(self.root / "output").generate(task)
        config = (output / "tests/config.json").read_text()
        self.assertNotIn("private_oracle", config)
        self.assertNotIn("expected_answer", config)
        self.assertNotIn("instruction 1", config)

    def test_rejects_non_twenty_turn_task(self) -> None:
        record = task_record(CODE_FAMILY)
        record["turns"].pop()
        path = self.root / "bad.json"
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "exactly 20 turns"):
            load_task(path, CODE_FAMILY)


class FeatureRuntimeRecorderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.state = root / "state"
        self.repo.mkdir()
        self.state.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.repo,
            check=True,
        )
        (self.repo / "tracked.py").write_text("before\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "original base"],
            cwd=self.repo,
            check=True,
        )
        self.base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        shutil.move(self.repo / ".git", self.state / "original.git")

        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "codemem@local"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "CodeMem"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "CodeMem phase baseline"],
            cwd=self.repo,
            check=True,
        )

    def test_patch_uses_private_history_not_visible_baseline(self) -> None:
        (self.repo / "tracked.py").write_text("after\n")
        (self.repo / "untracked.py").write_text("new\n")
        with (
            patch.object(runtime_recorder, "REPO", self.repo),
            patch.object(runtime_recorder, "STATE", self.state),
        ):
            result, error = runtime_recorder.workspace_patch(
                self.base,
                "turn_01",
            )

        self.assertIsNone(error)
        self.assertIn("+after", result)
        self.assertIn("untracked.py", result)
        visible_count = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=self.repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        self.assertEqual(visible_count, "1")
        self.assertFalse((self.state / "turn_01.index").exists())


if __name__ == "__main__":
    unittest.main()
