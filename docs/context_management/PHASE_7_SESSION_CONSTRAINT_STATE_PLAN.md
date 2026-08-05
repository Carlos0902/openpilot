# Phase 7 Plan: In-session Session Constraint State

## Status

The Session Constraint State foundation is complete and tested. A follow-up
production-entry audit found that the reducer has no production caller yet and
that the main standard/enhanced planner path does not consume the
`MemoryContextBuilder` projection. Therefore this phase is not a claim that
the full interactive conversation architecture is production-complete; the
follow-up production-ingress and canary work is tracked in
`PHASE_8_REAL_BENEFIT_CANARY_PLAN.md`.

## Objective

Preserve explicit, stable constraints for the lifetime of one conversation so
they survive dialog compaction without turning ordinary history into permanent
memory. The raw dialog remains the source of truth. A typed, source-linked
proposal may become an active session constraint only through an explicit
confirmation or an already-authoritative typed task contract. Active constraints
are projected as required context and are also checked at the relevant runtime
permission and verification boundaries.

## Non-goals

- no cross-session or project-wide memory write;
- no promotion of every user sentence, preference, or model inference;
- no replacement of raw dialog, task metadata, or observed tool evidence;
- no prompt-only enforcement of file scope, validation, or API compatibility;
- no provider-specific extraction or reasoning policy;
- no real-task canary before offline replay and safety gates pass.

## Stages and gates

1. **Stage 0 — inventory and impact note (complete).** Audit the existing runtime state,
   checkpoint, task/file scope, verification, context candidate, compact, and
   replay owners. Decide whether the state is an owned nested value of
   `RuntimeStateMetadata` and checkpoint state. Result: add strict owned nested
   values to `RuntimeStateMetadata`; do not add a public `MetadataKind` or copy
   the state into `SessionExecutionCursor`. Gate passed: every field has one
   owner, a typed legal state, and a named producer/consumer.
2. **Stage 1 — contract red tests.** Add strict proposal, active-constraint,
   conflict, scope, confirmation, supersession, and source-lineage tests before
   implementation. Gate: invalid control states fail closed and JSON round-trip
   is deterministic.
3. **Stage 2 — proposal and lifecycle.** Extract only deterministic, explicit
   constraint forms; support confirm, reject, conflict, supersede, revoke, and
   scope expiry. Unconfirmed proposals never control routing, permissions,
   verification, or completion. Gate: lifecycle tests and raw-dialog authority
   tests pass.
4. **Stage 3 — required-context projection.** Derive bounded
   `ContextCandidate` values from active constraints, preserve source IDs, and
   include them in compact/replay/request fingerprints. Gate: required active
   constraints survive omission/compaction; raw dialog remains unchanged; a
   budget failure is explicit rather than silent omission.
5. **Stage 4 — runtime enforcement (complete).** Bind typed file scope, validation command,
   and API-compatibility constraints to existing path, mutation, guard, and
   verification owners. Prompt projection is explanatory evidence, not the
   enforcement authority. Gate: disallowed writes and wrong validation commands
   fail closed, while unrelated tasks do not inherit stale constraints.
6. **Stage 5 — offline replay and admission review (complete for the foundation).** Run long-dialog replay,
   compaction quality, constraint recall/precision, conflict/supersession,
   prompt-growth, checkpoint/resume, and zero-provider mutation tests. Gate:
   deterministic hashes, no required-context loss, no cross-session leakage,
   bounded growth, and a documented decision about canary readiness. Result:
   `experiments/full_architecture_context_observation/STAGE11_SESSION_CONSTRAINT_OFFLINE_RESULT.md`.

## Completion evidence so far

- Stage 0 audit: three read-only audits and 20 existing runtime/context tests
  passed; the owner model and execution boundary are documented above.
- Stage 1 contracts: session-state tests cover typed variants, source/hash
  lineage, invalid combinations, JSON round-trip, runtime-state ownership, and
  revoked tombstones.
- Stage 2 reducer: deterministic tests cover user-only extraction, stable
  source IDs, proposal confirmation, same-key supersession, session mismatch,
  rejection, and revoke behavior.
- Stage 3 context projection: active state is one required,
  forbidden-truncation, source-hash-bound constraint candidate; revoked values
  are not rendered; the state hash enters request identity; required budget
  failure is explicit. Existing memory/checkpoint/context regressions remain
  green (`109 passed`).
- Stage 4 runtime enforcement: the controller, normal tool-event guard, and
  fast mutation path all narrow writes and validation against the typed state;
  project/session identity is checked and the state is forwarded through the
  real Context Loader → Iteration Agent → Project Improvement Runtime chain.
  Related runtime and pipeline tests passed (`224` targeted runtime tests and
  `52` pipeline/context tests).
- Stage 5 offline replay: the full-truth, compact-without-state, and
  compact-with-state arms ran at 10/20/50 messages with zero provider/network
  calls and zero project mutations. Active constraint recall was 100% with
  state and 0% assistant-origin authority acceptance. Compact prompts stayed
  at the 2,200-character cap; 50-vs-20 growth was 0%, while the without-state
  arm lost the early exact validation and write-scope facts in all three sizes.
  Assistant-only noise kept the state hash stable while changing the prompt
  hash; a user constraint revision changed the state hash.

## Metadata impact note

```text
Fact: an explicit, source-linked conversation constraint moves through proposed,
      confirmed/rejected, active, superseded, revoked, or expired states and may
      be projected into a required model-facing candidate
Authoritative producer: raw dialog remains authoritative; a deterministic
      constraint extractor creates proposals; the runtime/session owner applies
      confirmation and lifecycle transitions; existing task/path/verification
      owners remain authoritative for execution facts
Consumers: RuntimeStateMetadata and RuntimeCheckpointMetadata for session state;
      ContextCandidate/ContextAssembler for required projection; path/mutation
      guards and VerificationPlanMetadata for enforcement; replay/diagnostics
      for lineage and audit
Lifecycle: runtime session state plus checkpoint snapshot; derived prompt
      candidates are request-scoped and never become a memory record
Control impact: routing, permission, recovery, verification, and context budget;
      proposal text alone has no control effect
Existing contracts reviewed: RuntimeStateMetadata, RuntimeCheckpointMetadata,
      SessionExecutionCursor, ContextCandidate, ContextSelectionMetadata,
      ProjectStateMetadata, Task.write_files, TaskGraphNodeMetadata,
      PathResolutionMetadata, GuardDecisionMetadata, VerificationPlanMetadata,
      RuntimePromptContextSnapshot, and existing dialog compaction bindings
Decision: extend RuntimeStateMetadata with strict owned nested session-constraint
      values; do not add a new MetadataKind or relationship graph. Keep
      TaskGraphNodeMetadata.write_files/validation_command and
      RuntimeExecutionMode as the execution authorities; the session state is a
      source-linked constraint ledger and narrowing view, not a second authority
Why no duplicate source of truth is created: proposals and active values carry
      source message IDs/fingerprints; task/file/verification facts are reused
      or derived into their existing owners; raw dialog and exact prompt artifact
      remain authoritative
Serialization and migration: new nested fields default empty; historical runtime
      checkpoints remain readable; active constraints are session-scoped and are
      invalid outside their session or after explicit revocation
Tests: strict construction/invalid states/assignment/JSON round-trip; lifecycle,
      conflict and scope transitions; required-candidate projection; compact and
      replay hash binding; file/validation enforcement; offline long-dialog replay
Documentation updates: this phase plan, metadata catalog/API, context README,
      task-trajectory implementation log, and testing guide as behavior lands
```

## Stage 0 audit result

The existing fields are insufficient for this purpose:

- `RuntimeStateMetadata.execution_mode` only captures the root read-only versus
  mutation boundary;
- `known_facts` and `assumptions` are explanatory free text and cannot control
  permissions, routing, or retention;
- `TaskGraphNodeMetadata.write_files` and `validation_command` remain the
  per-task execution contract and must not be duplicated into a competing
  authority;
- `ContextCandidate(CONSTRAINT)` is a request-scoped projection, not state;
- project intent and long-term memory have a broader lifecycle and must not be
  used for in-session authority.

The selected shape is therefore `SessionConstraintState` containing typed
`SessionConstraintEntry` values nested under `RuntimeStateMetadata`. Revoked
entries remain as tombstones, and every entry carries a source user-message ID,
turn cursor, source hash, logical conflict key, and supersession link. Assistant
messages, summaries, and compact artifacts cannot produce authoritative entries.

## Candidate constraint classes

The first implementation is intentionally narrow and typed:

- allowed or forbidden target files;
- exact required validation command(s);
- preserve-existing-API compatibility;
- current goal or acceptance correction explicitly confirmed by the user.

Each class has a structured value and a bounded human-readable rendering. A
source excerpt or explanation is evidence only and cannot change the control
decision.

## Exit criteria

- Only explicit, stable, confirmed (or already typed task-authoritative)
  constraints become active.
- Active constraints survive compact/replay and are source-linked without
  retaining whole historical messages.
- Runtime enforcement consumes typed state rather than prompt text.
- Constraints are session-scoped, conflict-safe, revocable, and bounded.
- Offline tests and replay pass before any production canary is considered.
