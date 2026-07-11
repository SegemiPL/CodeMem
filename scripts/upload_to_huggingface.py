"""Upload the JSON datasets declared in ``constants.addr`` to Hugging Face.

Example:
    cd scripts
    python upload_to_huggingface.py zhangpl24/CodeMem --private
"""

import argparse
import os
from pathlib import Path
from constants.config import init_env_and_logger

from constants.addr import (
    CODE_MEM_ADDR,
    FILE_DEPENDENCY_ADDR,
    INSTANCE_INFO_ADDR,
    INSTANCE_QA_ADDR,
    INSTANCE_QUERY_ADDR,
    REPO_DEPENDENCY_ADDR,
)

REPO_ID = "SegemiPL/CodeMem"

DATA_FILES = {
    "file_dependency.json": FILE_DEPENDENCY_ADDR,
    "repo_dependency.json": REPO_DEPENDENCY_ADDR,
    "instance_info.json": INSTANCE_INFO_ADDR,
    "instance_qa.json": INSTANCE_QA_ADDR,
    "octo_instance_query.json": INSTANCE_QUERY_ADDR,
    "code_mem_dataset.json": CODE_MEM_ADDR,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload the data directory to Hugging Face.")
    parser.add_argument(
        "-c",
        "--commit-message",
        default="Upload CodeMem dataset files",
        help="Commit message used for the Hugging Face upload.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_env_and_logger()
    try:
        from huggingface_hub import HfApi
    except ImportError as error:
        raise SystemExit("Missing dependency: install it with `pip install huggingface_hub`.") from error

    token = os.getenv("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set. Add it to scripts/.env or export it before running.")

    api = HfApi(token=token)
    api.create_repo(
        repo_id = REPO_ID,
        repo_type = "dataset",
        private = False,
        exist_ok = True
    )

    api.upload_folder(
        repo_id = REPO_ID,
        repo_type = "dataset",
        folder_path = "../data",
        path_in_repo = "data",
        ignore_patterns = [
            "**/octo_image_file/**",
            "**/.DS_Storea"
        ],
        commit_message = args.commit_message,
    )

    

if __name__ == "__main__":
    main()
