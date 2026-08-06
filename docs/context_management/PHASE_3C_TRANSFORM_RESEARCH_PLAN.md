# Phase 3C Plan: Transform, Research, and Agent-Definition Entries — Completed

## Scope

Migrate seven request purposes:

1. memory conversation compression;
2. general text summarization;
3. web query generation;
4. web redirect/link selection;
5. web result cleanup;
6. agent slot generation;
7. slot-language repair.

## Boundary rules

- Summarization/compression input may use explicit head truncation because the
  output is already a lossy projection; it must not recursively invoke itself
  to repair input overflow.
- Web selection/cleanup uses only tool-bounded result/page excerpts and keeps
  source URLs/IDs in the owner payload.
- Slot repair preserves name, kind, and required fields; assembly does not
  reinterpret the generated slot contract.
- Existing retry, fallback, network, and response parsing behavior remains
  owned by each tool.

## Metadata impact note

```text
Fact: a transform/research/agent-definition call submitted a bounded typed request for one known purpose
Authoritative producer: each tool owns source results/text/slots; ContextRequestBuilder owns selection evidence
Consumers: summary parser, web tool, slot validator, diagnostics and replay
Lifecycle: request/response evidence and existing tool result lifecycle
Control impact: budget and recovery
Existing contracts reviewed: ContextRequestPurpose, ContextSelectionMetadata,
  SearchArtifactMetadata, TextArtifactMetadata, ToolResultMetadata, Slot models
Decision: reuse existing assembly and domain contracts
Why no duplicate source of truth is created: assembly carries projections only; tool/domain artifacts retain source facts
Serialization and migration: no schema additions in this batch
Tests: static entry coverage, purpose evidence, existing retry/fallback/parser behavior, full regression
Documentation updates: API, testing guide, implementation log, this plan
```

## TDD order

1. Add static coverage for all seven owner functions and one runtime purpose
   assertion for summarization or slot generation.
2. Migrate compressor and summarizer.
3. Migrate web query/link/cleanup paths.
4. Migrate slot generation and repair.
5. Run transform/research/agent tests, static phase-0 coverage, then full suite.

## Exit gate

- All 22 phase-0 purposes are migrated.
- Direct production `LLMRequest` construction remains only in the shared builder
  and documented transport/health test code.
- Existing source limits and fallbacks remain effective.
- Targeted and full suites pass.

## Completion evidence

- Migrated all seven transform/research/agent-definition purposes.
- Repository search finds executable `LLMRequest(...)` construction only in
  `memory.context_assembly.request_builder`.
- Static purpose coverage: `24 passed`.
- Transform, web, slot, memory, tool IO, and diagnostics regressions:
  `191 passed`.
- Diagnostics proxy now adapts optional completion kwargs by signature and
  safely observes minimal compatibility responses without a duplicate call.
- `Code` full suite: `651 passed`.
- Phase 3C exit gate passed.
