"""Harbor-native evaluation support for CodeMem revert tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import RevertEvalConfig, load_config
    from .generator import RevertTaskGenerator

__all__ = ["RevertEvalConfig", "RevertTaskGenerator", "load_config"]


def __getattr__(name: str) -> Any:
    # Lazy re-exports so that lightweight entry points (e.g. the monitor
    # subcommand) do not require tomllib/Python 3.11+ at package import time.
    if name in ("RevertEvalConfig", "load_config"):
        from . import config

        return getattr(config, name)
    if name == "RevertTaskGenerator":
        from .generator import RevertTaskGenerator

        return RevertTaskGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
