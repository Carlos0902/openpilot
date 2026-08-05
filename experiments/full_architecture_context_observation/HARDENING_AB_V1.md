# Context hardening A/B result

The frozen baseline is `20260803T145617Z`. The rejected 800-token risk gate is
`20260803T160609Z`; the revised 2,000-token gate is `20260803T161043Z`.

| Run | Requests | Completed usage | Failed-call reservation | Core task |
|---|---:|---:|---:|---|
| Baseline | 5 | 15,623 | unknown | failed, 1/3 subtasks |
| 800 ceiling | 7 | 17,091 | 2,400 | failed, invalid controller JSON |
| 2,000 dynamic ceiling | 10 | 16,581 | 5,076 | 5/5 subtasks, tests and compile passed |

Completed usage excludes failed provider calls. Reservation is a conservative
upper bound, not provider-reported usage. Baseline's timed-out uncapped request
cannot be bounded from saved evidence. Different stochastic decompositions mean
aggregate totals are not a clean causal comparison.

Mechanism evidence is clearer: same-purpose assembled tool-event prompts stayed
near 1,535–1,589 tokens after history appeared, versus baseline growth of
1,284 → 1,611 → 1,951. Successful tool-event outputs were 1,453 and 487 tokens,
versus 4,076 and 2,072. Three capped calls still returned incomplete/empty JSON,
but deterministic fallback completed the edit and verification. Final runtime
failure occurred afterwards when project-improvement context could not fit.

Conclusion: bounded history is validated directly. Completion budgeting now
prevents unbounded single-call and hidden JSON-repair multiplication while
preserving the core task in this gate, but 2,000 is not universally optimal.
Extend the policy to other purposes only behind purpose-specific quality gates.

## Correctness hardening follow-up

The earlier 2,000-token gate exposed a correctness defect hidden by the phrase
"fallback completed": a read-only subtask could fall through to whole-file
generation, truncated JSON could trigger a fresh generation, and a successful
`compileall` invocation could be accepted for a requested `pytest` validation.
Those are execution-contract failures, not acceptable token-saving tradeoffs.

The hardened run is `20260803T172926Z`. Its task decomposition emitted four
typed subtasks with explicit read/write scopes and exact validation commands.
The recorded trajectory contains:

- three read calls and no mutation in the inspect subtask;
- one write, scoped only to `calculator.py`, in the implement subtask;
- an actual `python -m pytest -q` call for the pytest validation;
- a later, separate `python -m compileall -q calculator.py` validation.

All nine recorded tool calls succeeded. Independent verification using the same
host interpreter reported `3 passed`; compileall also passed. The disposable
project's private `.venv` did not contain pytest, so host-interpreter availability
is an explicit reproducibility limitation of this fixture.

The run still ended at the later project-improvement tail because required
context could not fit. That remains a separate context-owner coverage gap; it
does not invalidate the core execution evidence, and the run must not be labeled
as a fully successful top-level pipeline.

Failed controller attempts now retain provider-reported usage, finish reason,
and partial response artifacts. A length-limited attempt may grant one bounded
bonus to the next controller allowance; empty responses do not cause mechanical
budget shrinkage. Purpose-aware fallback remains fail-closed when write scope or
the exact validation command is absent.

## Project-improvement context and completion follow-up

The later budget failure was localized more precisely: the improvement analysis
had already completed, while `iteration_task_design` wrapped its instruction,
goal, full project-state projection, improvement report, and schema into one
required, non-truncatable message. The same architectural risk existed in
`iteration_goal` and `project_improvement`, even though neither was the direct
failure point in that run.

All three purposes now submit candidate-level context. Instructions, the active
goal, safety constraints, and compact validation evidence remain required;
README, individual files, diagnosis, memory, and historical evidence can be
selected or omitted independently. Deterministic oversized fixtures exercise
the real adapters and prove that optional growth no longer turns the entire
request into one required block. A genuinely oversized required constraint
still fails before provider transport.

Completion semantics are now explicit. Automatic project improvement is an
optional enhancement, an explicitly positive `--improvement-iterations` value
is a required gate, and zero disables the stage. Core success is recorded
separately: optional failure remains visible but does not rewrite the verified
core result; required failure changes overall success while preserving the core
evidence.

Two post-change provider observations were inconclusive. Run
`20260803T180924Z` stopped during upstream task decomposition after a fail-closed
write-scope check. Run `20260803T181040Z` stopped because a compound validation
task did not produce exact per-command completion evidence. Neither reached the
improvement stage, so neither is evidence of project-improvement Token savings
or model quality. The deterministic context and completion fixtures are the
acceptance evidence for this stage; a provider counterfactual remains future
work after the upstream trajectory is made reproducible.

## Frozen counterfactual and single provider arm

The subsequent frozen-boundary replay isolates the Task Designer assembly
mechanism without calling a provider. With the same source events, tokenizer,
and 4,096 requested / 128 reserved policy, the legacy required message contains
96,209 characters and 29,342 tokens and fails with `budget_insufficient`. The
current candidate adapter contains 17,981 original tokens, selects 3,968, and is
`ready`. No required candidate is omitted or partially retained. The selected
required safety candidate contains all three authoritative non-regression
constraints from validation context, and the goal, schema, delivery surface,
and compact validation gates pass.

One fixed-decomposition provider arm, `20260803T183221Z`, then reached project
improvement after all four core tasks completed. Inspect remained read-only,
only `calculator.py` was written, the test-file hash was unchanged, and separate
exact `pytest` and `compileall` commands both exited zero. This establishes a
valid upstream trajectory, not a paired Token comparison.

The arm produced successful `project_improvement` and
`iteration_task_design` requests. `iteration_goal` was bypassed because Goal
Maker selected the report-derived seed goal deterministically, so this is not a
complete four-purpose sample. The next iteration then stopped before provider
transport when `code_generation` required context could not fit. Optional
completion semantics behaved as intended: the verified core result remained
successful while the failed enhancement, zero completed improvements, and
remaining goal stayed visible.

Seven successful provider responses total 22,511 tokens. The one failed,
length-limited controller attempt has retained provider usage of 3,642 tokens,
including 2,000 reasoning/output tokens, making total observable attempt usage
26,153 tokens. The analyzer now reports responded, failed-attempt, and total
observed usage separately; post-run recomputation has 100% observed usage and
reasoning coverage. It also rejects the sample for cost comparison because only
2/4 target purposes were transported (`iteration_goal` was deterministic and
`code_generation` did not reach transport).
Full measurements and claim limits are recorded in
`PROJECT_IMPROVEMENT_CONTEXT_AB_RESULT_V1.md`. This arm does not support paired,
distribution-wide, or Token-causal claims.
