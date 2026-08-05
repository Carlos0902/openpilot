# Project-improvement context A/B result V1

## Scope

This result combines two different evidence layers:

1. a deterministic, offline frozen-boundary replay of legacy and current Task
   Designer context assembly; and
2. one real provider arm using the fixed four-task decomposition.

The replay establishes an assembly mechanism and its quality gates. The provider
arm establishes one reachable full-runtime trajectory. There is no equal-success
legacy provider arm, no paired sample, and no replication, so this result does
not estimate Token causality or a distribution-wide benefit.

## Offline frozen-boundary replay

Both variants load the authoritative trajectory from run `20260803T172926Z`,
merge seq 44 project state with the context produced at seq 45, and reuse the
same seq 48 improvement report, seq 51 selected goal, exact DeepSeek tokenizer,
and 4,096 requested / 128 reserved prompt policy. Source payloads and the
experiment-owned legacy template are hash locked.

| Variant | Original chars | Original tokens | Final tokens | Status |
|---|---:|---:|---:|---|
| Legacy required message | 96,209 | 29,342 | 0 | `budget_insufficient` |
| Current candidate adapter (Stage 7 replay) | 24,144 | 6,785 | 3,967 | `ready` |

The current adapter selected eleven candidates; one optional candidate was
partially retained and none was omitted. Candidate decision coverage was 100%;
no required candidate was omitted or partially retained. Its selected required
representation passed the following hard gates:

- selected improvement goal and output schema are present;
- compact validation evidence is present;
- delivery surface `project_native` is present;
- all three validation-context non-regression constraints are present in the
  selected required safety candidate;
- assembly is `ready` within the unchanged effective prompt budget.

The Stage 7 replay reflects the bounded delta/task schema and compact projections
now in production. It reduces the original candidate set to 6,785 tokens while
retaining the authoritative safety facts; the selected request remains within
the unchanged 3,968-token effective budget (3,967 selected tokens).

## Real provider arm

Run `20260803T183221Z` used the optional project-improvement policy and the
hash-locked `calculator-four-stage-decomposition-v1` fixture. The fixture hash
was `sha256:62bc25e5240a11c8cc1171a7d578b7fc7595642ecf9136d3d31a092d79443de3`.
The upstream gate was `valid` and was not censored.

The immutable manifest retains the historical
`protocol_id=full-architecture-context-observation-v1` label, even though that
run installed a fixed decomposition. For this historical run, its recorded
`fixed_decomposition` descriptor is the authoritative intervention evidence.
Future fixed arms use `PROJECT_IMPROVEMENT_PROVIDER_ARM_PROTOCOL_V1.json`;
`OBSERVATION_PROTOCOL_V1.json` is restored to intervention-free semantics.

Core execution evidence was correct:

- inspect read `calculator.py` and `test_calculator.py` without mutation;
- implement wrote only `calculator.py`;
- `test_calculator.py` retained hash
  `c91400d8fcd3fc2557b4d4db8f4b559b08973ea7630b88946c2cc0156084abbe`;
- `python -m pytest -q` executed exactly, exited zero, and reported three tests
  passed;
- `python -m compileall -q calculator.py` executed separately and exited zero.

The runtime recorded four completed and zero failed core tasks, core success,
and passed verification. It also performed additional compile checks; those do
not replace the two required exact command records above.

## Target-purpose coverage

| Purpose | Original assembled | Final assembled | Provider input | Provider output | Result |
|---|---:|---:|---:|---:|---|
| `project_improvement` | 878 | 878 | 981 | 2,727 | responded, `ready` |
| `iteration_task_design` | 9,895 | 3,968 | 4,071 | 3,727 | responded, `ready` |
| `iteration_goal` | — | — | — | — | deterministic bypass |

For both submitted target requests, all five required candidates were kept in
full and no required candidate was omitted. The Task Designer request retained
the three safety constraints despite selecting only 3,968 of 9,895 original
assembled tokens.

There was no `iteration_goal` provider request. Goal Maker selected
`seed_action_1` deterministically from the improvement report, then emitted the
goal pipeline events without model transport. Consequently this run is a valid
two-purpose observation. Under the current four-purpose observation window it
also lacks a transported `code_generation` request, so it is not a complete
full A/B acceptance sample.

## Next context-owner gap and completion semantics

Task design produced a schema-complete task targeting only `calculator.py`.
During the next execution step, `code_generator` failed before provider
transport because its required context could not fit the configured prompt
budget. No file was changed by that failed enhancement, no retry was attempted,
and completed improvements remained zero of one.

This exposes the next purpose-specific context-owner gap: successful Task
Designer assembly does not imply that downstream `code_generation` has a legal
bounded projection.

The optional completion policy behaved correctly. The top-level result remained
successful because all core work and verification had passed, while the runtime
also recorded `partial_success`, `project_improvement_status=failed`, the failed
tool and stage, and the remaining goal. The enhancement failure was visible and
did not erase or falsely relabel the core evidence.

## Provider attempts and accounting

The arm made eight logical requests. All eight provider attempts are observable:
seven responded and one failed. The seven successful responses report:

- input tokens: 11,272;
- output tokens: 11,239;
- total tokens: 22,511.

The failed `tool_event_decision` attempt reports:

- input tokens: 1,642;
- output tokens: 2,000;
- reasoning tokens: 2,000;
- total tokens: 3,642;
- finish reason: `length`;
- JSON attempt: 1 of 1, with an empty visible response;
- transport retries: zero.

Therefore total observable provider-attempt usage is:

| Usage | Successful responses | Failed attempt | Observable total |
|---|---:|---:|---:|
| Input | 11,272 | 1,642 | 12,914 |
| Output | 11,239 | 2,000 | 13,239 |
| Total | 22,511 | 3,642 | 26,153 |

All successful JSON responses parsed on their first recorded attempt. Successful
transport histories contain one successful attempt each, and the failed attempt
records zero transport retries. There is no evidence of JSON-repair or transport
retry multiplication in this arm.

The immutable run's original `analysis.json` and guard totals include only the
seven successful responses, so they report 22,511 rather than 26,153. The
analyzer was subsequently corrected and a post-run recomputation now reports
eight logical requests with 100% observed usage coverage, responded totals of
22,511, failed-attempt totals of 3,642, and observed-attempt totals of 26,153.
It also marks target-purpose coverage as 2/4, `iteration_goal_mode=deterministic`,
with `iteration_goal` and `code_generation` missing,
and the cost conclusion as `incomplete` / `eligible=false`. The original run
artifacts remain unchanged for auditability.

## Claim boundary

This evidence supports the narrow claims that candidate assembly restores the
frozen Task Designer request to a legal budget while preserving its required
facts, and that the resulting request was executable in one real full-runtime
arm after correct core execution.

It does not support a paired Token-savings claim, a model-quality comparison, a
complete four-purpose sample, or a claim that project-improvement context is
fully solved. `iteration_goal` coverage and downstream `code_generation`
assembly remain explicit gaps.
