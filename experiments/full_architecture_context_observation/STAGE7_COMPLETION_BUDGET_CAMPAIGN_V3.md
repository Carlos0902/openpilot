# Stage 7 completion-budget campaign V3

V3 supersedes V2 for new execution. V2 proved that the selected static policy
was applied and that localized `code_edit` was bounded, but its first arm was
not an eligible comparison sample: the post-core goal was vague, no improvement
diff was produced, and the protocol incorrectly required both mutually
exclusive mutation routes (`code_generation` and `code_edit`). V2 remains
immutable failure evidence and is not resumed.

V3 preserves the frozen provider, fixed core decomposition, alternating
three-pair order, cache-off rule, usage gates, quality checks, and hard spend
limits. Both arms now receive the same deterministic, observable enhancement
goal: add a concise docstring to the existing `divide` function documenting
that a zero denominator raises `ValueError`. This common intervention removes
goal-selection variance; it is not an arm-specific optimization and does not
change production defaults.

Purpose coverage is route-aware:

- `project_improvement` and `iteration_task_design` must use the provider;
- `iteration_goal` must be recorded as deterministic;
- at least one real mutation route, `code_generation` or `code_edit`, must be
  observed;
- the quality gate still requires an actual `calculator.py` diff, unchanged
  tests, exact pytest and compileall validation, and successful improvement.

The static arm retains the V2 fixed purpose ceilings. The dynamic arm retains
the production completion-budget policy. A single-target Task Designer request
is classified as routine so the provider-neutral reasoning policy can select
the lowest supported reasoning effort; multiple safe targets remain complex.

The launcher is dry-run by default. Provider execution requires `--execute`
and a new output directory:

```bash
PYTHONPATH=Code/src:experiments/full_architecture_context_observation \
python experiments/full_architecture_context_observation/stage7_campaign.py \
  --execute \
  --output-dir experiments/full_architecture_context_observation/runs/stage7_campaign_v3
```

After every arm the campaign stops on quality failure, incomplete or unknown
usage, policy mismatch, transport retry, provider mismatch, snapshot drift, or
spend breach. Only three quality-matched complete pairs may support a claim
about this fixed task and mechanism; they do not establish distribution-wide
causality.

## Historical execution status

After the README permission repair, V3 completed its first static/dynamic pair
with equal quality and complete usage. Static enhancement usage was 6,052
tokens and dynamic was 5,990; lifecycle totals were 14,429 and 13,867. Before
the third arm completed, diagnostics showed it retrieving success/failure
memories written by earlier arms through the shared default memory store. The
campaign was manually stopped because the memory baseline was not fixed. This
pair is diagnostic evidence only and is not enrolled in V4.
