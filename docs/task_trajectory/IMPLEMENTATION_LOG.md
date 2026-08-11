# TASK_TRAJECTORY_IMPLEMENTATION_LOG.md

## 文档定位

这份文档是 OpenPilot 真实任务 / 任务轨迹证据工作的**实现总日志**。

它只记录两类内容：

1. **已经完成的问题切片**；
2. **阶段结束时明确可见的遗留问题**。

为了方便开发回看和对外汇报，本文档以后采用：

- **单一主文件维护**；
- **按日期分组记录**；
- **顶部总览 + 每日摘要 + 已完成切片 + 遗留问题 + 下一步计划** 的结构。

> 说明：历史内容已按“主要实现落点日期”重新归档。  
> 这是一种阶段性整理，不等价于逐 commit 级别的精确时间线。

---

## 更新规则

当一个问题切片满足以下条件时，必须在**同一个变更集**里更新本文件：

- 根因已经基本明确；
- 已经有实现改动；
- 已经有验证证据（测试 / 真实任务 / 轨迹）；
- 可以明确说出“这次解决了什么、还剩什么”。

如果只是：

- 还在猜测；
- 还没改代码；
- 还没验证；
- 只有方向，没有结论；

那么不应写入本文件，而应放在：

- `Thought.md`
- `REAL_TASK_FAILURE_ANALYSIS_*.md`
- 架构 / 计划类文档

---

## 推荐阅读方式

如果是**你自己回看开发过程**，建议看：

1. 顶部“进度总览”
2. 对应日期下的“已完成切片”
3. “当前遗留问题 / 下一步计划”

如果是**拿去给别人汇报进度**，建议只看：

1. 顶部“进度总览”
2. 每个日期下的“今日摘要（适合汇报）”

---

## 进度总览

| 日期 | 汇报主题 | 状态 | 验证结果 | 主要遗留 |
|---|---|---|---|---|
| 2026-07-04 | 方向切换、仓库清理、路径幻觉第一轮修复、轨迹证据层落地 | 已完成 | 路径相关测试与轨迹落盘验证 | 高层目标路径幻觉仍存在，后续 planning 仍未受证据强约束 |
| 2026-07-05 | timeout 恢复、planning surface、只读护栏、command path 硬化、真实任务复跑诊断 | 已完成 | timeout 回归、真实任务复跑、轨迹证据可复盘 | synthesis 空计划、`project_path` 贯穿不足、evidence 仍未成为唯一目标来源 |
| 2026-07-07 | 只读 synthesis 修复、fallback `project_path` 贯穿、最小路径守卫 | 已完成 | 定向 94 passed，全量 503 passed | route contract 仍粗、`runtime_mode` 仍非一等字段、全面 evidence-backed path policy 未完成 |
| 2026-08-02 | metadata 契约治理、固定上下文预算、选择记录与 prompt 去重 | 已完成 | metadata/上下文定向回归通过、全量 511 passed | 字符预算尚未结合 provider token 计数；对话边界还不是跨会话持久化恢复点 |
| 2026-08-05 | Session ingress、约束投影、checkpoint、Stage 5A 准入与 Stage 5B canary gates | 真实 Provider 小样本完成（边界受限） | 全量 947 passed；Stage 5B-2 定向 5 passed；Stage 5B-3a/3b 定向 4/17 passed；Stage 5B-3c fake 9 passed；Stage 6 实验层 45 passed；有效 Provider pair 2 calls、0 mutation | 仅 Task Designer 一对样本；raw dialog→ContextLoader/full project runtime 尚未覆盖；Stage 9 V1 冻结报告漂移；动态预算/reasoning 仍独立 |

# 2026-08-05

## 今日摘要（适合汇报）

- 找到并修复生产链路中的真实缺口：此前 Session Constraint reducer 只有
  离线测试，主 planner/decomposer 不稳定接收约束，pending proposal 也不会
  随 checkpoint 恢复；因此不能把 Phase 7 的 compact 结论当成完整对话收益。
- 建立交互 CLI 的稳定 conversation/run/turn ingress，并保持原始 turn 与
  长期记忆隔离；只把显式确认后的 active 状态投影给 planner。
- 将约束作为独立 required + non-truncatable candidate 接入 decomposer；
  tool planner、空计划 retry 和 tool-event request 都做 active projection
  召回检查，预算截断后缺失则在 Provider transport 前 fail closed。
- checkpoint 现在保存并校验 `SessionIngressState`，其约束快照必须与
  `RuntimeStateMetadata.session_constraints` 一致；resume 时拒绝身份或快照
  不一致的输入。

## 已完成切片

### [已完成] 生产 Session ingress 与约束投影

- 观察到的失败：每次 `execute()` 重新生成 run/session 身份，生产入口没有
  稳定 raw turn owner；确认/拒绝 reducer 没有 caller；主 decomposer 和
  tool planner 只能依赖后置 runtime guard，模型可能先做无效规划。
- 根因判断：对话身份、运行身份、turn cursor 和 runtime constraint state
  没有严格的跨模块 ingress 契约；把整个 `SessionIngressState` 直接塞入 prompt
  还会泄露 turn ledger，并且单 user candidate 的 HEAD 截断会丢掉约束。
- 实现修复：新增并接入 `ConversationIdentity`、`SessionTurn`、
  `SessionIngressState`、`SessionIngress`；主 planner/decomposer 使用 bounded
  source-linked projection；malformed/conflicting state fail closed；tool-event
  request 在完整 active projection 缺失时阻止 Provider transport。
- 验证结果：新增 ingress、prompt projection、retry、oversized-context、
  malformed/conflict、checkpoint round-trip 测试；`Code` 全量 **946 passed**，
  `git diff --check` 通过。
- 剩余限制：当前只完成离线生产 wiring；尚未证明输入/输出/调用次数 Token
  收益，也尚未运行真实 Provider canary。Stage 4 必须先完成零 Provider
  sentinel、shadow side-effect 和 usage coverage 门禁；动态预算/reasoning
  不与本切片混做。

### [阶段 4 门禁] 零 Provider compact/约束投影 sentinel

- 观察结果：Stage 10 分段 compact 与 Stage 11 会话约束投影的离线门禁共
  **10 passed**；active-constraint recall 为 **1.0**，assistant noise 不改变
  session-state hash，且 Provider/network/project mutation 均为 0。
- 停止原因：现有 Stage 9 Task Designer provider sentinel 在传输前发现提交的
  `STAGE9_TASK_DESIGNER_SCENARIO_CANARY_V2_OFFLINE_RESULT.json` 与当前确定性
  runtime snapshot 不一致，按设计 fail-closed（`offline Stage 9 gate is not
  frozen and passing`）。没有静默重冻 artifact，也没有执行 `--execute`。
- 结论：这是实验基线完整性问题，不是 compact 质量回归证据；因此 Stage 5
  真实 Provider paired canary 仍未获准。重新生成冻结报告必须作为独立、审阅过
  的实验基线变更，并重新运行离线回归后才能进入 Provider 门禁。

### [阶段 4A] ingress-aware compact sentinel

- Stage 11 的离线 fixture 已从直接构造 `SessionConstraintState` 改为走生产
  `SessionIngressState` 生命周期：`open_turn(user)` 产生 pending proposal，
  assistant turn 不增权，拒绝保持 inactive，确认后激活，撤销留下 tombstone。
- 新增身份门禁：跨 conversation、跨 project 和非单调 turn 均必须 fail closed。
  Stage 10 + Stage 11 定向测试仍为 **10 passed**，且未触发 Provider、网络或项目
  mutation；这证明的是 ingress/投影安全边界，不是 Provider 质量或 Token 收益。

### [阶段 5A] 完整 Session/compact canary admission contract

- 新增实验层版本化协议 `STAGE12_SESSION_COMPACT_CANARY_ADMISSION_V1.json` 与
  `stage12_session_compact_canary_admission.py`。它不改变生产运行时 authority，
  只为后续 canary 锁定 full-session projection scope、Stage 9 prerequisite
  hashes、read-only ready environment receipt、feature flag 和 kill switch。
- 约束了三类预算 scope：per-arm/campaign Token、call 和 wall-clock；未知 usage
  fail-closed；compact primary 与 current fallback 使用独立 account/receipt，
  不允许跨账本合并。`CanaryUsageLedger` 在 transport 前做 reservation，拒绝
  重放、超 cap、route/account 漂移和 usage overrun。
- 验证：Stage 10 + Stage 11 + Stage 12 **24 passed**；Stage 12 preflight
  为 `provider_execution_admitted=false`、Provider/network/project mutation
  均为 0。没有执行真实 Provider，也没有刷新旧 Stage 9 冻结 artifact。
- 剩余限制：Stage 12 仍是 admission-only；尚未把 kill switch、fallback receipt
  和 campaign ledger 接到完整生产执行路径，也尚未取得 full-session paired
  Provider Token/质量收益证据。Stage 5B 继续保持 pending。

### [阶段 5B-1/5B-2] project-improvement 约束传播与同源 compact sentinel

- 观察到的缺口：SessionIngressState 已进入主执行上下文，但 Goal Maker / Task
  Designer 这条 project-improvement 生产候选链仍会丢弃 typed session constraints；
  直接复用旧 Stage 9 paid shadow 又会混入旧冻结 artifact，无法证明是当前完整架构
  的同源输入。
- 实现修复：Goal Maker、Task Designer facade、pipeline 和
  `AutonomousIterationAgent` 统一接收并转发 `SessionConstraintState`；两个候选
  builder 都追加同一个 required/non-truncatable、source-hash-linked active
  constraint candidate。新增 Stage 5B-2 零 Provider probe，从真实
  `SessionIngress.open_turn → confirm_proposal` 生命周期生成 state，并用同一
  project/report/goal snapshot 分别装配 current/compact Goal Maker 与 Task
  Designer 请求。
- 验证结果：Stage 5B-2 **5 passed**；Provider/network/project mutation 均为 0；
  三个已确认约束在两臂的 recall 都为 **1.0**；Goal Maker 1,678/1,678，Task
  Designer current 1,717、compact 1,347 rendered tokens（该 fixture 下下降
  21.6%）。Provider input/output/total 全部为 `null`，避免把 offline estimate
  伪装成 observed usage；flag-off、kill-switch、compact fallback 和 transport
  injection 均 fail closed。
- 剩余限制：该 sentinel 直接调用 `AutonomousIterationAgent` 的 Goal/Task 边界，
  尚未通过 `IntelligentAutopilot.execute()` → `RuntimeController` →
  `ProjectImprovementRuntime`，也未把 raw SessionTurn 注入 ContextLoader；因此
  不能把它当作 full-session canary。Stage 5B-3 仍需在 Stage 12 admission、hard
  caps、独立 fallback ledger 和 unknown-usage fail-closed 全部接入后，才可执行一对
  真实 Provider 请求。

### [阶段 5B-3a/3b] full execute admission 与跨臂 pre-transport ledger

- 观察到的缺口：Stage 5B-2 虽已证明 Goal/Task candidate 投影，但没有通过
  `IntelligentAutopilot.execute()`、`AgentRuntimeController` 和 checkpoint；Stage
  5A 虽声明 campaign call/wall cap，原 ledger 实际只限制了单臂 call/wall。
- 实现修复：新增 full-entry 零 Provider admission probe，使用临时 checkpoint
  store 验证 raw turn、conversation/run/project identity 和 active constraint
  canonical hash 在 runtime state 与 checkpoint 间一致；新增跨臂
  `CanaryCampaignLedger`，在 transport 前预留 compact primary/current fallback
  两个 arm 的 Token/call/wall 预算，route/account/arm/unknown usage 全部严格校验。
- 验证结果：Stage 5B-3a **4 passed**；Stage 12 + 3b ledger **17 passed**；组合
  pre-transport gate 预留 4 个 Goal/Task 请求、observations 为 0、Provider/network/
  project mutation 为 0，且 `provider_execution_admitted=false`、
  `transport_attempted=false`。
- 剩余限制：仍没有真实 Provider usage、finish reason、reasoning token 或质量证据；
  下一切片只能在这些 gate 保持开启、unknown usage fail-closed、current fallback
  独立记账的前提下执行一对真实请求。

### [阶段 5B-3c] Task Designer 真实 Provider paired canary

- 观察结果：在 Provider-default reasoning 与既有 Task Designer 2,200 completion
  ceiling 下，compact/current 同源请求均完成 JSON Task，active constraint recall
  与写入目标质量均通过；compact rendered input 1,258 vs current 1,719，Provider
  input 1,361 vs 1,822，total 3,227 vs 3,891，分别下降 26.8%、25.3%、17.1%；
  reasoning tokens 1,674 vs 1,856，下降 9.8%。
- 实现与门禁：Provider request 使用 `transport_retries=0`、显式 campaign state
  persistence、primary/fallback 独立 account、pre-transport reservation、
  unknown usage fail-closed；full execute/runtime/checkpoint admission 在 pair
  前通过。两次 Provider call 均 observed usage、finish_reason=`stop`，无 project
  mutation。有效结果详见 `STAGE5B_3C_TASK_DESIGNER_PROVIDER_CANARY_RESULT_V1.md`。
- 失败探查：早期 runner 的 `max_retries=0` 实际未执行 Provider；512 completion
  reserve 造成空 length response；随后真实 attempt 出现已知 usage 的截断/JSON
  schema failure。均被记录并停止或走独立 current fallback，未把 unknown usage
  当 0，也未整段重生成。
- 剩余限制：这是一个 Task Designer/一个 source snapshot 的机制样本，不是完整
  conversation 或 project-improvement quality 结论；raw SessionTurn 仍未进入
  production ContextLoader/ShortMemory。下一阶段应先分析该样本，再单独设计
  reasoning routing 与多任务/多 Provider 扩样。

---

### [阶段 6] paired 结果分析与全会话生产边界审计

- 观察结果：有效 paired sample 中，compact/current 的 rendered input 为
  1,258/1,719（-26.8%），Provider input 为 1,361/1,822（-25.3%），total 为
  3,227/3,891（-17.1%），reasoning 为 1,674/1,856（-9.8%）；两臂各一次
  Provider call，均为合法 Task JSON、目标与约束通过、`finish_reason=stop`，无
  project mutation。实验层回归 45 passed，Code 全量 947 passed。
- 根因探查：该收益只能归因于当前 Task Designer request-boundary 的 compact
  机制信号，不能外推到完整会话。raw `SessionTurn` 尚未 hydrate 到
  `ShortMemory/ContextLoader`；`project_state.memory_context` 也未被 Goal/Task
  candidate builder 消费；project-improvement analyzer 在 Goal/Task 前还未接收
  typed session constraints。
- 额外边界：CLI route 分类发生在 ingress 接收之前，agent-generator 可能绕过
  ingress；`_load_context` 会吞掉异常返回空 context；未来 request hash 需包含
  ingress turn-ledger digest。真实 ContextLoader 还可能写入 `sketch.json` 与
  file-index artifacts，不能沿用当前 zero-mutation 假设；空 constraints 的
  ingress 仍可能绕过冲突的 conversation/project identity 校验，必须无条件
  fail closed。
- reasoning 结论：保留现有 provider-neutral `ReasoningPolicy`/capability
  profile 分层；只有 exact `effective=disabled` 才能作为关闭思考 treatment，
  generic/unknown provider 的 omitted/provider-default 必须单独分层。后续实验
  冻结 compact、schema、completion ceiling、cache 与 source，至少三组交错 pair，
  保留 nullable reasoning usage 和原始 finish reason。
- 实现状态：本阶段只补齐证据文档与下一阶段门槛，没有扩大 Provider 流量，也没有
  把未验证的 raw dialog 适配器伪装成已完成。
- 证据：`experiments/full_architecture_context_observation/STAGE6_CANARY_ANALYSIS.md`。

### [阶段 7a] ingress ownership 与 ContextLoader 只读/失败契约

- 观察到的失败：raw ingress 存在但 active constraints 为空时，冲突的
  conversation/project identity 可能绕过部分 runtime 校验；ContextLoader 还会
  隐式刷新 `sketch.json`/file-index，并把 source、snapshot 或 compaction 异常吞成
  空上下文，形成“成功但上下文缺失”。
- 实现修复：`IntelligentAutopilot.execute()` 与直接
  `AgentRuntimeController.run()` 都在 raw ingress 存在时无条件校验 conversation/
  session alias 与 canonical project root；`ContextLoaderAgent` 显式使用
  `project_index_mode=read_only`、`strict_sources=True`；`MemoryContextBuilder`
  增加 typed mode 与 `ContextSourceError`，严格源失败 fail-closed，显式
  `ProjectManager.update` 语义保持不变。
- 验证结果：相关 ingress/constraint/checkpoint 回归通过；memory/pipeline/context
  定向回归通过；`Code` 全量 **954 passed**；未增加 Provider/network/目标源码写入。
- 剩余限制：run_id 生命周期还未收紧；raw SessionTurn 尚未进入 ContextLoader 的
  DIALOG 派生视图，`project_state.memory_context` 仍未证明被 analyzer/Goal/Task
  request 消费；下一阶段按
  `docs/context_management/PHASE_8_STAGE7B_RAW_DIALOG_ADAPTER_PLAN.md` 实现，
  仍先零 Provider。

### [阶段 7b-1/7b-2] raw-dialog 投影传播与 analyzer/Goal/Task 消费证明

- 观察结果：ContextLoader 已能从 ingress turns 得到有界 DIALOG，但此前
  project-improvement analyzer、Goal Maker、Task Designer 仍只接收 constraints，
  不能证明 raw dialog 影响任何 model-facing request。
- 实现修复：新增复用的 `memory/session_dialog.py` adapter，校验 conversation/project、
  turn cursor、顺序和 message ID，输出稳定 source-linked DIALOG candidates 与
  turn-ledger SHA-256；MemoryContextBuilder 与三类 project-improvement candidate
  builder 复用同一投影。`SessionIngressState` 从 Autopilot →
  ProjectImprovementRuntime → AutonomousIterationAgent → Pipeline → ContextLoader/
  Goal/Task 贯通；analyzer 使用 runtime ingress handle 重算 digest，并在 typed
  `ToolInputMetadata.session_turn_source_hash` 不一致时 fail closed。
- 权限与失败边界：assistant/raw prose 仍只能是 DIALOG；active constraints 仍是
  独立 required/non-truncatable candidate；raw ledger 不进入 MemoryStore/LongTerm；
  ContextLoader source/预算/治理失败不再由 IterationAgent 转换为空成功。
- 验证结果：candidate builder 与 project-improvement request 定向测试通过；`Code`
  全量 **965 passed**；`git diff --check` 与 py_compile 通过；没有新增 Provider
  traffic 或目标源码 mutation。
- 剩余限制：当前还没有组合验证 compact/current、checkpoint resume、feature flag/
  kill switch 和 ContextLoader→analyzer→Goal→Task 的完整 no-provider gate；7B-3
  必须先完成这些组合测试，之后才重新评估 full-session canary。

### [阶段 7b-3a] 同源 current/compact 与 checkpoint raw-ledger 组合门

- 观察结果：仅证明 candidate builder 能接收 ingress 还不足以覆盖 session 生命周期；
  必须同时证明 current/compact 两臂没有各自重建或丢弃 raw dialog，并证明真实
  execute/runtime checkpoint 可以恢复同一份 raw turn ledger。
- 实现修复：Stage 13 离线 harness 为每个 Goal/Task request 记录 bounded DIALOG
  source IDs、dialog recall 与 `session_turn_source_hash`；Stage 14 的真实入口夹具
  增加 assistant raw turn，并对 ingress/checkpoint ledger 重算 hash；Stage 15 将
  这些 lineage checks 纳入 pre-transport gate。原始 turns 仍只存在于
  `SessionIngressState`/checkpoint，未写入 MemoryStore 或 LongTerm。
- 验证结果：Stage 7B-3a 定向回归 **12 passed**；两臂 dialog/constraint recall 均
  为 `1.0`，checkpoint 与 ingress raw-ledger hash 一致，Provider/network/目标源码
  mutation 均为 `0`。这不是 Provider 质量、成本或全链路收益结论。
- 剩余限制：feature-off、kill-switch、compact failure fallback receipt 及完整
  ContextLoader→analyzer→Goal→Task no-mutation 组合门仍在 7B-3b；通过后才进入
  provider-attempt output/reasoning telemetry。

### [阶段 7b-3b] full ContextLoader 链路与 typed fallback 安全门

- 观察结果：7B-3a 证明了 Goal/Task projection 和 checkpoint lineage，但没有真实
  `ContextLoaderAgent`→analyzer 的连续消费证据；fallback 也只有计数/字符串，无法
  证明失败 compact 与 current fallback 共享 ingress、约束和权限边界。
- 实现修复：新增零 Provider full-context sentinel，注入临时 `MemoryStore` 与
  `ProjectManager`，用 `read_only + strict_sources` 运行 ContextLoader，再把同一
  ingress/hash 交给 analyzer、Goal、Task。项目树和 memory store 做前后哈希快照；
  `CompactFallbackReceipt` 记录 failed/effective arm、execution IDs、source snapshot、
  turn/constraint hash 和 provider/network/project mutation 计数。Stage 15 同时校验
  Stage 12 locked protocol 的 controls 与运行参数一致，并检查 preflight status。
- 验证结果：7B-3b 定向 **16 passed**；Stage 10/12/13/14/15/16 加 7B-3b 组合回归
  **47 passed**。full-context 正常与 fallback 路径均 Provider/network/project/memory
  mutation 为 `0`，dialog/constraint recall 为 `1.0`，analyzer 仅调用 1 次本地 stub。
- 剩余限制：fallback sentinel 的失败点是候选构造注入，不是实际 compactor source
  binding 回退；Stage 10/Code atomic-compaction 契约仍是该部分权威。尚未开启 Provider
  output/reasoning telemetry，也没有 Token 收益、多模型或真实任务质量结论。

#### 7b-3b 门禁补强（零 Provider）

- 观察到的缺口：已有 fallback receipt 虽保留 source/turn/constraint hash，但没有
  结构化证明 ContextLoader 的只读环境与写作用域沿 current/compact 两臂保持一致；
  Stage 15 也只能检查 preflight 的布尔状态，不能检查返回值是否仍匹配锁定协议控制。
- 实现修复：实验层新增 `OfflineContextLineageReceipt`，在两臂顶层结果和
  `CompactFallbackReceipt` 中共同记录 source snapshot、raw turn digest、active
  constraint digest、`project_environment_mode=read_only` 与 typed write-scope digest，
  并在 full-context gate 中强断言两臂 receipt 完全一致。Stage 12 preflight 现在回传
  feature flag/kill switch，Stage 15 在预算预留前将其与协议及运行参数逐项比对。
- 验证证据：新增失败优先测试覆盖跨臂 lineage、fallback authority 字段和 preflight
  control drift；Stage 7B-3b/13/14/15/12 定向组合 **31 passed**，Provider/network/
  project/memory mutation 均为 `0`，`git diff --check` 通过。
- 剩余限制：receipt 仍是实验派生证据，不改变生产权限 owner；fallback 注入仍模拟
  候选装配失败而不是实际 compactor binding 故障。Provider 质量、成本与真实任务收益
  仍未由此门禁证明。

### [阶段 7b-3c / 7C] provider-attempt 输出与 reasoning 遥测契约

- 观察结果：成功响应已有 usage/finish reason，但失败 attempt 只在嵌套 free-form
  payload 中保存部分 usage；request hash、attempt ordinal、provider identity、
  normalized endpoint、reasoning usage 和 recovery 链接无法稳定重放。若把 unknown
  usage 当作零，campaign ledger 会产生虚假的成本与成功信号。
- 实现修复：LLM diagnostics producer 在既有 `trace_info`/`provider_details` escape
  hatch 中写入 credential-free request hash、ordinal、attempt ID、provider/model/
  endpoint 和 transport-attempted；新增 experiment-only `ProviderAttemptReceipt`
  projection 与 diagnostic-event adapter，显式区分 responded/failed/replayed/
  pretransport_blocked，保留 raw partial usage，reasoning 可空，且绑定 finish/retry/
  repair/recovery evidence。未新增 MetadataKind 或执行权限。
- 验证结果：Stage 7C receipt/event 回归 **7 passed**；runtime diagnostics 回归
  **35 passed**。完整 usage 才允许结算；unknown/partial usage、total mismatch、
  reasoning>output、缺 hash 和错误 replay 链都 fail closed。
- 剩余限制：这仍是离线 telemetry/replay contract，不是 Provider 质量或 Token 收益
  结果。下一阶段必须把 receipt 与真实 canary 的下游 action/verification evidence
  绑定，并在多任务/多 Provider 前保持 Current fallback 与 kill switch。

# 2026-08-02

## 今日摘要（适合汇报）

- 完成 77 个公开 metadata 契约的首轮全景盘点；
- 修复路径与问题信号模型覆盖公共 `source` 信封的问题；
- 把所有具体模型的 `kind` 锁定为唯一 Literal；
- 增加受测试约束的 metadata 完整目录和演进检查清单；
- 为生产上下文入口增加固定字符预算、逐节选择说明和对话边界；
- 消除 Goal Maker / Task Designer prompt 中的 memory context 重复副本；
- 全量 511 个测试通过。

## 已完成切片

### [已完成] metadata 公共信封与 kind 不变量加固

- 观察到的失败：`PathIntentMetadata`、`PathResolutionMetadata` 和
  `ProblemSignalMetadata` 把 `MetadataBase.source: MetadataSource` 覆盖成了
  业务字符串，序列化后不再满足 `API.md` 声明的统一信封；另有 16 个
  具体模型把 `kind` 声明为普通 `MetadataKind`，调用方可以构造 kind 与
  模型类型不一致的载荷。项目此前也没有能够完整回答“有哪些 metadata、
  分属哪个边界”的目录。
- 验证证据：对 `metadata.__all__`、`MetadataKind` 和 Pydantic 字段进行
  动态盘点，确认当时有 76 个公开具体模型与 76 个 kind，一一对应；同时
  稳定复现 3 个公共字段覆盖和 16 个未锁定 kind。
- 实现修复：业务来源改用 `path_source` / `signal_source`，公共 `source`
  恢复为 `MetadataSource`；旧版字符串 `source` 载荷在读取时自动迁移；
  所有具体模型的 kind 改为唯一 `Literal[MetadataKind.*]`；新增
  `docs/metadata/CONTRACT_CATALOG.md`，记录完整目录、所有权、生命周期、已知压力点
  和增删字段检查清单；新增测试阻止公共信封覆盖、重复/缺失 kind 以及目录
  漏项。
- 验证结果：metadata 定向测试 24 passed；路径治理、问题诊断、tool loop
  和 runtime controller 相关回归 148 passed；`Code` 全量 508 passed。
- 剩余限制：`ToolInputMetadata` 仍是包含大量可选字段的宽兼容契约；
  `attributes`、`raw_payload`、`details` 等 `JsonValue` 容器仍削弱部分结构
  保证，后续应按真实生产/消费链逐步类型化，不做一次性大重构。

### [已完成] 生产上下文固定预算、选择解释与 prompt 去重

- 观察到的失败：`MemoryContextBuilder` 只限制每类返回条数，不限制单条内容
  或最终 prompt 长度；未接入生产链的 `ContextCompressor` 无法阻止超长
  context。构建结果同时保存结构化条目和渲染后的 `prompt_text`，Goal Maker
  与 Task Designer 又完整序列化二者，使同一上下文证据重复进入模型输入。
- 验证证据：构造 5 条长对话即可让旧 builder 在没有任何预算/裁剪记录的
  情况下生成无界 prompt；构造带唯一标记的 memory context，可在两个下游
  prompt 中观察到相同证据重复出现。
- 实现修复：生产 builder 默认执行 16,000 字符上限，并允许 memory context
  tool 通过既有 `max_total_chars` 显式覆盖；选择顺序固定为 system prompt、
  最近对话连续后缀、相关文件、相关记忆、环境证据；新增第 77 个公开契约
  `ContextSelectionMetadata`，记录预算前后字符数、各 section 的保留/部分
  保留/丢弃原因、对话选取起点和时间戳。Goal Maker 与 Task Designer 改用
  单份 `prompt_text` 加选择记录，不再重复携带结构化内容；该记录通过现有
  `pipeline_progress/context_loader` 事件进入任务轨迹。
- 验证结果：上下文预算、显式工具预算、metadata 完整性、prompt 去重和轨迹
  落盘均有定向测试；`Code` 全量 511 passed。
- 剩余限制：当前上限是确定性的字符预算，不等同于 provider tokenizer 的
  精确 token 预算；记录的对话起点可以解释本次选择，但省略消息仍依赖拥有
  它们的 memory store，尚不能声称支持跨会话 durable resume；其他自主
  prompt 构造器仍需按实际 token 证据逐一纳入预算，而不是一次性统一改写。

## 当日关联文档

- `docs/metadata/CONTRACT_CATALOG.md`
- `docs/task_trajectory/TASK_TRAJECTORY_EVENT_ALIGNMENT.md`
- `API.md`
- `README.md`

---

# 2026-07-04

## 今日摘要（适合汇报）

- 工作主线从“自动修复测试”转向“证据优先的真实任务诊断”；
- 清理了旧 loop 方向残留，重新确立任务轨迹证据为主线；
- 解决了第一类 `/workspace/openpilot` 幻觉路径问题；
- 落地了任务轨迹证据层与 task / subtask / tool-call id 关联；
- 真实任务开始可以被稳定复盘，而不再只能翻终端日志。

## 已完成切片

### [已完成] 工作方向从 repair-first 切换到 evidence-first

- 背景：旧方向更偏向“生成测试 -> 跑测试 -> 修失败 -> 继续循环”。
- 失败现象：这种流程容易让系统过早修表面症状，而不是先稳定收集根因证据。
- 根因判断：问题不在于缺测试，而在于“问题发现、证据采集、假设形成、验证、修复”几个阶段没有被强约束地分开。
- 实现改动：主线工作流切换成“真实任务运行 -> 完整轨迹记录 -> 失败模式总结 -> 根因假设 -> 验证任务 -> 必要时再 repair”。
- 验证结果：新的文档、任务轨迹与 failure analysis 流程都围绕这一方向建立。
- 剩余限制：自动聚类与阶段总结仍然偏弱。

### [已完成] 仓库范围重置与旧 loop 残留清理

- 背景：构建新轨迹证据层前，仓库里还保留着上一轮 `codex_loop` / auto-test-repair 残留。
- 失败现象：旧文档、旧测试假设和新流程叙述不一致，容易混淆边界。
- 根因判断：仓库叙事没有及时跟着架构方向切换同步。
- 实现改动：活跃文档主线改为 `docs/task_trajectory/*`；旧版 real-task diagnostics 文档只保留为兼容指针。
- 验证结果：当前工作说明文档已经以 trajectory evidence 为中心。
- 剩余限制：后续仍需严格执行“完成一个切片就更新日志”的纪律。

### [已完成] `/workspace/openpilot` 幻觉根路径第一轮修复

- 背景：真实任务“请梳理从 CLI 入口到主执行运行时的核心链路，并指出关键模块之间的关系”首次运行时出现了容器式路径幻觉。
- 失败现象：agent 访问了 `/workspace/openpilot`，而本地真实项目根是 `/Users/abab/Documents/openpilot/Code`。
- 根因判断：系统对稳定 project root、cwd 与文件目标 grounding 的约束不够强，不能只靠模型记忆 prompt 内的路径。
- 实现改动：引入基于 project root 的路径 resolver，并把已知幻觉根路径映射回声明的 `project_path`。
- 验证结果：相关验证覆盖集中在：
  - `/Users/abab/Documents/openpilot/Code/tests/test_project_path_resolver.py`
  - `/Users/abab/Documents/openpilot/Code/tests/test_project_path_runtime_integration.py`
  - `/Users/abab/Documents/openpilot/Code/tests/test_path_boundary_validation.py`
- 剩余限制：虽然解决了已知幻觉根路径，但高层 guessed target（如 `setup.py`、`/openpilot/...`）仍未根除。

### [已完成] 路径意图 / 路径解析结果证据化

- 背景：路径字符串此前常被静默归一化，后续很难知道系统到底做了什么修正。
- 失败现象：即使路径被纠正或阻断，runtime state 和 trajectory 里也不一定能看出来。
- 根因判断：缺少显式承载路径意图与解析结果的元数据层。
- 实现改动：引入并记录：
  - `PathIntentMetadata`
  - `PathResolutionMetadata`
  - `RuntimeStateMetadata.path_intents`
  - `RuntimeStateMetadata.path_resolutions`
- 验证结果：路径纠正和阻断现在能在 runtime state 与后续轨迹分析中直接看到。
- 剩余限制：还缺少更强的硬约束，确保后续 planning 只能从 observed evidence 或 resolver-backed candidates 中选文件目标。

### [已完成] 任务轨迹证据层与 id 关联落地

- 背景：系统已经有 logger、tool loop、metadata、artifact 等基础能力，但证据分散在多个位置。
- 失败现象：任务失败后只能翻终端，很难稳定复盘一次真实运行到底发生了什么。
- 根因判断：缺少统一、持久化、可关联的 trajectory evidence layer。
- 实现改动：
  - 打通 `/Users/abab/Documents/openpilot/Code/src/runtime_diagnostics/`
  - 让真实任务写出 durable trajectory
  - 明确 root task / subtask / step / call id 分层
- 验证结果：真实任务运行现在会持久化写到：

```text
/Users/abab/Documents/openpilot/Code/data/runtime_diagnostics/task_trajectory/
```

典型文件包括：

```text
run.json
events.jsonl
artifacts.jsonl
artifacts/
summary.json
```

- 剩余限制：自动聚类、自动阶段总结、用户侧错误展示中的 id 清洗还要继续增强。

## 当前遗留问题

- 高层 guessed target 仍然存在，不只是低层 resolver 问题；
- planning 仍然可能脱离已采集证据；
- 自动聚类与自动阶段总结还不够强。

## 当日关联文档

- `/Users/abab/Documents/openpilot/docs/task_trajectory/TASK_TRAJECTORY_EVIDENCE.md`
- `/Users/abab/Documents/openpilot/docs/task_trajectory/TASK_TRAJECTORY_EVIDENCE_ARCHITECTURE.md`
- `/Users/abab/Documents/openpilot/docs/task_trajectory/TASK_TRAJECTORY_ID_STRATIFICATION.md`
- `/Users/abab/Documents/openpilot/docs/task_trajectory/TASK_TRAJECTORY_EVENT_ALIGNMENT.md`

---

# 2026-07-05

## 今日摘要（适合汇报）

- 为 LLM-backed tools 增加了有界 timeout 恢复与 fallback；
- 引入 planning surface，缩小 planner 首轮看到的能力面；
- 把 `project_improvement_runtime` 纳入统一 trajectory stream；
- 把分析类任务的只读约束下沉到 planner、guard、command 三层；
- 强化了 command path 语义；
- 真实任务复跑后，瓶颈被重新定位到 synthesis / evidence-grounded planning。

## 已完成切片

### [已完成] LLM 工具超时的可恢复处理

- 背景：某些内部依赖 provider 的工具会超过 executor 的可承受超时窗口。
- 失败现象：工具看起来像普通失败，但本质是 provider timeout，且外层 tool loop 可能过早终止任务。
- 根因判断：timeout 需要被视为可恢复证据，而不是简单终止信号。
- 实现改动：
  - timeout 类失败进入 tool loop 的 recoverable path；
  - 对瞬时 timeout 做一次有界 retry；
  - 对重复 `code_generator` timeout 增加确定性本地 fallback；
  - 支持 `timeout_override`；
  - timeout 证据进入用户可见 summary。
- 验证结果：相关回归主要位于 `/Users/abab/Documents/openpilot/Code/tests/test_execution_tool_planning_executor.py`
- 剩余限制：任务完成判断仍然必须基于 runtime state，而不是无限延长外部预算。

### [已完成] planning surface / deferred disclosure

- 背景：planner 最初看到的是过大的完整工具面。
- 失败现象：prompt 噪声过大，增加模型混乱、延迟与 timeout 风险。
- 根因判断：当前项目采用 `decision_needs -> ToolRouter -> tool execution` 链路，planner 并不需要看到完整工具实现。
- 实现改动：
  - 引入 `/Users/abab/Documents/openpilot/Code/src/autonomous_iteration/planning_surface.py`
  - 引入 `/Users/abab/Documents/openpilot/Code/src/autonomous_iteration/skill_specs.py`
  - 工具能力改为“紧凑 need catalog + capability cards + deferred disclosure”
- 验证结果：首轮 planning prompt 更小、更稳定。
- 剩余限制：synthesis 阶段的空计划问题证明瓶颈已经不是单纯 prompt 过大。

### [已完成] `project_improvement_runtime` 证据集成

- 背景：`project_improvement_runtime` 自身有很多有意义的阶段事件，但此前不在统一 trajectory stream 中。
- 失败现象：其结构化日志和主任务轨迹证据是割裂的。
- 根因判断：项目改进流程没有被纳入统一证据层。
- 实现改动：新增轨迹事件：
  - `pipeline_started`
  - `pipeline_environment_failed`
  - `environment_sync_completed`
  - `environment_repair_attempted`
  - `environment_sync_retried`
  - `pipeline_progress`
  - `project_state_read`
  - `pipeline_finished`
- 验证结果：project improvement runtime 已经能进入同一条 durable task trajectory。
- 剩余限制：还不能自动汇总多次 project-improvement failure 的共同根因。

### [已完成] 分析类任务只读护栏

- 背景：分析型任务必须保持只读，除非用户显式开启 repair task。
- 失败现象：像“梳理 / 分析 / 排查 / 取证”这类任务，仍可能漂移到写文件、patch、删除、bug fix 或 mutating commands。
- 根因判断：只靠 prompt 约束不够，必须下沉到 planner、guard 和 command 三层。
- 实现改动：
  - planner prompt 中加入只读任务说明；
  - `RuntimeGuard` / router 阻断 mutation-capable tools；
  - command 层阻断安装依赖、破坏性文件操作、shell 重定向写入、原地修改等明显 mutation 行为。
- 验证结果：只读分析任务的 mutation 风险显著下降。
- 剩余限制：只读任务仍然需要一种“不修改项目文件但能产生最终答案”的输出路径。

### [已完成] command path 角色化加固

- 背景：命令中的路径之前没有按“角色”拆分。
- 失败现象：executable path、data path、cwd、redirection target 混在一起，导致合法解释器路径也可能被误阻断。
- 根因判断：命令路径治理必须区分不同语义角色。
- 实现改动：明确区分：
  - `command_executable_path`
  - `command_cwd`
  - `command_data_path`
  - `command_redirection_path`
- 验证结果：
  - `/usr/bin/env` 及外部 Python 解释器在 executable 位置不再被误判；
  - 项目数据路径仍然走 project-root grounding；
  - redirection target 成为独立风险类别。
- 剩余限制：还不是完整的 Claude Code 风格命令语义提取器，后续仍可继续细化到 `python` / `pytest` / `cat` / `grep` / `git` 等具体命令。

### [已完成] 真实任务复跑与瓶颈重新定位

- 背景：在证据层、只读约束、planning surface、timeout 与 command path 改进后，重新运行了真实分析任务。
- 任务：

> 请梳理从 CLI 入口到主执行运行时的核心链路，并指出关键模块之间的关系。

- 执行命令：

```bash
cd /Users/abab/Documents/openpilot/Code
PYTHONPATH=src python -m ui.cli run --once "请梳理从 CLI 入口到主执行运行时的核心链路，并指出关键模块之间的关系。"
```

- 结果：任务正常启动、生成 durable trajectory、未被外部 timeout 提前打断，但最终仍然失败。
- 观察到的症状：
  - guessed file target：`setup.py`
  - guessed root：`/openpilot`
  - guessed old layout：`/openpilot/selfdrive/cli.py`
  - synthesis 阶段空 `decision_needs` 或不可路由 `decision_needs`
- 最终失败：

```text
Tool planning requires decomposition after empty decision_needs plan
```

- 结论：瓶颈已经从底层 path / timeout 问题，上移到 **evidence-grounded synthesis reliability**。
- 剩余限制：
  1. 已采集证据还不是唯一可接受目标来源；
  2. planner 更擅长 inspection，不擅长 final answer synthesis；
  3. empty-plan recovery 对分析型 synthesis 任务仍然偏弱。

## 当前遗留问题

- `project_path` 还没有稳定贯穿所有 runtime/planning 入口；
- 空 `decision_needs` 还没有被区分成“规划失败”与“已可总结”两类；
- evidence 仍然更多是“记录下来了”，还没有完全变成硬约束。

## 当日关联文档

- `/Users/abab/Documents/openpilot/docs/task_trajectory/failures/REAL_TASK_FAILURE_ANALYSIS_2026-07-04.md`
- `/Users/abab/Documents/openpilot/docs/task_trajectory/TASK_TRAJECTORY_EVIDENCE_PLAN.md`

---

# 2026-07-07

## 今日摘要（适合汇报）

- 修复了只读分析任务“空 `decision_needs` 被一律视为失败”的问题；
- 打通了 fallback `RuntimeStateMetadata` 对 `project_path` / `cwd` 的继承；
- 增加了只读场景下未 grounding 路径的最小 guard；
- 没有新增重复状态层，仍然复用 `RuntimeStateMetadata`；
- 定向测试 94 通过，全量测试 503 通过。

## 已完成切片

### [已完成] 只读 synthesis、项目上下文传递与最小路径守卫加固

- 背景：真实任务复跑后已经明确，当前瓶颈是只读分析的 synthesis 阶段与证据约束不足。
- 失败现象：
  - 只读仓库分析任务可能已经有足够证据，但 LLM 返回空 `decision_needs`；
  - tool-planning executor 把所有空计划统一视为 planning failure；
  - fallback 创建的 `RuntimeStateMetadata` 不能稳定继承 `project_path` / `cwd`；
  - 在没有 project context 或 prior path evidence 的情况下，系统仍可能继续路由 `setup.py` 这种模型提出的相对路径。
- 根因判断：
  1. “空 `decision_needs`”缺少语义分流：既可能是 planning gap，也可能是“已经有证据，可以直接总结”；
  2. `project_path` / `cwd` 没有稳定贯穿所有 runtime / tool planning 入口；
  3. 只读路径证据虽然被记录了，但还没有足够强地变成 guard。
- 实现改动：
  - 在 `ToolPlanningTaskExecutor` 中增加只读空计划 synthesis 完成逻辑；
  - 当 fallback runtime state 被创建时，从 task context 注入 `project_path` / `cwd`；
  - 在 `RuntimeGuard` 中加入一条最小策略：如果没有 `project_path` / `cwd` 或 prior path evidence，则阻断未 grounding 的只读 `file_read`。
- 实现原则：
  - **没有新增 `RuntimeSessionState`**；
  - 继续复用 `RuntimeStateMetadata` 作为 runtime fact source；
  - 对 mutation/actionable task 仍保持严格失败语义，不做过宽放行。
- 验证覆盖：
  - 只读分析 + 已有 runtime evidence + 空 `decision_needs` => 进入 synthesis，而不是失败；
  - 只读分析 + 无证据 + 空 `decision_needs` => 仍失败；
  - fallback `RuntimeStateMetadata` 继承 `project_path` 并记录为 fact / candidate；
  - 只读 `file_read` 在无 `project_path` 或 prior path evidence 时被阻断。
- 定向测试：

```text
PYTHONPATH=Code/src pytest -q \
  Code/tests/test_execution_tool_planning_executor.py \
  Code/tests/test_agent_runtime_controller.py \
  Code/tests/test_project_path_runtime_integration.py \
  Code/tests/test_path_boundary_validation.py
```

- 定向结果：

```text
94 passed
```

- 全量回归：

```text
PYTHONPATH=Code/src pytest -q Code/tests
503 passed
```

- 这次明确解决了什么：
  - 只读空计划不再一律报 `Tool planning requires decomposition after empty decision_needs plan`；
  - fallback runtime state 不再轻易丢失项目上下文；
  - 只读场景下裸相对路径读取至少有了一层最小阻断。
- 剩余限制：
  - `runtime_mode` 仍然通过 `runtime_mode:read_only_analysis` assumption marker 表达，不是一等 metadata field；
  - 当前 guard 仍是最小切片，还没有扩展成“所有 read tool / 所有 workflow mode”的 evidence-backed path policy；
  - `read_only_repository_analysis` 还没有正式进入 `TaskRouteMetadata` 作为独立 route；
  - 当前 trajectory data 仍不适合作 BERT / SVM classifier 的训练标签。

## 当前遗留问题

- route contract 仍然太粗，绝大多数任务仍直接落入 `autonomous_iteration`；
- `runtime_mode` 语义还没有做成更稳的一等字段；
- evidence 记录与 evidence 强约束之间仍有差距；
- BTX / 二级行为路由还没有进入实现阶段。

## 下一步计划

1. 把 `read_only_repository_analysis` 正式纳入 `TaskRouteMetadata`；
2. 扩展 path grounding guard 到 `multi_file_reader` 及更多 read tool；
3. 评估是否把 `runtime_mode` 升级成一等 metadata 字段；
4. 在更稳定的成功轨迹基础上，再考虑 classifier / BTX router 的训练数据问题。

## 当日关联文档

- `/Users/abab/Documents/openpilot/docs/task_trajectory/failures/REAL_TASK_FAILURE_ANALYSIS_2026-07-04.md`
- `/Users/abab/Documents/openpilot/Thought.md`
- `/Users/abab/Documents/openpilot/THOUGHT_ARCHITECTURE.md`

---

# 2026-08-02

## [已完成] 断点恢复 Phase 0–1：协议边界与原子检查点存储

- 观察到的失败：运行时只维护进程内 `RuntimeStateMetadata`；进程退出后会创建新 session 和新 state，轨迹事件也无法证明某个工具结果已经应用或预算已经计费。
- 验证证据：新增测试先确认项目不存在 checkpoint contract/store；随后覆盖 state 与 budget round-trip、严格项目指纹、不可变 generation、stale writer、SHA-256 完整性校验、损坏 latest 回退和敏感字段拒绝。
- 实现修复：
  - 新增严格的 `RuntimeCheckpointMetadata` 和 `ProjectFingerprint`；
  - 新增带进程锁、临时文件、fsync、原子替换和 generation 检查的 `RuntimeCheckpointStore`；
  - 检查点文件与 latest 指针分离，最新代损坏时可回退到上一有效代；
  - 拒绝把 API key、access token、password、private key 等敏感字段写入检查点；
  - 补齐 loop、supervisor、goal 和 session resume 的协议入口。
- 定向验证：`31 passed`。
- 全量验证：`518 passed`。
- 剩余限制：当前只是可靠的检查点契约和存储；controller 尚未写入安全边界，也没有开放 resume 执行入口，不能据此宣称任务已经支持断点恢复。

## [已完成] 断点恢复 Phase 2–3：只读安全边界与显式恢复

- 观察到的失败：即使检查点可以可靠保存，controller 仍会创建新 state/session，且没有可解释的恢复预检或预算继承入口。
- 验证证据：覆盖任务初始化、只读结果应用、受控停止、checkpoint 写入降级、原身份恢复、预算继承、已完成任务不重执行、错误 root task、恢复预算耗尽和缺失 stage cursor。
- 实现修复：
  - 只在显式 `checkpointing_enabled` 下写入只读安全边界；
  - checkpoint 成功后才镜像 `checkpoint_created` 轨迹，失败记录 `checkpoint_write_failed` 且不终止主任务；
  - 新增 `RuntimeResumeDecisionMetadata` 与显式 `resume(run_id, checkpoint_id, context)`；
  - 恢复保留 run/root task/session 和已消费预算，并为恢复尝试建立独立 ID；
  - 已完成 checkpoint 直接返回，不重新执行；不匹配、预算耗尽、indeterminate action 和缺失 session stage cursor 均 fail closed。
- 定向验证：`94 passed`。
- 全量验证：`525 passed`。
- 剩余限制：当前只有 `task_normalized` 和已完成 `controlled_stop` 能自动恢复。`tool_result_applied` 虽可持久化，但 session executor 尚无内部 stage cursor，因此明确阻塞；文件副作用对账和命令恢复尚未实现。

## [已完成] 断点恢复 Phase 4–6：文件副作用对账、命令默认阻塞与跨进程验收

- 观察到的失败：进程可能在文件写入后、tool result 落盘前退出；若简单重跑会重复 mutation，若直接继续又可能漏计预算或跳过验证。
- 验证证据：故障注入覆盖 prepared 后未执行、写入后未 observed、observed 后未 applied、applied 后未 verification、外部文件漂移、prepared checkpoint 写失败、外部命令 checkpoint，以及真实子进程 `os._exit(23)`。
- 实现修复：
  - tool loop 在 mutation 执行前强制 durable `prepared`；失败则不启动工具；
  - 工具返回后先保存 `observed`，再由 `StateUpdater` 保存 `applied` 并计费；
  - 文件 checkpoint 保存 typed tool input、调用 ID、执行前/执行后/预期内容哈希和最小 observed result；
  - 恢复时区分“尚未写”“已经写成预期内容”“用户/外部漂移”，分别单次执行、跳过写入继续 apply/verify、或阻塞；
  - 验证结果保存 `verification_applied`；
  - mutating command 保存 prepared/result 边界，但没有探针时不自动重放；
  - CLI 增加显式 checkpointing 和 `run_id + checkpoint_id + project_path` resume 参数。
- 验收结果：全量 `538 passed`，`compileall` 和 `git diff --check` 通过；损坏 latest 指针后仍能从不可变 checkpoint 扫描出当前 generation 并安全写入下一代；新进程 recorder 能按 run ID 重新打开原轨迹并从已有最大 event sequence 继续编号。
- 剩余限制：任意 session 内部 read-stage 仍缺通用 stage cursor；file patch 在无法计算预期内容哈希且目标已变化时安全阻塞；网络写和通用命令没有自动恢复；当前不是分布式事务系统。

## [已完成] 根任务/子任务状态隔离与证据化完成

- 观察到的失败：真实 coding run `6e76e501a419417abb4f6d712ea5a827`
  中，首个 inspect 子任务把共享 runtime 永久降为只读；后续写入 need 被 Guard
  过滤，已成功的 file read 又让 implement 被错误标为完成；与此同时
  `changed_files` 从计划字段推导出了实际未修改的文件。
- 验证证据：确定性回归覆盖 inspect 不污染根权限、read 成功不能掩盖 blocked
  write、无 mutation/command 证据不能完成、旧 assumption 迁移、observed-only
  changed-files、Guard typed trajectory，以及 validation 写入越界阻断。
- 实现修复：
  - `RuntimeStateMetadata` 增加 typed `execution_mode`、source、reason 与
    `guard_history`，旧 read-only marker 仅作为历史输入迁移；
  - 子任务 tag/kind 不再改写共享根权限，inspect/validate 的 mutation need 和超出
    `Task.write_files` 的目标被显式拒绝；
  - 必需 need 的 Guard 拒绝不再表现为空 selection，而是失败并记录
    `decision_need_blocked`；完成证据不足记录 `task_completion_rejected`；
  - implement/write 必须观察到成功文件副作用，validate 必须执行验证工具；
    `ExecutionStateMetadata.changed_files` 和 written-files 汇总只消费 observed evidence；
  - symbol 修改只有 code generation、缺少持久化动作时，合成一个仍受 Guard 与
    写入范围控制的 patch writer；
  - 修复 fallback cwd 选择，并限制 README 自动收尾只用于明确的新项目创建意图，
    避免 bugfix 任务修改范围外文件。
- 真实任务验收：隔离 calculator run `5903f1ea81da4a8dae92b1d2fd09aa78`
  实际只修改 `calculator.py`，把除零改为抛出包含 `zero` 的 `ValueError`；pytest
  结果为 `2 passed`，测试文件与 README 哈希不变。轨迹包含 read、code editor、
  file patch、write verification 和 validation command；最终 checkpoint 为
  `controlled_stop`，execution mode 为 `mutation_allowed`，modified/changed files
  均只有 `calculator.py`。
- 自动化验收：相关定向测试 `152 passed`；全量测试 `553 passed`；随后执行
  `compileall`、`git diff --check` 与计划/契约一致性检查。
- 剩余限制：默认 mutation mode 仍依赖根入口分类准确性；通用命令和网络写仍遵循
  既有 fail-closed/无自动恢复边界；不同模型可能生成其他合法计划，因此真实验收
  以权限、实际副作用和验证不变量为准，不绑定固定调用次数。

## [已完成] 跨进程恢复保留原验证计划与原 run

- 观察到的失败：真实模型任务在文件写入后被 `SIGKILL`，恢复虽能通过哈希对账避免
  重写文件，却丢失原计划中的 pytest，退化为 `python calculator.py --help`；同时新
  `DiagnosticRecorder` 只有进程内 task/session alias，恢复事件被写入第二个 run，
  原 run 永久停留在 `running`。
- 验证证据：先增加失败测试，分别复现 checkpoint 拒绝 pending verification、tool
  loop 不暴露后续 command、全新 recorder 创建第二个 run；随后覆盖 applied 与
  verification-applied 崩溃边界、原命令消费和 existing-run 事件续写。
- 实现修复：
  - `RuntimeCheckpointMetadata` 新增 typed `pending_verification`；
  - tool loop 在 mutation 执行前把同一计划中后续 command 转成
    `VerificationPlanMetadata`，随 prepared/observed/applied checkpoint 持久化；
  - 恢复优先执行持久化命令，成功后清空 pending plan；较弱的通用验证不能提前完成；
  - recorder 新增严格 existing-run attach，验证 run/task/session 后恢复进程才写事件。
- 自动化验收：相关测试 `149 passed`，全量测试 `557 passed`。
- 真实端到端：run `e2b689d1aee94da0b748badc96e634da` 使用真实
  `deepseek-v4-flash`；mutation `tool_result_observed` 后进程被 `SIGKILL`（137），
  第二个 CLI 进程执行 checkpoint 中原命令
  `python -m pytest .../test_calculator.py` 并成功。事件 sequence 从 32 连续到 40，
  没有第二个 run；writer 调用 1 次，file edit 计费 1 次，recovery 计费 1 次，
  最终 `2 passed`，测试文件和 README 哈希不变。
- 剩余限制：当前 durable pending plan 聚焦文件 mutation 后的单个 required
  validation；通用多步 session cursor、网络写和不可对账命令仍不自动恢复。

## [已完成] 恢复状态 Metadata 化与不可恢复兜底

- 观察到的失败：恢复预检虽然已有 `RuntimeResumeDecisionMetadata`，但 `blocked` 同时
  表示预算等待、身份错误、人工对账和永久不可恢复；controller/CLI 仍暴露零散
  `resume_status`、`reason` 和 `next_action` 字符串。请求的 checkpoint 缺失或损坏时直接
  抛异常，无法回答当前 run 是否可恢复，也没有结构化 fallback。
- Metadata impact：复用并扩展 `RuntimeStateMetadata`、
  `RuntimeResumeDecisionMetadata` 和 `RuntimeReportMetadata`；复用现有 checkpoint、budget、
  verification、failure、identity 和 evidence 字段；`RecoveryBlocker` 与
  `RecoveryFallback` 是 decision 内严格嵌套值，不新增 `MetadataKind`，也不引入新的关系层。
- 验证证据：先增加失败测试覆盖 typed round-trip、非法组合、旧 payload 保守迁移、
  explanation 文本变化、exact/already-complete/wrong-task/budget/missing-cursor/project-drift/
  external-command 边界、文件对账失败、损坏代回退、完全缺失 checkpoint、CLI 和 trajectory
  消费。
- 实现修复：
  - 增加 `RecoveryStatus`、`Recoverability`、`RecoveryMode`、
    `RecoveryAutomationPolicy`、`RecoveryReasonCode`、`RecoveryFallbackAction`、
    `VerificationStatus` 和 `CheckpointStatus`；
  - runtime state 持有当前恢复状态；单次 resume decision 持有可恢复性、模式、权限、
    reason code、blockers、fallback 和可选下一 checkpoint；report/trajectory/CLI 只投影；
  - validator 拒绝不可恢复却 exact resume、等待用户却自动执行以及新旧字段矛盾；旧
    decision payload 不按解释文本猜测，只保守映射；
  - 损坏 checkpoint 若存在上一有效代，返回 `recoverable_after_action +
    use_previous_valid_checkpoint`；无有效代时返回 `not_recoverable +
    terminate_preserving_evidence`，不再抛无状态异常或静默创建新 run；
  - CLI 根据 typed recoverability/fallback 展示，`reason`/`instructions` 仅用于说明。
- 自动化验收：恢复/metadata/CLI/trajectory 定向测试 `117 passed`；`Code` 全量
  `569 passed`；`compileall` 与 `git diff --check` 通过。
- 剩余限制：本切片没有持久化独立 Recovery Bundle artifact，也没有执行 linked-new-run
  handoff；通用多步 stage cursor、网络写和无对账探针的命令仍不自动恢复。旧
  `decision`/`resume_status` 暂留兼容输出，新增控制流不得消费它们。

### 同轮真实验收发现并补强：活跃 run 单写者边界

- 真实证据：真实 `deepseek-v4-flash` 只读 run
  `ccc10143f7cd440aafb2d9988640ea38` 在终端控制返回后仍有进程继续运行；此时从新进程
  resume，typed preflight 正确附着原 run，但两个 writer 产生重复 event sequence
  `11–14`，checkpoint generation 竞争被 store 拒绝。该 run 因此只作为失败证据，不能
  记为成功验收。
- 根因：checkpoint store 已有 generation/file lock，但 run 生命周期没有单写者 lease；
  recorder 的 `_event_sequences` 又是进程内缓存，两个 recorder 实例会各自分配同一序号。
- 实现修复：checkpointed run 在执行期持有 `.runtime.lease` 非阻塞文件锁；活跃 lease 下
  resume 返回 `waiting_retry/run_lease_active/retry_later`，不执行、不写原轨迹；trajectory
  append 增加 per-run `.events.lock`，持锁读取 durable 最大 sequence 后再追加。
- 更新后验收：新增 lease 排他、活跃 run 无写入、双 recorder 严格递增测试；恢复相关
  定向测试更新为 `117 passed`（包含真实子进程 lease 排他），`Code` 全量更新为
  `569 passed`。

## [已完成] Goal R1 / P0-A：恢复边界注册与确定性 checkpoint 故障注入

- 观察到的失败：checkpoint 的 `safe_boundary` 虽由 Literal 限制，controller 仍散落同名
  字符串；测试无法通过一个统一入口稳定模拟 durable write 前后进程退出。
- Metadata impact：复用 `RuntimeCheckpointMetadata.safe_boundary`，用
  `CheckpointBoundary` 替代重复 Literal；增加 runtime supporting enum
  `CheckpointFaultPoint`，不新增 `MetadataKind`，注入回调不序列化。
- 验证证据：先写失败测试验证未登记边界拒绝，以及 write-before 不落盘、write-after 已
  落盘；随后将 controller 的持久边界调用与恢复判断迁移到枚举。恢复/metadata 定向测试
  `90 passed`，`Code` 全量 `572 passed`。
- 剩余限制：本切片只冻结 checkpoint 持久边界和注入位置；session 内 route、decomposition、
  subtask、LLM/read apply 和多步验证仍缺 durable cursor，由 P0-B 至 P0-D 处理。

## [已完成待 Goal 总验收] Goal R1 / P0-B：Durable session execution cursor

- 观察到的失败：checkpoint 能恢复 runtime state 和文件动作，但 `tool_result_applied` 后缺少
  monolithic session 的原 decomposition、已完成 subtask 和下一位置，只能阻塞或重跑整段。
- Metadata impact：扩展 `RuntimeCheckpointMetadata`，增加 owned
  `SessionExecutionCursor`、`SessionSemanticSnapshot`、`SessionTaskResult`；任务计划复用
  `TaskGraphNodeMetadata` 并补充 priority、effort、tags 和 typed problem-resolution 字段，
  不复制自由 `Task.attributes`，不新增 `MetadataKind`。
- 实现修复：decomposition 落盘后记录 plan hash；每个 subtask 结果应用后推进连续 cursor；
  恢复校验 plan hash、mode、结果连续性，再重建任务状态并只执行 `next_task_index` 之后的
  工作；已完成结果以严格 summary/path evidence 提供给后续任务。
- 验证证据：cursor JSON round-trip、非法结果缺口、剩余 subtask 单次执行、controller typed
  preflight/传递 cursor 和旧路径回归通过；`Code` 全量 `576 passed`。
- 剩余限制：LLM/read response 的 subtask 内 apply-once 与多 tool/验证 cursor 尚未完成；
  跨进程 kill 和真实模型任务在 Goal R1 总验收统一执行，因此本项不单独宣称 Goal 完成。

## [已完成] Goal R1 / P0-C–P0-D：多步 session apply-once 与有界验证恢复

- 观察到的失败：session 只能恢复文件 mutation，不能证明 decomposition 后下一 subtask；
  LLM/read observed result 没有 durable artifact/apply marker；多条验证只保留第一条。真实 CLI
  验收又暴露 enhanced-UI 不发 cursor、相对 `Task.write_files` 未按 project root 解释、resume
  未重绑 project context、从旧 checkpoint 重试发生 generation conflict，以及 task-local
  `no_progress` 阻塞泄漏到下一 subtask。
- Metadata impact：继续扩展现有 `RuntimeCheckpointMetadata` 和
  `VerificationPlanMetadata` 所有权；新增 strict nested `SessionBootstrapCursor`、
  `SessionExecutionCursor`、`PendingLLMRequest`、`LLMReplayEntry`、
  `ReadToolReplayEntry`、`DurableArtifactReference`、`VerificationCommandSpec`，不新增
  `MetadataKind`。`resume_source_checkpoint_id` 记录旧代重试的不可变来源，generation 仍在
  当前 run tip 后单调追加。
- 实现修复：standard/enhanced-UI 在 decomposition 与每个 subtask 后保存 plan hash、连续
  result prefix 和 next index；LLM/read 结果先写 checksum artifact 再 apply，匹配 hash/
  ordinal/call ID 时只重放不再调用；验证计划逐命令持久化 cwd/mode/timeout 和连续完成前缀；
  relative write scope、resume context、CLI improvement options、旧 checkpoint generation
  lineage 与 task-local no-progress 生命周期均在真实路径补齐。
- 跨进程证据：确定性子进程在第一条验证完成后 `os._exit(23)`，新进程只执行第二条，验证
  budget 从 1 延续到 2；旧 checkpoint 重试回归证明新 generation 接在 current latest 后，
  且每代记录 source checkpoint。
- 自动化验收：恢复相关定向套件 `140 passed`；最终 `Code` 全量 `593 passed`，并通过
  `compileall` 与 `git diff --check`。
- 真实端到端：run `38df878196194c4287890b840a054050` 使用真实
  `deepseek-v4-flash`。原进程完成 inspect subtask 后，在 generation 20
  `llm_request_prepared` 被 `SIGKILL`；新 CLI 进程从 checkpoint
  `fabd54ea16724409ac8e1b3fbfa865e7` 明确恢复 `task 2/3`，没有重跑 task 1。最终只编辑
  `calculator.py` 一次，`pytest` 为 `2 passed`，`compileall` 成功，cursor 为
  `tasks_executed/3`，三项 result 均 completed；budget 为 file edit 1、recovery 1、
  verification 2，pending verification 已清空，最终 generation 42 的 source checkpoint
  lineage 可查。
- 真实验收事故与修复：一次修复前的 resume 因 project context 未成为 finalization 的优先
  project root，错误进入主仓库改进流程并创建 safety snapshot commit `afb5b0d`。为避免覆盖
  用户已有工作，本轮没有 reset/rewrite 该提交；随后让恢复入口重绑 execution context、让
  project inference 优先显式 restored `project_path`，并让 resume CLI 正确继承
  `--improvement-iterations 0`。最终成功 run 使用独立临时项目且未再次触发该问题。
- 剩余限制：provider request 已发送但响应完全丢失且 provider 无查询能力时仍只能按 bounded
  policy 处理；通用 command/package install/network/external write 没有因此开放；finalization、
  Recovery Bundle、linked run 与 supervisor 自动调度属于 Goal R2/P2，不在本轮实现。

## [已完成] Goal R2 / P1-A：可恢复且幂等的运行终结边界

- 观察到的失败：session 已返回完成后，旧流程先写 `controlled_stop`，再派生 report 并追加
  `task_finished`。进程在两者之间退出时，resume 会把 state 当作已经完成直接返回，导致 run
  长期保持 `running`；在事件写入附近重试又缺少跨进程幂等身份。
- Metadata impact：扩展现有 `RuntimeCheckpointMetadata`，增加 owned
  `RuntimeFinalizationCursor` 及三阶段 enum；扩展 `CheckpointBoundary`、`RecoveryMode`、
  `RecoveryReasonCode` 和 `RuntimeReportMetadata.state_hash`。报告继续是派生 checksum artifact，
  run/event 继续是投影；没有新增 `MetadataKind` 或第二份任务事实。
- 实现修复：controller 依次持久化 `runtime_state_completed`、report artifact、
  `runtime_report_persisted`、幂等完成事件和 `runtime_finalized`。resume 的
  `finalize_from_checkpoint` 分支不进入 session executor。Recorder 在 per-run 锁内按
  `task_finished:<finalization_id>` 去重，并在复用事件时补做 run projection。
- 验证证据：Metadata 非法阶段组合与 recorder 跨实例幂等测试通过；四个终结窗口故障注入
  均恢复为一个 artifact/一个完成事件；真实子进程在 report checkpoint 后 `os._exit(91)`，
  新进程成功附着原 run 并只完成终结。相关完整回归 `122 passed`。
- 剩余限制：旧 `controlled_stop` 可读取但没有新 cursor，不能反向声称 exactly-once；完全未
  建立 durable boundary 的存储故障维持 best-effort 完成并显式标记 unavailable。Recovery
  Bundle、linked-run handoff 和 supervisor bounded retry 仍属于 P1-B/P1-C。

## [已完成] C0-A：Prompt 上下文与 checkpoint 衔接

- 观察到的失败：`ContextSelectionMetadata` 能解释固定字符预算和对话后缀，但只随
  `pipeline_progress` 进入轨迹。进程恢复会重新读取当前 ShortMemory、MemoryStore 和项目索引；
  来源变化后，相同 session 可能得到不同 Prompt，旧 checkpoint 也无法证明模型输入。
- Metadata impact：复用 `ContextSelectionMetadata`、`DurableArtifactReference` 和
  `RuntimeCheckpointMetadata`，增加 checkpoint-owned strict nested
  `RuntimePromptContextSnapshot` 与 `context_assembled` boundary；不新增 `MetadataKind`，
  不把 `ProjectStateMetadata.memory_context` 扩成第二份权威记忆。
- 实现修复：builder 对完整构建参数生成稳定 request hash；controller 将已选 payload 保存为
  checksum `prompt_context` artifact，并绑定 Prompt hash、selection 和 session cursor/bootstrap。
  resume 只对完全匹配的 request 回放 artifact；checksum、Prompt hash 或 selection 不一致时
  fail closed，不从变化后的来源静默重建。
- 验证证据：strict contract、builder 原样 replay、controller resume 和 corrupt artifact 测试
  通过；真实子进程在 `context_assembled` 后 `os._exit(92)`，替换进程使用已变化的对话源，
  仍得到中断前完全相同的 Prompt。上下文/Metadata/checkpoint/controller/diagnostics 定向回归
  `148 passed`，`Code` 全量 `605 passed`。
- 后续状态：字符预算限制已由 C0-B 的 provider tokenizer 切片解决。当前衔接覆盖生产
  `MemoryContextBuilder`，其他独立 Prompt 构造器尚未统一；artifact 保存的是已选输入，省略的
  候选仍由原 memory/project store 拥有。

## [已完成] C0-B：Provider-aware 真实 Token 预算

- 观察到的失败：生产 builder 只用字符上限，未接入的 `ContextCompressor` 仍用
  `chars/4`；中文、英文和代码比例变化时无法证明模型输入没有超过 Token 预算。配置又依赖
  启动 cwd，可能让 tokenizer 绑定模型与实际 LLM 配置不同。
- Metadata impact：扩展现有 `ContextSelectionMetadata`，增加 budget unit、token 上限、
  裁剪前后 token、计数方法、tokenizer ID 和模型；复用 `ToolInputMetadata.max_tokens`，不新增
  `MetadataKind`。该计数只拥有上下文 slice，完整请求实际用量仍由 `LLMResponse.usage` 权威记录。
- 实现修复：新增 provider-aware `ProviderTokenCounter` 和显式 DeepSeek 官方 tokenizer 安装器；
  tokenizer 可用时 token-first、字符 ceiling 同时生效，不可用时明确回退字符预算。配置同时搜索
  repository root、`Code/` 和 cwd 的 `.env`，生产 builder 直接绑定 `llm_client.settings`。
- 验证证据：真实当前配置 `deepseek-v4-flash` 使用官方 tokenizer，将 10,814-token 中英文混合
  输入裁剪到 511/512；exact counter、missing-tokenizer fallback、Metadata、checkpoint replay
  和相关恢复回归通过。轻量 LLM adapter 不提供 `settings` 时仍初始化上下文 builder，并明确
  降级为 character budget；`Code` 全量 `610 passed`。
- 剩余限制：当前精确预算只覆盖 `MemoryContextBuilder` 输出片段，并非 ChatCompletion 包装后的
  全请求；其他 Prompt 入口需要逐步统一，provider 返回 usage 才是最终计费真值。

## [已完成] C0-C：统一上下文装配内核（第一阶段）

- 观察到的边界：来源收集、Prompt 渲染、预算、选择、截断、Metadata 生成和 request hash 原本
  全部集中在 `MemoryContextBuilder`，其他 Prompt 入口无法复用已验证的预算和恢复语义。
- Metadata impact：完整复查 `ContextSelectionMetadata`、`ContextSectionDecision`、
  `RuntimePromptContextSnapshot`、checkpoint 和 provider usage；决定复用现有契约，不新增字段、
  `MetadataKind` 或持久化状态。装配结果仍是来源事实的派生视图。
- 实现修复：新增 `memory.context_assembly.ContextAssembler`，统一拥有 request fingerprint、字符/
  token 双边界、确定性优先级选择、截断和选择证据。`MemoryContextBuilder` 仅保留 memory/project/
  environment 来源收集、专用渲染和 controller-owned checkpoint handler，并委托装配内核。
- 验证证据：直接装配、不可变来源、确定性决策、真实 token、Memory builder、checkpoint、恢复控制器
  与迭代流水线定向回归 `117 passed`；当前 `deepseek-v4-flash` 将 8,740-token 混合输入裁剪至
  512/512；`Code` 全量 `613 passed`。
- 剩余限制：第一阶段只建立并接入统一内核，任务分解、规划、代码生成、修复和评估 Prompt 尚未
  迁移；迁移前需要逐入口识别其候选来源与强制保留语义，不能把业务 Prompt 模板塞进装配模块。

## [已完成] C1-A：类型化上下文候选与装配结果

- 观察到的边界：通用装配内核已经统一预算算法，但跨模块调用仍缺少类型化候选、保留级别、
  截断策略和必需候选不足状态；若直接迁移业务入口，只能再次依赖自由字典和隐式优先级。
- Metadata impact：复用并扩展 `ContextSelectionMetadata`，新增 owned strict nested
  `ContextCandidate`、`ContextAssemblyPolicy`、`ContextCandidateDecision` 和
  `ContextAssemblyResult`；不新增 `MetadataKind`，所有新增字段提供历史默认值。
- 实现修复：`ContextAssembler.assemble_candidates` 按 required/preferred/optional、显式优先级和
  source order 确定性选择，支持 forbidden/head/tail 截断。必需候选无法容纳时产生类型化
  `budget_insufficient` 和 omitted-required IDs，来源值不被修改。
- 验证证据：直接契约与 typed assembly `42 passed`；Metadata、memory、checkpoint、controller、
  pipeline 定向 `156 passed`；`Code` 全量 `617 passed`。
- 剩余限制：完整请求固定开销尚未预留，22 种生产请求用途尚未迁移；进入阶段 2 前需先写
  full-request budget 阶段计划。

## [已完成] C1-B：完整请求内容预算与 provider 前置门禁

- 观察到的边界：上下文候选虽已类型化，但业务消息的固定指令和 framing 安全余量还没有进入同一
  请求预算；装配证据也未随 `LLMRequest` 进入 diagnostics 和 replay identity。
- Metadata impact：扩展现有 `ContextAssemblyPolicy`、`ContextSelectionMetadata`、
  `LLMRequestMetadata` 和核心 `LLMRequest` 的可选证据；不新增 `MetadataKind`，provider usage 仍是
  完整序列化请求和计费的唯一事后真值。
- 实现修复：新增 `ContextRequestBuilder`，保留候选 message role，并记录 requested、reserved、
  effective、final 和 remaining token。`LLMClient` 在 cache/transport 前按 typed assembly status
  拒绝必需候选不足；diagnostics 和 replay hash 携带同一 selection 对象。
- 验证证据：预算、request builder、缺 tokenizer 降级、client guard、diagnostics、request hash、
  checkpoint 和历史兼容定向 `273 passed`；`Code` 全量 `624 passed`。
- 剩余限制：22 种生产请求用途仍需按业务所有权分批迁移；provider 私有 chat framing 由显式 reserve
  覆盖，不冒充精确计费 token。

## [已完成] C1-C：编排与控制 Prompt 入口迁移（阶段 3A）

- 观察到的边界：semantic、decomposition、tool planning、iteration goal/task、project improvement
  和 runtime-output evaluation 共十种用途仍直接构造请求，可绕过统一预算与 purpose 证据。
- Metadata impact：新增 owned enum `ContextRequestPurpose` 并由现有 policy/selection 携带；不新增
  `MetadataKind`，不改变各业务结果契约。静态 registry 覆盖 phase-0 的全部 22 种用途。
- 实现修复：新增现有消息到 typed candidate 的共享适配器，system instruction 必需且禁止截断，
  user/assistant 使用显式截断方向；十种 3A owner 全部经 `build_context_llm_request` 提交。
- 验证证据：静态入口覆盖和 semantic runtime `10 passed`；owner parser、fallback、planning、
  iteration、evaluator 定向 `240 passed`；`Code` 全量 `637 passed`。
- 剩余限制：写文件相关的生成/编辑/bugfix 需要按证据位置设计候选，不能直接沿用普通 user head
  truncation；进入 3B 前先写独立计划。

## [已完成] C1-D：生成、编辑与 Bugfix Prompt 入口迁移（阶段 3B）

- 观察到的边界：code generator、text replacement、code unit、code editor 和 bugfix 的输出会进入
  后续文件写入；若普通 head/tail 裁剪破坏完整 scope，模型仍可能产生表面合法但越界的修改。
- Metadata impact：复用 `ContextRequestPurpose`、candidate retention/truncation、selection 和既有
  tool/bugfix/edit evidence；不新增 Metadata 或扩大工具权限。
- 实现修复：五种 owner 全部经共享 request adapter；完整 scope/message 标为 required + forbidden
  truncation，预算不足在 provider/write 之前产生 typed budget failure。兼容客户端接收已装配内容。
- 验证证据：静态与 runtime entry `16 passed`；tool IO、generation、bugfix、iteration、permission
  定向 `169 passed`；`Code` 全量 `643 passed`。
- 剩余限制：research/summary/compressor/slot generation 尚待 3C；其中摘要类允许显式裁剪但不得
  递归调用自身来解决预算。

## [已完成] C1-E：Transform、Research 与 Slot Prompt 入口迁移（阶段 3C）

- 观察到的边界：memory compressor、summarizer、web query/link/cleanup 与 slot generate/repair
  仍是最后七种绕过统一 request boundary 的用途。
- Metadata impact：全部复用 `ContextRequestPurpose`、selection 和现有 Search/Text/Tool/Slot owner；
  无新增 schema。摘要类的有损输入允许显式 head truncation，不引入递归压缩。
- 实现修复：七种用途全部经共享适配器；22 种 registry 用途已全部迁移，生产源码的 executable
  `LLMRequest(...)` 仅由 request builder 构造。diagnostics proxy 同时补齐 minimal client 的签名和
  response 兼容，不重复 provider 调用。
- 验证证据：静态 purpose coverage `24 passed`；transform、web、slot、memory、tool IO、diagnostics
  定向 `191 passed`；`Code` 全量 `651 passed`。
- 剩余限制：进入阶段 4 验证 typed selection 的 checkpoint/replay、进程恢复和真实 provider usage；
  未完成这些验收前不结束上下文装配 Goal。

## [已完成] C1-F：统一上下文装配恢复与真实 Provider 验收（阶段 4）

- 观察到的边界：入口全部迁移后，仍需证明 selection evidence 参与 request identity、durable response
  replay 不产生第二次调用、上下文 artifact 在真实进程退出后可回放，并核对真实 provider usage。
- Metadata impact：复用 `ContextSelectionMetadata`、`RuntimePromptContextSnapshot`、
  `PendingLLMRequest`、`LLMReplayEntry` 和 `LLMResponseMetadata`，不新增契约或事实副本。
- 验证证据：typed replay hash、zero-call durable replay、artifact checksum 阻断、`os._exit(92)` 跨进程
  恢复共 `5 passed`；context/Metadata/recovery 验收组 `197 passed`。真实 `deepseek-v4-flash` 请求
  返回预期文本，local content=14 tokens、reserve=128，provider usage=97 prompt/25 completion/122 total，
  `finish_reason=stop`。静态 inventory 22/22、唯一 request constructor；`Code` 全量 `651 passed`。
- 剩余限制：provider 私有 chat framing 只能用显式 reserve 覆盖；多数 owner 当前以 message 粒度
  暴露候选；usage 尚不自动反馈调参。这些限制均不破坏 typed budget、审计或恢复边界。

## [已完成] C2-A：上下文正确性边界（阶段 5A）

- 观察到的失败：legacy memory adapter 会把 1,400 字符固定 system instruction 缩成 239 字符并
  标记 ready；semantic JSON user message 会被字符级 head truncation 切成非法 JSON 后仍提交；
  iteration owner 又用 broad exception 把 typed budget failure 隐藏成普通 `None` fallback。
- Metadata impact：完整复查 candidate/policy/decision/selection/result、LLM request/failure 和 prompt
  checkpoint；复用既有 `ContextAssemblyBudgetError` 与 selection evidence，不新增字段、
  `MetadataKind` 或第二份 budget 状态。
- 实现修复：legacy memory adapter 先以 required + forbidden typed candidate 预检固定指令；semantic
  goal/plan-step 与 iteration goal/task-design 在当前 message 粒度下禁止原始截断；iteration 对
  `ContextAssemblyBudgetError` 单独传播，其他既有 provider/parse fallback 不变。
- 验证证据：三个新回归在修复前稳定失败、修复后通过；相关 context/semantic/iteration/checkpoint
  回归 `172 passed`；`Code` 全量 `654 passed`，compileall 与 `git diff --check` 通过。
- 剩余限制：memory/project/dialog/environment 仍经 legacy section adapter；phase 5B 已先写计划，
  将迁移到 per-source typed candidates，同时保留兼容 payload 与 checkpoint replay。

## [已完成] C2-B：Memory 上下文 typed source adapter（阶段 5B）

- 观察到的失败：统一装配内核和 typed candidate 契约已经存在，但生产
  `MemoryContextBuilder` 仍把 system/dialog/file/memory/environment 合成 section dict 后走
  legacy `assemble(payload)`；因此无法追溯每个来源条目的保留/截断/省略，且新旧 adapter
  的 checkpoint request hash 可能相同。
- Metadata impact：复查 `ContextCandidate`、`ContextAssemblyPolicy`、
  `ContextCandidateDecision`、`ContextSelectionMetadata`、`ContextAssemblyResult`、
  `RuntimePromptContextSnapshot` 与 artifact reference；复用现有 owned nested value，不新增
  `MetadataKind`、来源事实副本或关系层。candidate decision 为选择权威，旧 section decision
  仅作为派生兼容视图。
- 实现修复：instruction、每条 dialog、related file、memory 和 environment observation 分别适配为
  稳定 candidate/source ID；统一由 `assemble_candidates` 执行 retention/priority/budget 选择；
  固定 instruction required + forbidden；dialog 以递增优先级保留连续 recent suffix；按原 section
  顺序渲染，并从选择结果恢复旧 payload。request fingerprint 加入 typed adapter version，snapshot
  仍在读取变化来源前精确回放。
- 验证证据：四个 phase 测试在修复前分别暴露 legacy strategy/call、缺失 dialog omission 和 hash
  碰撞，修复后通过；context/iteration/dashboard/metadata/checkpoint/recovery 回归 `138 passed`；
  `Code/tests` 全量 `658 passed`。仓库根 pytest 会额外收集未配置 import path 的独立 experiments，
  因此项目主套件继续以 `Code/tests` 为验收范围。
- 剩余限制：来源仍缺少 typed trust/freshness/conflict/dedup 治理；phase 5C 必须先写计划和
  metadata impact note，再决定最小契约扩展。

## [已完成] C2-C：Typed source governance（阶段 5C）

- 观察到的失败：candidate decision 只有 `within_budget` / `prompt_budget`，因此精确重复内容会
  重复占用预算，显式过期证据和显式冲突组没有选择语义；required 冲突若只沿用 budget error，
  还会丢失真实阻断原因。兼容 section 最初也把 governance 省略误报成 prompt budget。
- Metadata impact：扩展既有 `ContextCandidate`、`ContextAssemblyPolicy`、
  `ContextCandidateDecision`、`ContextAssemblyStatus`、`ContextSelectionMetadata` 和
  `ContextSectionDecision` 的 owned nested value；新增 trust/freshness 枚举、显式 `conflict_key`、
  governance reason/status，但不新增 `MetadataKind`、来源关系图或事实副本。所有新字段有历史默认值。
- 实现修复：budget 前执行确定性 governance pass，仅处理同 kind normalized exact duplicate、typed
  stale 和相同显式 conflict key；冲突优先级为 retention/trust/freshness/priority/source order。
  required stale 或多个不同 required 冲突 fail closed 为 `governance_blocked`，并通过独立
  `ContextAssemblyGovernanceError` 传播。memory adapter 映射已知来源信任/新鲜度，拒绝让自由文本
  attributes、相似 prose、tag、embedding 或 confidence gap 控制冲突；兼容 section 使用
  `source_governance`。adapter fingerprint 升级为 governance v2。
- 验证证据：初始测试在新 enum import 阶段失败；治理实现后 core tests 通过；补充的兼容测试先稳定
  暴露 `prompt_budget` 误报再修复。context/metadata/memory/request/recovery 定向回归通过，
  `Code/tests` 全量 `666 passed`。
- 剩余限制：被预算淘汰的旧对话/大证据仍只能截断或省略，缺少可校验、可失效、可恢复的
  artifact-backed compaction；phase 5D 需先写计划再实现。

## [已完成] C2-D：Artifact-backed dialog compaction（阶段 5D）

- 观察到的失败：预算淘汰的旧对话只能丢弃；旧 `ContextCompressor` 使用 `chars/4` 估算、LLM 或
  heuristic fallback、untyped synthetic system message，且不绑定 source ID、selection、checksum
  artifact 或 checkpoint，不能直接接入已完成的 typed/recovery 边界。
- Metadata impact：新增 default-compatible owned nested `ContextCompactionRecord` 与
  `ContextCompactionBinding`，扩展 candidate 的窄 `compacted_candidate_ids`、decision reason、derived
  trust 和 prompt snapshot binding；不新增 `MetadataKind` 或通用关系图。ShortMemory 仍拥有原始
  dialog，prompt_context artifact 仍是 exact replay 权威。
- 实现修复：builder 先正常选择，再只对 budget-limited 旧 dialog prefix 做 deterministic extract；
  至少保留两条 recent message 原文，summary 必须完整试装配成功，且 checkpoint sink 成功返回
  `context_compaction` reference 后才采用。每个 source decision 以 `compacted` 链到 artifact
  candidate；source fingerprint 变化即生成新 record。adapter fingerprint 升到 compaction v3。
  Runtime snapshot 绑定 compaction artifacts，resume preflight 与 replay 校验 checksum/size/record。
- 验证证据：metadata/assembler/builder tests 从缺契约/行为失败开始；controller 跨 checkpoint exact
  replay 使用原 summary，不重读变化来源；独立 compaction artifact 损坏在 session 前阻断。首次全量
  仅有 1 个失败：测试的 500-byte 预算无法合法容纳 summary + 两条 recent 原文，安全 fallback 符合
  设计；验收窗口改为 700 bytes 后 `Code/tests` 全量 `672 passed`，compileall/diff check 通过。
- 剩余限制：尚无 context quality scorecard/fixture corpus；旧 standalone `ContextCompressor` 和
  legacy `assemble(payload)` 仍存在，phase 5E 需先用静态 inventory 与质量测试判断收敛方式。

## [已完成] C2-E：Context quality 与 legacy convergence（阶段 5E）

- 观察到的失败：selection/governance/compaction 已有丰富 typed evidence，但没有离线质量契约或
  固定语料阻止后续回归；legacy section `assemble(payload)` 仅被兼容测试调用，standalone
  `ContextCompressor` 完全无生产 caller，却没有静态守卫或弃用边界。request hash 仍写旧 strategy 名。
- Metadata impact：新增严格但不持久化的 owned nested `ContextQualityExpectation`、
  `ContextQualityEvaluation` 与 issue enum；fixture author 拥有显式 expected IDs，stateless evaluator
  只派生结构问题，不新增 `MetadataKind`、不复制来源、不控制 runtime，也不宣称开放语义相关性。
- 实现修复：evaluator 检查 ready/budget/decision coverage/required representation/duplicate leakage/
  governance link/recent suffix/compaction link；四例 JSON corpus 覆盖 budget、conflict、duplicate、
  compaction。AST inventory 强制 Code/src 中 legacy assembler/compressor caller 为 0；兼容定义发出
  DeprecationWarning，历史 section/strategy reader 保留。当前 strategy/hash 统一为
  `retention_priority_order_v1`，memory adapter fingerprint 升到 quality v4。
- 验证证据：初始 quality test 因 module 不存在而失败；实现后 corpus 与 deterministic failure case
  通过；metadata round-trip、legacy warning、static inventory 均通过；`Code/tests` 全量
  `680 passed`。
- 剩余限制：quality 依赖人工明确的 fixture expectations，不是通用 semantic evaluator；compaction
  仍为 deterministic extract；历史 compatibility code 仅隔离未删除。这些均已显式记录且不绕过
  当前 typed/budget/governance/recovery 边界。

## [已完成] C2-F：动态历史与 Controller completion budget

- 观察到的失败：完整架构基线中 `Previous Task Results` 随失败子任务逐项复制，令同用途
  `tool_event_decision` 输入 1,284 → 1,611 → 1,951 tokens；4 个成功调用产生 8,006 output tokens，
  其中 83.3% 为 reasoning。初版 800-token ceiling 又造成 3 次空/非法 JSON，证明固定小上限会损害质量。
- Metadata impact：权威任务事实仍为 `TaskExecutionResult` / `TaskResultMetadata`；history 仅改为不持久化
  derived view。completion 总量、ceiling/floor、round decay 和 usage 扩展现有
  `RuntimeBudgetMetadata`，最终 provider 控制仍复用 `LLMRequest.max_tokens`，不新增 `MetadataKind`。
- 实现修复：history 投影改为 900 字符内的 status counts、recent ledger、latest delta 和去重 evidence
  paths；tool-event completion 使用 12,000 runtime total、2,000 ceiling、800 floor、每恢复轮 -400，
  并按剩余调用动态 fair-share。请求前预留、成功按 usage 结算、失败保留预留；该 purpose 的 JSON repair
  限为一次，checkpoint replay 不重复消费。
- 验证证据：基线 history 离线回放从 921/1,863 降为 808/832 chars；相关 metadata/tool-loop/context/
  checkpoint tests `217 passed`。2,000 风险闸门将核心任务 5/5 完成，pytest 3/3 与 compileall 通过；
  同用途输入稳定在约 1,535–1,589，成功 controller 输出为 1,453/487；`Code/tests` 全量
  `685 passed`，compileall 与 diff check 通过。
- 剩余限制：三个 capped controller 请求仍因 reasoning 导致 JSON 不完整并走 deterministic fallback；
  任务完成后的 project-improvement context 仍因 budget insufficient 使顶层运行失败；不同随机分解令总
  Token 不能作严格因果比较。`code_generation` / `project_improvement` 仍需独立质量门后接入统一预算。

## [已完成] C2-G：子任务权限、fallback 与完成证据加固

- 观察到的失败：对 C2-F 风险闸门逐调用复核后发现，inspect 子任务在 controller JSON 失败后可退化为
  整文件生成/覆盖；正确但截断的 JSON 没有保留 provider attempt 证据而重新生成；请求 `pytest` 的
  validation 实际执行 `compileall` 后仍被标记成功。此前“fallback 完成”掩盖了权限越界与 suspicious
  success，不能视为有效的 Token 收益。
- Metadata impact：复用 `Task` 的 kind/read_files/write_files/validation_command 作为子任务权限与完成
  意图；将 kind 收紧为 typed vocabulary。completion accounting 只扩展现有 `RuntimeBudgetMetadata`
  的 one-shot recovery bonus，不新增 `MetadataKind` 或第二预算所有者。provider usage/finish reason
  留在失败执行证据，部分响应留在 artifact，不复制进 runtime state。
- 实现修复：分解器使用受保护的 strict schema system candidate，标准化有限 legacy type 并拒绝未知
  kind；inspect/analysis 只保留读取/研究 need，implement/repair 无明确 write_files 时 fail closed；
  validation fallback 只能执行精确的非空 validation_command。完成判定比较声明命令与实际成功工具
  输入的 argv，禁止 `compileall` 替代 `pytest`。失败调用记录真实 usage/finish reason/部分响应；空且
  无 usage 的非法响应退款，length 截断只授予下一次调用一次受总预算约束的恢复额度。
- 验证证据：真实完整架构 run `20260803T172926Z` 分解为 inspect/implement/pytest/compileall 四个 typed
  子任务；inspect 仅 3 次读取，唯一写目标为 `calculator.py`，轨迹实际包含
  `python -m pytest -q` 与后续独立 `python -m compileall -q calculator.py`。同 host interpreter 独立
  复验 `3 passed` 且 compileall 通过。定向权限、命令证据、失败 usage 和恢复额度回归通过；全量结果
  `700 passed`，compileall 与 diff check 通过。
- 剩余限制：该 run 的核心执行 4/4 正确，但 project-improvement 尾部仍因 required context
  budget insufficient 令顶层失败；临时项目 `.venv` 未安装 pytest，实验实际依赖 host interpreter。
  `code_generation` / `project_improvement` 仍需各自的 purpose-specific 装配和质量门，不能从 controller
  的恢复额度直接外推。

## [已完成] C2-H：Project improvement 上下文拆分与完成语义

- 观察到的失败：完整架构 run `20260803T172926Z` 中，改进分析已经成功；实际预算异常发生在后续
  `iteration_task_design`。该调用把固定指令、目标、完整 project state、improvement report 与 schema
  拼成一个 required + forbidden 的 message，任何低价值部分增长都会令整体无法装入。另有语义缺陷：
  改进返回失败会覆盖已验证核心成功，异常则可能跳过最终报告；fast path 还会改写核心
  `TaskExecutionResult`，导致无法区分核心结果与增强结果。
- Metadata impact：复用 `ContextCandidate`、`ContextAssemblyPolicy`、selection 与 quality contracts；
  新增严格 owned `ProjectImprovementPolicy` 及 requirement/source/status enums，并在既有 runtime state/report
  中增加 `core_success`、policy、status 和 failure 字段。不新增 `MetadataKind`、第二 context owner、项目事实
  副本或通用关系层；旧 bool/count 仅作为兼容输入或 policy 派生 view。
- 实现修复：`iteration_task_design`、`iteration_goal`、`project_improvement` 分别使用专属 candidate adapter。
  指令、当前目标、安全约束和紧凑验证摘要完整保留；README、单个文件、diagnosis、memory 与历史证据按项
  选择/截断/省略。自动改进默认为 optional，显式正数为 required，0 为 disabled；optional 失败保留 warning
  与 typed failure evidence 但不抹掉核心成功，required 失败只改变 overall success。异常统一产生 terminal
  `pipeline_failed`/`pipeline_finished`，并将真实 error type/reason 带入结果和恢复状态。
- 验证证据：真实 adapter fixture 覆盖 oversized optional、required fail-closed、稳定 source ID、selection
  evidence 与 quality expectation；完成矩阵覆盖 disabled/optional/required、返回失败/预算异常、CLI 映射、
  runtime state round trip 和 terminal trajectory。`Code/tests` 全量 `719 passed`。
- 观察实验限制：post-change runs `20260803T180924Z`、`20260803T181040Z` 分别被上游 task decomposition
  写权限闸门和 compound validation exact-evidence 闸门提前终止，均未到达 project improvement，因此不得
  宣称真实 provider 的 Token 收益或完成质量已改善。当前已证明的是结构性上下文边界和确定性完成语义；
  后续需固定/回放上游轨迹后做 provider 反事实，并为三个 purpose 单独标定动态 completion budget。

## [已完成] C2-I：Project improvement 冻结反事实与固定分解实验

- 观察到的失败：历史 run `20260803T172926Z` 的 Task Designer 输入可重建为 96,209 chars / 29,342
  tokens 的单个 required message，在 3,968 effective budget 下 transport 前失败。初次 current replay 虽能
  装配 ready，却发现 report 缺少 `prompt_context` 时 required safety candidate 为空；三条权威非回归约束
  实际仍在 `ProjectState.validation_context.product_intent`。另外，实验分析器只汇总成功响应，漏计失败
  provider attempt，并在只有 2/3 target purposes 时错误标记成本 eligible。
- 实现修复：三个 improvement purpose 的 safety projection 以 validated project-state product intent 为
  权威来源，并与 report 约束稳定去重合并，保留 delivery surface/runtime mode；未新增 metadata owner 或
  改变 retention。实验新增 hash-locked trajectory replay、legacy 模板、fixed four-task decomposition 和窄
  injection，只替换 `decompose`，不伪造执行结果。analyzer 分开统计 responded/failed/observed attempt usage，
  reasoning 缺失保持 unknown，并新增 target-purpose coverage、deterministic goal mode 与 complete/incomplete
  成本结论。
- 确定性证据：legacy 为 29,342 tokens、`budget_insufficient`；current safety 修复后为 17,981 original、
  3,968 selected、`ready`，9 candidates kept、8 omitted，required omission/partial 为 0，三条非回归约束与
  `project_native` hard gate 全通过。实验 harness/analyzer 定向测试 `16 passed`。
- 真实观察：optional run `20260803T183221Z` 使用 fixture
  `calculator-four-stage-decomposition-v1`（SHA-256 `62bc25e5240a11c8cc1171a7d578b7fc7595642ecf9136d3d31a092d79443de3`）。
  四个核心任务全部完成，inspect 只读，唯一写目标为 `calculator.py`，测试文件 hash 不变；精确 pytest
  报告 `3 passed`，独立 compileall 通过。`project_improvement` 878 → 878 tokens，
  `iteration_task_design` 9,895 → 3,968，required 全保留。optional enhancement 在后续 `code_generator`
  transport 前 budget insufficient；runtime 正确保留 `core_success=true`、overall success、
  `project_improvement_status=failed` 与完整 failure evidence。
- 成本与限制：新版 analyzer 重算 8 logical requests、7 responded、1 failed；responded 22,511 tokens，
  failed attempt 3,642，总可观察 26,153，usage/reasoning coverage 100%。`iteration_goal` 采用确定性
  `seed_action_1`，没有 provider/context-selection request，因此 coverage 仅 2/3，成本结论
  `incomplete`、`eligible=false`。本阶段只证明结构性恢复和一次真实可执行请求，不构成 paired Token 因果、
  三-purpose 完整样本或总体分布收益。下一独立缺口是 improvement execution 的 `code_generation` context
  owner；不得用扩大全局预算掩盖。

## [已完成] C2-J：Improvement execution 的 Code Generation 上下文治理

- 观察到的失败：run `20260803T183221Z` 的 improvement task 已正确限定只改 `calculator.py`，但
  `CodeGenerator` 将完整 prompt context 格式化后，又重复展开 rubric、product intent、dependency、stack
  与 UI guidance，最终作为单个 required/forbidden message 提交。36,586-char retry input 在 provider
  transport 前失败，导致 enhancement 0/1；这不是模型理解失败，而是 owner projection 失败。
- Metadata impact：复查 `Task`、`ToolInputMetadata`、`CodeGenerationRequest`、`ProductIntentMetadata`、
  `ContextCandidate`/policy/selection contracts，采用 reuse + runtime derived view；没有新增 `MetadataKind`、
  字段、权限 owner 或项目事实副本。
- 实现修复：contextual Code Generator 使用专属 candidate adapter，分别要求完整 instruction、task、
  mutation boundary、product safety、current source 与 output contract；validation/rubric/report 独立选择；
  diagnosis/environment/product judgment 只投影字段级摘要。existing-file replacement 缺当前源码或完整源码
  自身超预算时 transport 前 fail closed。无 prompt context 的旧简单生成路径保持兼容。
- 确定性证据：hash-locked 历史输入中 legacy 为 49,781 chars / 13,324 tokens、
  `budget_insufficient`；current 为 8,430 chars / 2,076 tokens、`ready`，required omission/partial 为 0，
  task/权限/非回归/源码 hard gates 全通过。首版因 optional diagnosis 填满 3,968 tokens 被质量门拒绝，
  摘要化后留出 1,892-token 余量。
- 真实证据：fixed arm `20260803T200118Z` 核心任务 4/4，improvement code generation 为 1,942
  assembled、2,025 provider input、342 provider output tokens，全部 required kept；唯一改动文件为
  `calculator.py`，测试 hash 不变，精确 pytest 3 passed、compileall 与直接运行通过，trajectory 记录
  `completed_improvements=1`。最终 Code/tests + context experiment 回归 `747 passed`，compileall 与
  `git diff --check` 通过。
- 剩余限制：该 run 的 `iteration_task_design` 仍由 optional memory 填满 3,968 tokens；controller 仍有一次
  2,000-token reasoning length failure；临时 `.venv` 不含 pytest，精确验证依赖 host interpreter。以上不由
  本轮 Code Generator adapter 掩盖，分别留作 task-design selection、completion budget 与环境一致性工作。

## [已完成] C2-K：Task Designer 记忆隔离与项目环境依赖对齐

- 观察到的失败：run `20260803T200118Z` 的 Task Designer 检索到其他项目记忆，并把旧环境记录的 PATH、
  Git snapshot、完整 dependency 等 attributes 作为不可区分的 optional evidence 贪心填满 3,968-token
  输入；同一临时项目只用 `written_files=[calculator.py]` 做 import scan，遗漏
  `test_calculator.py` 的 `pytest`，导致核心验证依赖 host interpreter，而后创建的 `.venv` 无法执行同一命令。
- Metadata impact：复用 `ProjectStateMetadata.memory_records` 的 runtime derived view、MemoryRecord 既有
  `project_path`/tags，以及 `EnvironmentSyncMetadata` 的 python/command/dependency 字段；没有新增字段、
  `MetadataKind`、记忆 owner 或解释器事实副本。模型上下文压缩与依赖扫描均为现有 owner 的派生行为。
- 实现修复：project/task/session memory 必须用规范化 `project_path` 或当前项目 tag 明确匹配；global
  feedback/long-term guidance 仍可使用。读取器只投影有界 content、tags 和环境/迭代属性白名单，Task
  Designer 再次防御性压缩并限制 3 条，禁止 raw PATH/provider/Git/dependency payload 进入 prompt。环境
  import scan 从“有 written_files 就提前返回”改为“优先 written_files 后补项目 Python 文件”，并跳过
  `.venv/.git/node_modules/__pycache__`、总计最多 200 文件，因此测试依赖能进入项目 `.venv`。
- 验证证据：新增跨项目隔离、runtime attribute 剥离、Task Designer 二次压缩与测试文件依赖发现红测，
  修复前 3/3 失败、修复后 3/3 通过；上下文/环境/runtime/tool-planning 定向回归 `129 passed`；
  `Code/tests` 全量 `729 passed`，compileall 与变更文件 diff check 通过。
- 剩余限制：本阶段修复项目环境同步后的依赖完备性和后续验证解释器一致性；核心任务在环境首次同步前
  若直接执行命令，仍可能使用 host interpreter。把 environment preparation 前移到核心执行生命周期会改变
  工具权限、网络副作用与新文件依赖发现时机，应作为单独架构变更和完整实验处理，不能暗中塞进本次小修复。

## [已完成] C2-L：核心执行前环境门禁与恢复解释器绑定

- 观察到的失败：核心 Python/pytest 子任务可能在 project improvement 创建 `.venv` 之前直接使用 host
  interpreter；进程恢复后 `_project_environments` 缓存丢失会再次退回 host。恢复验证还直接调用 executor，
  绕过 live command-context rewrite；若简单改写为绝对 `.venv/bin/python`，旧 checkpoint 进度又会因 effective
  command 与 requested command 不相等而停滞。
- Metadata impact：复用 `EnvironmentSyncMetadata`、`ToolInputMetadata` 和 `ProjectFingerprint`，不新增
  `MetadataKind`。环境 contract 增加 typed `operation`、`readiness`、`environment_id`；旧 payload 迁移为
  `legacy_sync + unknown`。Tool input 保存 requested/effective interpreter/environment identity；checkpoint 使用
  既有 interpreter/environment ID 字段，不复制一份运行时环境状态。
- 实现修复：standard/enhanced 核心执行在 task execution 前进入 environment gate；filesystem-only preflight
  可自动 attach ready `.venv`，setup/resync 在非 auto-approve 模式先询问，拒绝时零副作用。每个 Python
  validation 前再次 preflight 捕获写后 dependency drift；环境未 ready 时返回 `EnvironmentNotReady`，不再使用
  host。环境 identity 绑定 project/env、`pyvenv.cfg` 与 installed distribution 集。checkpoint 持久化 ready
  interpreter/identity；resume 只读重建 cache，file-mutation 与 legacy pending Python verification 同样检查
  drift，并在执行前应用 requested/effective rewrite。verification cursor 使用 requested command 推进。
- 验证证据：preflight 零副作用、existing attach、identity drift、setup denial、session gate、command rewrite、
  completion evidence、checkpoint binding、file-mutation resume 和 legacy fail-closed 均有确定性测试；环境、
  task executor、tool planning、runtime session、checkpoint、event emitter 与 metadata 联合回归 `267 passed`。
  无网络固定 calculator 项目由 preflight 判定 `ready`，effective interpreter 为项目 `.venv/bin/python`，并以
  该解释器完成 `python -m unittest -q`（2 tests OK）。仓库已有 `.venv` 因缺少 `pydantic` 无法收集项目测试，
  作为 stale/not-ready 负向证据保留，未借 host site-packages 掩盖也未擅自联网安装。最终 `Code/tests`
  全量回归 `744 passed`。
- 剩余限制：当前只治理项目 Python `.venv`，不宣称支持 Conda/uv/Poetry 或外部服务/secret readiness。
  Setup executor 仍是 runtime-owned 专用调用，虽有入口 approval 与 start/result evidence，但尚未纳入通用
  `prepared -> observed -> applied` checkpoint reconciliation，因此安装/Git side effect 不宣称 exactly once；
  网络/package registry 状态也只能重新评估。首次空项目仍需写后 preflight/resync 才能发现最终依赖。

## [已完成] C2-M：通用 Reasoning 策略、回放身份与整架构机制实验

- 观察到的失败：Controller 的窄 JSON 决策会让 provider 默认 thinking 吃满 2,000-token completion，出现
  `length`、部分 JSON 与 deterministic fallback；原请求/缓存身份没有绑定 provider capability 和实际
  reasoning 语义。直接全局关闭 reasoning 又会把复杂任务一并降级。整架构 pilot 还暴露可选 enhancement
  写坏文件后只报告失败、不恢复 safety snapshot，并可能继续修复 stale failed state。
- Metadata impact：在既有 `metadata/runtime.py` 内增加严格 owned `ReasoningPolicy`、
  `ResolvedReasoningPolicy` 和 typed mode/effort/profile/transport enums；它们扩展 `LLMRequestMetadata`，不新增
  `MetadataKind` 或第二 LLM owner。`provider_bound_v2` 扩展既有 recovery hash version；`IterationResult` 的
  rollback 字段是 iteration transaction 结果，不复制 Git snapshot authority。
- 实现修复：`core/reasoning.py` 用 endpoint 与显式 typed override 选择 versioned capability profile，统一解析
  caller intent，再由 transport 渲染 OpenAI/DeepSeek 字段。只有显式读取的 inspect、精确 validation 和单写
  目标 implementation 使用 configured routine policy；general/ambiguous/multi-write 保持
  `provider_default`。违反子任务 contract 的 plan 被全部过滤时 purpose-specific fail closed，不再扩大 fallback。
  v2 replay identity 绑定 provider、model、去凭证 endpoint（保留非默认端口）、profile version 与 effective
  reasoning；legacy unbound replay 被阻止。失败 provider attempt 保留 usage/finish/partial evidence。可选改进在
  写后失败时按 explicit changed files 从 pre-iteration snapshot 恢复并停止。
- 验证证据：固定 routine screening 的 offline system quality 从 baseline 1/4 到 economical 4/4，completion
  median 1,954 → 145；1,200 budget arm 为 4/4，800 为 3/4，故未降低 production 2,000 ceiling；complex
  economical/high 均通过且 high 多用 677 reasoning tokens，故没有自动 high 路由。最终单对整架构 pilot 中
  两组均完成 4 个核心任务、测试文件 hash 不变、通过项目 `.venv` 精确 pytest/compileall；routine-disabled
  相对 provider-default 的 Controller output -85.08%、total -40.94%、duration -68.64%，完整可观察 lifecycle
  tokens -27.88%，并移除该 pair 的 1 次 length failure。baseline failed attempt 的 usage、1,822 reasoning、
  `finish_reason=length` 与 partial output 完整保留。最终离线全量回归 `771 passed`。
- 剩余限制：最终 A/B 只有一对且非目标 LLM 调用仍随机，只能证明机制、不能宣称统计因果；应至少再做 3 对、
  交替 arm 顺序。两组仍有 late-run input amplification，optional project improvement 仍主导尾部输出，说明
  reasoning control 没有解决所有上下文膨胀。improvement fast-tool 路径尚无与 `ToolEventLoop` 等价的
  first-class `tool_called` 事件。环境 setup 仍仅支持 Python `.venv`，且 side effect 尚不宣称 exactly once。

## [已完成] C2-N：Project improvement fast-tool 持久化证据对齐

- 观察到的失败：`_execute_fast_tool` 虽生成 typed UI lifecycle 和最终 envelope，但不调用 runtime diagnostics
  hooks；module-owned 的 environment/project-state/improvement tools 连稳定 call ID/context 都没有。因此真实
  enhancement 写入和分析不会形成 first-class `tool_called/tool_succeeded/tool_failed`，run summary 出现动作
  假阴性。首版桥接复审又发现 diagnostics hook 异常可阻止执行或使已成功副作用被上层重试，且重复
  task/step 共用同一 call ID。
- Metadata impact：完整复用 `ToolCallMetadata`、`ToolContextMetadata`、`ToolErrorMetadata`、
  `ToolEventMetadata` 和 `ToolExecutionEnvelopeMetadata`；没有新增字段、`MetadataKind`、usage owner 或 fast-tool
  专用 event schema。execution route/runtime phase 是非控制 diagnostic attributes，provider usage 仍由
  `LLMResponseMetadata` 独占。
- 实现修复：registry fast path 与 module-owned path 为每次 invocation 生成唯一 call ID，保留同一逻辑调用内
  retry；通过现有 hooks 持久化恰好一个 start 和一个 terminal event。失败的 `ToolResultMetadata` 不再被记录为
  success，完整 `FailureMetadata` recovery/details 进入 `ToolErrorMetadata`。hook started/terminal 异常被桥接层
  隔离并仅 best-effort 记录，不能阻止、改变或重复业务动作；module-owned 路由由调用方显式标记，不再根据
  registry 中是否存在同名工具反推。
- 验证证据：红测首先观察到 fast durable events 为空、module envelope 无 call ID/context；实现后 success、
  exception failure、failed-result、retry-then-success、重复 step identity、root correlation 和 hook fault isolation
  均通过。相关 diagnostics/improvement tests `74 passed`，最终 `Code/tests` 全量 `779 passed`，compileall 与
  diff check 通过。
- 剩余限制：本阶段只证明事件和关联身份完整；fast mutation 仍未接入标准 edit Guard、pending verification、
  checkpoint prepare/observe/replay 和逐动作 completion evidence，不能据此声称权限或 exactly-once 对齐。
  单个 nested LLM attempt 的 tool-parent 归属仍以 purpose/phase 聚合为主；后续阶段再决定是否需要显式 parent
  correlation，不能复制 provider usage 到 tool envelope。

## [已完成] C2-O：Project improvement fast mutation 权限、checkpoint 与完成证据对齐

- 观察到的失败：fast path 虽已有 durable tool 事件，但写操作可以绕过标准 EditGuard、checkpoint
  prepare/observe/replay 与 pending verification。首版修复又被独立复核发现只在 fake controller 上成立：
  `readme_tool`、`bug_fix_tool` 被入口视为 mutation，真实 RuntimeController 和 EditGuard 的 allowlist 却仍会
  no-op。另有工具返回 success 但文件无 diff 时，曾可能先持久化成功 observation 再由外层判失败。
- Metadata impact：复用 `Task.write_files`、`VerificationPlanMetadata`、`EditPlanMetadata`、既有 checkpoint
  mutation fields 和 `FailureMetadata`，未新增 `MetadataKind` 或第二份权限/恢复 owner。新增的 shared mutation
  descriptor 只是从 typed tool input 派生 mutation targets，不持久化权威状态。
- 实现修复：真实 fast mutation 调用方在目标解析后显式声明 task kind、read/write files 与原样 validation
  command；README、基础文件写和 bounded bugfix 使用同一 target descriptor，统一进入 task/root scope、标准
  EditGuard、prepare/observe/apply、diff/hash 与 edit budget。Bugfix 目标收窄到 resolver 已授权的 primary file；
  环境再次改写 run command 后同步更新 task validation。prepared checkpoint 不 durable 时零执行，observation
  失败不能宣称成功，read replay 不重复执行；success/no-diff 在 observe 前转换为 typed failure。
- 验证证据：权限、越界、read-only、prepare/observe/replay、exact validation、no-diff failure、README/bugfix
  真实 controller 分类与 mutation targets 均有确定性测试；阶段相关回归 `228 passed`。独立只读审查发现并
  促成 shared descriptor P0 修复；最终全量回归留在本治理 Goal 的 Stage 7 统一执行。
- 剩余限制：bugfix 暂不自动扩大为多文件事务；合法多文件修复必须由后续 task decomposition 显式授权，
  不能从 validation 文本隐式扩权。任意外部命令和 environment setup 的不可逆副作用仍不宣称 exactly-once。

## [已完成] C2-P：Project improvement 项目身份、输入去重与 symbol edit 路由

- 观察到的失败：Task Designer 会把已经装配过的 `memory_context.prompt_text` 再作为 artifact 加回请求，
  同一代码、README、记忆和环境事实被重复发送；项目 memory 可凭 query/basename 混入其他项目；分析只看
  `written_files`，因而误判现有测试文件不存在。当前代码又被放在 nested project context，执行路由从顶层
  读取，单 symbol 修改退化为全文件生成。首版 nested 修复经独立复核发现仍用截断 prompt 片段计算 AST/
  行号；首版 manifest 也会先 `rglob+sorted` 全树再截 40，名义有界但 I/O 不有界。
- Metadata impact：复用 `ProjectStateSnapshot.file_summaries` 作为 derived manifest view、
  `safe_target_files` 继续独占可写范围，memory owner 与 selection/source ID 不变；未新增 `MetadataKind`、项目
  身份副本或写权限字段。Canonical path 是现有 `attributes.project_path` 的比较规则，manifest 空 preview
  条目是项目 inventory 派生事实，不是写授权。
- 实现修复：project/task/short-term memory 必须用 canonical resolved project path 明确匹配；global feedback/
  long-term/reference/user guidance 仍可使用，异常路径 fail closed。三个 improvement purpose 移除 assembled
  `prompt_text`，仅投影最多 3 条压缩 memory。required validation 投影增加最多 40 个文件名；`os.walk`
  top-down 在进入 `.git/.venv/node_modules/cache` 前剪枝，到上限立即停止，不读非目标正文且不扩大 safe
  targets。Symbol 路由与 code_editor code 从目标文件权威全文读取，compact projection 只作模型 evidence。
- 验证证据：canonical/symlink 与 Darwin `/var` alias 隔离、三 purpose aggregate 去重、granular memory
  保留、大测试文件只见文件名不见正文、排除目录预剪枝、大于 projection 上限的源码仍走 code_editor 且
  code 等于全文均有确定性测试。阶段相关回归 `123 passed`，复核后聚焦回归 `78 passed`；独立复核无 P0，
  提出的两个 P1 均已修正。
- 剩余限制：manifest 是 bounded name inventory，不提供内容语义；需要读取测试或其他代码时必须由后续
  evidence selection 显式选择。全局 guidance 的 project relevance 仍由 retrieval 决定，但不能携带冲突的
  explicit project path。超大单文件若必须 full replacement，仍由 code-generation context budget fail closed。

## [已完成] C2-Q：Project improvement 与 Task Designer 有界增量输出

- 观察到的失败：Project Improvement 与 Task Designer 的 schema 允许模型重述 summary、完整状态、多个 task
  及 task/goal ID；字段无代码级长度/数量限制。Task Designer 即使最终只取第一项，也已为多项输出支付
  reasoning/output Token。首版 delta 修复经独立复核发现 retained evidence 未校验、相对目标会被静默替换、
  metadata 丢 project/goal/iteration identity、Goal ID 仍由 provider 控制，且宽泛 stack patch 可进入持久化。
- Metadata impact：在现有 `ImprovementAnalysisMetadata` 增加 `evidence_ids` typed link；它只引用本次 request
  实际 retained candidate，不拥有证据内容。`DesignedImprovementTask.evidence_ids` 是 autonomous iteration
  local value；无新 `MetadataKind`、无第二 diagnosis/project/safety owner。Project/goal/iteration 仍由 tool input
  产生；provider delta 不能写身份。
- 实现修复：analysis schema 收窄为 changed signals、actions、one next goal、must-satisfy、risks、evidence IDs
  与 strict typed stack patch；extra/full-state、超长、超项、malformed payload 整体进入同界 fallback。旧字段仅在
  边界单向映射，不回灌 prompt context。Evidence IDs 依据 request `context_selection` 过滤；stack patch 只允许
  mutable typed fields，deterministic safety update 冲突时优先。Task Designer 每次只请求一个无身份 task delta；
  runtime 对 goal/task 内容做稳定 hash，canonicalize safe targets，无合法显式目标 fail closed，合并 authoritative
  goal criteria 与 validated product intent，并只保留 retained evidence IDs。Legacy `tasks[0]` 仅作受限迁移。
- 验证证据：analysis 红测 5 项、task delta 红测 4 项修复前全部失败；实现后 delta/metadata/context/execution
  联合 `101 passed`，Stage 5 相关回归 `226 passed`。独立只读复核无 P0，报告的 6 个 P1 均在阶段内修正。
- 剩余限制：增量协议减少可见 JSON 和重复事实，但真实 provider 的 hidden reasoning 降幅需等 Stage 6
  purpose-aware reasoning/completion budget 与 Stage 7 完整架构实验共同验证，不能仅凭 schema 宣称 Token 收益。

## [已完成] C2-R：Project improvement 阶段动态 completion 预算与恢复幂等

- 观察到的失败：三个增量 JSON 调用与 improvement code generation 没有共享的阶段 completion 总量；首版
  接线又把 `max_retries=0` 误当作“零额外重试”，真实 `LLMClient` 因而执行零次 provider。静态/随机 reservation
  会改变 checkpoint replay hash、重复扣款和结算；required 调用仍可能在 no-client/provider/schema-invalid 后
  静默 fallback；核心 codegen 还可能提前消耗尾部预算，截断代码可能进入写路径。
- Metadata impact：扩展唯一 `RuntimeBudgetMetadata`，增加 strict owned enhancement policy、purpose limit、
  request、reservation、reconciliation 与 complexity/value/requirement enums；没有新增 `MetadataKind` 或 provider
  usage owner。`ProjectImprovementPolicy` 仍唯一决定 stage/top-level success，单次
  `EnhancementCompletionRequirement` 只决定预算/生成失败是否允许 fallback。两个默认 12,000-token completion
  池分别属于 controller decision 与 post-core enhancement，明确不是一个全局池。
- 实现修复：四个 purpose 使用 purpose floor/ceiling、复杂度、剩余价值、prompt size、剩余调用与总量动态预留；
  `max_retries=1` 表示一次总尝试。Stable semantic logical key、checkpointed reservation/reconciliation ledger、
  provider-payload-only replay hash 与 aggregate validator 保证 reserve/reconcile apply-once；known failed usage
  计费，unknown usage 保守占用。两个 bounded JSON purpose 仅对 typed length 信号做一次增量恢复；codegen
  length typed fail。Required no-client/non-JSON/schema-invalid/无合法 goal/task 均 fail closed；optional 只在安全
  边界 fallback，显式越权 target 仍返回 no-task。核心 codegen 与 enhancement pool 隔离，改进 codegen 继承顶层
  requirement；完成过的 goal 在 task design 前去重。
- 验证证据：预算 exact-purpose/fair-share、真实 LLM attempt 语义、failed usage、length recovery、ledger JSON
  round-trip/损坏状态拒绝、历史 payload defaults、重建请求 replay identity、required/optional matrix、core/
  enhancement 隔离及截断代码拒绝均有确定性测试。Stage 6 宽回归在最终复核前为 `406 passed`，随后新增边界
  测试继续纳入 Stage 7 全量回归；`git diff --check` 通过。
- 剩余限制：trajectory 可关联 reservation 与 provider attempt，但没有独立 terminal reconciliation event，必须
  结合 checkpoint 才能重建 refund/unknown hold/final aggregates。真实 provider Token 与质量收益尚未声明；由
  Stage 7 固定轨迹机制检查验证，稳定后再做多组完整架构配对实验。

## [已完成] C2-S：上下文治理完整回归与固定轨迹机制验收

- 观察到的失败：旧实验 hard gate 仍匹配 Task Designer 的宽 `tasks` schema，Stage 5 收窄为单 `task` delta 后
  产生假失败；直接 pytest 实验目录会误收集故意保留历史 bug 的 fixture 项目。更重要的是，成本 collector 只看
  三个 purpose 和成功响应，遗漏 improvement `code_generation`、失败 provider usage 与 Stage 6 reservation trace，
  无法审计新的共享 enhancement window。
- 实现修复：实验目录通过本地 pytest 配置排除 fixtures/runs；Task Designer gate 与当前单 task schema 对齐，
  immutable source/hash 保持不变。固定分解 collector 扩为四 purpose，关联 `llm_responded`/`llm_failed`，分别输出
  responded、failed-attempt、observable totals 与 usage coverage；request trace 保留 reservation ID、reserved、
  remaining 和 recovery-of，但明确不从 trajectory 推断 checkpoint-owned reconciliation/refund。
- 验证证据：完整生产 `Code/tests` 为 `861 passed`，compileall 与 diff check 通过，独立复核无 P0/P1；实验
  harness `29 passed`。冻结重放在 hard gates 全过的前提下，Task Designer original tokens 29,342→6,785
  (-76.88%)，Code Generator 13,324→2,076 (-84.42%)。既有完整架构单对实验保持 tool-event total -40.94%、
  full lifecycle -27.88% 的机制信号，但不扩写为多样本因果结论。
- 剩余限制：历史 run 没有 Stage 6 reservation，不能证明新动态预算已降低 provider Token；11 个旧 run 中多数仍有
  late-run pressure 弱告警。稳定后应执行至少三对、交替 arm 顺序、固定代码/分解/provider/profile/cache-off 的
  完整架构实验，并按 core/enhancement 与四 purpose 分窗。实验 runner 的 wall-clock/provider cumulative hard
  limit 仍需在下一次真实 provider campaign 前单独加固。

## [已完成] C2-T：Stage 7 首臂失效、Code Edit 预算旁路与恢复收敛

- 观察到的失败：三对 campaign 的第一臂在 41,884 个已观察 Token 后 fail closed；核心任务成功，但 enhancement
  为 33,861 Token、usage coverage 仅 12/14，两个 `code_edit` timeout 用量未知。第三次 `code_edit` 仅返回 82
  个字符，却产生 9,566 completion Token（其中 9,542 reasoning），随后因目标符号误选为 `add`、内容无变化而
  `NoObservedFileMutation`。进一步核对发现 arm manifest 虽声明 static，本地实验 budget 在完整 runtime 中被
  controller-owned budget 覆盖；`.venv/bin/python` 前缀还使实际成功的验证命令被字符串全等门禁误判。
- Metadata impact：扩展唯一 `RuntimeBudgetMetadata.enhancement_completion_policy` 的 owned purpose map，将
  `ContextRequestPurpose.CODE_EDIT` 纳入同一共享池（400–1,600）；没有新增 `MetadataKind`、第二预算 owner 或
  provider usage 副本。历史四-purpose policy 在读取时补入 typed `code_edit` limit，新写入统一为五 purpose。
- 实现修复：实验 arm 固定权威 `_enhancement_runtime_budget` resolver，manifest 记录 effective policy；policy
  mismatch、purpose 缺失、未知 usage 和质量失败均逐臂停止，项目 `.venv` Python 验证命令按解释器等价规范化。
  增强 task executor 为 `code_editor` 显式附加现有 runtime budget；编辑请求预留、设置 `max_tokens`、使用通用
  routine reasoning policy、禁 transport retry 并对账 usage，普通核心/独立 edit 不消耗 enhancement pool。
  Symbol 推断改用任务/goal/验收证据的标识符边界，不再让 `adding` 命中 `add`。未知 usage timeout 不再触发
  full/compact/surgical 三连调用；只有具备 usage 且 `finish_reason=length` 的 typed failure 可进入受限恢复。
  无文件 diff 继续作为不可恢复失败，不能宣称成功。
- 验证证据：实验 effective-policy、五-purpose fail-fast、venv command normalization；metadata 历史迁移与
  round-trip；`code_edit` reservation/max-token/reasoning/reconciliation；增强路由 runtime handle；symbol 边界与
  timeout/length recovery admission 均有确定性测试。阶段聚焦回归分别为 40、122、53 passed；最终全量结果见
  本阶段收尾验证。
- 剩余限制：已停止的第一臂是污染样本，只能作为失败证据，不能纳入 static/dynamic 比较。当前没有重新执行
  provider campaign；已执行的四-purpose V1 协议保持历史冻结，修正后的五-purpose V2 必须从新目录开始。
  Provider timeout 仍可能没有 usage，
  此时系统保守占用 reservation 并停止恢复；不会把未知成本当作零。单个 text code response 若以 `length` 结束，
  仍须先通过代码完整性校验，不能仅因存在 partial text 自动写入。

## [已完成] C2-U：Stage 7 停止诊断、实验隔离与动态预算三对验收

- 观察到的失败：V2 首个 static 臂已正确命中五 purpose 静态上限，usage coverage 100%，无 transport retry/
  unknown failed usage；core 8,092、enhancement 13,378、lifecycle 21,470 Token，`code_edit` 仅 243 Token。
  但 post-core 改进选择了模糊依赖目标且没有产生 diff，质量门正确停止。协议还错误要求互斥的
  `code_generation` 与 `code_edit` 同时出现。单目标 Task Designer 被标为 standard，实际解析为
  provider-default reasoning，2,200 completion cap 被打满。
- 原因与计划：预算接线本身已得到机制验证，V2 停止不是“本项目缺少注入能力”，而是实验共同干预和验收条件
  没有隔离目标选择及 mutation routing。V3 为两臂固定同一个可观察 `divide` docstring 目标，要求
  project-improvement/task-design provider coverage、deterministic goal mode，并按 `code_generation|code_edit`
  any-of 验收。单安全目标 task design 改为通用 routine reasoning intent，多目标仍保持 complex。V3 首臂进一步
  暴露 Task Executor 无条件追加 README mutation：`calculator.py` 已产生 1 行目标 diff，但 README 不在 designed
  task target 内且被 EditGuard 拒绝，整个 improvement 被错误否决。现改为仅当当前 designed task 明确包含规范化
  README 路径时才授权、执行并计入成功；代码任务不再隐式扩写 `Task.write_files`。
- Metadata impact：无新 metadata 字段、owner 或权限事实；复用现有 `iteration_goal_mode`、purpose coverage、
  reasoning policy 与 improvement quality evidence。固定目标和 budget resolver 都是 manifest 明示的
  experiment-only common intervention，不改变生产默认目标选择。
- 验证证据：V3 协议、运行命令透传、route-aware arm/analyzer gate 与单目标 routine policy 的确定性测试已新增；
  首臂 Task Designer 为 3,993 input + 122 output、`finish_reason=stop`，未再打满 2,200 上限。未请求 README 跳过、
  明确请求 README 仍为必要步骤的红绿测试已通过；定向回归 `121 passed`、实验回归 `42 passed`、生产全量
  `867 passed`，`git diff --check` 通过。provider 配对结果将在本阶段完成后补写。
- 剩余限制：V2 单臂不能与 V1 构成配对因果比较；V3 只有固定任务机制效度，即使三对质量匹配，也不代表跨任务
  分布收益。

### V3 跨臂 memory 污染与 V4 隔离

- 观察到的失败：README 权限修复后 V3 第一对两臂均等质量成功，static/dynamic enhancement 分别为
  6,052/5,990 Token，lifecycle 为 14,429/13,867，usage 均完整。但第三臂启动时明确检索到前两臂刚写入的
  iteration succeeded/failed memory；fixture 与代码固定，默认 `data/memory` baseline 却随臂累积。
- 实现修复：中止第三臂，不把 V3 pair 纳入后续统计。V4 在构造完整 runtime 前把 `MemoryStore` 实验性绑定到
  `<arm output>/isolated_memory`；臂内记忆读写照常，跨臂不共享。manifest 记录实际路径，逐臂与 campaign analyzer
  都验证策略和规范化路径，不匹配立即停止。无生产 memory 默认行为或 metadata contract 变化。
- 验证证据：隔离 scope 从空目录构造 store、V4 command 透传、manifest gate 和旧协议兼容均有确定性测试；
  V4 provider 结果待执行后补写。

### V4 首次执行的 symbol 语义歧义

- 观察到的失败：V4 前三臂质量与 memory gate 均通过；第 4 臂的 designed task、iteration goal 和 acceptance
  criteria 都明确要求 `divide`，但 task description 以动词 “Add” 开头。旧 `_infer_target_symbol` 把所有字段
  拼接后按源码符号顺序扫描，先把动词 `Add` 命中函数 `add`；provider 返回原 `add`，写入层以 no-diff 拒绝，
  campaign 正确停止。
- 实现修复：symbol inference 按 explicit symbol → typed iteration goal → acceptance criteria → task/report prose
  分层解析。每层只有唯一 symbol 才可走 `code_editor`；同层多个 symbol 视为歧义并退回更安全的非 localized
  route，不再按源码顺序猜测。未新增 metadata；复用现有 typed goal/criteria 的权威顺序。
- 验证证据：“Add a concise docstring to divide” 红测修复前稳定选择 `add`，修复后选择 `divide`；Task Executor
  全套 `32 passed`，相关定向 `122 passed`、实验 `43 passed`、生产全量 `868 passed`，diff check 通过。
  V4 将在新快照和新目录完整重启，旧前三臂不复用。

### V4 完整三对结果

- 验证证据：新快照 `sha256:480702b1a6c750cc6007d9cb1297629d6695b483a49d7e9dccd5224d84625c0e`
  下六臂全部成功，3/3 quality matched，memory baseline/usage 100%，0 failed attempt、0 transport retry、0
  recovery。static/dynamic lifecycle 为 42,136/42,679，enhancement 为 18,110/18,343，dynamic 均 +1.29%。
  三对 lifecycle 变化依次 +0.47%、+1.82%、+1.58%。
- 结论：dynamic 每臂 completion reservation 由 5,300 降至 2,390（-54.9%），但实际三个 purpose 的输出都远低于
  dynamic ceiling 并自然 stop，所以没有正常态 Token 降幅；enhancement 多出的 233 Token 中 208 来自普通 output
  波动，未受干预的 core 同时多 310 Token。该策略已证明能限制最坏暴露与恢复，而非本固定任务的正常成本优化器。
- 下一信号：六次 Task Designer provider input 均为 3,992–3,993，原 candidate 约 8.6k，装配持续填满 3,968
  prompt budget；它贡献两臂合计 23,955/33,791 enhancement input。下一阶段应固定 completion policy，针对
  diagnosis artifact 做有质量门的 compact projection A/B，保留 typed goal、权限目标、验收、验证、安全和 evidence
  links，以实际 input 降幅和等质量为验收。
- 剩余限制：这是单任务/单 provider profile 的三对机制结果，不是任务分布因果估计；不能把 reservation 降幅当成
  usage 降幅，也不应把已停止的 V1–V4 诊断臂混入统计。

## [已完成] C2-V：Task Designer 证据可见性、紧凑投影与 Stage 8 三对验收

- 观察到的失败：Stage 7 V4 的六次 Task Designer provider input 均为 3,992–3,993 Token，装配持续填满
  3,968-token prompt budget；完整 diagnosis 被 HEAD 截断后仍占请求主体，两条 `project_environment` memory
  也被重复携带。Schema 要求 provider 返回 candidate evidence ID，但实际消息只发送 candidate content，模型
  看不到合法 ID，导致返回值被运行时过滤。Stage 8 首个诊断对又暴露两项实验门禁错误：把生产动态预算的
  prompt bonus 误写成全臂固定 1,150 Token，并把 treatment 前核心 provider 产生的等价代码措辞哈希当作
  Task Designer 质量等价条件。
- Metadata impact：无新增 metadata contract、字段或 owner。紧凑 diagnosis/iteration-memory 是从既有
  `ProjectDiagnosisMetadata`、生产 `MemoryRecord` 和 `ContextCandidate` 派生的一次性 model-facing view；证据
  仍由原 candidate/source ID 引用，`ContextSelectionMetadata` 仍独占装配决策与 Token 事实。没有复用或扩展
  面向持久化 dialog 的 `ContextCompactionRecord`，也不需要 migration。
- 实现修复：typed-candidate request 同时在预算 renderer 和实际 `LLMMessage` 中加入 JSON-escaped
  `[evidence_id="..."]` 头；legacy `build_messages` 适配路径保持原文。Task Designer builder 新增默认保持
  `current` 的显式 `projection_policy`，`compact` 保留 instruction/schema/goal/safety/完整 validation/project
  source，只保留目标相关且有界的 selected diagnosis 与最新相关 autonomous-iteration task result，排除
  `project_environment`/无关 memory；派生候选为 optional/derived/current/forbidden 且 source ID 版本化。
  Stage 8 固定源事实指纹并交叉三对，只切换 production builder policy；动态 reservation 用生产 coordinator
  按实际 prompt、复杂度、剩余价值、剩余调用和余额推导。质量门直接验证唯一授权目标、冻结验收条件、合法
  evidence roles、验证命令与 mutation scope，核心阶段最终文件哈希仅作描述性观察。
- 验证证据：生产全量 `876 passed`，实验回归在最终协议下 `68 passed`，compileall、diff check 与 dry-run
  均通过。正式目录 `runs/stage8_campaign_v1_20260804T085747Z` 六臂全部成功，3/3 quality matched，54/54
  logical request usage 完整，0 failed attempt、0 transport retry、0 recovery。Task Designer provider input
  11,979→2,778（-76.81%），final prompt 11,904→2,703（-77.29%）；三对降幅均为 76.81%。current/compact
  reservation 分别由生产公式合法推导为 1,150/1,000。完整 campaign 为 76,808 Token，无 warning 或 hard
  failure。首个 `runs/stage8_campaign_20260804T084822Z` 对仅作门禁诊断，不混入正式统计。
- 剩余限制：这是固定 calculator 任务、固定 frozen Task Designer source 与单一 provider/profile 的机制验收，
  可以证明该投影在此受控完整架构轨迹降低实际输入并保持结构化质量，不能外推为跨任务分布效应。compact
  本次实际省略的是与固定 goal 不相关的两条 environment memory 和整个 diagnosis，尚未覆盖“保留相关
  diagnosis 的紧凑分支”或相关 autonomous-iteration `MemoryRecord`/result-summary retention。compact 仍未成为生产默认；切换前应补充 goal-diagnosis
  相关、无关、部分相关三类样本，并至少执行不同任务类别的小规模 canary。每臂通用 enhancement cost collector
  因 deterministic goal 与 `code_edit` route 仍为 `eligible=false/incomplete`；enhancement/lifecycle 总量下降属于
  secondary non-causal observation，不能与 Task Designer 输入主指标使用同一因果强度表述。

### Stage 9 canary 前置：autonomous-iteration memory lineage

- 观察到的失败：Mind System 写入的成功/失败记录没有 `project_path`，因此 project reader 的 fail-closed
  项目过滤会排除这些记录；reader 的模型视图又丢失 `timestamp`，compact Task Designer 只能按列表位置选择。
  新记录还只保存 candidate title，标题变化后无法稳定关联 goal ID。Task Executor 失败分支另把
  `failure_context` 作为第六位置参数误传给 `improvement_report`，导致 stage/tool evidence 与 selected candidate
  同时丢失。
- Metadata impact：未新增或扩展 metadata contract；复用 `MemoryRecord.timestamp` 和现有 attributes 中的项目、
  候选 lineage，`ProjectStateSnapshot.memory_records` 与 Task Designer memory candidate 仍是派生视图。历史记录
  保留 title fallback，新 producer 写 canonical project path 与 candidate ID；模型可见 compact source 版本升为
  `task_designer_compact:v2`，无需持久化迁移。
- 实现修复：成功和失败调用都改为命名参数并显式传递 project path/report/failure context；reader 在语义 query
  之外有界保留同项目最近三条以及与当前 goal/query 精确相等的最近三条 autonomous-iteration
  PROJECT/TASK 记录并去重，避免在相关性判断前被其他 goal 的 latest-3 淹没；映射与 compact projection
  保留 timestamp，latest selector 只接受 PROJECT/TASK 类型，优先 candidate ID、历史记录才退回 title，
  并按解析后的真实时间排序。
- 验证证据：真实 `_record_mind_note → MemoryStore → project_state_reader_executor → ProjectStateSnapshot →`
  compact Task Designer candidate 链路测试通过；失败路径验证 canonical path、stage、tool、candidate ID 全部持久化。
  另有真实 MemoryStore 回归验证 global FEEDBACK/LONG_TERM 不能伪装 iteration result，以及四条更新但无关的记录
  不会淘汰当前 goal 的较旧最新证据。最小回归 `103 passed`，compileall 与 `git diff --check` 通过。
- 剩余限制：历史记录若既没有 canonical project path/tag，也没有 candidate ID，继续 fail closed；精确保留只比较
  typed `goal`/`selected_candidate_id`/`selected_candidate` 与当前 goal/query，不做模糊语义推断。

### Stage 9 V2：Task Designer 四场景零-provider gate

- 观察到的失败：Stage 8 只证明了 diagnosis/memory 与选中 goal 无关时的紧凑投影，没有覆盖精确相关、通过完整
  acceptance criterion 部分相关、以及独立相关 iteration memory。初版 Stage 9 fixture 还直接把持久化
  `MemoryRecord.memory_type` 形状放入 `ProjectStateSnapshot`，而真实 reader 给 consumer 的字段是 `type`；生产
  consumer 加入类型白名单后，该夹具被正确 fail-closed，暴露实验没有复现真实 producer-reader 边界。
- Metadata impact：未新增 metadata contract、字段或 owner。selected metric 明细是从既有
  `ProjectDiagnosisMetadata.success_metrics` 按 selected candidate 的精确 `target_metrics` 派生的有界 model-facing
  view；iteration evidence 仍是生产 `MemoryRecord` 经 project reader compact mapping 后的派生视图，无 migration。
- 实现修复：compact diagnosis 只保留 selected candidate、同 dimension assessment 和最多五个精确 metric ID
  对应的 bounded details；四个 fixture 分离为无关、candidate ID 精确相关、完整 criterion 部分相关、独立相关
  iteration memory。Memory fixture 先构造真实 `MemoryRecord`，再执行与生产 reader 等价的
  `memory_type -> type` 映射并经过 `compact_project_memory_record`。离线 runner 使用真实 `ContextAssembler`，冻结
  source/goal/quality-contract/content/decision fingerprints，检查 protected candidates、场景 sentinels、权限目标、
  验证命令和 product intent；Stage 9 协议、说明和结果统一冻结为 V2。
- 验证证据：四场景 current→compact 分别为 1,885→447（-76.29%）、1,898→587（-69.07%）、
  1,908→593（-68.92%）、2,194→605（-72.42%），required/forbidden 候选均完整保留，离线
  `provider_calls=0`。生产全量 `883 passed`，实验全量 `76 passed`，定向联合 `136 passed`；compileall、CLI
  snapshot equality 和 `git diff --check` 通过。
- 剩余限制：稳定 lexical counter 只证明装配机制与相对缩减，不替代 provider tokenizer/真实任务质量。
  compact 仍不是生产默认。下一门是三个正向场景各一对 current/compact 的 6-arm provider sentinel，硬上限
  30k/arm、60k/pair、180k/sentinel；只有全部质量、usage、权限与预算门通过后才允许另行执行反序 6-arm 确认。

### Stage 9 provider sentinel：增强窗口对齐与历史实付预算

- 观察到的失败：首个有效 provider 诊断臂完成核心、质量、usage 和 Task Designer 合同后消耗 13,167 Token，
  却因 fixture-to-final 全程快照把核心阶段创建的 README、`.gitignore`、`sketch.json` 和 `.openpilot` 索引算成
  enhancement mutation 而停止。保留的临时项目时间与 runtime event 显示这些文件在 Task Designer 请求前的
  环境/安全快照阶段写入；门禁的阶段边界错误，不能通过把这些路径加入白名单修正。
- Metadata impact：未新增生产 metadata contract、owner 或迁移。实验 descriptor 增加一次性的 canonical
  snapshot/evidence 字段，仅用于 Stage 9 phase-aligned gate；whole-run diff 继续作为描述性实验事实。预算协议把
  已有诊断 evidence path/hash/token 冻结为实验审计事实，不进入正式六臂 records。
- 实现修复：首次进入 production Task Designer builder、且在 candidate build/provider transport 前捕获有界项目
  快照；重复进入只允许相同指纹，缺失、capture count 非一、漂移、truncated 或 symlink 全部 fail closed。run
  结束后以该边界生成 `observed_enhancement_mutations`；Stage 9 calculator-only 门禁只读取它，原
  `observed_project_mutations` 保留为 descriptive。`.openpilot` 未排除，增强窗口内若有真实变动仍会明确失败。
  retry1 的 13,167 Token 以 campaign state/record SHA-256 冻结并由 preflight 校验，pair 1 初始剩余 46,833；
  state 分别报告 prior/formal/cumulative，所有安全上限按 cumulative 计算。零 Token 的首次诊断也保留审计引用。
- 验证证据：新增 post-core 边界、缺失、重复一致/漂移、truncated、symlink、whole-run core artifact 与增强期
  `.openpilot` 变动、历史实付 pair/campaign 累计预算红测；实验全量回归 `138 passed`，compileall、只读
  preflight 与 diff check 通过，`provider_calls=0`。
- 剩余限制：旧 retry1 没有当时的边界快照，不能把它事后升级为正式样本；停止目录均原样保留。新正式
  campaign 仍须从新目录完整启动，并对增强窗口内 `.openpilot` 变动做内容级 ownership 验证。

### Stage 9 provider sentinel：runtime-owned mutation 窄分类

- 观察到的失败：phase-aligned retry2 首臂质量与 usage 通过并消耗 13,373 Token，但增强窗口除
  `calculator.py` 外还刷新四个 `.openpilot/file_indexes/*.index.json` 和根 `sketch.json`。旧门禁仍把所有 diff
  都当作用户 mutation，因而停止；直接排除 `.openpilot` 或按 basename 白名单会隐藏伪造索引与路径穿越。
- Metadata impact：无生产 metadata contract 或 owner 变化。实验 record 保留权威的完整
  `observed_enhancement_mutations`，并派生 `runtime_owned_mutations`、`user_owned_mutations` 与 classification
  failures；派生视图不替代原始 hash diff，也不进入生产控制流。
- 实现修复：runtime-owned 仅接受根 `sketch.json` 与 `.openpilot/file_indexes` 下映射到现存项目文件的
  sidecar；逐文件解析 JSON 并严格核验 kind、`system/openpilot` source、canonical project root/directory、无
  traversal relative path、目标文件、`file_path`、`index_file`、sidecar mapping 与 symlink 边界。未知
  `.openpilot`、恶意 relative path、JSON/schema/source/path mismatch 全部 fail closed；user-owned changed set
  必须严格等于 `calculator.py`。retry2 记录原样保留且不进入正式样本，13,373 与 retry1 的 13,167 一并冻结为
  prior paid：累计 26,540，pair 1 剩余 33,460，campaign 剩余 153,460。
- 验证证据：真实 retry2 临时项目的五个 runtime artifact 全部通过内容分类，唯一 user-owned 为
  `calculator.py`；正常分类/门禁及 unknown path、bad JSON/kind/source/root/relative mapping/file path/symlink
  均有离线测试。实验全量 `149 passed`，compileall、只读 preflight、diff check 通过，`provider_calls=0`。
- 剩余限制：只承认当前生产确实生成且可强校验的两类 artifact；未来新增 runtime-owned 文件类型必须带真实
  证据和独立协议变更，不能泛化为 `.openpilot` 全目录可信。

### Stage 9 provider sentinel：独立 NO-GO 审查加固

- 观察到的失败：独立审查证明上一版仍可伪造证据：raw descriptor 没有强制 `all=added∪deleted∪modified`、
  category disjoint/去重/canonical/root-contained；攻击者还能在增强期新增一个自报 OpenPilot source 的 sidecar，
  或伪造 index hash/size/line、向 sketch `files` 注入任意 payload。另一个协议身份缺口是 parent 可校验内存中的
  custom protocol，但子进程固定加载默认 protocol path。
- Metadata impact：不改生产 metadata。实验使用现有严格 `FileContentIndexMetadata`、
  `FileContentSectionMetadata`、`DirectorySketchMetadata` 作为 validator；scope descriptor 增加从增强开始快照派生的
  expected artifact manifest，record 增加 `producer_validation`。原始 diff 仍是权威观察，派生分类不覆盖它。
- 实现修复：分类前强校验 raw 四集合的 union、互斥、无重复、canonical 与 root containment。runtime-owned 路径
  必须在首次 production builder 入口已存在并冻结，禁止 add/delete；final payload 通过 extra-forbid typed model，
  index 的 content SHA-256/byte size/line count/language/sections 从最终目标文件重建核对，sketch 通过生产 index
  policy 枚举最终 root 文件、逐个验证 index 并精确重建完整 `files` mapping。只忽略 sections 的 created_at 与
  correlation，字段集合仍严格。arm gate 重新计算并核对 producer validation。runner 显式下传 parent 实际
  protocol path，object/file 不一致在创建 campaign 前拒绝。
- 验证证据：将独立审查的 union mismatch、category overlap、duplicate、noncanonical、root escape、runtime add、
  非 start-frozen path、unknown field、类型 coercion、伪造 hash/size/line、sketch extra payload、symlink 和 protocol
  split 全部转为离线回归；真实 retry2 五个 runtime artifact 仍能由 final project facts 完整重建通过。实验全量 `166 passed`，
  compileall、只读 preflight、diff check 通过，`provider_calls=0`。
- 剩余限制：当前 production sketch 仅覆盖项目根目录，所以 validator 也只重建根 sketch；未来若生产开始持久化
  子目录 sketch，需要先扩展 start manifest 与对应目录级 bounded reconstruction，不能自动放宽。

### Stage 9 provider sentinel V1：runtime contract 漂移停止

- 观察到的失败：retry3 完成首个 strongly-related current/compact 对后，两个臂的独立 arm/quality/permission/
  usage 门都通过，但 pair gate 发现 runtime contract hash 不同并停止。逐字段重建两个 hash 后确认仅
  `file_summaries[0]` 不同：core 阶段分别生成 245/261 chars 的 `calculator.py`，异常消息分别为固定文本和包含
  denominator 的 f-string。goal、project path、safe targets、validation 与 prompt-context safety 完全一致。
- 原因与处置：Stage 9 干预直到 Task Designer builder 才生效；两次独立完整 provider 运行在更早的读取和代码
  生成轨迹已随机分叉。该漂移不是 compact policy 造成，但 file summary 是 Task Designer 的直接输入，不能在看见
  结果后放宽 exact hash 或做语义归一化。V1 按冻结的 stop-on-first-failure 终止，后四臂不再运行；结果只作描述性
  机制信号，不作因果 A/B。
- 验证证据：current/compact Task Designer provider input 为 2,805/1,644（-41.39%），assembled prompt 为
  2,780/1,619（-41.76%），output 为 171/140；两臂 lifecycle 共 24,851 Token。计入 retry1/retry2 后 Stage 9
  累计实付 51,391，原 180,000 上限剩余 128,609。campaign state 与两臂 record SHA-256 分别为
  `080dc4ac...f696`、`9a8af670...9f5a`、`c8c4b5d8...d56c`，原目录不删除、不重写。
- 剩余限制：compact 尚不能切为生产默认。下一实验必须在一次 core run 的唯一 post-core 边界冻结同一 live
  state/goal/report，从该不可变值同时构造 current/compact；一个结果进入生产下游，另一个只作无副作用 shadow
  质量检查，并跨运行交换 production 角色。该修复属于实验编排层，不需要产品 metadata 或上下文策略变更。

### Stage 9 V2：同运行 paired shadow 第一臂与观测器校正

- 观察到的失败：首个 V2 尝试消耗 12,790 Token 后，实验 harness 把模型返回的 improvement goal ID 当成
  context candidate ID 不匹配并提前判成未授权；生产 `_coerce_task` 实际会确定性过滤该引用。修复为 target/schema
  权限严格拒绝、evidence provenance 精确交集过滤并单独记录，同时提取单一 primary stop reason；预算账本显式计入
  该失败臂。重跑完成 production+shadow 与下游改进后又只报 `paired_max_completion_mismatch`。
- 原因与修复：production 与 shadow 的真实 request/diagnostics `max_tokens` 均为 1,000；shadow 按设计不占第二份
  产品 completion reservation。observer 错把 production-only `completion_budget.reserved_tokens` 当作请求上限，故把
  shadow 记为 0。现改为从 `trace_info.diagnostics.max_tokens` 读取请求上限，并把 product reservation 单独审计。
  Primary stop 仅抑制缺臂派生噪声，per-run、per-scenario、campaign、source drift 与 Guard hard gates 始终执行。
- 验证证据：修复后实验全量 `292 passed`；用原始 immutable manifest/events 零 provider 调用重建 record，
  `arm_stop_reasons=[]`。第一臂 quality 9/9、usage 10/10、无 retry/censor/overrun，唯一 user mutation 为
  `calculator.py`，docstring 与 pytest/compileall 均通过。current/compact provider input 2,838/1,659（-41.54%），
  assembled prompt 2,813/1,634（-41.91%），输出语义等价。原 state/record/manifest hash 与 bounded reanalysis 已冻结在
  `STAGE9_TASK_DESIGNER_PAIRED_SHADOW_V2_FIRST_ARM_REANALYSIS.json`。
- 剩余限制：两臂都引用了 goal-domain ID，context candidate provenance 被过滤为空；该共同缺陷不影响相对输出质量，
  但后续应改善 ID 可见性。Compact 本臂仍是 shadow-only，不能据此切换生产默认；还需完成反转 production role 与其余
  两个场景，并继续按新增的 12,790 + 14,950 实付更新 Stage 9 lifetime 账本。

### Stage 9 V2：六臂 paired-shadow 完成与受控切换边界

- 观察结果：三个上下文相关度场景均完成 current/compact 生产角色反转，6/6 arm 无停止原因，12/12
  Task Designer 请求完成，6/6 完整架构质量门通过。Compact 在六个配对中均降低总 Token：provider input
  17,262→9,702（-43.80%），provider output 1,086→1,054（-2.95%），provider total
  18,348→10,756（-41.38%）。收益主要来自上下文投影，而非压缩模型推理或输出。
- 安全与账本证据：独立审计确认 60 个 lifecycle execution ID 全部唯一，shadow 未被下游消费且未改变项目、
  memory 或预算状态；六臂 user-owned mutation 均仅涉及 `calculator.py`，验证和 mutation classification 全过。
  Arms 2--6 新增实付 74,929；连同已包含首臂 prefix 的 opening ledger 79,131，Stage 9 最终为
  154,060 / 180,000，剩余 25,940，无 unknown usage、retry、reservation 或 hard-limit 异常，且 resume
  seed 未重复计费。
- 决策：独立安全审计给出 conditional GO。Compact 仅具备成为 `iteration_task_design` 受控 production
  default 的资格；上线必须使用 feature flag，保留 Current fallback、kill switch、typed candidate gate、usage/
  quality telemetry 及现有 mutation/verification 控制。不得据此切换全局上下文默认、移除 Current，或同时改变
  reasoning/completion policy；单一 provider 与 calculator/docstring 任务也不能外推到其他模型和任务族。
- 剩余限制：六个 production 输出的原始 evidence IDs 均是 goal-domain ID，因而被 fail-closed provenance
  filter 拒绝。该问题未扩大 typed authority，也不是 Compact 特有退化，但 evidence-link 可审计性尚未解决。
  下一阶段应先区分 context-candidate ID 与 goal-domain ID 并增加契约测试，再进行小流量 Task Designer canary；
  扩展任务族/provider 与 reasoning/completion 策略应作为独立实验。

### Stage 9 V2 后续：Task Designer evidence identity 契约消歧

- 观察到的失败：六个 production Task Designer 输出都引用了 goal-domain ID，而不是 request header 中的
  context-candidate ID；运行时按 retained candidate 集合正确 fail closed，导致六次 accepted provenance 为空。
  原 schema 仅写“candidate id from this request”，但 prompt 正文同时可见 goal ID、diagnosis candidate ID、
  source ID 与 `[evidence_id="..."]`，模型无法从字段契约区分身份域。
- Metadata impact：复用现有 `DesignedImprovementTask.evidence_ids`，其权威 producer 仍是 Task Designer 输出经
  runtime retained-candidate filter 后的值，consumer 仍是 autonomous-iteration result 与 trajectory audit；生命周期、
  序列化和历史读取不变，控制影响仍为 evidence/audit，不授予任务、目标或修改权限。已审查
  `ImprovementAnalysisMetadata.evidence_ids`、context candidate identity 和 catalog；不新增字段、模型、
  `MetadataKind` 或第二份证据事实。
- 实现修复：instruction 与 JSON schema 明确要求 evidence IDs 只能逐字复制 `[evidence_id="..."]` header；goal、
  diagnosis candidate、task、source 及正文内部 ID 均不得作为 evidence。Runtime 继续精确过滤未知 ID，不放宽
  authority，也不根据字符串前缀猜测或转换身份。
- 验证证据：新增契约可见性测试及“合法 header ID 与两个 domain ID 混合返回”过滤测试；修复前红测失败，
  修复后 Task Designer delta、context policy 与 assembly 联合 `77 passed`。单臂完整架构 sentinel 以 Compact
  production、Current shadow 运行，15,420 / 20,000 Token，quality 9/9、`arm_stop_reasons=[]`；production
  raw/accepted/rejected 为 4/4/0，shadow 为 5/5/0，两个输出均只引用 header ID。随后用 4,000 Token 硬上限的
  单请求探针检查去重方案：仅保留 schema 完整规则、把 instruction 缩为短句时，模型再次返回 goal ID，
  accepted 0/1，实付 890 Token。该反证说明当前 provider 对 instruction 与 schema 双重显式约束敏感，因此恢复
  完整强契约；这增加约 72 input tokens/request，但相对旧 Current 仍保留约 40% 的 paired input 降幅。最终
  Code 全量 `885 passed`，compileall、结果 JSON 校验与 `git diff --check` 均通过。

### Context Phase 6：分段 compact 与安全硬化

- 观察到的失败：完整架构观察确认调用次数下降时总 Token 仍会因累计历史和重复大型 tool observation 上升。
  第一版 segmented compact 的独立审查又复现了七个边界问题：恢复投影 denylist 可漏出 `env` secret；控制字段
  可能被整块 mask；可截断 compactor 产生 selection 不一致；compactor 环可生成 ready 空 Prompt；user constraint
  被误当 observation；短 prefix 可抛 compaction 校验错误；v4 snapshot 在 v5 request 下可静默从变化后的 memory 重建。
- Metadata impact：复用 `ContextCandidate`、`ContextCompactionRecord`、`ContextCompactionBinding`、
  `DurableArtifactReference` 和 `RuntimePromptContextSnapshot`，仅向 algorithm literal 增加
  `deterministic_observation_mask_v1`；旧 `deterministic_dialog_extract_v1` 保持可读。不新增 `MetadataKind`，raw
  dialog/tool error 仍为权威事实，exact prompt artifact 仍为 replay 权威。
- 实现修复：tool recovery 改为显式 safe allowlist，秘密/unknown/attributes/runtime handles 不提交也不 hash；
  大型生成和观察字段确定性 mask，路径、命令、operation、symbol、mode、system/instruction 保持。Compactor 强制
  artifact+FORBIDDEN，禁止 required/nested/cycle，并以最多 C+1 轮的迭代原子回退恢复 source。Memory 仅压缩旧
  assistant observation，短段无收益时 no-op，adapter 升级 v5。v4→v5 pending replay hash mismatch fail closed，
  exact replay 成功后关闭一次性 replay gate。
- 验证证据：Stage 10 使用生产 builder 的 10/20/50 段零 provider 三臂结果为 full
  13,013/28,729/75,979 chars、recent-only 1,600/1,600/1,600、segmented 814/815/815；相对 full 最小
  降幅 93.74%，相对 select 约 49.1%，20→50 增长 0%。语义槽、权限签名、required/current failure、精确验证
  命令、source lineage、稳定 hash/changed-source 全过，未绑定 omitted source 会 fail closed。Stage 10 `8 passed`，
  Code 全量 `902 passed`，compileall 与 diff check 通过。
- 剩余限制：离线 corpus 是 synthetic 且按字符计量，尚未准入 provider/真实任务；旧 user dialog 虽不会再被
  误标 compacted，仍可能由基础预算策略作为非 required 历史消息省略。下一阶段应先把持久用户约束投影成 typed
  required candidate，再讨论更广泛语义 summarization。Stage 9 五个 frozen snapshot preflight 失败未被静默重冻。

### Context Phase 7：对话内 Session Constraint State

- 观察到的失败：旧 user 消息不会被 segmented compactor 当作 assistant observation 压缩，但在固定预算下仍可能
  直接省略；因此“只能修改某文件、不得改 README、必须运行精确验证命令”等持续有效约束会在长对话中消失。
- 原因与边界：原始对话继续是事实来源，普通历史、assistant 建议、summary 和 compact artifact 均不能产生运行时
  权威。约束需要经过 user-only 的显式模式提取、确认、冲突/同 key supersession、撤销和会话身份校验，才进入
  `RuntimeStateMetadata.session_constraints`。它是收窄视图，不复制 `TaskGraphNodeMetadata.write_files`、验证命令
  或 `RuntimeExecutionMode` 的权威。
- 实现修复：新增严格嵌套 metadata 与 reducer 生命周期；active entries 投影为一个 source/hash-linked、required、
  FORBIDDEN-truncation candidate，并将 state hash 纳入 request/replay identity。统一接入 controller prepare gate、
  normal tool-event guard、fast mutation path 和项目改进 Context Loader 链路；跨 session/project、只读命令、越界
  文件和错误验证命令 fail closed。
- 验证证据：相关元数据、reducer、context、runtime 与 pipeline 回归通过；阶段 5 三臂离线回放（10/20/50 messages）
  zero provider/network/mutation。无 state 臂在三种长度均丢失早期精确验证和写范围；with state 约束召回 100%、
  assistant-origin authority acceptance 0%，2,200-char compact prompt 在 20→50 增长 0%。assistant-only noise 保持
  state hash 稳定但改变 prompt hash；用户约束 revision 改变 state hash。
- 剩余限制：当前回放是 deterministic synthetic corpus，不能推出 provider Token 或真实任务质量收益；API compatibility
  目前仍是提示/证据约束而非 diff checker。下一步若做 canary，必须另行批准、保持 feature flag/kill switch，并先补
  provider 与多任务族的离线/影子证据。
### Context Phase 8：Stage 6A/6B post-core full-session canary runner

- 观察到的边界：原 Stage 16 Provider runner 只覆盖 Task Designer request boundary；它创建独立的 synthetic
  ingress/project snapshot，又调用另一套 full-entry admission，因此不能证明同一 raw SessionIngress、项目快照和
  checkpoint 贯穿 ContextLoader、project-improvement analyzer、Goal Maker 与 Task Designer。
- 原因与范围：生产链已传递完整 `SessionIngressState` 到 post-core project-improvement pipeline，但 core semantic
  analysis/decomposition 与 downstream execution 仍没有同一 raw-dialog adapter。Stage 6 因此明确使用
  `full_session_post_core_context_canary` claim boundary，不把它扩大为整个 `execute` 质量证据。
- 实现：新增 `stage6_full_session_canary.py`。每次运行只建立一个 immutable source bundle（turn-ledger、constraint、
  project-manifest/source hashes），复用 RuntimeController ingress/checkpoint 生命周期和 read-only/strict ContextLoader，
  再进入 production analyzer、shared Goal Maker 及 compact/current Task Designer。shadow 输出不进入 executor；项目和
  memory before/after manifest 必须相同。真实 Provider 默认关闭，显式 campaign state path 才可启用，并以 Stage 12
  flags/kill switch/ledger/hard caps 和 Stage 7C typed attempt receipt 记录 usage、finish、reasoning 与 hash lineage。
- 验证证据：Stage 6 runner dry-run、fake Provider pair 和已知 usage 的一次 current fallback 均通过；新增实验回归
  **4 passed**，与 Stage 7C/7B-3b 选择性回归合计 **15 passed**。dry-run 为 0 Provider/0 network/0 mutation，4 次仅本地 deterministic model calls；fake pair
  为 4 次已观察 usage，4/4 ledger observations，compact/current 两臂均通过目标路径与 acceptance contract。
- 剩余限制：当前 fixture 的 compact/current Task Designer 请求在无 diagnosis 历史时可能同长；这验证的是入口与安全契约，
  不是收益结论。Stage 6C 需先用真实 checkpoint compaction evidence 和足够的历史证据完成 dry-run gate，再由用户显式
  启用低风险 Provider；fallback once、跨 campaign source identity envelope、reasoning 策略及 core/decomposer 覆盖仍是
  后续独立阶段。

### Context Phase 8：Stage 6D 真实 Provider 停止与下游投影缺口

- 观察结果：真实配置 `deepseek-v4-flash` 的第一组 post-core canary 未进入下游 Goal/Task；Provider 在
  `project_improvement` 分析边界返回 `InvalidLLMResponseError`，本次证据为 1 个已观察失败 attempt（约
  2,886 input / 446 output，`finish_reason=stop`），随后 fail closed。没有项目/memory mutation；checkpoint 仍保留
  同一 turn/constraint hash 和 1 个 durable compaction artifact。此前一次 retry 变体也只产生已知 usage 的失败 attempts，
  没有把 unknown usage 当作 0。
- 根因信号：ContextLoader 已将 9 条 raw turn 压成 5 条选中内容并写入 compaction artifact，但下游
  `project_improvement_tool_executor`、Goal Maker 和 Task Designer 仍从 `SessionIngressState` 重新构造 raw dialog 候选，
  没有消费同一 `memory_context`/compaction projection。也就是说 compact 生命周期已经存在，但跨 agent 的派生视图没有
  统一装配，真实分析请求仍携带长 assistant 历史；这不是 Provider reasoning 策略结论。
- 处置：停止继续真实 Provider 扩样；保留 campaign ledger/receipt 作为失败证据，下一阶段先实现带 source/hash lineage 的
  derived context projection bridge，让 analyzer/Goal/Task 消费 ContextLoader 的选择/compact 结果，同时保留 raw turns 为
  唯一事实源。修复前不重新解释这组失败为质量回归，也不降低 hard cap 或改用估算 token 越过门禁。

### Context Phase 8：Stage 6E ContextLoader 派生视图桥接

- 观察到的缺口：ContextLoader 已完成 durable compaction，但 downstream analyzer、Goal Maker 和 Task Designer 仍可从
  `SessionIngressState` 重新生成 raw dialog；仅共享 turn/source hash 不能证明它们消费了同一个 selected/compact view。
- 修复：在现有 `ContextAssemblyResult`/`ContextCandidate` 之上增加严格嵌套的 `metadata.DerivedContextProjection`，并让
  compatibility payload 暴露 selected-only typed candidates、request/turn/constraint hashes。bridge 验证 ready 状态、选中候选
  与 decision 覆盖、session constraint 保留、ingress dialog source、compaction binding/summary/fingerprint；下游把 compact
  artifact 当 bounded evidence，不把被压缩 source IDs 交给第二个 assembler。无 projection 的 legacy/current control 路径仍显式
  走原始 dialog，不能静默冒充 compact arm。
- 验证：新增 projection stale/incomplete/selected-only 契约测试；Stage 6 dry-run/fake Provider 回归通过。fake pair 的
  analyzer/Goal/compact Task 共享同一 compaction candidate；compact Task 选中约 2,830 tokens，current control 选中约 2,912
  tokens，且 current 无 compaction candidate、保留 `session_dialog:*` raw source。Stage 6 相关测试与上下文/运行时 focused
  回归通过（当前 focused 集合 74 passed）。
- 剩余限制：这只是下游装配收益和 lineage 的离线/假 Provider 证据，不是跨任务族、跨 Provider 的真实质量结论；真实 Provider
  复测仍需新 campaign、同一 source envelope 和 fail-closed quality gate。selected-only compatibility field 对历史 checkpoint
  采用兼容回退，历史快照无法提供 compaction binding 时不能声称有新的投影证据。

### Context Phase 8：Stage 6F 真实 Provider 复测停止

- 观察结果：使用新 source-bound campaign，ContextLoader checkpoint compaction、turn hash、constraint hash 和零 mutation
  门均通过；真实 Provider 在 `project_improvement` analyzer 停止，未进入 Goal/Task。两个已知 usage attempt 均为
  `input=2,883`，`finish_reason=length`，completion 分别为 `600` 与一次受剩余预算约束的 `900` recovery；两次均保留了
  receipt、reservation、reconciliation，`unknown usage=0`，project/memory/network mutation after admission=0。
- 原因信号：返回内容不是 bounded JSON delta，而是带 `[evidence_id=...] ASSISTANT:` 的历史/证据文本；请求投影检查确认
  selected assistant dialog 是最后一条 provider `assistant` message，且没有 trailing user contract，模型实际续写了历史
  assistant evidence。两个 completion 值恰好打满 600/900，说明预算是可观察的 cap-hit 信号，但不是首要根因；compact
  source identity、约束和权限门均通过。当前 2,883 input 与离线 compact/current Task 的差异不能直接外推 analyzer 收益，
  且 analyzer 失败使下游 paired quality 无法观测。
- 处置：按 quality gate 停止，不扩大真实 Provider 样本、不调低 hard cap、不把 recovery 的第二次尝试当作成功。canary 现已在
  failed attempt 上记录 request purpose、selected candidate IDs、compaction/dialog IDs 和 rendered input tokens，便于下一阶段
  对照响应 schema、reasoning resolution、completion reserve 与重试语义。
- 剩余限制：尚未区分 provider 的 JSON-mode/思考输出行为、schema 提示位置和预算不足各自贡献；需要独立的 analyzer response
  contract probe，先用离线/fake provider 固化证据，再决定是否做模型通用的路由或窄 schema 修复。

### Context Phase 8：Stage 6G/6H analyzer 输出契约修复

- 修复决策：不改变全局 `ContextRequestBuilder` 的 role-preservation 语义，也不先扩大 reasoning/完成上限；在 project-improvement、
  iteration-goal、iteration-task-design 三个 purpose-specific candidate builders 中追加一个 typed required terminal user
  contract。它是已有 schema/instruction 的末端 framing，明确要求只返回一个 JSON object、不得续写或引用 dialog；source order
  固定在末端，纳入正常 `ContextSelectionMetadata` 预算和 evidence accounting。
- 离线证据：focused context/pipeline/Stage6 回归通过（34 passed）；fake capture 显示 analyzer/Goal/compact Task/current
  Task 的最后 message 均为 `user`，且内容为 terminal output contract；原始 assistant dialog 的 source/role 证据仍保留在
  content/selected-candidate lineage 中。未改变 reasoning policy、hard cap、fallback 或 raw ingress authority。
- 下一步：使用新的 source-bound campaign 复测一次真实 Provider。若 JSON 合法且不再 length-stop，维持现有 completion ceiling；
  只有在 terminal framing 修复后仍出现合法 JSON 的 completion cap-hit，才单独评估 project-improvement purpose ceiling。

### Context Phase 8：Stage 6H 复测后的新信号

- 结果：terminal framing 修复后，analyzer 首次响应不再是 assistant-history echo；它返回空 content、`finish=stop`、172 output
  tokens，随后 fail-closed。Goal Maker 进入了 STANDARD complexity 的 provider-default reasoning 路由，两个 attempt 分别使用
  920/1,200 reasoning tokens 并以 `finish=length` 停止；全程 usage 已知、无 mutation、source/constraint/compaction lineage 一致。
- 解释边界：这证明 role framing 的旧根因已消失，但还不能把空响应归因于 compact 或把 Goal cap-hit 归因于全局 reasoning
  策略；当前 helper 的明确语义是 routine→configured disabled，non-routine→provider default，Goal STANDARD 正好走后者。
  下一阶段要分别探查 empty-response contract、provider-default reasoning 的实际 transport resolution，以及是否应把单目标 Goal
  决策标为 routine；不在本阶段隐式改变模型路由。

### Context Phase 9：Stage 7B 零 Provider 门补强与 Stage 7C full-session canary

- Stage 7B 补强：实验层新增 `OfflineContextLineageReceipt`，把 source snapshot、raw turn digest、active constraint digest、
  `read_only` 环境和 typed write-scope digest 作为 current/compact/fallback 的同源证据；Stage 12 preflight 回传并由 Stage 15
  校验 feature flag、kill switch、`status` 和 `controls_admitted`。Stage 7B-3b/13/14/15/12 定向组合 **31 passed**，
  Provider/network/project/memory mutation 全为 `0`。
- Stage 7C 真实结果：在新的 source-bound campaign `runs/stage7_full_session_canary_v3/` 下，ContextLoader/derived projection/
  analyzer/Goal 走通，3 次 Provider attempt 均保存完整 usage；analyzer 为 `2,902 input / 179 output / stop`，Goal 首次和 recovery
  为 `2,987 input / 920 output / 920 reasoning / length` 与 `2,987 input / 1,200 output / 1,200 reasoning / length`。无项目或
  memory mutation，质量门在 Goal 空/无效 JSON 后停止，Task Designer compact/current 未执行。
- 根因边界：terminal user contract 已消除 analyzer 的 assistant-history echo；本次新失败是 Goal `STANDARD` 走 provider-default
  reasoning，推理耗尽 completion ceiling，不能归因于 compact 投影或约束丢失。由此暂停扩大 real canary，先做独立的 provider-neutral
  reasoning complexity A/B，冻结 context/schema/ceiling，避免把两种收益混为一个实验。

### Context Phase 9：Stage 7E reasoning complexity isolation

- 实现：新增 provider-neutral `ReasoningDecisionComplexity`，并在 `core/reasoning.py` 增加纯 resolver；它与
  `EnhancementCompletionComplexity` 分离，因此实验切换 reasoning 不会改变 completion reservation。Goal owner 可显式传入
  route；默认生产调用保持原有 STANDARD→provider-default 行为。Code 全量 **970 passed**，reasoning route/fake capture 定向
  **51 passed**。
- 真实 A/B：`runs/stage7e_goal_reasoning_canary_v2/result.json` 复用同一 source snapshot、候选和 schema；两臂 initial
  `max_tokens=840`、rendered input 均为 1,805。ROUTINE/disabled 一次返回合法 Goal（input 1,834、output 97、finish stop）；
  STANDARD/provider-default 在 1,912 input 后 output 1,140、reasoning 1,140、finish length，包含一次 bounded recovery 后仍无效
  JSON。共 3 次 Provider call，usage 全部可观测，project/memory mutation 为 0。
- 解释边界：这是一个 mechanism sample，不能外推到所有 Goal、任务族或 Provider；但它支持“单一有界 Goal 先走 routine、复杂/冲突决策保留
  provider-default”的下一步假设。下一阶段应做至少三组交错 pair 的质量复核，再决定是否切换生产 Goal 路由；compact 策略在此期间保持冻结。

### Context Phase 9：Stage 7F-1 三组交错 reasoning 复核

- 实验器先发现并修正三个观测问题：标准臂的 bounded `length` recovery 不能计入新的 treatment ceiling；recovery 失败时不能用最后异常
  覆盖首次 Provider attempt 的 usage；Token cap 不能只累加最终 recovery receipt，必须累加每个 attempt，unknown total usage 则 fail closed。
  当前 receipt 固定 initial `max_tokens`，逐 attempt 记录 usage、finish reason 和 error type，并额外记录 requested/effective reasoning policy
  与 capability profile；每组最多 3 次调用，总实验 cap 为 30,000 aggregate tokens。
- 修正后真实结果：`runs/stage7e_goal_reasoning_canary_v5/result.json`。三组 routine 均 requested `disabled`、已知 DeepSeek profile 下
  effective exact、一次调用、合法 Goal、`finish=stop`；standard 初始 `max_tokens=840`、requested `provider_default`，第 1/3 组首次
  `finish=length`，第 1 组 recovery 仍 `length` 导致无效 JSON，第 3 组 recovery 成功，第 2 组一次成功。总 Provider/network calls 为 8，
  aggregate attempt tokens 为 19,740（input 15,110、output 4,630；reasoning 因 routine unknown 保持 null），project/memory mutation 为 0。
- 结论边界：该 Goal 形状的 routine quality 为 3/3，但 standard baseline 为 2/3，故阶段状态为 treatment passed、baseline quality gate stopped，
  不能改变生产默认，也不能推广到未知 capability profile。它足以支持下一步仅在 feature flag、kill switch、同源 lineage、约束召回和完整质量门下，
  将 routine Goal 接入一次 full-session compact/current canary；该 canary 的上下文与 Task Designer 证据仍是最终依据。

### Context Phase 9：Stage 7F-2 routine Goal full-session compact/current canary

- 真实 campaign：`runs/stage7_full_session_canary_routine_v1/`，feature flag `canary_enabled`、kill switch `armed`、
  `read_only` 环境、routine Goal treatment 均锁定；ContextLoader、analyzer、Goal、compact Task 和 current Task 全部通过，
  4 Provider/network calls，usage 全知，project/memory mutation 为 0。
- lineage/约束门：9 条 raw turn 中 ContextLoader 选 5 条，保留 `session_constraints:*` required candidate 和 1 个
  `compaction:*` artifact；source snapshot、session-turn hash、session-constraint hash 与 checkpoint 三者一致。compact/current 两臂
  都生成只指向 `calculator.py`、带 `python -m pytest -q` 验证条件的 authorized task。
- paired context 结果：compact Task rendered/provider input 为 **2,857/2,892**，current 为 **2,940/2,974**，减少 **83/82 tokens**（约
  2.8%）；Provider total 为 **3,064 vs 3,169**，减少 105（约 3.3%）。compact 选择 compaction artifact 且不选择 raw
  `session_dialog:*`；current 不选择 artifact 并保留 raw session dialog。两臂 quality 均通过。
- 解释边界：这是当前 fixture 和已知 DeepSeek capability profile 下的第一条完整真实收益证据，证明 compact projection 已跨越
  analyzer→Goal→Task 并产生可测的输入下降；收益幅度仍小，不能外推到更长历史、其他任务族或未知 Provider。生产默认 reasoning/compact
  路由保持不变，下一阶段需另行审查多任务扩样，并保留 current fallback 的 kill-switch 路径。

### Context Phase 9：Stage 7G 完成审计

- 生产代码回归：`PYTHONPATH=Code/src pytest -q Code/tests` 为 **970 passed**；本阶段上下文/会话/实验定向集合为 **78 passed**；
  `compileall` 与 `git diff --check` 通过。
- 证据门复核：SessionIngress raw turns 仍是权威源，required session constraints 通过 checkpoint/selection 保留，compaction 只作为
  derived artifact；full-session routine campaign 的 analyzer、Goal、compact/current Task、usage、quality、permission scope、
  feature flag、kill switch 和 zero-mutation 门均通过。没有把 routine route 切成生产默认，也没有把 unknown reasoning usage 当成 0。
- 仓库级实验 harness 直接从根目录收集时仍有 6 个既有 snapshot/包路径失败（Stage 9 frozen offline report 与当前未冻结工作区不一致，及
  一个 3967/3968 token fixture 差一）；它们未进入本阶段定向证据，不能被 970/78 的通过数掩盖。应在单独的 Stage 9 fixture refresh
  计划中处理，不能覆盖历史冻结结果来伪造通过。
- 当前 goal 的实质完成边界：上下文控制已从“架构/离线证明”进入一条可复核的真实收益路径，但收益仅为当前 fixture 的约 2.8% Task
  input reduction；下一步是多任务/更长历史的独立 canary 扩样，而不是现在扩大生产流量或自动切换 reasoning 默认。

### Context Phase 10：Context governance enhancement Stage 0 audit

- 目标：在进入 LLM-assisted compaction、持久约束边界和 reasoning profile
  实现前，完成现状、契约所有权、主流实现和实验边界盘点。
- 观察到的信号：deterministic segmented compaction 已具备 artifact/source
  lineage 和原子回退，但 legacy `ContextCompressor` 仍是无生产 caller 的
  自由文本 summary；session constraints 的基础状态已扎实，但 CLI 命令
  路由、agent_generator ingress、API/acceptance runtime gate、stale proposal
  和 quota/expiry 仍有缺口；reasoning 已有 typed policy/profile，但仍存在
  model-prefix capability inference，且 native provider adapters/observed
  reasoning normalization 不完整。
- 处理决策：新增 Phase 10 分阶段计划；summary 复用
  `ContextCompactionRecord`/`ContextCompactionBinding` 并保持 raw source
  authority；constraint 继续归属 `SessionConstraintState`，不写入长期 memory；
  reasoning 继续使用 provider-neutral intent + explicit versioned profile，
  不把 Compact、constraint 和 reasoning 同时放入一项因果实验。
- 验证证据：三条只读审计完成；未调用 Provider、未产生 network/project/memory
  mutation。阶段计划和 metadata impact notes 已写入
  `docs/context_management/PHASE_10_CONTEXT_GOVERNANCE_ENHANCEMENT_PLAN.md`。
- 剩余限制：Stage 9 frozen snapshot/fixture mismatch 和 context README 阶段
  表滞后尚未修复；Stage 1 必须先写独立 compaction quality plan 和 baseline
  gate，再开始任何 summary runtime 代码。

### Context Phase 10：Stage 1 LLM summary contract and offline quality gate

- 阶段计划：先写 `docs/context_management/PHASE_10_STAGE_1_LLM_SUMMARY_PLAN.md`，
  明确 legacy `ContextCompressor` 不得直接接生产、summary 只能覆盖旧的
  non-required assistant/tool observation、required state 和 recent suffix 不受
  summary authority 影响，并记录 metadata impact note。
- 实现修复：新增严格嵌套 `ContextCompactionSummary`，只允许 goal delta、verified
  facts、decisions、open issues、source evidence IDs 和 next action；扩展现有
  `ContextCompactionRecord` 读取 `llm_rolling_summary_v1` 的可选 versioned
  payload/token evidence，同时保持 deterministic v1-v5 历史记录可读。新增
  `memory.compaction_summary` 纯校验 helper，拒绝 authority 字段、unknown
  evidence、empty/over-budget/unknown-usage summary，并提供 required/recent/schema
  reserve 后的 bounded summary budget 计算。
- 验证证据：新增 3×3×4 history/relevance/purpose 离线矩阵和 failure fixtures；
  summary contract 定向 `43 passed`，上下文/会话/恢复 focused `105 passed`，
  Code 全量 `1013 passed`，`compileall` 与 `git diff --check` 通过。全程未调用
  Provider、network、project、memory 或文件 mutation。
- 出口判断：Stage 1 的 contract/fixture/quality gate 已通过；production summary
  caller 仍为 0，Compact flag、Current fallback、reasoning 和 completion policy
  均未改变。Stage 2 必须另写 rolling-summary runtime 计划后再实现。
- 剩余限制：当前只验证结构化 contract 和 bounded fake payload，不证明 LLM
  语义保真或真实 Token 收益；source artifact atomic integration、rolling
  replacement 和 checkpoint/replay 接入仍属于 Stage 2。

### Context Phase 10：Stage 2 feature-flagged rolling summary adapter

- 阶段计划：先写 `docs/context_management/PHASE_10_STAGE_2_ROLLING_SUMMARY_PLAN.md`，
  冻结 default-off、增量 source segment、provider-free validation、artifact
  sink 复用和 deterministic Current fallback。
- 实现修复：新增 `memory.rolling_compaction`。它冻结 source IDs/fingerprint，
  校验结构化 payload、summary token ceiling、finish reason、usage evidence、
  stale source 和压缩收益，并返回 typed fallback。`MemoryContextBuilder` 增加
  default-off injectable request factory/adapter；生成 summary 只能作为 preferred
  derived candidate，若它挤掉 recent suffix、无法原子选中或 artifact sink 失败，
  自动恢复 deterministic observation mask（strict 模式沿用原有 fail-closed）。
  legacy `ContextCompressor` 仍没有生产 caller。
- 验证证据：rolling adapter `8 passed`；summary/context/rolling integration 与
  existing memory context `31 passed`；全量回归需在 Stage 3 完成后重新执行。
  离线运行未调用 Provider/network，也未修改 project、memory 或权限状态。
- 出口判断：Stage 2 的 default-off、atomic artifact、strict/non-strict sink
  boundary 和 deterministic fallback 已通过；真实 Provider canary 仍未开启。
- 剩余限制：当前 factory 是注入边界，尚未连接真实 Provider，也尚未把 summary
  attempt 的完整 usage/finish telemetry 纳入长期 trajectory；增量 previous
  summary 的生产调用和 checkpoint/replay 端到端 fixture 仍需在后续阶段补齐。

### Context Phase 10：Stage 3 session constraint boundary

- 阶段计划：先写 `docs/context_management/PHASE_10_STAGE_3_SESSION_CONSTRAINT_PLAN.md`，
  冻结统一 ingress、same-key stale proposal 处理、有界状态和 source-linked
  required projection 边界。
- 实现修复：Enhanced CLI 现在把 `/constraints`、`/confirm`、`/reject`、`/revoke`
  统一路由到同一 typed handler；新用户提案会 supersede 更早的同 key pending
  proposal，旧提案不能延迟激活；active entry 现在保留 `confirmed_at_turn`，而
  revoked entry 保留 `revoked_at_turn`；`SessionConstraintLimits` 对 pending proposals、
  active/revoked entries、序列化大小、scope paths、commands、criteria 和 item
  长度实施 fail-closed 配额，旧 checkpoint 缺少 limits 时使用默认迁移值。
- 验证证据：session constraint/reducer/ingress focused 集合 **27 passed**；覆盖
  command lifecycle、supersession、quota、legacy checkpoint readability、assistant
  non-authority 和 active projection。未调用 Provider/network，也未写长期 memory。
- 出口判断：Stage 3A/3B 的入口、生命周期和 bounded-state 门通过；API/acceptance
  仍只是 required projection，尚未接入独立 verification evidence gate；Agent
  Generator 的完整 ingress 接入仍是下一阶段限制。

### Context Phase 10：Stage 4 provider-neutral reasoning profiles

- 阶段计划：先写 `docs/context_management/PHASE_10_STAGE_4_REASONING_PROFILE_PLAN.md`，
  冻结 explicit typed profile、versioned registry、generic no-control fallback
  和 reasoning/Compact/completion 独立归因。
- 实现修复：`core.reasoning` 新增显式 profile registry（当前 generic、OpenAI
  compatible、DeepSeek compatible 均为 versioned v1），移除 endpoint/model-name
  capability inference。未配置 profile 时始终使用 generic provider-default；
  explicit profile 才允许 transport controls，版本不匹配或 unknown profile
  fail closed。业务模块仍只选择 provider-neutral `ReasoningPolicy`。
- 验证证据：reasoning policy、runtime diagnostics、iteration/task-delta 和
  code-generation context focused 集合 **97 passed**；包含 explicit profile
  selection、generic fallback、transport mapping、unsupported behavior、cache/
  replay hash 绑定。未实现或宣称 native Anthropic/Gemini transport。
- 出口判断：Stage 4 的 capability selection 不再依赖模型名；仍需在 Stage 5
  以独立 paired canary 验证真实 provider 的 observed reasoning usage 和质量，
  不得把 generic profile 的 no-control 结果外推为原生 provider 支持。

### Context Phase 10：Stage 5 independent offline acceptance

- 阶段计划：先写 `docs/context_management/PHASE_10_STAGE_5_CANARY_ACCEPTANCE_PLAN.md`，
  冻结 immutable source envelope、Current/Treatment 独立开关、逐 attempt evidence、
  required-state/provenance/mutation gates 和 no-global-default policy。
- 验证实现：新增 `test_context_governance_stage5_acceptance.py`，用同一源对照
  Current deterministic compact 与 Treatment injected rolling boundary；Treatment
  提供 unknown usage，必须回退 deterministic，同时两臂都保留 active required
  write-scope constraint。测试还验证 reasoning explicit profile 选择不改变 Compact
  authority或约束 projection。
- 证据结果：Stage 5 定向 **1 passed**；本阶段最终 `PYTHONPATH=Code/src pytest -q
  Code/tests` 为 **1040 passed**，compileall 和 `git diff --check` 通过。未调用
  Provider/network，也未产生 project/memory mutation。
- 决策：offline GO；仅允许后续小流量、独立 instrumented real-provider canary。
  不切换全局 Compact/reasoning 默认，不声称真实 LLM semantic quality 或 native
  Anthropic/Gemini 支持。

### Context Phase 11：Stage 6A real-provider readiness and manifest

- 阶段计划：先写 `docs/context_management/PHASE_11_STAGE_6A_PROVIDER_READINESS_PLAN.md`，
  冻结 credential-free endpoint identity、显式 versioned profile、exact tokenizer
  要求、独立 summary budget、Current/Treatment flags、kill switch、source-bound
  manifest 和已有 provider attempt receipt contract；本阶段禁止 Provider/network。
- 原因探查：真实 Provider 试验如果没有前置 readiness，缺少凭据、未知 profile、
  不可计数 tokenizer 或非只读路径都可能在 transport 前混入实验，导致“没有调用”
  与“调用但证据不完整”无法区分，也会把实验清单误当成运行时 authority。
- 实现修复：新增 experiment-owned `stage17_real_provider_readiness.py`，定义严格
  `ProviderReadiness`、`RollingSummaryBudgetPolicy`、`ExperimentFlags`、typed
  blocker 和 source/session/constraint/task/completion hash 绑定的
  `RollingSummaryExperimentManifest`。复用 `normalized_provider_endpoint`、
  `ProviderTokenCounter`、`calculate_summary_budget` 和既有
  `ProviderAttemptReceipt` 版本，不新增生产 `MetadataKind`，Treatment 未通过
  readiness 时 fail closed。
- 验证证据：新增 readiness/manifest 7 个离线测试；与 summary、rolling、reasoning、
  tokenizer focused 集合合计 **79 passed**。没有创建 LLM client、HTTP 请求、
  Provider call、project/memory mutation 或泄露 credential 的 artifact。
- 出口判断：Stage 6A offline GO；只允许进入 Stage 6B 的 shadow 规划。真实
  Provider semantic quality、usage、finish reason 和 token reduction 仍未测量。
- 剩余限制：当前只证明“可安全进入实验”的边界；还没有 provider-neutral shadow
  caller、captured response artifact、recorded replay 或 paired canary。

### Context Phase 11：Stage 6B provider-neutral rolling-summary shadow

- 阶段计划：先写 `docs/context_management/PHASE_11_STAGE_6B_PROVIDER_SHADOW_PLAN.md`，
  冻结 source snapshot、dynamic summary budget、strict JSON request、injected
  transport、attempt receipt 和 `used_in_prompt=false` observation boundary。
- 原因探查：如果真实 Provider 返回后直接交给 ContextBuilder，shadow 会同时改变
  Compact 和 Provider 质量，无法归因，也可能让 untrusted summary 取得 authority；
  因此先只测 transport/usage/finish/fallback，保持 Current 行为不变。
- 实现修复：新增 experiment-owned `stage18_provider_shadow.py`。它绑定 Stage 6A
  manifest，复用 `RollingSummaryAdapter` 和 `ProviderAttemptReceipt`，动态计算
  summary ceiling；zero budget、invalid manifest、非 Treatment、异常、unknown
  usage、truncated、stale source 和 invalid payload 均 fail closed，返回
  observation 而不修改 Prompt。
- 验证证据：Stage 6A/6B/attempt telemetry/summary focused 集合 **75 passed**；
  覆盖 response/attempt hash、usage 不补零、finish reason、provider exception、
  source size 和 no-transport gates。未调用 Provider/network，也未产生
  project/memory mutation。
- 出口判断：Stage 6B offline GO；可进入 recorded replay 设计。真实 Provider
  调用仍需 readiness-admitted、可回放的 response artifact 和独立 replay gate。
- 剩余限制：尚未持久化 shadow artifact、验证 replay 与原始 source/manifest 的
  原子关系，也未执行 paired canary 或真实 token reduction 分析。

### Context Phase 11：Stage 6C recorded rolling-summary replay

- 阶段计划：先写 `docs/context_management/PHASE_11_STAGE_6C_RECORDED_REPLAY_PLAN.md`，
  冻结 response artifact 的 source/manifest/request hash 绑定、credential-free
  序列化、`replay_receipt` no-transport 语义和 outcome drift 门禁。
- 原因探查：仅凭一次 shadow response 不能证明结果可重现；如果回放时重新调用
  Provider，会把网络波动、reasoning 或模型变化混入 Compact 归因。因此先将
  response/usage/finish/source 作为 artifact，完全离线重跑同一 adapter。
- 实现修复：新增严格 `RecordedShadowArtifact`、`capture_recorded_artifact` 和
  `replay_recorded_artifact`。回放前验证 manifest 和 request hash；回放使用既有
  `RollingSummaryAdapter` 与 `replay_receipt`，对 accepted/fallback、summary record
  和 source lineage 做 exact compare。pre-transport、provider exception 和无
  structured payload 的尝试不能伪装成 replayable artifact。
- 验证证据：Stage 6A/6B/6C/telemetry/summary focused 集合 **74 passed**；覆盖
  accepted replay、unknown-usage fallback replay、tampered source、replay no
  transport、non-replayable attempt。无 Provider/network 或 project/memory mutation。
- 出口判断：Stage 6C offline GO；可进入小流量 Current/Treatment paired canary
  设计。真实 semantic quality 和 token reduction 仍未宣称。
- 剩余限制：尚未在真实 Provider 上收集多目的 paired 数据，也未验证 mutation、
  verification、required/provenance 与 task-quality gate 的联合结果。

### Context Phase 11：Stage 6D small paired Current/Treatment canary gate

- 阶段计划：先写 `docs/context_management/PHASE_11_STAGE_6D_PAIRED_CANARY_PLAN.md`，
  冻结三种目的、同源/同约束 paired evidence、显式 `used_in_prompt`、required/
  provenance/verification/quality/mutation 门禁和 no-global-rollout 语义。
- 原因探查：token 下降本身不能证明 Compact 变好；如果 summary 进入 Prompt 时
  丢了 required constraint、source lineage 或验证证据，调用减少反而是坏结果。
  因此 canary 先把安全/质量 gate 与 token/call/fallback accounting 分开。
- 实现修复：新增 `stage20_paired_canary.py`。三种 purpose 必须覆盖；Current 和
  Treatment 共享 source/constraint hash；Treatment 只有在 accepted summary 的
  compaction ID、required retention、provenance、verification、quality 和零 mutation
  同时成立时才算真正使用 summary；fallback/unknown usage 单独计数。
- 验证证据：Stage 6A–6D、provider attempt telemetry、summary focused 集合
  **84 passed**；覆盖三目的通过、fallback、required/mutation/source mismatch、
  kill switch、purpose coverage 和 no-global-rollout。未执行 Provider/network 或
  project/memory mutation。
- 出口判断：Stage 6D offline GO；进入 Stage 6E 做全量回归、实际 readiness 检查和
  真实 Provider 流量决策。任何真实 canary 仍必须显式 opt-in，不能修改默认。
- 剩余限制：尚无真实 Provider 的多目的 paired 数据；当前 token reduction 是离线
  fixture 的 gate 证据，不是生产收益结论。

### Context Phase 11：Stage 6E final real-provider shadow decision

- 阶段计划：先写 `docs/context_management/PHASE_11_STAGE_6E_FINAL_GATE_PLAN.md`，
  冻结 full regression、readiness、最多三次低风险 shadow、完整 attempt receipt、
  以及“Transport 成功不等于 Compact 成功”的决策边界。
- 实验执行：环境中的真实 endpoint/model/tokenizer readiness 通过；原始配置没有
  explicit profile，因此仅在进程内显式声明 `generic-openai-compatible:v1`，不根据
  `deepseek-v4-flash` 猜 reasoning 能力，也不修改 env/生产默认。首轮 3 calls 因
  final report 未投影 error fields 被丢弃；修复 receipt projection 后重新执行同样
  上限的 3-call authoritative run。
- 结果证据：Code 全量 **1040 passed**；Stage 6A–6E focused **88 passed**；
  compileall/diff-check 通过。权威 run 的 3 calls（context_compaction、goal_plan、
  tool_event_decision）全部 `InvalidLLMResponseError`/validation，`finish_reason=length`，
  output=128 且 reasoning=128；input/output/total 分别为 495/128/623、506/128/634、
  503/128/631。usage 完整可 reconciliation，unknown usage=0，但 accepted summary=0、
  fallback=3、replayable artifact=0。
- 根因判断：128-token summary ceiling 被 Provider-default reasoning 完全占用，导致
  JSON summary 截断；这是 reasoning/completion allocation 信号，不是 segmented
  Compact 语义质量结论。attempt telemetry 已保留 usage、reasoning、finish、error
  category/type、retry recommendation 和 request hash。
- 决策：`global_default_changed=false`，Current deterministic context 保持生产唯一
  model-facing projection；不进入 paired Treatment canary。下一阶段应单独做
  explicit provider reasoning/completion allocation experiment，完成后再重跑 Compact。
- 文档：完整结果见 `docs/context_management/PHASE_11_STAGE_6E_RESULT.md`；剩余限制是
  尚无有效 summary response、replay artifact 或真实 token reduction/semantic quality
  结论。

### Context Phase 12：reasoning strategy experiment

- 阶段计划：先写 `docs/context_management/PHASE_12_REASONING_STRATEGY_EXPERIMENT.md`，
  冻结同一 source/schema 的四臂矩阵：provider-default 128、explicit disabled 128、
  provider-default 256、explicit enabled/high 128；reasoning、completion、Compact
  和 task quality 分开归因。
- 原因探查与实现：扩展 experiment-owned manifest，绑定 typed `ReasoningPolicy` 和
  version；shadow request 使用 manifest policy；新增 `stage22_reasoning_strategy_experiment.py`
  及分类器，严格区分 reasoning exhausted、普通 ceiling/schema truncation、unknown
  usage/finish、provider error 和 valid summary。没有新增生产 MetadataKind，也没有
  从 model name 推断 capability。
- 离线证据：reasoning/readiness focused **11 passed**；既有 Code 全量与 Compact
  focused 回归保持通过，compileall/diff-check 通过。
- 真实证据：4 calls 均抵达真实 DeepSeek endpoint。`default_128`、`default_256`、
  `enabled_high_128` 分别以 reasoning=output=128、256、128 和 finish `length` 失败；
  `disabled_128` 以 input=416、output=68、finish `stop` 成功返回并通过 rolling summary
  schema/lineage/budget 校验。结果是 1 个 valid shadow summary、3 个 reasoning-exhausted
  attempts；全程无 project/memory/task mutation。
- 根因判断：提高 completion ceiling 只让 provider 消耗更多 reasoning；显式 disabled
  才释放 summary completion。这锁定了 reasoning allocation 为根因，不能把失败归因
  给 Compact schema。
- 决策：不改变全局 reasoning 或 Compact 默认，不进入 paired task canary；下一步是
  用显式 disabled profile 做独立 Compact 收益实验。完整结果见
  `docs/context_management/PHASE_12_REASONING_STRATEGY_RESULT.md`。

### Context Phase 12：reasoning output inspection and adapter-boundary audit

- 诊断证据：对同一 provider-default/128 请求直接检查 Provider 原始 choice。可见
  `message.content` 长度为 0，`finish_reason=length`，completion=128、reasoning=128；
  `message.reasoning_content` 长 678 字符，停在 `- goal_delta: change in` 中途。模型
  还没有进入 JSON 输出通道，故不是“生成了错误 JSON”，而是隐藏 reasoning 先耗尽预算。
- 架构审计：当前已有 provider-neutral `ReasoningPolicy`、resolved policy、显式
  versioned profile 和禁止 model-name 推断；但 `render_reasoning_transport()` 仍在
  `core/reasoning.py` 内用 provider-specific 分支，profile 还是数据记录而非独立
  adapter protocol。通用基座 + 特定接口适配的方向已部分实现，尚未完全解耦。
- 后续边界：应保留通用 policy base，增加 versioned adapter registry/protocol，负责
  transport rendering 与 reasoning usage normalization；实验中的 explicit disabled
  不支持时必须 fail closed，不能静默回退 provider default。此次只补充诊断文档，未
  改变生产 reasoning/Compact 默认。

### Typed compaction provenance and reuse admission

- Observed failure: the runtime could not represent the difference between a
  provider-accepted summary, a builder-selected projection, and a reusable
  artifact considered only in shadow mode.
- Validation evidence: the regression commit fails while importing the missing
  typed contracts; the focused metadata suite passes after the implementation.
- Implemented fix: add strict, body-free attempt, admission, and shadow-failure
  values owned by `ContextSelectionMetadata`, plus a backward-compatible source
  binding hash.
- Remaining limitation: this change records and validates evidence only. It does
  not enable reusable summaries in model-facing prompts.

### Bounded provider completion outcome evidence

- Observed failure: empty, truncated, and failed provider attempts could not be
  represented as typed budget evidence or safely influence one retry allowance.
- Validation evidence: the regression commit fails while importing the missing
  outcome and diagnostic contracts; focused and full suites pass after the fix.
- Implemented fix: add an opt-in outcome signal, a bounded one-step recovery
  bonus, and per-attempt diagnostics that preserve unknown usage.
- Remaining limitation: this change records budget evidence only; provider
  transport integration remains a separate change.

### Stable model-facing session constraint identity

- Observed failure: advancing an ordinary conversation turn changed the
  constraint candidate identity even when active constraints were unchanged.
- Validation evidence: focused tests reproduce the identity drift and pass once
  snapshot identity is separated from authority identity.
- Implemented fix: preserve `canonical_hash` for checkpoint/replay and use an
  `authority_hash` that excludes only `processed_through_turn` for prompt views.
- Remaining limitation: any actual constraint revision or revoke still changes
  authority identity by design.

### Checkpoint ingress run identity validation

- Observed failure: a checkpoint accepted ingress state whose `run_id` differed
  from the checkpoint session identity.
- Validation evidence: the regression test fails on the stacked base and the
  focused checkpoint suite passes after validation is added.
- Implemented fix: reject mismatched nested ingress run identity during typed
  checkpoint validation.
- Remaining limitation: conversation identity remains separate by design and is
  validated through the existing constraint-ledger relationship.

### Typed provider reasoning adapters

- Observed failure: provider-specific reasoning rendering and usage shapes were
  embedded in one generic policy module and could not represent Anthropic,
  Gemini, or explicit no-reasoning OpenAI profiles.
- Validation evidence: the regression suite fails before the adapter and usage
  contracts exist and passes for all explicit profiles after implementation.
- Implemented fix: add versioned profile adapters for rendering and normalized,
  body-free reasoning observations; capability is never guessed from model text.
- Remaining limitation: native provider transport and streaming integration are
  separate changes.

### Scoped provider lane identity and budgets

- Observed failure: provider experiments lacked one immutable identity tying
  credentials, endpoint, model, tokenizer, reasoning profile, and budgets.
- Validation evidence: regression tests cover credential isolation, settings
  drift, tokenizer mismatch, empty identity, and explicit fan-out limits.
- Implemented fix: add bounded provider lanes and static canary/read-only/
  mutation budget profiles; settings construction skips repository env files.
- Remaining limitation: this change does not send provider requests or grant
  mutation authority.

### Native provider request and response contracts

- Observed failure: Anthropic and Gemini had no typed native request conversion
  or normalized response boundary outside the OpenAI-compatible client.
- Validation evidence: offline fixtures cover request shapes, reasoning fields,
  response usage, invalid budgets, registry selection, redirect policy, and
  oversized response rejection.
- Implemented fix: add strict native adapters and provider-tool message models;
  native HTTP performs one non-redirecting attempt with a 2,000,000-byte cap.
- Remaining limitation: `LLMClient` routing, retry, caching, and JSON repair are
  separate changes.

### Tool continuation and validation command contracts

- Observed failure: provider tool continuations lacked one reusable identity
  check, and validation text comparison could accidentally widen command scope.
- Validation evidence: focused tests cover missing/duplicate/drifted call IDs,
  DeepSeek reasoning state, quoting, wrappers, pipes, redirects, and bad quotes.
- Implemented fix: add ordered provider-neutral continuation helpers, a strict
  DeepSeek wrapper, and argv-only validation command equivalence.
- Remaining limitation: these helpers neither execute tools nor run commands.

### Structured output reasoning resolution

- Observed failure: `LLMClient` resolved provider-default reasoning without the
  request's structured-output fact, so known providers could spend the JSON
  completion budget on hidden reasoning despite supporting explicit disable.
- Validation evidence: the client-level regression test fails on the stacked
  base because the transport payload omits the supported disable field; focused
  and full suites pass after the fix.
- Implemented fix: bind `response_format=json_object` into both request execution
  and cache-key reasoning resolution, while leaving generic profiles unchanged.
- Remaining limitation: this does not add provider-native routing or streaming
  tool-call aggregation; those remain separate changes.

### LLM tool-call preservation

- Observed failure: `LLMClient` omitted typed tools from provider payloads,
  excluded them from cache identity, and discarded returned tool calls before
  orchestration could continue them.
- Validation evidence: four offline regressions fail on the stacked base for
  missing payload fields, cache collisions, dropped calls, and malformed-shape
  acceptance; focused and full suites pass after the fix.
- Implemented fix: render tools and continuation messages explicitly, normalize
  provider tool calls into the existing typed response contract, fail closed on
  malformed shapes, and never cache intermediate tool-call responses.
- Remaining limitation: streaming tool-call fragments and native-provider
  transport routing remain separate changes.

### Native provider routing in LLMClient

- Observed failure: selecting a native Anthropic or Gemini capability profile
  still constructed the OpenAI-compatible client, so the registered native
  transport contracts were unreachable from normal completion calls.
- Validation evidence: two offline regressions fail on the stacked base for
  incorrect routing and unsupported streaming admission; focused and full
  suites pass after the fix.
- Implemented fix: route non-OpenAI transport families through the explicit
  native registry and reject native streaming before transport.
- Remaining limitation: native retry evidence, proxy fallback, native streaming,
  and streamed reasoning/tool-call aggregation remain separate changes.

### Bounded native transport retry evidence

- Observed failure: native provider routing performed only one attempt and did
  not expose typed evidence explaining retryable or terminal failures.
- Validation evidence: two offline regressions fail on the stacked base for the
  missing retry helper; focused and full suites pass after implementation.
- Implemented fix: apply the configured finite retry count around single-shot
  native transports, stop immediately on terminal provider errors, retain
  bounded attempt history, reject overrides above five retries, and redact the
  configured credential from error text.
- Remaining limitation: native streaming remains a separate change.

### Native environment-proxy fallback

- Observed failure: after exhausting native retries on an environment-proxy
  network failure, `LLMClient` did not attempt the existing direct-connection
  recovery path used by the OpenAI-compatible transport.
- Validation evidence: two offline regressions fail on the stacked base for the
  missing direct success and direct failure evidence; focused and full suites
  pass after the fix.
- Implemented fix: permit exactly one `trust_env=False` native attempt after the
  classified proxy failure and record its distinct reason and outcome.
- Remaining limitation: native streaming and streamed reasoning/tool-call
  aggregation remain separate changes.

### Streamed reasoning preservation

- Observed failure: the streaming collector counted hidden reasoning fields but
  discarded their content from the normalized response.
- Validation evidence: two offline regressions fail on the stacked base because
  `reasoning_content` is absent; focused and full suites pass after the fix.
- Implemented fix: concatenate streamed reasoning fragments into the separate
  normalized message field while keeping visible delta events content-only.
- Remaining limitation: streamed tool-call fragment aggregation and native
  streaming remain separate changes.

### Streamed tool-call aggregation

- Observed failure: the streaming collector discarded provider tool-call
  fragments, so streamed autonomous requests could not continue tool execution.
- Validation evidence: four offline regressions fail on the stacked base for
  missing calls, index ordering, malformed containers, and negative indexes;
  focused and full suites pass after the fix.
- Implemented fix: accumulate fragments by provider index, concatenate function
  name and argument text, finalize calls in index order, and validate the
  existing typed call contract before returning the response.
- Remaining limitation: native streaming remains a separate change.

### Provider mapping response normalization

- Observed failure: provider adapters returning plain mapping messages lost
  visible content, fallback text, and field-name diagnostics because extraction
  assumed SDK object attributes.
- Validation evidence: two offline regressions fail on the stacked base for
  mapping content and fallback fields; focused and full suites pass after the
  fix.
- Implemented fix: normalize mapping and object message access through the same
  content and diagnostic boundary.
- Remaining limitation: provider-specific unknown fields remain diagnostic only.

### Reasoning response observation integration

- Observed failure: typed provider reasoning observations existed at the adapter
  layer but normal `LLMClient` responses did not attach them to provider evidence.
- Validation evidence: the offline client regression fails on the stacked base
  because `reasoning_observation` is absent; focused and full suites pass after
  integration.
- Implemented fix: pass the resolved profile identity through response and JSON
  error metadata assembly and attach the adapter's body-free typed observation.
- Remaining limitation: unknown provider usage fields remain unknown rather than
  being inferred.

### Provider tool-call correlation identity

- Observed failure: the tool metadata protocol had only the project-owned
  `call_id`, so provider request/result correlation would either overwrite that
  identity or be hidden in free-form diagnostics.
- Metadata impact note:
  - Fact: optional external provider tool-call identity.
  - Authoritative producer: provider tool admission; consumers: round-trip,
    error projection, and trajectory records.
  - Lifecycle: event evidence. Control impact: none; `call_id` remains authority.
  - Existing contracts reviewed: `ToolCallMetadata`, `ToolErrorMetadata`,
    `ToolEventMetadata`, and `ToolLoopMetadata`.
  - Decision: extend the two standalone call/error records; no duplicate source
    is created because provider and project IDs have distinct owners.
  - Serialization and migration: historical payloads default to `None`; new
    non-empty values round-trip and empty strings reject.
  - Tests and docs: focused construction, invalid input, historical read, JSON
    round-trip, catalog, API, and implementation log.
- Validation evidence: five regressions fail on the stacked base and pass after
  the optional strict fields are added; the full suite remains green.
- Implemented fix: add `provider_call_id` to call and error metadata without
  changing their `MetadataKind` or lifecycle authority.
- Remaining limitation: admission and execution producers remain separate PRs.

### Typed provider admission outcomes

- Observed failure: provider-native calls had no strict result distinguishing an
  executable project selection from a blocked protocol or policy failure.
- Validation evidence: the regression suite fails at import on the stacked base
  and passes seven admitted/blocked, lineage, contradiction, and identity tests
  after implementation; the full suite remains green.
- Implemented fix: add a core-owned strict admission outcome that reuses
  `ToolCallMetadata`, `ToolSelection`, `ToolErrorMetadata`, and `FailureMetadata`;
  provider error projection preserves both correlation identities.
- Remaining limitation: registry, argument, permission, scope, and budget
  admission decisions remain separate changes.

### Provider tool-definition projection

- Observed failure: provider requests lacked a narrow, deterministic schema
  projection from the registered tool contracts and risked exposing the broad
  internal input model.
- Validation evidence: the regression suite fails at import on the stacked base
  and passes five field-selection, conditional, type, duplicate, unknown, and
  untyped-contract checks after implementation; the full suite remains green.
- Implemented fix: project only contract-declared fields into strict provider
  function schemas, including required/any-of/conditional/default semantics and
  a bounded command-mode enum, with explicit 32-tool and 64-field caps.
- Remaining limitation: schema projection does not admit or execute provider
  calls; those remain separate boundaries.

### Provider argument and contract validation

- Observed failure: provider tool arguments lacked one bounded JSON-object
  decoder and reusable validation for required, alternative, and conditional
  typed input fields.
- Validation evidence: the regression suite fails at import on the stacked base
  and passes argument-shape, malformed JSON, size, required-field, any-of,
  conditional, and contract-field-cap checks after implementation; the full
  suite remains green.
- Implemented fix: add pure argument decoding with a 200,000-character cap and
  contract validation aligned with the preceding 64-field provider projection.
- Remaining limitation: validation does not select, authorize, or execute tools.

### Provider file path-scope validation

- Observed failure: provider file requests lacked one pure boundary for exact
  read/write scope matching, project containment, symlink rejection, and empty
  explicit scope semantics.
- Validation evidence: the regression suite fails at import on the stacked base
  and passes explicit-match, empty-scope, outside-root, symlink, missing-target,
  parent-escape, and 64-path-cap checks after implementation; the full suite
  remains green.
- Implemented fix: add bounded canonical path checks for read and mutation
  inputs; empty explicit scopes grant no authority and diagnostic output lists
  at most four outside paths.
- Remaining limitation: path validation does not itself authorize or execute a
  provider tool call.

### Typed provider tool budget admission

- Observed failure: provider tool resource counts and batch-prior usage were
  represented as free dictionaries, and validation attempts were not included
  in the same typed budget decision.
- Validation evidence: the regression suite fails at import on the stacked base
  and passes resource classification, exact-boundary, five exhaustion reason,
  invalid-count, accumulated-usage, and contradictory-state checks after
  implementation; the full suite remains green.
- Implemented fix: add strict per-call usage, accumulated batch usage, and
  admitted/blocked budget decisions covering calls, reads, edits, creates, and
  validation attempts.
- Remaining limitation: budget admission does not itself grant permission or
  execute tools.

### Exact provider validation command admission

- Observed failure: provider validation requests lacked a typed decision for
  duplicate use, argv mismatch, execution mode, and cwd widening.
- Validation evidence: the regression suite fails at import on the stacked base
  and passes exact/default binding, five typed rejection reasons, six shell
  widening forms, contradictory-state, and negative-counter checks after
  implementation; the full suite remains green.
- Implemented fix: add one body-free admitted/blocked decision over the existing
  shell-aware argv comparator, with exact cwd binding and automatic mode default.
- Remaining limitation: this decision does not execute the command or prove its
  result.

### Typed provider permission admission

- Observed failure: provider permission checks did not represent mutation
  opt-in separately from user confirmation and could accept truthy substitutes
  for those control facts.
- Validation evidence: the regression suite fails at import on the stacked base
  and passes low-risk, elevated permission, mutation opt-in, named mutation,
  forbidden/unknown, literal-boolean, and contradictory-state checks after
  implementation; the full suite remains green.
- Implemented fix: add a strict permission decision with typed reason codes,
  fail-closed unknown levels, separate mutation opt-in, and explicit
  confirmation evidence.
- Remaining limitation: permission admission alone does not create an executable
  selection or perform a mutation.

### Read-only provider single-call admission

- Observed failure: the pure provider admission decisions were not composed into
  one safe call boundary, so a provider request could not become an existing
  `ToolSelection` even after all read-only authority checks passed.
- Validation evidence: the regression suite fails at import on the stacked base
  and the focused provider-admission suite passes 81 protocol, registry,
  contract, budget, permission, scope, and validation-command checks after the
  implementation; the full suite passes 1,189 tests, followed by successful
  source compilation and diff validation.
- Implemented fix: add one non-executing single-call admission function that
  preserves provider/project identity, resolves both registry definition and
  executor, applies copied typed defaults, then checks contract, budget,
  permission, explicit read scope, and exact task-owned validation authority
  before constructing a selection. Mutations fail closed.
- Remaining limitation: mutation admission, bounded batch accumulation,
  provider round-trip execution, and runtime event-loop integration remain
  separate changes.

### Patch-only provider mutation admission

- Observed failure: provider mutation requests had no composed boundary that
  kept mutation opt-in separate from confirmation while also requiring exact
  write scope and task-owned validation authority before selection.
- Validation evidence: the regression test fails at import on the stacked base;
  the focused read-only and mutation composition suites pass 29 cases after the
  implementation, and the complete focused provider-admission set passes 93.
  The full suite passes 1,201 tests, followed by successful source compilation
  and diff validation.
- Implemented fix: add a separate non-executing mutation entry that admits only
  `file_patch_writer`, requires literal opt-in and confirmation, checks the
  typed edit budget and exact write scope, and requires both a non-empty
  validation command and registered `command_executor` before returning a
  selection.
- Remaining limitation: declared-read phase evidence, mutation receipts, exact
  validation execution, batch accumulation, and provider round-trip integration
  remain separate changes.

### Bounded provider tool-call batch admission

- Observed failure: provider calls could be admitted one at a time, but a
  response-level batch had no static cardinality bound, duplicate-ID rejection,
  deterministic ordinal binding, or accumulated typed resource usage.
- Validation evidence: the regression test fails at import on the stacked base;
  the batch plus single-call composition suites pass 42 cases after the
  implementation, including the exact 32-call boundary; the complete focused
  provider-admission set passes 106. The full suite passes 1,214 tests,
  followed by successful source compilation and diff validation.
- Implemented fix: add a separate non-executing batch module that rejects more
  than 32 calls and duplicate provider IDs, dispatches read-only and patch-only
  calls through their existing entry points, and accumulates calls, reads,
  edits, creates, and validation usage only for admitted selections.
- Remaining limitation: the batch does not enforce provider round-trip phase
  order, execute selections, record mutation receipts, or continue the model;
  those remain separate changes.

### Provider round-trip attempt and evidence contracts

- Observed failure: the source round-trip design represented normalized attempts
  and evidence coverage as mutable/unvalidated dataclass collections, leaving
  contradictory attempt outcomes and inconsistent repeated evidence legal.
- Validation evidence: the regression test fails at import on the stacked base;
  16 attempt/evidence tests cover JSON round trips, contradictory states,
  duplicate evidence, page-cap consistency, exact collection boundaries, and
  unknown fields; the complete focused provider set passes 122. The full suite
  passes 1,230 tests, followed by successful source compilation and diff
  validation.
- Implemented fix: add strict frozen core contracts for provider attempts,
  declared read windows, page-read counts, and evidence coverage, with bounded
  paths, windows, observed keys, duplicate-only rounds, and finalization counts.
- Metadata impact note:
  - Fact: normalized attempt outcomes and bounded evidence coverage.
  - Authoritative producer: the future provider round-trip runner; consumers:
    runtime task integration and trajectory/report projections.
  - Lifecycle: runtime-only with optional derived event evidence. Control impact:
    recovery observation, but no permission or completion grant.
  - Existing contracts reviewed: `ToolCallMetadata`, `ToolErrorMetadata`,
    `ToolLoopMetadata`, `LLMResponseMetadata`, `RuntimeBudgetMetadata`,
    `ProviderBudgetDiagnostic`, `LLMResponse`, and `ToolEventLoopRunResult`.
  - Decision: strict owned core values that reuse existing contracts; no new
    `MetadataKind` and no second provider, tool, budget, or checkpoint authority.
  - Serialization and migration: bounded Pydantic JSON projection; no historical
    migration because no production producer or persisted field exists yet.
  - Tests and docs: contract/JSON/invalid-state tests plus `API.md`,
    `Code/README.md`, the metadata catalog, and this implementation log.
- Remaining limitation: this change defines no runner state machine, duplicate
  partitioning, final result envelope, evidence collection, tool execution, or
  runtime integration.

### Provider round-trip result envelope

- Observed failure: attempt/evidence facts had no strict final runtime envelope,
  so contradictory success/error states, out-of-range attempt lineage, excess
  evidence counters, and unbounded conversation collections remained legal.
- Validation evidence: the regression suite fails because
  `ProviderToolRoundTripResult` is absent on the stacked base; 10 result tests
  cover nested JSON round trips, completion consistency, earlier-attempt
  lineage, round/evidence relationships, message and attempt limits, and
  unknown fields. The complete focused provider set passes 132. The full suite
  passes 1,240 tests, followed by successful source compilation and diff
  validation.
- Implemented fix: add a separate frozen result envelope that composes existing
  LLM responses/messages, tool-loop results, attempt/evidence contracts, typed
  budget diagnostics, and reasoning observations. It bounds repeated runtime
  collections and rejects contradictory completion facts.
- Metadata impact note:
  - Fact: one final provider round-trip outcome and its bounded runtime evidence.
  - Authoritative producer: the future provider round-trip runner; consumers:
    runtime task integration and derived trajectory/report projections.
  - Lifecycle: runtime-only with optional derived event evidence. Control impact:
    completion and recovery observation, but no permission or execution grant.
  - Existing contracts reviewed: `LLMResponse`, `LLMMessage`,
    `ToolEventLoopRunResult`, `ToolLoopMetadata`, `ProviderBudgetDiagnostic`,
    `ProviderToolAttempt`, `ProviderToolEvidenceCoverage`, and reasoning enums.
  - Decision: strict owned core envelope; no new `MetadataKind`, no duplicated
    provider response, tool-loop, budget, or checkpoint authority.
  - Serialization and migration: bounded Pydantic JSON projection; no historical
    migration because no production producer or persisted field exists yet.
  - Tests and docs: result/JSON/invalid-state tests plus `API.md`,
    `Code/README.md`, the metadata catalog, and this implementation log.
- Remaining limitation: this change defines no runner state machine, request
  diagnostics producer, tool execution, persistence, or runtime integration.

### Canonical provider tool-call signatures

- Observed failure: duplicate/replay detection lived inside the oversized source
  runner and lacked a reusable bounded identity function; relative and absolute
  project paths could otherwise describe the same call with different text.
- Validation evidence: the regression suite fails because the signature module
  is absent on the stacked base; nine tests pass after implementation for path,
  object-key and `file_paths` normalization, ordered non-path lists, provider ID
  independence, malformed arguments, project-root separation, and depth/item
  limits and bounded malformed-payload hashing. The complete focused provider
  set passes 141. The full suite passes 1,249 tests, followed by successful
  source compilation and diff validation.
- Implemented fix: add a pure SHA-256 signature helper over the tool name and
  canonical argument object. It resolves path fields against the project root,
  preserves ordinary list order, sorts `file_paths`, and replaces malformed raw
  arguments with a hash of only the bounded prefix plus total length.
- Remaining limitation: this change does not own an attempt ledger, partition
  duplicates, collect evidence, execute tools, or run provider rounds.

### Bounded provider tool-attempt ledger

- Observed failure: normalized attempts had a strict value contract but no
  runtime owner for first-signature identity, provider-call uniqueness, bounded
  retention, or duplicate lineage.
- Validation evidence: the regression suite fails because the ledger module is
  absent on the stacked base; 14 tests pass after implementation for first and
  failed attempt ownership, duplicate recording, invalid lineage, provider-ID
  reuse, exact capacity, overflow, invalid values, and configured limits. The
  complete focused provider set passes 155. The full suite passes 1,263 tests,
  followed by successful source compilation and diff validation.
- Implemented fix: add a runtime-only ledger capped by the existing 1,024
  attempt limit. It retains typed immutable attempts, owns the first attempt per
  normalized signature, rejects provider ID reuse, and requires later repeats
  to reference that first provider call explicitly.
- Remaining limitation: this change does not partition provider response calls,
  create preblocked tool results, collect evidence, execute tools, or run the
  provider state machine.

### Cross-round provider duplicate partition

- Observed failure: the attempt ledger and canonical signature helper did not
  yet separate unseen provider calls from signatures attempted in earlier
  rounds or produce a typed preblocked duplicate result.
- Validation evidence: the regression suite fails because the partition module
  is absent on the stacked base; 15 partition tests cover new/duplicate
  ordering, relative/absolute signature equivalence, lineage recording,
  provider-ID reuse, 32-call boundaries, atomic ledger capacity, invalid
  controls, and contradictory result contracts. The complete focused provider
  set passes 170. The full suite passes 1,278 tests, followed by successful
  source compilation and diff validation.
- Implemented fix: add a pure pre-execution partition that validates the full
  response first, computes all bounded signatures, preflights duplicate ledger
  capacity, then returns ordered unseen calls and fixed typed duplicate blocks.
  Existing duplicates append lineage to the ledger; no tool is executed.
- Remaining limitation: same-response unseen signature duplicates are not yet
  coalesced, and the partition does not create wire tool-result messages,
  collect evidence, execute tools, or control provider rounds.

### Bounded provider evidence runtime state

- Observed failure: the frozen evidence-coverage contract had no bounded runtime
  owner for completed reads, declared windows, projections, page counts,
  evidence keys, or duplicate/finalization round observations.
- Validation evidence: the regression suite fails because the evidence-state
  module is absent on the stacked base; 21 tests pass after implementation for
  empty projection, canonical reads/windows, idempotent evidence, page caps,
  exact path/key/round bounds, invalid observations, atomic overflow, and no
  project containment and no target-file creation. The complete focused provider
  set passes 191. The full suite passes 1,299 tests, followed by successful
  source compilation and diff validation.
- Implemented fix: add one runtime-only state owner that canonicalizes observed
  paths, bounds their union and every repeated fact, derives cap paths and
  bounded projections, and emits the existing frozen
  `ProviderToolEvidenceCoverage` contract.
- Metadata impact note:
  - Fact: accepted provider read/evidence observations for one round trip.
  - Authoritative producer: the future provider runner after successful typed
    tool results; consumers: duplicate/finalization policy and result projection.
  - Lifecycle: runtime-only with derived event evidence. Control impact:
    progress/recovery observation, but no permission, I/O, or completion grant.
  - Existing contracts reviewed: `ProviderToolEvidenceCoverage`,
    `ProviderDeclaredReadWindow`, `ProviderPageReadCount`, `ToolResultMetadata`,
    `ToolEventMetadata`, and `ToolLoopMetadata`.
  - Decision: one runtime state owner that projects the existing strict value;
    no new `MetadataKind` or duplicated persisted evidence source.
  - Serialization and migration: only the frozen coverage projection serializes;
    no historical migration because no production state producer exists yet.
  - Tests and docs: state/boundary/no-I/O tests plus `API.md`, `Code/README.md`,
    the metadata catalog, and this implementation log.
- Remaining limitation: no tool-result adapter, evidence extraction, runner
  policy, persistence, or runtime integration is added here.

### Bounded provider tool wire exchange

- Observed failure: typed duplicate blocks and provider tool results had no
  reusable bounded projection into the assistant/tool continuation message
  sequence required by provider-native round trips.
- Validation evidence: the regression suite fails because the wire-exchange
  module is absent on the stacked base; 14 tests pass after implementation for
  fixed duplicate JSON, assistant field preservation, response-call ordering,
  exact 32-call and 1,600-character boundaries, ID mismatch, invalid values,
  unbounded iterables, and empty exchanges. The complete focused provider set
  passes 205. The full suite passes 1,313 tests, followed by successful source
  compilation and diff validation.
- Implemented fix: add a pure duplicate-block adapter plus exact wire exchange.
  It validates all call/result identities and sizes before producing one
  assistant message and provider-call-ordered tool messages; it performs no
  execution or provider request.
- Remaining limitation: successful execution-result projection, artifact
  handoff, historical message compaction, request dispatch, and runner control
  flow remain separate changes.

### Provider identity in event-loop result maps

- Observed failure: `ToolCallMetadata.provider_call_id` survived admission and
  events, but `_append_tool_result()` dropped it, so provider result projection
  could not correlate successful or failed event-loop results back to wire call
  IDs.
- Validation evidence: the regression test fails for both provider-bound and
  local calls on the stacked base; both cases pass after implementation. The
  complete focused provider set passes 207. The full suite passes 1,315 tests,
  followed by successful source compilation and diff validation.
- Implemented fix: include `provider_call_id` only when it is non-null in the
  existing tool-result map. Provider calls retain wire correlation; ordinary
  local results preserve their historical dictionary shape.
- Remaining limitation: this change does not project result payloads, create
  `LLMToolResult` values, execute provider rounds, or integrate the runner.

### Bounded provider tool-result payloads

- Observed failure: result projection had no reusable strict JSON fitter, and
  the 1,600-character wire limit was duplicated inside wire exchange rather
  than owned by one payload boundary.
- Validation evidence: the regression suite fails because the payload module is
  absent on the stacked base; 19 tests pass after implementation for exact
  payloads, preview compaction, complete-window semantics, minimal fallback,
  compact artifact references, bounded failure text, determinism, exact limits,
  literal controls, non-JSON values, and exact depth/item/input-character
  boundaries. The complete focused provider set passes 239, and the complete
  repository suite passes 1,335 in an isolated detached worktree.
- Implemented fix: add a pure payload fitter with a 640–1,600 character range,
  strict `success`/`tool` inputs, deterministic JSON, binary-search preview
  fitting, bounded diagnostics, compact artifact fallback, and bounded input
  traversal. Wire exchange now reuses its maximum instead of defining a second
  authority.
- Remaining limitation: this change does not derive payloads from event-loop
  results, persist artifacts, construct full result batches, or run provider
  rounds.

### Bounded provider result artifact projection

- Observed failure: the aggregate runner projected text, file, and code result
  artifacts inside one large stateful class, so the reusable payload fitter had
  no independent bounded producer for previews, evidence labels, or
  project/provider artifact lineage.
- Validation evidence: the regression suite fails because the projection module
  is absent on the stacked base; 22 tests pass after implementation for large
  text lineage, complete and partial files, declared windows, code handoff,
  scalar fallback, source immutability, literal controls, bounded identities,
  exact artifact limits, aggregate file-list characters, and non-finite values.
  The complete provider-focused set passes 261, and the complete repository
  suite passes 1,357 in an isolated detached worktree, followed by successful
  source compilation and diff validation.
- Implemented fix: add one pure result projector with a 480-character preview,
  the existing 200,000-character input authority, SHA-256 artifact references,
  explicit inline/preview/window semantics, bounded scalar and collection
  fields, and no artifact storage or tool execution.
- Metadata impact note:
  - Facts: a model-facing bounded artifact view and body-free lineage reference.
  - Authoritative producers remain the existing tool-result metadata and future
    provider runner; this projection is derived and runtime-only.
  - Control impact: the caller must supply a literal declared-window completion
    fact; projection does not infer or grant read completion, permission,
    mutation authority, persistence, or execution.
  - Existing contracts reviewed: `ToolResultMetadata`, text/file/code artifact
    metadata, `ProviderToolEvidenceCoverage`, `LLMToolResult`, and the shared
    payload fitter. No new `MetadataKind` or second persisted fact is added.
  - Serialization and migration: dictionaries are passed to the existing
    deterministic JSON fitter; no historical migration is required.
- Remaining limitation: code artifact bodies are not yet registered for writer
  resolution, and event-loop results are not yet correlated into a complete
  `LLMToolResult` batch or provider continuation.

### Correlated provider tool-result batches

- Observed failure: event-loop result maps, typed recoverable errors, duplicate
  blocks, and missing batch executions had no independent strict adapter into
  one bounded `LLMToolResult` per assistant call. The aggregate runner used a
  dictionary comprehension that could silently overwrite duplicate provider
  IDs and mixed correlation with stateful round control.
- Validation evidence: the regression suite fails because the result-batch
  module is absent on the stacked base; 27 tests pass after implementation for
  out-of-order correlation, fixed aborts, typed errors, duplicate blocks,
  declared windows, duplicate/extra IDs, tool mismatch, literal success,
  omitted null diagnostics, local-result isolation, exact 32-call limits,
  contradictory states, source immutability, bounded controls, empty-batch
  budget validation, and missing execution/error evidence. The complete
  provider-focused set passes 288, and the complete repository suite passes
  1,384 in an isolated detached worktree, followed by successful source
  compilation and diff validation.
- Implemented fix: add one pure batch adapter that validates all response,
  event-loop, error, duplicate, and declared-window identities before emitting
  results. It reuses the artifact projector and sole payload fitter, preserves
  assistant call order, and performs no execution or provider request.
- Remaining limitation: the adapter does not register code bodies, build the
  assistant/tool continuation messages, compact history, dispatch another
  request, or own provider round state.
