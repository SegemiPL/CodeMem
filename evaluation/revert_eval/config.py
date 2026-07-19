from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Prompts:
    solve_target: str
    solve_middle: str
    revert_target: str
    restore_target: str
    manual_compact: str


@dataclass(frozen=True)
class Execution:
    middle_count: int = 1
    agent_timeout_sec: int = 3000
    verifier_timeout_sec: int = 1800
    build_timeout_sec: int = 1800
    cpus: int = 1
    memory_mb: int = 8192
    storage_mb: int = 10240
    manual_compact_before_final: bool = False
    record_trajectory: bool = True


@dataclass(frozen=True)
class RevertEvalConfig:
    prompts: Prompts
    execution: Execution


def _required(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing non-empty configuration value: prompts.{key}")
    return value


def load_config(path: Path) -> RevertEvalConfig:
    data = tomllib.loads(path.read_text())
    prompt_data = data.get("prompts", {})
    execution_data = data.get("execution", {})
    unknown = sorted(set(execution_data) - {field.name for field in fields(Execution)})
    if unknown:
        raise ValueError(f"Unknown execution configuration keys: {unknown}")
    execution = Execution(**execution_data)
    if execution.middle_count < 1:
        raise ValueError("execution.middle_count must be at least 1")
    return RevertEvalConfig(
        prompts=Prompts(
            solve_target=_required(prompt_data, "solve_target"),
            solve_middle=_required(prompt_data, "solve_middle"),
            revert_target=_required(prompt_data, "revert_target"),
            restore_target=_required(prompt_data, "restore_target"),
            manual_compact=_required(prompt_data, "manual_compact"),
        ),
        execution=execution,
    )
