# Phase 5D Plan: Artifact-backed Dialog Compaction

## Status

Completed. This plan and metadata impact note were written before phase 5D behavior
or test changes.

## Objective

Replace a budget-omitted prefix of older dialog candidates with one bounded,
deterministic summary candidate only when that summary is persisted as a
checksum-verified recovery artifact. Preserve recent dialog verbatim and retain
per-source evidence that identifies which candidates the artifact compacts.

## Diagnosed gap

The current typed adapter preserves a recent dialog suffix and records omitted
older messages, but omission loses useful earlier decisions. The existing
`ContextCompressor` is an independent LLM/fallback utility: it estimates tokens
as `chars/4`, catches every exception, creates an untyped synthetic system
message, and does not bind output to source IDs, a checksum artifact, selection
metadata, or checkpoint recovery. Wiring it directly into production would
weaken the boundaries completed in phases 0-5C.

## Scope and restraint

- Compact only dialog candidates omitted by the initial prompt budget, and only
  when they form the expected older prefix before the selected recent suffix.
- Use a deterministic extractive algorithm in this phase; do not add another LLM
  call, heuristic token claim, or summary hallucination surface.
- Preserve recent selected messages verbatim. The compact summary is a separate
  `artifact` candidate rendered before recent dialog.
- Add a narrow typed `ContextCompactionRecord` and binding to the existing
  `DurableArtifactReference`. Add no `MetadataKind` and no generalized source
  relationship graph.
- Allow only an `artifact` candidate to name `compacted_candidate_ids`.
  Compacted source candidates receive reason `compacted` and link to the summary
  candidate. Required candidates may never be compacted.
- Trial-assemble the summary before persistence. If it cannot fit completely or
  artifact persistence is unavailable/fails, return the original non-compacted
  assembly without claiming compaction.
- Version the memory adapter request fingerprint again so a compacting run cannot
  replay a pre-compaction snapshot.
- Store the compaction payload via the existing checkpoint artifact store with
  kind `context_compaction`; bind references into
  `RuntimePromptContextSnapshot`; validate their checksum/size during recovery
  preflight alongside prompt/LLM/tool artifacts.
- Exact full prompt-context replay remains the recovery source. The independent
  compaction artifact provides source linkage and corruption detection, not a
  second prompt authority.

Out of scope: semantic/abstractive LLM summarization, memory-store mutation,
cross-session global summary reuse, compaction of required instructions/tool
schemas, or automatic deletion of dialog history.

## Metadata impact note

```text
Fact: one deterministic compact projection represents a specific set of dialog
  candidate projections and is persisted as a checksum artifact
Authoritative producer: ShortMemory owns dialog; MemoryContextBuilder owns the
  deterministic projection; CheckpointStore owns artifact durability;
  ContextAssembler owns compacted/selected decisions
Consumers: model prompt, selection diagnostics, RuntimePromptContextSnapshot,
  recovery preflight and exact prompt replay
Lifecycle: derived per-run artifact; invalid when source fingerprint changes;
  retained with its checkpoint/run artifacts
Control impact: replaces only non-required budget-omitted dialog candidates after
  a successful fit trial and durable persistence
Existing contracts reviewed: ContextCandidate, ContextCandidateDecision,
  ContextAssemblyResult, ContextSelectionMetadata, DurableArtifactReference,
  RuntimePromptContextSnapshot, RuntimeCheckpointMetadata, CheckpointStore
Decision: add narrow owned nested ContextCompactionRecord/Binding and extend the
  existing candidate/decision/snapshot values; add no MetadataKind
Why no duplicate source of truth is created: raw dialog remains in ShortMemory;
  summary is explicitly derived and source-fingerprinted; exact prompt artifact
  remains the replay authority
Serialization and migration: new fields default empty; historical checkpoints and
  candidate payloads remain readable; adapter version prevents snapshot collision
Tests: contract round trip, illegal compactor/source relationships, fit fallback,
  artifact binding, source fingerprint invalidation, corruption preflight, exact
  replay, recent-suffix preservation, full regression
Documentation updates: API, contract catalog, test guide, context README,
  implementation log, recovery docs where affected
```

## TDD sequence

1. Add failing metadata tests for compaction record/binding round trips, historical
   defaults, artifact kind validation, and illegal non-artifact compactors.
2. Add failing assembler tests that compacted non-required candidates link to the
   selected artifact and required candidates cannot be compacted.
3. Add failing memory-builder tests for initial omission -> summary fit -> artifact
   persistence, recent suffix preservation, no-sink fallback, and changed-source
   fingerprint.
4. Add failing checkpoint/controller tests for snapshot binding, artifact checksum
   preflight, and exact replay without regeneration.
5. Implement the smallest deterministic compaction and persistence hooks.
6. Run context/metadata/checkpoint/recovery/controller/diagnostics and full suite.

## Exit gate

- No compaction is claimed without a selected complete summary and durable
  `context_compaction` artifact.
- Every compacted source has a typed decision linked to the summary candidate.
- Required candidates cannot be compacted.
- Recent dialog remains verbatim and contiguous.
- Source changes produce a different fingerprint/record; corrupt artifacts block
  recovery preflight.
- Historical checkpoints remain readable.
- Full `Code/tests` passes before phase 5E planning starts.

## Rollback

Disable the builder compaction hook and remove compaction bindings from newly
created snapshots while retaining historical defaulted readers. Do not rewrite
or delete existing raw dialog or recovery artifacts.

## Completion evidence

- Contract/assembler/builder tests initially failed because no compaction values,
  candidate relationship, or persistence hook existed.
- `ContextCompactionRecord` and `ContextCompactionBinding` are default-compatible
  owned nested values; only artifact candidates may compact non-required sources,
  which receive linked `compacted` decisions.
- The builder first performs normal selection, deterministically compacts the
  limited older prefix, retains at least two recent messages verbatim, trial-fits
  the complete summary, and persists only after a successful trial. Missing or
  failed sinks return the initial assembly.
- The adapter fingerprint is now `typed_memory_candidates_compaction_v3`.
- Runtime snapshots bind compaction artifacts; recovery preflight and exact replay
  validate them. A dedicated corruption test blocks the session before execution.
- The first full run exposed a test budget (500 bytes) that could not legally hold
  a summary plus two recent messages; safe fallback was correct. The acceptance
  budget was corrected to 700 bytes and full `Code/tests` passed: `672 passed`.
- `compileall` and `git diff --check` pass.

Remaining limitation for phase 5E: context selection now has rich evidence but no
deterministic quality scorecard or corpus of expected inclusions/exclusions. The
old standalone `ContextCompressor` and legacy `assemble(payload)` implementation
also remain available and need evidence-based convergence rather than immediate
deletion.
