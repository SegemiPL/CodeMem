"""setup.sh script builders shared by CodeMem task generators.

Every generated script is a one-shot bash snippet: harbor runs a step's
workdir/setup.sh before the agent starts, and the script deletes itself
so retries/resumes do not re-apply it.
"""

from __future__ import annotations

from evaluation.common.isolation import (
    AGENT_GID,
    AGENT_HOME,
    AGENT_UID,
    PRIVATE_GIT_DIR,
    REVERT_STATE_DIR,
)


def setup_script(*lines: str) -> str:
    """Assemble a one-shot setup.sh from body lines."""
    return "\n".join(
        [
            "#!/bin/bash",
            "set -euo pipefail",
            'script_path="$0"',
            "cd /testbed",
            'rm -f -- "$script_path"',
            "find /tests -mindepth 1 -depth -delete 2>/dev/null || true",
            "find /logs/verifier -mindepth 1 -depth -delete 2>/dev/null || true",
            *lines,
            f"chown -R {AGENT_UID}:{AGENT_GID} /testbed /logs/agent {AGENT_HOME}",
            "",
        ]
    )


def checkout_lines(base_commit: str, *, clean_args: str = "-fd") -> list[str]:
    """Hard-reset the repo to a base commit and clean the working tree."""
    return [
        f"git reset --hard {base_commit}",
        f"git clean {clean_args}",
    ]


def snapshot_restore_lines(tree_file: str) -> list[str]:
    """Restore a root-private tree without exposing the oracle repository."""
    return [
        "rm -rf /testbed/.git",
        f"GIT_DIR={PRIVATE_GIT_DIR} GIT_WORK_TREE=/testbed git clean -fdx",
        f"GIT_DIR={PRIVATE_GIT_DIR} GIT_WORK_TREE=/testbed "
        f'git read-tree --reset -u "$(cat {tree_file})"',
    ]


def fresh_baseline_lines() -> list[str]:
    """Give the agent a history-free repository for the materialized phase."""
    return [
        "git init -q /testbed",
        "git config user.name CodeMem",
        "git config user.email codemem@local",
        "git add -A",
        "git commit -qm 'CodeMem phase baseline'",
    ]


def private_checkout_lines(base_commit: str) -> list[str]:
    """Materialize a source commit from the root-only original repository."""
    return [
        "rm -rf /testbed/.git",
        f"GIT_DIR={PRIVATE_GIT_DIR} GIT_WORK_TREE=/testbed "
        f"git reset --hard {base_commit}",
        f"GIT_DIR={PRIVATE_GIT_DIR} GIT_WORK_TREE=/testbed git clean -fdx",
        *fresh_baseline_lines(),
    ]


def initialize_private_repo_lines(base_commit: str) -> list[str]:
    """Move the image's Git history behind the privilege boundary once."""
    return [
        f"install -d -m 0700 -o root -g root {REVERT_STATE_DIR}",
        f"test ! -e {PRIVATE_GIT_DIR}",
        f"mv /testbed/.git {PRIVATE_GIT_DIR}",
        *private_checkout_lines(base_commit),
    ]


# Restore the agent session stores checkpointed by the runtime evaluator.
# Only the branch matching the current run's agent exists on disk.
SESSION_RESTORE_LINES: list[str] = [
    f"if test -d {REVERT_STATE_DIR}/session_checkpoint/codex; then",
    "    rm -rf /logs/agent/sessions",
    "    mkdir -p /logs/agent",
    f"    cp -a {REVERT_STATE_DIR}/session_checkpoint/codex /logs/agent/sessions",
    "fi",
    f"if test -d {REVERT_STATE_DIR}/session_checkpoint/codex-memories; then",
    "    rm -rf /logs/agent/memories",
    "    mkdir -p /logs/agent",
    f"    cp -a {REVERT_STATE_DIR}/session_checkpoint/codex-memories /logs/agent/memories",
    "fi",
    f"if test -d {REVERT_STATE_DIR}/session_checkpoint/claude-code; then",
    "    mkdir -p /logs/agent/sessions",
    "    rm -rf /logs/agent/sessions/projects",
    f"    cp -a {REVERT_STATE_DIR}/session_checkpoint/claude-code /logs/agent/sessions/projects",
    "fi",
    f"if test -d {REVERT_STATE_DIR}/session_checkpoint/kimi-cli; then",
    "    rm -rf /logs/agent/kimi/share",
    "    mkdir -p /logs/agent/kimi",
    f"    cp -a {REVERT_STATE_DIR}/session_checkpoint/kimi-cli /logs/agent/kimi/share",
    "fi",
]
