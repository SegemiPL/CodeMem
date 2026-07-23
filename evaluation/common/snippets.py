"""setup.sh script builders shared by CodeMem task generators.

Every generated script is a one-shot bash snippet: harbor runs a step's
workdir/setup.sh before the agent starts, and the script deletes itself
so retries/resumes do not re-apply it.
"""

from __future__ import annotations


def setup_script(*lines: str) -> str:
    """Assemble a one-shot setup.sh from body lines."""
    return "\n".join(
        [
            "#!/bin/bash",
            "set -euo pipefail",
            "cd /testbed",
            *lines,
            'rm -f -- "$0"',
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
    """Restore the working tree to a recorded `git write-tree` snapshot.

    read-tree does not move HEAD; the follow-up mixed reset turns the
    snapshot-vs-HEAD difference into ordinary unstaged modifications.
    """
    return [
        "git clean -fd",
        f'git read-tree --reset -u "$(cat {tree_file})"',
        "git reset --mixed HEAD",
    ]


# Restore the agent session stores checkpointed by the runtime evaluator.
# Only the branch matching the current run's agent exists on disk.
SESSION_RESTORE_LINES: list[str] = [
    "if test -d /tmp/codemem/session_checkpoint/codex; then",
    "    rm -rf /logs/agent/sessions",
    "    mkdir -p /logs/agent",
    "    cp -a /tmp/codemem/session_checkpoint/codex /logs/agent/sessions",
    "fi",
    "if test -d /tmp/codemem/session_checkpoint/claude-code; then",
    "    mkdir -p /logs/agent/sessions",
    "    rm -rf /logs/agent/sessions/projects",
    "    cp -a /tmp/codemem/session_checkpoint/claude-code /logs/agent/sessions/projects",
    "fi",
    "if test -d /tmp/codemem/session_checkpoint/kimi-cli; then",
    "    rm -rf /logs/agent/kimi/share",
    "    mkdir -p /logs/agent/kimi",
    "    cp -a /tmp/codemem/session_checkpoint/kimi-cli /logs/agent/kimi/share",
    "fi",
]
