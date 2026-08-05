# Stage 8 Task Designer context campaign V1

This campaign tests one mechanism: whether the production `compact` Task
Designer projection lowers actual provider input tokens relative to the
production-compatible `current` projection. It does not compare completion
budget policies. Both arms use the same production dynamic enhancement budget
and reasoning remains disabled.

The reservation is not a fixed arm constant. The runner records the typed
request facts (`routine`, high remaining value, three remaining calls), prompt
tokens, effective production policy, and remaining pool. It asks the production
completion-budget coordinator to derive the expected reservation and requires
the observed reservation to match. With the frozen workload, a prompt at or
above 3,000 tokens receives the production prompt bonus (1,150 tokens in the
diagnostic current arm), while the compact 901-token prompt correctly derives
1,000 tokens. These values follow from prompt size and typed request facts, not
from arm names.

## Isolation

The campaign runs three crossed pairs in this order:

1. current, compact
2. compact, current
3. current, compact

Every arm uses the same provider, cache-off setting, zero transport retries,
fixed decomposition, fixed divide-docstring goal, isolated empty memory store,
and full-architecture calculator run. The only arm-specific value is
`projection_policy` passed to the production
`build_iteration_task_design_candidates` builder.

Task Designer input facts are frozen from the first eligible Stage 7 V4 arm.
Before every campaign, the runner verifies the whole trajectory file hash and
the canonical hashes of the saved project-state and improvement-report
payloads. It then replaces both `/var/.../<project>` and
`/private/var/.../<project>` roots with `.`. The resulting project-relative
source has the frozen fingerprint
`sha256:f15833e3b2ab5eff0d352b82986792032e79c886cf24650cf7f6ef8dbaa559e3`.
This prevents upstream provider variation and stale temporary paths from
becoming arm differences.

## Primary and secondary measures

The primary measures are only:

- Task Designer provider input tokens;
- Task Designer `context_selection.final_prompt_tokens`.

Task Designer output, enhancement totals, and lifecycle totals are retained as
non-causal secondary observations. An eligible result requires three pairs,
complete provider usage, identical source fingerprints and code snapshots,
the exact call contract, and equal quality evidence. Equal Task Designer quality
requires exactly one task for the frozen goal, only the authorized
`calculator.py` target, acceptance criteria exactly equal to the frozen goal,
and valid retained evidence covering goal, safety, validation, and project-file
roles. The existing Stage 7 quality signature, required commands, and mutation
scope must also pass.

The final `calculator.py` hash is recorded descriptively but is not a pair
inclusion field. The core provider repairs `calculator.py` before the Task
Designer treatment begins, and equivalent repair wording can vary between
fresh arms. Treating that upstream wording as a Task Designer outcome would
introduce a pre-treatment confound.

The mechanism threshold requires lower Task Designer input in every pair, at
least 10% reduction in the first compact arm, and at least 25% median paired
reduction. These fixed-task results cannot establish distribution-wide
causality.

## Fail-closed conditions

An arm or pair is inadmissible if required context is omitted or partially
retained, the source fingerprint changes, provider identity or code snapshot
changes, completion policy or isolated memory is not effective, usage is
missing, a transport retry occurs, the reservation differs from the production
derivation, or the structured Task Designer/command/mutation contract fails.

## Diagnostic-only first pair

`runs/stage8_campaign_20260804T084822Z` completed one current/compact pair. It
showed the intended input signal (3,993 versus 926 provider input tokens), but
the campaign stopped because the original protocol incorrectly required a
1,150-token reservation in both arms. Production correctly derived 1,000 for
the compact prompt after the prompt fell below 3,000 tokens. The original pair
gate also compared exact final file hashes even though the differing docstring
wording was produced by the core provider before the projection treatment.

That directory remains immutable diagnostic evidence and is excluded from the
refrozen campaign. All six arms must restart in a new output directory and a
single new code snapshot; no old arm is carried into the three-pair result.

Dry-run planning is the default and makes no provider calls:

```bash
PYTHONPATH=Code/src:experiments/full_architecture_context_observation \
python experiments/full_architecture_context_observation/stage8_task_designer_context_campaign.py
```

Paid execution requires the explicit flag and a new output directory:

```bash
PYTHONPATH=Code/src:experiments/full_architecture_context_observation \
python experiments/full_architecture_context_observation/stage8_task_designer_context_campaign.py \
  --execute \
  --output-dir experiments/full_architecture_context_observation/runs/stage8_task_designer_context_v1
```

## Completed campaign

The refrozen campaign completed all six fresh arms under
`runs/stage8_campaign_v1_20260804T085747Z`; all three crossed pairs were
quality-matched and eligible. Compact projection reduced aggregate Task Designer
provider input from 11,979 to 2,778 tokens (−76.81%) and aggregate final-prompt
tokens from 11,904 to 2,703 (−77.29%). It passed the frozen mechanism threshold
with zero failed provider attempts, transport retries, or completion recoveries.

The compact prompt correctly derived a 1,000-token completion reservation while
the current prompt derived 1,150, because only the latter crossed the production
prompt-bonus threshold. Stable rendered evidence-ID headers also allowed both
arms to return the same valid goal, safety, validation, and project-file
evidence roles.

See `STAGE8_TASK_DESIGNER_CONTEXT_CAMPAIGN_V1_RESULT.md` for the per-pair result,
76,808-token campaign spend, non-causal secondary observations, and the
`fixed_source_full_architecture_mechanism_only` claim boundary. This result does
not automatically change the production projection default.
