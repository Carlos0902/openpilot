# Task Trajectory Documentation Index

## Purpose

This file organizes the current task-trajectory / real-task-diagnostics
documents so the repository does not become ambiguous as the design evolves.

---

## Active documents

### 1. Design overview

- `./TASK_TRAJECTORY_EVIDENCE.md`

Use this for the high-level purpose and principles of the task trajectory
evidence layer.

### 2. Architecture

- `./TASK_TRAJECTORY_EVIDENCE_ARCHITECTURE.md`

Use this for layers, record families, and how the evidence system fits the
OpenPilot runtime.

### 3. Staged plan

- `./TASK_TRAJECTORY_EVIDENCE_PLAN.md`

Use this for phase planning and implementation order.

### 4. Event / metadata alignment

- `./TASK_TRAJECTORY_EVENT_ALIGNMENT.md`

Use this when adding or changing trajectory events.

### 5. ID stratification

- `./TASK_TRAJECTORY_ID_STRATIFICATION.md`

Use this when changing root task id, subtask id, step id, call id, or session id
handling.

### 6. Implementation log

- `./IMPLEMENTATION_LOG.md`

Use this as the chronological problem / validation / resolution record. Update
it whenever a diagnosed problem is completed.

### 7. Latest real-task failure analysis

- `./failures/REAL_TASK_FAILURE_ANALYSIS_2026-07-04.md`

Use this for the current observed failure phenomena and suspected root problems
from the latest real-task run.

### 8. Runtime checkpoint and recovery plan

- `../runtime_recovery/README.md`
- `../runtime_recovery/COMPREHENSIVE_RECOVERY_BOUNDARY_PLAN.md`
- `../runtime_recovery/RUNTIME_CHECKPOINT_RECOVERY_PLAN.md`

Use this for durable runtime checkpoints, restart recovery, side-effect
reconciliation, budget continuity, and recovery acceptance criteria. The
trajectory layer remains recovery evidence rather than the mutable recovery
source of truth.

### 9. Root/subtask state isolation plan

- `./ROOT_SUBTASK_STATE_ISOLATION_PLAN.md`

Use this for root execution-mode ownership, subtask-local planning constraints,
Guard rejection propagation, and evidence-backed completion/changed-file
semantics.

### 10. Recovery status and fallback plan

- `../runtime_recovery/RECOVERY_STATUS_AND_FALLBACK_PLAN.md`

Use this for typed recoverability status, additional checkpoint boundaries,
bounded supervisor behavior, unrecoverable-run fallback, recovery bundles, and
explicit linked-new-run handoff.

### 11. Reasoning policy and experiment plan

- `../active_iteration/REASONING_POLICY_PLAN.md`
- `../../experiments/full_architecture_context_observation/REASONING_POLICY_AB_RESULT_V1.md`

Use these for provider-neutral reasoning intent/resolution, provider-bound
replay identity, fixed-trajectory quality gates, and the current
full-architecture mechanism evidence and limitations.

---

## Legacy compatibility pointers

These are no longer the active design docs:

- `./legacy/REAL_TASK_DIAGNOSTICS.md`
- `./legacy/REAL_TASK_DIAGNOSTICS_PLAN.md`

They remain only so older references do not break.

---

## Stable thought documents

Do not modify these unless the user explicitly asks:

- `/Users/abab/Documents/openpilot/THOUGHT_ARCHITECTURE.md`
- `/Users/abab/Documents/openpilot/Thought.md`

---

## Documentation update rule

If a change completes a diagnosed problem in any of these areas:

- trajectory evidence;
- real-task execution;
- tool planning;
- path grounding;
- timeout / retry behavior;
- read-only guardrails;
- runtime state and completion;

then update:

1. the relevant design or plan doc if the intended architecture changed;
2. `./IMPLEMENTATION_LOG.md` with
   the completed problem slice.

The user should not need to separately request that the implementation log be
updated.
