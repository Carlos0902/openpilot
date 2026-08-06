# Phase 10 Stage 2 Plan: Feature-Flagged Rolling Summary Adapter

## Status

Plan and implementation complete. The production default remains
deterministic/current and the summary feature flag remains off.

## Objective

Add a narrow, injectable rolling-summary adapter that can consume only the
eligible old observation segment identified by the existing `ContextBuilder`
and return a validated `ContextCompactionRecord`. Reuse the current artifact
sink and `ContextAssembler._assemble_atomic_compactions` behavior. Do not wire
the legacy whole-conversation `ContextCompressor` into the runtime.

## Design

### Adapter boundary

The adapter receives:

- the immutable eligible source candidates;
- the previous validated summary payload, if any;
- the purpose-specific summary budget;
- a provider-neutral callable that returns a structured mapping and typed
  attempt evidence.

The adapter owns validation and record construction, but not source authority,
artifact persistence, or permission decisions. It must be injectable into
`MemoryContextBuilder` and absent by default.

### Rolling semantics

For a new segment, the compactor input is:

```text
previous validated summary (if present)
+ newly eligible old observations
```

It must not re-read or re-summarize the full raw dialog after a prior compact
record has been validated. A changed source fingerprint or missing previous
artifact invalidates the rolling replacement and falls back to deterministic
source selection.

### Atomic integration

The builder may offer the generated record as a preferred, non-truncatable
artifact candidate. The existing assembler must select it together with all
governed source decisions. If it is omitted, partially selected, stale, or
cannot be persisted, the builder returns the original deterministic source
view (or fails closed in strict mode).

### Budget and telemetry

Summary reservation is separate from the task completion reservation. The
adapter records requested summary limit, observed token count (nullable),
finish reason, validation outcome, source/previous-summary fingerprints, and
fallback reason. Unknown usage is a stop/fallback condition, never zero.

## Tests first

Add tests before implementation for:

- valid rolling input and stable source/previous-summary fingerprints;
- no eligible segment and no-op behavior;
- feature flag off and missing adapter behavior;
- malformed, empty, overlong, unknown-usage, and stale-source fallback;
- artifact sink failure and strict-mode fail-closed behavior;
- atomic compaction selection and recent/required candidate preservation;
- checkpoint/replay exactness and source mutation rejection;
- zero Provider/network/project/memory mutation in offline runs.

## Exit gate

Stage 2 is complete only when the adapter is default-off, legacy callers remain
zero, all failure paths restore deterministic source context, replay and
checkpoint tests pass, and the focused/full regression suite is green. No real
Provider canary is part of this stage.

## Metadata impact note

```text
Fact: provider-validated rolling summary record for one eligible source segment
Authoritative producer: injected summary adapter after validation
Consumers: MemoryContextBuilder, ContextAssembler, artifact sink, replay audit
Lifecycle: request-scoped candidate plus checkpointed compaction artifact
Control impact: context budget, recovery, evidence; no permission authority
Existing contracts reviewed: ContextCompactionSummary, ContextCompactionRecord,
  ContextCompactionBinding, ContextCandidate, ContextSelectionMetadata,
  RuntimePromptContextSnapshot
Decision: extend/reuse existing compaction record and binding; no new kind
Why no duplicate source of truth is created: raw dialog remains authoritative;
  the rolling record links source and previous-summary fingerprints
Serialization and migration: deterministic records remain readable; new fields
  are optional/versioned; stale/missing artifacts fail closed
Tests: adapter validation, budgets, atomic selection, fallback, replay, mutation
  and strict-mode gates
Documentation updates: this plan, implementation log, context README, API/catalog
  only if a public field changes
```
