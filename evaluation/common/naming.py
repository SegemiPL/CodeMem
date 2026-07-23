from __future__ import annotations

import re


def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-")


def swegym_image(instance_id: str) -> str:
    return "xingyaoww/sweb.eval.x86_64." + instance_id.replace("__", "_s_").lower()


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
