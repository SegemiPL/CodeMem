from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from evaluation.common.naming import safe_name, swegym_image

__all__ = [
    "CODE_FAMILY",
    "PROCESS_FAMILY",
    "FAMILIES",
    "FeatureTurn",
    "FeatureTask",
    "safe_name",
    "swegym_image",
    "load_task",
    "discover_tasks",
    "select_tasks",
]


CODE_FAMILY = "code"
PROCESS_FAMILY = "process"
FAMILIES = (CODE_FAMILY, PROCESS_FAMILY)


@dataclass(frozen=True)
class FeatureTurn:
    index: int
    instruction: str
    base_commit: str
    image_name: str | None
    workspace_policy: str | None
    inherits_previous_working_tree: bool | None
    source_instance_id: str | None
    instruction_source: str | None
    role: str | None


@dataclass(frozen=True)
class FeatureTask:
    family: str
    task_id: str
    subtype: str
    status: str
    repository: str
    image: str
    start_base_commit: str
    turns: tuple[FeatureTurn, ...]
    source_path: Path


def _required(record: dict[str, Any], key: str, source: Path) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: missing non-empty {key}")
    return value


def load_task(
    path: Path,
    family: str,
    repo_image_map: dict[str, str] | None = None,
) -> FeatureTask:
    if family not in FAMILIES:
        raise ValueError(f"Unknown feature family: {family}")
    raw = json.loads(path.read_text())
    task_id = _required(raw, "task_id", path)
    repository = _required(raw, "repository", path)
    raw_turns = raw.get("turns")
    if not isinstance(raw_turns, list) or len(raw_turns) != 20:
        count = len(raw_turns) if isinstance(raw_turns, list) else "missing"
        raise ValueError(f"{path}: expected exactly 20 turns, got {count}")

    expected_indices = list(range(1, 21))
    indices = [turn.get("turn") for turn in raw_turns]
    if indices != expected_indices:
        raise ValueError(f"{path}: turns must be numbered 1 through 20")

    start = _required(raw, "start_base_commit", path)
    if raw_turns[0].get("base_commit") != start:
        raise ValueError(f"{path}: start_base_commit must match turn 1 base_commit")

    repo_image_map = repo_image_map or {}
    if family == CODE_FAMILY:
        first_instance = _required(raw_turns[0], "source_instance_id", path)
        # Canonical per-repo image; turn 1's setup.sh checks out the base commit.
        image = repo_image_map.get(repository, swegym_image(first_instance))
    else:
        image = repo_image_map.get(
            repository, _required(raw_turns[0], "image_name", path)
        )
        if any(
            turn.get("workspace_policy") != "fresh_snapshot"
            or turn.get("inherits_previous_working_tree") is not False
            or not isinstance(turn.get("image_name"), str)
            or not turn["image_name"].strip()
            for turn in raw_turns
        ):
            raise ValueError(
                f"{path}: every process turn must use a fresh snapshot with an image"
            )

    turns = []
    for turn in raw_turns:
        instruction = (
            turn.get("agent_facing_instruction")
            if family == PROCESS_FAMILY
            else turn.get("instruction")
        )
        if family == PROCESS_FAMILY and not instruction:
            instruction = turn.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError(f"{path}: turn {turn['turn']} has no agent instruction")
        if family == PROCESS_FAMILY:
            instruction = (
                instruction.replace("/testbed/repo", "/testbed")
                .replace("/workspace", "/testbed")
                .replace("workspace_dir_name", "/testbed")
            )
        turns.append(
            FeatureTurn(
                index=turn["turn"],
                instruction=instruction.strip() + "\n",
                base_commit=_required(turn, "base_commit", path),
                image_name=turn.get("image_name"),
                workspace_policy=turn.get("workspace_policy"),
                inherits_previous_working_tree=turn.get(
                    "inherits_previous_working_tree"
                ),
                source_instance_id=turn.get("source_instance_id"),
                instruction_source=turn.get("instruction_source"),
                role=turn.get("role"),
            )
        )

    return FeatureTask(
        family=family,
        task_id=task_id,
        subtype=_required(raw, "subtype", path),
        status=_required(raw, "status", path),
        repository=repository,
        image=image,
        start_base_commit=start,
        turns=tuple(turns),
        source_path=path,
    )


def discover_tasks(
    root: Path,
    family: str,
    repo_image_map: dict[str, str] | None = None,
) -> list[FeatureTask]:
    paths = sorted(root.glob("*/tasks/*/task.json"))
    if not paths:
        raise FileNotFoundError(f"No feature task JSON files found under {root}")
    tasks = [load_task(path, family, repo_image_map) for path in paths]
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.task_id] = counts.get(task.task_id, 0) + 1
    duplicates = sorted(task_id for task_id, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"Duplicate task IDs under {root}: {duplicates}")
    return tasks


def select_tasks(
    tasks: Iterable[FeatureTask],
    *,
    task_ids: set[str] | None = None,
    subtypes: set[str] | None = None,
) -> list[FeatureTask]:
    selected = [
        task
        for task in tasks
        if (not task_ids or task.task_id in task_ids)
        and (not subtypes or task.subtype in subtypes)
    ]
    if task_ids:
        missing = sorted(task_ids - {task.task_id for task in selected})
        if missing:
            raise KeyError(f"Requested task IDs were not found: {missing}")
    return selected
