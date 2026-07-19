"""Terminal progress monitor for a running Harbor revert job.

Polls a Harbor jobs directory and prints one line whenever a trial moves
to a new step, a step's verifier finishes, or a trial completes, so a long
evaluation shows where it is without digging through trial logs.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _metrics_summary(metrics: dict[str, Any]) -> str:
    def _fmt(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return f"{value:.2f}"
        return str(value)

    return " ".join(
        f"{key}={_fmt(value)}"
        for key, value in sorted(metrics.items())
        if isinstance(value, (int, float, bool))
    )


def _step_names_from_toml(text: str) -> list[str]:
    if tomllib is not None:
        data = tomllib.loads(text)
        return [step["name"] for step in data.get("steps", [])]
    # Regex fallback: our generated task.toml always writes [[steps]] blocks
    # as 'name = "..."' on the line right after the header.
    return re.findall(r'\[\[steps\]\]\s*\n\s*name\s*=\s*"([^"]+)"', text)


def _load_step_names(trial: Path) -> list[str]:
    """Step order from the task's task.toml, via the trial's config.json."""
    try:
        config = json.loads((trial / "config.json").read_text())
        task_path = Path(config["task"]["path"])
        return _step_names_from_toml((task_path / "task.toml").read_text())
    except Exception:
        return []


def _is_trial(path: Path) -> bool:
    config = path / "config.json"
    if not (path.is_dir() and config.is_file()):
        return False
    try:
        return "trial_name" in json.loads(config.read_text())
    except json.JSONDecodeError:
        return False


def _iter_trials(job_dir: Path) -> list[Path]:
    """Trial dirs directly under job_dir, or one level down (Harbor creates
    a timestamped job directory under jobs_dir)."""
    trials = [path for path in job_dir.iterdir() if _is_trial(path)]
    if not trials:
        trials = [
            path
            for child in job_dir.iterdir()
            if child.is_dir()
            for path in child.iterdir()
            if _is_trial(path)
        ]
    return sorted(trials)


def scan(job_dir: Path, state: dict[str, dict[str, Any]]) -> list[str]:
    """One polling pass; returns new progress lines and mutates state.

    Harbor layout per trial: ``steps/<name>/{agent,verifier}/`` dirs are
    created when a step starts; while the step runs, its live output is in
    the trial-root ``agent/`` and ``verifier/`` mounts; at step end those
    contents are moved into the step dir. A step counts as verified once
    its ``verifier/reward.json`` exists (live mount or archived).
    """
    lines: list[str] = []
    for trial in _iter_trials(job_dir):
        entry = state.setdefault(
            trial.name,
            {"done": set(), "active": None, "finished": False, "step_names": None},
        )
        if entry["step_names"] is None:
            entry["step_names"] = _load_step_names(trial)
        step_names: list[str] = entry["step_names"]

        steps_dir = trial / "steps"
        started = (
            {path.name for path in steps_dir.iterdir() if path.is_dir()}
            if steps_dir.is_dir()
            else set()
        )
        verified = {
            name
            for name in started
            if (steps_dir / name / "verifier" / "reward.json").is_file()
        }

        # Steps whose verifier results just became available.
        for name in [n for n in step_names if n in verified - entry["done"]]:
            metrics = json.loads(
                (steps_dir / name / "verifier" / "reward.json").read_text()
            )
            lines.append(
                f"[{_now()}] {trial.name}: step {name} verified — "
                f"{_metrics_summary(metrics)}"
            )
            entry["done"].add(name)

        # The active step's verifier may have just written metrics to the
        # live mount, moments before they are moved into the step dir.
        active = next(
            (n for n in step_names if n in started and n not in verified), None
        )
        live_metrics = trial / "verifier" / "reward.json"
        if active and active not in entry["done"] and live_metrics.is_file():
            try:
                metrics = json.loads(live_metrics.read_text())
            except json.JSONDecodeError:
                metrics = None  # partially written; retry next pass
            if metrics is not None:
                lines.append(
                    f"[{_now()}] {trial.name}: step {active} verified — "
                    f"{_metrics_summary(metrics)}"
                )
                entry["done"].add(active)

        # Trial completion.
        result_path = trial / "result.json"
        if not entry["finished"] and result_path.is_file():
            try:
                result = json.loads(result_path.read_text())
            except json.JSONDecodeError:
                continue  # partially written; retry next pass
            if not result.get("finished_at"):
                continue
            entry["finished"] = True
            verifier_result = result.get("verifier_result") or {}
            metrics = verifier_result.get("rewards") or {}
            exception = result.get("exception_info")
            if metrics:
                lines.append(
                    f"[{_now()}] {trial.name}: TRIAL FINISHED — "
                    f"{_metrics_summary(metrics)}"
                )
            elif exception:
                lines.append(
                    f"[{_now()}] {trial.name}: TRIAL FAILED — "
                    f"{str(exception.get('exception_type') or exception)[:200]}"
                )
            else:
                lines.append(f"[{_now()}] {trial.name}: TRIAL FINISHED")

        # Current position within the step sequence (skip finished trials).
        if active != entry["active"] and not entry["finished"]:
            entry["active"] = active
            if active is not None:
                index = step_names.index(active) + 1
                lines.append(
                    f"[{_now()}] {trial.name}: running step {active} "
                    f"({index}/{len(step_names)})"
                )
    return lines


def run(job_dir: Path, interval: float = 10.0, once: bool = False) -> None:
    state: dict[str, dict[str, Any]] = {}
    print(f"Monitoring {job_dir} (poll every {interval:.0f}s, Ctrl-C to stop)")
    while True:
        for line in scan(job_dir, state):
            print(line, flush=True)
        if once:
            return
        if state and all(entry["finished"] for entry in state.values()):
            print("All trials finished.")
            return
        time.sleep(interval)
