# Phase 2 Plan: Full Request Budget Envelope — Completed

## Objective

Turn typed candidate selection into a provider-ready request boundary. Count all
model-visible message content under one provider tokenizer budget, explicitly
reserve chat-framing/safety capacity, attach selection evidence to `LLMRequest`,
and prevent `budget_insufficient` results from reaching the provider.

## Accuracy boundary

The local official tokenizer can exactly count message content but cannot prove
the provider's private chat framing, routing additions, or billing treatment.
Therefore:

- `original_prompt_tokens` and `final_prompt_tokens` remain exact counts of all
  selected message content rendered by the assembly policy;
- `reserved_prompt_tokens` is an explicit safety/framing reserve;
- `effective_content_tokens = requested_prompt_tokens - reserved_prompt_tokens`;
- provider `LLMResponse.usage` remains authoritative for the serialized request
  and billing.

No heuristic token count may be labeled exact.

## Metadata impact note

```text
Fact: one provider request consumed a bounded content allowance after an explicit framing/safety reserve
Authoritative producer: ContextAssembler produces pre-request budget evidence; provider response produces actual usage
Consumers: ContextRequestBuilder, LLMClient guard, diagnostics, checkpoint/replay hashing
Lifecycle: request evidence, checkpoint/replay identity, response evidence
Control impact: budget and recovery
Existing contracts reviewed: ContextAssemblyPolicy, ContextSelectionMetadata, LLMRequest,
  LLMRequestMetadata, LLMResponseMetadata, PendingLLMRequest, RuntimePromptContextSnapshot
Decision: extend existing policy/selection/request metadata; add one runtime prepared-request value without MetadataKind
Why no duplicate source of truth is created: pre-request selection and post-request provider usage remain distinct owners;
  LLMRequest carries the existing selection object rather than copying its fields into trace_info
Serialization and migration: optional fields/defaults preserve historical LLMRequest and Metadata reads;
  request hashes intentionally include attached selection evidence for newly assembled requests
Tests: reserve arithmetic, unavailable-tokenizer fallback, insufficient guard, message preservation,
  request hash/replay identity, diagnostics propagation, full regression
Documentation updates: API, catalog, test guide, implementation log, phase plan
```

## Implementation slices

1. Extend `ContextAssemblyPolicy` and `ContextSelectionMetadata` with requested,
   reserved, effective, and remaining prompt token evidence.
2. Add `ContextRequestBuilder` under `memory.context_assembly`; it converts
   selected typed candidates into role-preserving `LLMMessage` values and returns
   either a ready `LLMRequest` or a typed insufficient result.
3. Add optional `context_selection` to `LLMRequest` and `LLMRequestMetadata`.
4. Make the provider client reject a request whose typed assembly status is not
   `ready`; do not branch on messages or error text.
5. Propagate the same selection object through diagnostics and request hashing.

## TDD sequence

1. Metadata arithmetic and invalid-combination tests.
2. Request-builder role/order, reserve, fallback, and insufficient tests.
3. Provider-client guard and diagnostics/replay hash tests.
4. Implementation, targeted recovery tests, then full regression.

## Exit gate

- Ready assembled requests contain all selected model-visible messages and one
  attached `ContextSelectionMetadata` record.
- Exact final content tokens plus reserve never exceed the requested prompt
  budget when a provider tokenizer is available.
- Missing tokenizer produces explicit character mode and no false token fields.
- Insufficient required context cannot call the provider.
- Historical unassembled `LLMRequest` remains readable during phased migration.

## Rollback

Remove the optional request evidence and request builder together; preserve the
typed candidate API and legacy memory adapter. Never weaken the provider guard
by interpreting a reason string.

## Completion evidence

- Added explicit requested/reserved/effective/remaining prompt token evidence.
- Added `ContextRequestBuilder`, preserving selected candidate roles in
  `LLMMessage` and attaching the existing selection record to `LLMRequest`.
- `LLMClient` rejects typed `budget_insufficient` requests before cache or
  transport; diagnostics and replay hashes include selection evidence.
- Missing tokenizer remains explicit character mode with no token-envelope
  evidence.
- Budget, request, diagnostics, checkpoint, and compatibility regressions:
  `273 passed`; `Code` full suite: `624 passed`.
- Phase 2 exit gate passed.
