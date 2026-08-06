# Phase 11 Stage 6C Plan: Recorded Rolling-Summary Replay

## Status

Completed offline. Replay is entirely offline; it does not construct a
Provider client or call transport.

## Objective

Turn one shadow response into a versioned, source-bound artifact and prove that
the same response produces the same acceptance/fallback decision without
network access. Replay is an evidence check, not a second authority for
context or task state.

## Artifact contract

- Capture the Stage 6A manifest, immutable shadow request, structured response
  payload, and existing `ProviderAttemptReceipt`.
- Persist endpoint identity only (credential-free); never persist API keys or
  raw transport headers.
- Require a transported response with a structured payload. Pre-transport
  blocks and provider exceptions remain terminal receipts, not replayable
  summary artifacts.
- Bind the artifact to the original request hash, manifest hash, source
  fingerprint, candidate IDs, and summary schema/adapter versions.

## Replay gates

- Verify manifest hash and recomputed request hash before interpreting output.
- Use `replay_receipt` to mark no-transport replay and link it to the original
  attempt; never mark replay as a new Provider call.
- Re-run `RollingSummaryAdapter` with the captured payload and original usage/
  finish evidence. Compare acceptance, fallback reason, and summary record
  identity to the captured observation.
- Any tampering, schema/version mismatch, or outcome drift stops replay rather
  than silently falling back to a different source.

## Metadata impact note

```text
Fact: recorded replay of one provider shadow response
Authoritative producer: original shadow receipt and source-bound manifest
Consumers: paired canary gate and experiment report only
Lifecycle: immutable experiment artifact; no task/permission/context authority
Existing contracts reused: Stage 6A manifest, RollingSummaryShadowRequest,
  ProviderAttemptReceipt, replay_receipt, RollingSummaryAdapter
Decision: add one narrow replay artifact and validator; no new production
  MetadataKind and no second source of truth for summary content
Serialization: versioned strict JSON; credential-free endpoint and nullable usage
Tests: exact accepted replay, exact fallback replay, tamper, no-transport,
  and non-replayable attempt gates
```

## Exit gate

- Accepted and fallback responses replay to the same decision and summary ID.
- Replayed receipt is linked to the source attempt and proves no transport.
- Tampered source/manifest/request is rejected fail-closed.
- Offline replay tests perform zero network, project writes, memory writes, or
  Prompt mutation.

## Evidence

- Added `stage19_recorded_replay.py` with a strict, credential-free
  `RecordedShadowArtifact` and exact replay validator.
- Captured artifacts bind the Stage 6A manifest, source-bound shadow request,
  structured response payload, and existing attempt receipt. Pre-transport and
  provider-exception attempts are explicitly non-replayable.
- Replayed receipts use the existing `replay_receipt` path and preserve source
  attempt linkage, request hash, usage state, finish reason, and response hash.
- Accepted and unknown-usage fallback fixtures replay exactly; tampered source
  text fails request-hash validation before summary interpretation.
- Stage 6A/6B/6C/telemetry/summary focused set: **74 passed**. No network,
  Provider client, Prompt mutation, project write, or memory write occurred.
