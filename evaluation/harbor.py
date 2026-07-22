from __future__ import annotations

import json
from pathlib import Path


CODEX_TOOLCHAIN_TARGET = "/opt/codemem-agent"
CODEX_TOOLCHAIN_PATH = (
    f"{CODEX_TOOLCHAIN_TARGET}/bin:"
    f"{CODEX_TOOLCHAIN_TARGET}/node/bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)


def write_job_config(
    path: Path,
    *,
    tasks_path: Path,
    agent: str,
    model: str,
    environment: str,
    concurrency: int,
    jobs_dir: Path,
    record_trajectory: bool = True,
    codex_toolchain: Path | None = None,
    codex_version: str | None = None,
) -> Path:
    """Write the Harbor job wrapper shared by CodeMem task adapters."""
    if codex_version is not None and agent != "codex":
        raise ValueError("A pinned Codex version requires agent='codex'")
    if codex_toolchain is not None:
        if agent != "codex":
            raise ValueError("A shared Codex toolchain requires agent='codex'")
        if environment != "docker":
            raise ValueError("A shared Codex toolchain is supported only by local Docker")
        codex_toolchain = codex_toolchain.expanduser().resolve()
        if not codex_toolchain.is_dir():
            raise FileNotFoundError(
                f"Shared Codex toolchain directory does not exist: {codex_toolchain}"
            )
        for executable in ("codex", "node", "rg"):
            candidate = codex_toolchain / "bin" / executable
            if not candidate.exists():
                raise FileNotFoundError(
                    f"Shared Codex toolchain is missing {candidate}; "
                    "run scripts/prepare-codex-toolchain.sh first"
                )

    exclude = ""
    if not record_trajectory:
        exclude = (
            '    exclude_logs: ["trajectory.json", "sessions/**", '
            '"kimi/share/sessions/**", "kimi-cli.txt"]\n'
        )
    mounts = ""
    agent_env = ""
    agent_kwargs = ""
    if codex_toolchain is not None:
        mounts = f'''  mounts:
    - type: bind
      source: {json.dumps(str(codex_toolchain))}
      target: {json.dumps(CODEX_TOOLCHAIN_TARGET)}
      read_only: true
'''
        agent_env = f'''    env:
      PATH: {json.dumps(CODEX_TOOLCHAIN_PATH)}
'''
    if codex_version is not None:
        agent_kwargs = f'''    kwargs:
      version: {json.dumps(codex_version)}
'''
    content = f'''jobs_dir: {jobs_dir}
n_attempts: 1
n_concurrent_trials: {concurrency}
environment:
  type: {environment}
  delete: true
{mounts}agents:
  - name: {agent}
    model_name: {model}
    resume_trajectory: true
{agent_env}{agent_kwargs}{exclude}datasets:
  - path: {tasks_path}
'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path
