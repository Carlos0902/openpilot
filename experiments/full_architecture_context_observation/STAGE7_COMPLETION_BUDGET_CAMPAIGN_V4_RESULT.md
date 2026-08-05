# Stage 7 completion-budget campaign V4 result

## Outcome

The completed retry at
`runs/stage7_campaign_v4_retry_20260804T081517Z` is eligible for the frozen
fixed-task mechanism analysis. All three pairs had equal quality, isolated empty
memory at arm start, complete provider usage, the required real diff and exact
validation commands, no failed attempts, no transport retries, and no length
recovery.

| Window | Static | Dynamic | Change |
| --- | ---: | ---: | ---: |
| Core | 24,026 | 24,336 | +1.29% |
| Enhancement | 18,110 | 18,343 | +1.29% |
| Lifecycle | 42,136 | 42,679 | +1.29% |

Lifecycle pair results were:

| Pair | Static | Dynamic | Dynamic change |
| --- | ---: | ---: | ---: |
| 1 | 14,102 | 14,168 | +0.47% |
| 2 | 14,090 | 14,347 | +1.82% |
| 3 | 13,944 | 14,164 | +1.58% |

The dynamic policy reduced reserved completion capacity from 5,300 to 2,390
tokens per arm (−54.9%), but it did not reduce observed usage. Every enhancement
response ended normally and stayed far below even the dynamic ceiling:
`project_improvement` used at most 207 output tokens against a 600-token dynamic
reservation, `iteration_task_design` at most 237 against 1,150, and `code_edit`
at most 102 against 640. There were no recoveries for either arm.

Across enhancement calls, static used 16,883 input and 1,227 output tokens;
dynamic used 16,908 input and 1,435 output. Thus 208 of the 233-token dynamic
increase came from ordinary stochastic output variation, not retries or prompt
growth. The unaffected core window also increased by 310 tokens, reinforcing
that the observed +1.29% lifecycle difference is not evidence of a budget
saving.

## Interpretation

For this fixed task, the production dynamic completion budget is a successful
worst-case exposure and recovery boundary, but not a normal-case Token
optimizer. Lowering a ceiling does not save tokens when the model already stops
well below it. The campaign therefore does not support a claim that dynamic
completion budgeting reduces routine provider usage.

The dominant remaining context signal is Task Designer input. Each of the six
calls sent 3,992–3,993 provider input tokens, while its original candidate set
was about 8.6k tokens and assembly filled the 3,968-token prompt budget. Task
Designer alone accounted for 70.9% of enhancement input across both arms
(23,955 of 33,791 input tokens). Its optional diagnosis
artifact was repeatedly partially kept to fill the remaining prompt budget.

The next optimization experiment should therefore hold the now-stable
completion policy constant and compare the current Task Designer context with a
compact diagnosis/task-result projection. It must retain the typed goal,
authorization targets, acceptance criteria, validation evidence, safety facts,
and selected evidence IDs. The success condition is lower Task Designer input
with equal task metadata, diff, and validation—not merely a lower reservation.

## Claim boundary

This is a three-pair result for one fixed task and provider profile. It is not a
distribution-wide causal estimate. Earlier V1–V4 stopped runs remain diagnostic
evidence and are not pooled into this result.
