# Full-architecture context observation

Experiment 1 is an observation-only pilot. It runs a fixed multi-stage repair
through the normal `IntelligentAutopilot` execution path without changing prompt
selection, context assembly, budgets, schemas, or controller behavior.

The frozen protocol is `OBSERVATION_PROTOCOL_V1.json`. A fresh disposable copy
of `fixtures/calculator_project` is used for every run. Runtime diagnostics and
derived results are kept beneath `runs/`, separate from production diagnostics.

`OBSERVATION_PROTOCOL_V1.json` has `intervention=none`; the default runner does
not replace task decomposition. Fixed-decomposition provider runs use the
separate `PROJECT_IMPROVEMENT_PROVIDER_ARM_PROTOCOL_V1.json`, selected either
with `--fixed-decomposition` or an explicit `--protocol` path. Only that
provider-arm protocol installs and records the fixed-decomposition descriptor.

The runner enforces provider-call, cumulative provider-attempt Token, and wall
clock limits before every provider request. Cumulative usage includes successful
responses and failed attempts whenever the provider failure exposes usage;
unobservable failed-attempt usage remains explicitly unknown. An in-flight call
is not cancelled if it reaches or crosses a limit: its response or failure is
preserved and accounted, while every later request is blocked before transport.
`manifest.json.guard_observation` records the blocking limits, whether another
request was allowed, and whether the runtime ended after such a final attempt or
because the guard blocked the next request.

The pilot can reveal a within-run inflation signal. With one task and no
counterfactual it cannot quantify the benefit of a future budget module or prove
that context management caused a task outcome.

`code_generation_context_ab.py` replays the downstream failure from run
`20260803T183221Z` against the contextual Code Generator candidate adapter.
Its source events are hash locked, and its hard gates require the task, mutation
boundary, safety facts, current source, and output contract to remain complete.
Results and the subsequent fixed-decomposition provider arm are documented in
`CODE_GENERATION_CONTEXT_AB_RESULT_V1.md`.

The reasoning-policy experiment is frozen by
`REASONING_POLICY_AB_PROTOCOL_V1.json`. `reasoning_policy_ab.py` runs the fixed
routine, complex, and completion-budget screens with cache disabled and
hash-locked fixtures. `REASONING_POLICY_AB_REANALYSIS_V1.json` preserves the
offline system-quality correction for the earliest immutable run. The decision,
source hashes, and final one-pair full-architecture mechanism pilot are recorded
in `REASONING_POLICY_AB_RESULT_V1.md`; one pair is not a statistical causal
estimate.

The cross-stage acceptance boundary and next measurement are summarized in
`CONTEXT_GOVERNANCE_ACCEPTANCE_V1.md`.

The stopped V1, V2, and V3 Stage 7 runs remain immutable diagnostic evidence. V1 exposed
an ungoverned `code_edit` path and an overwritten experiment budget. V2 proved
that the corrected policy reached all selected calls, then stopped because the
enhancement produced no diff and the protocol treated mutually exclusive
mutation routes as jointly required. V3 completed one quality-matched pair, then
was stopped because the default memory store leaked prior-arm outcomes into
later arms. New three-pair execution is defined by
`STAGE7_COMPLETION_BUDGET_CAMPAIGN_V4.json` and documented in
`STAGE7_COMPLETION_BUDGET_CAMPAIGN_V4.md`. Its launcher is dry-run by default;
only an explicit `--execute` starts the six provider arms.
The completed three-pair outcome and its claim boundary are recorded in
`STAGE7_COMPLETION_BUDGET_CAMPAIGN_V4_RESULT.md`.

Stage 8 isolates the Task Designer projection policy under the production
dynamic completion budget. Its refrozen protocol is
`STAGE8_TASK_DESIGNER_CONTEXT_CAMPAIGN_V1.json`, the dry-run-by-default launcher
is `stage8_task_designer_context_campaign.py`, and the rationale and gates are
documented in `STAGE8_TASK_DESIGNER_CONTEXT_CAMPAIGN_V1.md`. The first completed
pair under `runs/stage8_campaign_20260804T084822Z` is diagnostic-only: it exposed
an invalid fixed-reservation gate and an upstream core-mutation hash confound.
The refrozen execution under
`runs/stage8_campaign_v1_20260804T085747Z` restarted all three crossed pairs and
accepted 3/3. Compact projection reduced Task Designer provider input by 76.81%
(11,979 to 2,778) and final-prompt tokens by 77.29% (11,904 to 2,703), with
matched task and full-architecture quality. Reservations were dynamically
derived as 1,150 for the current prompt and 1,000 for the compact prompt; all
six arms had zero failed provider attempts, transport retries, and completion
recoveries. Stable rendered evidence-ID headers let both arms retain the same
goal, safety, validation, and project-file evidence roles. The 76,808-token
campaign and its non-causal secondary measures are documented in
`STAGE8_TASK_DESIGNER_CONTEXT_CAMPAIGN_V1_RESULT.md`. The result is limited to a
fixed-source full-architecture mechanism claim and does not automatically
change the production projection default.

Stage 9 V2 adds a zero-provider scenario gate before broader paid confirmation.

The subsequent Stage 9 provider sentinel V1 stopped after its first paid pair.
It observed a 41.39% Task Designer provider-input reduction with both arm-level
quality gates passing, but the two independent full runs reached Task Designer
with different live `calculator.py` summaries. The exact runtime-contract gate
correctly rejected the pair as causal A/B evidence. See
`STAGE9_TASK_DESIGNER_PROVIDER_SENTINEL_V1.md`; no later V1 arm may run. The
next experiment uses a same-run paired production/shadow request at one
post-core snapshot rather than relaxing the runtime hash.
Its frozen protocol is `STAGE9_TASK_DESIGNER_SCENARIO_CANARY_V2.json`, the
offline runner is `stage9_task_designer_scenario_gate.py`, and the rationale,
sentinels, fail-closed gates, and two deferred six-arm phases are in
`STAGE9_TASK_DESIGNER_SCENARIO_CANARY_V2.md`. The four fixtures cover an
unrelated diagnosis, exact diagnosis identity, a diagnosis sharing one complete
criterion, and an independently relevant iteration-result memory. The committed
offline result is `STAGE9_TASK_DESIGNER_SCENARIO_CANARY_V2_OFFLINE_RESULT.json`.
Running the offline gate makes zero provider calls; no paid runner is included.
Running the offline gate makes zero provider calls. The separately opt-in V1
paid runner is retained for audit, but its stop-on-first-failure state is
terminal; the paired-shadow successor is a separate protocol.

Stage 7F isolates the provider-neutral Goal reasoning route from compact
selection and completion reservation. Three interleaved pairs are recorded in
`STAGE7F_INTERLEAVED_GOAL_REASONING_RESULT.md`; the corrected runner counts
every attempt, including recovery, and keeps unknown usage fail-closed. The
follow-up full-session routine canary is documented in
`STAGE7F2_FULL_SESSION_ROUTINE_COMPACT_RESULT.md`: it reached analyzer, Goal,
and both Task Designer arms with matching source/constraint/checkpoint hashes,
and measured a modest Task prompt reduction. These are provider-profile-scoped
canary results; the production reasoning and projection defaults remain
unchanged.

For a run that reaches project improvement, `improvement_cost` treats
`project_improvement`, `iteration_goal`, `iteration_task_design`,
`code_generation`, and `code_edit` as the target observation window. It reports successful and
failed-attempt usage separately, their observable total, and logical-request
usage coverage. New completion-budget request traces also expose
`reservation_id`, reserved and remaining tokens, and `recovery_of`. These are
request-time audit facts only: the experiment analyzer does not infer
reconciliation, refunds, or unknown-usage settlement from them.

Run the deterministic harness suite with:

```bash
PYTHONPATH=Code/src pytest -q experiments/full_architecture_context_observation
```

The local `pytest.ini` excludes `fixtures/` and immutable `runs/`; fixture
project tests are experiment inputs, not harness tests.
