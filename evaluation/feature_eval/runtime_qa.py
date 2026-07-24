#!/usr/bin/env python3
"""Record a CodeMem feature task's final memory answer and access state."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path("/testbed")
LOGS = Path("/logs/verifier")
CONFIG = Path("/tests/config.json")
STATE = Path("/var/lib/codemem-private/feature")
ANSWER = REPO / "codemem_answer.txt"


def main(step_name: str) -> None:
    if step_name != "memory_qa":
        raise ValueError(f"unexpected QA step: {step_name}")
    LOGS.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = json.loads(CONFIG.read_text())
    access_mode = config["access_mode"]
    answer = ANSWER.read_text().strip() if ANSWER.is_file() else ""

    visible_entries = sorted(
        path.name for path in REPO.iterdir() if path.name != ANSWER.name
    )
    private_workspace = STATE / "final-workspace"
    visible_git_commits: int | None = None
    if access_mode == "closed_book":
        access_enforced = (
            private_workspace.is_dir()
            and not (REPO / ".git").exists()
            and not visible_entries
        )
    elif access_mode == "open_book_final_tree_only":
        git_count = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if git_count.returncode == 0:
            try:
                visible_git_commits = int(git_count.stdout.strip())
            except ValueError:
                visible_git_commits = None
        access_enforced = (
            (REPO / ".git").is_dir() and visible_git_commits == 1
        )
    else:
        raise ValueError(f"unknown access mode: {access_mode!r}")

    (LOGS / "memory_answer.txt").write_text(answer + ("\n" if answer else ""))
    details = {
        "task_id": config["task_id"],
        "family": config["family"],
        "access_mode": access_mode,
        "answer": answer,
        "answer_recorded": bool(answer),
        "access_enforced": access_enforced,
        "visible_git_commits": visible_git_commits,
        "unexpected_visible_entries": visible_entries,
    }
    (LOGS / "metrics.json").write_text(json.dumps(details, indent=2) + "\n")
    (LOGS / "reward.json").write_text(
        json.dumps(
            {
                "memory_answer_recorded": bool(answer),
                "access_enforced": access_enforced,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"[codemem] phase=memory_qa "
        f"answer_recorded={str(bool(answer)).lower()} "
        f"access_enforced={str(access_enforced).lower()}",
        flush=True,
    )
    raise SystemExit(0 if answer and access_enforced else 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(__file__).name} <step_name>")
    main(sys.argv[1])
