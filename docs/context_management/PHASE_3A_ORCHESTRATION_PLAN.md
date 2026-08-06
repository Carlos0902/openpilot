# Phase 3A Plan: Orchestration and Control Entries — Completed

## Scope

Migrate these request purposes:

1. semantic goal analysis;
2. semantic plan-step analysis;
3. tool-event decision planning;
4. empty-plan retry;
5. task complexity estimation;
6. task decomposition;
7. iteration goal making;
8. iteration task design;
9. project improvement analysis;
10. runtime-system-output evaluation.

## Metadata impact note

```text
Fact: one typed business purpose selected and submitted one bounded set of messages
Authoritative producer: each business owner selects purpose and message policy; ContextRequestBuilder selects content
Consumers: LLMClient, diagnostics, replay/checkpoint, business response parser
Lifecycle: request and response evidence
Control impact: routing, budget, recovery
Existing contracts reviewed: ContextAssemblyPolicy, ContextSelectionMetadata,
  LLMRequestMetadata, PendingLLMRequest, ToolDecisionMetadata, Goal/Task models
Decision: add ContextRequestPurpose enum and extend owned selection/policy values; reuse all domain result contracts
Why no duplicate source of truth is created: purpose identifies the call; domain results remain owned by their agents
Serialization and migration: optional/default purpose preserves historical selection reads
Tests: purpose round-trip, message adapter, each owner receives attached ready evidence,
  fallback and parser behavior, request hash/replay, full regression
Documentation updates: API, catalog, testing guide, implementation log, this plan
```

## TDD and implementation order

1. Add purpose enum, migration registry, and message-adapter tests.
2. Implement the shared adapter without changing owners.
3. Migrate semantic and decomposition calls; run their tests.
4. Migrate tool planning and iteration calls; run pipeline/recovery tests.
5. Migrate project improvement and runtime-output evaluation; run evaluator tests.
6. Run static coverage, all phase-3A targeted tests, then full suite.

## Owner-specific policies

- System contracts: required, priority 100, truncation forbidden.
- User task/plan payloads: required, priority 90, head truncation unless the
  owner explicitly needs tail evidence.
- Runtime stdout/stderr evidence: required, tail truncation.
- Existing deterministic fallbacks and retry limits remain unchanged.
- Output `max_tokens` is not reused as an input budget.

## Exit gate

All ten purposes attach ready selection evidence for normal inputs; typed
budget insufficiency follows the owner's existing fallback/error path; no
direct request construction remains in these owner functions.

## Completion evidence

- Added typed purpose registry covering all 22 phase-0 purposes.
- Migrated all ten phase-3A purposes through `build_context_llm_request`.
- Static entry coverage and semantic runtime evidence: `10 passed`.
- Owner parser/fallback/planning/iteration/evaluator regressions: `240 passed`.
- `Code` full suite: `637 passed`.
- Phase 3A exit gate passed.
