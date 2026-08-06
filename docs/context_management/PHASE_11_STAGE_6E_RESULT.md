# Phase 11 Stage 6E Result: Real-Provider Rolling Summary

## Decision

The experiment is **transport-valid but Compact-quality blocked**. The real
Provider was reached under an explicit, in-process
`generic-openai-compatible:v1` profile, but all three bounded shadow calls
were truncated before a structured summary was returned. No Treatment summary
entered a Prompt, no paired canary was admitted, and no global default changed.

## Offline evidence

- Code full suite: **1040 passed**.
- Stage 6A–6E focused readiness/shadow/replay/paired/final set: **88 passed**
  after the final attempt-telemetry fix.
- `compileall` and `git diff --check`: passed.
- No production default, reasoning policy, completion policy, constraint policy,
  project file, or memory record changed.

## Readiness and traffic

- Provider endpoint identity: `https://api.deepseek.com` (credential-free).
- Model: `deepseek-v4-flash`.
- Profile: explicit `generic-openai-compatible:v1`, supplied only to the
  in-process experiment settings; no environment/config file was changed.
- Tokenizer: `deepseek-official-api-tokenizer`, available locally.
- Flags: Treatment enabled for the bounded run, kill switch armed, read-only.
- Calls: 3, one each for `context_compaction`, `goal_plan`, and
  `tool_event_decision`.

## Attempt evidence

| purpose | input | output | total | reasoning | finish | result |
|---|---:|---:|---:|---:|---|---|
| context_compaction | 495 | 128 | 623 | 128 | `length` | invalid/truncated |
| goal_plan | 506 | 128 | 634 | 128 | `length` | invalid/truncated |
| tool_event_decision | 503 | 128 | 631 | 128 | `length` | invalid/truncated |

All three attempts were `InvalidLLMResponseError`, validation category,
non-recoverable, with `retry_recommended=false`. Usage is complete and
reconciles; unknown-usage count for the authoritative run is zero. The first
identical 3-call run is discarded as evidence because the report projection
did not yet expose error fields; the receipt telemetry was fixed before this
authoritative rerun.

## Root cause and boundary

The 128-token summary ceiling was entirely consumed by provider-default
reasoning (`reasoning_tokens=128`). The provider stopped at `length`, so the
JSON summary contract never reached the rolling adapter. This is a reasoning/
completion allocation signal, not evidence that segmented Compact semantics
are bad or that LLM summaries should be enabled globally.

The Phase 12 experiment isolated reasoning: use an explicit provider profile
that is known to support a non-thinking/disabled mode, or allocate a separate
reasoning reserve and summary completion ceiling. It must not silently infer
capabilities from the model name or change the Compact experiment's result.

The follow-up evidence is recorded in
`docs/context_management/PHASE_12_REASONING_STRATEGY_RESULT.md`.

## Rollout decision

`global_default_changed=false`. Keep deterministic Current context as the only
production model-facing projection. Do not run a paired Treatment canary until
one independent reasoning/completion experiment produces complete structured
summary responses and replayable artifacts.
