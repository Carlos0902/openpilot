# Metadata impact note

The Stage 7B experiment adds no new runtime metadata model, routing value,
permission value, or persistence authority. Stage 7C does make a bounded producer
change inside the existing diagnostic `trace_info`/`provider_details` escape hatches:
request/response/failure events now carry a credential-free request hash, ordinal,
attempt ID, normalized endpoint, and transport-attempted flag. These values are
audit links, not runtime control fields, and remain readable by the existing
`LLMRequestMetadata`/`LLMResponseMetadata` envelopes.

It reuses the authoritative `LLMRequestMetadata`, `LLMResponseMetadata`, and
`ContextSelectionMetadata` evidence already emitted by the production runtime.
The run harness adds an external call/token safety guard and writes derived
analysis outside the production diagnostics directory. The diagnostic additions
are backward-readable free-form audit data; no migration is required. `API.md`
documents the new evidence boundary.

The Stage 6E bridge adds the owned nested `metadata.DerivedContextProjection`
value and the selected-only `MemoryContextBuilder` compatibility view. It does
not introduce a new `MetadataKind`, persistence authority, or source store: the
projection reuses existing `ContextCandidate`, `ContextSelectionMetadata`, and
`ContextCompactionBinding` values, while raw ingress remains authoritative.
Historical context payloads without the selected-candidate field remain readable
through the compatibility path; new session-ingress paths validate request,
turn-ledger, constraint, selection-status, and compaction-binding identity before
passing the view to analyzer/Goal/Task adapters.

The Stage 7B-3b gate hardens only experiment-owned evidence.
`OfflineContextLineageReceipt` is a typed derived view of the existing source
snapshot, ingress turn digest, active-constraint digest, read-only ContextLoader
mode, and write-scope digest; it is not a new runtime authority or permission
owner. `CompactFallbackReceipt` now carries the same lineage fields, so fallback
evidence cannot claim shared context from a counter or free-form reason alone.
The Stage 12 preflight output also echoes the locked feature-flag and kill-switch
values for Stage 15 to compare before any reservation. No Provider transport or
production default is enabled by these experiment changes.

Stage 7E-1 adds the metadata-owned `ReasoningDecisionComplexity` enum as a
typed request-owner input. It does not add a new metadata kind or persistence
authority. The enum is deliberately distinct from
`EnhancementCompletionComplexity`, so a reasoning A/B can freeze the same
completion reservation. `core/reasoning.py` maps it to provider-neutral
`ReasoningPolicy`; provider capability profiles remain the only model/provider
adaptation boundary.
