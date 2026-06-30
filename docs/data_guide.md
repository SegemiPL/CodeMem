# Dataset Construction Guide

This document focuses on concrete construction methods, required metadata, filtering constraints, and evaluation protocols for `dataset_build`.

## Scope

uses two source families:

- SWE-Gym-based code memory tasks.
- OctoBench-based user-instruction memory tasks.

The main simplification is file-scoped construction:

- The static dataset stores prompts, expected file scopes, repo metadata, and QA ground truth.
- Runtime evaluation records checkpoints around the agent's actual operations.
- Exact diff evaluation compares configured files against runtime checkpoints.
- `Middle` examples are filtered so they are file-disjoint from the files used by the final evaluation.

## Runtime Checkpoints

The dataset is static, but the agent's edits are dynamic. Do not require the static dataset to predict the exact agent diff. Instead, the evaluator should checkpoint repository states during the run.

Recommended checkpoint implementation in an isolated checkout:

```bash
git add -A
checkpoint_ref=$(git write-tree)
git reset --mixed -q
```

Later, compare the current working tree against that checkpoint:

```bash
git diff --exit-code "$checkpoint_ref" -- path/to/file_a path/to/file_b
```

This supports dynamic agent edits while keeping dataset metadata static. The static dataset only needs to know which files should be compared.

## Sequence Terms

- `Base`: repository state before the target prompt.
- `Target`: the prompt and operation that should later be remembered.
- `Middle`: unrelated intervening prompt and operation.
- `Delete`: an agent-facing prompt that asks the agent to delete selected code.
- `Final`: the final prompt used for `Revert`, `Restore`, or `QA`.

## Common Metadata Schema

Each item should be decomposable into the following components. Task views can select only the components they need.

```yaml
repo_metadata:
  instance_id:
  source:
  repo:
  base_commit:

target:
  prompt:
  files:
  source_diff: optional

middle:
  - prompt:
    files:
    source_diff: optional
    placement: before_delete | after_delete

delete:
  prompt: optional
  files: optional
  source_diff: optional
  delete_code_qa: optional
    question:
    groundtruth:

final:
  prompt:
  eval_type:
  eval_files:
  side_effect_files: optional
```

Field usage:

- `prompt` fields are agent-facing.
- `files` fields are static evaluation scopes and filtering constraints.
- `source_diff` is optional construction/debug metadata. It is not required for V1 exact evaluation.
- `delete_code_qa` is only needed for QA tasks about deleted content.

## SWE-Gym Construction

Use SWE-Gym instances to derive `Target` and `Middle` prompts and file scopes. The source patch can be used to identify files and to debug construction, but V1 final scoring should rely on runtime checkpoints plus file scopes.

### Target Construction

For each candidate target instance:

1. Record `repo`, `base_commit`, and source instance ID.
2. Derive `target.prompt` from the SWE-Gym task description.
3. Derive `target.files` from the files touched by the source patch.
4. Optionally store `target.source_diff` for debugging and later analysis.

Target filtering constraints:

- `target.files` should be small enough for exact file-scoped evaluation.
- Avoid generated files, lockfiles, vendored files, and broad formatting-only patches unless the task explicitly targets them.
- The target prompt should not reveal the final revert answer directly.
- In a runtime benchmark, the target stage should produce a non-empty diff in `target.files`; otherwise the later revert task is invalid or trivial.

### Middle Construction

For each target, select one or more unrelated middle instances.

Middle selection method:

1. Select a candidate from the same repository, or create a simple self-contained middle prompt.
2. Derive `middle.files` from the source patch or intended file scope.
3. Require file disjointness from the final evaluation scope.
4. During runtime, validate that the agent's middle operation did not modify protected files.

Code Revert V1 constraints:

```text
target.files ∩ middle.files = empty
```

Delete-and-Restore V1 constraints:

```text
delete.files ∩ middle_after_delete.files = empty
```

Runtime validation constraints:

- After each middle prompt, changed files should not intersect `target.files` for `Code Revert`.
- After post-delete middle prompts, changed files should not intersect `delete.files` for `Delete-and-Restore`.
- If this validation fails, mark the run as a setup failure rather than a final-task failure.

`middle.source_diff` is not needed for final scoring, but it is useful for reproducing construction and checking why a candidate was accepted.

## Code Revert Task

### Runtime Sequence

1. Check out `repo_metadata.base_commit`.
2. Checkpoint `C0` before `target.prompt`.
3. Ask the agent to complete `target.prompt`.
4. Validate that `target.files` changed relative to `C0`.
5. Ask the agent to complete each `middle.prompt`.
6. Validate that middle edits did not modify `target.files`.
7. Checkpoint `C_prefinal` before `final.prompt`.
8. Ask the agent to complete `final.prompt`, which asks it to revert the target operation.
9. Evaluate the final workspace.

### Final Evaluation

Main score:

```bash
git diff --exit-code "$C0" -- ${target_files[@]}
```

The command should return empty diff. This checks whether the agent restored `target.files` to their pre-target state.

Side-effect score:

```bash
git diff --exit-code "$C_prefinal" -- ${middle_files[@]}
```

This checks whether the final revert damaged unrelated middle work. It can be reported separately from the main score.

### Static Fields Required

```yaml
repo_metadata:
target.prompt:
target.files:
middle[].prompt:
middle[].files:
final.prompt:
final.eval_type: file_diff_to_checkpoint
final.eval_files: target.files
final.side_effect_files: middle.files
```

`target.source_diff` and `middle.source_diff` are optional.

## Delete-and-Restore Task

Treat `Delete` as a special middle operation. It is a normal agent-facing prompt, not a hidden dataset operation.

### Runtime Sequence

1. Check out `repo_metadata.base_commit`.
2. Ask the agent to complete `target.prompt`.
3. Ask the agent to complete optional `middle_before_delete` prompts.
4. Checkpoint `C_predelete`.
5. Ask the agent to complete `delete.prompt`.
6. Validate that `delete.files` changed relative to `C_predelete`.
7. Ask the agent to complete optional `middle_after_delete` prompts.
8. Validate that post-delete middle edits did not modify `delete.files`.
9. Checkpoint `C_prefinal`.
10. Ask the agent to complete `final.prompt`, which asks it to restore the deleted code.
11. Evaluate the final workspace.

### Final Evaluation

Main score:

```bash
git diff --exit-code "$C_predelete" -- ${delete_files[@]}
```

The command should return empty diff. This checks whether the agent restored `delete.files` to their state immediately before deletion.

Side-effect score:

```bash
git diff --exit-code "$C_prefinal" -- ${middle_after_delete_files[@]}
```

This checks whether the restore operation damaged unrelated post-delete middle work.

### Static Fields Required

```yaml
repo_metadata:
target.prompt:
target.files:
middle[].prompt:
middle[].files:
delete.prompt:
delete.files:
final.prompt:
final.eval_type: file_diff_to_checkpoint
final.eval_files: delete.files
final.side_effect_files: middle_after_delete.files
```

`delete_code_qa` is not required for code restoration evaluation. It is only needed if the task also includes a QA question about deleted content.

## Deleted-Code QA Task

Deleted-code QA should be treated separately from code restoration.

Static fields required:

```yaml
delete:
  prompt:
  files:
  delete_code_qa:
    question:
    groundtruth:
final:
  prompt:
  eval_type: qa
```

Constraints:

- The QA answer must not be recoverable from the current repository after deletion.
- If deletion is performed dynamically by the agent, the evaluator must verify that the intended code was actually deleted. Otherwise the QA item should be marked invalid for that run.
- If exact QA ground truth is required, prefer deleting a predefined region or using a deterministic deletion script during construction.

## Feature Revert Task

Feature Revert is a harder variant of Code Revert. It should not be mixed into V1 exact file-scoped revert unless the feature's files are cleanly isolated.

Construction constraints:

- The target change should represent one coherent feature.
- `target.files` should cover the feature's implementation surface.
- Middle edits should be file-disjoint from `target.files` for the first version.
- If the feature touches shared files or later middle edits touch the same files, do not use simple file-scoped diff evaluation.

Evaluation options:

- File-scoped diff to the pre-target checkpoint when the feature is file-isolated.
- Unit tests or behavior checks when exact file restore is too strict or same-file edits are allowed.

## OctoBench Instruction Construction

Use OctoBench-style conversations for user-instruction memory tasks.

### User Instruction Task

Construction sequence:

1. Insert or select `target_instruction`.
2. Add unrelated middle turns.
3. Add a final QA or constrained coding prompt.
4. Store the expected active instruction and output constraints.

Required metadata:

```yaml
repo_metadata:
target_instruction:
middle_turns:
final.prompt:
final.eval_type: qa | constrained_output
groundtruth:
output_constraints:
```

Constraints:

- The final prompt must not restate the target instruction.
- The instruction must be specific enough to grade.
- Coding tasks should be simple; the difficulty should be instruction recall, not solving a hard programming problem.

### Stale Instruction Task

Construction sequence:

1. Insert `instruction_a`.
2. Add middle turns.
3. Insert conflicting `instruction_b`.
4. Add the final QA or constrained coding prompt.
5. Store which instruction is active at final time.

Required metadata:

```yaml
instruction_a:
instruction_b:
active_instruction:
conflict_resolution_rule:
final.prompt:
groundtruth:
```

Constraints:

- The active instruction must be unambiguous.
- The final prompt should reveal whether the model follows the active instruction rather than stale memory.

## Filtering Summary

V1 should reject or mark invalid any item or run where:

- The target stage does not change `target.files`.
- A middle stage modifies files protected by the final evaluation.
- The delete stage does not change `delete.files`.
- Post-delete middle modifies `delete.files`.
- The final answer can be recovered from the current repository or final prompt alone.
- The file scope is too broad for reliable exact diff evaluation.
- QA ground truth depends on a dynamic agent deletion that was not verified.
