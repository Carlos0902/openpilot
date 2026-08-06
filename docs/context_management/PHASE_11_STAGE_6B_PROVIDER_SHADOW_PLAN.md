# Phase 11 Stage 6B Plan: Provider-Neutral Rolling-Summary Shadow

## Status

Completed offline. The shadow caller may use an explicitly admitted Provider
only after this contract and its offline tests pass. Its response is never
inserted into the model-facing Prompt.

## Objective

Measure provider attempt cost and output validity against the same immutable
source segment used by Current, without allowing an untrusted summary to alter
runtime behavior. This separates transport/usage evidence from semantic
quality and keeps Compact, reasoning, completion, and constraints unchanged.

## Caller boundary

- Input is a source-bound Stage 6A manifest plus an immutable old-segment
  snapshot, previous validated-summary fingerprint, purpose, and dynamic
  summary budget.
- Request is provider-neutral: strict JSON response format, bounded schema,
  source IDs in the prompt, no tool or mutation authority, explicit profile
  resolved outside the business payload.
- A transport callable is injected. The real implementation may delegate to
  `LLMClient.complete`; offline tests use a fake response and make zero network
  calls.
- Every attempt is normalized to the existing `ProviderAttemptReceipt`,
  retaining input/output/total/reasoning usage, finish reason, response hash,
  request hash, retry/repair and error fields. Unknown values remain unknown.
- The validated result is returned as an observation only. The caller marks
  `used_in_prompt=false`; the production `MemoryContextBuilder` is not called
  with the result in this stage.

## Fail-closed gates

- No treatment manifest or zero dynamic summary budget: pre-transport block.
- Missing/partial usage, missing/unknown finish reason, stale source, invalid
  schema, over-budget output, or no compression gain: deterministic Current
  fallback; never a Provider-derived context candidate.
- Provider exceptions produce a failed receipt and a typed fallback; they do
  not trigger a broad retry or a different task/purpose.
- Response artifacts contain structured payload and hashes only; no API key,
  raw credential, or unrelated project mutation is persisted.

## Metadata impact note

```text
Fact: one provider shadow attempt and its unconsumed rolling-summary result
Authoritative producer: experiment-owned shadow caller plus existing LLMResponse
  and ProviderAttemptReceipt projection
Consumers: recorded replay, paired canary analysis, audit report
Lifecycle: experiment observation; not task, permission, or context authority
Control impact: provider call accounting and fallback admission only
Existing contracts reused: Stage 6A manifest, RollingSummaryAdapter,
  ProviderAttemptReceipt, LLMRequest/LLMResponse, ContextCompactionRecord
Decision: add a narrow experiment-owned observation wrapper; do not add a
  production MetadataKind or duplicate summary/source authority
Serialization: versioned JSON with hashes and nullable usage; unknown fields fail closed
Tests: fake response, partial usage, truncation, stale source, zero budget,
  exception, request hash, and no-Prompt-use gates
```

## Exit gate

- Offline fake transport tests pass with zero network and no Prompt mutation.
- A real Provider path is admitted only from a Stage 6A treatment manifest.
- Each attempt has a settleable or explicitly unknown usage receipt and a
  typed fallback/acceptance result.
- Shadow outputs are available for Stage 6C replay but cannot affect Current or
  Treatment task execution.

## Evidence

- Added `stage18_provider_shadow.py` with a validated immutable source request,
  provider-neutral JSON request, zero-budget/pre-transport gate, injected
  transport boundary, and observation-only result.
- Reused `RollingSummaryAdapter` and the existing `ProviderAttemptReceipt`;
  response usage, reasoning details, finish reason, request/response hashes,
  and provider endpoint identity remain available for later analysis.
- Added five offline tests covering accepted-but-unused output, partial usage,
  truncation, stale source, zero budget, transport exception, and source-size
  validation.
- Focused readiness/shadow/telemetry/compaction set: **75 passed**. No HTTP
  request, `LLMClient` construction, Prompt mutation, project write, or memory
  write was performed.
