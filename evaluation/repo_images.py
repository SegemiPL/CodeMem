from __future__ import annotations

import json
from pathlib import Path

# Canonical per-repo runtime images. All tasks of a repo share one image;
# each task checks out its own base commit before the first step.
DEFAULT_REPO_IMAGE_MAP = Path("/data/zhangpeilin/data/data/repo_image_map.json")


def load_repo_image_map(path: Path | None) -> dict[str, str]:
    """Load the canonical repo -> image mapping. Empty when no path is given."""
    if path is None:
        return {}
    if not path.is_file():
        raise FileNotFoundError(
            f"Repo image map not found: {path}; "
            "build it from the dataset or pass --repo-image-map"
        )
    return json.loads(path.read_text())


def resolve_image(repo_image_map: dict[str, str], repo: str, fallback: str) -> str:
    """Return the canonical image for repo, falling back to the per-instance image."""
    return repo_image_map.get(repo, fallback)
