from __future__ import annotations

import argparse
from pathlib import Path

from evaluation.common.cli import add_job_config_subcommand, run_job_config
from evaluation.repo_images import DEFAULT_REPO_IMAGE_MAP, load_repo_image_map

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
    result.add_argument(
        "--repo-image-map",
        type=Path,
        default=DEFAULT_REPO_IMAGE_MAP,
        help="Canonical repo -> image mapping JSON; tasks of the same repo "
        "share one image and check out their own base commit",
    )
    sub = result.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="Generate feature tasks in batch")
    generate.add_argument("--family", choices=["all", CODE_FAMILY, PROCESS_FAMILY], default="all")
    generate.add_argument("--task-id", action="append", dest="task_ids")
    generate.add_argument("--subtype", action="append", dest="subtypes")
    generate.add_argument("--overwrite", action="store_true")

    add_job_config_subcommand(
        sub,
        default_path=ROOT / "evaluation/generated/feature-job.yaml",
        default_jobs_dir=ROOT / "evaluation/results",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "job-config":
        print(run_job_config(args, default_tasks=args.output))
        return

    # Select Tasks
    repo_image_map = load_repo_image_map(args.repo_image_map)
    tasks = []
    if args.family in ("all", CODE_FAMILY):
        tasks.extend(discover_tasks(args.code_root, CODE_FAMILY, repo_image_map))
    if args.family in ("all", PROCESS_FAMILY):
        tasks.extend(discover_tasks(args.process_root, PROCESS_FAMILY, repo_image_map))
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
