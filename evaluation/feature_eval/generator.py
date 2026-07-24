from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from evaluation.common.prompts import wrap_solve_instruction
from evaluation.common.scaffold import (
    bundle_runtime_script,
    render_task_toml,
    write_dockerfile,
    write_step_dirs,
    write_step_test_script,
)
from evaluation.common.snippets import checkout_lines, setup_script
from evaluation.common.isolation import FEATURE_STATE_DIR

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
        write_step_dirs(task_dir, step_names)

        write_dockerfile(task_dir, task.image, FEATURE_STATE_DIR)

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
        bundle_runtime_script(
            task_dir, Path(__file__).with_name("runtime_recorder.py"), "record.py"
        )

        self._write_task_toml(task_dir, task, step_names)
        for turn, name in zip(task.turns, step_names):
            step = task_dir / "steps" / name

            instruction = turn.instruction
            if task.family == CODE_FAMILY:
                # Code instructions are bare problem statements; wrap them in
                # the solve directive (process instructions are already
                # agent-facing).
                instruction = wrap_solve_instruction(
                    instruction, turn.source_instance_id or task.task_id
                )
            (step / "instruction.md").write_text(instruction)

            # Per-Step scripts are all in /tests/record.py in docker, using args to pass state
            write_step_test_script(step, "record.py", name)

            # Every turn starts from its own base commit (checkout + clean).
            self._write_turn_setup(step, turn)
        return task_dir

    @staticmethod
    def _write_turn_setup(step: Path, turn: FeatureTurn) -> None:
        workdir = step / "workdir"
        workdir.mkdir()
        setup = workdir / "setup.sh"
        setup.write_text(
            setup_script(
                *checkout_lines(turn.base_commit, clean_args="-fdx"),
                f"mkdir -p {FEATURE_STATE_DIR}",
                f"printf '%s\\n' {json.dumps(turn.base_commit)} > "
                f"{FEATURE_STATE_DIR}/current_commit",
            )
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
        content = render_task_toml(
            task_name=task_dir.name,
            description="CodeMem 20-turn feature rollout",
            metadata={
                "benchmark": "CodeMem-Feature",
                "family": task.family,
                "subtype": task.subtype,
                "source_status": task.status,
                "source_task_id": task.task_id,
                "repository": task.repository,
                "turn_count": 20,
            },
            steps=[
                (name, timeout, execution.verifier_timeout_sec)
                for name in step_names
            ],
            build_timeout_sec=execution.build_timeout_sec,
            cpus=execution.cpus,
            memory_mb=execution.memory_mb,
            storage_mb=execution.storage_mb,
        )
        (task_dir / "task.toml").write_text(content)
