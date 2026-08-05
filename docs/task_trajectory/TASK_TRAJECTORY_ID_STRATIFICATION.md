# TASK_TRAJECTORY_ID_STRATIFICATION.md

## Purpose

This document defines the **ID stratification model** for OpenPilot's task
trajectory evidence layer.

Its purpose is to stop task identity drift across:

- one real task run;
- internal subtasks;
- tool calls;
- LLM calls;
- persisted trajectory events.

The key principle is:

> one real user task should keep one stable top-level task identity, while
> internal execution units should be represented as subordinate IDs rather than
> overwriting the root task identity.

---

## Core problem

Without explicit ID stratification, one trajectory may mix:

- a top-level task ID;
- one or more internal subtask IDs;
- step IDs;
- tool call IDs;
- LLM call IDs.

This makes it harder to:

- review one run coherently;
- compare runs across failures;
- cluster repeated failure modes;
- separate root-task patterns from subtask-local mistakes.

---

## The five-layer ID model

## 1. `run_id`

Meaning:

- one full real execution attempt;
- one persisted trajectory container.

Properties:

- unique per run;
- stable for the lifetime of that run;
- maps to one trajectory directory.

Examples:

- `5539f44c73914d56b66e2588f92c982c`

## 2. `root_task_id`

Meaning:

- the top-level user task;
- the task that the user actually asked OpenPilot to perform.

Properties:

- one per run in the current architecture;
- should remain stable across the full run;
- should be the main task identity used by trajectory records.

Examples:

- `cli_27a9336616374b3ba147186b397b8d8a`

## 3. `subtask_id`

Meaning:

- an internal task created during decomposition or execution planning.

Properties:

- there may be many per run;
- they may fail, be retried, or be replaced;
- they must not replace the root task identity in trajectory shells.

Examples:

- `05bd9d97-2ef0-455f-8a96-500ecd18ce92`
- `b15c9855-f25a-4a94-a803-2fe593b14b25`

## 4. `step_id`

Meaning:

- one step inside a subtask.

Properties:

- belongs under a subtask;
- should connect tool calls and LLM calls to the local execution point.

Examples:

- `step_1_1`
- `step_2_1`

## 5. `execution_id` / `call_id`

Meaning:

- one concrete tool call or one concrete LLM call.

Properties:

- finest-grained ID in the trajectory;
- used for exact event/artifact correlation;
- must not be treated as task identity.

## 6. `resume_attempt_id`

Meaning:

- one explicit attempt to continue a durable checkpoint after a process/session boundary.

Properties:

- `run_id`, root task ID, and session ID remain unchanged;
- each resume request receives a new value;
- it must not replace tool/LLM execution IDs;
- checkpoints and resume preflight evidence use it to distinguish repeated recovery attempts.

Examples:

- `05bd9d97-...:r2:c1`
- `llm_52bf0e4b40e146d5a463f3b5df472e3f`

---

## Recommended role of each layer

```text
run_id
└── root_task_id
    ├── subtask_id A
    │   ├── step_id
    │   │   ├── llm execution_id
    │   │   └── tool execution_id
    │   └── step_id
    └── subtask_id B
        ├── step_id
        └── step_id
```

---

## Required semantics in persisted trajectories

## 1. `RunRecord`

`RunRecord.task_id` should mean:

- the **root task ID**

It should not drift to a subtask ID later in the same run.

## 2. `EventRecord`

`EventRecord.task_id` should mean:

- the **root task ID**

The event shell answers:

> which top-level real task does this event belong to?

It should not answer:

> which internal subtask emitted this event?

That second question belongs in payload-level subordinate identity fields.

## 3. `payload.correlation.task_id`

Default meaning:

- the **root task ID**

This keeps correlation consistent across:

- route events;
- runtime state events;
- tool events;
- LLM events;
- finish events.

## 4. `subtask_id`

Recommended storage location:

- `payload.annotations.subtask_id`

Optional companion fields:

- `payload.annotations.parent_task_id`
- `payload.annotations.root_task_id`

This preserves the internal execution identity without letting it overwrite the
root task semantics.

---

## Recommended mapping rules

## Rule 1

`event.task_id` must always equal the root task ID.

## Rule 2

`payload.correlation.task_id` should default to the root task ID.

## Rule 3

If the underlying metadata payload already carries a different task ID that
represents an internal subtask, preserve it as:

- `annotations.subtask_id`

instead of letting it replace the root task identity.

## Rule 4

`step_id` stays in:

- `correlation.step_id`

## Rule 5

`call_id` or `execution_id` stays in:

- `correlation.execution_id`

## Rule 6

The trajectory layer may normalize identity for persistence even if the runtime
module originally emitted a narrower internal task ID.

This is acceptable because:

- the root task identity is the stable review axis;
- the original internal task identity is still preserved as subordinate
  evidence.

---

## Why this model fits the current project

This model matches the existing OpenPilot architecture:

- `metadata.correlation` already provides task / session / step / execution
  slots;
- the trajectory shell already owns `run_id`;
- internal executor task IDs already exist and can be preserved as subordinate
  annotations;
- no new top-level business metadata type is required.

So this is a **normalization rule**, not a new framework.

---

## Practical implementation guidance

## Minimal implementation target

For the current phase, the trajectory layer should enforce:

- `RunRecord.task_id = root_task_id`
- `EventRecord.task_id = root_task_id`
- `payload.correlation.task_id = root_task_id`

When a payload originally carried an internal task ID that differs from the
root task ID:

- preserve the original value under `payload.annotations.subtask_id`
- optionally set `payload.annotations.parent_task_id = root_task_id`

## What should not be changed

Do not remap:

- `step_id`
- `execution_id`
- `session_id`

These are already different layers of identity and should remain distinct.

---

## Interpretation checklist

When reading a trajectory:

1. Use `run_id` to locate the whole run.
2. Use `event.task_id` to group by top-level task.
3. Use `payload.annotations.subtask_id` to understand which internal task
   emitted the evidence.
4. Use `correlation.step_id` to locate the step.
5. Use `correlation.execution_id` to connect tools, LLM calls, and artifacts.

---

## Relationship to other trajectory docs

This document defines **identity stratification** only.

It complements:

- `docs/task_trajectory/TASK_TRAJECTORY_EVIDENCE.md`
- `docs/task_trajectory/TASK_TRAJECTORY_EVIDENCE_ARCHITECTURE.md`
- `docs/task_trajectory/TASK_TRAJECTORY_EVENT_ALIGNMENT.md`

If event payload alignment changes, review whether the ID stratification rules
still hold.
