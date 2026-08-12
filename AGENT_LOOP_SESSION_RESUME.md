# Agent Loop Session Resume Protocol

## Purpose

This document is the normative short-form contract for restarting an OpenPilot
runtime session. The staged implementation and test matrix live in
`docs/runtime_recovery/RUNTIME_CHECKPOINT_RECOVERY_PLAN.md`.

## Pre-task iteration records

The feature-flagged unified autonomous entry uses a conversation-owned
`IterationTurnRecordMetadata` before any task checkpoint exists. Its offline
`IterationTurnStore` is separate from `RuntimeCheckpointStore`: it persists
immutable turn generations, checksum-bound artifacts, and a revisioned
`SessionIngressState` snapshot. Reads may fall back from a corrupt latest turn
record to the newest previous valid generation, but a writer must fail closed
when any existing turn history or ingress snapshot is unreadable. Corruption
must never be interpreted as generation/revision zero.

Response artifacts are content-addressed. `IterationTurnCommitter` recovers the
response path in the fixed order pending turn record → assistant ingress turn →
committed turn record. The assistant message ID, turn index, payload reference,
and canonical payload hash must match at every boundary. An identical retry
reuses the same terminal record/payload; an existing message ID with different
content fails closed. A crash after the ingress write must not append another
turn, and recovery after the terminal record write may replay only the exact
durable display payload. This replay never invokes or authorizes a Provider.

This response recovery is not task-resume authorization. Prepared-task
materialization, current session-authority freshness/revocation checks, and the
transition to an active `RuntimeCheckpointMetadata` remain mandatory gates
before the unified entry can be enabled.

## Source of truth

`RuntimeStateMetadata.recovery_status` is the current operational status;
`RuntimeCheckpointMetadata` persisted by `RuntimeCheckpointStore` is its durable
snapshot. `RuntimeResumeDecisionMetadata` is the immutable assessment for one
resume attempt. Task trajectory events are audit evidence and Git/file
fingerprints are reconciliation evidence.

## Identity

Resume preserves `run_id`, `root_task_id`, and `session_id`. Each resume creates
a new `resume_attempt_id`; every checkpoint written by that attempt also records
the selected immutable `resume_source_checkpoint_id`. Retrying an older
checkpoint continues generation numbering after the run's current latest
generation. Internal subtask, step, and execution IDs retain their documented
subordinate meanings.

## Preflight

Before execution resumes, the runtime must validate:

1. checkpoint checksum and schema compatibility;
2. requested run, task, session, and project root;
3. project and relevant file fingerprints;
4. pending side-effect state;
5. restored budget and stop conditions;
6. restored root `execution_mode`, its source, and compatibility migration;
7. session plan hash and contiguous completed-result prefix;
8. checksums for referenced LLM/read recovery artifacts;
9. ordered verification progress as a contiguous command prefix.
10. finalization cursor stage, report artifact checksum/state hash, and completion event identity.
11. Prompt-context request hash, rendered Prompt hash, selection record, prompt
    artifact checksum, and every source-linked context-compaction artifact checksum.
12. the checkpointed project interpreter/environment identity and a fresh
    read-only readiness observation for any pending Python verification.
13. for replayable LLM observations, a `provider_bound_v2` request identity
    covering provider, model, credential-free endpoint including non-default
    port, capability-profile version, and effective reasoning semantics.

The authoritative result has four typed dimensions:

- `recoverability`: `recoverable_now`, `recoverable_after_action`,
  `not_recoverable`, or `already_complete`;
- `recovery_mode`: exact resume, reconciliation, replan, retry,
  user-assisted resume, completed return, or none;
- `automation_policy`: automatic, approval required, manual only, or forbidden;
- stable `reason_code`, typed blockers, and one typed fallback action.

The old `decision` and result-level `resume_status` fields remain compatibility
projections only. They must not drive new control flow, and model validation
rejects contradictions with the typed fields.

Unsupported or ambiguous cases fail closed. They must not silently start a new
run.

A missing/corrupt requested checkpoint returns a typed unavailable assessment
instead of an unclassified exception. If a prior generation is valid, preflight
offers `use_previous_valid_checkpoint` and requires explicit confirmation. If
no valid checkpoint remains, the run is `unrecoverable` and the fallback is to
terminate while preserving available evidence. A linked new run is never an
implicit resume.

Before reading or applying a checkpoint, resume must acquire the run's
non-blocking single-writer lease. If another process still owns it, preflight
returns `recoverable_after_action + retry_from_checkpoint + run_lease_active`
and does not write an event to the active run. The caller may retry only after
the lease is released.

The recovered root execution mode is authoritative. A resumed internal
inspect/validate subtask cannot downgrade a mutation-allowed root, and no
subtask can upgrade a read-only root. Legacy read-only assumption markers are
migrated once into the typed mode before execution continues.

## Budget

All consumed counters are restored exactly. A resume attempt consumes the
existing recovery budget. Budget exhaustion remains a stop unless the user
explicitly authorizes and records a budget extension.

## Side effects

Observed LLM and local read results are replayed only when request/call identity,
input hash, ordinal, and artifact checksum match. Replay applies the stored result
without another provider/read call or duplicate budget charge. Prepared but
unobserved work follows its declared bounded retry policy. Mutating calls are
never replayed from an indeterminate state. File operations
must reconcile target hashes and Git evidence; commands and external writes
require a tool-specific idempotency key or probe.

Legacy unbound LLM hashes cannot prove provider or reasoning equivalence and
therefore fail closed for replay. Endpoint normalization strips credentials and
query data but preserves a non-default port, so distinct local/provider routes
cannot collide.

Completed task state uses a separate finalization recovery mode. Recovery from
`state_completed` may derive and persist the report; recovery from
`report_persisted` may apply the idempotent completion event; recovery from
`runtime_finalized` only returns durable evidence. None of these paths may call
the session executor. The report is a checksum artifact derived from completed
state, and `task_finished:<finalization_id>` is the recorder idempotency key.

If a file-mutation checkpoint contains `pending_verification`, recovery must
execute that exact typed ordered plan after reconciliation, advance its durable
index after each successful command, and clear it only after the final command.
A generic smoke command cannot satisfy a different persisted validation need.
Before any resume event is emitted, the replacement process must attach its
recorder to the checkpoint's existing `run_id` and validate root-task/session
identity; it must not infer a new run from an empty in-memory alias cache.
Before executing a pending Python command, resume must rebuild the ready
environment attachment and apply the same requested/effective command binding
as a live session. Missing or drifted environment evidence blocks recovery.
Resume preflight itself may attach an existing environment but may not create a
venv, install packages, mutate Git state, or silently use host Python. Package
registry/network state is reassessed by a separately authorized setup/resync and
is not claimed as an exactly-once replayable side effect.

The session cursor is supported by both standard and enhanced-UI execution.
When a checkpoint owns `RuntimePromptContextSnapshot`, the restored context
builder must replay its checksum-verified payload for the identical request
before reading current dialog, memory, or project sources. A request mismatch
selects fresh context; a matching request with corrupt evidence fails closed.
`context_assembled` is resumable only with a valid session bootstrap or cursor.
Relative task file scopes and resume tool paths are grounded against the restored
project context. The task-local `no_progress` stop condition is reset only when
starting a new subtask and only if it was the exact source of the prior block;
permission, budget, drift, and other blockers remain authoritative.

## Evidence

Every request records the checkpoint, recoverability, mode, automation policy,
reason code, blockers, fallback, remaining budget, and evidence references.
Human explanation and instructions are display-only. Diagnostic failure must
not change the decision; checkpoint failure means the boundary cannot be
advertised as durable. Event append uses a per-run file lock and derives the
next sequence from durable events while holding that lock; process-local caches
must not create duplicate sequence numbers.
