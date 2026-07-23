from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.feature_eval.generator import FeatureTaskGenerator
from evaluation.feature_eval.models import CODE_FAMILY, PROCESS_FAMILY, load_task


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
        # Code instructions are wrapped with the solve directive.
        instruction = (output / "steps/turn_01/instruction.md").read_text()
        self.assertIn("Solve the following issue in the repository", instruction)
        self.assertIn("Instance: owner__repo-1", instruction)
        self.assertIn("instruction 1", instruction)
        dockerfile = (output / "environment/Dockerfile").read_text()
        self.assertIn("xingyaoww/sweb.eval.x86_64.owner_s_repo-1", dockerfile)

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


if __name__ == "__main__":
    unittest.main()
