from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

from evaluation.harbor import write_job_config

from .config import RevertEvalConfig


@dataclass(frozen=True)
class Instance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    touched_files: tuple[str, ...]

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Instance":
        patch = record.get("patch") or ""
        # Both diff headers are collected so pure file deletions
        # (+++ /dev/null) and rename sources are covered as well.
        touched = tuple(
            dict.fromkeys(
                match.group(1)
                for match in re.finditer(
                    r"^(?:--- a/|\+\+\+ b/)(.+)$", patch, flags=re.MULTILINE
                )
            )
        )
        return cls(
            instance_id=record["instance_id"],
            repo=record["repo"],
            base_commit=record["base_commit"],
            problem_statement=record["problem_statement"],
            patch=patch,
            test_patch=record.get("test_patch") or "",
            fail_to_pass=tuple(record.get("FAIL_TO_PASS") or ()),
            pass_to_pass=tuple(record.get("PASS_TO_PASS") or ()),
            touched_files=touched,
        )


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-")


def _format_prompt(template: str, instance: Instance, **extra: str) -> str:
    values = {
        "instance_id": instance.instance_id,
        "problem_statement": instance.problem_statement.strip(),
        **extra,
    }
    return template.format(**values).strip() + "\n"


class RevertTaskGenerator:
    def __init__(
        self,
        ordered_candidates_path: Path,
        dataset_path: Path,
        output_root: Path,
        config: RevertEvalConfig,
        repo_image_map: dict[str, str] | None = None,
    ) -> None:
        self.ordered_path = ordered_candidates_path
        self.dataset_path = dataset_path
        self.output_root = output_root
        self.config = config
        # Canonical per-repo images; tasks check out their own base commit.
        self.repo_image_map = repo_image_map or {}
        self._ordered = json.loads(self.ordered_path.read_text())["targets"]
        self._records = self._build_record_index()

    def _build_record_index(self) -> dict[str, Instance]:
        records: dict[str, Instance] = {}
        if self.dataset_path.suffix == ".parquet":
            try:
                import pyarrow.parquet as pq
            except ImportError as exc:
                raise RuntimeError(
                    "Reading the canonical SWE-Gym dataset requires pyarrow; "
                    "install it or pass the legacy code_mem_dataset.json via --dataset"
                ) from exc
            for record in pq.read_table(self.dataset_path).to_pylist():
                records[record["instance_id"]] = Instance.from_record(record)
        elif self.dataset_path.suffix == ".json":
            raw = json.loads(self.dataset_path.read_text())["tasks"]["Revert"]
            for pair in raw:
                for record in [pair.get("target"), *(pair.get("middle") or [])]:
                    if record:
                        records[record["instance_id"]] = Instance.from_record(record)
        else:
            raise ValueError(
                f"Unsupported SWE-Gym dataset format: {self.dataset_path} "
                "(expected .parquet or .json)"
            )
        return records

    def candidates(
        self,
        target_id: str,
        middle_ids: Sequence[str] | None = None,
        middle_count: int | None = None,
    ) -> tuple[Instance, tuple[Instance, ...]]:
        selected = next(
            (item for item in self._ordered if item["target"]["instance_id"] == target_id),
            None,
        )
        if selected is None:
            raise KeyError(f"Target is not in ordered candidates: {target_id}")
        chain = selected["longest_ordered_chain"]
        if not chain:
            raise ValueError(f"Target has no ordered middle candidates: {target_id}")
        available_ids = [item["instance_id"] for item in chain]
        if middle_ids:
            selected_ids = list(middle_ids)
            unknown = [item for item in selected_ids if item not in available_ids]
            if unknown:
                raise ValueError(
                    f"Middle instances are not in target {target_id}'s selected "
                    f"ordered chain: {unknown}"
                )
            positions = [available_ids.index(item) for item in selected_ids]
            if positions != sorted(positions) or len(set(selected_ids)) != len(selected_ids):
                raise ValueError("Explicit middle instances must be unique and in chain order")
        else:
            count = (
                middle_count
                if middle_count is not None
                else self.config.execution.middle_count
            )
            if count < 1:
                raise ValueError("middle_count must be at least 1")
            if count > len(chain):
                raise ValueError(
                    f"Requested {count} middle instances, but target {target_id} "
                    f"has only {len(chain)} in its ordered chain"
                )
            selected_ids = available_ids[:count]
        try:
            target = self._records[target_id]
            middles = tuple(self._records[item] for item in selected_ids)
        except KeyError as exc:
            raise KeyError(
                f"Full SWE-Gym record missing for ordered instance {exc.args[0]}"
            ) from exc
        target_files = tuple(selected["target"].get("touched_files") or target.touched_files)
        if not target_files:
            raise ValueError(f"Target has no touched files: {target_id}")
        middle_by_id = {item["instance_id"]: item for item in chain}
        resolved_middles = tuple(
            replace(
                middle,
                touched_files=tuple(
                    middle_by_id[middle.instance_id].get("touched_files")
                    or middle.touched_files
                ),
            )
            for middle in middles
        )
        return replace(target, touched_files=target_files), resolved_middles

    def candidate(
        self, target_id: str, middle_id: str | None = None
    ) -> tuple[Instance, Instance]:
        """Backward-compatible single-middle selection."""
        target, middles = self.candidates(
            target_id, [middle_id] if middle_id else None, middle_count=1
        )
        return target, middles[0]

    def generate(
        self,
        target_id: str,
        middle_ids: Sequence[str] | None = None,
        middle_count: int | None = None,
        *,
        overwrite: bool = False,
    ) -> Path:
        # Get the target instance and middle instance
        target, middles = self.candidates(target_id, middle_ids, middle_count)

        # Dir suffix: first middle id plus, for multi-middle tasks, the count
        # and a short digest of the full middle list so that different middle
        # combinations can never collide on the same task directory.
        suffix = middles[0].instance_id
        if len(middles) > 1:
            digest = hashlib.sha1(
                "|".join(middle.instance_id for middle in middles).encode()
            ).hexdigest()[:8]
            suffix += f"--{len(middles)}-middles-{digest}"
        task_dir = self.output_root / _safe_name(f"{target.instance_id}--{suffix}")
        if task_dir.exists():
            if not overwrite:
                raise FileExistsError(f"Task already exists: {task_dir}")
            shutil.rmtree(task_dir)

        # task adapter for harbor
        (task_dir / "environment").mkdir(parents=True)
        (task_dir / "tests").mkdir()
        middle_steps = [f"solve_middle_{index:02d}" for index in range(1, len(middles) + 1)]

        # Create All Steps
        steps = ["solve_target", *middle_steps]
        if self.config.execution.manual_compact_before_final:
            steps.append("compact")
        steps.extend(["revert_target", "restore_target"]) 
        for step in steps:
            (task_dir / "steps" / step / "tests").mkdir(parents=True)

        # Basic image: canonical per-repo image when a map is provided; the
        # task's solve_target setup.sh checks out this instance's base commit.
        image = self.repo_image_map.get(
            target.repo,
            "xingyaoww/sweb.eval.x86_64."
            + target.instance_id.replace("__", "_s_").lower(),
        )

        # Env File
        # safe.directory: on providers where the exec user differs from the
        # image's /testbed owner (e.g. Modal gVisor), git refuses to operate
        # without it. System-level config covers agent and verifier users.
        (task_dir / "environment" / "Dockerfile").write_text(
            f"FROM {image}\nWORKDIR /testbed\n"
            "RUN git config --system --add safe.directory /testbed"
            " && mkdir -p /logs /tmp/codemem\n"
        )

        # Metadata
        metadata = {
            "target": self._instance_json(target),
            "middles": [self._instance_json(middle) for middle in middles],
            "manual_compaction_requested": self.config.execution.manual_compact_before_final,
        }

        # Tests config
        (task_dir / "tests" / "config.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )

        # Evaluator src
        evaluator_source = Path(__file__).with_name("runtime_evaluator.py").read_text()
        (task_dir / "tests" / "evaluate.py").write_text(evaluator_source)

        # whole task toml (all the instances) 
        # like metadata, per step resource restriction 
        self._write_task_toml(task_dir, steps, target, middles)

        # for target and middles, write instructions
        # per instance specific instruction
        self._write_instructions(task_dir, target, middles)

        # for targets, create set up scripts!!!
        # Every solve step (target and each middle) checks out its own base
        # commit and cleans the working tree before the agent starts.
        self._write_setup_scripts(task_dir, target, middles)
        for step in steps:
            script = task_dir / "steps" / step / "tests" / "test.sh"
            script.write_text(
                "#!/bin/bash\nset -u\npython3 /tests/evaluate.py " + step + "\n"
            )
            script.chmod(0o755)
        return task_dir

    @staticmethod
    def _instance_json(instance: Instance) -> dict[str, Any]:
        return {
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "base_commit": instance.base_commit,
            "test_patch": instance.test_patch,
            "FAIL_TO_PASS": list(instance.fail_to_pass),
            "PASS_TO_PASS": list(instance.pass_to_pass),
            "touched_files": list(instance.touched_files),
        }

    def _write_task_toml(
        self,
        task_dir: Path,
        steps: list[str],
        target: Instance,
        middles: tuple[Instance, ...],
    ) -> None:
        e = self.config.execution
        # Steps
        step_blocks = "\n".join(
            f'''[[steps]]
name = "{name}"
[steps.agent]
timeout_sec = {e.agent_timeout_sec}
[steps.verifier]
timeout_sec = {e.verifier_timeout_sec}
environment_mode = "shared"
'''
            for name in steps
        )

        # Main content
        content = f'''schema_version = "1.3"
multi_step_reward_strategy = "final"

[task]
name = "codemem/{task_dir.name}"
description = "Checkpointed target revert and deleted-file restoration evaluation"

[metadata]
benchmark = "CodeMem-Revert"
target_instance_id = "{target.instance_id}"
middle_instance_ids = {json.dumps([middle.instance_id for middle in middles])}
middle_count = {len(middles)}

[environment]
build_timeout_sec = {e.build_timeout_sec}
cpus = {e.cpus}
memory_mb = {e.memory_mb}
storage_mb = {e.storage_mb}
workdir = "/testbed"

{step_blocks}'''

        # Write the toml
        (task_dir / "task.toml").write_text(content)

    def _write_instructions(
        self, task_dir: Path, target: Instance, middles: tuple[Instance, ...]
    ) -> None:
        prompts = self.config.prompts
        middle_ids = "\n".join(f"- {middle.instance_id}" for middle in middles)
        values = {
            "target_instance_id": target.instance_id,
            "middle_instance_id": ", ".join(middle.instance_id for middle in middles),
            "middle_instance_ids": middle_ids,
        }
        instructions = {
            "solve_target": _format_prompt(prompts.solve_target, target),
            "compact": prompts.manual_compact.strip() + "\n",
            "revert_target": _format_prompt(prompts.revert_target, target, **values),
            "restore_target": _format_prompt(prompts.restore_target, target, **values),
        }
        for index, middle in enumerate(middles, start=1):
            instructions[f"solve_middle_{index:02d}"] = _format_prompt(
                prompts.solve_middle,
                middle,
                middle_index=str(index),
                middle_count=str(len(middles)),
            )
        for step, instruction in instructions.items():
            step_dir = task_dir / "steps" / step
            if step_dir.exists():
                (step_dir / "instruction.md").write_text(instruction)

    def _write_setup_scripts(
        self, task_dir: Path, target: Instance, middles: tuple[Instance, ...]
    ) -> None:
        first_workdir = task_dir / "steps" / "solve_target" / "workdir"
        first_workdir.mkdir()
        first = first_workdir / "setup.sh"
        first.write_text(
            f'''#!/bin/bash
set -euo pipefail
cd /testbed
git reset --hard {target.base_commit}
git clean -fd
mkdir -p /tmp/codemem
git rev-parse '{target.base_commit}^{{tree}}' > /tmp/codemem/baseline.tree
rm -f -- "$0"
'''
        )
        first.chmod(0o755)

        # Every middle step starts from its own base commit.
        for index, middle in enumerate(middles, start=1):
            middle_workdir = (
                task_dir / "steps" / f"solve_middle_{index:02d}" / "workdir"
            )
            middle_workdir.mkdir()
            setup = middle_workdir / "setup.sh"
            setup.write_text(
                f'''#!/bin/bash
set -euo pipefail
cd /testbed
git reset --hard {middle.base_commit}
git clean -fd
rm -f -- "$0"
'''
            )
            setup.chmod(0o755)

        # Revert starts from the recorded post-target snapshot (target base
        # + the agent's own target solution) rather than whatever the last
        # middle step left in the working tree.
        revert_workdir = task_dir / "steps" / "revert_target" / "workdir"
        revert_workdir.mkdir()
        revert = revert_workdir / "setup.sh"
        revert.write_text(
            '''#!/bin/bash
set -euo pipefail
cd /testbed
test -s /tmp/codemem/after_target.tree
git clean -fd
git read-tree --reset -u "$(cat /tmp/codemem/after_target.tree)"
git reset --mixed HEAD
rm -f -- "$0"
'''
        )
        revert.chmod(0o755)

        restore_workdir = task_dir / "steps" / "restore_target" / "workdir"
        restore_workdir.mkdir()
        restore = restore_workdir / "setup.sh"
        quoted_files = " ".join(_shell_quote(path) for path in target.touched_files)
        restore.write_text(
            f'''#!/bin/bash
set -euo pipefail
cd /testbed
test -s /tmp/codemem/after_target.tree
test -d /tmp/codemem/session_checkpoint
git clean -fd
git read-tree --reset -u "$(cat /tmp/codemem/after_target.tree)"
git reset --mixed HEAD
if test -d /tmp/codemem/session_checkpoint/codex; then
    rm -rf /logs/agent/sessions
    mkdir -p /logs/agent
    cp -a /tmp/codemem/session_checkpoint/codex /logs/agent/sessions
fi
if test -d /tmp/codemem/session_checkpoint/claude-code; then
    mkdir -p /logs/agent/sessions
    rm -rf /logs/agent/sessions/projects
    cp -a /tmp/codemem/session_checkpoint/claude-code /logs/agent/sessions/projects
fi
if test -d /tmp/codemem/session_checkpoint/kimi-cli; then
    rm -rf /logs/agent/kimi/share
    mkdir -p /logs/agent/kimi
    cp -a /tmp/codemem/session_checkpoint/kimi-cli /logs/agent/kimi/share
fi
rm -f -- {quoted_files}
rm -f -- "$0"
'''
        )
        restore.chmod(0o755)

    def write_job_config(
        self,
        path: Path,
        *,
        tasks_path: Path,
        agent: str,
        model: str,
        environment: str,
        concurrency: int,
        jobs_dir: Path,
        agent_toolchain: Path | None = None,
        agent_version: str | None = None,
        n_attempts: int = 1,
    ) -> Path:
        return write_job_config(
            path,
            tasks_path=tasks_path,
            agent=agent,
            model=model,
            environment=environment,
            concurrency=concurrency,
            jobs_dir=jobs_dir,
            record_trajectory=self.config.execution.record_trajectory,
            agent_toolchain=agent_toolchain,
            agent_version=agent_version,
            n_attempts=n_attempts,
        )


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
