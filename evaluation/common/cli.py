"""Shared CLI pieces for CodeMem task generators.

Every domain CLI exposes the same ``job-config`` subcommand; the
arguments and the write_job_config invocation live here so a new task
type gets the interface for free.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from evaluation.harbor import write_job_config


def add_job_config_subcommand(
    sub: argparse._SubParsersAction,
    *,
    default_path: Path,
    default_jobs_dir: Path,
) -> argparse.ArgumentParser:
    job = sub.add_parser("job-config", help="Write a Harbor job YAML")
    job.add_argument("--path", type=Path, default=default_path)
    job.add_argument(
        "--tasks",
        type=Path,
        default=None,
        help="Dataset directory for the job; defaults to --output",
    )
    job.add_argument("--agent", required=True)
    job.add_argument("--model", required=True)
    job.add_argument(
        "--environment", choices=["docker", "daytona", "modal"], default="docker"
    )
    job.add_argument("--n-attempts", type=int, default=1)
    job.add_argument("--concurrency", type=int, default=1)
    job.add_argument("--jobs-dir", type=Path, default=default_jobs_dir)
    job.add_argument(
        "--agent-toolchain",
        type=Path,
        help="Bind-mount a prepared shared agent toolchain into every local-Docker task",
    )
    job.add_argument("--agent-version", help="Require this agent CLI version")
    return job


def run_job_config(args: argparse.Namespace, *, default_tasks: Path) -> Path:
    return write_job_config(
        args.path,
        tasks_path=args.tasks or default_tasks,
        agent=args.agent,
        model=args.model,
        environment=args.environment,
        concurrency=args.concurrency,
        n_attempts=args.n_attempts,
        jobs_dir=args.jobs_dir,
        agent_toolchain=args.agent_toolchain,
        agent_version=args.agent_version,
        record_trajectory=True,
    )
