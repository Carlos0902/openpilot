# Phase 5E Plan: Context Quality and Legacy Convergence

## Status

Completed. This plan and metadata impact note were written before phase 5E behavior
or test changes.

## Objective

Add an offline, deterministic quality gate for assembled context and make legacy
section assembly/compression explicitly non-production so future changes cannot
silently reintroduce the paths replaced in phases 1-5D.

## Inventory evidence

- `ContextAssembler.assemble(payload)` has no production caller; only two legacy
  compatibility tests invoke it.
- Production memory context calls `assemble_candidates(...)` and all 22 LLM
  request purposes use `ContextRequestBuilder`.
- The standalone `ContextCompressor` has no production caller. Its `chars/4`
  estimator, broad fallback, and synthetic message remain incompatible with the
  typed artifact compaction boundary.
- `ContextSectionDecision` remains a live compatibility projection consumed by
  existing memory/dashboard clients, so removing that value now would be a
  breaking cleanup without benefit.
- `priority_then_recency_v1` remains necessary only as a historical selection
  default/reader; current typed assembly emits `retention_priority_order_v1`.

## Scope and restraint

- Add strict owned nested `ContextQualityExpectation` and
  `ContextQualityEvaluation` values, with typed issue codes. Add no
  `MetadataKind`; evaluation is offline derived evidence, not runtime control.
- Evaluate a completed `ContextAssemblyResult` against explicit expected-present
  and expected-absent candidate IDs and structural invariants:
  ready status, budget compliance, required representation, complete decision
  coverage, no normalized duplicate leakage, linked governed omissions, recent
  dialog suffix continuity, and compaction linkage.
- Never claim generic semantic relevance. Expected inclusion/exclusion comes from
  fixture authors, not an embedding score or evaluator LLM.
- Add a small deterministic fixture corpus covering budget retention, explicit
  conflict, duplicate governance, and compaction linkage.
- Add a production-source inventory test that fails if code outside the legacy
  definitions calls section `assemble(payload)` or instantiates
  `ContextCompressor`.
- Keep legacy readers/definitions for compatibility, but mark them deprecated and
  document their allowlist. Do not delete `ContextSectionDecision` or historical
  strategy literals.
- Change request fingerprint strategy identity to the actual typed strategy and
  bump the memory adapter version, preventing collision with previous snapshots.

Out of scope: online reward modeling, LLM-as-judge, automatic policy tuning,
semantic relevance claims, or deletion of backward-compatible serialized fields.

## Metadata impact note

```text
Fact: offline comparison between one typed assembly result and explicit quality expectations
Authoritative producer: fixture/test author owns expectations; evaluator owns derived issues/metrics
Consumers: regression tests and architecture diagnostics only
Lifecycle: ephemeral/offline; not persisted into runtime state or used for routing
Control impact: test/acceptance gate only, never model-request selection
Existing contracts reviewed: ContextCandidate, ContextCandidateDecision,
  ContextAssemblyPolicy, ContextAssemblyResult, ContextSelectionMetadata,
  ContextCompactionRecord/Binding, RuntimePromptContextSnapshot
Decision: add strict nested quality values and stateless evaluator; no MetadataKind
Why no duplicate source of truth is created: evaluation references candidate IDs
  and selection evidence; it does not copy source facts or alter the result
Serialization and migration: new values have no persisted production owner;
  historical strategy/section fields stay readable
Tests: quality pass/fail issue codes, fixture corpus, deterministic output,
  inventory guard, legacy warning, request hash version, full regression
Documentation updates: API, catalog, test guide, context README, implementation log
```

## TDD sequence

1. Add failing metadata/evaluator tests for valid round trip, deterministic pass,
   missing expected evidence, forbidden leakage, duplicate leakage, and broken
   governed/compaction links.
2. Add a failing fixture-corpus test for budget, conflict, duplicate, and compaction
   cases.
3. Add failing static inventory tests for production legacy callers.
4. Implement the stateless evaluator and legacy deprecation boundary.
5. Update current strategy/hash identity and adapter version; verify exact replay
   tests still pass under the new fingerprint.
6. Run full context/metadata/recovery/diagnostics and `Code/tests`.

## Exit gate

- Quality evaluation is deterministic and uses only explicit expectations and
  typed assembly evidence.
- The fixture corpus covers the delivered selection/governance/compaction layers.
- Production source has zero legacy section-assembler or standalone-compressor callers.
- Compatibility definitions warn on use and historical payloads still load.
- Typed strategy identity is consistent in metadata and request fingerprints.
- Full main suite, compileall, and diff check pass.

## Rollback

Remove the offline evaluator and inventory gate without changing runtime selection.
Keep historical readers intact; do not restore a production legacy caller merely
to silence a compatibility warning.

## Completion evidence

- The new quality test initially failed at module import, proving no evaluator or
  quality contracts existed before implementation.
- Strict expectation/evaluation values round-trip without a runtime Metadata owner.
  The stateless evaluator emits deterministic issue codes for explicit inclusion/
  exclusion and structural selection/governance/compaction invariants.
- The four-case JSON fixture corpus passes for budget retention, explicit conflict,
  normalized exact duplicate, and artifact compaction. A repeated failure case
  produces byte-for-byte equal evaluation values.
- AST inventory proves production source has zero legacy `.assemble(...)` calls and
  zero `ContextCompressor` instantiations outside their definitions. Compatibility
  calls emit deprecation warnings; historical section/strategy payloads remain valid.
- Current request strategy is `retention_priority_order_v1`; memory adapter
  fingerprint is `typed_memory_candidates_quality_v4`.
- Full `Code/tests`: `680 passed`. Final compileall/diff/static checks are recorded
  in the goal closeout.

Remaining limitations are explicit rather than hidden: quality expectations are
fixture-authored and do not measure open-ended semantic relevance; compaction is
deterministic/extractive rather than abstractive; historical compatibility code is
quarantined but intentionally not deleted.
