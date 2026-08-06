# Phase 10 Stage 5 Plan: Independent Context Governance Canary

## Status

Plan and offline acceptance gate complete. No global default or real-provider
canary is enabled by this document.

## Objective

Measure the context governance changes without mixing their causes. Compact,
session-constraint extraction, and reasoning each get an independent paired
arm with its own kill switch and evidence envelope.

## Protocol

1. Freeze one immutable raw ingress/session/source envelope per pair.
2. Interleave Current and Treatment by task and purpose; do not run all Current
   first or reuse mutated project state.
3. Keep summary, constraint, and reasoning flags independently switchable.
4. Account for every provider attempt, including retries, empty responses,
   capped responses, fallback attempts, and unknown usage.
5. Run offline gates before any real-provider traffic; real traffic is limited to
   previously passing task families and a small feature-flagged canary.

## Measurements

- input, output, reasoning, total, and unknown token usage;
- provider call count, fallback/stop reason, and latency;
- context-selection quality, required-state/provenance preservation, and replay;
- constraint precision/recall, activation/revocation correctness, and runtime
  verification success;
- mutation drift, permission violations, environment/preflight status, and
  task/schema correctness.

## Acceptance gates

- No required constraint, safety field, current task fact, or verification plan
  disappears in Treatment.
- Invalid summary or reasoning controls fall back to the typed Current path.
- Unknown usage is visible and never silently counted as zero.
- Treatment does not increase unauthorized mutations or suspicious-success
  outcomes.
- A token/call reduction is reported only with matching quality and evidence
  gates; one provider or one fixture cannot switch the global default.

## Metadata impact note

```text
Fact: paired treatment result and governance evidence envelope
Authoritative producer: experiment runner from immutable source/checkpoint data
Consumers: offline analysis, trajectory audit, rollout decision
Lifecycle: experiment artifact; never runtime authority
Control impact: rollout only
Existing contracts reviewed: ContextSelectionMetadata, ContextCompactionBinding,
  SessionIngressState, ReasoningResolution, attempt usage/evidence metadata
Decision: reuse existing telemetry and add only missing typed evidence fields;
  no production source-of-truth duplication
```

## Exit gate

Each arm has reproducible artifacts and an explicit GO, CONDITIONAL, or NO-GO
decision. Global defaults remain unchanged unless all relevant quality and
safety gates pass across the intended task families.
