# Stage 8 Task Designer context campaign V1 result

## Decision

The compact Task Designer projection passed the frozen mechanism threshold in
all three quality-matched pairs. Across the six arms, compact projection reduced
Task Designer provider input from 11,979 to 2,778 tokens (−76.81%) and final
prompt size from 11,904 to 2,703 tokens (−77.29%). Every pair had the same
76.81% provider-input reduction, above both the 10% first-compact threshold and
the 25% median threshold.

This result validates the compact projection mechanism for the frozen
divide-docstring task under the full architecture. It does not establish a
distribution-wide causal effect and does not automatically switch the
production default. A broader task mix is required before making that policy
change.

In this fixture the fixed goal was not associated with the frozen diagnosis or
the two environment-memory records. Compact therefore retained instruction,
schema, goal, safety, validation, calculator, and README candidates, while
removing both unrelated memory candidates (216 and 211 selected tokens) and the
diagnosis candidate (2,582 selected tokens from a 6,155-token raw candidate).
This campaign validates relevance-based omission of unrelated evidence; it does
not yet exercise the compact branch that retains a goal-relevant diagnosis or a
relevant autonomous-iteration task result.

The authoritative result is
`runs/stage8_campaign_v1_20260804T085747Z/campaign_analysis.json`. All three
pairs were eligible; none was excluded.

## Primary result

| Pair | Order | Current provider input | Compact provider input | Reduction | Current final prompt | Compact final prompt |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | current → compact | 3,993 | 926 | 76.81% | 3,968 | 901 |
| 2 | compact → current | 3,993 | 926 | 76.81% | 3,968 | 901 |
| 3 | current → compact | 3,993 | 926 | 76.81% | 3,968 | 901 |
| **Total** | crossed | **11,979** | **2,778** | **76.81%** | **11,904** | **2,703** |

The source fingerprint was identical in all six arms:
`sha256:f15833e3b2ab5eff0d352b82986792032e79c886cf24650cf7f6ef8dbaa559e3`.
Provider identity, code snapshot, isolated empty-memory baseline, typed request
facts, reasoning-disabled mode, and production dynamic completion policy also
matched the frozen protocol.

Completion reservations were derived by the production coordinator rather than
held at an artificial arm constant. The 3,968-token current prompt crossed the
3,000-token prompt-bonus threshold and reserved 1,150 tokens; the 901-token
compact prompt reserved 1,000. This is the expected dynamic response to prompt
size, not a second manually assigned treatment.

## Quality and execution evidence

All six arms passed the same structured Task Designer contract and the same
full-architecture quality gate:

- one task for `campaign-divide-docstring`;
- only the authorized `calculator.py` target;
- acceptance criteria exactly matching the frozen goal;
- goal, safety, validation, and project-file evidence roles retained;
- core success, verification, improvement, mutation scope, required commands,
  unchanged test file, and fixed decomposition all passed.

The request builder fix that renders stable candidate IDs as
`[evidence_id="..."]` made those IDs visible to the model. Both arms returned
the same valid retained IDs, including `iteration_task_design:goal`,
`iteration_task_design:safety`, `iteration_task_design:validation`, and the
authorized project-file ID. This closes the earlier mismatch in which the
schema asked for evidence IDs that the prompt did not expose.

Provider usage coverage was 100% for every arm. There were zero failed provider
attempts, zero transport retries, zero completion recoveries, and zero unknown
failed-usage records. Each arm did record the existing review-level planning
signal `no_progress_rounds=4`; it did not require a fix and did not invalidate
the frozen quality gates.

## Secondary observations are non-causal

The campaign spent 76,808 lifecycle tokens in total. This total is the sum of
all six arms, not a projected saving.

| Secondary measure | Current arms | Compact arms | Difference |
| --- | ---: | ---: | ---: |
| Task Designer provider output | 596 | 539 | −57 |
| Enhancement total | 18,655 | 9,326 | −9,329 |
| Lifecycle total | 43,213 | 33,595 | −9,618 |

These values are descriptive only. The campaign froze Task Designer source
facts and isolated the projection policy for its primary input measures, but
provider output and upstream core output remained stochastic. Therefore the
enhancement and lifecycle totals must not be presented as causal savings from
compact projection.

The generic enhancement cost collector also marked every arm
`eligible=false` / `cost_conclusion_status=incomplete`, because this fixed-goal
trajectory intentionally bypassed a provider `iteration_goal` call and used
`code_edit` rather than `code_generation`. That generic eligibility result does
not invalidate the purpose-specific Task Designer input comparison, but it is
another reason not to promote enhancement or lifecycle totals to primary cost
claims.

## Diagnostic-only run and claim boundary

The earlier directory `runs/stage8_campaign_20260804T084822Z` remains
diagnostic-only and is not included in the totals above. Its first pair exposed
two protocol errors: a fixed 1,150-token reservation gate that rejected the
production-derived compact reservation of 1,000, and a final-file hash gate
that treated pre-treatment core-provider wording as a Task Designer outcome.
The campaign was refrozen and all six arms were restarted in a new directory.

The allowed claim is exactly
`fixed_source_full_architecture_mechanism_only`: on this frozen source and task,
the production compact projection sharply reduced actual Task Designer input
while preserving the specified task contract and full-architecture quality.
The result does not prove the same effect across different goals, repository
sizes, diagnosis shapes, providers, or memory states. Compact projection should
remain an explicit policy until those cases are covered by a broader campaign.
