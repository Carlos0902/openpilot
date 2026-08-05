# Phase 5C Plan: Typed Source Governance

## Status

Completed. This plan and metadata impact note were written before phase 5C behavior
or test changes.

## Objective

Add a small, deterministic governance pass before budget selection so context
assembly can explain exact duplicates, explicitly stale evidence, and explicit
conflict groups without guessing semantic contradictions or moving source facts
out of their owners.

## Diagnosed gap

Phase 5B gives every memory-context source an auditable candidate decision, but
the only decision causes are currently `within_budget` and `prompt_budget`.
`MemoryRecord` has confidence and timestamps, and the memory vault has an
embedding-based contradiction helper, yet neither produces a strict input to the
model-facing assembler. Consequently duplicate or explicitly conflicting source
projections can both consume budget, and stale evidence has no typed selection
meaning.

## Scope and restraint

- Extend the existing owned nested candidate/policy/decision values; add no new
  `MetadataKind` and no generalized relationship graph.
- Add typed trust and freshness vocabulary to `ContextCandidate`, plus an optional
  explicit `conflict_key`. Unknown/default values preserve historical behavior.
- Add typed policy switches for exact-duplicate, stale, and explicit-conflict
  governance. Defaults retain safe deterministic governance but do nothing when
  candidates carry no applicable evidence.
- Resolve exact duplicates only after normalization within the same candidate
  kind. Do not use embeddings or an LLM to infer equivalence.
- Resolve conflicts only when producers provide the same non-empty typed
  `conflict_key`; do not infer contradiction from similar prose, tags, or
  confidence gaps.
- Record `duplicate`, `stale`, and `conflict_precedence` as typed decision reasons
  and link the winning candidate where applicable.
- Fail closed when governance would omit a required stale candidate or when two
  materially different required candidates share a conflict key. This is a typed
  assembly status, not a provider/parse fallback.
- Map only facts already known by the memory adapter: fixed instructions are
  authoritative/current, dialog is direct/current, project-file sketches are
  observed/current, retrieved memories are historical with confidence reflected
  in priority, and environment memories are observed. No free-form memory
  attribute will silently control governance in this phase.

Out of scope: semantic contradiction detection, memory-store mutation, automatic
expiry heuristics, user-facing conflict resolution, or a source relationship
graph.

## Metadata impact note

```text
Fact: typed governance classification and derived selection disposition for one
  model-facing candidate
Authoritative producer: source adapters own trust/freshness/conflict facts;
  ContextAssembler owns derived duplicate/stale/conflict decisions
Consumers: request builder, provider preflight, diagnostics, checkpoint replay,
  memory compatibility view
Lifecycle: runtime derived view persisted inside existing context selection artifact
Control impact: candidates may be excluded or a request may fail closed before provider
Existing contracts reviewed: ContextCandidate, ContextAssemblyPolicy,
  ContextCandidateDecision, ContextAssemblyStatus, ContextSelectionMetadata,
  ContextAssemblyResult, RuntimePromptContextSnapshot, LLMRequest
Decision: extend existing owned nested values and enums; add no MetadataKind
Why no duplicate source of truth is created: governance values describe only a
  candidate projection; memory/file/dialog owners retain their facts
Serialization and migration: all new candidate/policy fields have compatibility
  defaults; historical decision/selection payloads remain readable; request hashes
  already include serialized selection for provider replay
Tests: legacy reads, enum validation, deterministic exact dedup, explicit stale,
  conflict precedence, required fail-closed, adapter mapping, replay, full regression
Documentation updates: API, catalog, test guide, context README, implementation log
```

## TDD sequence

1. Add failing metadata tests for valid governance values, illegal statuses, and
   backward-compatible defaults.
2. Add failing assembler tests for exact dedup, explicit stale omission, explicit
   conflict precedence, and fail-closed required conflicts.
3. Add failing memory-adapter tests for source trust/freshness evidence in candidate
   decisions without reading untyped `attributes` as control input.
4. Implement the governance pre-pass inside `ContextAssembler`, then run existing
   budget selection on eligible candidates.
5. Preserve source-order rendering and section compatibility projections.
6. Run metadata/context/request/replay/controller diagnostics and full `Code/tests`.

## Exit gate

- Every governed omission has a typed reason and, for duplicate/conflict cases, a
  winning candidate ID.
- Unknown governance values preserve legacy selection behavior.
- Required stale or mutually conflicting required candidates cannot be submitted.
- No semantic conflict is inferred from free text, embeddings, confidence, or tags.
- Memory adapter emits known trust/freshness facts but does not mutate sources.
- Historical serialized candidates, policies, decisions, and selections still load.
- Full repository main suite passes before phase 5D planning starts.

## Rollback

Remove the governance pre-pass and new optional/defaulted fields together. Do not
leave decision reasons that older assembly cannot reproduce, and do not mutate or
delete any source memories as part of rollback.

## Completion evidence

- Initial governance tests failed at contract import; after contract delivery,
  exact duplicate, stale, conflict precedence, and required fail-closed behavior
  passed. A subsequent compatibility test failed because section evidence still
  claimed `prompt_budget`; it now reports `source_governance`.
- Candidate/policy/decision/selection contracts gained only defaulted owned nested
  fields and enums; historical payload tests remain valid and no `MetadataKind`
  was added.
- Required governance failures use `governance_blocked` and
  `ContextAssemblyGovernanceError`; budget insufficiency remains distinct.
- Memory source mapping emits authoritative/current instruction, direct/current
  dialog, observed/current project files, retrieved/historical memories, and
  observed/historical environment projections without reading free-form attributes
  as control inputs.
- The memory adapter fingerprint is `typed_memory_candidates_governance_v2`, so
  governed runs cannot replay phase-5B snapshots.
- Targeted context/metadata/memory/request/recovery regressions passed; full
  `Code/tests` result: `666 passed`.

Remaining limitation for phase 5D: old dialog and large evidence can only be
truncated or omitted. There is no durable, source-linked compaction artifact that
can be replayed and invalidated independently.
