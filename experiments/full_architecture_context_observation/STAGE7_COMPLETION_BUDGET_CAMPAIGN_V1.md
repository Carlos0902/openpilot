# Stage 7 completion-budget campaign V1

## Purpose

This campaign measures the historical four-purpose enhancement completion policy in the
full `IntelligentAutopilot` path. It is a three-pair mechanism campaign, not a
distribution-wide causal benchmark. No result may be reported unless at least
three pairs have equal quality, complete provider-usage coverage, and all four
target purposes.

The frozen machine-readable protocol is
`STAGE7_COMPLETION_BUDGET_CAMPAIGN_V1.json`.

## Paired design

The six runs use alternating order to reduce simple order bias:

1. static, dynamic;
2. dynamic, static;
3. static, dynamic.

Both arms use the same code snapshot, fixed four-task decomposition,
provider/model/endpoint/reasoning profile, cache-off setting, task, and quality
gates. The static arm fixes each purpose at its existing ceiling. The dynamic
arm uses the production complexity/value/prompt-size/remaining-call policy.
This policy assignment is the only arm-specific isolation point. Static sets
`floor == ceiling` and `recovery_step=0`; dynamic keeps the production
`recovery_step=300` and one-shot length recovery. Both arms otherwise share the
same coordinator, total pool, request contracts, runtime, and common Goal Maker
intervention. The analyzer counts `recovery_of` requests separately.

The historical calculator path normally derives `iteration_goal`
deterministically from `selected_candidate`. The campaign disables that bypass
at `AutonomousIterationAgent._goal_from_candidate` for both arms so the
`iteration_goal` purpose is actually transported. This common intervention is recorded
in every arm manifest and is not a production default change.

## Fixed-state and safety rules

At campaign start the orchestrator hashes `Code/src` and the experiment code
and protocols, excluding runs, caches, and experiment memory data. The same
hash is checked before and after every arm. Provider identity must equal the
frozen protocol before the first provider call. Every arm has a 900-second
outer timeout; `run_observation.py` retains its own call and Token guards.

Spend gates are 45,000 observable tokens per arm, 90,000 per pair, and 270,000
for the campaign. Warnings are emitted at 75,000 per pair and 225,000 for the
campaign. Transport retry count is fixed at zero. After every arm the campaign
stops immediately on quality failure, incomplete usage, unknown failed-attempt
usage, provider mismatch, transport retry evidence, hard spend breach, or code
snapshot drift.

The command is dry-run by default and therefore cannot call a provider:

```bash
PYTHONPATH=Code/src:experiments/full_architecture_context_observation \
python experiments/full_architecture_context_observation/stage7_campaign.py
```

After reviewing the emitted identity, snapshot, and six-run schedule, the
provider campaign requires an explicit flag and a new output directory:

```bash
PYTHONPATH=Code/src:experiments/full_architecture_context_observation \
python experiments/full_architecture_context_observation/stage7_campaign.py \
  --execute \
  --output-dir experiments/full_architecture_context_observation/runs/stage7_campaign_v1
```

## Quality-first analysis

Each run must preserve:

- core success and passed verification;
- successful project improvement;
- mutation scope limited to `calculator.py`;
- unchanged `test_calculator.py` hash;
- successful exact `python -m pytest -q` and
  `python -m compileall -q calculator.py` commands;
- the frozen decomposition descriptor.

Pairs are excluded if their quality signatures differ, provider usage coverage
is below 100%, any of the four purposes is absent, the code snapshot changes,
provider identity differs, or the arm descriptor is wrong.

The analyzer reports core, enhancement, and full-lifecycle usage; per-purpose
enhancement usage; failed-attempt usage; usage coverage; request-time
reservations; and `recovery_of` counts. Reservation facts do not establish
settlement: refunds and unknown-usage reconciliation are never inferred.

Each arm uses a fresh disposable fixture and its production rollback behavior.
If the campaign stops, completed arm directories remain immutable evidence;
the orchestrator does not silently resume, reuse a partial pair, refund unknown
usage, or reorder the remaining arms. Recovery is a deliberate new campaign
output directory after the cause is resolved. Within one dynamic arm, only the
production one-shot `length` recovery is allowed and is recorded through
`recovery_of`; the static control arm has no recovery expansion.

## Claim boundary

Three accepted pairs can support a claim about this fixed mechanism and task.
They cannot establish distribution-wide causality or prove that context
pressure has disappeared. Immutable historical runs remain unchanged and are
not silently enrolled as campaign arms.

## Historical execution status

V1 executed one static-labelled arm on 2026-08-04 and stopped fail closed. The
arm is not an eligible static sample: the controller-owned runtime budget
overrode the local experiment policy, localized mutation used ungoverned
`code_edit`, two timeout attempts had unknown usage, and project improvement
failed with no observed diff. V1 is retained only to describe that immutable
failure. The corrected five-purpose campaign is V2 and must use a new output
directory.
