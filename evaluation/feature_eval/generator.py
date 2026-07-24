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
from evaluation.common.snippets import (
    fresh_baseline_lines,
    initialize_private_repo_lines,
    private_checkout_lines,
    setup_script,
)
from evaluation.common.isolation import (
    AGENT_UID,
    FEATURE_FINAL_WORKSPACE_DIR,
    FEATURE_PRIVATE_GIT_DIR,
    FEATURE_STATE_DIR,
)

from .models import (
    CLOSED_BOOK,
    CODE_FAMILY,
    PROCESS_FAMILY,
    FeatureTask,
    FeatureTurn,
    safe_name,
)


@dataclass(frozen=True)
class FeatureExecution:
    code_agent_timeout_sec: int = 3000
    process_agent_timeout_sec: int = 7200
    qa_agent_timeout_sec: int = 600
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
        turn_step_names = [f"turn_{turn.index:02d}" for turn in task.turns]
        step_names = [
            *turn_step_names,
            *(["memory_qa"] if task.family == CODE_FAMILY else []),
        ]
        write_step_dirs(task_dir, step_names)

        write_dockerfile(task_dir, task.image, FEATURE_STATE_DIR)

        safe_config = {
            "task_id": task.task_id,
            "family": task.family,
            "subtype": task.subtype,
            "repository": task.repository,
            "start_base_commit": task.start_base_commit,
            "runtime_image": task.image,
            "access_mode": task.access_mode,
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
        if task.family == CODE_FAMILY:
            bundle_runtime_script(
                task_dir, Path(__file__).with_name("runtime_qa.py"), "qa.py"
            )

        self._write_task_toml(task_dir, task, step_names)
        for turn, name in zip(task.turns, turn_step_names):
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
        if task.family == CODE_FAMILY:
            self._write_qa_step(task_dir / "steps" / "memory_qa", task)
        return task_dir

    @staticmethod
    def _write_turn_setup(step: Path, turn: FeatureTurn) -> None:
        workdir = step / "workdir"
        workdir.mkdir()
        setup = workdir / "setup.sh"
        if turn.index == 1:
            repository_setup = initialize_private_repo_lines(
                turn.base_commit,
                state_dir=FEATURE_STATE_DIR,
                private_git_dir=FEATURE_PRIVATE_GIT_DIR,
            )
        else:
            repository_setup = private_checkout_lines(
                turn.base_commit,
                private_git_dir=FEATURE_PRIVATE_GIT_DIR,
            )
        setup.write_text(
            setup_script(
                *repository_setup,
                f"printf '%s\\n' {json.dumps(turn.base_commit)} > "
                f"{FEATURE_STATE_DIR}/current_commit",
            )
        )
        setup.chmod(0o755)

    def generate_many(
        self, tasks: Iterable[FeatureTask], *, overwrite: bool = False
    ) -> list[Path]:
        return [self.generate(task, overwrite=overwrite) for task in tasks]

    @staticmethod
    def _write_qa_step(step: Path, task: FeatureTask) -> None:
        if not task.memory_question:
            raise ValueError(f"{task.task_id}: code-feature memory question is missing")
        access_instruction = (
            "The repository has been removed from your readable workspace. "
            "Do not attempt to recover it from private evaluator state."
            if task.access_mode == CLOSED_BOOK
            else (
                "You may inspect the final working tree, but Git history, "
                "earlier checkpoints, diffs, and evaluator state are unavailable."
            )
        )
        (step / "instruction.md").write_text(
            "Answer this memory question:\n\n"
            f"{task.memory_question.strip()}\n\n"
            f"{access_instruction}\n"
            "Write only your answer to /testbed/codemem_answer.txt. "
            "Do not include analysis or a preamble in that file.\n"
        )
        write_step_test_script(step, "qa.py", "memory_qa")

        if task.access_mode == CLOSED_BOOK:
            access_setup = [
                f"rm -rf {FEATURE_FINAL_WORKSPACE_DIR}",
                f"install -d -m 0700 -o root -g root "
                f"{FEATURE_FINAL_WORKSPACE_DIR}",
                "find /testbed -mindepth 1 -maxdepth 1 "
                f"-exec mv -t {FEATURE_FINAL_WORKSPACE_DIR} -- {{}} +",
                f"find /tmp -xdev -mindepth 1 -uid {AGENT_UID} "
                "-depth -delete 2>/dev/null || true",
                "find /logs/agent -mindepth 1 -maxdepth 1 "
                "! -name sessions ! -name memories ! -name kimi "
                "-exec rm -rf -- {} +",
                "rm -f /home/codemem-agent/.bash_history "
                "/home/codemem-agent/.zsh_history "
                "/home/codemem-agent/.python_history "
                "/home/codemem-agent/.lesshst",
            ]
        else:
            access_setup = [
                "rm -rf /testbed/.git",
                *fresh_baseline_lines(),
            ]

        workdir = step / "workdir"
        workdir.mkdir()
        setup = workdir / "setup.sh"
        setup.write_text(
            setup_script(
                "rm -f /testbed/codemem_answer.txt",
                *access_setup,
            )
        )
        setup.chmod(0o755)

    def _write_task_toml(
        self, task_dir: Path, task: FeatureTask, step_names: list[str]
    ) -> None:
        execution = self.execution
        timeout = (
            execution.code_agent_timeout_sec
            if task.family == CODE_FAMILY
            else execution.process_agent_timeout_sec
        )
        step_timeouts = [
            (
                name,
                execution.qa_agent_timeout_sec
                if name == "memory_qa"
                else timeout,
                execution.verifier_timeout_sec,
            )
            for name in step_names
        ]
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
                "access_mode": task.access_mode,
            },
            steps=step_timeouts,
            build_timeout_sec=execution.build_timeout_sec,
            cpus=execution.cpus,
            memory_mb=execution.memory_mb,
            storage_mb=execution.storage_mb,
        )
        (task_dir / "task.toml").write_text(content)
