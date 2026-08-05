# Stage 7 completion-budget campaign V4

V4 supersedes V3 for new execution. V3 completed one quality-matched pair, but
the next arm visibly retrieved success/failure memories written by preceding
arms. The fixture and source snapshot were fresh, while the default
`data/memory` store was not. That makes arm order and accumulated experiment
history a changing input, so the V3 pair is retained as diagnostic evidence and
excluded from V4 statistics.

V4 keeps V3's provider, task, fixed decomposition, deterministic observable
docstring goal, route-aware purpose coverage, quality gates, alternating order,
cache-off rule, and spend limits. It adds one common intervention: every arm
constructs the full runtime against an empty memory store beneath that arm's
output directory. Memory remains writable and retrievable within the arm, but
cannot cross arm boundaries. The manifest records the resolved store path, and
the campaign stops if it is not exactly `<arm output>/isolated_memory`.

This is experiment isolation, not a production memory behavior change. No
historical V1–V3 arm is reused. Execution remains explicit and requires a new
output directory:

```bash
PYTHONPATH=Code/src:experiments/full_architecture_context_observation \
python experiments/full_architecture_context_observation/stage7_campaign.py \
  --execute \
  --output-dir experiments/full_architecture_context_observation/runs/stage7_campaign_v4
```

Only three complete, equal-quality V4 pairs can support a fixed-task mechanism
claim. They do not establish distribution-wide causality.

## First execution attempt

The first V4 attempt completed one pair and the dynamic half of pair two, then
stopped on the pair-two static arm. Memory isolation remained valid. The task
description began with the imperative verb “Add” and later named `divide`; the
localized symbol resolver flattened all prose and selected the earlier source
symbol `add`. The resulting unchanged `add` replacement was correctly rejected
as no diff. Production routing now resolves evidence by authority layer and
rejects ambiguous same-layer matches. The stopped directory remains immutable;
the protocol must restart all six arms against one new source snapshot.
