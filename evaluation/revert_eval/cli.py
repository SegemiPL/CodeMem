from __future__ import annotations

import argparse
from pathlib import Path

from evaluation.common.cli import add_job_config_subcommand, run_job_config
from evaluation.repo_images import DEFAULT_REPO_IMAGE_MAP, load_repo_image_map

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT.parent / "data" / "data"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Generate Harbor CodeMem revert evaluations")
    result.add_argument("--config", type=Path, default=Path(__file__).with_name("config.toml"))
    result.add_argument(
        "--ordered",
        type=Path,
        default=DATA_ROOT / "revert/ordered_revert_candidates.json",
    )
    result.add_argument(
        "--dataset",
        type=Path,
        default=DATA_ROOT / "swegym/raw/train-00000-of-00001.parquet",
        help="SWE-Gym parquet or legacy code_mem_dataset.json",
    )
    result.add_argument("--output", type=Path, default=ROOT / "evaluation/generated/revert-tasks")
    result.add_argument(
        "--repo-image-map",
        type=Path,
        default=DEFAULT_REPO_IMAGE_MAP,
        help="Canonical repo -> image mapping JSON; tasks of the same repo "
        "share one image and check out their own base commit",
    )
    sub = result.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="Generate one checkpointed Harbor task")
    generate.add_argument("--target", required=True)
    selection = generate.add_mutually_exclusive_group()
    selection.add_argument(
        "--middle",
        action="append",
        dest="middles",
        help="Explicit middle ID; repeat in ordered-chain order",
    )
    selection.add_argument(
        "--middle-count",
        type=int,
        help="Use the first N instances in the target's ordered chain",
    )
    generate.add_argument("--overwrite", action="store_true")
    generate.add_argument(
        "--manual-compact",
        action="store_true",
        help="Insert a manual compact step before the final checkpoint, "
        "overriding execution.manual_compact_before_final",
    )

    add_job_config_subcommand(
        sub,
        default_path=ROOT / "evaluation/generated/revert-job.yaml",
        default_jobs_dir=ROOT / "evaluation/results",
    )

    monitor = sub.add_parser(
        "monitor", help="Print one-line progress lines for a running Harbor job"
    )
    monitor.add_argument("job_dir", type=Path, help="Harbor jobs_dir to watch")
    monitor.add_argument("--interval", type=float, default=10.0)
    monitor.add_argument(
        "--once", action="store_true", help="Print a single snapshot and exit"
    )
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "monitor":
        from .monitor import run as monitor_run

        monitor_run(args.job_dir, interval=args.interval, once=args.once)
        return

    if args.command == "job-config":
        print(run_job_config(args, default_tasks=args.output))
        return

    # Imported lazily so the monitor subcommand also works on interpreters
    # without tomllib (Python < 3.11).
    from dataclasses import replace

    from .config import load_config
    from .generator import RevertTaskGenerator

    config = load_config(args.config)
    if args.manual_compact:
        config = replace(
            config,
            execution=replace(config.execution, manual_compact_before_final=True),
        )
    generator = RevertTaskGenerator(
        args.ordered,
        args.dataset,
        args.output,
        config,
        repo_image_map=load_repo_image_map(args.repo_image_map),
    )
    print(
        generator.generate(
            args.target,
            args.middles,
            args.middle_count,
            overwrite=args.overwrite,
        )
    )


if __name__ == "__main__":
    main()
