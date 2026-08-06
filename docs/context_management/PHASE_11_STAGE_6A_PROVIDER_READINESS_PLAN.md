# Phase 11 Stage 6A Plan: Provider Readiness and Experiment Contracts

## Status

Completed offline. Plan was written before implementation; no Provider or
network call was made, and Treatment remains disabled by default.

## Objective

Create a fail-closed, reproducible readiness boundary for one explicit real
Provider profile and the rolling-summary experiment. This stage must not call
the Provider or enable Treatment.

## Readiness contract

The readiness result must be typed and include:

- provider and credential-free endpoint identity;
- model and explicit capability profile/version;
- tokenizer identity/availability;
- summary schema version and static token cap;
- dynamic budget reservation policy;
- feature flag and kill-switch state;
- project/session/source mode and mutation policy;
- a typed list of blockers.

Readiness fails closed when API settings are missing, the profile is absent or
unknown, tokenizer/budget inputs are invalid, or the experiment would permit a
write path before canary admission.

## Experiment manifest

The manifest is an experiment artifact, not runtime authority. It binds:

- experiment/arm IDs and immutable source envelope hash;
- session-turn and session-constraint hashes;
- provider/profile/model/endpoint identity;
- context, summary, completion, and reasoning policy versions;
- requested limits and kill-switch state.

## Tests first

- ready and blocked configurations;
- endpoint identity excludes credentials but preserves meaningful ports;
- explicit profile/version is required;
- invalid budget and schema versions fail closed;
- Current/Treatment flags are independent;
- manifest serialization and hash stability;
- zero Provider/network/project/memory mutation.

## Metadata impact note

```text
Fact: one real-provider rolling-summary experiment readiness assessment
Authoritative producer: experiment harness from typed LLM settings and source envelope
Consumers: shadow runner, replay runner, canary admission, audit report
Lifecycle: experiment artifact; never task or permission authority
Control impact: rollout admission and budget accounting only
Existing contracts reviewed: LLMSettings, ReasoningCapabilityProfileId,
  ContextCompactionRecord, ContextCompactionBinding, ContextSelectionMetadata,
  SessionIngressState, RuntimeBudgetMetadata
Decision: add a strict experiment-owned nested contract and reuse existing
  runtime/source hashes; do not create a new production MetadataKind
Why no duplicate source of truth is created: raw context, settings, and runtime
  contracts remain authoritative; the manifest records their identities only
Serialization and migration: versioned JSON artifact; unknown/invalid fields fail closed
Tests: readiness, manifest, hash, flags, blocker, and zero-side-effect gates
Documentation updates: Phase 11 master plan, this stage plan, implementation log
```

## Exit gate

- Readiness and manifest contracts pass offline tests.
- A missing/invalid Provider setup produces a typed blocker.
- No Provider/network call occurs.
- Treatment remains disabled by default.

## Evidence

- Added `stage17_real_provider_readiness.py` with strict experiment-owned
  readiness, budget, flag, and source-bound manifest models.
- Readiness strips credentials from endpoint identity, requires an explicit
  versioned reasoning profile and exact tokenizer only for Treatment, and
  rejects non-read-only or unarmed Treatment.
- Reuses the existing `ProviderAttemptReceipt` contract version and
  `calculate_summary_budget`; no new production `MetadataKind` was added.
- `PYTHONPATH=Code/src:experiments/full_architecture_context_observation
  pytest -q experiments/full_architecture_context_observation/test_stage17_real_provider_readiness.py
  Code/tests/test_compaction_summary_contract.py Code/tests/test_rolling_compaction.py
  Code/tests/test_reasoning_policy.py Code/tests/test_token_counting.py`:
  **79 passed**.
- No `LLMClient`, HTTP transport, project mutation, memory write, or Provider
  credential was used.
