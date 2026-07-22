# CodeMem Feature Evaluation CLI

`evaluation.feature_eval.cli` converts CodeMem code-feature and process-feature source records into Harbor task directories. It also writes an optional Harbor job configuration for running those tasks in batch.

The CLI has two subcommands:

- `generate`: validate source records and generate Harbor-compatible task directories.
- `job-config`: write a Harbor job YAML that points at a generated task directory.

The CLI does not execute Harbor jobs and does not implement the final feature-evaluation scorer.

## Global options

These options appear before the subcommand.

| Option | Description |
| --- | --- |
| `--code-root PATH` | Root directory containing code-feature category directories and task JSON files. |
| `--process-root PATH` | Root directory containing process-feature category directories and task JSON files. |
| `--output PATH` | Directory where generated Harbor task directories are written. |

The source roots are expected to contain files matching:

```text
<family-category>/tasks/<task-directory>/task.json
```

## `generate`

Generate one or more Harbor task directories:

```bash
python -m evaluation.feature_eval.cli \
  --output evaluation/generated/feature-tasks \
  generate [OPTIONS]
```

### `generate` options

| Option | Default | Description |
| --- | --- | --- |
| `--family {all,code,process}` | `all` | Select code-feature tasks, process-feature tasks, or both. |
| `--task-id TASK_ID` | none | Restrict generation to a task ID. Repeat the option to select multiple IDs. |
| `--subtype SUBTYPE` | none | Restrict generation to a subtype. Repeat the option to select multiple subtypes. |
| `--overwrite` | disabled | Replace an existing generated directory with the same safe task name. Without this flag, an existing task is an error. |

Examples:

Generate all code-feature tasks:

```bash
python -m evaluation.feature_eval.cli \
  --output evaluation/generated/feature-tasks \
  generate \
  --family code \
  --overwrite
```

Generate all feature families:

```bash
python -m evaluation.feature_eval.cli \
  --output evaluation/generated/feature-tasks \
  generate \
  --family all \
  --overwrite
```

Generate selected tasks:

```bash
python -m evaluation.feature_eval.cli \
  --code-root /data/zhuyiqi/CodeMem/data/code_feature \
  --output evaluation/generated/feature-tasks \
  generate \
  --family code \
  --task-id cb1-strict20-getmoto-moto-5876 \
  --task-id cb3-strict20-getmoto-moto-5701 \
  --overwrite
```

### Generated task layout

For a source task named `cb1-strict20-getmoto-moto-5876`, generation creates:

```text
evaluation/generated/feature-tasks/
└── code--cb1-strict20-getmoto-moto-5876/
    ├── task.toml
    ├── environment/Dockerfile
    ├── tests/config.json
    ├── tests/record.py
    └── steps/
        ├── turn_01/instruction.md
        ├── turn_01/workdir/setup.sh
        ├── turn_01/tests/test.sh
        └── ...
```

Every generated task has 20 steps. The generated `task.toml` controls Harbor timeouts and shared-environment behavior. The Dockerfile selects the runtime image. The turn instruction and setup files are generated from the source turn records.

Code-feature tasks use the SWE-Gym image derived from the first turn's `source_instance_id`. They create setup only for turn 1 so the workspace can carry changes across turns.

Process-feature tasks use the image specified by the source record. They create setup for every turn; each setup resets the shared workspace to that turn's `base_commit` and removes untracked files.

Private construction and evaluation fields are not copied into the agent-facing instructions or `tests/config.json`.

## `job-config`

Write a Harbor job YAML for generated tasks:

```bash
python -m evaluation.feature_eval.cli \
  job-config [OPTIONS]
```

### `job-config` options

| Option | Default | Description |
| --- | --- | --- |
| `--path PATH` | `evaluation/generated/feature-job.yaml` | Output path for the generated Harbor YAML. |
| `--tasks PATH` | value of global `--output` | Task directory or task dataset path passed to Harbor. |
| `--agent NAME` | required | Harbor agent name, such as `codex` or `kimi-cli`. |
| `--model NAME` | required | Model identifier passed to the agent. |
| `--environment {docker,daytona,modal}` | `docker` | Harbor environment backend. |
| `--concurrency N` | `1` | Number of concurrent Harbor trials. |
| `--jobs-dir PATH` | `evaluation/results` | Directory where Harbor job results are stored. |

Example:

```bash
python -m evaluation.feature_eval.cli \
  --output evaluation/generated/feature-tasks \
  job-config \
  --path evaluation/generated/feature-job.yaml \
  --agent kimi-cli \
  --model openai/kimi-k2.5 \
  --environment docker \
  --concurrency 1 \
  --jobs-dir evaluation/results
```

The generated YAML configures the batch-level settings:

```yaml
jobs_dir: evaluation/results
n_attempts: 1
n_concurrent_trials: 1
environment:
  type: docker
  delete: true
agents:
  - name: kimi-cli
    model_name: openai/kimi-k2.5
    resume_trajectory: true
datasets:
  - path: evaluation/generated/feature-tasks
```

`job-config` does not add API keys or provider URLs. Supply those through Harbor's agent environment options or the relevant Harbor configuration. It also does not change per-turn instructions, commits, Dockerfiles, setup scripts, or verifier logic; those are defined by the generated task directory.

## Typical workflow

1. Generate the task directories:

   ```bash
   python -m evaluation.feature_eval.cli \
     --output evaluation/generated/feature-tasks \
     generate --family code --overwrite
   ```

2. Optionally generate a reusable Harbor job YAML:

   ```bash
   python -m evaluation.feature_eval.cli \
     --output evaluation/generated/feature-tasks \
     job-config \
     --agent kimi-cli \
     --model openai/kimi-k2.5 \
     --environment docker
   ```

3. Run Harbor with the generated task path or job configuration, using the credentials and provider settings appropriate for the selected agent.

4. Inspect `evaluation/results/<job-name>/result.json` and the per-trial `steps/turn_XX` artifacts.

## Validation scope

Generation validates the source schema, turn numbering, required commits, task IDs, and process workspace/image policies. It records per-turn workspace patches through `tests/record.py`.

At present, generation and recording do not perform final semantic grading from `evaluation_grounding` or `response_grading`. A successful artifact reward means that a turn was recorded, not that the agent's code or answer is correct.
