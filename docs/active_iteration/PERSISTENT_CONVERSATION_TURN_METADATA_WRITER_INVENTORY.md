# Persistent Conversation-Turn Metadata and Writer Inventory

> 状态：实施前事实冻结；本文件批准 contract 方向，不批准运行时接线。
>
> Baseline: the runtime error-boundary implementation that precedes this change.

## 1. 结论

This change introduces a public `IterationTurnRecordMetadata`. It is conversation/run-owned,
可独立持久化且可审计的 pre-task 控制记录；现有 contract 没有相同 owner 和 lifecycle。

该 public contract 只作为 envelope。以下事实使用 strict owned nested values，不新增各自的
`MetadataKind`：

- pre-task state；
- iteration disposition、outcome、completion scope 和 stop reason；
- authority ceiling 与授权来源；
- completion obligations、response candidate 和 grounding decision；
- iteration control cursor；
- root decision budget 与 no-progress signature；
- assistant ledger commit state；
- prepared/active task binding；
- previous-outcome evidence reference。

现有 `TaskRouteMetadata`、`SessionIngressState`、`RuntimeStateMetadata`、
`RuntimeCheckpointMetadata`、`RuntimeBudgetMetadata` 和 `RuntimeReportMetadata` 均不扩展为
pre-task owner。这样可以避免 response-only turn 伪造 Task、task checkpoint、core success 或
post-core policy。

## 2. 已审查的现有 contract 与判定

| Contract / value | 当前 owner 与 lifecycle | Decision |
|---|---|---|
| `TaskRouteMetadata` | UI entry 的一次 route decision | Keep `agent_generator | autonomous_iteration`; it must not own internal disposition. |
| `ConversationIdentity` | conversation/run/turn/project identity | 复用并嵌入 turn record，不复制身份字段 |
| `SessionTurn` | session ingress 接受的 raw user/assistant message | 保持 assistant ledger 的消息事实；turn record 只保存 commit binding/reference |
| `SessionIngressState` | 内存中的 conversation ingress、turn ledger、constraint ledger；task checkpoint 中可按值快照 | 不嵌入 pre-task control。后续增加独立 durable ingress store，而不是把 turn record 塞进 task checkpoint |
| `SessionExecutionCursor` | task session 的 semantic/decomposition/task-result cursor | 不扩展；它在 TaskGraph 之后生效，无法表示 pre-task response/evidence state |
| `RuntimeStateMetadata` | task-owned mutable operational state | 不扩展为 pre-task owner；其默认 mutation execution mode 和 improvement policy 不得泄漏到 response-only turn |
| `RuntimeCheckpointMetadata` | task runtime 的 immutable checkpoint generation | active task 后继续权威；turn record 只保留 integrity-bound checkpoint reference |
| `RuntimeBudgetMetadata` | task/tool/recovery/enhancement budgets | 不扩展；root decision/provider/grounding/decomposition budget 生命周期早于 Task |
| `DecisionNeedMetadata` | task/controller 提出的一个 evidence/action need | materialize task 后复用；不能替代 obligation owner |
| `FailureMetadata` / `ToolErrorMetadata` | failure 和 tool protocol evidence | 复用，不新增平行 error model |
| `DurableArtifactReference` | checksum/size-bound artifact reference | 复用 response payload、canonical task snapshot 和 previous outcome artifact refs |
| `ProjectFingerprint` | checkpoint-owned project/environment drift snapshot | active task 和跨 turn evidence freshness 复用 reference；不复制 project facts |
| `RuntimeReportMetadata` | completed task 的 derived auditable projection | response-only 不创建；project-task previous outcome 可引用 |
| `RuntimeFinalizationCursor` | task report/finalization lifecycle | 不扩展；response assistant ledger 使用独立 commit state |
| `RuntimeFactProjection` | CRU-1 内部、只读、secret-free derived view | UNDERSTAND_TASK 输入；不得持久化成第二份配置权威或授权事实 |

`MetadataKind`、`metadata.__all__`、`docs/metadata/CONTRACT_CATALOG.md` 和
`Code/tests/test_metadata_models.py` 的 completeness gates 已核对。新 envelope 落地时必须同步这四处。

## 3. Metadata impact notes

### 3.1 Iteration turn record

```text
Fact: 一个 autonomous conversation run 的 pre-task 控制、durability 和 task-binding 状态
Authoritative producer: AgentRuntimeController transition reducer
Consumers: unified autonomous entry、turn store、resume preflight、assistant ledger committer、task materializer、UI projection
Lifecycle: checkpoint-like durable conversation/run state；Task active 后保留 integrity-bound reference
Control impact: routing（autonomous 内部）、permission ceiling、budget、recovery、completion
Existing contracts reviewed: TaskRouteMetadata, SessionIngressState, SessionExecutionCursor, RuntimeStateMetadata, RuntimeCheckpointMetadata, RuntimeReportMetadata
Decision: new contract (`IterationTurnRecordMetadata`) with owned nested values
Why no duplicate source of truth is created: raw messages remain SessionIngressState-owned；task truth remains RuntimeCheckpointMetadata-owned；record only owns pre-task facts and references
Serialization and migration: schema 1.0；new feature flag only creates new records；legacy runs have no implicit record and cannot resume through the new path
Tests: kind/export/catalog, valid/invalid state combinations, JSON round-trip, integrity digest, historical missing-record rejection
Documentation updates: CONTRACT_CATALOG, API, active-iteration plan, trajectory alignment/log
```

### 3.2 Root authority state

```text
Fact: response_only | read_only_eligible | mutation_eligible ceiling plus producer/source/revision lineage
Authoritative producer: AgentRuntimeController from user intent, session constraints, confirmation and typed task facts
Consumers: disposition reducer, capability projection, task materializer, resume freshness gate
Lifecycle: turn-record owned until materialization；then mapped once into RuntimeStateMetadata.execution_mode
Control impact: permission
Existing contracts reviewed: RuntimeExecutionMode, RuntimeExecutionModeSource, SessionConstraintState, GuardDecisionMetadata
Decision: strict owned nested value; reuse existing task execution mode only after atomic binding
Why no duplicate source of truth is created: pre-task value is the source before Task exists；active task checkpoint becomes source after committed mapping
Serialization and migration: no default mutation eligibility；legacy absence cannot imply permission
Tests: default response-only, legal monotonic eligibility changes, confirmation/revocation freshness, forbidden direct mutation grant
Documentation updates: API, CONTRACT_CATALOG nested-value note, trajectory alignment
```

### 3.3 Outcome, completion scope and stop reason

```text
Fact: outcome and completion scope are orthogonal typed facts; stop reason is typed where it controls resumption
Authoritative producer: AgentRuntimeController completion reducer
Consumers: UI, persistence, resume, core/post-core admission
Lifecycle: turn-record outcome; task completion remains RuntimeState/Report-owned
Control impact: recovery and completion
Existing contracts reviewed: ResultStatus, RuntimeStateMetadata.core_success/completion_reason, RuntimeReportMetadata, Recoverability
Decision: strict owned enums/union; explanatory text remains diagnostic
Why no duplicate source of truth is created: response-only outcome never populates core_success；project outcome references verified runtime report
Serialization and migration: invalid outcome/scope combinations fail validation；no generic success=True migration
Tests: complete response vs project task, awaiting user, blocked/failed/interrupted/cancelled, illegal scope combinations
Documentation updates: API, CONTRACT_CATALOG, trajectory evidence
```

### 3.4 Completion obligation and grounding

```text
Fact: required claim/evidence obligations, legal satisfaction/waiver state, response hash and Runtime-owned claim coverage
Authoritative producer: AgentRuntimeController; provider may propose but cannot close or waive
Consumers: completion gate, evidence escalation, bounded repair, audit
Lifecycle: turn-record owned; selected evidence remains referenced by existing artifact/runtime facts
Control impact: completion
Existing contracts reviewed: DecisionNeedMetadata, GuardDecisionMetadata.required_evidence, ContextCandidate, VerificationPlanMetadata
Decision: strict owned values plus derived GroundingDecision
Why no duplicate source of truth is created: obligation owns required/closed state；DecisionNeed is only an action request；evidence bodies stay with existing owners
Serialization and migration: required obligations default open；unknown/stale/disputed/impossible do not become completed
Tests: closure/waiver matrix, non-waivable permission/verification/side-effect obligations, claim omission and source misclassification
Documentation updates: API, CONTRACT_CATALOG nested-value note, trajectory evidence
```

### 3.5 Root decision budget and no-progress

```text
Fact: max/used decision rounds, provider calls, response tokens, grounding repairs, decomposition calls and canonical progress signature
Authoritative producer: AgentRuntimeController reducer
Consumers: bounded response step, grounding, decomposition admission, resume
Lifecycle: turn-record owned；frozen mapping enters initial RuntimeCheckpoint when Task materializes
Control impact: budget and recovery
Existing contracts reviewed: RuntimeBudgetMetadata, ToolLoopMetadata counters, ProviderBudgetDiagnostic
Decision: strict owned RootDecisionBudget; do not extend task RuntimeBudgetMetadata
Why no duplicate source of truth is created: root budget owns pre-task consumption；task budget receives an explicit one-time mapped snapshot with lineage
Serialization and migration: counters start at zero, cannot exceed limits, and cannot decrease across generations
Tests: every boundary, over-consumption rejection, no-progress threshold, deterministic mapping to initial task checkpoint
Documentation updates: API, CONTRACT_CATALOG nested-value note, trajectory evidence
```

### 3.6 Assistant payload and ledger commit

```text
Fact: exact durable assistant payload ref/hash, stable message ID/turn index and pending|committed ledger state
Authoritative producer: Controller prepares payload; SessionIngress owns committed raw turn; turn store records commit binding
Consumers: idempotent ledger committer, recovery replay, UI display
Lifecycle: durable response artifact + turn record + durable conversation ingress
Control impact: persistence, idempotency, recovery
Existing contracts reviewed: SessionTurn, SessionIngressState, DurableArtifactReference, RuntimeFinalizationCursor
Decision: reuse SessionTurn and DurableArtifactReference; add owned commit binding/state to turn record; add independent stores, not another message ledger
Why no duplicate source of truth is created: exact content exists once in artifact and once as committed SessionTurn by protocol; hashes bind them and retries cannot create a new ID/index
Serialization and migration: same message ID + same payload hash is idempotent；same ID + different hash fails closed；legacy in-memory turns are not silently considered durable
Tests: crash at every write boundary, replay, duplicate same payload, conflicting payload, display only after committed record
Documentation updates: API, session-resume protocol, trajectory alignment/log
```

### 3.7 Prepared/active task binding

```text
Fact: none|prepared|active binding, canonical task snapshot ref/hash, task/state digest and active checkpoint reference
Authoritative producer: AgentRuntimeController task materializer and checkpoint store
Consumers: resume preflight, task runtime activation, turn audit
Lifecycle: turn-record binding until active；RuntimeCheckpoint owns task truth afterward
Control impact: permission, persistence, recovery
Existing contracts reviewed: RuntimeCheckpointMetadata, SessionBootstrapCursor, DurableArtifactReference, ProjectFingerprint
Decision: owned binding value reusing artifact/checkpoint IDs; no copied RuntimeState after active
Why no duplicate source of truth is created: prepared snapshot is the only pre-checkpoint task source；active binding contains references/digests only
Serialization and migration: content-addressed canonical snapshot required for prepared/active；legacy tasks cannot synthesize it during resume
Tests: prepared crash recovery, missing/corrupt snapshot, checkpoint-before-active crash, digest mismatch, authority revocation/rejection freshness
Documentation updates: API, AGENT_LOOP_SESSION_RESUME, CONTRACT_CATALOG nested-value note, trajectory alignment/log
```

## 4. 当前控制写入者 inventory

### 4.1 Conversation/session owner

- `ui.enhanced_cli._execute_goal_interactive` 直接生成 user/assistant message ID、turn index 和 run ID；
- `SessionIngress.open_turn` 校验并 append raw turns，同时推进 constraint cursor；
- assistant turn 当前在整个 autonomous/agent-generator 调用返回后用
  `Execution result: {str(result)[:2000]}` 临时构造，没有 durable payload-first/idempotent commit；
- `SessionIngressState` 当前仅在进程内持有，或作为 task checkpoint 的 nested snapshot 持久化；
- constraint `/confirm`、`/reject`、`/revoke` 由 UI 调用 `SessionIngress` reducer。

迁移要求：UI 仍拥有输入交互，但 new autonomous path 的 message ID、payload 和 commit 状态由
Controller/turn store 协议生成；Agent Generator 保持 golden parity，不进入新 record。

### 4.2 Task runtime state writers

`RuntimeStateMetadata` 自身当前含两个业务 mutator：

- `request_replan` 写 `replan_count`、budget、phase、unknown；
- `block` 写 phase 和 completion reason。

`AgentRuntimeController` 当前写入：

- initial phase；
- recover/verify/summarize transitions；
- verification status；
- core success、project improvement status/failure；
- completion reason；
- checkpoint generation、pending request/tool/result/finalization cursors；
- tool/evidence absorption 和 no-progress。

`_RuntimeSessionExecutor` 当前决定 semantic → memory → decomposition → execute → assemble →
improvement 的固定 session pipeline，并产生 generic `dict + success` result；其结果由
`AgentRuntimeController._absorb_session_result` 再写回 task state。

`ToolPlanningTaskExecutor` 当前直接写：

- phase（blocked/recover/summarize 等）；
- completion reason；
- read-only evidence synthesis completion；
- tool-planning no-progress state。

`IntelligentAutopilot` 和 `_RuntimeSessionExecutor._update_stats` 还会从 task list 的 `all(...)`
派生局部 `core_success`/top-level success，再经 dict result 传回 Controller。

Migration requirement: keep one reducer for pre-task transitions; task runtime writers gradually
返回 typed transition proposal，由 `AgentRuntimeController` 应用。不得先在旧 pipeline 外增加第三个
自由状态机。`RuntimeStateMetadata` mutator 在兼容期只由 Controller 调用。

### 4.3 Diagnostic/report consumers（非 authority）

- `runtime_diagnostics.collector/recorder/hooks` 读取 phase、verification、completion；
- `RuntimeReportMetadata` 和 finalization path 派生 auditable report；
- UI 读取 failure/result projection；
- post-core admission 读取 core success 与 improvement policy/status。

这些 consumer 不得成为 transition writer，也不得从 reason/log/text 反推控制状态。

## 5. 持久化与原子性边界

现有 `RuntimeCheckpointStore` 提供：

- per-run single-writer lease；
- immutable generation + atomic latest pointer；
- checksum/size-bound recovery artifacts；
- sensitive-key rejection；
- generation conflict detection。

Reuse the existing file-atomic-write, checksum, safe-ID, and lease semantics while creating an
independent conversation/run turn store,
不能创建假的 `RuntimeCheckpointMetadata`。至少需要：

1. immutable `IterationTurnRecordMetadata` generations；
2. atomic latest record pointer；
3. response/canonical-task artifact store；
4. durable `SessionIngressState` snapshot with message-ID conflict checks；
5. record/ingress generation compare-and-swap；
6. crash fault points覆盖 response prepared、ledger committed、prepared binding、initial checkpoint、active binding。

## 6. 实施顺序

1. Contract tests: enums, owned values, strict outcome unions, budgets, integrity, and invalid combinations;
2. 新增 `IterationTurnRecordMetadata`、export、catalog 和 API；
3. turn/ingress store tests 与 atomic persistence；
4. assistant payload idempotent commit/recovery tests；
5. prepared/active task binding tests；
6. Controller transition reducer 与 root writer migration；
7. feature-flagged autonomous entry wiring；
8. Agent Generator parity、trajectory 和 crash matrix。

在第 1–5 步通过前，不把 CRU-1 `RuntimeFactProjection` 接到用户回答，也不改变 mandatory
decomposition admission。
