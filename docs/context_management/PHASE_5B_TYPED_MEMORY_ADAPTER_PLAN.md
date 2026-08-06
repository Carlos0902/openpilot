# Phase 5B Plan: Typed Memory Context Adapter

## Status

Completed. This plan was written before phase 5B behavior or test changes.

## Objective

Replace the production `MemoryContextBuilder` section-dictionary selection path
with candidate-level assembly while preserving source ownership, rendered Prompt
semantics, checkpoint replay, and the compatibility payload consumed by current
non-model/UI code.

## Scope

- Convert fixed instruction, dialog messages, related project files, retrieved
  memories, and environment observations into individual `ContextCandidate`
  values with stable candidate/source identity.
- Keep `ContextAssembler` as the only budget and projection owner.
- Reconstruct the existing compatibility payload from selected source references;
  it remains a view and not a second memory/project store.
- Preserve recent-dialog preference, deterministic source rendering order,
  provider-token evidence, and exact checkpoint replay.
- Version the memory adapter request fingerprint so a typed-adapter run cannot
  replay a legacy section-selection artifact under the same request hash.

Out of scope: adding trust fields, contradiction policy, summarization, or a new
memory store. Those require later plans and production evidence.

## Current producers and consumers

Producers:

- `ShortMemory` supplies dialog messages;
- `MemoryStore` / `MemoryVaultAgent` supply memory records;
- `ProjectManager` supplies related-file projections;
- environment memory supplies project-environment observations;
- `ContextLoaderAgent` supplies the fixed instruction and query.

Consumers:

- Goal Maker and Task Designer consume bounded `prompt_text` and selection
  evidence through `ProjectStateSnapshot`;
- iteration dashboard reads compatibility section counts;
- runtime controller persists/replays the complete selected payload as a
  checksum `prompt_context` artifact;
- tests and memory context tool read selected source entries.

## Selection invariants

- Fixed instruction: required, priority 100, forbidden truncation.
- Dialog: each message has stable identity; newer messages have higher selection
  priority and selected dialog remains a contiguous recent suffix.
- Related files: preferred, deterministic score-derived priority, head
  truncatable, rendered after dialog.
- Memories: preferred, deterministic relevance/confidence-derived priority,
  head truncatable, rendered after files.
- Environment: optional, newest observations first for selection but rendered in
  source order after memories.
- Candidate selection order may differ from render order; render order remains
  instruction, dialog, files, memories, environment.
- Omitted sources stay authoritative in their existing stores and are not copied
  into checkpoint state.

## Metadata impact note

```text
Fact: one memory/project/dialog/environment source projection was selected,
  partially selected, or omitted for a model-facing memory context
Authoritative producer: each existing source store owns facts; MemoryContextBuilder
  owns candidate adaptation; ContextAssembler owns the derived decision
Consumers: iteration planning, dashboard compatibility view, diagnostics,
  RuntimePromptContextSnapshot
Lifecycle: runtime derived view and checksum checkpoint artifact
Control impact: model-input selection and recovery identity
Existing contracts reviewed: ContextCandidate, ContextAssemblyPolicy,
  ContextCandidateDecision, ContextSelectionMetadata, ContextAssemblyResult,
  ContextSectionDecision, RuntimePromptContextSnapshot, DurableArtifactReference
Decision: reuse existing owned nested candidate/selection values and the existing
  context-selection Metadata owner; add no MetadataKind or generalized source graph
Why no duplicate source of truth is created: compatibility section values are
  selected projections keyed back to current source IDs; source stores remain authoritative
Serialization and migration: retain the existing builder return keys and historical
  ContextSelectionMetadata defaults; change request fingerprint with an explicit adapter version
Tests: stable candidate/source IDs, recent-dialog suffix, source-order rendering,
  compatibility payload, exact token budget, checkpoint replay, legacy read, full regression
Documentation updates: API, contract catalog, test guide, implementation log,
  context README, this plan
```

## TDD sequence

1. Add failing tests for per-source candidate decisions and stable source IDs.
2. Add failing tests for contiguous recent-dialog selection and compatibility
   section reconstruction.
3. Add request-hash versioning and replay tests.
4. Implement the typed source adapter and grouped renderer with the smallest
   changes inside `memory/`.
5. Preserve section-level selection decisions as a compatibility projection from
   candidate decisions; candidate decisions remain authoritative.
6. Run context, memory tool, dashboard, iteration, metadata, checkpoint, recovery,
   diagnostics, and full suites.
7. Record completion evidence and remaining phase-5C trust/conflict limitations.

## Exit gate

- Production `MemoryContextBuilder` no longer calls legacy `assemble(payload)`.
- Every selected/omitted source item has a candidate decision with stable
  candidate ID, kind, source ID, and before/after size evidence.
- Fixed instructions cannot be partially retained.
- Selected dialog is a contiguous recent suffix.
- Existing compatibility keys and exact replay remain valid.
- Typed adapter request hashes cannot collide with legacy adapter hashes.
- Full repository tests pass before phase 5C planning begins.

## Rollback

Restore the legacy adapter call and its request fingerprint together. Do not
change checkpoint artifacts in place, weaken instruction retention, or keep a
partially migrated producer that emits candidate decisions inconsistent with its
compatibility sections.

## Completion evidence

- The four phase tests failed first on the legacy strategy, legacy assembler
  call, missing per-dialog omission evidence, and unversioned request hash.
- `MemoryContextBuilder` now calls only `assemble_candidates(...)` for production
  selection and reconstructs the historical section payload from typed decisions.
- Fixed instruction is required/non-truncatable; dialog decisions select a
  contiguous recent suffix; file/memory/environment candidates preserve source IDs.
- Adapter version participates in the checkpoint request fingerprint, while exact
  snapshot replay before source reads remains unchanged.
- Context, iteration, dashboard, metadata, checkpoint, and recovery regression:
  `138 passed`; `Code/tests` full suite: `658 passed`.
- `compileall` and `git diff --check` are part of the final phase gate below.

Remaining limitation for phase 5C: candidate relevance currently controls budget
selection, but provenance trust, stale evidence, duplicates, and contradictions do
not yet have typed governance.
