# Phase 10 Stage 3 Plan: Session Constraint Boundary

## Status

Plan and Stage 3A/3B implementation complete. Agent Generator ingress
unification and independent verification evidence remain explicitly out of
this stage's accepted scope.

## Objective

Make conversation-scoped persistence reliable without turning ordinary dialog,
assistant text, summaries, or compact artifacts into execution authority. The
stage extends the existing `SessionIngressState` / reducer path only.

## Findings carried from Stage 0

- The interactive loop recognizes `/constraints` but not the already-supported
  `/confirm`, `/reject`, and `/revoke` commands at the dispatch boundary.
- A later proposal with the same key does not currently invalidate an older
  pending proposal, so a delayed confirmation can revive stale intent.
- Pending proposals and revoked entries have no hard count/size budget.
- Runtime guards exist for write scope, read-only mode, and validation command,
  while API compatibility and acceptance criteria remain projection-only facts.

## Scope and order

### 3A — Ingress and lifecycle safety

1. Route all constraint commands through the same typed ingress handler.
2. Mark older pending same-key proposals as `superseded` when a newer user
   proposal is accepted; a superseded proposal cannot be confirmed.
3. Preserve source message ID, source hash, source turn, confirmation turn, and
   revocation turn in the existing typed state. Never infer authority from a
   rendered statement.

### 3B — Bounded state and enforcement evidence

1. Add typed count/serialized-size limits for pending proposals, active entries,
   revoked tombstones, and each value variant. Exceeding a limit fails closed.
2. Add deterministic tests that API compatibility and goal acceptance are
   carried as required context but are not reported as verified execution until
   an existing task/verification evidence owner confirms them.
3. Keep extraction conservative: explicit stable user directives only; no
   model-generated extraction is allowed to activate a constraint.

### 3C — Checkpoint and multi-turn coverage

1. Verify confirm/reject/revoke, supersession, quota failure, project/session
   scope, and replay round trips through `SessionIngressState`.
2. Verify every production prompt path receives the same active constraint
   projection or fails closed when ingress is absent; do not silently create a
   second authority in Agent Generator.

## Metadata impact note

```text
Fact: explicit user-confirmed session constraint and its lifecycle evidence
Authoritative producer: SessionIngress/reducer from user-origin turns
Consumers: ContextAssembler required projection, runtime guards, checkpoint/replay
Lifecycle: conversation-scoped ingress state; reset at new conversation
Control impact: routing, permission, validation, and context retention
Existing contracts reviewed: SessionConstraintState/Entry/Proposal/Value,
  SessionIngressState, RuntimeStateMetadata, ContextCandidate, VerificationPlanMetadata
Decision: extend existing nested contracts and reducer; no memory record or new
relationship layer
Source-of-truth rule: raw ingress turn remains evidence; only confirmed reducer
state controls runtime
Failure policy: stale, cross-session, over-quota, ambiguous, or unverified input
is rejected or remains a non-authoritative proposal
Migration: optional lifecycle fields preserve old checkpoint readability; invalid
new states fail closed
Tests: command dispatch, extraction precision, supersession, quotas, enforcement,
  replay, scope, and no assistant/summary authority
```

## Exit gate

- All four constraint commands reach one ingress owner.
- A superseded proposal cannot activate and cannot overwrite a newer value.
- State and prompt projections remain bounded under adversarial repeated turns.
- Required projections include only active, source-linked entries.
- Runtime enforcement and checkpoint/replay tests pass with no Provider/network
  or long-term-memory side effects.

## Rollback

Disable new extraction/constraint handling at the ingress feature boundary while
retaining the historical reducer schema. Existing active state remains readable;
no compaction or reasoning behavior changes in this stage.
