from __future__ import annotations

import argparse
from pathlib import Path

from evaluation.harbor import write_job_config

from .generator import FeatureTaskGenerator
from .models import CODE_FAMILY, PROCESS_FAMILY, discover_tasks, select_tasks

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = Path("/data/zhuyiqi/CodeMem/data")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Generate Harbor CodeMem code/process feature rollouts"
    )
    result.add_argument(
        "--code-root", type=Path, default=DEFAULT_DATA_ROOT / "code_feature"
    )
    result.add_argument(
        "--process-root", type=Path, default=DEFAULT_DATA_ROOT / "process_feature"
    )
    result.add_argument(
        "--output", type=Path, default=ROOT / "evaluation/generated/feature-tasks"
    )
    sub = result.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="Generate feature tasks in batch")
    generate.add_argument("--family", choices=["all", CODE_FAMILY, PROCESS_FAMILY], default="all")
    generate.add_argument("--task-id", action="append", dest="task_ids")
    generate.add_argument("--subtype", action="append", dest="subtypes")
    generate.add_argument("--overwrite", action="store_true")

    job = sub.add_parser("job-config", help="Write a Harbor job YAML")
    job.add_argument(
        "--path", type=Path, default=ROOT / "evaluation/generated/feature-job.yaml"
    )
    job.add_argument("--tasks", type=Path, default=None)
    job.add_argument("--agent", required=True)
    job.add_argument("--model", required=True)
    job.add_argument(
        "--environment", choices=["docker", "daytona", "modal"], default="docker"
    )
    job.add_argument("--concurrency", type=int, default=1)
    job.add_argument("--jobs-dir", type=Path, default=ROOT / "evaluation/results")
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "job-config":
        print(
            write_job_config(
                args.path,
                tasks_path=args.tasks or args.output,
                agent=args.agent,
                model=args.model,
                environment=args.environment,
                concurrency=args.concurrency,
                jobs_dir=args.jobs_dir,
                record_trajectory=True,
            )
        )
        return

    # Select Tasks
    tasks = []
    if args.family in ("all", CODE_FAMILY):
        tasks.extend(discover_tasks(args.code_root, CODE_FAMILY))
    if args.family in ("all", PROCESS_FAMILY):
        tasks.extend(discover_tasks(args.process_root, PROCESS_FAMILY))
    tasks = select_tasks(
        tasks,
        task_ids=set(args.task_ids or ()),
        subtypes=set(args.subtypes or ()),
    )

    # Core Generate Process
    paths = FeatureTaskGenerator(args.output).generate_many(
        tasks, overwrite=args.overwrite
    )

    print(f"Generated {len(paths)} tasks under {args.output}")


if __name__ == "__main__":
    main()
