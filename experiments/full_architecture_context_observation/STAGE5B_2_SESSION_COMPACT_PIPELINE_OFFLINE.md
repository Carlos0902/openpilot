# Stage 5B-2: Session compact pipeline offline probe

## Decision boundary

This is a zero-provider ingress/projection sentinel, not a paid canary and not
evidence for the complete conversation architecture. It invokes the production
`AutonomousIterationAgent` Goal Maker and Task Designer boundaries with one
immutable `SessionIngressState`-derived source snapshot. It does not invoke
`IntelligentAutopilot.execute()` or `AgentRuntimeController`; those entry and
checkpoint contracts remain covered by the Code contract tests and must be
exercised by the later provider runner.

The probe does not treat unknown Provider usage as zero. It records exact local
rendered-prompt tokens as `rendered_input_tokens`, while Provider input/output/
total fields remain `null` and `usage_observed=false` because no request is
transported.

## Current result

Command:

```bash
PYTHONPATH=Code/src:experiments/full_architecture_context_observation \
  python -m pytest -q \
  experiments/full_architecture_context_observation/test_stage13_session_compact_pipeline_offline.py
```

Result: **5 passed**. The probe observed zero Provider calls, zero network
calls, and zero project mutations. Both arms recalled all three confirmed
session constraints (`constraint_recall=1.0`) and used one source snapshot.

| Purpose | Current rendered input | Compact rendered input |
| --- | ---: | ---: |
| Goal Maker | 1,678 | 1,678 |
| Task Designer | 1,717 | 1,347 |

The Task Designer reduction is 370 rendered tokens (21.6%) in this deterministic
fixture. It is a mechanism signal only; it is not Provider usage or a quality
claim.

## Controls tested

- feature flag disabled: stop before request assembly;
- runtime kill switch engaged: stop before request assembly;
- compact assembly failure: execute one separately recorded current fallback;
- injected Provider transport: reject the probe;
- no downstream task execution, mutation, memory write, or budget mutation.

## Why this is not yet Stage 5B real canary evidence

`SessionIngressState` is passed into the Goal Maker and Task Designer calls, but
the probe calls those agent boundaries directly. It does not yet route through
`IntelligentAutopilot.execute()` → `AgentRuntimeController` →
`ProjectImprovementRuntime`, and the current production Context Loader does not
hydrate raw `SessionTurn` values into `ShortMemory`. Therefore the claim is
limited to typed session-constraint projection at the two improvement-agent
boundaries. The real runner must add this production entry path before any
Provider transport and must fail closed on unknown usage rather than allowing
`run_observation.analyze()`'s missing-usage zeros into a canary total.

Stage 9 V1/V2 frozen artifacts are not modified or used as the source of this
probe.
