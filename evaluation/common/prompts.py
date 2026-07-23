"""Shared instruction templates for CodeMem task generators.

Revert prompts are user-configurable via revert_eval/config.toml; the
solve directive below is the same wording, kept here for task families
whose source data carries bare problem statements (e.g. code-feature).
"""

from __future__ import annotations

SOLVE_INSTRUCTION_TEMPLATE = """
Solve the following issue in the repository. Work carefully, run relevant tests, and leave the implementation in the working tree.

You must solve this issue by editing files inside the repository directly. Do not use external tools such as curl, wget, a browser, or any API to fetch patches, hints, or solutions.

Instance: {instance_id}

{instruction}
"""


def wrap_solve_instruction(instruction: str, instance_id: str) -> str:
    """Wrap a bare problem statement with the solve directive."""
    return (
        SOLVE_INSTRUCTION_TEMPLATE.format(
            instance_id=instance_id,
            instruction=instruction.strip(),
        ).strip()
        + "\n"
    )
