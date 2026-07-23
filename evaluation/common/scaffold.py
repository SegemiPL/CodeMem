"""Harbor task scaffolding shared by all CodeMem task generators.

The helpers here reproduce the exact on-disk layout every CodeMem task
uses: an environment/Dockerfile, per-step tests/test.sh wrappers, and a
task.toml describing the multi-step job.
"""

from __future__ import annotations

import json
from pathlib import Path


def write_dockerfile(task_dir: Path, image: str, state_dir: str) -> None:
    """Write environment/Dockerfile based on a runtime image.

    safe.directory is set system-wide because on some providers the exec
    user differs from the image's /testbed owner (e.g. Modal gVisor), and
    git refuses to operate without it.
    """
    (task_dir / "environment").mkdir(parents=True, exist_ok=True)
    (task_dir / "environment" / "Dockerfile").write_text(
        f"FROM {image}\nWORKDIR /testbed\n"
        "RUN git config --system --add safe.directory /testbed"
        f" && mkdir -p /logs {state_dir}\n"
    )


def write_step_dirs(task_dir: Path, step_names: list[str]) -> None:
    for name in step_names:
        (task_dir / "steps" / name / "tests").mkdir(parents=True, exist_ok=True)


def write_step_test_script(step_dir: Path, runtime_script: str, step_name: str) -> None:
    """Write the per-step tests/test.sh invoking the bundled runtime script."""
    script = step_dir / "tests" / "test.sh"
    script.write_text(
        "#!/bin/bash\nset -u\npython3 /tests/" + runtime_script + " " + step_name + "\n"
    )
    script.chmod(0o755)


def bundle_runtime_script(task_dir: Path, source: Path, name: str) -> None:
    """Copy the domain runtime script into tests/ so it ships with the task."""
    (task_dir / "tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "tests" / name).write_text(source.read_text())


def render_task_toml(
    *,
    task_name: str,
    description: str,
    metadata: dict,
    steps: list[tuple[str, int, int]],
    build_timeout_sec: int,
    cpus: int,
    memory_mb: int,
    storage_mb: int,
    workdir: str | None = "/testbed",
) -> str:
    """Render a multi-step task.toml.

    steps holds (name, agent_timeout_sec, verifier_timeout_sec) tuples;
    metadata is rendered in insertion order after the benchmark key.
    """
    step_blocks = "\n".join(
        f'''[[steps]]
name = {json.dumps(name)}
[steps.agent]
timeout_sec = {agent_timeout}
[steps.verifier]
timeout_sec = {verifier_timeout}
environment_mode = "shared"
'''
        for name, agent_timeout, verifier_timeout in steps
    )
    metadata_lines = "\n".join(
        f"{key} = {json.dumps(value)}" for key, value in metadata.items()
    )
    workdir_line = f"workdir = {json.dumps(workdir)}\n" if workdir else ""
    return f'''schema_version = "1.3"
multi_step_reward_strategy = "final"

[task]
name = {json.dumps("codemem/" + task_name)}
description = {json.dumps(description)}

[metadata]
{metadata_lines}

[environment]
build_timeout_sec = {build_timeout_sec}
cpus = {cpus}
memory_mb = {memory_mb}
storage_mb = {storage_mb}
{workdir_line}
{step_blocks}'''
