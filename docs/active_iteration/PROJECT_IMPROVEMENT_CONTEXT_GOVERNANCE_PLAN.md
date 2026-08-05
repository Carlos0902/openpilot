# Project Improvement Context Governance Plan

## Objective

Reduce post-core `project_improvement` context and completion growth without
weakening task authority, exact validation, rollback, or core-task success. The
work proceeds in independently testable stages. Each implementation stage starts
with its own red tests and a plan update before production behavior changes.

## Observed failure

The final full-architecture pilot showed that reasoning control fixed the
Controller but not the enhancement tail:

| Purpose | Input | Output | Reasoning |
| --- | ---: | ---: | ---: |
| `project_improvement` | 979 | 2,736 | 2,482 |
| `iteration_task_design` | 4,071 | 5,211 | 4,882 |
| `code_generation` | 2,237 | 4,484 | 4,169 |

The visible JSON bodies were only about 1,071 and 1,452 characters for the first
two calls. Most output cost was provider reasoning, while all three requests had
`max_tokens=None` and `provider_default` reasoning.

The same immutable evidence revealed quality and projection failures:

- project-improvement analysis received only `written_files=[calculator.py]`,
  treated that mutation scope as a project inventory, and incorrectly claimed
  that `test_calculator.py` did not exist;
- Task Designer loaded granular file and memory candidates and then reloaded the
  already assembled `memory_context.prompt_text`; that aggregate alone was 7,766
  characters and repeated files, tests, README, memories, and environment;
- project memory was not consistently bound to canonical project identity and
  included unrelated/duplicated historical improvement evidence;
- current code was written to `project_context.current_code_context`, while the
  execution router read a top-level `current_code`, so a possible symbol edit
  degraded into full-file generation;
- fast-tool calls build typed in-memory lifecycle events but do not bridge them
  to durable diagnostics; more seriously, fast mutations bypass the standard
  edit guard and checkpoint prepare/observe/replay lifecycle.

## Design constraints

- Preserve one authoritative current project state and one authority for each
  diagnosis, candidate, policy, budget, tool call, and provider usage fact.
- Never use `written_files` as a complete project inventory.
- Preserve the original goal, explicit permission/write boundary, protected
  files, current selected goal/candidate, acceptance criteria, exact validation
  command/evidence, and environment binding before optional history.
- Provider usage remains owned by `LLMResponseMetadata`; tool envelopes and
  budgets may reference or reconcile it but must not copy it as a second fact.
- A shorter prompt or event stream cannot be used to claim that a mutation is
  safely controlled. Durable evidence parity and execution semantics are
  separate acceptance gates.
- Code/text generation has different output semantics from narrow JSON
  decisions and receives a separate budget policy.

## Stage 1 — Root-cause and contract audit

Status: complete.

Reviewed the project-improvement pipeline, purpose-specific candidate adapters,
memory selection, code-generation routing, tool lifecycle, diagnostics hooks,
completion budgets, metadata inventory, and immutable provider artifacts. No
production behavior changed in this stage.

## Stage 2 — Durable fast-tool evidence parity

Status: complete.

### Plan

1. Add red tests using a real `RuntimeDiagnosticsHooks` and
   `DiagnosticRecorder` for one fast success, one fast failure, and one
   retry-then-success.
2. Reuse `ToolCallMetadata`, `ToolContextMetadata`, `ToolEventMetadata`,
   `ToolErrorMetadata`, and `ToolExecutionEnvelopeMetadata`; do not create a
   fast-tool event schema.
3. Bridge each logical fast call to exactly one durable `tool_called` and one
   terminal `tool_succeeded` or `tool_failed` event. Retain attempt detail in
   `retry_history` rather than inflating logical-call counts.
4. Apply the same correlation contract to module-owned environment, project
   state, and improvement tools, or explicitly leave a tested gap if a path
   cannot yet share the bridge.
5. Verify root/subtask correlation, call ID, purpose attribution, failure detail,
   hooks-disabled compatibility, and unchanged execution results.

This stage adds observation only. Its documentation explicitly states that
guard/checkpoint parity remains unresolved until Stage 3.

## Stage 3 — Fast mutation guard and checkpoint parity

**Status: completed.** Fast file/README/bug-fix mutations now share target
classification across task scope, EditGuard, checkpoint reconciliation, diff
evidence, and budget accounting. Real call sites provide explicit authority and
exact validation identity; no-diff successes become observed failures.

### Plan

1. Add red tests proving read-only fast writes are rejected, a failed prepared
   checkpoint prevents execution, an observation failure cannot report success,
   and recovery does not repeat an observed mutation.
2. Reuse the existing controller `prepare -> observe -> apply -> verify`
   protocol and edit guard. Prefer extracting the smallest preselected-call
   execution primitive over duplicating ToolEventLoop branches.
3. Preserve the original `Task`, root execution mode, requested action, target
   files, and exact pending validation command through fallback/retry.
4. Keep the existing iteration Git snapshot/rollback as transaction recovery;
   do not present it as a substitute for write-before authorization or durable
   call reconciliation.
5. Verify planned write versus observed diff and exact validation evidence.

Validation: targeted runtime/mutation suites passed (`228 passed`), followed by
the prior full offline suite (`788 passed`) before the final shared-descriptor
review fix. The descriptor change is covered by the targeted suite and will be
rechecked by the final Stage 7 full run.

## Stage 4 — Project identity, input deduplication, and edit routing

**Status: completed.** Project memory is canonical-path scoped, downstream
improvement purposes consume granular records instead of the assembled memory
prompt, the required input carries a bounded name-only manifest without write
authority, and symbol edits use authoritative full-file source.

### Plan

1. Add red fixtures that reproduce unrelated memory, `/var` versus
   `/private/var` aliasing, duplicate environment records, missing test inventory,
   whole-prompt reloading, and the nested-current-code routing bug.
2. Bind project-scoped memory to canonical project identity. Basename tags may
   support retrieval but cannot establish identity.
3. Remove `memory_context.prompt_text` from downstream model-facing candidates.
   Persist the aggregate only as evidence/artifact; use governed granular
   candidates for model input.
4. Limit and compact historical candidates consistently for all three
   improvement JSON purposes. Represent each source fact once.
5. Add a bounded current project-file manifest and protected-file facts to the
   required projection so analysis can distinguish project inventory from write
   scope without loading every source file.
6. Read current code from its authoritative nested location and route a bounded
   symbol task to `code_editor`; use full-file generation only when the task
   contract actually requires replacement.
7. Assert required safety/goal/validation coverage and a non-growing second-round
   prompt while retaining source IDs.

Validation: focused context, memory, routing, improvement, and runtime suites
passed (`123 passed`; post-review focused set `78 passed`). A full run before
the two review fixes found only two obsolete identity/aggregate fixtures
(`793 passed, 2 failed`); those fixtures and both P1 review findings were then
corrected. Stage 7 will run the final full suite.

## Stage 5 — Incremental output contracts

**Status: completed.** Analysis and task-design provider outputs are strict,
bounded deltas; runtime owns identity, evidence filtering, compatibility mapping,
safe targets, safety merging, and deterministic fallback.

### Plan

1. Freeze representative analysis and task-design fixtures and define semantic
   quality expectations before changing schemas.
2. Project-improvement analysis returns only changed signals, proposed actions,
   next goal, must-satisfy constraints, blocking risks, evidence IDs, and a stack
   preset patch. Existing project/diagnosis facts remain authoritative.
3. Task design returns one bounded task delta: description, target files,
   acceptance criteria, risks, and evidence IDs. Runtime assigns stable task and
   goal identity.
4. Enforce item counts and field lengths in code, not only in prompt prose.
5. Store full diagnosis, reports, old evaluations, diffs, and logs as artifacts;
   downstream requests receive a bounded projection or evidence reference.
6. Use economical reasoning only when typed facts prove a unique candidate and
   bounded single-target task. Ambiguous candidate selection remains
   `provider_default` until experiments justify another policy.

Validation: new delta red tests first failed `5 + 4` cases. After implementation,
the delta/metadata/context/execution suite passed `101 passed`, and the broader
Stage 5 related suite passed `226 passed`. Independent review found no P0 and
six P1 boundary gaps; all were corrected before progressing.

## Stage 6 — Stage-aware completion budget and value stop

### Plan

1. Complete the metadata impact note below and add contract tests first.
2. Give JSON purposes explicit ceilings/floors and one shared enhancement-stage
   total. Code/text generation receives a separate allowance under the same
   stage total.
3. Derive each reservation from purpose, selected prompt size, typed task
   complexity, remaining attempts, remaining total, optional/required policy,
   prior finish class, and one bounded length-recovery opportunity.
4. Reconcile actual completion usage; unknown usage conservatively retains its
   reservation. Empty/malformed output does not mechanically shrink later calls
   or authorize a broader fallback.
5. Stop an optional enhancement before another provider call when remaining
   value or budget cannot justify it. Required enhancement returns a typed
   failure instead of silently lowering acceptance criteria.
6. Reject repeated candidate/goal selection and no-gain cycles using typed facts,
   not explanation-text matching.

### Stage 6 outcome (2026-08-04)

Completed. `RuntimeBudgetMetadata` now owns an independent 12,000-token
post-core enhancement pool for the four planned purposes. Stable semantic
logical keys, checkpointed reservation/reconciliation ledgers, structural and
aggregate validators, provider-payload replay hashes, failed-attempt usage,
one bounded JSON length recovery, required/optional failure boundaries, and
truncated-code rejection are implemented. Goal/task allocation receives typed
complexity, remaining-call, and decision-value signals; code generation derives
complexity from operation/target scope. Core code generation is isolated from
the enhancement pool, and completed goal titles are filtered before another
task-design call. Stage 7 still owns full regression and provider mechanism
measurement; Stage 6 does not itself claim a real-provider Token reduction.

## Stage 7 — Acceptance and experiments

1. Run metadata, diagnostics, mutation safety, context quality, iteration,
   recovery, and full offline regression suites.
2. Reanalyze the existing immutable pair to check prompt projections and
   simulation ceilings without rewriting source evidence.
3. Run a fixed-trajectory provider mechanism check only after all safety and
   semantic quality gates pass.
4. Defer repeated paired full-architecture experiments until the architecture is
   stable. The later experiment should use at least three pairs with alternating
   arm order and separate core/enhancement windows.
5. Update API, metadata catalog, trajectory alignment, testing guide, experiment
   protocol/results, and implementation log.

### Stage 7 outcome (2026-08-04)

Completed. Production regression is `861 passed`; compileall and diff checks
pass, and independent review found no remaining P0/P1. The deterministic
experiment harness is `29 passed`. Frozen replay keeps all hard quality gates
while reducing Task Designer original candidate tokens from 29,342 to 6,785
(-76.88%) and Code Generator from 13,324 to 2,076 (-84.42%). The existing
one-pair full-architecture reasoning pilot still shows tool-event total -40.94%
and full observed lifecycle -27.88%, with equal task-quality gates. The
collector now covers all four enhancement purposes, failed attempts, usage
coverage, and request-time reservation/recovery facts. No immutable run contains
the new Stage 6 reservations, so provider-level benefit for that budget remains
explicitly deferred to the later multi-pair experiment.

## Metadata impact note

### Stages 2–5

Fact: fast-tool lifecycle evidence and bounded model-facing projections.

Authoritative producer: existing tool executor/emitter and existing project,
diagnosis, candidate, memory, and context owners.

Consumers: runtime diagnostics, project-improvement agents, tests, and experiment
analyzer.

Lifecycle: event evidence and runtime-only derived views.

Control impact: none for Stage 2; permission/recovery/completion for Stage 3;
input selection/routing for Stages 4–5.

Existing contracts reviewed: `ToolCallMetadata`, `ToolContextMetadata`,
`ToolEventMetadata`, `ToolErrorMetadata`, `ToolExecutionEnvelopeMetadata`,
`ContextCandidate`, `ContextSelectionMetadata`, `ProjectStateSnapshot`,
`ProjectDiagnosisMetadata`, `ImprovementCandidateMetadata`, and
`ImprovementAnalysisMetadata`.

Decision: reuse existing event contracts and derived purpose-specific views.

Why no duplicate source of truth is created: durable events serialize the
existing typed call/envelope; projections preserve source IDs and do not become
project or diagnosis authority.

Serialization and migration: no new public kind; old runs remain readable.

Tests: lifecycle parity, guard/checkpoint boundaries, projection quality,
identity isolation, deduplication, and routing.

Documentation updates: API, trajectory alignment, testing guide, and
implementation log as each behavior stage lands.

### Stage 6 provisional decision

Fact: static enhancement completion policy and cumulative runtime consumption.

Authoritative producer: runtime configuration for allowed policy; enhancement
request coordinator for reservations and reconciliation.

Consumers: project-improvement request routing, stop logic, checkpoint/report,
and experiment analysis.

Lifecycle: policy configuration plus checkpointed runtime budget state.

Control impact: budget, retry, completion, and optional/required stopping.

Existing contracts reviewed: `ProjectImprovementPolicy`,
`RuntimeBudgetMetadata`, `ContextRequestPurpose`, `ContextSelectionMetadata`,
`LLMRequestMetadata`, and `LLMResponseMetadata`.

Decision: extend existing owners with strict nested values; no new
`MetadataKind`.

Why no duplicate source of truth is created: policy owns allowed limits,
runtime budget owns consumption, selection owns prompt usage, and response owns
provider usage/finish reason.

Serialization and migration: new nested values require safe defaults for
historical state/checkpoints and contradiction validation.

Tests: model round-trip, invalid combinations, historical reads, reservation,
reconciliation, recovery bonus, and optional/required stop matrices.

Documentation updates: metadata catalog, API, recovery protocol if checkpoint
shape changes, trajectory alignment, testing guide, and implementation log.

## Stop conditions

- Any change that loses an explicit safety constraint, protected file, exact
  validation command, or environment identity fails the stage.
- Any fast mutation that is merely logged but still bypasses permission or
  checkpoint gates blocks progression beyond Stage 3.
- Token reduction is ineligible when task quality, write scope, validation, or
  rollback evidence is incomplete.
- Provider experiments do not start while deterministic quality gates fail.
