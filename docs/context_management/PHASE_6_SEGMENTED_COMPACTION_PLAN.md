# Phase 6 Plan: Segmented Context Compaction

## Status

Completed for offline admission. This plan and metadata impact note were recorded
before the offline long-trajectory experiment. No provider or real-task canary
was run in this phase.

## Objective

Strengthen the existing selective projection with deterministic observation
masking and source-linked segmented compaction. Preserve typed authority,
current failures, exact validation commands, recent context, and recovery
evidence while bounding repeated old observations.

## Stages

1. Audit the existing selection, compaction, artifact, recovery, and quality
   contracts and freeze semantic/permission gates.
2. Bound recoverable-tool prompts by masking only explicit large observation
   fields; retain control inputs and the authoritative `ToolErrorMetadata`.
3. Extend artifact-backed dialog compaction with a versioned deterministic
   observation-mask algorithm and atomic source/replacement selection.
4. Admit the algorithm only on `MemoryContextBuilder`, with a new adapter
   fingerprint and backward-compatible recovery validation.
5. Compare full, recent-only selection, and segmented compaction on a zero-provider
   long-trajectory corpus before considering any provider or real-task canary.

## Metadata impact note

```text
Fact: an older non-required model-facing segment may be represented by one
  deterministic signal projection plus a masked-observation marker
Authoritative producers: source memory/tool metadata remain authoritative;
  MemoryContextBuilder and ToolEventLoopRunner own request-derived projections
Consumers: ContextAssembler, prompt diagnostics, checkpoint recovery, offline quality
Lifecycle: request-derived; memory compaction is persisted only through the existing
  context_compaction artifact binding; recovery prompt masking is ephemeral
Control impact: budget/recovery evidence only; no routing, permission, mutation,
  validation-command, or completion authority is added or removed
Existing contracts reviewed: ContextCandidate, ContextCandidateDecision,
  ContextCompactionRecord, ContextCompactionBinding, DurableArtifactReference,
  RuntimePromptContextSnapshot, ToolErrorMetadata, ToolInputMetadata
Decision: reuse all existing contracts and add one backward-compatible algorithm
  literal; add no MetadataKind or generalized relationship layer
No duplicate truth: raw sources remain unchanged, projections carry exact source
  IDs/fingerprint/checksum, and exact prompt artifacts remain replay authority
Migration: historical deterministic_dialog_extract_v1 records remain readable;
  the memory adapter fingerprint changes for newly assembled requests
Tests: deterministic masking, source immutability, atomic fallback, required-source
  rejection, old/new record recovery, corruption fail-closed, offline trajectory gates
```

## Admission gates

- Required candidates and typed control inputs are never compacted.
- A source is marked `compacted` only when its complete replacement is selected.
- Recent dialog remains a contiguous verbatim suffix.
- Source IDs, source fingerprint, artifact checksum, and replay hash remain stable.
- Corrupt artifacts fail closed.
- The offline corpus passes semantic-slot and permission-equivalence checks and
  demonstrates material bounded-growth improvement before provider admission.

## Rollback

Restore the previous memory adapter algorithm for new requests and disable the
recovery projection helper. Do not rewrite raw memory, checkpoints, or historical
compaction artifacts; their old algorithm value remains readable.

## Completion evidence

- Recovery prompts now start from an explicit safe field allowlist. `env`,
  arbitrary `attributes`, runtime handles, free-form duplicate task/context, and
  unknown fields are neither submitted nor hashed. Long system/instruction
  controls remain exact; explicit large generated/observation bodies receive a
  deterministic length and SHA-256 marker. The original typed error is unchanged.
- Compactors must be complete non-truncatable artifacts, cannot compact required
  candidates or another compactor, and are applied with a bounded iterative
  atomic fallback. A failed replacement restores its sources and preserves
  selection evidence consistency.
- Observation compaction accepts only older assistant dialog. Old user dialog is
  never labeled `compacted`; a prefix with no strictly smaller valid projection
  returns the original selection. The adapter fingerprint is
  `typed_memory_candidates_segmented_compaction_v5`.
- Historical algorithm values remain readable. A pending v4 snapshot whose hash
  does not match the v5 request now fails closed; a successful exact replay is
  consumed once so later new context requests may assemble normally.
- Offline 10/20/50-message cases used production `MemoryContextBuilder`: full
  prompts were 13,013/28,729/75,979 chars, recent-only selection was 1,600 chars,
  and segmented prompts were 814/815/815 chars. Minimum reduction versus full was
  93.74%; reduction versus recent-only was about 49.1%; 50-versus-20 growth was
  0%. Semantic, permission, required, current-failure, exact-validation, source
  lineage, stable-hash, and changed-source gates passed. Provider/network/project
  mutations were all zero.
- Verification: Stage 10 `8 passed`; complete `Code/tests` `902 passed`; compileall
  and `git diff --check` passed.

## Remaining limits

- The segmented extractor is deterministic and deliberately narrow; it does not
  claim semantic summarization quality for arbitrary assistant prose.
- Old user dialog is protected from false observation compaction, but the base
  budget selector may still omit non-required old user messages. A later phase
  should project durable user constraints into typed required candidates before
  considering broader compaction.
- Evidence is synthetic/offline and character-based. Provider-token and real-task
  admission require a separate reviewed canary.
- The historical Stage 9 frozen experiment suite has five snapshot-preflight
  failures after later Task Designer contract changes. Those immutable results
  were not silently re-frozen in this phase.
