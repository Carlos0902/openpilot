# Experiment 1 findings

## Outcome

The full-architecture observation pilot found a context-inflation signal. It is
not the mini-SWE cumulative-message pattern. In this run, recovery/controller
requests repeatedly copied a growing `Previous Task Results` block.

The task itself failed: one of three subtasks completed, two failed, and the
disposable `calculator.py` remained unchanged. The failure therefore supplied a
useful recovery-pressure trajectory but is not evidence of task-quality gain.

## Token observations

Five provider requests were attempted; four returned usage and the fifth timed
out. The four completed calls used 7,617 input tokens and 8,006 output tokens,
15,623 total.

| Request | Purpose | Assembled prompt | Provider input | Output | Reasoning | Result |
|---:|---|---:|---:|---:|---:|---|
| 1 | semantic_goal | 342 | 445 | 256 | 109 | responded |
| 2 | task_decomposition | 3,968 | 4,071 | 1,602 | 1,287 | responded |
| 3 | tool_event_decision | 1,284 | 1,387 | 4,076 | 3,595 | responded |
| 4 | tool_event_decision | 1,611 | 1,714 | 2,072 | 1,681 | responded |
| 5 | tool_event_decision | 1,951 | unavailable | unavailable | unavailable | timed out at 45 seconds |

Provider input is consistently 103 tokens above assembled prompt tokens in the
four completed calls. The context metadata therefore measures the selected
payload consistently, while provider usage includes request-level overhead.

## Inflation mechanism

The repeated `tool_event_decision` prompts grew 1,284 → 1,611 → 1,951 assembled
tokens, a 52% increase from the first to the third request. Their character
counts grew 5,689 → 6,608 → 7,519.

Section-level comparison localizes nearly all character growth to one dynamic
section:

| Section | Request 3 | Request 4 | Request 5 |
|---|---:|---:|---:|
| Previous Task Results | 51 chars | 946 chars | 1,888 chars |
| Need Catalog | 1,081 | 1,081 | 1,081 |
| Core Capability Cards | 693 | 693 | 693 |
| Deferred Capability Cards | 615 | 615 | 615 |
| Important / fixed instructions | 1,722 | 1,722 | 1,722 |

The dynamic section added roughly 900 characters on each recovery decision.
The rest of the inspected controller prompt stayed fixed. This is linear
accumulation of prior result/error envelopes, not uncontrolled full dialog
history. It is below the 3,968-token selected-prompt ceiling in this short run,
but repeated failures would continue consuming the remaining headroom.

Task decomposition showed a separate pressure signal: its original prompt was
5,369 tokens and was truncated to the full 3,968-token selection ceiling before
the provider call. This happened once, so it does not meet the frozen
three-occurrence budget-pressure criterion, but it shows that a single static
assembly can already saturate the budget.

## Output anomaly

The four completed calls emitted 8,006 output tokens, more than their 7,617
input tokens. Of those output tokens, 6,672 (83.3%) were provider-reported
reasoning tokens. The two `tool_event_decision` requests did not set
`max_tokens`; together they consumed 6,148 output tokens for two controller
decisions.

This supports treating output budget as part of context management. A smaller
incremental response schema can reduce visible/state payload, but the provider
reasoning component also needs an explicit per-purpose completion limit or
equivalent model control; schema narrowing alone does not guarantee that.

## Frozen-criterion interpretation

The preregistered late-run amplification signal is positive: the final-third
completed-call input median was 1,714 tokens versus 445 in the first third.
This comparison is confounded by different request purposes and is weak on its
own.

The stronger mechanism evidence is secondary because request 5 timed out and
has no provider usage: exact tokenizer-backed assembled tokens for the same
purpose increased by 52% across three requests, and the prompt diff identifies
the growing section. This is sufficient to justify a targeted next experiment,
but not a causal claim about a budget module's benefit.

## Decision

Do not redesign the whole context architecture from this one task. The current
assembler is enforcing a hard per-request ceiling and exposing good evidence;
the gap is lifecycle control over dynamic recovery content and completion
budgets.

The next stage should first implement and test the smallest two controls behind
an experiment flag:

1. Compact `Previous Task Results` into typed deltas: changed status, latest
   failure code, action effect, next decision, and evidence IDs. Do not resend
   unchanged result envelopes.
2. Add purpose-specific completion budgets for `tool_event_decision`, including
   an explicit reserve/limit and telemetry for limit hits.

Then rerun the same frozen task plus at least two additional strata (a clean
success and a longer recovery case) as an A/B comparison. The current run is a
diagnostic baseline, not the benefit estimate.

## Limitations

- One task, one provider run, no counterfactual, and no replication.
- The task failed for a tool/symbol-selection problem, so quality and token
  efficiency cannot be separated here.
- Request 5 timed out; only context-selection tokens, not provider usage, are
  available for it.
- The fixture copy contained harmless Python cache files created by the
  preflight test. They affected traceback source paths but not the assertion or
  source contents. The runner now excludes caches from future disposable copies.
