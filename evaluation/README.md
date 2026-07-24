# Harbor revert evaluation

This directory turns chains from
`../data/data/revert/ordered_revert_candidates.json` and the canonical
SWE-Gym parquet into
Harbor multi-step tasks. It reuses Harbor for agent installation, native session
resume, Docker/Daytona/Modal environments, concurrency, log collection, and
per-step verification. The custom code is limited to dataset conversion,
checkpoint management, and revert-specific metrics.

## Evaluation shape

Each generated task runs in one persistent Harbor environment:

1. Reset the SWE-Gym image to the target base commit and solve the target.
2. Solve one selected middle instance.
3. Save a checkpoint of both the Git tree and the agent's native session.
4. Optionally ask the CLI to compact, then replace the checkpoint with the
   post-compaction session.
5. Revert the target and evaluate it.
6. Restore the Git and session checkpoint, delete the target-touched files,
   then ask the checkpointed session to restore them.

Restoring the session checkpoint before step 6 is important: the restore branch
does not see the revert-branch conversation. Claude Code and Codex CLI are
supported through Harbor's built-in `claude-code` and `codex` agents — both
store resumable sessions under `/logs/agent/sessions` (the `codex` label in the
checkpoint covers both). Kimi CLI stores its share dir at
`/logs/agent/kimi/share`. Another
agent can be selected without changing this adapter if its Harbor implementation
sets `SUPPORTS_RESUME` and writes its session store to one of these locations;
otherwise the checkpoint step records a `session_checkpoint_error` warning in
`metrics.json` and the restore branch fails its setup, while solve/revert
metrics are still produced.

## Generate and run

From the CodeMem repository (using the workspace virtual environment):

```bash
/data/zhuyiqi/CodeMem/.venv/bin/python -m evaluation.revert_eval.cli generate \
  --target Project-MONAI__MONAI-1010

/data/zhuyiqi/CodeMem/.venv/bin/python -m evaluation.revert_eval.cli job-config \
  --agent codex \
  --model openai/gpt-5.3-codex \
  --environment docker \
  --concurrency 1
```

### Shared agent toolchains for local Docker

Harbor normally installs Codex, Claude Code, or Kimi CLI inside every fresh
task container. For large local-Docker jobs, prepare one versioned host
toolchain and bind-mount it read-only into every task instead. The shared
toolchain flow supports `codex`, `claude-code`, and `kimi-cli`:

```bash
scripts/prepare-agent-toolchain.sh \
  --agent codex \
  --agent-version 0.144.6

scripts/prepare-agent-toolchain.sh \
  --agent claude-code \
  --agent-version 2.1.218

scripts/prepare-agent-toolchain.sh \
  --agent kimi-cli \
  --agent-version 1.49.0

/data/zhuyiqi/CodeMem/.venv/bin/python -m evaluation.revert_eval.cli job-config \
  --agent codex \
  --model openai/gpt-5.3-codex \
  --environment docker \
  --agent-toolchain /data/zhuyiqi/CodeMem/.cache/codex-toolchain \
  --agent-version 0.144.6 \
  --concurrency 1
```

The default outputs are `.cache/<agent>-toolchain`. The preparation script
downloads each toolchain once and stages updates before replacing an existing
toolchain. The generated Harbor job mounts it at `/opt/codemem-agent` and adds
its `bin` directories to the agent `PATH`. The agent adapter then detects the
existing executable and skips per-container installation. Use an explicit
agent version for reproducible benchmark runs; `latest` is convenient only for
initial smoke testing. The mount is local-Docker specific and is rejected for
Daytona or Modal jobs.

Then run the generated configuration with the Harbor checkout requested for the
experiment (the exact launcher depends on how Harbor is installed):

```bash
scripts/harbor.sh run \
  -c evaluation/generated/revert-job.yaml
```

Use the repository launcher for shared Kimi jobs: it adds the CodeMem package
to Harbor's host-side import path so Harbor can load the compatibility adapter
that skips its otherwise-unconditional Kimi installation. Set `HARBOR_BIN` if
Harbor is installed somewhere other than the server default.

The same launcher loads CodeMem's Codex memory adapter for every Codex job.
Harbor's native Codex adapter retains resumable sessions but deletes its
temporary `CODEX_HOME`; the adapter additionally enables Codex Memories and
retains only `$CODEX_HOME/memories/` under `/logs/agent/memories/`. It does not
copy `auth.json`, configuration, caches, or the rest of `CODEX_HOME`. Revert
tasks include this learned-memory store in the post-middle checkpoint and
restore it before the restore branch, preventing memory learned while solving
the revert branch from leaking into the alternate trajectory.

Use `--environment daytona` or `--environment modal` and raise `--concurrency`
for a parallel provider. Provider credentials and agent credentials are handled
by Harbor in the usual way.

Select a non-default member of the target's longest ordered chain with
`--middle INSTANCE_ID`. Use `--overwrite` to regenerate a task.

## Configuration

Edit `evaluation/revert_eval/config.toml` to change prompts, resource limits,
timeouts, trajectory retention, or manual compaction. Manual compaction is off by
default. When enabled, the configured `manual_compact` prompt is an extra resumed
step before the final checkpoint. The default prompt is `/compact`, which only
works with agents that interpret slash commands in headless mode (Kimi CLI,
Claude Code); Codex treats it as plain prompt text.

Each pytest invocation is capped by `CODEMEM_TEST_TIMEOUT_SEC` (default 600s);
a timed-out test is recorded as `error` instead of hanging the whole verifier.

`record_trajectory = false` writes Harbor agent log exclusions for ATIF,
raw-session, and CLI wire-log files. Session files still exist inside the live
environment long enough to implement checkpoint/resume; they are excluded from
the retained job output.

## Progress monitoring

While a job runs, print one-line progress updates (step starts, per-step
verifier metrics, trial completion) in a terminal:

```bash
python3 -m evaluation.revert_eval.cli monitor evaluation/results/<job-name>
```

Use `--once` for a single snapshot and `--interval N` to change the poll
period. The runtime evaluator also prints a `[codemem] phase=... ` summary
line at the end of every step, which lands in each step's
`verifier/test-stdout.txt` and the trial log.

## Metrics and artifacts

Every step stores `metrics.json` with per-test status (`pass`, `fail`, or
`error`), return code, and bounded output. For Harbor compatibility the verifier
still writes `reward.json`, but it no longer contains an RL-style `reward`
field; instead it holds readable per-step metrics.

Every step also captures the complete workspace change as a binary Git patch.
It exists only in the step-scoped verifier area and Harbor archives it as that
step's `verifier/workspace.patch`; the next root setup removes the in-container
copy before another agent turn starts. The corresponding
`metrics.json` entry records the semantic base revision, workspace tree hash,
patch size, SHA-256 digest, and artifact path. Solve steps use their own
instance base commit, while revert and restore use the target base commit.

## In-container isolation

Generated environments create a `codemem-agent` account. Codex, Claude Code,
and Kimi CLI commands run as that unprivileged user, while Harbor workdir setup
and verifier commands remain root. Evaluator-only Git history, workspace trees,
and branch checkpoints live under root-only `/var/lib/codemem-private`.

Before every agent turn, root setup removes the previous `/tests` and
`/logs/verifier` contents. Revert phases are materialized from the private
repository and exposed through a new one-commit `/testbed/.git`, so original
history and verifier-created dangling objects are not readable by the agent.
The active `/logs/agent` session and memory stores remain agent-readable because
they are the state being evaluated.

- `file_revert_match` / `file_restore_match`: booleans indicating whether the
  target-touched files match the target base tree or saved post-target tree.
- For each target or middle test group, metrics expose three fields:
  `{prefix}_fail_to_pass_total`, `{prefix}_fail_to_pass_passed`, and
  `{prefix}_fail_to_pass_ratio`, plus the corresponding `pass_to_pass` fields.
  After solve/restore, `passed` counts tests that passed; after revert,
  Fail-to-Pass metrics use `{prefix}_fail_to_pass_reverted` to count tests that
  returned to fail.
- After solve/restore: target Fail-to-Pass and Pass-to-Pass tests must pass.
- After revert: target Fail-to-Pass tests must fail (an error is not accepted),
  while target Pass-to-Pass and all middle tests must pass.
- `session_compacted_before_final`, `compaction_count` (in detailed metrics), and
  `manual_compaction_requested` distinguish automatic from requested compaction.
  The revert step additionally records `session_at_revert` in `metrics.json`, a
  live rescan that also catches compaction happening during the revert step
  itself.

The step-level Harbor archives preserve the full test-state transition rather
than collapsing an expected post-revert failure into an ordinary failed score.

## Dynamic evaluation advice

For trajectory-derived QA in the same conversation, model it as more Harbor
steps with `resume_trajectory: true`: record the pre-QA trajectory, generate QA
outside the agent, upload it in the next step's `workdir/setup.sh`, and resume the
native session for answers.

For independent branches, checkpoint both workspace and session as this adapter
does. Copying only the Git tree is insufficient because the second branch would
inherit the first branch's dialogue. Harbor's `agent.load_trajectory` is currently
a reserved, unimplemented interface, so portable cross-job resume is not yet
available. Keep branch execution within one multi-step trial for now. A useful
upstream Harbor addition would be a provider-neutral checkpoint hook plus an
agent `fork_session(checkpoint)` API; that would make the same design work across
jobs and for providers with native environment snapshots.

## Code/process feature rollouts

`evaluation.feature_eval` converts the private code-feature and process-feature
construction records into Harbor 1.3 multi-step tasks. Each task runs its 20
development turns while resuming the same native agent session. Code-feature
keeps one shared working tree. Process-feature resets and cleans the repository
to each turn's own `base_commit`, so code edits do not carry between turns while
the conversation does.

Harbor 0.20 cannot switch the main environment image between steps. Process
tasks therefore run all turns in the turn-1 SWE-efficiency image and record each
turn's expected `image_name` as provenance in `tests/config.json` and verifier
metrics. This is an intentional approximation of the source collection's
fresh-image policy; checkout, rebuild, or workload failures caused by dependency
drift should be treated as environment ineligibility rather than agent failure.

Generate all 160 tasks on THUMT:

```bash
python3 -m evaluation.feature_eval.cli generate
```

The server defaults are:

- code data: `/data/zhuyiqi/CodeMem/data/code_feature`
- process data: `/data/zhuyiqi/CodeMem/data/process_feature`
- output: `evaluation/generated/feature-tasks`

Use `--family code` or `--family process`, repeat `--task-id`/`--subtype` to
filter, and pass `--overwrite` to regenerate existing output. Global path
options must appear before the `generate` subcommand.

Create the Harbor job wrapper after generation:

```bash
python3 -m evaluation.feature_eval.cli job-config \
  --agent codex \
  --model openai/gpt-5.3-codex \
  --environment docker \
  --concurrency 1
```

The per-turn verifier currently does not score memory quality. It records a
cumulative binary workspace patch, Git status, and completion metadata. Harbor
trajectory retention remains enabled because process-feature evaluation will
later derive its oracle from the evaluated agent's own tool trace. Full source
task JSON, memory questions, response grading, and private oracle fields are
not copied into the agent container.
