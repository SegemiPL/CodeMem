#!/usr/bin/env python3
"""Non-scoring per-turn recorder for feature rollouts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path("/testbed")
LOGS = Path("/logs/verifier")
CONFIG = Path("/tests/config.json")
STATE = Path("/var/lib/codemem-private/feature")


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def workspace_patch(base_commit: str, turn_name: str) -> tuple[str, str | None]:
    """Capture the workspace through the root-private original repository."""
    STATE.mkdir(parents=True, exist_ok=True)
    index = STATE / f"{turn_name}.index"
    index.unlink(missing_ok=True)
    env = dict(
        os.environ,
        GIT_DIR=str(STATE / "original.git"),
        GIT_WORK_TREE=str(REPO),
        GIT_INDEX_FILE=str(index),
    )
    try:
        read_tree = run("git", "read-tree", base_commit, env=env)
        if read_tree.returncode != 0:
            return "", read_tree.stdout
        add = run("git", "add", "-A", "--", ".", env=env)
        if add.returncode != 0:
            return "", add.stdout
        diff = run("git", "diff", "--cached", "--binary", base_commit, env=env)
        if diff.returncode != 0:
            return "", diff.stdout
        return diff.stdout, None
    finally:
        index.unlink(missing_ok=True)


def main(turn_name: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = json.loads(CONFIG.read_text())
    turn = next(item for item in config["turns"] if item["name"] == turn_name)
    base_commit = turn["base_commit"]
    patch, error = workspace_patch(base_commit, turn_name)
    (LOGS / "workspace.patch").write_text(patch)
    status = run("git", "status", "--short")
    head = run("git", "rev-parse", "HEAD")
    details = {
        "task_id": config["task_id"],
        "family": config["family"],
        "turn": turn_name,
        "base_commit": base_commit,
        "expected_image_name": turn.get("image_name"),
        "actual_image_name": config.get("runtime_image"),
        "fresh_snapshot": turn.get("workspace_policy") == "fresh_snapshot",
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "git_status": status.stdout,
        "workspace_patch_bytes": len(patch.encode()),
        "artifact_error": error,
    }
    metrics = {
        "turn_completed": True,
        "artifacts_recorded": error is None,
    }
    (LOGS / "metrics.json").write_text(json.dumps(details, indent=2) + "\n")
    (LOGS / "reward.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(
        f"[codemem] phase={turn_name} turn_completed=true "
        f"artifacts_recorded={str(error is None).lower()}",
        flush=True,
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(__file__).name} <turn_name>")
    main(sys.argv[1])
