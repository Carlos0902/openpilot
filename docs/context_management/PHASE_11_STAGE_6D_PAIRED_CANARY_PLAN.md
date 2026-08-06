# Phase 11 Stage 6D Plan: Small Current/Treatment Paired Canary

## Status

Completed offline. This stage admits only a small, read-only paired evidence
gate; it cannot change the global Compact default.

## Objective

Compare Current deterministic context with a Treatment projection on the same
source envelope across three low-risk purposes. A Treatment summary may enter a
pair only after a successful Stage 6B attempt and Stage 6C replayable evidence;
otherwise the pair remains on Current fallback and is counted explicitly.

## Pair contract

- Every pair shares source-envelope, session-constraint, task-input, and
  completion-policy identities.
- Current and Treatment carry prompt token counts, required-candidate retention,
  source provenance, verification result, quality result, and observed mutation
  paths.
- Treatment records whether the summary was actually used in its Prompt. An
  accepted shadow result alone is insufficient evidence; the projection must
  explicitly prove source lineage and required-state retention.
- Read-only cases require zero mutation paths and a passed verification gate.
- Minimum coverage is one pair each for context compaction, goal/plan, and
  tool-event decision. Pairs are interleaved and bounded by a fixed request and
  token cap.

## Admission and rollback

- Feature flag and kill switch are checked before any Treatment projection.
- A failed pair stops the canary; it cannot be hidden by averaging token
  savings. Unknown usage, fallback, provenance, required-state, verification,
  quality, and suspicious-success signals remain visible.
- The result may report token reduction and provider calls, but
  `rollout_admitted=false` and `global_default_changed=false` are invariant.
- Rollback is simply Treatment off plus Current deterministic context.

## Metadata impact note

```text
Fact: one bounded paired context projection result
Authoritative producer: paired canary harness from immutable source/evidence
Consumers: Stage 6E analysis and rollout decision only
Lifecycle: experiment evidence; never task/permission/runtime authority
Existing contracts reused: Stage 6A manifest, Stage 6B observation,
  ContextCompactionRecord, SessionConstraint required projection, attempt receipt
Decision: add narrow pair/projection evidence models and an aggregate gate;
  do not change production ContextBuilder defaults or add MetadataKind
Serialization: strict versioned JSON; mutation/quality/required fields typed
Tests: purpose coverage, source mismatch, required retention, provenance,
  verification, mutation, fallback, token accounting, kill switch, and
  no-global-rollout gates
```

## Exit gate

- Three-purpose offline paired fixtures pass with zero project/memory mutation.
- A single source/provenance/required/verification/quality/mutation failure
  stops admission and identifies the pair.
- Token/call/fallback metrics are reported independently of quality gates.
- No global default, reasoning policy, completion policy, or constraint policy
  changes as a result.

## Evidence

- Added `stage20_paired_canary.py` with typed Current/Treatment projection
  evidence, three fixed purposes, required-candidate/provenance/verification/
  quality/mutation checks, source/constraint hash checks, and independent
  token/call/fallback/unknown-usage accounting.
- A Treatment projection must explicitly prove that its accepted summary was
  used and carries the same compaction ID; a shadow observation alone cannot
  satisfy this gate. Fallback remains visible and is counted as Current route.
- Five offline tests cover three-purpose pass, unknown-usage fallback,
  required-state failure, mutation failure, source mismatch, kill switch, and
  coverage. Stage 6A–6D plus telemetry/summary focused set: **84 passed**.
- No project/memory mutation, Provider call, network request, global default,
  reasoning policy, completion policy, or constraint policy changed.
