from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import CODE_FAMILY, PROCESS_FAMILY, FeatureTask, FeatureTurn, safe_name


@dataclass(frozen=True)
class FeatureExecution:
    code_agent_timeout_sec: int = 3000
    process_agent_timeout_sec: int = 7200
    verifier_timeout_sec: int = 300
    build_timeout_sec: int = 1800
    cpus: int = 1
    memory_mb: int = 8192
    storage_mb: int = 20480


class FeatureTaskGenerator:
    def __init__(self, output_root: Path, execution: FeatureExecution | None = None) -> None:
        self.output_root = output_root
        self.execution = execution or FeatureExecution()

    # Core Task Generation Function
    def generate(self, task: FeatureTask, *, overwrite: bool = False) -> Path:
        task_dir = self.output_root / safe_name(f"{task.family}--{task.task_id}")
        if task_dir.exists():
            if not overwrite:
                raise FileExistsError(f"Task already exists: {task_dir}")
            shutil.rmtree(task_dir)

        (task_dir / "environment").mkdir(parents=True)
        (task_dir / "tests").mkdir()
        step_names = [f"turn_{turn.index:02d}" for turn in task.turns]
        for name in step_names:
            (task_dir / "steps" / name / "tests").mkdir(parents=True)

        (task_dir / "environment" / "Dockerfile").write_text(
            f"FROM {task.image}\nWORKDIR /testbed\n"
            "RUN git config --system --add safe.directory /testbed"
            " && mkdir -p /logs /tmp/codemem-feature\n"
        )
        safe_config = {
            "task_id": task.task_id,
            "family": task.family,
            "subtype": task.subtype,
            "repository": task.repository,
            "start_base_commit": task.start_base_commit,
            "runtime_image": task.image,
            "turns": [
                {
                    "name": f"turn_{turn.index:02d}",
                    "turn": turn.index,
                    "base_commit": turn.base_commit,
                    "image_name": turn.image_name,
                    "workspace_policy": turn.workspace_policy,
                    "inherits_previous_working_tree": (
                        turn.inherits_previous_working_tree
                    ),
                    "source_instance_id": turn.source_instance_id,
                    "instruction_source": turn.instruction_source,
                    "role": turn.role,
                }
                for turn in task.turns
            ],
        }
        (task_dir / "tests" / "config.json").write_text(
            json.dumps(safe_config, indent=2) + "\n"
        )
        recorder = Path(__file__).with_name("runtime_recorder.py").read_text()
        (task_dir / "tests" / "record.py").write_text(recorder)

        self._write_task_toml(task_dir, task, step_names)
        for turn, name in zip(task.turns, step_names):
            step = task_dir / "steps" / name

            # Instructions can be wrapped by prompt, remain to do.
            (step / "instruction.md").write_text(turn.instruction)

            # Per-Step scripts are all in /tests/record.py in docker, using args to pass state
            test = step / "tests" / "test.sh"
            test.write_text(
                "#!/bin/bash\nset -u\npython3 /tests/record.py " + name + "\n"
            )
            test.chmod(0o755)

            # If the task is PROCESS_FEATURE: checkout base commit for each turn by setup.sh
            if task.family == PROCESS_FAMILY or turn.index == 1:
                self._write_turn_setup(step, turn)
        return task_dir

    @staticmethod
    def _write_turn_setup(step: Path, turn: FeatureTurn) -> None:
        workdir = step / "workdir"
        workdir.mkdir()
        setup = workdir / "setup.sh"
        setup.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "cd /testbed\n"
            f"git reset --hard {turn.base_commit}\n"
            "git clean -fdx\n"
            "mkdir -p /tmp/codemem-feature\n"
            f"printf '%s\\n' {json.dumps(turn.base_commit)} > "
            "/tmp/codemem-feature/current_commit\n"
            "rm -f -- \"$0\"\n"
        )
        setup.chmod(0o755)

    def generate_many(
        self, tasks: Iterable[FeatureTask], *, overwrite: bool = False
    ) -> list[Path]:
        return [self.generate(task, overwrite=overwrite) for task in tasks]

    def _write_task_toml(
        self, task_dir: Path, task: FeatureTask, step_names: list[str]
    ) -> None:
        execution = self.execution
        timeout = (
            execution.code_agent_timeout_sec
            if task.family == CODE_FAMILY
            else execution.process_agent_timeout_sec
        )
        step_blocks = "\n".join(
            f'''[[steps]]
name = {json.dumps(name)}
[steps.agent]
timeout_sec = {timeout}
[steps.verifier]
timeout_sec = {execution.verifier_timeout_sec}
environment_mode = "shared"
'''
            for name in step_names
        )
        content = f'''schema_version = "1.3"
multi_step_reward_strategy = "final"

[task]
name = {json.dumps("codemem/" + task_dir.name)}
description = "CodeMem 20-turn feature rollout"

[metadata]
benchmark = "CodeMem-Feature"
family = {json.dumps(task.family)}
subtype = {json.dumps(task.subtype)}
source_status = {json.dumps(task.status)}
source_task_id = {json.dumps(task.task_id)}
repository = {json.dumps(task.repository)}
turn_count = 20

[environment]
build_timeout_sec = {execution.build_timeout_sec}
cpus = {execution.cpus}
memory_mb = {execution.memory_mb}
storage_mb = {execution.storage_mb}
workdir = "/testbed"

{step_blocks}'''
        (task_dir / "task.toml").write_text(content)
