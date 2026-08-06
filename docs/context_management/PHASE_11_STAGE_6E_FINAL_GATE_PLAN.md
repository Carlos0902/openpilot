# Phase 11 Stage 6E Plan: Final Regression and Real-Provider Decision

## Status

Completed. The final offline and bounded real-provider checks ran under the
rules below. No rollout was admitted.

## Objective

Run the complete offline regression and inspect the actual local Provider
configuration. If the Stage 6A treatment readiness is green, run only the
bounded three-purpose shadow/pair campaign with explicit transport opt-in. If
readiness is blocked, do not guess, install, or contact a Provider; record the
typed blockers and close the experiment as offline-ready but real-traffic
blocked.

## Final checks

- `Code/tests` full suite, experiment focused suite, compileall, and
  `git diff --check` must pass.
- Readiness must be evaluated from current settings without printing a key;
  endpoint identity remains credential-free.
- Real traffic, if admitted, is capped to three low-risk shadow calls (one per
  purpose), uses the Stage 6A manifest and Stage 6B caller, records every
  attempt, and does not execute project mutations or use the returned summary
  in task Prompt state.
- Any missing credential/profile/tokenizer, unknown usage, exception, or
  non-replayable output stops expansion. Do not silently install a tokenizer or
  change a feature flag as part of this stage.

## Decision boundary

The final report must separate:

1. offline contract and safety result;
2. whether real Provider transport was attempted;
3. observed provider usage/finish/reasoning and summary acceptance;
4. paired token/call/fallback metrics;
5. required/provenance/verification/quality/mutation result; and
6. rollout decision (always `global_default_changed=false` in this goal).

## Metadata impact note

```text
Fact: final real-provider rolling-summary experiment decision
Authoritative producer: stage-specific artifacts and full regression evidence
Consumers: engineering rollout decision and next experiment plan
Lifecycle: immutable experiment report; no runtime/task authority
Existing contracts reused: readiness/manifest, shadow, replay, paired canary,
  ProviderAttemptReceipt, existing context/constraint/completion/reasoning contracts
Decision: add a report/runner only if real settings are admitted; no new
  production MetadataKind and no production-default mutation
Serialization: versioned report with typed blockers and nullable usage
Tests: full offline suite, final readiness, bounded traffic or explicit blocker
```

## Exit gate

- All required offline tests and static checks pass.
- A real traffic result is either bounded and fully evidenced or explicitly
  blocked by typed readiness reasons.
- The final report states what was measured and what remains unknown; no token
  reduction is promoted to a global policy without a later approved rollout.

## Evidence

See `docs/context_management/PHASE_11_STAGE_6E_RESULT.md`. The authoritative
three-call run had complete usage and finish evidence, but all responses hit
`length` with reasoning consuming the entire 128-token output ceiling. The
experiment therefore ends with a typed, reproducible Compact-quality block;
reasoning/completion allocation is the next independent experiment.
