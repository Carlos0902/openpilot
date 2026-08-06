# Agent Loop Protocol

## Purpose

This document defines the runtime-owned execution protocol for one OpenPilot
task. External scheduling and restart policy belong to
`AGENT_LOOP_SUPERVISOR.md`; durable restart semantics belong to
`AGENT_LOOP_SESSION_RESUME.md`.

## Runtime ownership

- `IntelligentAutopilot` owns task entry and the stable session identity.
- `AgentRuntimeController` owns phase, budget, recovery decisions, and safe
  checkpoint boundaries.
- `_RuntimeSessionExecutor` owns the ordered session stages.
- The tool loop owns one tool-call lifecycle and its `call_id`.
- Tool executors own concrete side effects and tool-specific evidence.
- Runtime diagnostics observes these facts but does not control execution.

## Stable phases

The runtime phases are the values of `AgentPhase`. A phase change is valid only
after the state facts that justify it have been applied. A persisted checkpoint
must name a boundary from `RuntimeCheckpointMetadata`; arbitrary stack frames
are not resumable boundaries.

## Root and subtask authority

The typed `RuntimeStateMetadata.execution_mode` belongs to the root task. Its
source is a user constraint, root task card, root goal, default, or migrated
legacy input. A subtask may plan a narrower read-only action set, but its kind,
tags, phase, or temporary assumptions must not mutate the root execution mode.
Required needs rejected by the Guard terminate that subtask as failed/blocked
and remain visible in `guard_history` and trajectory evidence.

## Side-effect rule

Mutating tools follow the durable progression `prepared -> observed -> applied
-> verified`. A process loss between `prepared` and `observed` produces an
indeterminate action. It must be reconciled before any replay.

## Project environment gate

Before core execution reaches a project-scoped Python validation, the session
executor performs a read-only environment preflight. A ready environment may be
attached automatically. Setup or resync is a separate side-effecting operation
and inherits the root execution mode and approval policy. Writing project code
may invalidate dependency readiness, so the runtime repeats preflight before
validation. Admission fails closed when no ready binding exists; command rewrite
must never substitute the host interpreter. The requested command remains the
completion identity and the effective interpreter/environment ID remain
execution evidence.

## Reasoning policy gate

Reasoning mode and effort are typed caller intent. The core resolver selects a
versioned capability profile from the configured provider endpoint and renders
provider-specific fields only at transport. Routine tool routing must be based
on typed task facts: bounded inspection, exact validation, or one-file
implementation. Ambiguous, general, and multi-write work remains
`provider_default`; a truncated response does not by itself authorize a higher
reasoning tier or a broader task.

## Completion rule

Success requires the controller's final state and verification policy to agree
with the session result. Planned deliverables are not execution evidence:
write/implement tasks require an observed successful file mutation, validation
tasks require an executed validation result, and `changed_files` contains only
observed side effects. A stopped, blocked, or evidence-incomplete run remains
inspectable and may be resumed only under `AGENT_LOOP_SESSION_RESUME.md`.

`ProjectImprovementPolicy` is the only authority for combining a completed core
task with the post-core enhancement stage. Runtime configuration owns the policy;
the iteration helper, controller, fast path, UI, and report only consume it.
Overall success is `core_success` when the policy is disabled or optional, and
`core_success && improvement_success` when required. Improvement failure or
interruption must be normalized into a terminal outcome and trajectory evidence;
it must never rewrite a completed core `TaskExecutionResult`.
An enhancement iteration is a file transaction over its explicit changed-file
set. Failure after mutation restores those files from the pre-iteration safety
snapshot and records rollback evidence before returning; the runtime must not
continue by repairing stale failed enhancement state.

Post-core model calls reserve from the checkpoint-owned enhancement completion
pool before transport. A logical call key is stable across resume; reserve and
reconcile are apply-once, known failed-attempt usage is charged, and unknown
usage remains conservatively reserved. Only one typed length-limit recovery of
an already bounded JSON contract is allowed. Controller-decision completion and
post-core enhancement completion are separate pools.
