# Phase 1 Plan: Typed Candidate Assembly — Completed

## Objective

Add a reusable typed candidate, policy, decision, and result protocol to the
existing `ContextAssembler`. Preserve the five-section memory adapter and every
serialized historical `ContextSelectionMetadata` payload.

## Metadata impact note

```text
Fact: one candidate is offered to an assembly policy, then selected, truncated, or omitted with a typed reason
Authoritative producer: source adapter produces candidate values; ContextAssembler produces decisions and result
Consumers: prompt-specific adapters, ContextAssembler, diagnostics, checkpoint artifact writer
Lifecycle: runtime-only input and derived event/checkpoint evidence
Control impact: budget and recovery
Existing contracts reviewed: ContextSelectionMetadata, ContextSectionDecision,
  RuntimePromptContextSnapshot, DurableArtifactReference, LLMRequestMetadata, LLMResponseMetadata
Decision: extend ContextSelectionMetadata with owned strict candidate decisions; add strict nested values without MetadataKind
Why no duplicate source of truth is created: candidate content is a model-facing projection with source identity;
  source stores remain authoritative and the assembly result remains derived
Serialization and migration: all new ContextSelectionMetadata fields have legacy defaults; old payloads remain valid
Tests: construction, invalid states, assignment, JSON round-trip, deterministic selection, required-candidate failure,
  truncation policy, exact-token and legacy adapter compatibility
Documentation updates: contract catalog, API, test guide, implementation log, phase plan
```

## Proposed strict values

- `ContextCandidate`: stable candidate ID, typed kind, source ID, content,
  retention, priority, ordering, and truncation policy.
- `ContextAssemblyPolicy`: named strategy plus character/token budget.
- `ContextCandidateDecision`: selected/partially selected/omitted, typed reason,
  before/after sizes, and source lineage.
- `ContextAssemblyResult`: typed status, rendered Prompt, selected candidates,
  and the existing `ContextSelectionMetadata` evidence.

These are owned nested values, not independent Metadata records. The only public
Metadata owner remains `ContextSelectionMetadata`.

## Legal behavior

1. Required candidates are considered before preferred and optional candidates.
2. Priority and source order break ties deterministically.
3. `forbidden` truncation either keeps the complete candidate or records it as
   an omitted required candidate and returns `budget_insufficient`.
4. `head` and `tail` truncation are explicit; free-form reason text cannot
   change selection.
5. `ready` results cannot omit a required candidate.
6. Exact token evidence is emitted only with an available named tokenizer;
   otherwise the policy explicitly uses the character boundary.

## TDD sequence

1. Add Metadata contract/round-trip/invalid-combination tests.
2. Add direct typed-assembler ordering, truncation, insufficient-budget, source
   immutability, and determinism tests.
3. Extend existing contracts with backward-compatible defaults.
4. Implement typed assembly beside the legacy memory adapter.
5. Run Metadata, assembly, memory, checkpoint, and full regressions.

## Exit gate

- Typed callers do not use section-name dictionaries to control selection.
- Required-candidate insufficiency is a typed result, not an exception string.
- Candidate decisions preserve source identity and exact before/after evidence.
- Legacy memory payloads, request hashes, checkpoint replay, and historical
  Metadata JSON remain compatible.
- Full repository tests pass.

## Rollback

Remove the typed API and optional legacy-default fields together. Do not alter
the existing memory adapter or checkpoint schema as a rollback shortcut.

## Completion evidence

- Added strict nested `ContextCandidate`, `ContextAssemblyPolicy`,
  `ContextCandidateDecision`, and `ContextAssemblyResult` values without a new
  `MetadataKind`.
- Extended `ContextSelectionMetadata` with typed assembly status, candidate
  decisions, and omitted-required IDs using legacy-compatible defaults.
- Direct contract and typed-assembly tests: `42 passed`.
- Metadata, assembly, memory, checkpoint, controller, and pipeline regressions:
  `156 passed`.
- `Code` full suite: `617 passed`.
- Phase 1 exit gate passed. No production business request path was migrated in
  this phase.
