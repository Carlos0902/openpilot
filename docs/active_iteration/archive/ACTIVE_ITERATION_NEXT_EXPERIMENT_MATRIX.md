# Active Iteration Experiment Matrix

> Status: current completion and extension matrix. The scoped core milestone is
> complete through RS-V5. No item below authorizes production execution,
> unrestricted commands, source-project mutation or model replacement.

## Current Boundary

The core active-iteration milestone is complete at the scoped
provider-backed disposable-sandbox level. The evidence chain now includes:

- **Active measurement:** on frozen real trajectories, an adaptive measurement
  policy preserved diagnosis/alignment/freshness while reducing logical
  measurement cost.
- **Mechanism-aware proposal selection:** on a new provider-backed holdout, a
  frozen failure-mechanism map improved read-only proposal matching from `12/16`
  to `16/16` without safety or freshness regression.
- **Scoped execution effect:** on the frozen eight-task/four-stratum V5 sandbox
  suite, objective success improved `2/8 -> 8/8` with six paired improvements,
  zero regressions, eight measurement-assisted successes and zero false success.

This does not establish production execution, arbitrary-task generalization,
expert-routing causality or cross-model transfer.

## Validation Scope

The current milestone is the **core active-iteration architecture**: active
measurement, mechanism-aware action choice, scoped execution, fresh
post-action verification, recovery and stopping. Expert specialization/router
causality (R1-R3) and cross-model transfer (M1-M2) are deferred extensions and
do not block this milestone. They remain necessary only for claims about expert
routing or model-independent generalization.

S1-S3, C1-C3 and the V5 disposable real-task sandbox confirmation have passed
their gates. There is no remaining blocking experiment for the scoped core
milestone. Production execution remains outside scope and would require a new
authorization and rollout program.

## Experiment Inventory

The table retains the original 11-item work breakdown for auditability. Only
the safety/control rows and the disposable real-task confirmation are active in
the current milestone; routing and transfer rows are deferred. A failed
experiment remains a result and may require a new protocol version.

| ID | Lane | Main question | Minimum exit condition | Dependency |
|---|---|---|---|---|
| S1 | Execution safety | Can a proposed action be applied only inside a disposable worktree? | passed contract preflight: path/source fingerprint/cleanup gates; zero escape | none |
| S2 | Execution safety | Are writes, commands, retries and partial failures captured as receipts? | passed contract preflight: immutable receipts; missing/duplicate/cross-run fail closed | S1 contract |
| S3 | Execution safety | Does rollback restore the original worktree under injected faults? | passed V1 synthetic disposable-sandbox matrix: 5 rolled back, 1 cleanup-inconclusive, severe/false success `0/0`, source unchanged | S1, S2 |
| C1 | Closed-loop control | Does the controller choose measure/action/recover/stop better than fixed control on unseen synthetic cases? | passed V3: recovery/fresh success `6 -> 18` of 24, severe/false success `0/0`, matched diagnosis and per-stratum noninferiority | E2/E3 contracts; synthetic receipt shape |
| C2 | Closed-loop control | Does the loop stop correctly under budget, no-gain, regression and external-block conditions? | passed V2 after V1 runner-drift rejection: correct stop `12 -> 30` of 30, successful closure `6 -> 6`, treatment false success/unsafe continuation `0/0` | C1 |
| C3 | Closed-loop control | Does active iteration retain known strengths while fixing a targeted weakness? | passed V2 after invalid C2-binding trial: known strengths `16 -> 16`, target weaknesses `0 -> 16`, total correct `16 -> 32`, severe/false success `0/0`, every stratum noninferior | C1, C2 |
| R1 | Deferred extension | Does fixed expert specialization beat an unqualified same-model baseline at equal budget? | deterministic harness preflight passed; real-model effect deferred | not blocking core milestone |
| R2 | Deferred extension | Does routing add value beyond fixed expert assignment? | matched expert pool; router-only treatment; route regret and safety gates | deferred |
| R3 | Deferred extension | Can an expert be added, retired or rolled back without breaking old routes? | registry/version/rollback replay and zero stale authorization | deferred |
| M1 | Deferred extension | Does the frozen mechanism survive a second model/provider at matched budget? | interaction analysis, no critical regression, independent holdout | deferred |
| M2 | Deferred extension | Does the complete loop preserve relative gain across scales? | same task strata and hidden evaluator; pre-registered confidence intervals | deferred |
| RS-V5 | Core milestone | Does E2 measurement followed by E3 repair improve independently evaluated outcomes in a disposable sandbox? | passed: `2/8 -> 8/8`, six improvements, zero regression/false success, all strata improved, source unchanged | S1-S3, C1-C3, formal E2/E3 shadow |

## Current Schedule

S1-S3 and C1-C3 are complete at the synthetic evidence level, and RS-V5 closes
the scoped real-sandbox execution gate. No automatic next experiment is implied.
Future work must be chosen by the next desired claim: production integration,
routing causality, cross-model transfer or broader task-distribution validity.

## Gating Order

The shortest defensible path to the current core active-iteration claim is:

```text
S1 -> S2 -> S3
          └-> C1 -> C2 -> C3
                         └-> RS-V5 disposable real-task confirmation (passed)
```

S1/S2 are safety contracts rather than evidence of capability gain. S3 is
synthetic disposable-sandbox safety evidence, not real-task execution. C1-C3
test the active diagnose-control-act-stop loop. R1-R3 and M1-M2 remain separate
extension claims and are not part of the current completion gate.

## What Counts as Completion

The core active-iteration claim can be reported only if all of the following
are true:

- C1-C3 pass their primary non-compensating gates on unseen holdouts;
- S1-S3 pass without any escape, unrecovered rollback or false-success case;
- a disposable real-task execution confirms task-outcome improvement with
  independent evaluation and no source escape, rollback failure or false success;
- all failed, incomplete and invalid trials remain in the denominator and the
  independent evaluator never exposed hidden outcomes to the loop.

These conditions are now satisfied for the scoped V5 provider/model and task
strata. The correct status is **core mechanism validated through bounded
provider-backed disposable-sandbox execution**. Expert routing, cross-model
transfer, production execution and arbitrary-task generalization remain
explicitly out of scope.

## First Safety-Lane Result

S1 and S2 passed their deterministic boundary preflights. S3 V1 then executed
six frozen fault classes only inside disposable sandboxes: pre-write,
post-first-effect, partial effect, retry, transient rollback failure and cleanup
failure. Five cases restored and cleaned normally; cleanup failure remained
inconclusive even though safety cleanup completed. Source bytes were unchanged,
unrecovered severe cases and false success were `0/0`, and missing, duplicate
and cross-run receipt probes all failed closed. This does not authorize real
task or production execution.

## First Closed-Loop Result

C1 V3 passed a 24-case independent synthetic holdout. Both arms received the
same frozen diagnosis trace and the action outcome remained outside the policy
view. Fixed control performed action, measurement and stop; active control could
select a recovery action from the fresh measurement before stopping. Recovered
and fresh-success cases improved `6 -> 18`, while severe failures, false success
and treatment-only severe cases remained zero. All four hidden-outcome strata
passed recovery, freshness and safety noninferiority independently, and all 12
externally blocked arm-cases stopped correctly. C2 V2 then passed a 30-case
long-horizon adversarial holdout: correct stopping improved `12 -> 30`, the
active arm had zero false success and zero unsafe continuation, and all five
stop strata passed `6/6` independently while designated successful closure was
retained at `6 -> 6`. C3 V2 then passed a 32-case independent synthetic
holdout: known-strength outcomes remained `16 -> 16`, both target-weakness
strata improved `0/8 -> 8/8`, and aggregate correct outcomes improved
`16 -> 32` with zero severe regression or false success. C3 V1 is retained but
excluded because it bound the invalid C2 V1 protocol. C1-C3 now support the
synthetic closed-loop control claim only; they do not authorize real or
production execution.

## Accepted Sandbox Result

RS-V1 was methodologically invalid and RS-V2 produced a valid `4/4 -> 4/4`
control-ceiling result. V3 then froze eight tasks, four strata and all
noncompensating gates before outcome generation; V3/V4 were retained as
incomplete invalid trials after malformed provider responses interrupted them.
V5 bound those records without changing the suite or thresholds.

Accepted V5 produced control/active success `2/8 -> 8/8`, six paired
improvements, zero paired regression, eight measurement-assisted successes,
zero false success and improvement in all four strata. Provider execution was
complete, the source repository was unchanged, every sandbox was cleaned and
all persisted public/hidden outcomes replay independently. This closes the
scoped core milestone only.

## First Routing-Lane Result

R1 V1 passed a deterministic synthetic specialization-harness preflight over 24
paired cases. Model identifier, budget, tools, task input and fixed route were
held constant; only role context and output contract differed. Success and
safety changed `8/24 -> 24/24`, with severe failures `16 -> 0`. Because the
"model" is deterministic and synthetic, this is not a real-model effect and it
does not test routing causality. A provider-backed read-only R1 confirmation and
R2 remain open.
