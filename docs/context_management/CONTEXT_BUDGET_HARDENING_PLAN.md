# Context budget hardening plan

## Evidence

Full-architecture observation run `20260803T145617Z` showed two independent
pressure points:

- `tool_event_decision` assembled prompts grew 1,284 → 1,611 → 1,951 tokens
  because `Previous Task Results` copied another result/error envelope for each
  completed subtask;
- four completed calls emitted 8,006 completion tokens, including 6,672
  provider-reported reasoning tokens. The two tool-event decisions had no
  explicit `max_tokens`.

The existing context assembler enforced its 3,968-token selection ceiling and
recorded exact selection evidence. This change therefore extends the current
contracts and request path instead of replacing the assembler.

## Stage 1 — bounded execution-history projection

### Metadata impact note

Fact: A prompt-only, bounded projection of prior task results containing status
counts, a recent status ledger, the latest result delta, and observed evidence
paths.

Authoritative producer: Existing `TaskExecutionResult` and nested
`TaskResultMetadata` remain authoritative. `IntelligentAutopilot` derives the
existing history payload; `ToolPlanningTaskExecutor` derives the prompt view.

Consumers: `ToolPlanningTaskExecutor` prompt construction and its capability
planning surface.

Lifecycle: runtime-only derived view.

Control impact: indirect prompt context selection; it does not independently
control routing, permission, retry, recovery, or completion.

Existing contracts reviewed: `TaskExecutionResult`, `TaskResultMetadata`,
`FailureMetadata`, `ContextSelectionMetadata`, `RuntimeBudgetMetadata`, and
`TaskExecutionContext`.

Decision: derived view. No new public metadata contract or kind.

Why no duplicate source of truth is created: the view is rebuilt from current
task results for each subtask, is not persisted, and cannot update task state.

Serialization and migration: no persisted shape changes. The prompt JSON shape
changes from a list of result envelopes to a bounded object.

Tests: deterministic size ceiling, latest-change retention, status counts,
evidence-path retention/deduplication, oversized-value handling, and prompt
integration.

Documentation updates: this plan, API context behavior, context-management
notes, and task-trajectory implementation log after validation.

### Implementation boundary

- Maximum serialized prompt view: 900 characters. The initial 1,200-character
  proposal was tightened after offline replay showed that one-history evidence
  paths could otherwise make the first dynamic prompt larger than baseline.
- Keep status counts for the complete provided history.
- Keep a small recent status ledger without result bodies.
- Keep details only for the latest change: typed failure name when available,
  bounded error text, and bounded result summary.
- Keep a bounded, deduplicated set of observed paths across recent results.
- If optional values exceed the ceiling, drop or shorten optional details in a
  deterministic order; always return valid JSON.

## Stage 2 — purpose-specific completion budget

### Metadata impact note

Fact: The runtime-wide remaining completion-token allowance for
`tool_event_decision`, its per-call static ceiling/floor, recovery-round decay,
and actual provider completion tokens consumed.

Authoritative producer: `RuntimeBudgetMetadata`; `ToolEventLoopRunner` derives
one call limit and consumes provider-reported completion usage.

Consumers: `ToolEventLoopRunner`, runtime checkpoints/reports, and trajectory
analysis. Lifecycle: mutable runtime state, checkpointed with existing state.
Control impact: budget and recovery.

Existing contracts reviewed: `RuntimeBudgetMetadata`, `RuntimeStateMetadata`,
`LLMRequest.max_tokens`, `LLMRequestMetadata`, `LLMResponseMetadata`, and
`ContextRequestPurpose`.

Decision: extend `RuntimeBudgetMetadata`. The provider limit remains the
existing `LLMRequest.max_tokens`; request trace data is evidence only.

Why no duplicate source of truth is created: allowance and usage live only in
runtime budget state. The request receives one derived limit and provider usage
is consumed once after the response.

Serialization and migration: new fields have validated defaults, so historical
checkpoints load without migration.

Tests: invalid floor/ceiling/total combinations, remaining arithmetic,
round-based decay, fair-share behavior, request propagation, usage consumption,
and historical default loading.

### Frozen rule

- Runtime total: 12,000 completion tokens for tool-event decisions.
- Static per-call ceiling: 2,000; floor: 800.
- Recovery decay: subtract 400 per in-loop recovery round, down to the floor.
- Dynamic fair share: remaining allowance divided by calls remaining in the
  current tool loop.
- Effective limit: minimum of remaining allowance, decayed ceiling, and fair
  share; the floor applies only when the remaining allowance permits.
- JSON repair is limited to one provider attempt for this purpose. The dynamic
  limit is reserved before transport and reconciled to actual provider usage on
  success; failed calls retain the reservation. An initial 800-token ceiling
  was rejected by the same-task risk gate after three empty/invalid JSON calls.

## Stage 3 — validation

1. Run targeted and full offline tests.
2. Reuse the frozen failing repair task as the baseline comparison.
3. Add a clean-success task and a longer recovery task.
4. Compare task outcome, call count, input/output/reasoning tokens, timeout
   count, prompt-selection pressure, and repeated-purpose growth.
5. Reject the change if token reduction comes with worse required verification
   or loss of path/failure evidence.
