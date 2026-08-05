# Phase 8 Plan: Production Ingress and Real-Benefit Canary

## Status

Stages 0–4, the Stage 5A admission contract, Stage 5B-1/5B-2 zero-provider
projection sentinels, Stage 5B-3a/3b full-entry admission and campaign ledger,
and one bounded Stage 5B-3c Task Designer Provider pair are complete. Stage 6
analysis is also complete: the pair shows a useful compact mechanism signal,
but it is not full-session evidence. Stage 7's offline ingress and telemetry
gates are complete. The Stage 6A/6B post-core full-session runner is now
implemented and its zero-Provider dry-run gate is the next validation step. No
broad real-task rollout is admitted.

## Objective

Move the Phase 7 context foundation into the real execution boundary, measure
actual input/output/call-count effects, and admit only a bounded, reversible
Provider canary. Conversation identity and run identity remain distinct; raw
turns remain the source of truth; `RuntimeStateMetadata.session_constraints`
remains the only execution ledger.

## Stages and gates

1. **Stage 0 — production audit (complete).** Confirm the actual CLI/REPL,
   task-pool and resume owners; identify the absence of a multi-turn owner,
   missing stable message IDs, missing reducer callers, and the fact that the
   main planner/decomposer does not consume `MemoryContextBuilder`. Reuse the
   existing runtime diagnostics and experiment guards.
2. **Stage 1 — session/turn ingress contract (complete offline).** Define typed conversation ID,
   run ID, turn cursor, stable raw-message identity, pending proposal ownership,
   explicit confirmation/rejection/revocation, and checkpoint/resume identity.
   Decide whether pending proposals are same-turn only or bounded checkpointed
   state; never put them in long-term memory. Gate: cross-session/project
   mismatch, ambiguous checkpoint identity, assistant-origin proposals, and
   unconfirmed proposals fail closed.
3. **Stage 2 — production prompt projection (complete offline).** Feed the confirmed state into
   the actual standard/enhanced planner/decomposer prompt as a narrow typed
   required section, while keeping runtime enforcement authoritative. Gate:
   state is visible before planning, appears once, does not expand permissions,
   and request/replay hashes include the state revision.
4. **Stage 3 — typed telemetry and canary gates (complete offline).** Reuse existing
   `RuntimeDiagnosticsHooks`, `TrajectoryLLMClientProxy`, `ContextSelectionMetadata`,
   Provider usage, and `run_observation.py`. Add only a typed experiment
   descriptor/audit mirror: arm, variant, projection policy, state hash,
   context request hash, fallback reason, stop reason. Unknown usage, quality
   failure, mutation drift, provenance regression, or budget/call/wall limits
   stop the arm; fallback is a separately accounted current request.
5. **Stage 4 — zero-provider sentinel and flag-off/flag-on shadow (projection
   gate passed; broader gate stopped).** Validate manifest/protocol hashes,
   deterministic source snapshots, no downstream shadow consumption, complete
   usage accounting, no mutation, and exact verification evidence before any
   Provider call. Stage 10/11 segmented-compact and session-constraint
   projection checks pass offline. The existing Stage 9 Task Designer gate is
   not admitted to Provider because its committed offline report does not match
   the current deterministic runtime snapshot; no artifact was silently
   re-frozen.
6. **Stage 5B-3c — low-risk Task Designer Provider pair (bounded complete).** Use one approved
   provider/task family, fixed source snapshot, transport retries disabled,
   hard per-arm and campaign Token/call/wall caps, current fallback and kill
   switch. Compare input/output/reasoning/total tokens, calls, latency,
   constraint recall, verification success, fallback and stop reasons. Do not
   change reasoning/completion policy in this canary.
7. **Stage 6 — paired-result analysis and production-gap audit (complete).** Keep
   the bounded pair as a mechanism sample only. Audit raw SessionTurn hydration,
   actual candidate consumption, route bypasses, fail-closed behavior, replay
   hashes, and provider-neutral reasoning semantics before expanding traffic.
8. **Stage 7 — typed raw-dialog adapter and telemetry closure (offline complete).**
   The memory-boundary adapter, end-to-end candidate consumption, read-only full
   ContextLoader gate, and provider-attempt telemetry projection now pass offline;
   no real Provider traffic was added by this stage.
9. **Stage 6A/6B — post-core full-session canary runner (implemented, dry-run
   gate pending).** The new `stage6_full_session_canary.py` reuses one immutable
   ingress/project source bundle through RuntimeController checkpointing,
   read-only ContextLoader, project-improvement analyzer, shared Goal Maker and
   compact/current Task Designer requests. It records typed Stage 7C attempt
   receipts, source/turn hashes, identity mapping and mutation manifests. The
   claim boundary is `full_session_post_core_context_canary`: core semantic
   analysis/decomposition and downstream task execution are not yet in scope.
   The default remains a zero-Provider dry run; real transport requires an
   explicit campaign state path.
10. **Stage 8 — bounded real canary decision and next experiment.** Only after the
   offline gates pass, run
   an independently controlled reasoning comparison and expand across task
   families/providers. Promote only if paired gains are repeatable and all
   safety gates pass; otherwise revert to current and diagnose.

## Stage 0 evidence

- `ui/enhanced_cli.py` and `runtime_diagnostics/runner.py` create a new
  `IntelligentAutopilot` per execution; `IntelligentAutopilot.execute()` creates
  a new run/session ID.
- REPL `InMemoryHistory` is input history, not a domain conversation ledger.
- `ShortMemory.Message` lacks stable message/turn/session identity, production
  execution does not append raw turns, and the four reducer functions have no
  production caller.
- `RuntimeStateMetadata.session_constraints` is the current single-run
  authority. `SessionIngressState` now binds conversation/run/project/turn
  identity, and `RuntimeCheckpointMetadata.session_ingress_state` preserves the
  bounded ingress snapshot and pending proposals for resume.
- Runtime diagnostics already capture request context selection, Provider
  usage/finish reason, failed-attempt usage, reasoning tokens, call counts and
  stop/limit signals. Existing Stage 9 paired-shadow infrastructure is reusable
  only within its documented `iteration_task_design` claim boundary.

## Metadata impact note

```text
Reuse: RuntimeStateMetadata.session_constraints, RuntimeCheckpointMetadata,
       ContextSelectionMetadata, LLMRequestMetadata/LLMResponseMetadata,
       RuntimePromptContextSnapshot, existing diagnostics and experiment guards.
Implemented ingress values: ConversationIdentity, SessionTurn, and
       SessionIngressState are nested values; checkpoint ingress must match the
       runtime constraint snapshot on serialization and resume.
New typed contract only if required: conversation/run/turn identity or a
       bounded pending-proposal owner; no MetadataKind, MemoryRecord, Task copy,
       or free-form trace field may become authoritative.
Authority: raw turn ledger and explicit user confirmation are ingress facts;
       RuntimeStateMetadata is the execution projection; TaskGraphNode and
       runtime verification contracts remain execution authorities.
Control: canary policy may select prompt projection only; it may not expand
       write scope, change validation commands, or consume shadow output.
```

## Non-goals

- no broad real-task rollout;
- no simultaneous reasoning, completion-budget, or output-schema change;
- no claim that Stage 9 Task Designer savings equal full-conversation savings;
- no unknown Provider usage treated as zero;
- no long-term memory promotion of session constraints.

## Offline evidence for completed stages 1–3

- `Code/tests/test_session_ingress_contract.py`: ingress identity, monotonic
  turns, proposal confirmation/rejection/revocation, and run/conversation
  separation.
- `Code/tests/test_session_constraint_prompt_projection.py`: standard
  decomposer and tool planner projection, retry projection, malformed/conflicting
  state rejection, and preservation of required constraints when unrelated
  context is truncated.
- `Code/tests/test_runtime_checkpoint.py`: ingress round-trip and divergence
  rejection. Full `Code` suite: **947 passed**.
- The tool-event loop fails closed before Provider transport if the complete
  active constraint projection is absent from the assembled request. This is a
  safety gate, not a quality claim or a canary result.

## Stage 4 evidence and stop reason

- `test_stage10_segmented_compaction_offline.py` and
  `test_stage11_session_constraint_offline.py`: **10 passed**. Stage 11 now
  obtains its state through the production ingress lifecycle
  (`open_turn → reject/confirm → revoke`) and checks cross-conversation,
  cross-project, and non-monotonic-turn rejection. The offline campaign
  recorded zero Provider calls, zero network calls, and zero project mutations;
  active-constraint recall was **1.0**, assistant authority was **0.0**, and
  assistant noise did not change the session-state hash.
- The Stage 9 scenario gate's live deterministic sentinel reports `passed=True`
  and `provider_calls=0`, but its committed
  `STAGE9_TASK_DESIGNER_SCENARIO_CANARY_V2_OFFLINE_RESULT.json` does not match
  that runtime snapshot. The provider sentinel therefore stops with
  `offline Stage 9 gate is not frozen and passing` before transport. This is a
  baseline-integrity stop, not evidence of a compact-quality regression.
- No `--execute` path was run. The artifact must be refreshed only as a
  separately reviewed experiment-baseline change; until then Stage 5 remains
  pending.

## Stage 5A admission contract evidence

- `stage12_session_compact_canary_admission.py` and its offline tests define a
  versioned, full-session scope contract without adding production authority.
  It locks the compact/current projection pair and the Stage 9 prerequisite by
  hash, requires a read-only ready-environment receipt, defaults Provider,
  network, and project mutation to false, and models an armed feature flag plus
  an independently switchable kill switch.
- Primary compact usage and current fallback usage have separate account IDs and
  receipts. The reservation ledger rejects duplicate IDs, per-arm/campaign
  Token reservations, per-arm call/wall limits, route/account mismatches, usage
  overrun, and unknown usage before a later transport can be admitted.
- The admission CLI completed **24 focused tests** across Stage 10, Stage 11,
  and Stage 12; the result was `provider_execution_admitted=false`, with zero
  Provider/network/project calls or mutations. This is an admission-safety
  result, not a Provider quality or Token-savings result.
- The remaining Stage 5B work is a separately reviewed runner that wires this
  contract to the real full-session execution path and performs one bounded
  paired canary. The stale Stage 9 V1 artifact must not be overwritten to do so.

## Stage 5B-1/5B-2 evidence

- Production propagation now carries the typed `SessionConstraintState` from
  `AutonomousIterationAgent` into both Goal Maker and Task Designer candidate
  builders. The active state is one required, non-truncatable,
  source-hash-linked candidate; no raw turn ledger or long-term memory record is
  promoted into the prompt.
- `stage13_session_compact_pipeline_offline.py` exercises both production
  candidate/request boundaries from one immutable typed source snapshot and a
  real `SessionIngress.open_turn → confirm_proposal` lifecycle. It records exact
  locally available provider-tokenizer rendered input, while Provider usage
  fields remain `null` and `usage_observed=false`.
- Focused Stage 5B-2 tests: **5 passed**. Both arms recalled all active session
  constraints (`1.0`); Provider/network/project calls were `0`; compact Task
  Designer input was **1,346 tokens** versus **1,716** current tokens (about
  **21.6%** lower for this projection fixture). This is a projection sentinel,
  not a full-session quality or Provider-cost result.
- The remaining gap is intentional: the probe bypasses
  `IntelligentAutopilot.execute()`/`RuntimeController` and does not yet carry raw
  dialog into the production ContextLoader path. Stage 5B-3 must wire the
  Stage 12 admission contract, feature flag, kill switch, fallback receipt,
  campaign ledger, and hard caps to that full execution boundary before one
  real Provider pair is allowed.

## Stage 5B-3a/3b evidence

- The full-entry admission probe now invokes
  `IntelligentAutopilot.execute()` → `AgentRuntimeController.run()` with a real
  ingress lifecycle and a temporary checkpoint store. The checkpoint's raw turn
  and active constraint state match the ingress canonical hash; the offline
  session executor prevents Provider transport and project writes.
- The campaign ledger closes the Stage 5A gap: shared campaign Token/call/wall
  limits are enforced before transport across compact primary and current
  fallback arms, with typed arm assignment and independent route/account
  receipts. Unknown usage remains a stop condition, never a zero total.
- The composed pre-transport gate reserves four Goal/Task requests from the two
  arms, records zero observations, and reports
  `provider_execution_admitted=false`, `transport_attempted=false`.
- Stage 5B-3a/3b focused evidence is recorded in
  `STAGE5B_3_PRETRANSPORT_GATE.md`. The next gate is one explicit real Provider
  pair; it must preserve the current reasoning/completion policy and stop on
  unknown usage, finish-reason ambiguity, verification failure, mutation drift,
  or any hard-cap crossing.

## Stage 5B-3c evidence

- One persistent-state Task Designer Provider pair completed after the full
  execute/runtime/checkpoint admission. Compact primary and current
  control/fallback both returned valid Task JSON, recalled all active session
  constraints, and stayed within the authorized `calculator.py` write scope.
- Compact vs current rendered input was **1,258 vs 1,719** tokens (-26.8%);
  Provider input was **1,361 vs 1,822** (-25.3%); total usage was **3,227 vs
  3,891** (-17.1%); reasoning tokens were **1,674 vs 1,856** (-9.8%). Calls
  were one per arm, observed usage and `finish_reason=stop` were present, and
  project mutations were zero.
- The campaign state is persisted and the ledger contains two reservations and
  two observations under independent primary/fallback accounts. Earlier
  diagnostic attempts exposed and fixed runner semantics (`max_retries=0`, too
  small completion reserve, overrun telemetry, and fallback routing); they are
  excluded from the valid paired metrics.
- This remains a mechanism sample at the Task Designer request boundary. It
  does not establish full raw-dialog ContextLoader coverage, multi-task/provider
  generalization, or top-level project quality. Reasoning policy was held at
  Provider-default; reasoning routing is a separate experiment.

## Stage 6 evidence: paired analysis and raw-dialog/reasoning audits

- The valid paired sample reduced rendered input by **26.8%**, Provider input by
  **25.3%**, total usage by **17.1%**, and reasoning tokens by **9.8%**, with one
  call per arm, valid Task JSON, full constraint recall, `finish_reason=stop`, and
  zero project mutation. This is one Task Designer/source-snapshot mechanism
  sample, not a full-session quality claim. See
  `experiments/full_architecture_context_observation/STAGE6_CANARY_ANALYSIS.md`.
- Earlier diagnostic attempts are excluded from the paired metrics. They exposed
  zero-attempt `max_retries=0` semantics, an undersized completion reserve,
  known-usage truncation/JSON failures, and the need for persistent campaign state;
  unknown usage remains a hard stop and fallback never regenerates the whole plan.
- The raw-dialog audit found that ingress is checkpointed and constraints are
  projected, but `ShortMemory` is not hydrated from raw turns; `memory_context`
  is not consumed by the Goal/Task candidate builders; the pre-Goal/Task analyzer
  also lacks the typed session constraint; one CLI route can bypass ingress; context
  exceptions are swallowed into empty context; and future request hashes must include
  an ingress turn-ledger digest. These are Stage 7 gates.
- The same audit found that a real `MemoryContextBuilder` project update can write
  `sketch.json`/file-index artifacts. Therefore a future full-session canary must use
  a read-only index/snapshot mode or explicitly account for those side effects; the
  Stage 5B-3c zero-mutation result does not cover ContextLoader.
- An empty-constraint ingress probe also found that conflicting conversation/project
  identity can escape validation when checks are conditional on active entries. Stage
  7 must make ingress ownership checks unconditional whenever a raw ingress snapshot
  is present, including the empty-constraint case.
- The reasoning audit confirms the provider-neutral policy split. Only a capability
  profile that resolves `disabled` exactly may be a disabled-thinking treatment;
  generic/unknown providers are a separate omitted/provider-default stratum. A
  future reasoning experiment must freeze compact/prompt/completion variables and
  compare at least three interleaved pairs while retaining nullable reasoning usage
  and raw finish reasons.
- Stage 6 validation: experiment-layer **45 passed**; full `Code` suite **947
  passed**. No additional Provider traffic was admitted during the audit.

## Stage 7a evidence and Stage 7b plan

- Stage 7a hardens ingress ownership at both `IntelligentAutopilot.execute()` and
  direct `AgentRuntimeController.run()`: a raw ingress snapshot with empty active
  constraints still rejects conflicting conversation/session aliases or project
  roots before runtime/provider work. The `run_id` lifecycle remains intentionally
  separate and is a follow-up contract.
- ContextLoader now requests a typed read-only project-index mode and strict source
  handling. It no longer refreshes `sketch.json`/`.openpilot/file_indexes` as a hidden
  prompt side effect; memory/environment, snapshot, and compaction failures become
  `ContextSourceError` instead of an apparently successful empty context. Explicit
  ProjectManager update paths retain their prior write semantics.
- Stage 7a validation: full `Code` suite **954 passed**. No Provider traffic was
  added. The Stage 7b implementation plan is recorded in
  `docs/context_management/PHASE_8_STAGE7B_RAW_DIALOG_ADAPTER_PLAN.md`; its first
  gate is a zero-Provider typed raw-dialog adapter with stable source IDs, ingress
  digest in replay hashes, and proof that derived candidates are actually consumed.
- Stage 7b-1/2 validation now passes the full `Code` suite (**965 passed**). The same
  bounded derived DIALOG projection is consumed by the project-improvement analyzer,
  Goal Maker, and Task Designer candidate builders; the analyzer's typed tool input
  carries `session_turn_source_hash`, while the runtime handle remains the raw-state
  authority and recomputes the digest. This is still offline/zero Provider evidence;
  Stage 7b-3 must combine it with checkpoint resume, compact/current, feature flags,
  and no-mutation gates before any full-session canary.
- Stage 7b-3a now adds a zero-Provider composition gate: current/compact Goal/Task
  requests preserve identical raw user/assistant source IDs and a common
  `session_turn_source_hash`, while the real execute/runtime checkpoint preserves and
  re-hashes the same raw ledger. Stage 7b-3a focused regression is **12 passed** with
  dialog and constraint recall both `1.0`, zero Provider/network/project mutation, and
  `provider_execution_admitted=false`. This remains an admission/lineage result, not a
  full-session Provider quality or cost result. Stage 7b-3b still needs the combined
  feature-off, kill-switch, fallback-receipt, and ContextLoader→analyzer→Goal→Task
  no-mutation assertions.
- Stage 7b-3b now exercises the real read-only `ContextLoaderAgent` with a temporary
  `MemoryStore`/`ProjectManager`, then the production project-improvement analyzer and
  Goal/Task boundaries under one ingress snapshot. Before/after project and memory
  manifests prove zero mutation, and a typed `CompactFallbackReceipt` links compact
  failure to the current fallback without changing turn/constraint hashes. The locked
  Stage 12 protocol controls are checked before composition (`status=passed`,
  `controls_admitted=true`). Stage 7b-3b regression is **16 passed** (combined selected
  experiment regression **47 passed**), with Provider/network/project/memory mutation
  all zero. The local analyzer stub is not Provider evidence; real compactor fallback
  remains covered separately by Stage 10/Code atomic-compaction tests.

## Stage 7b-3c / Stage 7C telemetry contract

- Existing diagnostics now carry the credential-free provider-bound request hash and
  ordinal on `llm_requested`; response details and failed `provider_attempt` evidence
  carry attempt ID, normalized endpoint, provider/model identity, and whether transport
  was attempted. This closes the prior gap where failure cost could only be reconstructed
  by guessing from nested payloads.
- `stage7c_provider_attempt_telemetry.py` defines an experiment-only strict receipt
  projection and deterministic event adapter. It keeps partial/unknown usage as `null`
  with raw evidence, extracts optional reasoning usage, preserves finish reasons and
  repair/retry metadata, links replay to the source attempt, and rejects usage mismatch
  or reasoning-over-output. It does not add runtime authority or Provider traffic.
- Validation: Stage 7C offline receipt/event regression is **7 passed**; runtime
  diagnostics regression is **35 passed**. This is telemetry/replay evidence only;
  the next bounded canary still requires explicit downstream action/verification evidence
  and must stop on unknown usage.
