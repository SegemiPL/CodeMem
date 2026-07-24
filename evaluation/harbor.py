from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


AGENT_TOOLCHAIN_TARGET = "/opt/codemem-agent"
_SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True)
class AgentToolchain:
    required_executables: tuple[str, ...]
    path_entries: tuple[str, ...]

    @property
    def path(self) -> str:
        return ":".join((*self.path_entries, _SYSTEM_PATH))


AGENT_TOOLCHAINS: dict[str, AgentToolchain] = {
    "codex": AgentToolchain(("codex", "node", "rg"), (f"{AGENT_TOOLCHAIN_TARGET}/bin", f"{AGENT_TOOLCHAIN_TARGET}/node/bin")),
    "claude-code": AgentToolchain(("claude", "node", "rg"), (f"{AGENT_TOOLCHAIN_TARGET}/bin", f"{AGENT_TOOLCHAIN_TARGET}/node/bin")),
    "kimi-cli": AgentToolchain(("kimi", "uv", "rg"), (f"{AGENT_TOOLCHAIN_TARGET}/bin",)),
}

# Harbor 0.20's built-in Kimi adapter always reinstalls uv and kimi-cli during
# setup. Select a compatibility subclass only when a prebuilt Kimi toolchain is
# mounted; jobs without a shared toolchain retain Harbor's normal behavior.
PREINSTALLED_AGENT_IMPORTS = {
    "kimi-cli": "evaluation.agents.preinstalled_kimi:PreinstalledKimiCli",
}

# Harbor's Codex adapter deletes its temporary CODEX_HOME after retaining only
# sessions. Always use a compatibility subclass that also retains memories/
# without copying auth.json or the rest of CODEX_HOME.
AGENT_IMPORTS = {
    "codex": "evaluation.agents.memory_codex:MemoryCodex",
}


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
    agent_toolchain: Path | None = None,
    agent_version: str | None = None,
    n_attempts: int = 1,
) -> Path:
    """Write the Harbor job wrapper shared by CodeMem task adapters."""
    if n_attempts < 1:
        raise ValueError("n_attempts must be at least 1")

    # Check for Local Agent Begin
    # Toolchain definitions are registered above for each supported agent.
    # These should be encapsulated  as a function, taking the args of (agent, agent_toolchain, agent_version)

    toolchain_definition = AGENT_TOOLCHAINS.get(agent)
    if agent_version is not None and toolchain_definition is None:
        raise ValueError(f"A pinned agent version requires a supported agent; supported: {sorted(AGENT_TOOLCHAINS)}")
    if agent_toolchain is not None:
        if toolchain_definition is None:
            raise ValueError(f"A shared agent toolchain requires a supported agent; supported: {sorted(AGENT_TOOLCHAINS)}")
        if environment != "docker":
            raise ValueError("A shared agent toolchain is supported only by local Docker")
        agent_toolchain = agent_toolchain.expanduser().resolve()
        if not agent_toolchain.is_dir():
            raise FileNotFoundError(
                f"Shared {agent} toolchain directory does not exist: {agent_toolchain}"
            )
        for executable in toolchain_definition.required_executables:
            candidate = agent_toolchain / "bin" / executable
            if not candidate.exists():
                raise FileNotFoundError(
                    f"Shared {agent} toolchain is missing {candidate}; "
                    "run scripts/prepare-agent-toolchain.sh first"
                )
    
    # Check For Local Agent End

    # whether to record trajectory
    # Always leave it as True
    exclude = ""
    if not record_trajectory:
        exclude = (
            '    exclude_logs: ["trajectory.json", "sessions/**", '
            '"kimi/share/sessions/**", "kimi-cli.txt"]\n'
        )
    
    # Mounts: Local Agent Toolchain mounted to docker container
    mounts = ""
    agent_env = ""
    agent_kwargs = ""
    configured_agent = AGENT_IMPORTS.get(agent, agent)

    # If use local agent toolchain
    if agent_toolchain is not None:
        # mounts the local agent toolchain
        mounts = f'''  mounts:
    - type: bind
      source: {json.dumps(str(agent_toolchain))}
      target: {json.dumps(AGENT_TOOLCHAIN_TARGET)}
      read_only: true
'''

        # PATH for agent in docker
        agent_env = f'''    env:
      PATH: {json.dumps(toolchain_definition.path)}
'''
        configured_agent = PREINSTALLED_AGENT_IMPORTS.get(agent, configured_agent)

    # Confirm the version
    if agent_version is not None:
        agent_kwargs = f'''    kwargs:
      version: {json.dumps(agent_version)}
'''

    # Main Config
    content = f'''jobs_dir: {jobs_dir}
n_attempts: {n_attempts}
n_concurrent_trials: {concurrency}
environment:
  type: {environment}
  delete: true
{mounts}agents:
  - name: {configured_agent}
    model_name: {model}
    resume_trajectory: true
{agent_env}{agent_kwargs}{exclude}datasets:
  - path: {tasks_path}
'''

    # write config yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path
