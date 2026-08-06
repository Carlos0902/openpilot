# Active Iteration Experiment Review

## 1. Document status

This is the current synthesis of the OpenPilot E0/E2/E3 validation program
through the accepted provider-backed disposable-sandbox V5 experiment on
2026-07-29.

Use this document to answer:

- what was tested;
- why each experiment existed;
- which trials were rejected or invalidated;
- what the accepted evidence supports;
- what remains outside the supported claim.

The source-of-truth hierarchy is:

1. frozen protocol, suite, execution and result JSON under `experiments/`;
2. [ACTIVE_ITERATION_EXPERIMENT_LOG.md](./ACTIVE_ITERATION_EXPERIMENT_LOG.md)
   for experiment-level audit history;
3. [IMPLEMENTATION_LOG.md](../task_trajectory/IMPLEMENTATION_LOG.md) for
   chronological implementation history;
4. this review for the current cross-experiment conclusion.

This review does not replace raw artifacts or reclassify development replay as
independent evidence.

## 2. Executive conclusion

The current evidence supports a scoped claim:

> With the frozen provider/model and the tested runtime-contract task strata,
> an active loop that receives a fresh E2 measurement after failure and then
> performs an E3 repair in a disposable sandbox improves independently
> evaluated task success over the fixed control without paired regression,
> false success, source-project mutation or sandbox-cleanup failure.

The final V5 confirmation produced:

| Metric | Control | Active / result |
|---|---:|---:|
| Objective task success | `2/8` | `8/8` |
| Paired improvements | - | `6` |
| Paired regressions | - | `0` |
| Measurement-assisted successes | - | `8` |
| False successes | `0` | `0` |
| Improved frozen strata | - | `4/4` |
| Source repository unchanged | - | yes |
| All sandboxes cleaned | - | yes |
| Provider execution complete | - | yes |

This closes the core disposable-sandbox E2-to-E3 research gate. It does not
authorize production Controller wiring, arbitrary commands, source-project
writes, tool-originated network, external side effects or automatic promotion.

Expert-routing causality and cross-model transfer remain separate, deferred
claims.

## 3. Validation method

The program used the following evidence ladder:

| Level | Purpose | Claim strength |
|---|---|---|
| Harness / contract test | Validate metadata, replay and permission boundaries | No policy-effect claim |
| Synthetic feasibility | Check whether a mechanism can work at all | Constructed-domain evidence |
| Development replay | Repair a revealed failure and replay it | No independent confirmation |
| Independent synthetic holdout | Test a frozen mechanism on an unseen dimension | Independent synthetic evidence |
| Real shadow | Use real provider/task trajectories without state-changing action | Read-only real-input evidence |
| Sandbox execution | Apply scoped writes only in disposable environments | Real provider plus bounded execution evidence |

Across these levels, the following rules were enforced:

- protocols, task suites, policies, runners and evaluators were fingerprinted;
- failed and invalid trials were retained rather than overwritten;
- hidden outcomes were excluded from policy input;
- development results were not promoted to holdout evidence;
- safety, freshness and false-success gates were noncompensating;
- source-project mutation and tool-originated network stayed forbidden;
- persisted artifacts had to replay from their bound content.

## 4. Evidence foundation

| ID | Problem tested | Result | Supported conclusion |
|---|---|---|---|
| E0-H1 | Can trajectories and EvidenceBundles fail closed on cross-run references, drift, mutation and stale evidence? | Passed deterministic integrity and freshness gates | Later E2/E3 results can use the trajectory layer as a replayable evidence substrate |
| Phase 0 fixture harness | Can task/run/evidence fixtures be loaded without silently accepting malformed contracts? | Passed strict model and fixture checks | Experiment inputs have a typed boundary |
| Shadow conformance | Do in-memory and persisted shadow outcomes agree? | Passed disk replay and drift checks | Shadow results are not terminal-output-only claims |
| Observed receipt provenance | Can claimed observations be tied to attempts and effects? | Missing and inconsistent provenance fails closed | Receipt-based conclusions require bound evidence |
| Experiment manifest persistence | Are protocol, model, prompt, tool and evaluator identities stored together? | Passed manifest replay | Experiment identity is content-addressed rather than inferred from prose |
| Local cache observation | Can cached and provider-executed responses be distinguished? | Passed provenance checks | Provider evidence is not silently replaced by local cache output |

These experiments validated the measurement apparatus. They did not establish
E2 or E3 policy effects.

## 5. E2 active-measurement experiments

| ID | Problem tested | Result | Interpretation |
|---|---|---|---|
| E2-V1 | Was the first active-measurement protocol sufficiently specified? | Rejected before execution | Field propositions, checker coverage, conflict handling and legal omission were under-specified |
| E2-V2 | Can unknown-first active measurement preserve diagnosis while reducing work? | Exact diagnosis `5/5` in both arms; logical cost `62 -> 53` | Synthetic feasibility only |
| E2-N1 | Does V2 remain safe under missing/stale/nuisance measurements? | Correct `10/10`, but critical errors `28 -> 31`; failed | Unknown-first V2 was unsafe under nuisance |
| E2-D3 | Can failure-exposure-aware ordering repair E2-N1? | Correct `10 -> 13`, critical `28 -> 16` | Successful development repair on revealed cases |
| E2-T1 | Does D3 survive independent second/third-measurement timing faults? | Correct `8 -> 5`; failed accuracy gate | Lower critical-error count could not compensate for accuracy loss |
| E2-D4 | Can a guarded fixed prefix retain baseline strengths before adapting? | Matched fixed on nuisance; timing critical `12 -> 2` with correct `8 -> 8` | Baseline-safe development policy |
| E2-C4 | Does V4 pass a new 27-case confirmation suite? | Correct `23 -> 24`, critical `3 -> 3`, false success `0`, fresh `14 -> 16` | Independent synthetic confirmation |
| E2-D5 | Can balanced-prefix V5 fix the long-path freshness gap exposed by the invalid joint draft? | Passed all four revealed datasets; joint diagnostic fresh `11 -> 12` | Development result; required new joint holdout |
| RT-E2-S0 | Can real trajectories be converted into blind E2 packets without outcome leakage? | Eight packets, C1-C8 opportunities, no provider call or mutation | Structural real-trajectory entry gate |
| RT-E2-X1 | Did formal E2 V1 EvidenceBundles support the claimed freshness scope? | Rejected before persistence | Single-record bundles could not prove whole-chain freshness |
| RT-E2-R2 | Does frozen E2 V5 preserve real-trajectory diagnosis while reducing logical measurement cost? | Outcome/alignment/fresh `24/24` in both arms; severe/false success `0`; cost `264 -> 216` | Formal read-only real-trajectory E2 confirmation |

E2 is therefore supported at two levels: independent synthetic nuisance
confirmation and formal real-trajectory diagnostic efficiency. It is not a
claim that every production measurement is cheaper or more accurate.

## 6. E3 action, recovery and evidence experiments

| ID | Problem tested | Result | Interpretation |
|---|---|---|---|
| J-V1 | Did the first joint runner model real transitions? | Rejected because actions wrote derived outcomes directly | Invalid causal boundary |
| J-V2 | Can measured diagnosis drive a minimal action/recovery loop? | Recovery `5/5` both arms; cost `82 -> 68` | Joint synthetic feasibility |
| E3-A1 | With diagnosis held fixed, does action policy explain part of the gain? | Action-policy cost `20 -> 15` | Exploratory mechanism attribution only |
| E3-L1 | Is residual-controller-v1 safe over an exhaustive actionable-residual lattice? | Severe `14 -> 26`; failed | Controller was unsafe outside the pilot |
| E3-C2 | Can closure-aware V2 repair lattice failures? | Recovery `92 -> 95`, severe `14 -> 12` | Development replay only |
| E3-T1 | Does V2 survive an unseen single-action no-op transition? | Aggregate severe `320 -> 314`, but two treatment-only severe cases | Promotion rejected despite aggregate improvement |
| E3-M3 | Can fault-aware minimax V3 eliminate those treatment-only failures? | Recovery `343 -> 349`, severe `320 -> 314`, treatment-only severe `0` | Development repair |
| E3-P1 | Does V3 survive independent partial-action effects? | Recovery `417 -> 425`, severe `254 -> 246`, no treatment-only severe | Independent synthetic confirmation of partial-effect handling |
| E3-R1 | Does V3 survive fault timing, positional no-op and optional retry? | Recovery `291 -> 299`, severe `474 -> 462` | Narrow independent pass; receipt timing still open |
| E3-R2 | Does success remain fresh with missing receipts and delayed validation? | Recovery improved, but evidence-blocked `5 -> 8`; failed freshness gate | Physical recovery is not enough without evidence closure |
| E3-D4 | Can reconciliation V4 close revealed receipt gaps without changing action selection? | Fresh success `29/38`, evidence-blocked `0` | Development repair |
| E3-C4 | Does a new V4 confirmation validate that adapter? | Numerically passed, then invalidated | Runner bypassed the frozen adapter and exceeded the terminal-checkpoint limit |
| E3-D5 | Can one shared reconciliation V5 implementation serve development and confirmation? | Retry/terminal reconciliation `24/7`, provenance passed | Corrected development boundary |
| E3-C5 | Does shared V5 pass unseen masks, external observations and negative probes? | Recovery `33/33`, positive fresh `22/22`, negative probes `48/48` fail closed; cost `584 -> 564` | Independent synthetic receipt/reconciliation confirmation |
| RT-E3-D1 | Can durable real-trajectory mechanisms improve action proposals? | Mechanism match `16 -> 24`, safe/fresh `24/24` | Post-corpus development; required new holdout |
| RT-E3-H0/C1 | Does the frozen mechanism map improve proposals on a new provider corpus? | Mechanism match `12/16 -> 16/16`, safe/fresh `16/16`, zero false/missed actions | Independent real-provider proposal confirmation |
| RT-E3-C1 V1/V2 | Are proposal results permission-audited and runner-bound? | V1 invalid; V2 derives 18 file-reader, 7 multi-reader, 8 denial, 0 forbidden calls | Confirms read-only proposal selection, not execution |

E3 is supported for action selection, partial/no-op recovery, stopping and
receipt reconciliation in synthetic holdouts, plus proposal selection on a new
real-provider corpus. Before the final sandbox experiment it still lacked an
executed task-outcome estimate.

## 7. Joint E2 x E3 synthetic exit

| ID | Problem tested | Result | Interpretation |
|---|---|---|---|
| J-X1 | Could the original final joint holdout run with E3-C4 as prerequisite? | Blocked before outcome | Invalid E3 prerequisite correctly prevented joint evidence |
| E2-D5 | Could the revealed long-path E2 failure be repaired? | Balanced-prefix V5 passed revealed datasets | Development only |
| J-N2 | Do frozen E2 V5 and E3 V5 jointly survive unseen cross-layer nuisance? | 60 cases/arm correct and fresh; severe/false success `0`; 120 bundles replay | Independent synthetic joint exit passed |

J-N2 opened only the read-only real-task shadow stage. It did not authorize a
mutating executor.

## 8. Real-provider shadow and trajectory experiments

| ID | Problem tested | Result | Interpretation |
|---|---|---|---|
| RTS-X1 | Can the first provider shadow produce strict answer/evidence records? | Objective `1/12`; side-effect-free `12/12` | Exposed timeout, truncation, JSON and quote-contract failures |
| RTS-X2 | Do larger responses and quote aliases fix provider output? | Answers `12/12`, strict evidence/objective `4/12` | Free-text evidence remained unstable |
| RTS-X3 | Does candidate-ID evidence remove free-text matching ambiguity on unseen tasks? | Answer/evidence/objective `12/12`; repository stable | Provider harness gate passed, not an E2/E3 effect estimate |
| RT-P0 | Can real trajectories be collected under a permanent read-only tool boundary? | Two-reader allowlist, no-index reader, terminal denial, no mutation | Permission/trajectory preflight passed |
| RT-R1 | Is the acquired corpus diverse and complete enough for formal shadow experiments? | 24 terminal, 8 unique, 4 success, 20 failed, 22 complete, dominance `0.125` | Real-provider corpus eligibility passed |

The real-shadow work separated provider transport from tool network, retained
failed runs in the denominator and kept the source fingerprint stable.

## 9. Execution-safety and closed-loop control experiments

| ID | Problem tested | Result | Interpretation |
|---|---|---|---|
| S1 | Is the execution target provably outside the source project and cleanup fail-closed? | Passed path, fingerprint and cleanup checks | Disposable-sandbox boundary established |
| S2 | Are action attempts, effects, retries and receipts immutable and cross-run safe? | Missing, duplicate, drifted, cross-run and partial receipts fail closed | Receipt representation boundary established |
| S3-V1 | Does rollback restore disposable state under injected write faults? | Five verified rollbacks, one cleanup-inconclusive; false success/unrecovered severe `0` | Synthetic local-file rollback passed |
| C1-V3 | Can fresh action observations drive recovery or safe stopping? | Recovery/fresh success `6 -> 18`; severe/false success `0` | Independent synthetic closed-loop confirmation |
| C2-V2 | Does the loop stop on budget, no gain, regression and external block? | Correct stop `12/30 -> 30/30`; unsafe continuation `18 -> 0`; closure `6 -> 6` | Independent long-horizon stopping confirmation |
| C3-V2 | Does net gain preserve known strengths instead of compensating across cases? | Known strengths `16 -> 16`; target weaknesses `0 -> 16`; total `16 -> 32`; severe/false success `0` | Independent noncompensating net-gain confirmation |
| R1-V1 | Can the harness isolate expert specialization at equal synthetic model/budget/tools? | Success/safety `8/24 -> 24/24`, severe `16 -> 0` | Harness identifiability only; no real-model or routing claim |

S1-S3 and C1-C3 established the safety and control prerequisites for a bounded
real-provider execution experiment.

## 10. Provider-backed disposable-sandbox experiments

| ID | Problem tested | Result | Interpretation |
|---|---|---|---|
| Sandbox V1 | Does the first paired repair harness show task-outcome gain? | `0/4 -> 0/4`, three false successes | Methodologically invalid: public specification was incomplete and written content was not replayable |
| Sandbox V2 | After fixing visibility and replay, does active feedback improve success? | `4/4 -> 4/4`, zero false success | Valid negative result with a control ceiling; strict-improvement gate failed |
| Sandbox V3 | Can a pre-stratified measurement-dependent suite avoid the ceiling? | Incomplete on provider JSON without `file_content` | Infrastructure-invalid trial; no outcome persisted or inspected |
| Sandbox V4 | Does malformed-object handling make the run robust? | Incomplete on empty-response `InvalidLLMResponseError` | Second infrastructure-invalid trial; same suite and gates retained |
| Sandbox V5 | Does E2 measurement followed by E3 repair improve independently evaluated outcomes? | Control `2/8`, active `8/8`, improvements `6`, regressions `0`, measurement-assisted success `8`, false success `0`, all four strata improved | Accepted scoped real-provider disposable-sandbox confirmation |

V3 froze the eight-task suite and four strata before outcome generation:

- `api-signature`;
- `data-shape`;
- `error-semantics`;
- `encoding-contract`.

The original gates were retained through V5:

- control success at most `2/8`;
- active success at least `6/8`;
- at least four paired and measurement-assisted improvements;
- zero paired regression;
- improvement in every stratum;
- zero false success;
- all responses provider-executed;
- source unchanged and every sandbox cleaned.

V5 changed only malformed-response accounting. It bound the V3/V4 invalid
records, unchanged suite, exact runner and original gates. No task or threshold
was changed after observing an outcome.

### Three-arm development comparison

A separate development preflight compared `fixed_order`, `model_directed` and
`active_iteration` using the same provider, two revealed sandbox tasks, tool
surface, hidden evaluator and budget limits. The primary future comparison is
active versus model-directed; fixed order is only a mechanism baseline.

V1 did not retain invalid provider action responses. V2 added decision receipts
and showed that representation-only JSON differences were exhausting the
model-directed budget. V3 added one shared deterministic action adapter but was
invalidated because an external process changed source-repository documentation
during execution. V4 bound all three records and retained the same V3 execution
behavior and two-task suite.

V4 produced `2/2` objective success in every arm, zero active/model-directed
paired improvements and zero regressions. Active used 4 provider calls and 3419
tokens; model-directed used 14 calls and 10409 tokens. Provider provenance was
complete, the source was unchanged and all sandboxes were cleaned.

This is development evidence only (`hypothesis_evidence_eligible=false`). The
success tie removes any basis for claiming active task-success superiority on
these tasks. The usage difference is a hypothesis for a new, independently
frozen real-repository holdout, not a confirmed efficiency effect.

### mini-SWE Phase 0 conformance

The next comparison now has an isolated mini-SWE-agent `2.4.6` package rather
than the prior custom four-action loop. Fifty-one deterministic checks cover
the complete lightweight `ActiveState`, stable-ID controller inputs, strict
controller schema, native-message measurement execution, state update before
the next E3 decision, decision sequencing, provider/token/tool/iteration
accounting, budget exhaustion, malformed responses, tool failures, native
trajectory preservation, inherited-secret denial and hidden-evaluator
separation.

`PHASE0_DEVELOPMENT_PROTOCOL_V1.json` binds the upstream tag commit, package
lock, controller Prompt, Bash schema, implementation and tests. It explicitly
sets provider execution, production execution and hypothesis eligibility to
false. Both arms enforce the same
provider/token/tool/iteration/wall-time budget before model calls and tool
execution. A macOS Seatbelt fixture denies repository reads, network and
outside-sandbox writes; hidden tests are materialized only into a host-side
evaluator copy.

The paired provider smoke progressed through three retained invalid trials:
V1 omitted stock template variables and accepted a zero-call run, V2 exported
five active rounds as zero iterations, and V3 failed to receipt an empty
controller response. Hash-bound V4 repaired those boundaries. On its one
exposed task, ordinary and active both passed the independently replayed hidden
evaluator. Active used `5` calls / `15464` tokens / `5` tools / `5` iterations;
ordinary used `8` / `16398` / `10` / `8`. Active wall time was slower
(`37.11s` versus `13.66s`). Provider provenance, source stability, cleanup,
false-success and regression gates passed.

This completes Phase 0 live-provider conformance only. The success tie and
mixed cost result establish neither superiority nor efficiency. Phase 1 remains
closed. Exploratory acquisition rules now pin a specific 500-row SWE-bench
Verified revision, a minimum 60-task eligible pool, six-stratum/20-task
fixed-seed sample and repeated base/gold Docker preflight. The source audit
passed and produced a deterministic 500-candidate redacted inventory
(`6a49facd...33662`) with zero project-exposed IDs; hidden issue/patch/test
content was not persisted. The current arm64 host still has no running Docker
daemon and only about 3.9 GiB free versus the frozen 120 GiB requirement.
Therefore no eligible pool, exploratory protocol or unseen outcome was
generated.

## 11. Rejected and invalid trials

Invalid trials are part of the evidence record, not disposable noise.

| Trial | Why excluded | What it taught the program |
|---|---|---|
| E2-V1 | Under-specified protocol | Freeze propositions and legal omission before running |
| J-V1 | Actions wrote derived outcomes | Separate action effect from evaluator derivation |
| E3-C4 | Runner bypassed frozen adapter | Reuse the same implementation and audit semantic provenance |
| J-X1 | Bound invalid E3 prerequisite | Prerequisite validity must block joint execution |
| RT-E2-X1 | Bundle support narrower than freshness claim | Claims must not exceed bound evidence scope |
| RT-E3-C1 V1 | Runner and event-derived permission counts not frozen | Bind evaluator and permission accounting before confirmation |
| C1 V1/V2 | Persistence/receipt replay drift | Recompute receipts from complete step identity |
| C2 V1 | Runner drift after metric repair | Bind accepted runner before dependent experiments |
| C3 V1 | Bound invalid C2 dependency | Dependency validity is noncompensating |
| Sandbox V1 | Incomplete public specification and no content replay | Preserve exact written content and fair policy input |
| Sandbox V3/V4 | Provider response failures aborted the run | Persist malformed responses as failed attempts without swallowing network/auth failures |
| Comparative V1 | Invalid action responses were not receipted | Persist raw decisions and schema failure provenance |
| Comparative V2 | Representation-only action JSON exhausted autonomous budget | Apply one arm-neutral deterministic adapter |
| Comparative V3 | Source changed concurrently during execution | Preserve the invalid run and repeat only after repository stability |
| mini-SWE V1 | Stock template variables missing; zero-call completeness accepted | Bind native template inputs and require explicit nonzero provider evidence |
| mini-SWE V2 | Active rounds exported as zero top-level iterations | Reconcile active control rounds into final common-budget usage |
| mini-SWE V3 | Empty controller response rejected before billable receipt | Receipt malformed/empty provider responses before fail-closed termination |

## 12. What is verified

The following claims have direct supporting experiments:

- trajectory and EvidenceBundle integrity can fail closed;
- active measurement can preserve diagnosis while reducing logical measurement work;
- E2 survives independent synthetic nuisance and formal real-trajectory evaluation;
- E3 can select safer actions, recover from no-op/partial effects and stop correctly;
- receipt and delayed-validation evidence can be reconciled with provenance;
- frozen E2 and E3 mechanisms pass an independent synthetic joint holdout;
- strict read-only provider trajectories can be acquired without repository mutation;
- mechanism-aware E3 proposals improve on a new provider-backed corpus;
- sandbox writes and rollback remain scoped outside the source repository;
- active control preserves known strengths while improving targeted weaknesses;
- the final E2-measurement-to-E3-repair loop improves task outcome in the frozen
  provider-backed disposable-sandbox strata.

## 13. What is not verified

The evidence does not establish:

- production Controller safety or net gain;
- unrestricted command execution or source-project mutation safety;
- external side-effect safety such as remote services or production data;
- population-level gain across arbitrary repository tasks;
- superiority or efficiency over a model-directed agent on an independent holdout;
- cross-provider, cross-model or cross-scale transfer;
- real-model expert specialization;
- routing causality beyond the fixed synthetic R1 harness;
- asynchronous/concurrent production receipt delivery;
- production fault prevalence or calibrated economic cost.

These are separate claims and require new pre-registered experiments. They must
not be inferred from the accepted V5 sandbox result.

## 14. Primary artifacts and reproduction

The accepted final artifacts are:

- [Protocol V5](../../experiments/real_sandbox_execution/REAL_SANDBOX_ACTIVE_ITERATION_PROTOCOL_V5.json);
- [Task suite V3](../../experiments/real_sandbox_execution/REAL_SANDBOX_TASK_SUITE_V3.json);
- [Invalid trial V3](../../experiments/real_sandbox_execution/REAL_SANDBOX_ACTIVE_ITERATION_INVALID_TRIAL_V3.json);
- [Invalid trial V4](../../experiments/real_sandbox_execution/REAL_SANDBOX_ACTIVE_ITERATION_INVALID_TRIAL_V4.json);
- [V5 execution](../../experiments/real_sandbox_execution/v5/execution.json);
- [V5 result summary](../../experiments/real_sandbox_execution/v5/result_summary.json).

The final content identities are:

| Artifact | SHA-256 |
|---|---|
| Protocol V5 | `9a78e35ecd8bb46cab69eb6968d33de756f8795f450c8e44adc737f8fab9a4ae` |
| Task suite V3 | `ff1b9bc8d6d0a1986027f6e8430ad10127e539fa1dce0753151f18d2788500f3` |
| Runner | `0eb3a462140b7c2bd83ba47721551da43b665f584d73fb0173559c560685cd4e` |
| Execution | `3361a3f8d945fe3a6a01abf2e3ce2aedbef3ec8277b89365a230f0f47417025c` |

Offline verification:

```bash
PYTHONPATH=Code/src pytest -q Code/tests/test_real_sandbox_active_iteration.py
PYTHONPATH=Code/src pytest -q Code/tests
```

The final complete repository test result was `1195 passed`. Offline tests
replay contracts and persisted outcomes; they do not repeat provider calls.

## 15. Current program state

The core active-iteration milestone is complete at the scoped
provider-backed disposable-sandbox evidence level.

The next work should be selected by the claim the project wants to make:

- superiority over a model-directed agent requires a new matched three-arm
  `fixed_order` / `model_directed` / `active_iteration` experiment with equal
  model, task, tool and budget boundaries;
- production integration requires a separate authorization, rollout and
  rollback program;
- routing claims require R2/R3 with the expert pool held fixed;
- model-independent claims require M1/M2 cross-provider and cross-scale
  confirmation;
- general repository-task claims require a larger pre-registered task
  distribution and population-level analysis.

None of those extensions is implied by the current core-loop completion.
