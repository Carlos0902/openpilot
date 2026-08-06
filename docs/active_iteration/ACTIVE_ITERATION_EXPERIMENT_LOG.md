# Active Iteration Experiment Log

## 1. Purpose and update rule

This is the append-oriented experiment ledger for the E0/E2/E3 active-iteration
program. It records why each experiment existed, what was frozen before outcome
generation, what happened, what the result does and does not establish, and what
must happen next.

Every future experiment must be added here in the same change set as its frozen
protocol or result. A rejected protocol remains in the ledger. Development replay
must never be relabeled as an independent holdout, and a passed aggregate gate
must not hide a failed case-level safety gate.

Evidence levels used in this log:

- `harness`: validates instrumentation or experiment boundaries, not a policy claim.
- `synthetic feasibility`: validates a mechanism on a constructed same-domain suite.
- `exploratory ablation`: post-outcome mechanism attribution; not independent evidence.
- `development replay`: a policy fix evaluated on already revealed failures.
- `independent synthetic holdout`: frozen new synthetic dimension evaluated once.
- `real shadow`: real task inputs and tools, but no state-changing action is authorized.
- `sandbox execution`: real tasks with state changes restricted to disposable environments.

Historical synthetic and read-only experiments retain their original evidence
eligibility fields. The accepted sandbox V5 result is eligible only for the
scoped provider-backed disposable-sandbox E2-to-E3 claim. Production Controller
behavior is unchanged and remains unauthorized.

The core E2/E3 milestone now includes active measurement, mechanism-aware action
selection, execution safety, closed-loop control and the accepted disposable
real-task sandbox confirmation. Expert routing, cross-model transfer and
production integration are deferred, separate claims.

## 2. Program status

| ID | Experiment | Evidence level | Status | Main result |
|---|---|---|---|---|
| E0-H1 | trajectory/evidence acceptance harness | harness | passed | same-run, mutation invalidation, freshness and independent-point derivation fail closed |
| E2-V1 | active measurement protocol V1 | protocol audit | rejected before execution | propositions, coverage and conflict semantics were under-specified |
| E2-V2 | active measurement pilot | synthetic feasibility | passed | exact diagnosis `5/5` in both arms; logical cost `62 -> 53` |
| J-V1 | E2+E3 closed-loop pilot V1 | invalid trial | rejected | actions directly wrote derived outcomes |
| J-V2 | E2+E3 closed-loop pilot V2 | synthetic feasibility | passed | recovery `5/5` in both arms; total logical cost `82 -> 68` |
| E3-A1 | matched-diagnosis isolated ablation | exploratory ablation | passed | action-policy logical cost `20 -> 15` with equal diagnosis |
| E3-L1 | actionable-residual lattice V1 | independent synthetic state-space check | failed safety gate | severe failures `14 -> 26`; residual-controller-v1 was unsafe |
| E3-C2 | closure-aware controller V2 | development replay | passed development target | recovery `92 -> 95`, severe `14 -> 12` |
| E3-T1 | single-action no-op transition holdout | independent synthetic holdout | aggregate pass, promotion rejected | aggregate severe `320 -> 314`, but two treatment-only severe cases |
| E3-M3 | fault-aware minimax controller V3 | development replay | passed development target | recovery `343 -> 349`, severe `320 -> 314`, treatment-only severe `0` |
| E3-P1 | partial-action-effect holdout | independent synthetic holdout | passed | recovery `417 -> 425`, severe `254 -> 246`, treatment-only severe `0` |
| E2-N1 | measurement nuisance/freshness holdout | independent synthetic holdout | failed critical-error gate | correct `10/10`, critical errors `28 -> 31`; unknown-first V2 rejected |
| E2-D3 | failure-exposure-aware V3 | development replay | passed development target | correct `10 -> 13`, critical errors `28 -> 16` on revealed E2-N1 |
| E2-T1 | measurement timing holdout | independent synthetic holdout | failed accuracy gate | correct `8 -> 5`, critical errors `12 -> 10`; tradeoff rejected |
| E2-D4 | guarded fixed-prefix V4 | development replay | passed baseline-safety target | nuisance matches fixed `10/28`; timing `8/12 -> 8/2` |
| E2-C4 | guarded V4 new-suite confirmation | independent synthetic holdout | passed | correct `23 -> 24`, critical `3 -> 3`, false success `0 -> 0`, fresh `14 -> 16` |
| E3-R1 | positional no-op/transient-retry holdout | independent synthetic holdout | narrow pass; planned scope incomplete | recovery `291 -> 299`, severe `474 -> 462`; receipt/validation pending |
| E3-R2 | receipt/delayed-validation holdout | independent synthetic holdout | failed freshness gate | recovery `29 -> 38`, severe `19 -> 11`, false success `0`; evidence-blocked `5 -> 8` |
| E3-D4 | receipt reconciliation adapter V4 | development replay | passed development target | fresh success `29/38`, evidence-blocked `0/0`; action policy and costs unchanged |
| E3-C4 | receipt reconciliation confirmation V1 | invalid trial | rejected after semantic audit | runner bypassed frozen V4 and violated its one-checkpoint limit; numeric output is ineligible |
| E3-D5 | shared receipt reconciliation adapter V5 | development replay | passed development target | fresh success `29/38`; retry/terminal reconciliation `24/7`, provenance passed |
| E3-C5 | receipt reconciliation confirmation V2 | independent synthetic holdout | passed | recovery `33/33`, severe `18/18`, positive fresh `22/22`, negative probes `48/48` fail closed; cost `584 -> 564` |
| J-X1 | E2 x E3 joint synthetic-exit V1 | invalid blocked draft/diagnostic | rejected before joint execution | invalid E3-C4 prerequisite; measurement-only diagnostic exposed V4 freshness `11 -> 10` |
| E2-D5 | balanced fixed-prefix V5 | development replay | passed four revealed datasets | fixed/V5 freshness on joint diagnostic `11 -> 12`; all per-dataset gates passed |
| J-N2 | E2 x E3 joint synthetic-exit V2 | independent synthetic holdout | passed after two pre-run revisions were rejected | 60/arm correct and fresh, severe/false success `0/0`, all exposure and replay gates passed; cost `1416 -> 1416` |
| RTS-X1 | first real-provider shadow sample | exploratory real shadow | failed objective gate, boundary retained | objective `1/12`; side-effect-free `12/12`; provider/parse and evidence-contract failures preserved |
| RTS-X2 | provider-compatible response/quote development | real-shadow development | failed evidence gate | answers `12/12`, strict evidence/objective `4/12`; free-text quote contract rejected |
| RTS-X3 | unseen candidate-evidence confirmation | exploratory real-shadow confirmation | passed harness gate | objective/evidence `12/12`, side-effect-free `12/12`; harness only, later formal rows below |
| RT-P0 | strict-read-only trajectory preflight | permission/trajectory harness | passed; read-only real-trajectory entry eligible | immutable two-tool registry, zero-index reader, terminal disabled-tool denial; frozen 8-task/24-run protocol |
| RT-R1 | provider-backed real-trajectory corpus acquisition | real-provider trajectory gate | passed corpus eligibility | `24/8/4/20/22`, dominance `0.125`, stable repository fingerprint; corpus gate only, later E2/E3 rows below |
| RT-E2-S0 | blind real-trajectory E2 packet smoke | structural smoke | passed structural gate | 8 tasks, expected `4/4`, observed `1/7`, no outcome leakage/provider call/mutation; not effect evidence |
| RT-E2-X1 | formal E2 V1 semantic audit | invalid real-trajectory trial | rejected before persistence | freshness claim exceeded one-record EvidenceBundle support; draft metrics excluded |
| RT-E2-R2 | formal E2 paired shadow V2 | formal read-only diagnostic shadow | passed all E2 gates | outcome/alignment/fresh `24/24`, severe/false success `0/0`, cost `264 -> 216`; opened the later E3 proposal shadow |
| RT-E3-D1 | mechanism-aware action-proposal adapter | post-corpus real-trajectory development | passed development target | mechanism match `16 -> 24`, safe/fresh `24/24`, dispatch/mutation `0/0`; independently confirmed by RT-E3-H0/C1 below |
| S1/S2 | sandbox boundary and immutable receipt contracts | deterministic safety preflight | passed | strict outside-source sandbox; missing/duplicate/cross-run/partial receipts fail closed |
| S3-V1 | disposable-sandbox rollback fault matrix | synthetic local-file safety | passed | `5 rolled_back / 1 inconclusive`, source unchanged, false success/unrecovered severe `0/0` |
| C1-V3 | active closed-loop control holdout | independent synthetic holdout | passed after V1/V2 audit rejection | matched diagnosis; recovery/fresh success `6 -> 18`, severe/false success `0/0`, all per-stratum gates passed |
| C2-V2 | adversarial long-horizon stopping | independent synthetic holdout | passed after V1 runner drift rejection | correct stop `12/30 -> 30/30`, unsafe continuation `18 -> 0`, successful closure `6 -> 6` |
| C3-V2 | noncompensating net-gain holdout | independent synthetic holdout | passed after V1 invalid dependency binding | known strengths `16 -> 16`, target weaknesses `0 -> 16`, total correct `16 -> 32`, severe/false success `0/0` |
| R1-V1 | same-model expert-specialization preflight | deterministic synthetic harness | passed harness gates | paired success/safety `8/24 -> 24/24`, severe `16 -> 0`; no real-model or routing-effect claim |
| RT-E3-H0/C1 | new provider holdout acquisition and proposal confirmation | real-provider read-only confirmation | passed | mechanism match `12/16 -> 16/16`, safe/fresh `16/16`, zero false/missed actions |
| RT-E3-C1-V2 | permission-audited proposal confirmation | real-provider read-only audit | passed after V1 invalidation | 18 file-reader, 7 multi-reader, 8 denial, 0 forbidden dispatch |
| RS-V1 | first provider-backed disposable-sandbox execution | invalid execution trial | failed method gate | `0/4 -> 0/4`, three false successes; incomplete policy input and no content replay |
| RS-V2 | replayable provider-backed sandbox execution | valid negative sandbox result | failed strict-improvement gate | `4/4 -> 4/4`, zero false success; control ceiling |
| RS-V3/V4 | stratified sandbox confirmation attempts | incomplete invalid trials | rejected | malformed and empty provider responses aborted before outcome persistence |
| RS-V5 | stratified E2-measurement/E3-repair confirmation | provider-backed sandbox execution | passed all preregistered gates | success `2/8 -> 8/8`, six paired improvements, zero regressions, eight measurement-assisted successes, all four strata improved |
| MS-P0-V1-V4 | stock mini-SWE paired provider development smoke | development conformance | V1-V3 invalid; V4 passed boundary and safety gates | V4 success `1/1 -> 1/1`; active/ordinary calls `5/8`, tokens `15464/16398`, tools `5/10`, wall time `37.11/13.66s`; no hypothesis claim |

R1 now validates the expert-specialization experiment harness only. Because it
uses a deterministic synthetic model, it must not be described as evidence that
a real model improves under specialization or that dynamic routing adds value.

## 3. Execution record and reproduction entry points

The historical shell invocation was not retained for every early experiment.
The table therefore names the exact repository runner that generated or can
replay each result, plus the focused deterministic verification command. A test
pass validates contracts and replay behavior; it is not a substitute for an
independent holdout run. Provider runs are never reproduced by the offline test
command, and no provider should be called merely to check this ledger.

| ID | What was actually executed | Runner / durable evidence | Offline verification |
|---|---|---|---|
| E0-H1 | deterministic acceptance harness tests; no policy outcome | `derive_e0_acceptance`; E0 implementation record | `PYTHONPATH=Code/src pytest -q Code/tests/test_e0_instrumentation.py` |
| E2-V1 | protocol review only; no outcome run | rejected V1 protocol/candidates; no result file | protocol tests only; must not be reported as an experiment pass |
| E2-V2 | five-case synthetic feasibility run, persisted | `run_bound_active_measurement_pilot`; `e2_pilot_v2/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_active_measurement_bound_execution.py` |
| J-V1 | invalid trial; no accepted or persisted result | rejected V1 draft/protocol | `PYTHONPATH=Code/src pytest -q Code/tests/test_active_control_protocol.py` |
| J-V2 | five-case joint feasibility run, persisted | `run_joint_control_pilot`; `e2_e3_joint_v2/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_active_control_experiment.py` |
| E3-A1 | post-outcome matched-diagnosis replay, persisted | `run_isolated_e3_ablation`; `e3_isolated_v1/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_active_control_ablation_experiment.py` |
| E3-L1 | 128-state synthetic lattice run, persisted | `run_active_control_lattice`; `e3_lattice_v1/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_active_control_lattice_experiment.py` |
| E3-C2 | development replay on revealed lattice, persisted | `run_closure_aware_control_development_replay`; `e3_closure_v2/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_closure_aware_control_experiment.py` |
| E3-T1 | 768-case independent transition holdout, persisted | `run_transition_fault_holdout`; `e3_transition_holdout_v1/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_transition_fault_holdout_experiment.py` |
| E3-M3 | development replay on E3-T1, persisted | `run_fault_aware_minimax_development_replay`; `e3_fault_aware_minimax_v3/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_fault_aware_minimax_experiment.py` |
| E3-P1 | 768-case independent partial-effect holdout, persisted | `run_partial_effect_holdout`; `e3_partial_effect_holdout_v1/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_partial_effect_holdout_experiment.py` |
| E2-N1 | 30-case independent nuisance holdout, persisted failure | `run_active_measurement_nuisance_holdout`; `e2_nuisance_v1/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_active_measurement_nuisance_experiment.py` |
| E2-D3 | development replay on E2-N1, persisted | `run_failure_exposure_development_replay`; `e2_failure_exposure_v3/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_failure_exposure_measurement_experiment.py` |
| E2-T1 | 25-case independent timing holdout, persisted failure | `run_measurement_timing_holdout`; `e2_measurement_timing_v1/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_measurement_timing_holdout_experiment.py` |
| E2-D4 | development replay on E2-N1/E2-T1, persisted | `run_guarded_measurement_v4_development_replay`; `e2_guarded_fixed_prefix_v4/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_guarded_measurement_v4_experiment.py` |
| E2-C4 | 27-case independent V4 confirmation, persisted with execution | `run_guarded_v4_confirmation`; `e2_v4_confirmation_v1/{result_summary.json,execution.json}` | `PYTHONPATH=Code/src pytest -q Code/tests/test_guarded_measurement_v4_confirmation.py` |
| E3-R1 | 896-case positional timing/retry holdout, persisted | `run_fault_timing_retry_holdout`; `e3_fault_timing_retry_v1/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_fault_timing_retry_experiment.py` |
| E3-R2 | 56-case/arm receipt-validation holdout, persisted failure and ledger | `run_receipt_validation_holdout`; `e3_receipt_validation_v2/{result_summary.json,attempt_ledger.json}` | `PYTHONPATH=Code/src pytest -q Code/tests/test_receipt_validation_holdout_experiment.py` |
| E3-D4 | development replay on E3-R2, persisted | `run_receipt_reconciliation_development`; `e3_receipt_reconciliation_v4/` | `PYTHONPATH=Code/src pytest -q Code/tests/test_receipt_reconciliation_experiment.py` |
| E3-C4 | nominal confirmation run persisted, then semantically invalidated | `run_receipt_reconciliation_confirmation`; invalid V1 result/ledger retained | `PYTHONPATH=Code/src pytest -q Code/tests/test_receipt_reconciliation_confirmation_experiment.py` verifies retention, not eligibility |
| E3-D5 | shared-adapter development replay, persisted | `run_receipt_reconciliation_v5_development`; `e3_receipt_reconciliation_v5/` | `PYTHONPATH=Code/src pytest -q Code/tests/test_receipt_reconciliation_v5_development.py Code/tests/test_receipt_reconciliation_adapter_v5.py` |
| E3-C5 | 56-case/arm independent confirmation, persisted with ledger | `run_receipt_reconciliation_confirmation_v2`; `e3_receipt_reconciliation_confirmation_v2/` | `PYTHONPATH=Code/src pytest -q Code/tests/test_receipt_reconciliation_confirmation_v2.py` |
| J-X1 | joint runner blocked before outcome; only 12-case measurement diagnostic persisted | fail-closed V1 joint runner; `e2_joint_draft_diagnostic/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_joint_synthetic_exit_experiment.py` |
| E2-D5 | four-dataset revealed development replay, persisted | `run_balanced_prefix_v5_development`; `e2_balanced_prefix_v5/result_summary.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_active_measurement_v5_experiment.py` |
| J-N2 | final 60-case/arm holdout run, persisted with 120 bundles | `run_joint_synthetic_exit_v2` then `persist_joint_exit_v2_artifacts`; `joint_synthetic_exit_v2/` | `PYTHONPATH=Code/src pytest -q Code/tests/test_joint_synthetic_exit_v2_experiment.py Code/tests/test_joint_synthetic_exit_v2_semantics.py Code/tests/test_joint_synthetic_exit_v2_suite_exposure.py` |
| C1-V3 | 24 matched cases/arm in pure in-memory action environment; V1/V2 retained invalid | `run_c1_closed_loop_holdout` then `persist_c1_execution`; `c1_closed_loop_holdout_v3/` | `PYTHONPATH=Code/src pytest -q Code/tests/test_c1_closed_loop_holdout.py` |
| RTS-X1/X2/X3 | three distinct 12-cell provider matrices; external outcomes persisted under `/tmp` | `run_real_task_shadow` then `persist_real_task_shadow_result`; versioned protocol/public/hidden files | `PYTHONPATH=Code/src pytest -q Code/tests/test_real_task_shadow.py` checks harness only and does not call provider |
| RT-P0 | offline permission-boundary tests and frozen trajectory-pool audit; no provider/task matrix run | strict runtime path plus `real_trajectory_shadow/{TASK_POOL_V1.json,REAL_TRAJECTORY_SHADOW_PROTOCOL_V1.json}` | `PYTHONPATH=Code/src pytest -q Code/tests/test_strict_read_only_boundary.py Code/tests/test_real_trajectory_shadow_protocol.py Code/tests/test_real_trajectory_shadow_eligibility.py` |
| RT-R1 | three final frozen 8-task provider rounds; two earlier 24-run attempts invalidated for repository drift | normal `IntelligentAutopilot` path via `build_autopilot_executor(strict_read_only=True)`; `real_trajectory_shadow/RESULT_SUMMARY_V1.json` plus external full corpus | same RT-P0 offline suite verifies contracts only; it does not reproduce provider outcomes |
| RT-E2-S0 | blind packet construction over one trajectory per task | `run_real_trajectory_e2_smoke`; `real_trajectory_shadow/E2_SMOKE_RESULT_V1.json` | `PYTHONPATH=Code/src pytest -q Code/tests/test_real_trajectory_e2_shadow.py` |
| RT-E2-R2 | 24-case paired real-trajectory diagnostic replay | `run_real_trajectory_e2_formal_experiment`; formal V2 result/execution artifacts | `PYTHONPATH=Code/src pytest -q Code/tests/test_real_trajectory_e2_formal_experiment.py` |
| RT-E3-H0/C1 | 16-run provider holdout plus offline proposal confirmation | provider holdout acquisition archive and confirmation V2 artifacts | `PYTHONPATH=Code/src pytest -q Code/tests/test_e3_provider_holdout_acquisition.py Code/tests/test_e3_provider_holdout_confirmation.py` |
| S1/S2/S3 | deterministic sandbox boundary, receipt probes and six rollback fault classes | disposable-sandbox/receipt protocols and `s3_rollback_fault_v1/` | `PYTHONPATH=Code/src pytest -q Code/tests/test_disposable_sandbox_contract.py Code/tests/test_action_effect_receipt_contract.py Code/tests/test_s3_rollback_fault_experiment.py` |
| C2-V2/C3-V2 | long-horizon stopping and noncompensating net-gain holdouts | accepted V2 execution/result artifacts | `PYTHONPATH=Code/src pytest -q Code/tests/test_c2_adversarial_stopping_holdout.py Code/tests/test_c3_noncompensating_net_gain_holdout.py` |
| R1-V1 | 24 paired deterministic specialization cases | `r1_expert_specialization_v1/` | `PYTHONPATH=Code/src pytest -q Code/tests/test_r1_expert_specialization_holdout.py` |
| RS-V1/V2 | two four-task provider-backed sandbox matrices | `real_sandbox_execution/v1/` and `v2/` | `PYTHONPATH=Code/src pytest -q Code/tests/test_real_sandbox_active_iteration.py` replays artifacts without provider calls |
| RS-V3/V4 | incomplete provider attempts, no persisted outcome execution | hash-bound invalid-trial JSON | same sandbox test verifies retention and protocol bindings |
| RS-V5 | eight-task/four-stratum paired provider-backed sandbox confirmation | `run_real_sandbox_experiment`; `real_sandbox_execution/v5/{execution.json,result_summary.json}` | `PYTHONPATH=Code/src pytest -q Code/tests/test_real_sandbox_active_iteration.py` independently reruns all persisted public/hidden outcomes |
| MS-P0-V1-V4 | one exposed task per frozen paired stock mini-SWE smoke; V1-V3 retained invalid | `python -m mini_swe_active_iteration.smoke`; `mini_swe_active_iteration/development_smoke_v*/` | `cd experiments/mini_swe_active_iteration && PYTHONPATH=src uv run pytest -q`; V4 final files were also independently replayed through the host evaluator |

The two rejected J-N2 pre-run revisions (`f00d7f...a534b` and
`5df117...c8ce`) were in-memory/pre-outcome audit failures. They produced no
formal result file and are intentionally absent from the persisted-outcome
count. Likewise, E3-C4's files remain reproducible bytes but not valid evidence.

## 4. Completed experiments

### E0-H1 trajectory/evidence acceptance harness

**Problem.** Existing trajectory and `EvidenceBundle` contracts froze same-run
events and artifacts, but there was no deterministic entry point for field-level
mutation invalidation, evidence freshness, independent validation points, or
success-stop eligibility.

**Design.** `derive_e0_acceptance` consumes append-only `EventRecord`,
`EvidenceBundle`, field specifications and observations. Tests cover sequence
gaps, cross-run references, content-hash drift, registry drift, mutation without
replacement evidence, repeated attempt/evidence identities, empty turns and
future-event references.

**Result.** The harness fails closed for all tested integrity and freshness
violations and deterministically derives current satisfaction, invalid fields,
independent stable points and `stop_success_eligible`.

**Conclusion.** Existing trajectory/evidence storage is sufficient for the
synthetic E2/E3 program when combined with strict signal identities. This does
not prove that production recorder events contain every required mutation and
validation envelope.

**Primary record.** `docs/task_trajectory/IMPLEMENTATION_LOG.md`, section
"E0 trajectory/evidence acceptance harness".

### E2-V1 active measurement protocol audit

**Problem.** The first protocol bound candidate sources but did not uniquely
define field propositions, measurement prerequisites, C5-C8 inputs, conflict
resolution, or the mechanism by which active selection could legally omit work.

**Decision before outcome.** Two independent protocol reviews rejected V1 before
pilot execution. No V1 result summary was produced.

**Conclusion.** Keeping the rejected protocol prevents a later V2 result from
being presented as if V1 had passed.

**Artifacts.** `experiments/active_measurement/E2_PILOT_PROTOCOL_V1.md` and
`experiments/active_measurement/e2_pilot_v1/candidates.json`.

### E2-V2 active measurement synthetic feasibility

**Problem.** Determine whether an active unknown-first measurement policy can
reduce measurement work without reducing exact diagnosis or increasing critical
errors.

**Frozen design.** Five same-domain repository-analysis fixtures, nine fields,
eight deterministic measurements, strict checker versions, fixed and active
policy fingerprints, content-addressed suite, equal case set, accuracy and
critical-error gates, then cost comparison.

**Result.** Both arms diagnosed `5/5` cases exactly with `0` critical errors.
Fixed cost was `62`; active cost was `53`; eligible delta was `-9`.

**Conclusion.** Active measurement is mechanically useful on this synthetic
suite. It does not estimate an effect on real task distributions, provider cost,
or noisy measurement systems.

**Protocol/result.**
`experiments/active_measurement/E2_PILOT_PROTOCOL_V2.json` and
`experiments/active_measurement/e2_pilot_v2/result_summary.json`.

### J-V1 rejected joint closed-loop trial

**Problem.** Test whether E2 diagnosis can drive E3 action selection.

**Observed invalidity.** A1 and other actions directly set derived
`synthesis_ready` or deliverable outcomes. This allowed success without the
required atomic evidence.

**Decision.** Trial numbers were discarded; no result summary was saved. V1
draft and protocol remain audit artifacts.

### J-V2 E2+E3 synthetic closed-loop feasibility

**Frozen design.** Control used fixed measurement plus fixed applicable-action
order. Treatment used unknown-first measurement plus residual-controller-v1.
Actions changed atomic fields only; `synthesis_ready` was recomputed after every
action. Both arms shared action catalog, three-step budget, hidden truth and
post-action validation semantics.

**Result.** Both arms recovered `5/5`, had `0` severe failures and `5/5` fresh
success stops. Control measurement/action/total cost was `62/20/82`; treatment
was `53/15/68`; eligible joint delta was `-14`.

**Conclusion.** Measured E2 output can drive the deterministic E3 harness without
breaking recovery, safety or freshness. Because both measurement and action
policies changed, the joint difference cannot be attributed to E3 alone.

**Protocol/result.** `experiments/active_control/E2_E3_CLOSED_LOOP_PILOT_V2.json`
and `experiments/active_control/e2_e3_joint_v2/result_summary.json`.

### E3-A1 matched-diagnosis exploratory ablation

**Problem.** Separate E3 action-policy contribution from the joint `-14` result.

**Design.** Run unknown-first diagnosis once per case, freeze its fingerprint,
and pass the identical diagnosis trace and measurement cost to fixed-action-v1
and residual-controller-v1.

**Result.** Both arms recovered `5/5` with no severe failure. Measurement cost was
`53` in both arms; action cost was `20/15`; total cost was `73/68`.

**Conclusion.** On the already revealed suite, E3 action selection accounts for
`-5` logical cost and E2 for `-9`. This is post-joint exploratory attribution,
not independent E3 evidence.

**Result.** `experiments/active_control/e3_isolated_v1/result_summary.json`.

### E3-L1 exhaustive actionable-residual lattice

**Problem.** Five cases could not expose unsafe residual combinations under the
three-action budget.

**Frozen design.** All `2^7=128` assignments over actionable atomic fields,
always-met workspace root, derived synthesis rule, complete severity registry,
shared diagnosis and fixed/residual policies.

**Result.** Fixed/residual recovery was `92/95`, but severe failures were
`14/26`. Safety failed, so raw costs `1079/971` were not comparable and the claim
was false.

**Finding.** residual-controller-v1 ranked direct coverage only. It could not
value A2 restoring critical synthesis through dependency closure.

**Result.** `experiments/active_control/e3_lattice_v1/result_summary.json`.

### E3-C2 closure-aware controller V2 development replay

**Problem.** Repair the dependency-closure failure found by E3-L1.

**Frozen change.** Project each candidate action's atomic effects, recompute
derived fields, then rank actual resolved severity, resolved count, cost and
action ID.

**Result.** On the revealed lattice, fixed/V2 recovery was `92/95`, severe
failures `14/12`, and all development gates passed. Raw cost `1079/1029` was
ineligible because recovery differed.

**Conclusion.** The known lattice failure was repaired without a new severe
regression. This is development evidence because the failure set was already
visible.

**Result.** `experiments/active_control/e3_closure_v2/result_summary.json`.

### E3-T1 single-action no-op transition holdout

**Problem.** The diagnosis lattice assumed every selected action applied its
declared effects.

**Frozen design.** Cross 128 states with `none` or one A1-A5 validated no-op for
768 cases. A no-op consumed cost and budget, marked the action used, validated
the attempted fields, and changed no atomic or derived value.

**Result.** Fixed/V2 recovery was `343/352`, severe failures `320/314`, fresh
success `343/352`; preregistered aggregate gates passed. Two V2-only severe cases
were then identified: mask 044 and 108 with A2 no-op.

**Conclusion.** The original aggregate claim remains passed under its frozen
rules, but production promotion was rejected. Aggregate safety can hide
case-level critical regressions.

**Result.** `experiments/active_control/e3_transition_holdout_v1/result_summary.json`.

### E3-M3 fault-aware minimax controller V3 development replay

**Problem.** Repair the two V2-only severe cases and add a non-compensating
case-level safety gate.

**Frozen change.** Maintain fault hypotheses over remaining single-use actions;
after validated success eliminate the selected action fault, and after validated
no-op exhaust the fault budget. Recursively minimize worst critical residuals,
severity-weighted burden and total residuals within remaining budget. Robust
ties use fixed-policy safety precedence, then nominal cost and action ID.

**Result.** On the revealed 768 cases, fixed/V3 recovery was `343/349`, severe
failures `320/314`, and treatment-only severe count `0`. The two known A2 cases
were repaired. Cost remained ineligible because recovery differed.

**Conclusion.** V3 satisfies its development target but the same revealed data
cannot confirm it.

**Result.** `experiments/active_control/e3_fault_aware_minimax_v3/result_summary.json`.

### E3-P1 independent partial-action-effect holdout

**Problem.** V3 had only seen full success and full no-op. A real action may
apply some atomic effects while omitting another, causing validation to observe
change without complete repair.

**Frozen design.** After V3 was frozen, cross 128 states with `none` and five
A1/A2 single-field omission variants for 768 new cases. Non-omitted effects
apply normally, derived fields recompute, and cost/budget/used/validation
semantics remain unchanged. Protocol SHA-256 was frozen before outcome.

**Result.** Fixed/V3 recovery was `417/425`, severe failures `254/246`, fresh
success `417/425`, and treatment-only severe count `0`. Recovery, aggregate
safety, case-level safety and freshness gates passed. Raw cost `6578/6520` was
ineligible because recovery differed.

**Conclusion.** Frozen V3 is confirmed on the new synthetic partial-effect
dimension. This does not cover fault timing, transient retry, multiple faults or
real executor side effects.

**Protocol/result.** `experiments/active_control/E3_PARTIAL_EFFECT_HOLDOUT_V1.json`
and `experiments/active_control/e3_partial_effect_holdout_v1/result_summary.json`.

### E2-N1 independent measurement nuisance/freshness holdout

**Problem.** E2-V2 assumed every selected measurement remained available and
fresh and did not receive conflicting or one-sided evidence.

**Pre-execution freeze.** Protocol SHA-256
`d69931aaadcceddd3abfe4ee72f549772bb4522c955ee400b7478b3fe522a28d`
fixed five base cases crossed with six variants: none, first-selected
unavailable, invalidated public seed, invalidated first reading, one-sided
unknown evidence, and fail-closed conflict. This produced 30 cases.

**Result.** Fixed/unknown-first V2 correct counts were `10/10`; critical errors
were `28/31`; false-success counts were `0/0`; fresh-correct counts were
`10/10`. The critical-error noninferiority gate failed, cost comparison was
forbidden, and the claim was false.

**Observed failure.** For `broken-runtime-handoff`, V2 selected the unique
critical derived measurement C6 first. When that result became unavailable,
invalidated or conflicted, no alternative measurement restored
`synthesis_ready`, creating three additional critical errors.

**Conclusion.** Unknown-first V2 is not robust to first-measurement evidence
failure. The failed holdout remains independent evidence against promotion.

**Artifacts.** Protocol above; result
`experiments/active_measurement/e2_nuisance_v1/result_summary.json`, SHA-256
`140aab763903f51426d3da6e00d888bac751f187083b679a534c71040f6dcc1e`.

### E2-D3 failure-exposure-aware V3 development replay

**Problem.** Repair E2-N1 without using nuisance identity or hidden truth.

**Frozen change.** Rank by unique critical failure exposure, highest unknown
severity, logical cost and measurement ID, recomputing after every measurement.

**Result.** On the revealed 30 cases, fixed/V3 correct counts were `10/13`,
critical errors `28/16`, false success `0/0`, and fresh correct `10/10`. All
development gates passed; cost remained ineligible because primary outcomes
differed.

**Conclusion.** V3 repairs the revealed first-measurement failure set, but is
post-failure development evidence only.

**Artifacts.** `experiments/active_measurement/E2_FAILURE_EXPOSURE_POLICY_V3.json`
and `experiments/active_measurement/e2_failure_exposure_v3/result_summary.json`.

### E2-T1 independent second/third measurement timing holdout

**Problem.** Test frozen V3 at previously unseen failure positions.

**Pre-execution freeze.** Protocol SHA-256
`593454b123651b78c9c572a21b233b8068a4296496a92fe88fc446a0835d41d7`
crossed five base cases with none, attempt-two/three unavailable and
attempt-two/three reading invalidation, for 25 cases.

**Result.** Fixed/V3 correct counts were `8/5`, critical errors `12/10`, false
success `0/0`, and fresh correct `5/5`. Accuracy noninferiority failed even
though aggregate critical errors improved. Cost was forbidden and the claim
was false.

**Conclusion.** The gate correctly rejected a non-compensating tradeoff: fewer
critical residuals do not authorize loss of complete diagnosis.

**Artifacts.** Protocol above; result
`experiments/active_measurement/e2_measurement_timing_v1/result_summary.json`,
SHA-256 `8df20b3ea2dcef44a35d17ccf0e17ce79c720e4c6435a57ecc57534994a04987`.

### E2-D4 guarded fixed-prefix V4 development replay

**Problem.** Repair both revealed E2 failures with the smallest policy change.

**Frozen change.** The first selected attempt uses fixed-v2 applicable order;
all later attempts use the exact V3 ranking. A failed first attempt still
consumes the prefix, cost and availability. The policy cannot read nuisance
variant, target attempt, hidden truth or fixture identity.

**Result.** On E2-N1, fixed/V4 both produced `10` correct, `28` critical errors,
`0` false success and `10` fresh correct at cost `380`. On E2-T1, fixed/V4
produced `8/8` correct, `12/2` critical errors, `0/0` false success and `5/5`
fresh correct at raw cost `310/310`. Both dataset-specific development gates
passed.

**Observed limitation.** V4 gives back V3's E2-N1 development gain (`13`
correct and `16` critical errors) to obtain baseline safety across both
revealed datasets. It is a fitted safety compromise, not a V3-dominating policy.

**Conclusion.** V4 is eligible for a new independent synthetic confirmation,
not for real-task shadow.

**Artifacts.** Protocol SHA-256
`0008b9f0878e4ba57e4f5a7fb5ee64b1516cecbbc0e43c26a546e6b7c1a7dea4`;
result SHA-256
`87366c9bb96803095c3aa888fa9872512d54716b22aa9583670c2e5edce85bda`.

### E2-C4 independent guarded V4 confirmation

**Problem.** V4 was fitted to two revealed failure sets whose five base
fixtures and C2/C3/C4 ordering had influenced the repair.

**Pre-execution freeze.** Protocol SHA-256
`0deec3c7dd1548ba443fbc0c19878f6e74fbeb8209ba7d070fd2dbcf8100d892`
bound a new three-case suite SHA-256
`828f110ee2df9e764afab2b1c2563385e7f1142d233fa6fc788977a6cc3389fc`
and nine variants covering attempts one through four, delayed/partial
invalidation, late conflict and one two-failure slice. The old five case IDs
were excluded.

**Result.** Fixed/V4 exact correct was `23/24`, critical errors `3/3`, false
success `0/0`, and fresh correct `14/16`. All four ordered gates passed. Raw
cost was `326/302`, but cost comparison was ineligible because quality outcomes
were unequal.

**Conclusion.** Frozen V4 is confirmed on a new synthetic suite and may enter
the final joint synthetic experiment. It remains synthetic and does not by
itself authorize real-task shadow.

**Artifacts.** Summary SHA-256
`9d387717937c699f77b2db64f14d3a16ed1d67afd1f902a2521a9c0a1005cda2`;
full 27-case execution SHA-256
`133738f0f75e5ba2095e5347e13a0ac5873ef108bf77e524b5bd5beb9b242d10`.

### E3-R1 positional fault-timing and optional-retry holdout

**Problem.** Extend E3 V3 beyond one fixed no-op and partial action effects.

**Pre-execution freeze.** Protocol SHA-256
`41506f9e7d91a9eeb3d7d233b84ac245d847c77481f30e94ec924e9e553cae0a`
crossed 128 states with none and permanent/transient no-op at attempts one,
two and three, producing 896 cases. Both arms shared fault position and kind;
the fault attached to the action each arm selected at that position.

**Result.** Fixed/V3 recovery was `291/299`, severe failures `474/462`, fresh
success `291/299`, and treatment-only severe count `0`. All frozen gates passed;
cost was ineligible.

**Observed limitations.** The adapter allowed an optional same-action retry; it
did not force retry. Missing receipts and delayed validation were not generated,
and freshness was therefore not challenged on those dimensions. Aggregate
summaries were persisted, but full per-case attempt/receipt/EvidenceBundle
artifacts were not.

**Conclusion.** Frozen V3 passes a narrow positional no-op/retry holdout. This
does not complete the originally planned E3-R1 scope and does not satisfy the
real-environment evidence gate.

**Artifacts.** Protocol above; result
`experiments/active_control/e3_fault_timing_retry_v1/result_summary.json`,
SHA-256 `41a9533c777749e44182d2cd493e9402284480f6c079180a57bb2157cc26cd06`.

### E3-R2 receipt and delayed-validation holdout

**Problem.** E3-R1 could not observe missing receipts or validation that
arrived later or never arrived, and it did not persist attempt-level evidence.

**Pre-execution freeze.** Protocol SHA-256
`62cb4c9ff546130e065a86b409281f81eef8e5529ccf88e192880d330ddb3d3b`
crossed eight diagnosis masks with seven receipt/validation variants for 56
cases. Missing receipts withheld effects from trusted state; one-step delay used
a separate checkpoint; late-missing validation blocked success. A typed
per-attempt ledger was required.

**Result.** Fixed/V3 recovery was `29/38`, severe failures `19/11`, fresh success
`24/30`, false-success stops `0/0`, and evidence-blocked outcomes `5/8`.
Recovery, aggregate safety, case-level safety and false-success gates passed,
but the frozen freshness gate failed; the total gate and claim were false.

**Conclusion.** The result is preserved as a failed holdout. It demonstrates
fail-closed stopping and an auditable 284-entry attempt ledger, but does not
complete E3's synthetic exit gate. The exact relationship between physical
recovery, blocked success and the preregistered freshness estimand must be
resolved in a new version, followed by a new independent confirmation.

**Artifacts.** Summary SHA-256
`b1ee1465ea1a3d4f88ed04b0002fc5c556c5266dedda7e1a202940866f45b648`;
ledger SHA-256 stored in the result is
`81adbf8c12f4f96c3ac2c02625af991cf2c95bf5777cbec928923e21651e416d`.

### E3-D4 receipt reconciliation adapter V4 development replay

**Problem.** Close the 13 recovered-but-evidence-blocked outcomes exposed by
E3-R2 without changing V3 action selection or declaring missing evidence valid.

**Frozen change.** Protocol SHA-256
`a605b0decf6e2f8c3d63d0174322e796b59a5e096e767764357f31f1bbd2399f`
reuses the same idempotency key for same-action retry, accepts reconciliation
only from a successful receipt plus exact full-field validation, and permits at
most one zero-cost terminal validation checkpoint that does not consume action
budget. Atomic effects must be idempotent assignments.

**Result.** On the revealed 56-case E3-R2 set, fixed/V3 recovery remained
`29/38`, severe failures `19/11`, action costs `474/416`, and false success
`0/0`. Fresh success became `29/38` and evidence-blocked became `0/0`; all
development gates passed. The reconciliation ledger contains 35 entries.

**Conclusion.** V4 closes the known evidence gap without changing action
outcomes or costs. This is post-failure development evidence and requires a new
receipt/validation holdout before E3 can enter the final joint gate.

**Artifacts.** Result SHA-256
`08870807fc576785e6fd3c4ebdee72706ff5a39c0b6b107333790912fb444bb7`;
ledger SHA-256
`2f90212ec7181c6d560eff44395d51f59d0a468fa8a839ecf3eff7e4c42b3f63`.

### E3-C4 independent receipt reconciliation confirmation

**Problem.** E3-D4 was fitted to the exact missing-receipt and late-validation
cases revealed by E3-R2.

**Pre-execution freeze.** Protocol SHA-256
`e199a5cb30a90999266de95404f2e20afa9fb03ebbe56d2b8d662dd885781525`
bound the V4 protocol, development result, reconciliation ledger and adapter
fingerprint, then used new masks and receipt/validation combinations.

**Recorded output.** Fixed/V3 recovery was `49/49`, severe failures `7/7`, fresh
success `49/49`, false success `0/0`, and treatment-only severe count `0`; the
runner reported cost `423/415`, delta `-8`. These numbers are retained for
audit, but are not eligible holdout results.

**Post-run invalidation.** Independent semantic audit found that the runner
implemented its own reconciliation path instead of calling the frozen V4
adapter, generated fresh validation fields from the variant label, and created
two terminal checkpoints for variants even though V4 freezes
`max_terminal_checkpoints=1`. Two nominal receipt variants also mapped to the
same underlying fault, and the ledger contained no `idempotent_retry_receipt`
entry. Byte replay and hash binding passed, but they only reproduced this
invalid runner. E3-C4 V1 is therefore rejected, the synthetic exit gate remains
open, and the final joint holdout is not authorized by this result.

**Artifacts.** Result SHA-256
`df7fe6e754c900a304f2a8c039ccb9530c6c7167d7b225ee4a4715360b157112`;
246-entry attempt ledger SHA-256
`e6ac9304acbbb18c13925d8d4ae5e35a3eddc4f2833c6c22512fb017d6a5b7e1`.

### E3-R2 receipt and delayed-validation holdout V2

**Pre-execution freeze.** V2 preserved E3-R1 and froze 8 diagnosis masks crossed
with none, missing receipt at attempts one through three, one-step validation
delay at attempts one/two, and final-attempt missing validation. Both arms share
fault position and kind; the fault attaches to the action selected by that arm.

**Result.** Fixed/V3 recovery was `29/38`, severe failures `19/11`, fresh
success `24/30`, and false-success stops `0/0`. The adapter safely blocked `5/8`
physically recovered cases with incomplete evidence. Consequently the freshness
and overall holdout gates failed, and cost comparison was forbidden.

**Evidence artifact.** All 284 action attempts and validation checkpoints are
stored in a strict typed ledger. The summary binds its raw-byte SHA-256
`81adbf8c12f4f96c3ac2c02625af991cf2c95bf5777cbec928923e21651e416d`.

**Conclusion.** The false-stop guard works, but the frozen receipt/validation
policy does not restore fresh evidence in all recovered cases. Production
promotion remains blocked; the freshness gate must not be relaxed.

### E3-D5 shared receipt reconciliation adapter V5 development

**Problem.** E3-C4 V1 bypassed the advertised V4 adapter, constructed freshness
from variant labels, exceeded the one-checkpoint limit, and never exercised the
retry reconciliation branch.

**Pre-execution freeze.** Protocol SHA-256
`c3d79d62d84238d9dafc91c7a1c1d0507b8260ae1276d7525b01b7cb336bd965`
binds the V4 protocol/result/ledger and all three invalid E3-C4 V1 artifacts.
The target is post-failure development only; `hypothesis_evidence_eligible=false`.

**Design.** Development and confirmation share
`reconcile-case-evidence-v5`. It consumes typed attempts and at most one external
terminal observation, requires exact field coverage and same-action retry
identity, fails closed on stale/partial/missing observations, and validates
every source against the same case and arm. Action selection is unchanged.

**Result.** On the revealed 56-case E3-R2 set, fixed/V3 recovery remained
`29/38`, severe `19/11`, cost `474/416`, false success `0/0`, and fresh success
was `29/38`. All development gates passed. The 31 ledger entries comprise 24
retry and 7 terminal reconciliations.

**Observed failures.** No new action-policy or false-success regression was
observed. The data were already revealed and used to define V5.

**Conclusion.** V5 repairs the implementation boundary that invalidated V1 and
exercises both mechanisms. This is development evidence only.

**Limitations and next gate.** The adapter still uses synthetic attempts and
observations, not signed receipts, asynchronous delivery or production
`EvidenceBundle` objects. It required a new independent confirmation.

**Artifacts.** Result SHA-256
`ac2564829c04d040315c1b42116a3770f3f854578d459db938ac5d6557d62409`;
ledger SHA-256
`338cc01792e10759d6ff91894aea5ae0cf940b572a1c70a7f2646626932a4b1a`;
adapter fingerprint
`d6c49efe642ed57c6a7b2b8bcc1cc28fd50dfba21ebae1ccbc219fb592d72efc`.

### E3-C5 receipt reconciliation confirmation V2

**Problem.** Confirm shared V5 on unseen masks and external observation outcomes
without reproducing E3-C4 V1's self-certified freshness.

**Pre-execution freeze.** Protocol SHA-256
`30f630a50f13575599e07158b1a91dbc36cd139ede996139be4435b4319fffe3`
binds V5 protocol/result/ledger/fingerprint. It freezes masks
`11,12,17,18,31,63,95,126`, seven distinct variants, four positive and three
negative variants, one terminal-observation maximum, and ordered gates.

**Design.** The 56 cases per arm use the shared V5 function. Positive variants
cover no fault, missing-receipt retry, fresh terminal receipt reconciliation and
fresh delayed-validation closure. Negative variants supply stale, partial or
missing observations and must remain blocked. Attempts, 80 observations and
reconciliation provenance are persisted.

**Result.** Fixed/V3 recovery was `33/33`, severe `18/18`, positive fresh success
`22/22`, false success `0/0`, and treatment-only severe `0`. All 48 negative
case-arm probes failed closed. All provenance, freshness and safety gates passed.
Equal primary outcomes made cost eligible: `584/564`, delta `-20`. The 40
reconciliations split into 30 retry and 10 terminal entries.

**Observed failures.** Eleven recovered cases per arm were intentionally not
fresh under negative probes; none was reported as success. The 18 severe outcomes
per arm were matched, with no treatment-only severe case.

**Conclusion.** V5 passes this independent synthetic receipt/validation
confirmation. This closes the E3 component prerequisite for a new joint
synthetic holdout, not the full E2 x E3 gate.

**Limitations and next gate.** The holdout is deterministic and synthetic;
`hypothesis_evidence_eligible=false`. It does not authorize production Controller
integration, real tools, shadow execution or real-environment claims.

**Artifacts.** Result SHA-256
`a804ff367638a18b0ff08f6815af23680867dc0d25084fbc0b0d125324cd1ca9`;
296-attempt/80-observation ledger SHA-256
`fb132b126ae17afdbe348d612003c00299c31ff4c8949f5939a3a00fd065bcc8`.

### J-X1 invalid joint synthetic-exit V1 diagnostic

**Problem.** The first joint draft crossed E2 measurement nuisance with E3
receipt nuisance before E3-C4 survived semantic audit.

**Pre-execution freeze.** None. Protocol SHA-256
`51c82dd4c1ea0191c21ffe22865fdc61894374ab6793ec2e9691a2c91444362d`
records `pre_execution_frozen=false`, `holdout_evidence_eligible=false`, and an
invalid E3-C4 prerequisite.

**Design.** The blocked draft described three bases x four measurement x four
action variants, 48 prospective cases. The runner failed closed before joint
execution and persisted no joint result. A separate post-invalid measurement-only
diagnostic replayed 12 base x measurement cases solely to locate E2 risk.

**Result.** No joint result exists. The diagnostic found fixed/V4 correct
`12/12`, false success `0/0`, fresh correct `11/10`, and cost `95/101`.

**Observed failures.** `attempt-5-unavailable` affects V4's longer path after
fixed has stopped. This is failure localization, not a holdout estimate.

**Conclusion.** J-X1 is rejected and retained only as an audit artifact and E2
development input.

**Limitations and next gate.** No E2 x E3 comparison was executed. The revealed
suite cannot be reused as an independent joint holdout.

**Artifacts.** Base-suite SHA-256
`3049ac0a480759e1c27d68fe2e9c1ba89643045abba5f11b56c568ffe43650da`;
diagnostic result SHA-256
`f42334ea769786a145ca867e8edd25fca4f3066fc98ab76c7e8d1c23632223aa`.

### E2-D5 balanced fixed-prefix V5 development

**Problem.** Guarded V4's one-attempt fixed prefix followed a longer adaptive
path, so attempt-5 unavailability reduced fresh correctness from fixed's 11 to
10 on the invalid joint diagnostic.

**Pre-execution freeze.** Protocol SHA-256
`1aadda15f531fcabfb8697af382690624fdca8420a4b987f282a1b206f85089b`
binds V4 development/confirmation and the invalid joint draft/suite/diagnostic.
Four revealed datasets must pass independently; this is post-failure development.

**Design.** V5 uses the first three fixed-v2 applicable measurements, counts a
failed attempt against the prefix, recomputes after each measurement, then uses
failure-exposure-aware V3 as the suffix. Fixed-v2 is the comparator.

**Result.** All dataset gates passed. Fixed/V5 results were: nuisance correct
`10/10`, critical `28/28`, fresh `10/10`; timing correct `8/8`, critical `12/12`,
fresh `5/5`; prior confirmation correct `23/24`, critical `3/2`, fresh `14/16`;
invalid joint diagnostic correct `12/12`, critical `0/0`, fresh `11/12`. False
success was zero in every arm and dataset.

**Observed failures.** No per-dataset gate remained failed. All four datasets
were revealed before V5 was selected, so improvements are not independent.

**Conclusion.** V5 meets its cross-revealed-set development target and repairs
the known V4 long-path freshness failure.

**Limitations and next gate.** This is not an independent E2 estimate and is not
a joint result. V5 must remain frozen in a new holdout; real provider/tool cost
and task distributions remain untested.

**Artifacts.** Result SHA-256
`93388fadb6816272151c9d19093f91c3d3399fd870d31313a1e4884b62de303b`;
policy fingerprint
`8f4d3237bd267b324f7842c7895d69b48c12affa634c14fce2dd5b1851eae5a2`.

### J-N2 joint E2 x E3 nuisance holdout V2

**Problem.** E2 V5 and E3 V5/C5 had only separate development/confirmation
evidence. The final synthetic gate had to cross long-horizon measurement faults
with receipt/validation faults without allowing E3 recovery to conceal an E2
diagnosis regression.

**Rejected pre-run revisions.** Protocol SHA-256 `f00d7f...a534b` scheduled an
attempt-3 terminal fault that none of the original bases could reach. After the
action schedule was corrected, SHA-256 `5df117...c8ce` still used bases that
finished measurement in three attempts, so all attempt-5/6 measurement faults
had zero exposure. Its in-memory runner check was rejected and no formal result
was persisted. These failures caused stricter typed variants, exact binding and
gate validators, new long-horizon bases, and per-arm exposure gates; they are not
counted as holdout evidence.

**Pre-execution freeze.** Final protocol SHA-256
`285843d897c15adf1530fccd0945c43d2e29cb52cdbe0614ddff6a8006747721`
binds suite SHA-256
`c64dc17262570b192520e145fd0ca0f734b7f44c6938a6268bfdd2ac5ea71260`,
E2 V5, E3 V5/C5, fixed/V3 policies and the shared V5 adapter. Three new bases,
four measurement variants and five action variants produce 60 cases per arm.

**Design.** Both arms receive the same attempt-position fault schedule. Every
base/arm reaches at least six measurements; V5 diverges only after its three-step
fixed prefix. Action variants cover same-action retry at attempts one/two, one
fresh terminal validation and one stale negative probe. External observations
are built from the executor attempt record before adapter invocation, never from
the adapter's required-field output. Ordered non-compensating gates separately
cover E2 accuracy, critical error, diagnosis false success and measurement
freshness, then E3 false success, recovery, safety and positive freshness.

**Result.** Control/treatment diagnosis correct was `60/60`, critical errors
`0/0`, diagnosis false success `0/0`, measurement fresh `60/60`, recovered
`60/60`, severe failures `0/0`, final false success `0/0`, and positive action
freshness `48/48`. Measurement-fault exposure was `45/45`; retry reconciliation
`20/20`; terminal reconciliation `12/12`; stale probes `12/12`. Every primary
gate passed. Equal primary outcomes made logical cost eligible: `1416/1416`,
delta `0`.

**Evidence audit.** The artifact contains 240 attempt entries, 48 external
observations, 64 reconciliation entries and exactly 120 same-run
`EvidenceBundle` objects. Fresh replay matched the persisted summary and artifact
byte-for-byte. Every bundle's four resolved references matched its measurement,
action, observation and reconciliation payload; maximum observations per
case-arm was one.

**Conclusion.** The frozen E2 V5/E3 V3/V5 composition passes the final planned
synthetic nuisance gate. This supports moving to a read-only real-task shadow
stage, not production mutation or sandbox execution.

**Limitations and next gate.** All tasks, receipts, observations and costs are
deterministic synthetic constructs; `hypothesis_evidence_eligible=false` remains.
Real shadow must deny writes, commands, tool-originated network calls and project mutation, and
must first validate recorder completeness and provider/model conformance.

**Artifacts.** Result summary SHA-256
`81c05683c005ce550c0878455436a69b9003e59a020577c38cdf4a86add6e310`;
full execution artifact SHA-256
`53425cde79b7a73dc25e74755239a3985fbaea7d2d056200931c7ca2e9d978c1`.

## 5. Real-shadow experiments after synthetic exit

J-N2 made only a read-only real-task shadow eligible. The following entries are
the exploratory provider runs performed after that decision. They are not
synthetic experiments, are not E2/E3 treatment estimates, and do not authorize
E3 side effects. A local-model download was not required.

### RTS-X1 exploratory real-task shadow (first provider sample)

**Problem.** The synthetic J-N2 gate established only deterministic contract
and evidence behavior. It did not establish that a real provider can answer
repository tasks under the same read-only boundary, nor that malformed,
partial, or slow provider responses are retained without silently becoming a
success.

**Pre-execution freeze.** The run loaded protocol SHA-256
`771dbe993e9c383cadeb8981b891db68bf72c1cd2781aefc5a875009c9c21505`, public
suite SHA-256 `8bf8bb615e76a77c5ade790c3764ae669adaf4941c729108540f94966071df0f`,
hidden oracle SHA-256
`7b70cf584a9d7aa9bf7400051b804d913dff3dc008d93df453f1bb99af913444`, and
endpoint fingerprint
`7e80a84f6cde6688fae981c53a82d5cdf260066ee7b0d38a2dcb7c665b4289c0` for
`openai-compatible / https://api.deepseek.com / deepseek-v4-flash`.
The source snapshot was loaded before this ledger entry was appended; its
stage-boundary source hash was
`1351d1c11df618eca59fa3f09ec7942c57a5b84ddb7b5358c6aea19f89b629cf`.

**Design.** Three repository tasks were run in four frozen modes
(`single_pass`, `free_loop`, `fixed_expert`, `active_iteration`), with one
provider JSON attempt per cell, no cache, no tools, no commands, no writes,
no tool-originated network, and provider transport network as the sole network
permission. Each cell captured request/raw response/evidence claims/receipt/
oracle hash/verdict in a same-run EvidenceBundle and replayed it offline.

**Result.** The matrix completed all 12 cells. Objective success was `1/12`;
complete provider-side records were side-effect-free `12/12`; repository
before/after fingerprint was identical; the shadow gate was `false`;
`production_mutation_authorized=false`; and
`hypothesis_evidence_eligible=false`. Per-cell outcome:

| Task | single_pass | free_loop | fixed_expert | active_iteration |
| --- | --- | --- | --- | --- |
| stage-boundary | provider/parse failure | provider/parse failure | provider/parse failure | provider/parse failure |
| joint-result | provider/parse failure | pass | schema/evidence failure | provider/parse failure |
| engineering-boundary | provider/parse failure | answers match, evidence invalid | provider/parse failure | answers match, evidence invalid |

Provider outputs commonly exceeded the frozen response window or used an
evidence key (`verbatim_quote`) different from the frozen contract (`quote`).
The runner retained raw-response hashes and continued the matrix; no failure
was promoted to success. The first exploratory preflight request was manually
interrupted after the endpoint exceeded the deadline and is not included in
the 12-cell result.

**Conclusion.** RTS-X1 validates the provider boundary, source isolation,
failure continuation, and EvidenceBundle replay path. It does **not** validate
E2/E3 effect, general task reliability, or entry to a real executor. The
formal real-task gate remains blocked.

**Limitations and next gate.** Before any formal real-task estimate, freeze a
provider-compatible JSON/evidence contract (or add a deterministic adapter),
define provider timeout/partial-response handling, add independent quote-to-
answer checks, and pre-register task sampling/repeats/stopping rules. A local
8B download is not an entry condition and no production Controller is
authorized.

**Artifacts.** Result summary and execution were persisted outside the
repository at `/tmp/openpilot_real_task_shadow_v1_output/` to preserve the
no-project-mutation boundary. Summary SHA-256
`0bcf8c7002808d7d172e520c5902bd0d52b517f689422a6264729d55e9f30ac5`; full
execution SHA-256
`ca18170161c1366b9abb2de75b6bbb429c90f6a08b8b63e32faeec27ec9d80e8`.

### RTS-X2 provider-compatible response and strict quote development

**Problem.** RTS-X1 had nine provider/parse failures under a 512-token response
budget and unstable evidence field spelling. Its free-text quote contract also
accepted any source substring without independently tying the quote to the
answer.

**Pre-execution freeze.** Protocol/public/hidden SHA-256 were respectively
`308180a994e2598d0368b9fc44625682723a3d9837773bcc2942d5181e596415`,
`dd940166112c8fb437884d924a0efe91059cfb3b631fe02bbd53a6b581cef2a0`, and
`23e26d9724e6569e7973160cc53fbb65275443bbd584ad6fddcbfb2367a5c2b7`.
The protocol retained the same endpoint and four modes, raised the response
budget to 2048 tokens and the hard deadline to 60 seconds, accepted only the
pre-registered `verbatim_quote -> quote` alias, and required each quote to match
a hidden per-answer allowlist.

**Result.** All 12 cells returned schema-valid output and all 12 answer maps
matched the hidden oracle. Provider/parse failure fell from `9/12` to `0/12`.
The joint-result task passed strict evidence in all four modes; stage-boundary
and engineering-boundary answers were correct but their quotes differed by
Markdown backticks, capitalization, punctuation or line wrapping, so only
`4/12` cells met the non-compensating objective gate. The shadow gate remained
`false`; side-effect-free was `12/12`; repository before/after fingerprint was
identical.

**Conclusion.** X2 repairs the response-budget and field-alias failures, but
rejects free-text verbatim quotes as a stable provider contract. The observed
format variants are not added post hoc to the hidden allowlist and X2 is not
reclassified as a pass.

**Limitations and next gate.** The next development protocol must replace
free-text quote generation with public evidence candidate IDs and a hidden
answer-to-candidate mapping, then confirm that contract on unseen tasks. X2 is
still a three-task harness study, not a formal real-task effect estimate or
real-executor entry gate.

**Artifacts.** Repository-external result directory:
`/tmp/openpilot_real_task_shadow_v2_output/`. Summary SHA-256
`25e23a00fd5134ecb0b54662dca452d1d244d720231dd27a6f2d466c0b8f4d38`;
execution SHA-256
`8f624524d932d848586c726e190f0218c1570381591ea3eaa8b5c6cffacf4d16`.

### RTS-X3 unseen candidate-evidence confirmation

**Problem.** X2 showed that free-text quote generation is not a stable provider
interface even when all answers are correct. Expanding its hidden quote
allowlist after observing outputs would invalidate the gate.

**Pre-execution freeze.** Protocol/public/hidden SHA-256 were
`c143455f5237fc17665cfceb1e92f15aa4b88fab8e784135c18de69273d55d01`,
`b93f038b597e27d9cd3ef5f83923ea30181481143a1d1a5ec76995247e2f4bf8`, and
`a4813d5cb1ef7eb4d06bfb9f38f85ee4cdf2e1f128c7c761f5d83cb46dee9ef5`.
Three unseen tasks covered permission metadata, architecture scale boundaries,
and LLM configuration defaults. Public inputs exposed exact evidence candidates
plus distractors; only the hidden oracle mapped answer keys to correct candidate
IDs.

**Result.** All 12 task/mode cells had correct answers, correct hidden evidence
candidate selection, valid EvidenceBundles, zero provider/schema failures, and
zero observed side effects. Repository before/after fingerprint was identical.
The X3 shadow gate passed `12/12`; `production_mutation_authorized=false` and
`hypothesis_evidence_eligible=false` remain.

**Conclusion.** X3 closes the exploratory real-provider harness and evidence-
selection interface gate. It supports starting a pre-registered formal
real-task shadow sample. It does not establish an E2/E3 treatment effect and
does not authorize a real executor, writes, commands, or tool network.

**Limitations and next gate.** Formal shadow still needs task-source sampling,
strata, repeated independent provider runs, stopping rules and comparative
mode metrics. Only after that non-compensating gate passes may disposable,
rollback-protected sandbox execution be considered.

**Artifacts.** `/tmp/openpilot_real_task_shadow_v3_output/`; summary SHA-256
`5a2b911a266b3800f5b8684e0e2d0926cc12aa5d37e82801c8365087025b453d`;
execution SHA-256
`f22ab7e51c197c7df2995ebd68590bfb8d36435f4fc9c4bbb9f157c841de3e84`.

### RT-P0 strict-read-only trajectory preflight

**Problem.** J-N2 and RTS-X3 did not prove that the normal Autopilot tool path
was genuinely read-only. Registry filtering could be reversed by later
registration, `multi_file_reader` generated indexes and `sketch.json`, and an
unavailable command proposal was represented as a recoverable unknown tool.

**Pre-execution freeze.** No provider execution or trajectory outcome was
authorized. The task pool SHA-256 is
`3016a63614250e28cf7ac31b91e3297e95bba67ab2b97534a1cfbe9a77fd42bf`;
the final protocol SHA-256 is
`27cd32297b5d99dae1042bd9575bafff937beb285071be492acda0f163ba2a49`.
The pool contains four expected-success and four required expected-failure
strata, repeated three times. Non-compensating eligibility requires `24`
terminal runs, `8` unique tasks, `12` successes, `12` failures, `24` complete
event runs and task dominance no greater than `0.125`.

**Rejected pre-run draft.** A shared-worktree draft weakened those thresholds
to `4/4/20/0.35` and set `provider_execution_authorized=true`. It was rejected
before any provider call or outcome generation because it contradicted the
balanced 24-run design and the synthetic-only scope of this stage.

**Design.** The strict registry permanently exposes only `file_reader` and
`multi_file_reader`; later `allow_override=True` cannot register
`command_executor`. The strict multi-reader suppresses directory sketches and
file indexes. Disabled decision needs raise terminal `ToolNotAllowed` before
executor dispatch. Reference search, contextual capability cards and iterative
improvement are disabled; memory writes are isolated under the diagnostics
recorder rather than the repository task root.

**Result.** The focused protocol/boundary suite passed `11/11`; the joint
synthetic/shadow gate suite passed `47/47`; the broader affected runtime suite
passed `264/264`. Repository diff validation passed. No provider, command,
tool-network or project mutation action was executed by this experiment.
The final denial/event-chain audit passed `9/9`: both `tool_denied` and executed
`tool_called + outcome` terminal branches are complete, and repository
fingerprint checks remain stable under read-only execution.

**Conclusion.** The offline strict-read-only boundary is now strong enough to
enter a provider-backed real-trajectory shadow run. This is a harness result,
not an E2/E3 real-task treatment estimate, and it authorizes only the next
strictly read-only stage.

**Limitations and next gate.** The frozen preflight itself keeps
`provider_execution_authorized=false`. Before execution, a provider-run protocol
must bind the chosen endpoint/model fingerprint while preserving the task pool,
24-run schedule, thresholds and permission envelope. Sandbox mutation remains
out of scope.

**Artifacts.** `experiments/real_trajectory_shadow/TASK_POOL_V1.json`,
`experiments/real_trajectory_shadow/REAL_TRAJECTORY_SHADOW_PROTOCOL_V1.json`,
`Code/tests/test_strict_read_only_boundary.py`, and
`Code/tests/test_real_trajectory_shadow_protocol.py`.

### RT-R1 provider-backed real-trajectory corpus acquisition

**Problem.** RT-P0 proved the offline permission boundary, but the existing real
trajectory corpus had only 12 terminal runs, two unique task texts, one success,
10 complete event chains and `0.9167` single-task dominance. It could not support
formal E2/E3 shadow analysis.

**Pre-execution freeze.** The executable collection revision bound task-pool
SHA-256 `3016a63614250e28cf7ac31b91e3297e95bba67ab2b97534a1cfbe9a77fd42bf`
and protocol SHA-256
`a8e187b0d20ad877e53079f3b9061604456d4b1c3994726f0038eb69da90423f`.
Eight task texts were each scheduled three times. Non-compensating corpus gates
were `terminal >= 24`, `unique >= 8`, `success >= 4`, `failed >= 4`, complete
event chains `>= 20`, and maximum task dominance `<= 0.35`. Provider transport
was authorized; production mutation remained forbidden.

**Invalid trials.** Two complete 24-run acquisitions are retained as invalid.
In the first, repository fingerprint changed from `fb82dceb...cb9` to
`73d38a88...4f63`; in the second it changed from `73d38a88...4f63` to
`f4222f28...e1c6`. The model tool registry stayed read-only, but a concurrently
finishing subagent changed protocol/documentation files. Neither result was
post-hoc exempted or used for the gate.

**Result.** The final frozen acquisition used repository fingerprint
`a89ab95a753c7aa8adba151b187d2756ad632473ac615727cc401cd935cabdf2`
before and after. It produced 24 terminal runs across eight unique task texts:
four successes, 20 failures, 22 complete event chains, and `0.125` maximum task
dominance. All six gates passed and corpus SHA-256 is
`c7038b71dacdcb66be742160935bfaa9e35ed3de38b1d5816e758b4aba0eec4d`.
The two incomplete runs were failed experiment-audit tasks with complete
task/provider/terminal events but no tool call or policy-denial event; the
pre-registered allowance of four incomplete chains kept them in the denominator.

**Permission evidence.** The run observed 111 responses from
`openai-compatible/deepseek-v4-flash` and 201,540 total tokens. Executed tools
were only `file_reader` (38) and no-index `multi_file_reader` (26). The registry
denied `command_executor` 11 times, `file_writer` twice, and `readme_tool` once
before executor dispatch. No terminal event claimed a modified file, and the
repository fingerprint remained unchanged.

**Conclusion.** The real-provider trajectory corpus is eligible as input to a
formal E2 diagnostic shadow followed by an E3 action-proposal shadow. This is a
corpus-acquisition gate, not an E2/E3 treatment result. It does not authorize
commands, writes, tool network, sandbox mutation, or production integration.

**Limitations and next gate.** The collection protocol records the observed
provider/model in the audit result but does not pre-register an endpoint
fingerprint as a compared experimental factor. Formal E2/E3 shadow protocols
must bind their provider/model/prompt/evaluator identities, select measurement
packets without outcome leakage, preserve all 24 runs including failures, and
keep `hypothesis_evidence_eligible=false` until their own gates pass.

**Artifacts.** Durable aggregate:
`experiments/real_trajectory_shadow/RESULT_SUMMARY_V1.json` (SHA-256
`cb2c62f84112dc9a98b9b2d1982f21db0c529e0ff4f607eaff0b7919a76ba60d`). External full
corpus: `/tmp/openpilot_real_trajectory_final_v1.Z1Smr7`; eligibility SHA-256
`668bf7742d7554c4526e4c0ae3acab1e137de76a7acea11811f8618b283093e1`;
audit-summary SHA-256
`ca162d2f7a1a62c17636bb95b5e3b82b0bd55c5daf8fd97194f1df0ab8fd507f`.
The final repository test suite passed `1105/1105` with zero failures or skips.

### RT-E2-S0 blind real-trajectory measurement-packet smoke

**Problem.** RT-R1 made a real-provider corpus eligible, but directly exposing
`run.final_status`, the task-stratum expected outcome, or terminal payloads to
the E2 policy would reduce diagnosis to answer leakage. Provider, model, prompt,
policy and evaluator identity also had to be bound before any comparison.

**Pre-execution freeze.** Protocol SHA-256 is
`7d2d558f81992bd9c56fb17434944cc5cd2df06afa3996d760635ac1e71b0593`.
It binds corpus `c7038b71...eec4d`, RT-R1 protocol/result/task-pool bytes,
`openai-compatible/deepseek-v4-flash`, all 111 source request artifacts as
prompt bundle `518eb3f1...9bbd1`, balanced-prefix V5 protocol bytes, and
deterministic evaluator fingerprint `8100c94f...3cf2`. Selection is the lowest
run ID per task, fixed before reading the oracle.

**Design.** Build one outcome-blind packet for each of eight tasks. Policy
context contains task/category hashes and non-terminal event identities only.
The four frozen outcome sources are forbidden. C1-C8 expose only named
measurement opportunities over intake, routing, provider provenance, tool
boundary/outcomes, event/artifact chains and terminal presence. This smoke does
not execute either arm or call a provider.

**Result.** Eight packets covered eight tasks. Frozen task expectations were
`4 success / 4 failed`, while observed RT-R1 terminal outcomes were retained
separately as `1 success / 7 failed`; packet-set SHA-256 is
`ac2979fe...e0d28`. All packets had eight measurement opportunities. Outcome
exposure, provider execution and mutation were `false/0/0`; the structural gate
passed.

**Conclusion.** The RT-R1 corpus can be transformed into reproducible,
outcome-blind E2 inputs. This is a harness result only. It is explicitly
`hypothesis_evidence_eligible=false` and is not an E2 treatment estimate.

**Limitations and next gate.** Before the 24-run paired comparison, freeze the
per-measurement checker outcomes, field severities, missing-evidence semantics,
final diagnosis rule, freshness rule and non-compensating gates. A checker may
use hidden terminal truth only in the independent scoring path, never in policy
state. E3 remains blocked.

**Artifacts.** `REAL_TRAJECTORY_E2_SMOKE_PROTOCOL_V1.json`,
`E2_SMOKE_RESULT_V1.json`, strict metadata/runner modules and
`Code/tests/test_real_trajectory_e2_shadow.py`.

### RT-E2-X1 rejected formal E2 V1 draft

**Problem.** C6/C7 were named full/indexed freshness, while each EvidenceBundle
bound only one concrete event or artifact. Replay proved that record, not the
broader whole-chain proposition.

**Observed draft.** Both arms diagnosed outcome `24/24` with logical cost
`264 -> 216`. These numbers are retained only for audit and excluded from
formal evidence. V1 was rejected before result persistence; V2 binds its
protocol and invalid-trial record.

### RT-E2-R2 formal paired real-trajectory E2 shadow V2

**Problem.** Test whether frozen balanced-prefix V5 preserves diagnosis of
RT-R1 terminal outcomes and expected-outcome alignment with fewer logical
measurements than fixed-v2. Correct fail-closed and unexpected failures must be
separate.

**Pre-execution freeze.** Protocol SHA-256 is
`f8e7532c86cff5a18e20675f94f940a83b75a2fb0ea095b4cd0d1e238a3de7a1`.
It binds RT-R1 corpus/provider/model/prompts, S0, V5, and invalid V1 bytes. V2
narrows freshness to existence of a selected same-run, content-bound,
replayable evidence route. All 24 runs remain in both denominators.

**Design.** Both policies receive identical hidden-outcome-free cases. Eight
fields cover intake, routing, provider provenance, read-only boundary,
pre-terminal failure/completion evidence, replayable evidence and terminal
marker presence. Independent scoring compares predicted terminal outcome,
expected task outcome and alignment. Cost is compared only after five primary
noncompensating gates.

**Result.** Expected strata were `12 success / 12 failed`; observed strata were
`4 success / 20 failed`, with `16 aligned / 8 misaligned`. Both arms produced
outcome/alignment/fresh `24/24`, severe error `0/0` and false success `0/0`.
Primary outcomes were equal, so cost was eligible: `264 -> 216`, delta `-48`
(`-18.2%`). Provider calls and mutation were zero.

**Evidence.** The full artifact contains 24 fixed results, 24 treatment results
and 48 replayable EvidenceBundles. Execution SHA-256 is
`b70e585d...a6d99`; aggregate result SHA-256 is `4120b475...e50b8`.

**Conclusion.** E2 V5 passes this read-only real-trajectory diagnostic shadow
and authorizes E3 action-proposal shadow. It does not establish task execution
improvement and does not authorize proposal dispatch, commands, writes, tool
network, sandbox mutation or production Controller integration.

**Artifacts.** `REAL_TRAJECTORY_E2_FORMAL_PROTOCOL_V2.json`,
`E2_FORMAL_RESULT_V2.json`, `E2_FORMAL_EXECUTION_V2.json`,
`e2_formal_v2/execution.json.gz`, formal metadata/runner and tests.

### RT-E3-D1 mechanism-aware action-proposal development replay

**Problem.** Status alignment alone hid failure-mechanism differences. Durable
events showed 12 expected-failure trajectories reached policy denial, while
eight expected-success failures required intervention: six empty-decomposition
replans and two reroutes away from forbidden readers.

**Pre-execution freeze.** Protocol SHA-256 is
`7f372bf6763eac34b656c9fa801aac977ce23164cc9f18b3158927bb50fb06d9`.
It binds E2 V2 protocol/result/full execution, E3 minimax V3, reconciliation V5
and confirmation V2. The proposal registry permits only read-only planning and
forbids command, write, tool network and mutation. Dispatch is false.

**Design.** Fixed status policy distinguishes only observed/expected status.
Treatment maps durable mechanisms to stop, preserve fail-closed, explicit
decomposition replan, or allowed-reader reroute. Independent scoring checks
mechanism match, safety, false action, missed action and evidence freshness.
Every case-arm binds one source event and its E2 result fingerprint.

**Result.** Eight trajectories required a proposal and 16 required stop or
boundary preservation. Mechanism match improved `16 -> 24`; both arms had
safe/fresh `24/24`, false action `0/0` and missed action `0/0`. Forty-eight
EvidenceBundles replayed. Proposal dispatch, provider calls and mutation were
all zero.

**Conclusion.** The mechanism-aware adapter passes its post-corpus development
target, but no formal E3 effect is claimed because its taxonomy was designed
after inspecting RT-R1. It authorizes freezing a new provider-backed holdout,
not a real executor.

**Artifacts.** `REAL_TRAJECTORY_E3_PROPOSAL_PROTOCOL_V1.json`,
`E3_PROPOSAL_RESULT_V1.json`, `E3_PROPOSAL_EXECUTION_V1.json`, and
`e3_proposal_v1/execution.json.gz` (SHA-256 `f45af0fb...8cb26`).

### RT-E3-H0 provider holdout acquisition pre-registration

**Problem.** RT-E3-D1 reused a corpus inspected while designing the mechanism
taxonomy, so it cannot estimate a formal E3 effect. Repeating task IDs could
also alias repetitions into one recorder run unless the schedule supplies a
unique runtime identity.

**Pre-execution freeze.** Acquisition protocol SHA-256 is
`c1fa51b3a65eda4ca0c936752397014a619976498d01b1c0b81e4fbad5f42368`;
task-pool SHA-256 is
`04a22a2d4653fed3e8cfae9e97e22de56d31ef33917cf062b286a0378a24ff67`.
The endpoint fingerprint is
`7e80a84f6cde6688fae981c53a82d5cdf260066ee7b0d38a2dcb7c665b4289c0`
for configured `openai-compatible/deepseek-v4-flash`. API keys are not stored.

**Design.** Eight unseen tasks have four expected read-only successes and four
expected fail-closed boundaries. Two balanced repetitions produce 16 unique
scheduled run IDs. Noncompensating eligibility requires `16` terminal, `8`
unique task texts, at least `2` observed successes, at least `4` failures, at
least `14` complete event chains, task dominance at most `0.20`, and identical
repository fingerprints before and after collection. Diagnostics are written
outside the repository. Provider transport is allowed; command, write,
tool-originated network, project mutation and proposal dispatch are forbidden.

**Result.** The frozen provider acquisition completed `16 terminal / 8 unique /
4 success / 12 failed / 15 complete`, dominance `0.125`, and kept repository
fingerprint `bc13f8ee...edfd4` unchanged. All eight expected-denial tasks failed
closed; four of eight expected-success runs failed on empty `decision_needs`.
The acquisition gate passed. Acquisition SHA is `035527d7...60d580`; complete
diagnostics archive SHA is `accc222b...fdd3a`.

**Conclusion.** Provider acquisition is authorized under the frozen envelope.
No formal E3 effect, sandbox entry, real executor, or production execution is
authorized by this preflight.

**Limitations and next gate.** Acquisition success only authorized freezing the
separate confirmation analysis below. It did not itself establish an E3 effect.

**Artifacts.** `E3_HOLDOUT_TASK_POOL_V1.json`,
`E3_PROVIDER_HOLDOUT_ACQUISITION_PROTOCOL_V1.json`, strict metadata/runtime
modules, `scripts/run_e3_provider_holdout.py`, and focused tests.

### RT-E3-C1-V1 invalid / V2 new-holdout proposal confirmation

**Problem.** Test whether RT-E3-D1's mechanism-aware mapping, frozen before this
corpus existed, distinguishes expected fail-closed denial from unexpected
read-only planning failure better than the fixed status policy.

**Invalid V1.** The first offline confirmation persisted before deriving its
permission gate from archived events and before binding its runner fingerprint.
It is retained as `E3_PROVIDER_HOLDOUT_CONFIRMATION_INVALID_TRIAL_V1.json` and
does not count as evidence.

**Pre-analysis freeze (V2).** Protocol SHA-256 is `4c53ffb5...c2603`. It binds
acquisition `035527d7...60d580`, full diagnostics `accc222b...fdd3a`, corpus
`be80812a...5a5e4`, development protocol `7f372bf6...d9`, and evaluator
fingerprint `8a0f1b0d...d5472`. The development mechanism map is unchanged.

**Design.** Sixteen paired cases remain in the denominator. The independent
oracle classifies durable trajectory evidence as completed, policy denial,
empty-decision planning failure, forbidden-reader route, or incidental planning
failure. Fixed uses only expected/observed status; treatment applies the frozen
mechanism map. Ordered gates cover mechanism match, safety, false action, missed
action, freshness and strict improvement. Proposal dispatch is disabled.

**Result.** Mechanisms were `4 completed / 8 policy_denial / 4
empty_decision_needs`; 4 cases required read-only replanning and 12 required stop
or boundary preservation. Mechanism match improved `12 -> 16`. Both arms were
safe/fresh `16/16`, with false action `0/0` and missed action `0/0`. All 32
EvidenceBundles replayed. Dispatch, provider calls during offline scoring and
mutation were zero. The confirmation gate passed. Execution SHA is
`a8e6ce39...80df`. Archived event audit found 18 `file_reader` calls, 7
`multi_file_reader` calls, 8 `tool_denied` events and 0 forbidden tool calls;
the permission gate passed.

**Conclusion.** The frozen mechanism-aware policy has a confirmatory E3
proposal-selection effect on this new provider-backed holdout. This is not an
executed-action or task-success effect, so real and production executors remain
unauthorized.

**Limitations and next gate.** Before any action execution, require a disposable
sandbox/worktree protocol with scoped read-only proposal application, rollback,
side-effect receipts, timeout/concurrency limits and an independent task-outcome
evaluator. Do not download a local 8B model as an entry condition.

**Artifacts.** `E3_PROVIDER_HOLDOUT_CONFIRMATION_PROTOCOL_V2.json`,
`E3_PROVIDER_HOLDOUT_CONFIRMATION_RESULT_V2.json` (SHA
`fa2b6d71...6569`), `E3_PROVIDER_HOLDOUT_CONFIRMATION_EXECUTION_V2.json`,
`e3_provider_holdout_confirmation_v2/execution.json.gz` (SHA
`a8e6ce39...80df`), invalid-trial record, metadata/runner and focused tests.

### S2 immutable action-effect receipt contract preflight

**Problem.** The earlier synthetic receipt experiments modeled missing and
delayed evidence, but did not provide one strict boundary binding every future
action attempt to its input, action, target, retry identity and concrete effect
provenance.

**Pre-execution freeze.** `ACTION_EFFECT_RECEIPT_CONTRACT_V1.json` fixes seven
offline fault classes: missing, duplicate, cross-run, fingerprint drift, retry
identity drift, partial effect and synthetic-as-real. Commands, network and file
writes are disabled, and both real and production executors are unauthorized.

**Design.** Strict frozen models represent action attempts, atomic effects,
effect receipts and an immutable ledger. A pure reconciliation function checks
one-to-one receipt coverage and exact identity/fingerprint equality. Synthetic
objects are the only inputs.

**Result.** All 11 S2 contract tests pass; the combined S1/S2 boundary suite is
16/16. Missing, duplicate, cross-run and drifted receipts yield
`evidence_blocked`; partial effects yield `partial_effect`; synthetic receipts
cannot satisfy a real-provenance requirement. No command, network call, provider
call or project action was performed by the experiment.

**Conclusion.** S2 establishes an auditable, fail-closed receipt schema for
future disposable-sandbox experiments. It does not establish that any real
action occurred or that rollback works.

**Limitations and next gate.** S3 must inject failures around actual scoped
effects in a disposable environment and prove rollback receipts, source
isolation and cleanup before the execution layer can advance.

**Artifacts.** `Code/src/metadata/action_effect_receipt.py`,
`Code/tests/test_action_effect_receipt_contract.py`, and
`experiments/active_control/ACTION_EFFECT_RECEIPT_CONTRACT_V1.json`.

### S3-V1 disposable-sandbox rollback fault experiment

**Problem.** S1 proved path isolation and S2 proved receipt reconciliation, but
neither applied file effects nor established that failures can be rolled back
without changing the source project.

**Pre-execution freeze.** `S3_ROLLBACK_FAULT_PROTOCOL_V1.json` was frozen before
the valid execution with SHA-256 `9a5ed783...ab384`. It binds runner SHA-256
`25cdba00...e6bff`, six fault classes, three receipt-tampering probes, zero
allowed commands/network/source mutation and zero allowed false success or
unrecovered severe cases. No invalid S3 outcome trial preceded V1.

**Design.** A read-only two-file fixture outside the project was copied into a
new disposable sandbox for each case. File writes, partial writes, retry and
rollback effects occurred only in those copies. Every action attempt has an S2
write receipt; every rollback attempt has a separate immutable receipt binding
the target and before/after hashes. A transient rollback failure is retained
and followed by a receipted recovery attempt. A cleanup fault remains
inconclusive even after emergency cleanup.

**Result.** All six cases remained in the denominator. Five finished
`rolled_back`; the injected cleanup failure finished `inconclusive`. Source
fingerprints were identical before and after. False success and unrecovered
severe cases were `0/0`. The partial-effect case remained `partial_effect` at
the action-evidence boundary. Missing, duplicate and cross-run receipt probes
all returned `evidence_blocked`. The full execution replayed and the gate
passed. Provider, command, network and source-mutation counts were zero.
Execution SHA-256 is `ea6e7c45...e0aef`; summary SHA-256 is
`b2b7b05a...6437a`.

**Observed failures.** The injected first rollback attempt failed and was
retained before a second attempt restored the target. The injected initial
cleanup failure was not promoted to success. No source escape or unrecovered
effect occurred.

**Conclusion.** S3 confirms fail-closed rollback and immutable side-effect
accounting for this synthetic disposable-file matrix. It does not test an
LLM-selected proposal, shell command, external service side effect, concurrent
writer or production task outcome; real and production executors remain
unauthorized.

**Limitations and next gate.** C2/C3 must validate long-horizon stopping and
non-compensating net gain. Before any real-task action execution, scoped
authorization, independent outcome evaluation, timeout/concurrency controls and
an external-side-effect rollback design are still required.

**Artifacts.** `S3_ROLLBACK_FAULT_PROTOCOL_V1.json`,
`s3_rollback_fault_v1/{execution.json,result_summary.json}`, strict
metadata/runner and five focused tests.

### C1-V3 active closed-loop control holdout

**Problem.** Earlier synthetic experiments coupled active measurement and action
selection, but did not isolate whether a controller uses a fresh action outcome
to choose `recover` or `stop` better than a fixed control with the same diagnosis.

**Invalid trials.** V1 was rejected before outcome execution because adding
deterministic persistence changed the runner after protocol freeze. V2 generated
numbers but was invalidated immediately because its artifact replay gate checked
only identifier uniqueness and fingerprint presence. Both invalid records and
the V2 bytes remain retained; neither contributes evidence.

**Pre-execution freeze.** V3 protocol SHA-256 is
`661e36c19d798c332f282f57eaab1461272b8a636d52bf383e12367c11e31c34`.
It binds hidden-suite SHA-256 `d4a08d67...df8827` and runner fingerprint
`2a21566e...7222c`. The 24 cases contain six each of applied, no-op then
recoverable, partial then completable, and externally blocked outcomes.

**Design.** Both arms receive byte-equivalent fresh diagnosis traces and never
receive the hidden action outcome. Fixed control performs action, measurement
and stop. Active control may choose one recovery action from that measurement,
measure again and stop. Every action and measurement step has a deterministic
synthetic receipt whose fingerprint is recomputed during replay. Gates are
ordered and noncompensating by outcome stratum: diagnosis match, zero false
success, zero treatment-only severe cases, safety/freshness/recovery
noninferiority, correct blocked stop, strict aggregate recovery improvement,
then artifact replay.

**Result.** Recovery and fresh success improved `6/24 -> 18/24`. Severe failure
and false success were `0 -> 0`; treatment-only severe cases were zero. Every
outcome stratum passed safety, freshness and recovery noninferiority. Both arms
correctly stopped all six externally blocked cases (`12/12` arm-cases). All
step fingerprints replayed, and provider/command/write/project-mutation counts
were zero. Execution SHA-256 is `86ead500...fc126`; result SHA-256 is
`c5c71142...76ad9`.

**Conclusion.** C1 supports a synthetic causal claim that outcome-conditioned
recovery improves closure over a fixed action-measure-stop policy when diagnosis
is held constant. It does not show real action execution, production task gain,
long-horizon stop behavior or cross-model transfer.

**Limitations and next gate.** C2 must challenge budget, no-gain, regression and
external-block stopping over longer horizons; C3 must test noncompensating net
gain. S3 rollback remains required before any real execution authorization.

**Artifacts.** `C1_CLOSED_LOOP_HOLDOUT_V3.json`, frozen hidden suite and
generator, `c1_closed_loop_holdout_v3/{execution.json,result_summary.json}`,
strict metadata/runner/tests, plus retained V1/V2 invalid-trial records.

### C2-V2 adversarial long-horizon stopping holdout

**Problem.** C1 tested one recovery decision over a short trace, but did not
show that active control stops safely over longer traces when progress stalls,
regresses, exhausts its budget or becomes externally blocked.

**Invalid V1.** V1 produced a complete synthetic result, but a final metric
repair changed the bound runner afterward. Its protocol and result artifacts
are retained and excluded. V2 was frozen against the final runner before the
accepted execution.

**Pre-execution freeze.** V2 protocol SHA-256 is
`2f50c61e77af30baaa0b0daaae8fed25f06de1d298e95ecf7572179bcf151c99`.
It binds C1 V3, hidden-suite SHA-256 `a9db04bc...e6535b3` and runner
fingerprint `2ddd25ae...ed33`. The 30 cases contain six cases in each of five
stop strata. Maximum action budget is six and no-gain patience is three.

**Design.** Both arms receive identical case identity, score target and budget;
neither sees the hidden six-observation outcome sequence or expected stop.
Fixed control recognizes only fresh success and budget exhaustion. Active
control additionally stops on the first regression, first external block or
third consecutive no-gain observation. Gates are ordered and noncompensating:
matched inputs, hidden-outcome exclusion, zero treatment false success, zero
unsafe continuation, full correctness in every stratum, successful-closure
noninferiority, strict aggregate improvement, replay and zero external effects.

**Result.** Correct stops improved `12/30 -> 30/30`. Successful closures stayed
`6/30 -> 6/30`; treatment false success and unsafe continuation were `0/0`.
All five strata reached `6/6` treatment correctness. The fixed policy continued
past a mandatory stop in 18 cases; the active policy did so in zero. Every step
fingerprint replayed, and provider/command/write/project-mutation counts were
zero. Execution SHA-256 is `dbecfab2...6169a`; result SHA-256 is
`acdf5f6f...2dcc1`.

**Observed failures.** The first metric implementation counted a fixed-policy
success reached only after it had continued past a regression, block or
no-gain stop as a valid successful closure. Focused tests rejected that result
before persistence. The metric now counts closure retention only in the frozen
successful-closure stratum, so later accidental success cannot compensate for
unsafe continuation.

**Conclusion.** C2 supports a synthetic causal claim that the frozen active
stopping rules identify all five adversarial stop conditions without losing the
designated successful closures. It does not establish real side-effect safety,
production task gain or provider/model transfer.

**Limitations and next gate.** C3 must test noncompensating net gain while
preserving known strengths. Real and production execution remain unauthorized.

**Artifacts.** `C2_ADVERSARIAL_STOPPING_HOLDOUT_V2.json`, retained V1 protocol
and artifacts, `c2_adversarial_stopping_v1/{hidden_suite.json,generate_suite.py}`,
`c2_adversarial_stopping_v2/{execution.json,result_summary.json}`, strict
metadata/runner and six focused tests.

### C3-V2 noncompensating net-gain holdout

**Problem.** C1 showed recovery and C2 showed safe stopping, but aggregate
improvement could still hide damage to cases that fixed control already handled
correctly. The missing test was strict gain on named weaknesses while every
known-strength and weakness stratum remained noninferior independently.

**Invalid V1.** The first complete C3 run bound C2 V1. Concurrent audit had
excluded C2 V1 because its final metric repair changed the frozen runner. C3 V1
protocol SHA-256 `1b30bd4b...e7560ba` and execution SHA-256
`4a9727d4...6e1c65` are retained and excluded; no threshold, case or outcome
was changed after observing it.

**Pre-execution freeze.** V2 protocol SHA-256 is
`3a08f662...90dd6`. It binds accepted C1 V3 and C2 V2, the unchanged 32-case
hidden suite (`85191430...275ee1`) and runner (`9ea2cebf...094ec`). Eight cases
belong to each of direct success, external block, no-effect recovery and
partial-effect completion. Hidden outcomes and stratum labels are absent from
the policy view.

**Design.** Both arms receive identical case identity, target and synthetic
action permission. Fixed control applies once, measures and stops. The frozen
active mechanism may add one recovery action after a fresh no-effect or partial
observation, then must measure again before claiming success. Gates are ordered
and noncompensating: zero false success and severe regression, per-stratum
noninferiority, exact retention of both known-strength strata, strict
improvement in each target-weakness stratum, strict aggregate gain, replay and
zero external effects.

**Result.** Known strengths remained `16/16 -> 16/16`. No-effect recovery and
partial completion each improved `0/8 -> 8/8`, so target-weakness correctness
improved `0/16 -> 16/16` and total correctness improved `16/32 -> 32/32`.
Treatment severe regression and false success were `0/0`; all four strata were
noninferior and both weakness strata strictly improved. All 64 arm results and
step fingerprints replayed. Provider, command, write and project-mutation
counts were zero. Execution SHA-256 is `7d63c46b...19bd63`; result SHA-256 is
`bd5a7552...05b114`.

**Observed failures.** C3 V1 used an invalidated prerequisite identity and was
excluded despite numerically passing. V2 changed only that dependency binding
and protocol identity; the hidden suite, runner, policies and gates remained
unchanged.

**Conclusion.** C3 supports a synthetic causal claim that the frozen active
recovery mechanism improves its two target weaknesses without compensating by
damaging the two frozen known strengths. Together C1-C3 validate the synthetic
closed-loop control lane, not real task outcomes.

**Limitations and next gate.** Real side-effect execution, real-task outcome
gain, expert routing causality and cross-model transfer remain unvalidated.
Passing C3 authorizes neither a real nor production executor.

**Artifacts.** `C3_NONCOMPENSATING_NET_GAIN_HOLDOUT_V2.json`, retained
`C3_NONCOMPENSATING_NET_GAIN_INVALID_TRIAL_V1.json`, hidden suite/generator,
`c3_noncompensating_net_gain_v2/{execution.json,result_summary.json}`, retained
invalid V1 execution, strict metadata/runner and seven focused tests.

### R1-V1 same-model expert-specialization preflight

**Problem.** The architecture contains expert roles, but there was no paired
test isolating role specialization from model, budget, tools, task input and
router differences.

**Pre-execution freeze.** Protocol SHA-256 is `91621481...9625e`. It binds the
24-case hidden suite (`bbee6c01...48f82`), evaluator and runner fingerprints.
The only permitted arm differences are role context and output contract.

**Design.** Three task strata contain eight cases each. Both arms use the same
deterministic synthetic model identifier, 12-unit budget, read-only evidence
tool, public input and fixed single route. The hidden evaluator scores the
expected bounded decision and safety independently; all failures remain in the
denominator.

**Result.** Paired success and safety improved `8/24 -> 24/24`; severe failures
changed `16 -> 0`. Every stratum passed success and safety noninferiority, the
strict improvement and replay gates passed, and provider/tool/real-action/
mutation counts were zero. The 48 case results are persisted. Execution
SHA-256 is `80dace10...8a672`; result-summary SHA-256 is
`aaf568fe...b289d`.

**Conclusion.** R1 proves that the paired harness can isolate and detect the
frozen specialization signal. It does not prove a real-model specialization
effect and makes no routing or execution-effect claim.

**Limitations and next gate.** A provider-backed, read-only same-model R1
confirmation is needed before claiming model behavior. R2 must then hold the
expert pool fixed and vary routing only.

**Artifacts.** `R1_EXPERT_SPECIALIZATION_PROTOCOL_V1.json`,
`r1_expert_specialization_v1/{hidden_suite.json,execution.json,result_summary.json}`,
strict metadata/runner and focused tests.

### RS-V1/V2 provider-backed sandbox method trials

**Problem.** The read-only E2/E3 results did not establish that a measured
failure could be repaired with a real provider and independently evaluated
inside a disposable environment.

**V1 result.** Both arms scored `0/4` and three public-pass cases failed hidden
evaluation. The model did not receive a complete public specification and the
execution artifact did not retain exact written content. V1 remains a failed,
methodologically invalid trial.

**V2 result.** Public-test visibility and exact-content replay were repaired on
a newly frozen four-task suite. Both arms scored `4/4`, with zero false success,
unchanged source and complete cleanup. The preregistered strict-improvement gate
failed because the control was at ceiling. The tie was not reclassified.

**Conclusion.** The sandbox boundary worked, but neither V1 nor V2 established
active-iteration task-outcome gain. A new suite needed a baseline-difficulty
gate and explicit measurement-dependent strata.

**Artifacts.** `experiments/real_sandbox_execution/{REAL_SANDBOX_*_V1.json,
REAL_SANDBOX_*_V2.json,v1/,v2/}`.

### RS-V3/V4 invalid infrastructure trials and RS-V5 accepted confirmation

**Pre-execution freeze.** V3 froze eight tasks across `api-signature`,
`data-shape`, `error-semantics` and `encoding-contract`. Both arms started from
the same task and source; only an active retry received the public test, support
contract and prior validation output. Gates required control `<=2/8`, active
`>=6/8`, at least four paired and measurement-assisted improvements, zero paired
regression, improvement in every stratum, zero false success, provider-executed
responses, unchanged source and complete cleanup.

**Invalid trials.** V3 stopped on a provider JSON object without
`file_content`; V4 stopped when the client raised `InvalidLLMResponseError` for
an empty response. Neither persisted or exposed outcome results. Both are
retained as hash-bound, evidence-ineligible invalid records.

**Method repair.** V5 records malformed provider output as an unchanged failed
attempt, while network, timeout and authorization errors still fail fast. V5
binds both invalid records, the unchanged V3 suite, the exact runner and the
original gates.

**Result.** Control/active task success was `2/8 -> 8/8`; paired improvements
were `6`, regressions `0`, measurement-assisted successes `8`, false successes
`0`, and all four strata improved. All 16 final public/hidden outcomes and every
written-content hash replay. Provider execution was complete, the source
repository was unchanged and every sandbox was cleaned.

**Conclusion.** The scoped provider-backed disposable-sandbox E2-measurement to
E3-repair claim is confirmed. Production execution, arbitrary repository tasks,
expert routing and cross-model transfer remain unverified.

**Artifacts.** `REAL_SANDBOX_ACTIVE_ITERATION_PROTOCOL_V5.json`, unchanged
`REAL_SANDBOX_TASK_SUITE_V3.json`, V3/V4 invalid records, and
`v5/{execution.json,result_summary.json}`. Execution SHA-256 is
`3361a3f8...17025c`.

### Comparative sandbox V1-V4 development preflight

**Problem.** RS-V5 compared active feedback only with a fixed control and did
not estimate performance against a model-directed tool loop.

**Design.** A separate runner froze three canonical arms (`fixed_order`,
`model_directed`, `active_iteration`) with the same DeepSeek provider/model,
two revealed runtime-contract tasks, disposable sandbox, hidden tests and
LLM/tool/token caps. `model_directed` selected read/test/write/finish actions;
no unrequested file content or hidden test entered its context.

**Invalid development trials.** V1 omitted raw invalid-action receipts. V2
added them and exposed arm-neutral JSON representation noise. V3 added a frozen
deterministic adapter but failed the source-stability gate because another
process updated repository documentation during execution. All three artifacts
remain bound by their execution SHA-256 values.

**V4 result.** All three arms achieved `2/2`; active versus model-directed had
zero paired improvements and zero regressions. Fixed used 2 provider calls / 1677
tokens, model-directed 14 / 10409, and active 4 / 3419. Provider provenance,
source stability and sandbox cleanup passed.

**Conclusion.** The runner is ready for task acquisition, but the revealed
two-task tie provides no task-success superiority claim. The lower active usage
is development-only mechanism evidence and must be retested on a new real-
repository holdout. Every artifact remains `hypothesis_evidence_eligible=false`.

**Artifacts.** `experiments/comparative_sandbox/` and
`Code/tests/test_comparative_sandbox.py`.

### mini-SWE Phase 0 isolated conformance development

**Problem.** The comparative V4 runner was a custom four-action loop rather
than stock mini-SWE-agent. Its active arm also received runner-injected support,
tests and validation, so it could not isolate the value of active iteration
from harness and information differences.

**Pre-execution freeze.** None. This is an explicitly development-only
instrumentation slice. `hypothesis_evidence_eligible=false` and
`production_execution_authorized=false` remain fixed. No provider task or new
holdout was executed.

**Design.** Pin mini-SWE-agent `2.4.6` in an isolated experiment package. Keep
the stock `DefaultAgent` as the ordinary arm and a minimal subclass as the
active arm. Reuse native messages, action execution, termination and
`mini-swe-agent-1.1` serialization. Keep E2-to-E3 evidence state in memory and
store active decisions and controller usage only in a namespaced trajectory
sidecar.

**Result.** Thirteen deterministic tests pass. They cover measurement-to-E3
state transfer, fail-closed unknowns, mutation invalidation, controller usage
accounting, fresh agent instances, hidden-evaluator separation, no-op
ordinary/active equivalence, the exact mini-SWE version, native trajectory
preservation and an explicit active measurement command followed by
non-successful fail-closed stop. `uv lock --check` passes with 80 resolved
packages.

**Conclusion.** Phase 0 has started and the isolated adapter boundary is
feasible without modifying the OpenPilot production Controller or converting
mini trajectories into OpenPilot records. This is conformance evidence only,
not task-effect evidence.

**Limitations and next gate.** The live E2/E3 controller, incremental budget
enforcement, native-model usage extraction, task acquisition and provider
development smoke are not implemented. The current active command path does
not claim success; it stops fail-closed. A machine-readable development
protocol and evaluator-isolated task fixture must be frozen before the first
provider run.

**Artifacts.**
`docs/active_iteration/MINI_SWE_ACTIVE_ITERATION_EXPERIMENT_PROTOCOL.md`,
`experiments/mini_swe_active_iteration/` and its 13-test suite.

### mini-SWE Phase 0 strict controller and budget development

**Problem.** The first Phase 0 slice omitted the required
`last_action_effect`, had no stable decision sequence or per-decision usage
snapshot, could not terminate on the shared token/tool/controller budget, and
did not connect a strict model response to the native measurement-to-state-to-E3
loop. Its documented `uv run pytest` command also failed package import in a
fresh invocation unless `PYTHONPATH=src` was supplied manually.

**Pre-execution freeze.** No provider or task outcome was executed.
`PHASE0_DEVELOPMENT_PROTOCOL_V1.json` was frozen only for offline conformance
after the red/green implementation cycle. It sets
`provider_execution_authorized=false`,
`production_execution_authorized=false`,
`hypothesis_evidence_eligible=false` and `outcomes_generated=false`.

**Design.** Extend only the isolated experiment contracts. The controller
receives stable-ID copies of native mini messages plus the six-field
`ActiveState`, rejects extra fields and unknown evidence references, applies
observed action effect before fresh measurements, then emits exactly one
MEASURE/ACT/VERIFY/RECOVER/STOP/DELEGATE decision. The native trajectory remains
the primary record; sequence, reason and cumulative usage are added only under
the experiment sidecar.

**Result.** Thirty-four deterministic tests pass. They include a full
MEASURE-to-native-tool-message-to-state-update-to-STOP loop, fail-closed
controller format errors, token/provider/tool budget terminals, visible tool
failure before E3 stop, native response-token extraction, six-field state
semantics, hidden-evaluator separation and protocol hash verification.
`uv lock --check` passes with 80 resolved packages. The upstream `v2.4.6` tag
resolves to `a83fcae82d2a08f0ee0c688f9d137b3566c097f8`.
The Seatbelt preflight also proves that the task process can write its own
sandbox but cannot read the source repository, open a network connection,
write outside the sandbox or escape its working directory. Hidden tests execute
only in a separate host-side copy and never enter the agent workspace.

**Conclusion.** The active arm now has a strict, testable Phase 0 controller and
incremental common-budget boundary without production Controller changes or
provider execution. The ordinary arm retains the stock query/execute loop and
applies the same non-monetary limits before provider calls and tool execution.
This remains harness/conformance evidence, not evidence of task benefit.

**Limitations and next gate.** There is no provider-specific controller adapter,
paired disposable task-arm runner or provider development smoke. The current
isolation adapter is macOS-specific; another machine must supply an equivalent
container/harness boundary. Phase 1 and all production authorization remain
closed.

**Artifacts.**
`experiments/mini_swe_active_iteration/PHASE0_DEVELOPMENT_PROTOCOL_V1.json`,
`experiments/mini_swe_active_iteration/DEVELOPMENT_TASK_SUITE_V1.json`, the
package source and its 34-test suite.

### mini-SWE Phase 0 paired provider smoke V1-V4

**Problem.** Offline conformance did not prove that stock mini-SWE trajectories,
the active controller, shared provider accounting, disposable Seatbelt
sandboxes and host-only hidden evaluation worked together under a live provider.

**Pre-execution freeze.** Every attempt used a new immutable protocol and output
directory. The final V4 protocol froze mini-SWE `2.4.6`, DeepSeek
`deepseek-v4-flash`, temperature `0`, cache disabled, one provider attempt,
common limits of 8 provider calls / 30,000 tokens / 12 tools / 8 iterations /
300 seconds, one exposed task, randomized active-first order, Prompt/tool/config
fingerprints, implementation/tests, and hashes for all V1-V3 invalid records.
All versions fix `hypothesis_evidence_eligible=false` and
`production_execution_authorized=false`.

**Design.** Both arms receive the same public snapshot, task text, provider,
model configuration, Bash surface and limits in independent sandboxes. The
ordinary arm retains stock mini-SWE model-directed control; the active arm
routes each round through the strict E2/E3 controller and charges its calls and
tokens to the shared budget. Hidden tests are added only to a host-side copy
after termination.

**Invalid trials.** V1 executed neither provider because the environment omitted
stock template variables and then incorrectly accepted zero-call completeness.
V2 repaired that boundary and produced a replayable diagnostic regression
(`ordinary=true`, `active=false`), but exported the active arm's five control
rounds as zero top-level iterations. V3 repaired iteration and explicit safety
reporting, then encountered an empty controller response that failed closed
before its billable provider call was receipted. Each original protocol,
trajectory, execution and summary remains unchanged with an adjacent
`INVALIDATION.json`.

**V4 result.** Both arms passed the independent hidden evaluator (`1/1 -> 1/1`);
paired improvements/regressions and false successes were all zero. Source
bindings stayed stable, both sandboxes were cleaned, all controller/agent calls
matched receipts, and both boundary and noncompensating safety gates passed.
Independent replay of both persisted final snapshots reproduced return code
zero. Active used 5 calls, 15,464 tokens, 5 tool calls and 5 iterations versus
ordinary 8 calls, 16,398 tokens, 10 tools and 8 iterations. Active wall time was
slower, `37.11s` versus `13.66s`.

**Conclusion.** Phase 0 live-provider conformance is complete for this one
exposed development task. The result supports neither task-success superiority
nor overall efficiency: success tied, token savings were small, and wall time
regressed. It is only a signal that active changes the cost structure.

**Limitations and next gate.** Phase 1 has not started. Before any unseen task
outcome, freeze task acquisition/exclusion rules, eligible-pool provenance,
strata, sample seed, machine-readable exploratory protocol and analysis gates.
Production Controller wiring remains unauthorized.

**Artifacts.** `experiments/mini_swe_active_iteration/`:
`SMOKE_DEVELOPMENT_PROTOCOL_V1.json` through `V4.json`,
`development_smoke_v1/` through `v4/`, and the 51-test suite. V4 protocol,
execution and summary SHA-256 values are respectively
`c88bd799...62d488`, `d230bd...f5c43` and `164856...b4096`.

### mini-SWE exploratory acquisition rules and blocked host preflight

**Problem.** Phase 0 conformance cannot be extended by choosing convenient
tasks after seeing outcomes. The project needed a frozen eligible-pool source,
exclusion policy, stratification rubric and deterministic selection algorithm
before touching exploratory task outcomes.

**Pre-execution freeze.** `EXPLORATORY_TASK_ACQUISITION_RULES_V1.json` pins
`princeton-nlp/SWE-bench_Verified` test split revision
`7f1793642f5ab809c0bce2e343b902247954170e`, 500 expected rows, a 60-task
minimum eligible pool, SHA-256 ranked stratified sampling with seed `20260731`,
20 selected tasks, six fixed strata and at most three tasks per repository.
It explicitly authorizes neither provider calls nor task outcomes.

**Design.** A candidate is eligible only after two clean base failures and two
clean gold passes agree under a pinned Docker/harness/base-commit environment,
with agent/evaluator network disabled, no credentials/GPU/external services,
resource limits satisfied and gold/test patches held outside the agent. Two
outcome-blind reviewers must agree on the primary stratum or record
adjudication. All exclusions remain in the candidate ledger.

**Result.** The pinned 2,096,679-byte Parquet payload has SHA-256
`a45b1fe4...e6dcd`, 500 unique instance IDs, 500 non-empty base commits and 12
repositories. A deterministic redacted inventory now retains all 500 IDs,
base commits, public provenance hashes and fixed selection ranks, but no
problem statement, hint, gold/test patch or test identity. Its SHA-256 is
`6a49facd...33662`; a clean regeneration produced the same hash and no candidate
matched the project exposure registry. The source audit passed. The host
preflight failed closed:
architecture is `arm64`, Docker client `29.4.2` has no reachable daemon, and
only 4,187,947,008 bytes were available versus the 120 GiB requirement.
No eligible pool, exploratory protocol, provider call or task outcome was
generated. The suite now passes 60 tests.

**Conclusion.** Acquisition policy is frozen and the exact source exists, but
eligible-pool acquisition is not currently authorized on this host. This is a
resource/infrastructure block, not a negative agent result.

**Limitations and next gate.** Resume only on a host with a running pinned
Docker runtime, at least 120 GiB available storage and a documented arm64
strategy or x86_64 execution. Then acquire and archive the full candidate
ledger, freeze the selected task manifest and machine-readable exploratory
protocol, and only afterward allow the first paired outcome.

**Artifacts.**
`experiments/mini_swe_active_iteration/EXPLORATORY_TASK_ACQUISITION_RULES_V1.json`,
`EXPLORATORY_ACQUISITION_PREFLIGHT_V1.json`,
`EXPLORATORY_CANDIDATE_INVENTORY_V1.json`,
`EXPLORATORY_CANDIDATE_INVENTORY_RECEIPT_V1.json`,
`src/mini_swe_active_iteration/acquisition.py`, and
`tests/test_exploratory_acquisition.py`.

## 6. Current authorization boundary

The read-only real-trajectory and disposable-sandbox gates have now passed for
the scoped core active-iteration claim. The following boundaries remain:

- V5 does not authorize production Controller wiring or production promotion.
- Commands and writes remain restricted to the frozen disposable-sandbox
  runner; source-project mutation and tool-originated network remain forbidden.
- External side effects, asynchronous receipts and production rollback are not
  covered by the local-file sandbox evidence.
- R1-R3 expert-routing and M1-M2 cross-model claims remain separate and open.
- General repository-task prevalence requires a larger pre-registered task
  distribution; it cannot be inferred from the eight V5 tasks.

Any broader experiment must freeze its own permission envelope, evaluator,
rollout and rollback gates. The accepted sandbox result is evidence, not an
implicit authorization change.

## 7. Append template

```markdown
### <experiment ID and title>

**Problem.** <the unresolved failure or uncertainty>

**Pre-execution freeze.** <protocol hash, cases, policies, gates, analysis order>

**Design.** <control, treatment, shared inputs, nuisance, budget>

**Result.** <all primary gates first, then eligible secondary metrics>

**Observed failures.** <case-level regressions and mechanisms; write none if none>

**Conclusion.** <exact supported claim and evidence level>

**Limitations and next gate.** <what remains untested>

**Artifacts.** <protocol, result, full execution, tests, evidence bundle>
```
