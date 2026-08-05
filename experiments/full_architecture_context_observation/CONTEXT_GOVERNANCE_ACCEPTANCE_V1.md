# Context Governance Acceptance V1

## Decision

The context-governance architecture is accepted for continued development. The
mechanisms now prevent the observed unsafe fallback, cumulative prompt reload,
cross-project memory mixing, unbounded output restatement, truncated-code write,
and non-idempotent completion accounting failures. This is not a claim that
late-run context pressure has disappeared or that the new Stage 6 enhancement
budget has a measured provider-level causal benefit.

## Deterministic acceptance evidence

- Full production regression: `861 passed`; source compile and diff checks pass.
- Independent safety/recovery/budget review: no remaining P0/P1.
- Task Designer frozen replay: legacy 29,342 original tokens and
  `budget_insufficient`; current 6,785 original / 3,967 selected tokens and
  `ready`. Original candidate volume fell 76.88%; all required goal, schema,
  validation, delivery-surface, and non-regression gates pass.
- Code Generator frozen replay: legacy 13,324 original tokens and
  `budget_insufficient`; current 2,076 original/selected tokens and `ready`.
  Original volume fell 84.42%; all mutation-boundary, current-source, safety,
  and output-contract gates pass.
- Experiment harness suite: 38 deterministic tests pass. Fixture and immutable
  run directories are excluded from harness collection.

## Provider evidence already available

The existing one-pair full-architecture reasoning pilot kept both arms behind
the same task-quality gates. Typed routine reasoning reduced tool-event output
from 5,276 to 787 tokens (-85.08%), tool-event total from 11,691 to 6,905
(-40.94%), and full observed lifecycle total from 38,789 to 27,975 (-27.88%).
This supports the controller-output mechanism, but one pair is not a statistical
estimate and non-target calls remained stochastic.

## Stage 6 claim boundary

No immutable provider run contains the new enhancement `reservation_id` and
reconciliation evidence. Therefore the original four-purpose dynamic completion pool was
accepted on contract, replay, failure, and deterministic allocation tests only.
Historical unpaired totals must not be used as its A/B result. The experiment
collector now treats `project_improvement`, `iteration_goal`,
`iteration_task_design`, and improvement `code_generation` as one observation
window, includes failed-attempt usage, and exposes reservation/recovery facts;
refund and unknown-usage settlement remain checkpoint-owned facts.

## Next measurement

After the architecture remains stable, run at least three paired
full-architecture repetitions with alternating arm order, identical frozen
decomposition/code state/provider/profile, cache disabled, and separate core and
enhancement windows. Acceptance requires equal task, permission, mutation,
exact-command validation, and rollback outcomes; report all five current
enhancement purposes (`project_improvement`, `iteration_goal`,
`iteration_task_design`, `code_generation`, and `code_edit`), failed attempts,
usage coverage, reservations, one-shot recoveries,
and final checkpoint used/reserved totals. Until then, the supported conclusion
is “the mechanisms work and existing measured signals decrease,” not “context
management is fully solved.”
