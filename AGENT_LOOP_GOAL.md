# Agent Loop Goal and Stop Conditions

## Goal

For one user task, make bounded progress while preserving project safety,
stable identity, exact budget accounting, and evidence-backed completion.

## Acceptance conditions

A run may report success only when:

- the requested result is present;
- runtime state has absorbed the relevant tool results;
- required verification has completed successfully;
- no indeterminate side effect remains;
- the final report exposes remaining risks.

Core task acceptance and post-core project improvement are separate. A typed
`ProjectImprovementPolicy` decides whether improvement is disabled, optional, or
required. Optional improvement failure must remain visible but does not negate a
verified core result; required improvement is part of acceptance and must
succeed before the run reports overall success.

## Stop conditions

The loop stops when:

- the goal and required verification are complete;
- a runtime budget is exhausted;
- user input or approval is required;
- project drift or an indeterminate side effect cannot be reconciled safely;
- the checkpoint is corrupt or incompatible;
- bounded recovery or replanning is exhausted.

A stop is not automatically a failure. It must carry a completion reason and,
when checkpointing is enabled, a controlled-stop checkpoint if the current
state is safe to persist.
