# Phase 10 Stage 5 Offline Acceptance Report

## Scope

This is the deterministic, no-provider gate for the three independent context
governance changes. It does not authorize a real-provider canary or change any
global default.

## Evidence

Command:

```text
PYTHONPATH=Code/src pytest -q Code/tests/test_context_governance_stage5_acceptance.py
```

Result: **1 passed**.

The test uses one active, source-linked write-scope constraint and the same raw
assistant history in Current and Treatment fixtures. It verifies:

- Current (`rolling_summary_enabled=False`) remains deterministic;
- Treatment invokes only the injected summary boundary, rejects unknown usage,
  and persists the deterministic fallback;
- both arms retain the required active constraint and its file scope in the
  model-facing projection;
- reasoning profile selection is independent: an unconfigured model uses the
  generic no-control profile while an explicit profile opts into a versioned
  capability profile;
- no Provider/network or project-state mutation is needed.

The broader regression command also passed during this stage:

```text
PYTHONPATH=Code/src pytest -q Code/tests  # 1040 passed
```

## Decision

Offline gates are green. This supports a small, separately instrumented canary
only after real-provider usage/finish evidence, quality, permission, mutation,
and replay gates are wired. It does not support switching the global Compact or
reasoning defaults.

## Remaining limitations

- The test does not claim semantic quality of a real LLM summary.
- Agent Generator still needs a production ingress decision or explicit
  fail-closed boundary.
- API/acceptance constraints still need independent verification evidence.
- Native Anthropic/Gemini transports are not implemented.
