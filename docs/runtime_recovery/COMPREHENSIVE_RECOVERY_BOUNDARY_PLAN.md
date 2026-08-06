# Comprehensive Runtime Recovery Boundary Repair Plan

## 1. 目的和计划权威

本计划把已有 checkpoint 方案、恢复状态方案、真实任务暴露的问题和剩余恢复边界合并
为一条可执行路线。它是后续断点恢复工作的唯一实施顺序来源；详细契约仍由 metadata
模型和根目录协议文件决定。

目标是在固定预算下，使典型任务：

- 更少因进程、上下文或预算边界硬中断；
- 从已声明支持的边界恢复时不重复应用结果、不重复副作用、不重置预算；
- 无法恢复时有明确 typed 状态、安全终点和可交接证据；
- 每次继续、对账、重规划、等待或终止都能由 metadata 和 evidence 解释；
- 未经证明的命令、网络写和外部副作用始终 fail closed。

本计划不包含记忆架构扩展、大规模 Agent benchmark、分布式事务系统或通用工作流框架。

## 2. 已完成基线

以下能力视为后续切片必须保护的回归基线，而不是待实现目标：

1. `RuntimeCheckpointMetadata` 和不可变 generation checkpoint store；
2. 原子写入、checksum、latest 损坏扫描回退、stale generation 拒绝；
3. 显式 `run_id + checkpoint_id + project_path` 恢复入口；
4. run/root task/session 身份延续，resume attempt 单独标识；
5. runtime state、root execution mode 和全部已消费预算恢复；
6. typed recovery status、recoverability、mode、automation policy、reason code、
   blockers 和 fallback；
7. 文件 create/replace/delete 的 prepared/observed/applied 哈希对账；
8. 文件已写但未应用时不重写，继续应用并执行持久化验证；
9. 单个 mutation 后 `pending_verification` 跨进程保留；
10. project/file drift 阻塞，绝不静默覆盖用户修改；
11. checkpointed run 的单写者 lease，以及 trajectory append 的跨进程顺序锁；
12. 缺失/损坏 checkpoint 的 typed 结论、上一有效代建议和保留证据终止；
13. CLI、trajectory 和 report 消费 typed 控制字段，解释文本不控制行为。

基线的已知限制：当前不能据此声称可从任意 session stage 继续，也不能自动重放通用
命令、包安装、网络写或外部系统操作。

## 3. 统一不变量

每个新边界在开放自动恢复前必须同时满足：

| 不变量 | 要求 |
| --- | --- |
| 身份 | 原 `run_id/root_task_id/session_id` 不漂移；subtask/step/call 不越权覆盖根身份 |
| 状态 | checkpoint 能确定最后已应用事实、当前位置和唯一下一动作 |
| 预算 | 已消费额度只恢复、不重置；同一结果或动作只计费一次 |
| 副作用 | prepared、observed、applied、verified 可区分；不确定副作用不自动重放 |
| 证据 | checkpoint 是恢复真相；trajectory/artifact/fingerprint 只作审计和对账证据 |
| 单写者 | 同一 run 同时只有一个执行 writer；事件 sequence 全局单调且不重复 |
| 完成 | 所有 required action 有成功证据后才允许完成 |
| 失败终点 | 每个不支持或不可恢复状态都有 typed blocker 和唯一安全 fallback |

任何一项无法证明，该边界只能进入 `waiting_user`、`waiting_retry`、`replan_required` 或
`unrecoverable`，不能用“从头重跑”掩盖。

## 4. Metadata-first 设计约束

### 4.1 现有契约复用

后续首先复用并按所有权扩展现有 runtime family：

- `RuntimeStateMetadata`：当前可变运行状态；
- `RuntimeCheckpointMetadata`：一个安全边界的不可变快照；
- `RuntimeResumeDecisionMetadata`：一次恢复预检结论；
- `RuntimeReportMetadata`：最终派生审计视图；
- `ToolCallMetadata`、`LLMRequestMetadata`、`LLMResponseMetadata`、
  `VerificationPlanMetadata`、`FailureMetadata`、budget 和各层 ID：已有事实与证据。

不创建平行 recovery metadata family，不把控制状态藏进 `reason`、`next_action`、
`attributes`、异常文本或终端文本。

### 4.2 候选扩展的审查顺序

下面只是待每个生产切片验证的候选形状，不预先批准为新公共契约：

- session/stage cursor：优先作为 `RuntimeCheckpointMetadata` 拥有的 strict nested value；
- bounded pending execution cursor：优先扩展现有 `pending_verification`/tool plan 所有权，
  避免第二份计划真相；
- LLM/tool result apply marker：优先复用 call/execution ID、artifact ref 和 checkpoint
  side-effect/apply 状态；
- tool recovery policy：优先扩展现有 `ToolContractMetadata`，按工具声明，不另建通用
  重放注册表；
- Recovery Bundle manifest：先做 artifact 的严格嵌套 manifest；只有出现独立持久化生命
  周期和多个真实消费者时，才评估新 `MetadataKind`；
- linked-run lineage：优先复用现有 run/checkpoint/correlation ID 引用，不复制原 run 状态。

### 4.3 本计划的 Metadata impact note

```text
Fact: 支持边界的执行位置、已应用标记、恢复能力、阻塞条件和兜底动作
Authoritative producer: runtime controller/checkpoint writer；工具能力由 tool contract producer 声明
Consumers: resume preflight、session/tool executor、supervisor、CLI、trajectory、report
Lifecycle: checkpoint + event evidence；Recovery Bundle 为 artifact
Control impact: routing | permission | budget | recovery | completion
Existing contracts reviewed: RuntimeStateMetadata, RuntimeCheckpointMetadata,
  RuntimeResumeDecisionMetadata, RuntimeReportMetadata, ToolContractMetadata,
  ToolCallMetadata, LLMRequestMetadata, LLMResponseMetadata,
  VerificationPlanMetadata, FailureMetadata, RuntimeBudgetMetadata, correlation IDs
Decision: 默认 reuse/extend/owned nested value/existing reference；新 contract 逐切片单独审查
Why no duplicate source of truth is created: checkpoint 持有恢复真相；trajectory/report/UI 只投影
Serialization and migration: 新 persisted 字段必须有保守历史读取；未知状态 fail closed
Tests: model legal combinations、JSON round-trip、故障注入、跨进程、真实任务
Documentation updates: catalog、API、session resume、supervisor、testing、trajectory log
```

## 5. 综合恢复边界矩阵

“开放”表示已有实现和回归证据；“局部”表示只覆盖明确子集；“关闭”表示必须阻塞或人工
处理，不能由计划文本推断已经支持。

| 边界/崩溃窗口 | 当前 | 恢复所需 durable facts | 目标处理 | 不满足时 |
| --- | --- | --- | --- | --- |
| task normalized | 开放 | root identity、project、budget、execution mode | exact/replan | waiting user |
| route/decomposition 后 | 开放 | stage cursor、plan hash、已完成 node | exact continue | replan |
| subtask 切换中 | 开放 | root/subtask ownership、next subtask index | continue remaining | block/replan |
| LLM request 发送前 | 开放 | request ordinal、input hash、pending marker | send once | retry later |
| LLM request in-flight | 关闭 | provider request ID、是否计费、响应探针 | probe/retry | bounded wait/manual |
| response 已保存未应用 | 开放 | response artifact/hash、apply marker | apply once | replan/corrupt |
| read tool prepared/in-flight | 开放 | call ID、input hash、project fingerprint | safe retry | replan |
| read result observed 未应用 | 开放 | result artifact/hash、apply marker | apply once | block on corruption |
| file mutation prepared | 开放 | tool input、before/expected hash、call ID | reconcile | manual conflict |
| file mutation 已发生未 observed | 局部 | current/expected hash、typed input | skip rewrite then apply | manual conflict |
| file result observed 未 applied | 开放 | observed result/failure、identity | apply once | fail closed |
| file applied 未验证 | 开放/有界命令序列 | exact pending verification | run persisted validation | waiting user/replan |
| 多个 validation 命令之间 | 开放 | ordered command specs、连续完成前缀 | continue remaining | replan/manual |
| validation observed 未 applied | 关闭 | command ID、result artifact、apply marker | apply once | idempotent rerun only |
| synthesis/report pending | 关闭 | input state hash、report generation | deterministic recompute | preserve state |
| final report 已写、run 未终止 | 关闭 | report ref、finalization marker | finalize once | repair projection |
| controlled stop/already complete | 开放 | final state/checkpoint | return without execution | corrupt -> fallback |
| user input/approval pending | 局部状态 | typed request、scope、expiry、answer ref | resume after action | waiting user |
| budget stop | 开放状态 | consumed counters、extension evidence | explicit extension only | preserve stop |
| latest checkpoint 损坏 | 开放 | immutable prior generation/checksum | offer previous valid | terminate evidence |
| all checkpoints missing/corrupt | 开放状态 | surviving run/event/artifact refs | Recovery Bundle | preserve evidence |
| schema/runtime incompatible | 局部 | version、migration result | explicit migrate | bundle/linked run |
| wrong project/root/task/session | 开放状态 | expected/observed identities | user correction only | no writes |
| active run lease | 开放 | lease ownership/liveness | wait and reassess | no concurrent writer |
| stale/crashed lease owner | 局部 | OS lock release；缺 owner diagnostics | reacquire after proof | waiting retry |
| checkpoint 写成功、event 写失败 | 局部 | checkpoint event cursor | continue, repair audit | never demote checkpoint |
| event 写成功、artifact 缺失 | 关闭 | artifact checksum/ref | block/recover artifact | bundle |
| 只读本地 command | 关闭 | cwd/env/input、idempotency declaration | registered retry | manual |
| mutating command/package install | 关闭 | idempotency key、before/after probe | per-tool reconcile | manual only |
| network read | 关闭 | freshness policy、request identity | bounded retry | retry later |
| network write/deploy/message/delete | 关闭 | remote key、stable resource ID、read probe | per-tool reconcile | manual/terminate |

## 6. 实施切片和依赖顺序

每一切片都采用 TDD：先锁定 metadata 合法状态和故障窗口，再改 controller/executor，最后
跑跨进程与真实任务。一个切片未通过退出条件，不进入下一项，也不顺带重构其他核心模块。

### P0-A：冻结边界清单与故障注入入口

**状态（2026-08-03）**：已完成。`CheckpointBoundary` 集中登记 durable boundary，
`CheckpointFaultPoint` 提供写前/写后 runtime-only 注入；未登记边界在 checkpoint Metadata
校验时拒绝。定向与全量离线测试通过（572 passed）。

范围：

- 把本矩阵中的 boundary name 与代码中的 `safe_boundary` 做一一映射；
- 为每个边界登记 owner、checkpoint 时机、可否自动、所需 evidence；
- 建立 before/after durable write 的统一 deterministic failure injection；
- 加一条检查，防止新增恢复分支匹配 explanation/exception 文本。

退出条件：边界名没有匿名字符串分支；每个未实现边界返回 typed unsupported/fallback；
现有基线测试不退化。

### P0-B：Durable session execution cursor

这是下一步最高优先级，也是当前 `tool_result_applied` 之后无法继续 monolithic session 的
根因修复。

**状态（2026-08-03）**：已完成。checkpoint 以 owned `SessionExecutionCursor` 持有
semantic snapshot、现有 typed task graph、plan hash、连续结果和 next task index；standard
与真实 CLI 的 enhanced-ui session 都在 decomposition 和每个 subtask 后推进游标，恢复只执行
剩余 subtask。真实 run `38df878196194c4287890b840a054050` 从 `next_task_index=1` 跨进程恢复。

范围：

- 持久化 route、decomposition、当前 subtask、stage、plan index 和已完成节点引用；
- 明确 cursor 的 owner、合法前进和失效条件；
- 恢复只执行 cursor 之后的剩余节点；
- cursor 与 checkpoint state/plan hash 不一致时转 replan，不猜测位置。

退出条件：多 subtask 任务在每个 stage 前后被终止，恢复结果与不中断基线等价；已完成
subtask、工具历史和预算均不重复。

### P0-C：LLM 与只读工具的 durable apply protocol

**状态（2026-08-03）**：已完成。checkpoint 拥有 bootstrap marker、pending LLM request、
checksum-addressed response/read artifact 和 apply-once ledger；observed response/read result 在
状态应用前落盘，恢复命中相同 hash/ordinal/call ID 时不再次调用 provider/tool，artifact 缺失
或损坏时 fail closed。

范围：

- 为 request prepared、response/result observed、state applied 建立持久边界；
- 使用 execution/call ID 和 artifact hash 去重；
- 明确 provider in-flight 无法查询时的计费和 bounded retry 策略；
- read tool 只有在 project/input fingerprint 仍一致时才重放。

退出条件：response/result 已观察后不重复调用；未观察且允许重试时最多执行一次新的、具
有关联的 attempt；预算不会因 apply/replay 重复计费。

### P0-D：Bounded pending execution cursor

**状态（2026-08-03）**：已完成当前 Goal 的有界验证序列。复用并扩展
`VerificationPlanMetadata`，以 ordered `command_specs`、`next_command_index` 和严格连续
`completed_commands` 前缀持有唯一计划；跨进程在第一条验证后退出时只执行剩余后缀。
通用命令、包安装与外部副作用仍按本计划 P2 保持关闭。

范围：

- 将当前单个 mutation 后验证扩成有界的多 tool/多 command cursor；
- 持久化每项 required need、selection、approval、状态、结果引用和当前位置；
- 验证失败可在剩余 replan budget 内形成新的 typed plan；
- completion gate 检查所有 required item，而不是只看最后一条命令。

退出条件：任意两步之间中断都不丢命令、不重复完成项；多验证任务跨进程恢复通过。

### P1-A：Finalization 与证据存储边界

**状态（2026-08-03）**：已完成。

**Metadata impact note**：

```text
Fact: runtime state 已完成、派生 report 已持久化、run completion event 已唯一应用、final checkpoint 已持久化
Authoritative producer: AgentRuntimeController；event append 由 DiagnosticRecorder 原子去重
Consumers: resume preflight、controller finalization、CLI、trajectory summary、runtime report
Lifecycle: RuntimeCheckpointMetadata 拥有 strict finalization cursor；report 为 checksum artifact；run/event 为投影
Control impact: recovery | completion | audit aggregation
Existing contracts reviewed: RuntimeStateMetadata, RuntimeCheckpointMetadata,
  RuntimeReportMetadata, RuntimeResumeDecisionMetadata, DurableArtifactReference,
  CheckpointBoundary, RecoveryMode, RecoveryReasonCode, EventRecord
Decision: 扩展现有 checkpoint/report family；不新增 MetadataKind，不复用自由文本控制分支
Why no duplicate source of truth is created: state-completed checkpoint 是任务事实；report 只派生；run/event 只投影
Serialization and migration: cursor 为 optional；旧 controlled_stop 保守兼容，不声称具备新 finalization exactly-once 证据
Tests: legal stage combinations、state hash/report checksum、四窗口故障注入、重复 resume、跨进程 event 去重
```

范围：

- 区分 state complete、report persisted、run finalized、final checkpoint durable；
- report 使用 state hash 保证可重算且不成为第二真相；
- checkpoint/event/artifact 任一写失败时定义明确的修复或降级路径；
- 解决“任务已完成但 run 仍 running”与重复 final event。

退出条件：在四个收口窗口逐点 kill 后，最终只有一个完成结论、一个连续事件序列，且
恢复不会重执行任务。

**完成证据**：四个 fault point 的确定性回归全部通过；另有真实子进程在
`runtime_report_persisted` 后 `os._exit(91)`，替换进程附着原 run 后只完成终结，未调用
session executor，最终只有一个 report artifact、一个 `task_finished` 和一个
`runtime_finalized` tip。旧 payload 通过 optional cursor 兼容；从未建立 durable boundary
的存储故障继续返回 `checkpoint_status=unavailable`，不宣称可恢复。

### C0-A：Prompt 上下文与 checkpoint 衔接

**状态（2026-08-03）**：已完成。

**Metadata impact note**：

```text
Fact: one model-facing context request selected one bounded payload and rendered one exact prompt
Authoritative producer: MemoryContextBuilder；durable binding 由 AgentRuntimeController 生成
Consumers: MemoryContextBuilder replay、resume preflight、session executor、trajectory diagnostics
Lifecycle: RuntimeCheckpointMetadata owns one strict prompt-context snapshot；payload lives in checksum artifact store
Control impact: recovery | model input | audit explanation
Existing contracts reviewed: ContextSelectionMetadata, RuntimeCheckpointMetadata,
  DurableArtifactReference, SessionExecutionCursor, CheckpointBoundary
Decision: reuse ContextSelectionMetadata and DurableArtifactReference；新增 checkpoint-owned strict nested value，
  不新增 MetadataKind，不扩展 ProjectStateMetadata.memory_context 为第二份权威状态
Why no duplicate source of truth is created: artifact 是当次已选 payload；snapshot 只保存 request/prompt hash、
  selection 和 artifact reference；memory stores 仍拥有原始候选来源
Serialization and migration: snapshot optional；旧 checkpoint 按原行为恢复，不声称可原样回放 prompt
Tests: strict hash/artifact contract、source changed 后原样 replay、corrupt artifact fail closed、跨 checkpoint 不重选
```

范围：

- 在 context selection 完成后保存 checksum artifact，并写入显式 `context_assembled` checkpoint；
- resume 只在完整 request hash 匹配且 artifact、Prompt hash、selection 均验证通过时回放；
- session cursor 和上下文 artifact 一起恢复，避免重新读取已变化的对话/记忆/项目索引；
- 若 Prompt 使用 dialog compaction，snapshot 同时绑定每个 `context_compaction` artifact；
  preflight 和 replay 均校验其 checksum/size 与 source-linked record，任一损坏即 fail closed；
- 不在本切片引入 tokenizer、embedding 召回、上下文压缩或全项目 Prompt 重构。

退出条件：上下文源在进程中断后发生变化时，恢复任务仍获得中断前的完全相同 Prompt；artifact
损坏时阻塞恢复；原 session 的已完成前缀不重跑。

**完成证据**：Metadata、builder replay、controller integration 和 corrupt-artifact
回归通过；真实子进程在 `context_assembled` 后 `os._exit(92)`，替换进程的对话源已变化，
但相同 request 得到与中断前完全相同的 Prompt。恢复使用原 session bootstrap，损坏 artifact
返回 `not_recoverable/checkpoint_corrupt`，不会调用 session executor。

### C0-B：Provider-aware 真实 Token 预算

**状态（2026-08-03）**：已完成。

**Metadata impact note**：

```text
Fact: one bounded context projection was measured with a named tokenizer before provider submission
Authoritative producer: MemoryContextBuilder using provider/model-bound TokenCounter
Consumers: context selection、checkpoint replay、trajectory diagnostics、operator configuration
Lifecycle: extend existing ContextSelectionMetadata；checkpoint artifact already preserves the record
Control impact: model input budget | recovery | audit explanation
Existing contracts reviewed: ContextSelectionMetadata, RuntimePromptContextSnapshot,
  LLMSettings, LLMResponse.usage, ToolInputMetadata.max_tokens/max_total_chars
Decision: extend ContextSelectionMetadata；reuse ToolInputMetadata.max_tokens；no new MetadataKind
Why no duplicate source of truth is created: tokenizer count controls the context slice before request；
  provider usage remains authoritative for the actual full request after response
Serialization and migration: new token fields have legacy defaults；old character-only payloads remain valid
Tests: exact tokenizer count、token-first truncation、unavailable fallback、checkpoint round-trip
```

范围：

- DeepSeek 配置使用官方离线 tokenizer artifact；
- token budget 可用时以 token 而不是字符决定 keep/partial/omit；
- Metadata 记录 tokenizer、模型、预算和裁剪前后 token；
- tokenizer 不可用时显式回退现有字符预算，不使用 `chars/4` 冒充真实 token；
- provider 响应的 usage 继续作为完整请求实际用量，不与上下文片段计数混淆。

退出条件：中英文混合长上下文按官方 tokenizer 保证不超过 token budget；checkpoint 回放保留
完全相同的 token 选择证据；无 tokenizer 环境的降级可解释且旧测试兼容。

**完成证据**：官方 DeepSeek tokenizer 安装到用户 cache 后，当前
`deepseek-v4-flash` 对 10,814-token 的中英文混合上下文裁剪为 511/512 tokens；Metadata
记录 provider tokenizer、模型和裁剪前后 token。无 tokenizer 测试保持 character fallback；
checkpoint 恢复测试证明 token selection 随 artifact 原样回放；`Code` 全量 `610 passed`。

### [已完成] C0-C：统一上下文装配内核（第一阶段）

目标：把 `MemoryContextBuilder` 中已经验证的预算、确定性选择、选择证据和 request fingerprint
提取为 `memory.context_assembly` 模块，同时保持记忆来源收集、Prompt 渲染、checkpoint handler
以及所有外部 payload 完全兼容。本阶段不迁移其他业务 Prompt，也不引入通用 Prompt 框架。

Metadata impact note：

```text
Fact: one model-facing context slice was deterministically assembled under one named policy and budget
Authoritative producer: ContextAssembler produces the derived selection evidence; source stores retain source facts
Consumers: MemoryContextBuilder, runtime checkpoint snapshot/artifact path, future prompt adapters
Lifecycle: runtime-only selection plus existing checkpoint artifact/event evidence
Control impact: budget and recovery
Existing contracts reviewed: ContextSelectionMetadata, ContextSectionDecision,
  RuntimePromptContextSnapshot, RuntimeCheckpointMetadata, LLMResponse.usage
Decision: reuse existing contracts; extract execution logic only
Why no duplicate source of truth is created: the assembler returns a derived bounded view and existing
  ContextSelectionMetadata; it neither stores source entries nor adds another persisted state model
Serialization and migration: no schema change and no payload migration; request strategy remains
  priority_then_recency_v1
Tests: direct assembler budget/determinism tests, MemoryContextBuilder compatibility, checkpoint replay,
  exact-token and full regression
Documentation updates: API, metadata catalog wording, test guide, implementation log, this plan
```

退出条件：`MemoryContextBuilder` 只负责来源收集和记忆专用渲染，预算与选择逻辑只有一个实现；
现有 payload、selection Metadata、request hash、真实 token 裁剪与 checkpoint 回放保持不变。

**完成证据**：新增 `memory.context_assembly.ContextAssembler`；直接装配、记忆构建、checkpoint、
恢复控制器和迭代流水线定向回归 `117 passed`。当前 `deepseek-v4-flash` 官方 tokenizer 将
8,740-token 混合输入裁剪为 512/512，`Code` 全量 `613 passed`。本阶段未迁移其他业务 Prompt。

### P1-B：Recovery Bundle 和显式 linked-run handoff

范围：

- 不可恢复时封存原 run，生成脱敏 artifact manifest；
- bundle 包含最后可信位置、已验证事实、未完成项、预算、副作用和 evidence refs；
- 只有无 indeterminate side effect 且用户授权时创建新 run；
- 新 run 记录原 run/checkpoint lineage，不冒充 exact resume，不修改原 run。

退出条件：每个 `not_recoverable` reason code 有唯一 typed fallback；全损坏 checkpoint
仍能导出可审计 bundle；linked run 的身份、预算和 lineage 测试通过。

### P1-C：Lease 生命周期与 supervisor bounded retry

范围：

- 补充 lease owner 的 PID/start time/runtime identity 诊断，不把文本当真相；
- 明确正常退出、崩溃、OS 锁释放和长时间无进展的处理；
- supervisor 只消费 typed policy，按原恢复预算 bounded retry；
- `run_lease_active` 只等待重评估，绝不并发 resume。

退出条件：双进程竞争、原进程 SIGKILL、快速重启和重复失败均无双 writer、无重复 sequence、
无无限恢复循环。

### P2-A：本地命令与环境变更逐工具开放

范围：

- 先审查并扩展 `ToolContractMetadata` 的 recovery capability；
- 首批只允许测试、静态检查、只读 Git、确定性环境查询；
- 包安装/环境变更必须有 lockfile、env identity 和后置探针；
- 未登记工具保持 `manual_only`，不存在通用“重跑上一命令”。

退出条件：每个开放工具都有独立幂等/对账测试和真实沙箱重复执行证据。

### P2-B：网络和外部系统边界

范围：

- network read 先定义 freshness 与 retry policy；
- network write 必须有远端 idempotency key、稳定资源 ID 和读取探针；
- 消息、部署、审批、支付、删除分别审查，不共享宽泛自动恢复许可。

退出条件：只有在真实测试环境证明“请求丢失、响应丢失、已成功未应用”三个窗口均不会
重复副作用后，单个工具才可开放；否则永久保持人工对账。

## 7. 每切片测试门禁

### 7.1 Metadata 和迁移

- valid/invalid legal combinations、assignment validation、JSON round-trip；
- 历史 payload 保守读取，未知 enum/version fail closed；
- explanation 文本任意变化不影响决策；
- snapshot-by-value 与 artifact/reference lineage 明确；
- 无第二份 authoritative fact。

### 7.2 确定性故障注入

- 每个边界的 durable write 前后强制退出；
- observed 与 applied 之间、budget charge 前后、checkpoint 与 event 之间；
- checkpoint store、trajectory、artifact store 分别失败；
- 重复 resume request 和两个进程竞争。

核心断言：文件/外部 mutation 最多一次、结果应用一次、预算计费一次、event sequence
唯一递增、完成结论唯一。

### 7.3 跨进程和真实任务

每个 P0/P1 切片至少包含：

1. 不终止的 deterministic baseline；
2. 独立进程在指定边界 `SIGKILL`；
3. 新 CLI 进程附着原 run 并恢复；
4. 核对身份、cursor、预算、文件哈希、验证结果和轨迹；
5. 一条真实模型 coding task，验收不绑定固定模型调用次数。

真实运行若暴露并发 writer、错误命令、身份漂移或未知副作用，只能记录为失败证据，修复
并复跑后才可计为成功。

## 8. 发布、迁移和回滚

- checkpoint 写入、resume、命令恢复、external recovery 分别受窄 feature gate 控制；
- 发布顺序为只写观察、只读恢复、本地文件、session cursor、本地注册命令、外部工具；
- persisted 字段新增提供默认值或显式 migration；无法安全迁移时返回 typed incompatible；
- 关闭 resume 不删除 checkpoint、trajectory 或 artifact；它们继续用于诊断；
- checkpoint 保留/清理不能早于 run 完成、审计期和 linked-run lineage 需求；
- 不用 Git reset/checkout 或覆盖 checkpoint 来“修复”恢复失败。

## 9. 综合完成定义

只有以下条件全部满足，才能宣称“典型本地开发任务的断点恢复边界已做牢固”：

- P0-A 至 P1-C 全部通过；P2 边界按工具明确标注开放或关闭；
- 支持边界中断后与不中断基线的最终 state、文件和验证结果等价；
- run/root/subtask/session/call 身份与 lineage 全程可解释；
- 预算、工具结果、mutation 和验证没有重复计费或重复应用；
- project drift 和 indeterminate side effect 永不被静默覆盖或重放；
- 每个 unsupported/not-recoverable 情况都有 typed blocker、fallback 和 evidence；
- Recovery Bundle 与用户授权 linked-run handoff 可用；
- supervisor 不读自由文本、不无限重试、不产生并发 writer；
- 全量离线测试、跨进程矩阵和至少一条真实多步任务通过；
- API、metadata catalog、session/supervisor 协议、测试指南、trajectory 对齐和实施日志同步。

## 10. 推荐近期执行顺序

近期只推进四刀，形成一个明显且可验收的提升：

1. P0-A：锁死边界名、unsupported 状态和故障注入；
2. P0-B：durable session execution cursor；
3. P0-C：LLM/read result 的 apply-once；
4. P0-D：多 tool/多验证 pending cursor。

完成这四刀后，真实 coding task 才能从“只恢复文件写入和单个验证”提升到“恢复整个多步
session 的剩余工作”。随后再做 finalization、Recovery Bundle 和 supervisor；命令与网络
按工具逐个开放，不与 session cursor 混在同一轮修改。

## 11. 文档与实施记录规则

每个切片开始前：

- 在本文件把该切片标为进行中并补齐该切片的 Metadata impact note；
- 对照 `docs/metadata/CONTRACT_CATALOG.md` 和实际 producer/consumer；
- 先提交失败测试和故障注入证据。

每个切片完成后同步：

- `docs/metadata/CONTRACT_CATALOG.md`（若契约变化）；
- `API.md`；
- `AGENT_LOOP_SESSION_RESUME.md` 和 `AGENT_LOOP_SUPERVISOR.md`；
- `docs/testing/TEST_DESIGN_GUIDE.md`；
- task trajectory 的 event alignment、ID stratification、README；
- `docs/task_trajectory/IMPLEMENTATION_LOG.md`，记录 observed failure、validation、fix 和
  remaining limitations。

计划本身不登记为已完成修复；只有代码行为和验收证据落地后才更新实施日志。

## 12. Goal 模式执行约束

本文件已经足够作为 Goal 模式的总路线图。执行中不再创建另一份综合计划，只允许在当前
切片开始前补充该切片的 Metadata impact note、失败测试清单和实测参数。这些补充不能
扩大 Goal 范围或改变本文件的不变量。

### Goal R1：本地多步 session 恢复

**状态（2026-08-03）**：已完成代码与真实任务验收；`Code` 全量离线测试 `593 passed`。

**Objective**：完成 P0-A 至 P0-D，使典型本地 coding task 能从原 run 的持久化游标继续
剩余 subtask、LLM/read apply 和多步验证，同时保持身份、预算、结果和文件副作用恰好
应用一次。

**In scope**：

- 边界登记与 deterministic failure injection；
- durable session execution cursor；
- LLM response/read result 的 observed/applied 协议；
- bounded pending execution cursor；
- metadata、controller、session/tool executor、checkpoint、trajectory 和必要 CLI 投影；
- 离线、跨进程和真实模型 coding task 验收。

**Out of scope**：

- Recovery Bundle、linked new run 和 supervisor 自动调度；
- 通用命令、包安装、网络写和外部系统自动恢复；
- 记忆/上下文架构和大规模 Agent benchmark；
- 与恢复边界无关的重构。

**Mandatory order**：P0-A → P0-B → P0-C → P0-D。每刀必须先有失败测试、通过该刀退出
条件、同步文档和实施日志，才可进入下一刀。

**Completion gate**：P0-A 至 P0-D 的退出条件全部通过；全量离线测试通过；至少一个真实
多 subtask、多验证命令任务被独立进程强制中断后，附着原 run 成功恢复；核对身份连续、
文件最多写一次、结果最多应用一次、预算最多计一次、event sequence 连续。任何 required
action 丢失、重复或被弱验证替代，都不得完成 Goal。

### Goal R2：收口、兜底与 supervisor

**Objective**：完成 P1-A 至 P1-C，使任务完成收口恰好一次，不可恢复 run 能封存并生成
Recovery Bundle，授权后可建立有 lineage 的 linked run，supervisor 能在单写者和恢复预算
内有界调度。

**依赖**：Goal R1 完成。不得为了推进 R2 回退或绕过 R1 的 cursor/apply-once 协议。

**Completion gate**：finalization 四窗口故障注入通过；每个 not-recoverable reason 都有
唯一 typed fallback；bundle 脱敏；linked run 必须显式授权；双 writer、重复 final event
和无限恢复循环均为零。

### Goal R3：本地命令和环境恢复

**Objective**：完成 P2-A，只按独立 tool contract 开放有幂等或对账证据的本地命令和环境
动作。

**依赖**：Goal R1、R2 完成。每个工具是一个独立生产切片；一个工具的证据不得给另一
工具授予自动恢复权限。

**Completion gate**：没有通用“重跑上一命令”路径；未登记工具 fail closed；每个开放
工具有契约测试、崩溃窗口测试和真实沙箱证据。

### Goal R4：网络和外部系统恢复

**Objective**：完成 P2-B，逐工具审查 network read/write 和外部系统副作用。

**依赖**：Goal R1 至 R3 完成，并且目标工具具备真实测试环境、稳定资源 ID、远端
idempotency key 或可靠读取探针。缺少其中任一条件时，该工具保持 manual only，这属于
正确终点而不是 Goal 阻塞。

**Completion gate**：每个开放工具在请求丢失、响应丢失、远端已成功但本地未 applied 三个
窗口均不会产生重复副作用；部署、消息、审批、支付、删除之间不共享宽泛许可。

### Goal 运行纪律

- Goal 状态只能在对应 completion gate 全部满足时标记 complete；测试接近通过、预算临近
  上限或只完成代码均不算完成。
- 发现真实失败先登记 evidence 和根因，再做最小修复；不得用扩大预算、从头新跑或降低
  验证强度绕过。
- 同一阻塞条件尚未满足 Goal 模式规定的 blocked 阈值时，继续完成安全的诊断、测试和
  文档工作；确实需要新权限、用户选择或外部条件时才交还用户。
- 新 metadata 必须先完成目录去重审查；自由文本不得控制恢复；未知状态默认 fail closed。
- 每次只允许一个切片处于进行中，不跨 Goal 顺手实现后续边界。
- 实测失败 run 永远保留为失败证据；修复后必须使用新的验收 run，不能把原 run 改写为
  成功。
