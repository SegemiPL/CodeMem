#!/usr/bin/env python3
"""Runtime verifier copied into each generated Harbor task."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path("/testbed")
STATE = Path("/tmp/codemem")
LOGS = Path("/logs/verifier")
CONFIG = Path("/tests/config.json")

# Per-test timeout so a hanging test cannot eat the whole verifier budget
# and leave the step without any metrics.
TEST_TIMEOUT_SEC = int(os.environ.get("CODEMEM_TEST_TIMEOUT_SEC", "600"))


def run(
    *args: str, check: bool = True, timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}")
    return result


def snapshot_tree() -> str:
    run("git", "add", "-A")
    tree = run("git", "write-tree").stdout.strip()
    run("git", "reset", "--mixed", "HEAD")
    return tree


def resolve_test_python() -> str:
    candidates = [
        os.environ.get("CODEMEM_TEST_PYTHON"),
        "/opt/miniconda3/envs/testbed/bin/python",
        "/opt/miniconda/envs/testbed/bin/python",
        sys.executable,
    ]
    attempted: list[str] = []
    for candidate in dict.fromkeys(item for item in candidates if item):
        attempted.append(candidate)
        try:
            probe = subprocess.run(
                [candidate, "-c", "import pytest"],
                cwd=REPO,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError:
            continue
        if probe.returncode == 0:
            return candidate
    raise RuntimeError(
        "No Python interpreter with pytest available; tried: " + ", ".join(attempted)
    )


def restore_tree(tree: str) -> None:
    run("git", "clean", "-fd")
    run("git", "read-tree", "--reset", "-u", tree)
    run("git", "reset", "--mixed", "HEAD")


def _patch_files(patch: str) -> list[str]:
    """Files touched by a unified diff (both headers, /dev/null excluded)."""
    return list(
        dict.fromkeys(
            line[6:]
            for line in patch.splitlines()
            if line.startswith(("--- a/", "+++ b/"))
        )
    )


def reset_test_files(patch: str, base_commit: str | None) -> None:
    """Reset the files a test patch touches to the instance's base commit.

    Agents frequently edit the same test files while solving, which makes a
    plain `git apply` of the test patch fail and would zero out the scores.
    The SWE-bench harness resets those files to the base commit first; files
    the patch adds anew are removed instead so the apply succeeds.
    """
    if not base_commit:
        return
    for path in _patch_files(patch):
        exists = run("git", "cat-file", "-e", f"{base_commit}:{path}", check=False)
        if exists.returncode == 0:
            run("git", "checkout", base_commit, "--", path)
        else:
            (REPO / path).unlink(missing_ok=True)


def test_instance(instance: dict[str, Any], workspace_tree: str) -> dict[str, Any]:
    patch = instance.get("test_patch") or ""
    result: dict[str, Any] = {
        "instance_id": instance["instance_id"],
        "tests": {},
        "expected_test_count": {
            kind: len(instance.get(kind, []))
            for kind in ("FAIL_TO_PASS", "PASS_TO_PASS")
        },
    }
    try:
        if patch:
            patch_path = STATE / f"{instance['instance_id']}.test.patch"
            patch_path.write_text(patch)
            reset_test_files(patch, instance.get("base_commit"))
            applied = run("git", "apply", "--check", str(patch_path), check=False)
            if applied.returncode != 0:
                result["patch_error"] = applied.stdout
                return result
            run("git", "apply", str(patch_path))
        try:
            test_python = resolve_test_python()
        except RuntimeError as exc:
            result["infrastructure_error"] = str(exc)
            return result
        result["test_python"] = test_python
        for kind in ("FAIL_TO_PASS", "PASS_TO_PASS"):
            for node_id in instance.get(kind, []):
                try:
                    completed = run(
                        test_python,
                        "-m",
                        "pytest",
                        "-q",
                        node_id,
                        check=False,
                        timeout=TEST_TIMEOUT_SEC,
                    )
                except subprocess.TimeoutExpired:
                    result["tests"][node_id] = {
                        "group": kind,
                        "status": "error",
                        "return_code": None,
                        "output": f"pytest timed out after {TEST_TIMEOUT_SEC} seconds",
                    }
                    continue
                status = (
                    "pass"
                    if completed.returncode == 0
                    else "fail"
                    if completed.returncode == 1
                    else "error"
                )
                result["tests"][node_id] = {
                    "group": kind,
                    "status": status,
                    "return_code": completed.returncode,
                    "output": completed.stdout[-12000:],
                }
    finally:
        restore_tree(workspace_tree)
    return result


def rate(result: dict[str, Any], group: str, expected: str) -> float:
    tests = [v for v in result["tests"].values() if v["group"] == group]
    if not tests:
        return 1.0 if result.get("expected_test_count", {}).get(group, 0) == 0 else 0.0
    return sum(item["status"] == expected for item in tests) / len(tests)


def test_middles(
    middles: list[dict[str, Any]], workspace_tree: str
) -> list[dict[str, Any]]:
    return [test_instance(middle, workspace_tree) for middle in middles]


def test_group_metrics(
    result: dict[str, Any], group: str, expected: str, correct_label: str = "correct"
) -> dict[str, int | float]:
    """Return readable count/ratio metrics for a test group.

    - total: number of tests considered for this group.
    - {correct_label}: number whose status matches `expected`.
    - ratio: {correct_label} / total (1.0 if no tests and none expected, 0.0 if
      none ran but some were expected).
    """
    tests = [v for v in result["tests"].values() if v["group"] == group]
    total_expected = result.get("expected_test_count", {}).get(group, 0)
    if not tests:
        if total_expected == 0:
            return {"total": 0, correct_label: 0, "ratio": 1.0}
        return {"total": total_expected, correct_label: 0, "ratio": 0.0}
    correct = sum(item["status"] == expected for item in tests)
    return {"total": len(tests), correct_label: correct, "ratio": correct / len(tests)}


def add_middle_metrics(
    metrics: dict[str, Any], results: list[dict[str, Any]]
) -> None:
    for index, result in enumerate(results, start=1):
        prefix = f"middle_{index:02d}"
        for suffix, group, expected in (
            ("fail_to_pass", "FAIL_TO_PASS", "pass"),
            ("pass_to_pass", "PASS_TO_PASS", "pass"),
        ):
            for key, value in test_group_metrics(result, group, expected, "passed").items():
                metrics[f"{prefix}_{suffix}_{key}"] = value


def files_match(reference_tree: str, current_tree: str, files: list[str], name: str) -> bool:
    completed = run(
        "git", "diff", "--no-ext-diff", "--binary", reference_tree, current_tree, "--", *files,
        check=False,
    )
    (LOGS / f"{name}.diff").write_text(completed.stdout)
    return completed.returncode == 0 and not completed.stdout


def _format_metric_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return str(value)


def _is_compaction_event(event: dict[str, Any]) -> bool:
    event_names = {
        "compact_boundary",
        "compaction_boundary",
        "compaction",
        "compacted",
        "context_compaction",
        "context_compacted",
    }
    candidates = [event]
    payload = event.get("payload")
    if isinstance(payload, dict):
        candidates.append(payload)
    return any(
        str(candidate.get(key, "")).lower() in event_names
        for candidate in candidates
        for key in ("type", "subtype")
    )


def agent_session_roots() -> list[tuple[str, Path]]:
    """Return resumable session stores for the supported Harbor agents."""
    return [
        # Harbor sets CLAUDE_CONFIG_DIR=/logs/agent/sessions for claude-code;
        # its session JSONLs live under the projects/ subdirectory. List it
        # first and scope it to projects/ so claude-code evidence is labeled
        # correctly instead of being picked up incidentally under the codex
        # root (which shares the same parent directory).
        ("claude-code", Path("/logs/agent/sessions/projects")),
        ("codex", Path("/logs/agent/sessions")),
        ("kimi-cli", Path("/logs/agent/kimi/share")),
    ]


def detect_compaction(sessions: Path | None = None) -> dict[str, Any]:
    roots = [("session", sessions)] if sessions else agent_session_roots()
    evidence: list[str] = []
    # Roots may nest (claude-code's projects/ lives inside codex's root);
    # scan each file once so events are not double-counted.
    seen: set[Path] = set()
    for agent, root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            if path in seen:
                continue
            seen.add(path)
            for line in path.read_text(errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and _is_compaction_event(event):
                    evidence.append(f"{agent}/{path.relative_to(root)}")
    return {
        "session_compacted_before_final": bool(evidence),
        "compaction_count": len(evidence),
        "evidence_files": sorted(set(evidence)),
    }


def checkpoint_session() -> list[str]:
    """Copy resumable session stores into STATE.

    Returns the labels of the session stores that were copied. An empty
    result means no supported agent session was found; callers record a
    warning instead of aborting the step so that earlier-phase metrics
    still get written (the restore branch will then fail its setup).
    """
    destination = STATE / "session_checkpoint"
    if destination.exists():
        shutil.rmtree(destination)
    copied: list[str] = []
    for agent, source in agent_session_roots():
        if not source.exists():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination / agent)
        copied.append(agent)
    return copied


def record_pre_final_session() -> dict[str, Any]:
    session = detect_compaction()
    (STATE / "session-before-final.json").write_text(
        json.dumps(session, indent=2) + "\n"
    )
    return session


def pre_final_session() -> dict[str, Any]:
    path = STATE / "session-before-final.json"
    if not path.is_file():
        raise RuntimeError("Pre-final session metrics were not recorded at checkpoint")
    return json.loads(path.read_text())


def main(phase: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    config = json.loads(CONFIG.read_text())
    target = config["target"]
    middles = config["middles"]
    current = snapshot_tree()
    details: dict[str, Any] = {"phase": phase, "workspace_tree": current}
    metrics: dict[str, Any] = {}

    # After solve target 
    if phase == "solve_target":
        # record the git tree
        (STATE / "after_target.tree").write_text(current + "\n")

        # test the target instance
        target_result = test_instance(target, current)

        # record the test result
        details["target"] = target_result
        metrics = {}
        for suffix, group, expected, label in (
            ("fail_to_pass", "FAIL_TO_PASS", "pass", "passed"),
            ("pass_to_pass", "PASS_TO_PASS", "pass", "passed"),
        ):
            for key, value in test_group_metrics(target_result, group, expected, label).items():
                metrics[f"target_{suffix}_{key}"] = value

    # Middle and compact
    elif phase.startswith("solve_middle_") or phase == "compact":
        # After a middle instance
        if phase.startswith("solve_middle_"):
            # Get the index
            middle_index = int(phase.rsplit("_", 1)[1])
            if middle_index < 1 or middle_index > len(middles):
                raise ValueError(f"Invalid middle step index in phase: {phase}")
            
            """ Problem: the middle test is not necessary
            We couldn't ensure that the middle instance can be resolved correctly, so testing middle instances is meaningless.
            Test the target when we resolve the LAST_MIDDLE_INSTANCE.
            """
            target_result = test_instance(target, current)
            middle_results = test_middles(middles[:middle_index], current)

            details.update(target=target_result, middles=middle_results)
            metrics = {}
            for suffix, group, expected, label in (
                ("fail_to_pass", "FAIL_TO_PASS", "pass", "passed"),
                ("pass_to_pass", "PASS_TO_PASS", "pass", "passed"),
            ):
                for key, value in test_group_metrics(target_result, group, expected, label).items():
                    metrics[f"target_{suffix}_{key}"] = value
            add_middle_metrics(metrics, middle_results)

            # if this is the last middle instance, save it as a checkpoint for restoration.
            is_checkpoint = middle_index == len(middles)
            if is_checkpoint:
                (STATE / "after_middles.tree").write_text(current + "\n")
        
        # After compact
        else:
            metrics = {}
            is_checkpoint = True
        
        # If this session should be resume, record it
        if is_checkpoint:
            copied = checkpoint_session()
            if not copied:
                details["session_checkpoint_error"] = (
                    "No agent session directory found under /logs/agent; "
                    "the restore branch will fail its setup"
                )
            session = record_pre_final_session()
            details["session"] = session

        if phase == "compact":
            metrics = {
                "session_compacted_before_final": session["session_compacted_before_final"],
                "manual_compaction_requested": config["manual_compaction_requested"],
            }
    
    # After revert the target
    elif phase == "revert_target":
        # Get the saved session id
        session = pre_final_session()
        details["session"] = session

        # Compaction may also happen during the revert step itself; scan the
        # live session again so the metrics show both checkpoints.
        session_at_revert = detect_compaction()
        details["session_at_revert"] = session_at_revert

        # Get the current results of the target test
        target_result = test_instance(target, current)

        # Problem as above
        middle_results = test_middles(middles, current)
        details.update(target=target_result, middles=middle_results)

        # matrics
        metrics = {
            # git diff
            "file_revert_match": files_match(
                (STATE / "baseline.tree").read_text().strip(), current,
                target["touched_files"], "target-revert",
            ),

            # whether the session has been compacted at any point up to and
            # including the revert step (pre-final checkpoint OR live scan)
            "session_compacted_before_final": (
                session["session_compacted_before_final"]
                or session_at_revert["session_compacted_before_final"]
            ),

            # whether we manually require the session to be compacted
            "manual_compaction_requested": config["manual_compaction_requested"],
        }
        for suffix, group, expected, label in (
            ("fail_to_pass", "FAIL_TO_PASS", "fail", "reverted"),
            ("pass_to_pass", "PASS_TO_PASS", "pass", "passed"),
        ):
            for key, value in test_group_metrics(target_result, group, expected, label).items():
                metrics[f"target_{suffix}_{key}"] = value
        add_middle_metrics(metrics, middle_results)
    
    # After restore the target
    elif phase == "restore_target":
        # Get the session-id
        session = pre_final_session()
        details["session"] = session

        # Compaction may also have happened since the pre-final checkpoint
        # (during revert or the restore step itself); rescan the live session.
        session_at_restore = detect_compaction()
        details["session_at_restore"] = session_at_restore

        # Test the result
        target_result = test_instance(target, current)

        # Problem as above
        middle_results = test_middles(middles, current)
        details.update(target=target_result, middles=middle_results)

        # matrics
        metrics = {
            # git diff
            "file_restore_match": files_match(
                (STATE / "after_target.tree").read_text().strip(), current,
                target["touched_files"], "target-restore",
            ),

            # whether the session has been compacted at any point up to the
            # end of the trial (pre-final checkpoint OR live scan)
            "session_compacted_before_final": (
                session["session_compacted_before_final"]
                or session_at_restore["session_compacted_before_final"]
            ),

            # whether we manually require the session to be compacted
            "manual_compaction_requested": config["manual_compaction_requested"],
        }
        for suffix, group, expected, label in (
            ("fail_to_pass", "FAIL_TO_PASS", "pass", "passed"),
            ("pass_to_pass", "PASS_TO_PASS", "pass", "passed"),
        ):
            for key, value in test_group_metrics(target_result, group, expected, label).items():
                metrics[f"target_{suffix}_{key}"] = value
        add_middle_metrics(metrics, middle_results)

    else:
        raise ValueError(f"Unknown evaluation phase: {phase}")

    (LOGS / "metrics.json").write_text(json.dumps(details, indent=2) + "\n")
    (LOGS / "reward.json").write_text(json.dumps(metrics, indent=2) + "\n")

    # One-line progress marker; Harbor captures verifier stdout into the
    # trial log, so this is what the monitor and humans grep for.
    summary = " ".join(
        f"{key}={_format_metric_value(value)}" for key, value in sorted(metrics.items())
    )
    print(f"[codemem] phase={phase} {summary}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(__file__).name} <phase>")
    main(sys.argv[1])
