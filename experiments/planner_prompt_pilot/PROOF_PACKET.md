# Planner prompt slimming pilot proof packet

Status: `mechanism_canary_passed` (experiment-only; not eligible for a production default)

## Change definition and hypothesis

The treatment removes the planner's fixed create/add/modify/delete tutorial and
other repeated process instructions while retaining the decision-needs JSON
contract, task/goal, projected evidence, session constraints, and Router/Guard
authority language. The hypothesis is that runtime-enforced boundaries make the
tutorial partly redundant, so a shorter prompt can preserve legal planning while
reducing input size.

## Frozen inputs

- Manifest: [`manifest.json`](manifest.json)
- Corpus: [`corpus.json`](corpus.json), 30 cases across the plan's §6.2 shapes
- Arms: `control` and `treatment`, rendered by [`prompts.py`](prompts.py)
- Repeats: 3 per task and arm (180 rows)
- Evaluator/runner: [`evaluator.py`](evaluator.py), [`runner.py`](runner.py)
- Acceptance registry: [`acceptance.py`](acceptance.py); every corpus acceptance
  criterion is registered and evaluated fail-closed with typed evidence.
- Production default: disabled; no metadata, permission, tool, budget, or validation behavior is changed

## Authority and safety audit

The pilot is a pure offline renderer/evaluator. It does not execute tools,
mutate files, or decide scope. Router, Guard, read/write scope, mutation
approval, exact validation, evidence, recovery, and completion remain runtime
authorities. Missing, malformed, stopped, and provider-failure responses are
recorded as failures. A model response cannot mark execution successful.

## Observed evidence

The offline runner generated 180 rows. Rendered prompt totals were 223,827
characters for control and 103,947 for treatment. Focused validation passed:

```text
PYTHONPATH=.:Code/src pytest -q experiments/planner_prompt_pilot/test_pilot_harness.py
15 passed
PYTHONPATH=.:Code/src pytest -q Code/tests/test_session_constraint_prompt_projection.py Code/tests/test_execution_tool_planning_executor.py
109 passed
```

The size reduction is an offline mechanism signal only. No provider responses
were supplied, so task success, independent validation, security, retry,
latency, and token-usage quality metrics are not estimated.

`analysis.py` performs the required task-clustered paired accounting. On the
default no-response run it reports `inconclusive_no_provider_pairs` with 90
unknown pairs and explicitly keeps non-inferiority unestimated. It also avoids
promoting an all-failure recording set to a quality result.

The no-response run intentionally yields 180 malformed failures. It is a
runner-integrity check, not a quality estimate: missing recordings cannot pass
any fixture acceptance criterion, and the output retains the global dimensions
for legal plan, required need, fabricated path, Router/Guard, validation,
false-success, malformed, and provider-failure boundaries.

## Decision, rollback, and limitations

Decision: continue to paired provider pilot/holdout preparation. Do not switch
the production planner. Rollback is the unchanged production control path;
because treatment is not wired into production, no runtime kill switch was
needed. A production decision requires the plan's zero-tolerance gates,
pre-registered non-inferiority intervals, paired holdout, efficiency threshold,
and complete proof packet with real responses.
