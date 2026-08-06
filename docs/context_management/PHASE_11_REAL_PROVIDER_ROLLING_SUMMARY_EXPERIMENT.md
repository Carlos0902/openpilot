# Phase 11: Real-Provider Rolling Summary Experiment

## Status

Stages 6A–6E are complete. The final three-call real-provider shadow is
transport-valid but Compact-quality blocked by reasoning consuming the entire
summary completion ceiling. No production feature flag is enabled, and the
next work should be an independent reasoning/completion allocation experiment.

## Objective

Measure whether a validated LLM rolling summary reduces downstream context cost
on the full architecture while preserving required state, provenance,
verification, permissions, and task quality.

## Immutable experimental rules

- Current is deterministic segmented compaction with no summary Provider call.
- Treatment uses only an explicitly configured Provider capability profile and
  the injected rolling-summary boundary.
- Both arms share one immutable raw source envelope, project snapshot,
  session-turn hash, session-constraint hash, task input, and completion policy.
- Compact, reasoning, completion, and constraint extraction flags remain fixed
  across a pair; only the rolling-summary arm differs.
- Unknown usage, incomplete finish evidence, stale source, invalid schema,
  over-budget output, or artifact failure never enters Treatment context.
- Every Provider attempt, retry, repair, timeout, fallback, and cache decision is
  recorded. Unknown usage is nullable/visible, never zero.

## Stage order

### Stage 6A — Readiness and contracts

Freeze the explicit profile, endpoint identity, model, tokenizer, budget policy,
summary schema, attempt evidence, experiment manifest, and kill switch. Run all
checks without Provider traffic.

### Stage 6B — Shadow Provider calls

Call the configured Provider with the eligible old segment, previous validated
summary, purpose, and strict schema, but do not use the result in the Prompt.
Validate output, usage, finish reason, budget, source lineage, and fallback.

### Stage 6C — Recorded replay

Replay captured responses offline and verify exact source/provenance/constraint
behavior, checkpoint recovery, deterministic fallback, and no mutation.

### Stage 6D — Small paired canary

Run interleaved Current/Treatment pairs across the first three purposes with
read-only or low-risk tasks. Keep the global default and kill switch unchanged.

### Stage 6E — Decision and expansion

Expand only if quality, safety, provenance, verification, and mutation gates
pass. A token reduction alone cannot switch the global default.

## Primary measurements

- summary input/output/reasoning/total tokens and compression ratio;
- downstream Prompt input/output/reasoning/total tokens;
- Provider call count, retries, latency, cache and fallback rates;
- required-candidate retention, source lineage, replay exactness;
- task/schema quality, verification success, mutation drift, and suspicious
  success;
- unknown-usage and stop/fallback reason coverage.

## Rollback

Set the rolling-summary flag off. Current deterministic context remains the only
model-facing projection. Delete or quarantine Treatment artifacts; raw dialog,
task contracts, and checkpoint sources remain authoritative.
