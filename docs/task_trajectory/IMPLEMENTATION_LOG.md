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

# 2026-08-09

## 今日摘要（适合汇报）

- 进入 H8R2AI-2F Compact raw/segmented paired control 的 2F-0 离线门禁；未发起真实
  Provider 请求。
- 发现并修复 receipt 只统计成功 `responses`、且 retry/fallback 可被硬编码为 0 的证据缺口。

## 已完成切片

### [已完成] H8R2AI-2F-0：Provider attempt 与恢复计数证据

- 观察到的失败：`RecordingLLMClient.responses` 不包含 transport/provider 异常；原聚合器
  用 `len(responses)` 作为调用数，并将 retry/fallback/campaign retry 缺失字段当作 0，
  可能产生 suspicious success。
- 根因判断：request ledger、successful response、typed budget diagnostic 和 tool-loop
  recovery counter 的 owner/语义没有在 campaign receipt 聚合层分开。
- 实现修复：以 `requests` 统计 provider attempts，单独统计 responses 和 failed attempts；
  `ProviderBudgetDiagnostic.provider_attempt_failed` 负责失败证据；新增
  `ToolLoopMetadata.retry_count`/`fallback_count` 并由 runner 计数；缺失 telemetry 保持
  unknown，real/postrun/compact verifiers fail closed。
- 验证证据：2F focused suite **18 passed**；补充 H8R2AI 离线回归 **29 passed**；核心
  tool-loop/metadata/provider suite **250 passed**。
- 剩余限制：尚未同步目标隔离副本，也尚未运行真实 R/K campaign；旧 receipt 缺少新 counters
  时不能用于收益归因。真实请求前仍需轮换已暴露的旧凭据。

### [已完成] H8R2AI-2F-1：R/K selection 与目标绑定

- 验证证据：本机 ready-only selection 为 `sha256:e7b8055a…d69fbee`；远端同源重建的
  ready-only selection 为 `sha256:0b1c2c1f…d66fee51`；远端 target-bound selection 为
  `sha256:c4855d99…be111a63`。绑定校验通过，execute root 保持不存在，Provider calls、
  project mutations、memory/network/writer/verification side effects 均为 0。
- 处理细节：本机绝对 source paths 未被伪造搬运；在远端同一脚本重建等价 selection 后再绑定，
  因此 target source hashes 真实指向 `openpilot-air` 隔离副本。
- 剩余限制：远端副本不是当前 Git checkout，source commit 记录为 unknown-uncommitted；
  真实 campaign 尚未执行。

### [已完成] H8R2AI-2F-2：远端 readiness

- credential-free readiness 已通过（hash `sha256:5b597dce…968698069`），保留 0 Provider
  calls、0 mutation 证据；官方 tokenizer 通过 checksum 复制到远端用户缓存。
- 远端 ready-only Compact campaign plumbing 也通过（hash
  `sha256:eda3e51b…6dc3a8beb`）：`R1,K1,R2,K2` schedule 已冻结，0 completed arms、0
  transport、0 mutation；这只是传输前管线验收，不是 Compact 收益结果。
- 发现并修正 readiness 脚本未配置 tokenizer path 时把工作目录当文件读取的问题；现在缺失或
  非 regular file 会明确 fail closed。
- credentialed readiness 已通过（hash `sha256:1a8a9ff7…d7054a96`），只在关闭回显的 PTY
  stdin 中注入新 key，0 Provider calls、0 mutation，credential 未序列化；随后才获准进入
  真实 campaign。

### [已完成] H8R2AI-2F-3/4：真实 Compact paired control 与独立验收

- 实验：真实 DeepSeek provider、完整 mutation/tool-loop 架构，交错 `R1,K1,R2,K2`；R/K
  只改变 raw history 与 deterministic segmented Compact 投影，reasoning 固定 disabled。
- 验证证据：4/4 arms passed；16 attempts/responses、0 failed、0 retry、0 fallback、0
  unknown usage、0 cap-hit；每臂 writer=1、精确 pytest=1、required omission=0、权限和
  public API 检查通过。独立 verifier receipt 与 campaign hash 已写入
  `PHASE_H8R2AI_2F_COMPACT_PAIRED_CONTROL_RESULT.md`。
- 收益：两次重复合计 input prompt `16,150 → 7,867`（下降 **51.3%**），completion
  `996 → 908`（下降 8.8%），总 measured tokens `17,146 → 8,775`（下降 **48.8%**）；
  provider calls 两臂均为 8，未证明调用次数下降。
- 剩余限制：这是固定 calculator fixture 的因果证据，不能外推到多文件、长历史或其他
  provider；多文件 Compact 已由 H8-R2X/Z 覆盖，下一步进入 provider-native required
  constraint 长历史实验。

### [已完成] H8R2AI-2F-5：Compact verifier hardening

- 观察到的证据缺口：独立 verifier 没有要求每个 arm 的 `campaign_retries` 具备明确
  typed evidence，也没有独立比较 receipt-derived campaign aggregate 与顶层
  `side_effects`；缺失或漂移可能被误报成零。
- 修复：real-execution、postrun 和 Compact verifier 现在对
  `campaign_retries_known` fail closed，并从 immutable arm receipts 重算并严格比较
  provider attempts/responses、失败 attempts、mutation、writer、verification、retry、
  fallback 和 campaign retry aggregate；汇总显式输出 `campaign_retries`。
- 验证证据：H8R2AI focused suite **31 passed**；compileall/diff-check 通过；新增缺失
  retry 与 aggregate 漂移负例均按预期拒绝。对 `openpilot-air` 不可变 2F campaign
  的只读复验为 `verified`，16 provider calls、campaign retries=0、failed attempts=0、
  unknown usage=0、cap-hit=0，原 campaign canonical hash 不变。
- 剩余限制：该修复只完善证据验收，不扩展 Compact 或 Provider 结论；target selection
  仍记录 `source_commit_sha/source_tree_sha256=unknown-uncommitted`，后续阶段必须在
  新 selection 中绑定可审计 source snapshot。

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

### Context Phase 13：mainstream reasoning adapter contract

- 阶段计划：先写 `docs/context_management/PHASE_13_REASONING_ADAPTERS_PLAN.md`，将
  provider-neutral intent、显式 profile、provider payload、usage observation 与
  native transport 接入边界分开；不通过 model/endpoint 字符串推断能力。
- 原因探查：此前 `render_reasoning_transport()` 在 resolver 内用 provider-specific
  分支硬编码，只覆盖 OpenAI-compatible Chat Completions 与 DeepSeek，且没有统一的
  reasoning usage observation；这会让 Anthropic 的 adaptive/manual thinking 和
  Gemini 的 level/budget 语义无法安全表达。
- 实现修复：新增 `core/reasoning_adapters.py` 与版本化 registry，支持
  OpenAI Chat、DeepSeek Chat、Anthropic Messages、Gemini GenerateContent 的最小
  request rendering 和 usage normalization。新增嵌套 `ReasoningUsageObservation`；
  缺失 reasoning token 字段保持 unknown。`LLMClient` 通过 registry 记录 observation，
  对当前尚未接入的 native Anthropic/Gemini transport fail closed。
- 验证证据：新增 adapter fixture **7 passed**；reasoning/runtime focused **142
  passed**；覆盖 profile 选择、wire shape、adaptive/manual budget、Gemini level/budget、
  usage unknown、空 visible + `finish_reason=length` 和 native transport gate。
  未执行 Anthropic/Gemini 网络调用，未改变 production default reasoning 或 Compact。
- 剩余限制：Anthropic/Gemini 目前只有 adapter contract 与离线归一化，尚无原生 HTTP
  client、model-version capability matrix 或真实 provider paired quality 数据；下一
  阶段需独立设计 native transport 与真实 provider shadow，不得把它们与 Compact 收益
  实验混为一个变量。

### Context Phase 14：native Anthropic/Gemini transport

- 阶段计划：先写 `docs/context_management/PHASE_14_NATIVE_TRANSPORT_PLAN.md`，把
  native transport 限定为一次 HTTP attempt + response normalization；retry、JSON repair、
  cache 和 completion evidence 继续由 `LLMClient` 统一拥有。
- 原因探查：Phase 13 的 native profiles 已能渲染 provider payload，但旧 client 只会
  调用 OpenAI-compatible `chat.completions.create()`；直接放行会把 Anthropic/Gemini
  请求发到错误 wire protocol。
- 实现修复：新增 `core/native_llm_transport.py`，实现 Anthropic `/messages` 与 Gemini
  `:generateContent` 的 system/content 转换、reasoning merge、JSON response 配置、
  response normalization、HTTP error 分类和 proxy-safe native attempt。`LLMClient`
  按 resolved `ReasoningTransportFamily` 路由，原有 OpenAI-compatible path 保持不变；
  native streaming 及未覆盖 response shape fail closed。Anthropic manual budget 低于
  1024 或不小于 `max_tokens` 时在发请求前拒绝。
- 验证证据：native fixture/route **5 passed**，reasoning + native + phase1/runtime
  focused **177 passed**；compileall/diff-check 通过。未执行真实 provider/network，未
  改变 Compact 或默认 reasoning。
- 剩余限制：native transport 目前不支持 streaming、tools、图像内容或完整 provider
  structured-output 能力；profile 仍需由部署声明对应的模型版本。下一步只能在显式
  opt-in、低风险、只读 shadow 中验证真实 usage、finish reason、JSON validity 和
  reasoning token 行为，不能直接宣称 Compact 收益。

### Context Phase 14：native transport readiness decision

- readiness 检查：当前加载配置只有 DeepSeek OpenAI-compatible endpoint 和 key，未配置
  Anthropic/Gemini native profile 或 endpoint；检查只输出 presence，未打印密钥。
- 决策证据：不发起 Anthropic/Gemini 网络请求。native fixture、focused regression 和
  full Code suite 均通过；但离线 adapter/transport 通过不能证明具体 deployed
  model/version 接受同一 reasoning mode。
- 出口：Phase 14 offline GO；真实 native shadow 保持显式 opt-in，必须先具备 versioned
  profile、provider endpoint/key、只读小 ceiling 请求，以及完整 usage/finish/reasoning/
  JSON validity receipt。DeepSeek 现有配置只继续用于独立 OpenAI-compatible reasoning
  实验，不作为 native provider 兼容证据。

### Context Phase 15：DeepSeek tool-call round-trip

- 观察到的问题：现有 `LLMMessage` 只保留 `role/content`，`LLMRequest` 没有 `tools`，
  provider 返回的 `reasoning_content` 与 `tool_calls` 无法进入下一轮 history；直接使用
  累计可见文本会违反 DeepSeek thinking + tools 的续接协议。
- 修复内容：新增严格的 `LLMToolDefinition`、`LLMToolCall`、`LLMToolResult` 和
  assistant/tool role 校验；`LLMClient` 接入 tools payload、response normalization、
  cache identity、diagnostics，以及 OpenAI-compatible 流式 tool-call 分片重组；新增
  `append_deepseek_tool_round_trip()` 校验 reasoning evidence、call-ID 唯一性、结果数量和
  顺序。legacy `chat()` fallback 改用 compact renderer，普通消息不会因为新增字段膨胀。
  Anthropic/Gemini native tools 暂未实现时明确 fail closed。
- 验证证据：DeepSeek 专项 **11 passed**；定向 reasoning/runtime/LLM/context **180
  passed**；Code 全量 **1064 passed**；compileall 和 diff-check 通过。详细结果见
  `docs/context_management/PHASE_15_DEEPSEEK_TOOL_ROUNDTRIP_RESULT.md`。
- 剩余限制：尚未把项目现有 JSON `tool_event_loop` 迁移为 provider-native tools；真实
  DeepSeek tool shadow 尚未执行；Anthropic/Gemini tools 仍需独立 wire contract。当前
  阶段只证明离线 transport round-trip contract，不等同于真实任务收益。

### Context Phase 16：DeepSeek tool-call round-trip 真实 shadow

- 阶段计划：固定显式 `deepseek-chat-known:v1`，最多 3 次 provider calls，只使用本地
  `get_date` mock，不读写项目、不改变 `.env`/memory/runtime budget；receipt 只记录 hash、
  字段 presence/长度、call ID、usage 和 finish reason。
- 真实证据：DeepSeek `deepseek-v4-flash` 完成 2 次 calls。首轮
  `finish_reason=tool_calls`，reasoning content 存在，reasoning tokens=22；第二轮 request
  roles 为 `user → assistant → tool`，assistant reasoning 与 call ID 均被回传，tool result
  使用同一 ID；最终 `finish_reason=stop` 且无 tool call。`errors=[]`、`project_mutation=false`。
- receipt：`experiments/full_architecture_context_observation/runs/deepseek_tool_roundtrip_shadow/20260806T031656Z/receipt.json`；
  详细结论见 `docs/context_management/PHASE_16_DEEPSEEK_TOOL_ROUNDTRIP_SHADOW_RESULT.md`。
- 结论边界：真实 transport round-trip 已验证；项目 JSON `tool_event_loop` 尚未迁移，
  也尚未证明真实任务收益、权限映射或工具失败恢复。下一阶段若接入运行时，必须先把
  provider call ID 映射到 `ToolCallMetadata` 并单独验证权限、参数、恢复和预算。

### Context Phase 17：provider tool call admission boundary

- 阶段计划：先完成 metadata inventory/impact note，再把外部 provider ID 作为
  `ToolCallMetadata.provider_call_id` 的可选关联字段；不覆盖项目 `call_id`，不新增
  `MetadataKind`，不替换 JSON `tool_event_loop`。
- 实现修复：新增 `core/provider_tool_admission.py`。它把 provider function arguments
  解析为 `ToolInputMetadata`，检查 registry/executor/required fields、权限确认和
  `RuntimeBudgetMetadata`，只有 admission 通过才生成可交给现有 executor 的
  `ToolSelection`；失败均生成 typed `ToolErrorMetadata`，`provider_tool_error()` 同时
  保留 project/provider 两种 correlation ID。
- 验证证据：admission/metadata focused **9 passed**；Code 全量 **1072 passed**；
  compileall、receipt JSON 校验和 diff-check 通过。详细结果见
  `docs/context_management/PHASE_17_PROVIDER_TOOL_ADMISSION_RESULT.md`。
- 剩余限制：生产 runtime 尚未自动执行 provider-native calls；仍需将 admission 结果接入
  checkpoint、executor、state updater、event emitter，并验证并行调用、tool result 回传、
  recovery retry 和用户确认交互。

### Context Phase 18：provider-native tool execution bridge

- 观察到的问题：Phase 17 已能把 DeepSeek 外部 tool call 安全映射为项目-owned
  `ToolSelection`，但 admission 结果尚未进入真实工具事件生命周期；如果直接在 provider
  适配层执行，会绕过 checkpoint、状态记账、验证和诊断边界。
- 实现修复：新增显式 `ToolEventLoopRunner.run_provider_tool_calls()`。它只接受已经
  admission 的结果，沿用 pending/running/completed/error 事件、项目上下文绑定、编辑守卫、
  checkpoint prepare/observe、`ToolExecutor`、`StateUpdater`、验证和诊断钩子；失败不会
  被标为成功。`provider_call_id` 仅作为外部线关联字段传播，项目 `call_id` 继续负责权限、
  checkpoint、预算和恢复。现有 JSON planner 未自动迁移，桥接默认关闭。
- 验证证据：新增 provider execution bridge **5 passed**；工具事件/检查点 focused
  **136 passed**；Code 全量 **1077 passed**；compileall 和 diff-check 通过。未发起真实
  provider 请求，也未修改项目文件。
- 剩余限制：当前批次在首个 blocked/failed call 后 fail-stop；尚未由桥接自行构造并发送
  DeepSeek `role=tool` 回传消息，调用方仍需使用已有 round-trip helper。并行调用、真实任务
  canary 和收益实验继续保持独立阶段。

### Context Phase 19：provider-native real-task readiness

- 观察到的问题：Phase 18 已能执行 admission 结果，但没有完整编排下一轮 provider 请求；
  tool result 需要 compact projection，且 context assembler 原本无法保留 assistant tool calls、
  `reasoning_content` 与 `role=tool` 的结构化历史。
- 实现修复：新增 `core/provider_tool_roundtrip.py` 和显式
  `ToolPlanningTaskExecutor.execute_provider_tool_task()`。每轮使用 context assembler，并为
  provider tool schema 预留 tokenizer token；工具调用经过 admission 和既有执行生命周期后，
  以带原始 provider ID 的紧凑 JSON `LLMToolResult` 回传。tool-call assistant/tool candidates
  设为 required + non-truncatable，重建请求时保留原始 reasoning/tool-call 字段。入口默认关闭，
  只允许显式 allowlist；写工具还需要代码级 mutation opt-in 与用户确认。
- 验证证据：provider round-trip/task entry **5 passed**；Code 全量 **1082 passed**；真实
  DeepSeek read-only canary 2 requests 成功，首轮 `tool_calls`、次轮 `stop`，README SHA-256
  前后相同，`project_mutation=false`。receipt 位于
  `experiments/full_architecture_context_observation/runs/provider_tool_real_task_canary/20260806T103751Z/receipt.json`。
- 剩余限制：当前只达到显式、有限轮数的只读真实任务入口；写任务仍需独立 allowlist、确认、
  验证命令和 canary，不得因为只读 canary 成功就自动放开 mutation。

### Context Phase 20：provider tool execution recovery

- 观察到的问题：真实只读路径复现中，`file_reader` 对仓库根目录的调用被正确标记为
  `FileReaderDirectoryPath`、`recoverable=true`，但 provider round-trip 仍因“只允许 admission
  错误恢复”在第一轮终止，未把执行错误作为下一轮输入。
- 实现修复：round-trip 现在只对 `file_read` 且无 mutation/command/code-execution 能力的
  recoverable execution failure 继续；工具事件批次仍在失败点 fail-stop，所有 provider call ID
  都生成对应的 `role=tool` 结果。checkpoint、验证、不确定副作用和写工具错误保持 terminal。
- 验证证据：新增恢复、混合批次 ID 保序、checkpoint fail-stop 测试；focused provider suite
  **13 passed**；Code 全量 **1085 passed**，compileall 和 diff-check 通过。真实复现第一轮
  recoverable error 已进入第二轮 `role=tool` history，随后因 provider 一次发出四个读取调用而
  在 required context budget 处 fail-closed；receipt 位于
  `experiments/full_architecture_context_observation/runs/provider_tool_real_task_repro/20260806T122654Z/receipt.json`。
- 剩余限制：真实 provider 可能仍选择错误工具参数；本修复只保证错误反馈和安全继续，不替模型
  选择正确路径，也不开放 `multi_file_reader` 或任何写工具。

### Context Phase 21：provider tool context compaction

- 观察到的问题：Phase 20 已把 recoverable execution error 正确送回 provider，但真实重跑在
  后续多轮中积累了大量 required assistant/tool history，导致 `Required context cannot fit`
  fail-closed。
- 实现修复：provider tool result 改为有上限的确定性 projection，保留 status/error、artifact
  kind、source/provider call ID、hash、大小和短 preview；完整 typed artifact 仍由执行证据持有。
  旧 tool round 进一步压缩，单轮 fan-out 根据剩余 prompt headroom 限制，超出的调用返回
  `ProviderToolBatchAborted`。
- 验证证据：provider/context focused **45 passed**；Code 全量 **1089 passed**，compileall 和
  diff-check 通过。真实 receipt 为
  `experiments/full_architecture_context_observation/runs/provider_tool_real_task_repro/20260806T124816Z/receipt.json`：
  不再出现 required-context budget 错误，4 轮后因显式 round limit 停止，`project_mutation=false`。
- 剩余限制：provider 仍可能在预算可用时重复请求错误路径；下一问题是 evidence-grounded
  tool-loop stopping/target selection，而不是上下文溢出。

### Context Phase 22：provider tool progress governance

- 观察到的问题：Phase 21 已消除 `Required context cannot fit`，但真实只读复现中 provider
  在四轮内重复发出读取请求，最终只触发显式 round limit；这说明剩余根因是重复目标和无进展，
  而不是 compact 预算。
- 实现修复：provider-native runner 现在对工具名和路径参数生成跨轮规范化指纹并保留 typed
  attempt ledger。已经尝试过的同一输入返回 `ProviderToolDuplicateAttempt`，不会再次执行；
  连续轮次没有成功工具证据时返回 `ProviderToolNoProgress`，不再发起投机性下一轮。对
  inspect/analysis/investigate/codebase_understanding 等非 mutation 任务，`Task.read_files`
  同时作为 prompt 约束和 admission 的 canonicalized scope，越界读取返回可恢复的
  `ProviderToolScopeViolation`。JSON planner、普通 ToolEventLoopRunner 和 mutation 路径未改动。
- 验证证据：新增 read scope、相对/绝对路径指纹、完整文件分页阻断、duplicate 阻断、no-progress
  停止和 entry-point 传播测试；Code 全量 **1095 passed**，compileall 与 diff-check 通过。真实
  receipt 为 `experiments/full_architecture_context_observation/runs/provider_tool_real_task_repro/20260806T131353Z/receipt.json`：
  provider 在首轮成功读完四个显式文件后，后续用不同 offset/max_lines 重读同一文件；这些调用均被
  `ProviderToolDuplicateAttempt` 阻断，3 次 provider request 后以
  `ProviderToolNoProgress after 2 round(s)` 停止，未发生 context budget 错误，
  `project_mutation=false`。
- 剩余限制：当前只判断规范化重复和“是否产生成功证据”，不判断两个不同文件读取的语义覆盖度；
  后续若增加 evidence coverage，应另立阶段，不能把语义判断混进确定性安全边界。

### Context Phase 23：real-task budget profile

- 观察到的问题：provider-native canary 的 4,096 prompt、1,600 单轮 completion、12,000 总
  completion 和 3 轮上限适合安全 smoke test，但不足以代表长程真实任务。
- 实现修复：新增 typed `canary` / `real_read_only` profile；real profile 仅允许显式非 mutation
  provider 入口，提供 12,288 prompt、4,096 单轮 ceiling、24,000 总 completion、8 轮、40 calls、
  60 reads。provider runner 每轮根据 runtime 剩余总 completion 动态分配上限，并按真实 usage
  reconcile；无 usage 时保守保留 reservation。JSON planner、mutation 路径和默认 canary 未放大。
- 验证证据：新增 profile 值、入口传播和 usage reconciliation 测试；Code 全量 **1098 passed**，
  compileall 与 diff-check 通过。真实任务脚本改为显式 `real_read_only` profile，receipt 为
  `experiments/full_architecture_context_observation/runs/provider_tool_real_task_repro/20260806T142808Z/receipt.json`：
  真实请求的动态 completion ceiling 为 3000、3327、3296，3 次请求后仍由 no-progress 策略停止，
  没有 context budget 错误，`project_mutation=false`。
- 剩余限制：这些值是受控实验起点，不是全局最优；后续需与 canary 对照任务成功率、耗时、调用数、
  token 使用、compact 决策和失败原因。

### Context Phase 24：budget profile A/B experiment

- 实验设计：同一 DeepSeek 只读 path-grounding 任务、同一 `file_reader` allowlist 和相同
  `Task.read_files`，分别运行 `canary` 与 `real_read_only`；两组均禁止 mutation，只写独立 receipt。
- 结果：canary 在首轮 completion 打满 1600（reasoning 1237，`finish_reason=length`），第二轮
  prompt 达到 3319 后触发 `Required context cannot fit`。real profile 的动态 ceiling 为
  3000/3362/3296，3 次请求内没有 context overflow，最后以 `ProviderToolNoProgress after 2 round(s)`
  停止；两组 `project_mutation=false`。
- 结论边界：本对照证明 real profile 移除了当前 canary 的预算失败边界，但两组都未完成最终任务，
  不能据此宣称任务成功率提升。后续需重复同类任务或建立小型固定任务矩阵，比较最终答案质量、调用数、
  token、compact 决策和停止原因。

### Context Phase 25：budget profile task matrix

- 实验设计：将同一 DeepSeek 只读 provider 对照扩展为 `single_file`、`two_files` 和
  `long_reproduction` 三个固定任务；canary 与 `real_read_only` 各运行一次。两臂均只开放
  `file_reader`、声明显式 `Task.read_files`、禁止 mutation，并保存请求、usage、停止原因和文件哈希。
- 结果：六次运行均在 3 个 provider request 后以
  `ProviderToolNoProgress after 2 round(s)` 结束；首轮允许的读取成功，后续重复路径被
  `ProviderToolDuplicateAttempt` 阻断。长任务另有一次越界读取 `setup.py`，被
  `ProviderToolScopeViolation` 拒绝。canary 的 prompt/completion 配额为 4,096/1,600，real
  profile 为 12,288/4,096；两臂均未发生 context overflow，所有项目 sentinel 均未变化。
- 结论边界：本矩阵没有证明 real profile 的任务质量提升，因为两臂都在相同的重复/无进展边界停止；
  当前限制信号是 provider target selection 与 progress evidence，而不是预算不足。后续应先让
  full-file evidence 满足对应读取需求，并要求新一轮必须产生新的 scoped evidence 或最终答案，
  再重新做预算收益实验。

### Context Phase 26：provider tool evidence coverage and safe finalization

- 观察到的问题：Phase 25 的三类真实任务都已成功读取允许文件，却在重复读取后以
  `ProviderToolNoProgress` 停止；完整 artifact 与 provider 可见的 bounded preview 没有被区分，
  因此没有可靠的最终回答路径。进一步复现确认，未配置显式 DeepSeek capability profile 时，
  finalization 请求会以 `finish_reason=length` 结束，reasoning tokens 吃满 completion ceiling，
  visible content 为空。
- 实现修复：新增 runtime-only `ProviderToolEvidenceCoverage`，引入只读能力门控、投影感知分页、
  每源 page cap、duplicate/no-progress 一次性 no-tools finalization 和 fail-closed 错误。完整
  artifact 只保留有界 source excerpt，分页不能覆盖完整证据；finalization 指令放在 digest 前面，
  digest 另有字符上限，避免 context builder 裁掉停止契约。`file_reader` 现在尊重 full mode 下的
  `max_lines`/`offset`，并接受 `range`/`offset` aliases。reasoning 层使用显式 capability profile：
  DeepSeek disabled continuation 不再强制要求 `reasoning_content`，reasoning 耗尽被标记为
  `ProviderToolFinalizationReasoningExhausted`。
- 验证证据：Code 全量 **1113 passed**；focused provider/DeepSeek **43 passed**；compileall 和
  diff-check 通过。显式设置 `deepseek-chat-known` 与 disabled tool-event reasoning 后，
  `real_read_only` 最新 run 的 single/two 通过质量门槛，long 仅质量门槛失败，分别使用
  3/5/3 requests；每项 `project_mutation=false`，并各触发一次 finalization。紧邻的
  上一次 replicate 三项均通过，说明剩余是 provider 语义回答方差而非停止/权限故障。最终 receipts 位于
  `experiments/full_architecture_context_observation/runs/budget_profile_task_matrix/`
  下的 `20260806T154232Z`、`20260806T154236Z` 和 `20260806T154246Z` 目录。canary 的
  single/two 通过，long 仅质量门槛失败且仍安全停止、未发生 mutation。

### Context Phase 27：provider boundary hardening and semantic quality diagnosis

- 观察到的问题：Phase 26 的长任务质量结果在相邻真实复现中有波动；失败回答实际是在拒绝
  验证未纳入 `Task.read_files` 的路径，而不是 context overflow。另发现 provider task entry
  可让显式 `max_rounds` 超过 typed profile，且 direct `ProviderToolRoundTripRunner` 没有独立的
  code-level mutation opt-in。
- 实现修复：任务入口拒绝超过 profile 上限的显式 rounds，并把配置 rounds clamp 到 profile；
  runner 新增默认关闭的 `allow_mutations`，mutation 工具必须同时满足 code-level opt-in 和
  user confirmation，否则在发出 provider 请求前 fail-closed。task failure details 保留 stop
  reason/evidence coverage；矩阵 quality receipt 单独记录 `scope_limited_refusal`，但不放宽
  成功门槛。
- 验证证据：focused provider/DeepSeek **47 passed**；Code 全量 **1117 passed**；compileall 和
  diff-check 通过。最新显式 DeepSeek `real_read_only` matrix 的 single/two/long 均通过，分别
  3/3/3 requests、一次 finalization，`project_mutation=false`；receipts 为
  `20260806T155217Z`、`20260806T155222Z`、`20260806T155227Z`。
- 剩余限制：语义质量仍可能因 provider 输出方差变化；下一轮应选择所有必需事实都在授权 scope 内的
  任务，或单独设计 path-existence capability。mutation 任务仍需独立 canary、验证命令和回滚计划。

### Context Phase 28：fully scoped evidence projection stability

- 观察到的问题：所有必需事实均位于 `Task.read_files` scope 内的两文件只读任务仍会出现
  语义质量失败。根因不是 scope、预算或 mutation，而是 972 行 `enhanced_cli.py` 的 provider
  projection 只稳定保留头尾与符号索引，中段的 `args.once -> _run_once_mode` 和嵌套
  `_execute_agent_generator` 调用关系没有稳定进入 finalization evidence。
- 实现修复：长源 projection 增加有界 local call-edge 与优先级 call-site windows；先保留
  orchestration/guard 调用行，再保留其余边和符号索引，不增加 read scope/page cap，也不重放
  完整 source。实验质量契约同时要求 direct `args.once -> _run_once_mode`，拒绝把 nested
  agent-generator call 当成 direct handler，并允许明确的否定纠正句。
- 验证证据：Phase 28 focused **43 passed**；Code 全量 **1125 passed**；compileall 和
  diff-check 通过。最终 DeepSeek `real_read_only` 重复三次（receipts
  `20260806T162505Z`、`20260806T162514Z`、`20260806T162521Z`）均 `passed`、3 requests、
  1 finalization、两文件 scope 完成、`project_mutation=false`、无 reasoning exhaustion。
- 剩余限制：当前只证明一个 fully scoped 两文件关系任务；local call-site 是有界投影提示而非
  完整 AST 语义图。`file_reader` 对 code 的 `adaptive + offset/max_lines` 仍有未覆盖边界，
  后续另立修复阶段；本阶段没有改变 compact/reasoning 策略，也没有宣称跨 provider Token 收益。

### Context Phase 29：file_reader adaptive window semantics

- 观察到的问题：`file_reader` 对 code/config 的 `adaptive` 请求在显式提供
  `offset/max_lines` 时仍走 `read_full=True` 快捷路径，返回完整文件；Provider round-trip
  则把同一请求当作 window/page read，导致工具结果和 evidence coverage 语义不一致。
- 实现修复：仅在 adaptive 没有显式窗口时使用 file-type full-read 策略；存在
  `offset` 或 `max_lines` 时复用 bounded `_read_text_file`，不新增 metadata/interface 字段。
  `API.md` 同步记录 code/config 的默认 full-read 与显式窗口规则。
- 验证证据：adaptive focused **3 passed**；Code 全量 **1127 passed**；compileall 和
  diff-check 通过；无 Provider、网络、项目命令或 mutation。
- 剩余限制：尚未在 Provider round-trip 中对 adaptive code window 做真实分页计数验证；
  下一阶段的 fully-scoped evidence matrix 负责该验证。本阶段未改变 Compact、Reasoning 或预算。

### Context Phase 30：fully-scoped evidence projection matrix

- 观察到的问题：初始 `adaptive_window_evidence` 只读臂完成了授权读取，却因质量契约要求
  Provider 复述内部实现名、并把合法的 `truncated` 结果字段当作通用负面词，连续 3 次被判定为
  `failed_quality`。
- 验证证据：`single_file_symbol`、`two_file_linkage`、`long_file_middle`、`guarded_branch`
  各 3/3 通过；将质量契约重标定为公开 adaptive 行为后，adaptive 臂 3/3 通过。所有通过运行
  均使用 3 次 Provider 请求、`file_reader` 只读、精确 scope、无 mutation，receipt 保留完整
  source lineage；原始失败 receipt 作为基线保留。
- 实施修复：新增 adaptive 臂重标定计划；将实验质检改为公开行为和 typed fields；支持按任务
  定制 negative-marker 集合，使合法的 `truncated` 元数据不再被误拒；补充离线质量测试。
- 剩余限制：目前只验证固定 DeepSeek profile 下的只读投影稳定性；尚未验证 scope 越界拒答、长会话
  Compact、对话约束持久化、多 Provider 或 mutation/验证契约。

### Context Phase 31：scope-boundary refusal and safety classification

- 观察到的问题：路径越界、跨文件关系越界和工具/权限越界需要分别验证；首轮分类器把
  `outside the allowed read scope`、`cannot be verified` 等明确拒答词形漏判，且一次跨文件控制
  run 在最终化前中断、没有 receipt，不能把日志成功当作实验成功。
- 验证证据：直接路径越界 3/3 `scope_limited_refusal`、控制 3/3 `control_pass`；跨文件越界
  三份 fresh receipt 在修正词形后确定性重分类为 3/3 `scope_limited_refusal`、控制补跑后 3/3
  `control_pass`；authority 越界 3/3 `authority_refusal`、控制 3/3 `control_pass`。越界运行
  均仅完成授权读取、无 scope 扩大、无 mutation、无未授权工具。
- 实施修复：质量分类器要求越界任务必须同时有 scope 标记和明确不可验证/不可推断语义；补充
  `cannot be verified`、`cannot infer` 等词形；增加 authority refusal 分类和离线回归测试；
  对跨文件任务收紧提示，禁止对未读模块做预测；补跑缺失的控制 repetition。
- 剩余限制：拒答质量仍是模型输出层策略，不是 Provider wire-level 的硬 schema；后续 Compact、
  mutation 和多 Provider 实验必须继续检测没有证据却宣称完成的 suspicious success。

### Context Phase 32：long-session constraint persistence and Compact

- 观察到的问题：阶段计划需要同时验证 10/20/50 长历史、撤销后的投影、Goal/Task current/compact
  同源回退，以及 feature/kill-switch 门禁；旧离线测试未把 API/forbidden/typed-authority、
  lineage hash 和 revoked projection 全部纳入阶段门禁。
- 验证证据：10/20/50 treatment `active_constraint_recall=1.0`、compact prompt 均为 2,200 chars、
  50-vs-20 growth=0；full baseline 16,387→108,064 chars。negative typed-candidate recall=0，
  assistant authority=0，确认/拒绝/撤销/identity guards 通过；revoked write-scope projection
  不再出现 active file scope。Goal/Task current/compact 同源，强制 compact failure 恰好一次回退
  current；feature-off/kill-switch 为空请求、零副作用；pre-transport admission 保持不发 Provider。
- 实施修复：补充阶段32 runner 与离线测试；增加 API/forbidden/typed source 检查、revoked branch、
  current/compact/fallback source/turn/constraint/write-scope hash 一致性、空请求和 fallback
  门禁；结果文档明确 deterministic long-history 与短 ingress boundary 的 claim boundary。
- 剩余限制：raw-dialog recall 的独立指标、LLM summary 语义质量、Provider token/多 Provider 或
  真实 mutation 收益仍不属于本阶段；本阶段不调整预算或 Reasoning。50-turn checkpoint/resume
  缺口已由后续 Phase32D 补充实验单独闭环。

### Context Phase 32D：session checkpoint/resume constraint Compact

- 观察到的问题：Phase32 已证明 deterministic long-history 与短 ingress boundary，但明确留下了
  50-turn `SessionIngressState` checkpoint/resume 未验证的限制；现有 metadata 虽已嵌入 ingress
  snapshot 和 runtime constraint equality validator，缺少实际长会话实验。
- 实施计划与证据：`docs/context_management/PHASE_32D_SESSION_CHECKPOINT_RESUME_PLAN.md`；执行
  `stage32_checkpoint_resume.py`，在 turn 25 保存 typed checkpoint，加载后继续到 turn 50，并以
  production `MemoryContextBuilder` 检查恢复后的 Compact。
- 验证结果：`test_stage32_checkpoint_resume.py` **2 passed**；checkpoint checksum、runtime/ingress
  constraint hash、tamper rejection、cross-run/project identity rejection、duplicate/non-monotonic
  tail rejection 和 uninterrupted 50-turn state equality 全部通过；Compact prompt 2,197 chars，三条
  active constraint（scope、pytest、API）、current failure/current action 和 `sha256:` source 全部保留。
  resumed `session_turn_source_hash` 与 uninterrupted ledger、`session_constraints_hash` 与 active ledger、
  request hash 与 uninterrupted Compact 均一致；Provider/network/project mutation 均为 0。
- 结果与限制：Phase32D **PASS（offline runtime checkpoint/resume gate）**，结果见
  `PHASE_32D_SESSION_CHECKPOINT_RESUME_RESULT.md`。这是 direct `RuntimeCheckpointStore` round-trip，
  不等于 `AgentRuntimeController.resume` exact continuation；后者仍需 session execution/bootstrap
  cursor。现有 metadata 对 top-level checkpoint identity 与 nested ingress identity 的恶意不一致仍未
  单独拒绝；OpenAI Phase46D 仍独立受凭据闸门限制。

### Context Phase 32E-0：checkpoint/ingress execution identity contract

- 观察到的失败：恶意 checkpoint 可以让顶层 execution `session_id` 与嵌套
  `SessionIngressState.identity.run_id` 不一致；`RuntimeCheckpointMetadata`、store 和 Controller
  resume 之前都可能接受这条混合身份。
- 先写负向测试，修复前稳定 `DID NOT RAISE`；最小修复绑定
  `checkpoint.session_id == ingress.identity.run_id`。顶层 `checkpoint.run_id` 保留为
  DiagnosticRecorder/checkpoint-store 路由 ID，未被错误地绑定到 ingress。
- 验证：checkpoint 与 Controller checkpoint/resume focused subset **59 passed, 36 deselected**；
  Phase32E malformed identity lane 在持久化前拒绝。契约说明同步到 `API.md` 与
  `docs/metadata/CONTRACT_CATALOG.md`。

### Context Phase 32E-B：AgentRuntimeController resume context canary

- 计划：`docs/context_management/PHASE_32E_CONTROLLER_RESUME_CONTEXT_PLAN.md`；在临时诊断/项目
  根中构造合法 `SessionExecutionCursor` 与 50-turn ingress，使用严格本地 executor，不调用
  Provider、网络、命令或 mutation 工具。
- 结果：`test_stage32e_controller_resume_context.py` **2 passed**；Controller 返回 exact resume，
  cursor/context 注入成功；derived projection、request hash、turn ledger hash、constraint hash
  与初始 assembly 一致；三条 typed constraints、current failure/action、typed source 全保留，
  替换 memory sentinel 未进入 prompt，prompt 2,197 chars，provider/network/project mutation=0。
- 结果与限制：Phase32E-B **PASS（offline Controller resume/context gate）**，结果见
  `PHASE_32E_CONTROLLER_RESUME_CONTEXT_RESULT.md`。它不声称 IntelligentAutopilot end-to-end、
  bootstrap-only resume、真实 Provider 语义质量或 mutation 收益；相关 bootstrap/artifact replay
  仍由现有 Controller 单测覆盖，OpenAI Phase46D 仍受凭据闸门限制。

### Context Phase 32E-A：process-replacement bootstrap resume

- 观察到的边界：Phase32D 和 Phase32E-B 已证明 checkpoint ingress round-trip 与 Controller
  cursor/context 恢复，但 bootstrap-only 的真实子进程退出/替换进程路径尚未有独立 artifact 化
  证据；若 goal hash 未校验，替换进程可能把错误会话送入 executor。
- 实验计划与实现：新增 `PHASE_32E_A_BOOTSTRAP_RESUME_PLAN.md` 与
  `stage32e_bootstrap_resume.py`。子进程使用生产 Controller 在 `TASK_NORMALIZED` 保存
  `SessionBootstrapCursor` 后以状态码 93 退出；替换进程调用生产 `.resume`，严格本地 executor
  只接收 bootstrap，不接收 execution cursor，并记录一次性阶段轨迹。另加 malformed goal hash
  的 fail-closed 负向臂。
- 验证证据：`test_stage32e_bootstrap_resume.py` **3 passed**；child exit=93，goal/session
  identity 一致，replacement call=1，bootstrap-only stage trace 无重复，invalid goal hash 在
  executor 前以 `checkpoint_corrupt` 阻断；provider/network/project mutation=0。结果见
  `PHASE_32E_A_BOOTSTRAP_RESUME_RESULT.md`。
- 剩余限制：仍是 provider-free Controller 边界，不等于 IntelligentAutopilot end-to-end、
  Provider 语义质量、token usage 或 mutation 收益；OpenAI Phase46D 仍受凭据门禁限制。

### Context Phase 32F：IntelligentAutopilot full-entry resume context

- 观察到的缺口：Phase32E-A/B 已覆盖生产 Controller 的 bootstrap 与 cursor/context resume，
  但没有证明公开 `IntelligentAutopilot.resume()` 会把恢复上下文正确交给 Controller；外层
  `resume` 还会执行只读项目环境 reattach，必须单独验证其身份和副作用边界。
- 阶段计划与实现：新增 `PHASE_32F_INTELLIGENT_AUTOPILOT_RESUME_PLAN.md` 与
  `stage32f_intelligent_autopilot_resume.py`。实验实例化生产 `IntelligentAutopilot`，替换的
  仅是 session executor 为严格本地 recorder，并保留真实 Controller、outer resume 和只读
  environment preflight；同时加入冲突 project identity 负向臂。
- 验证证据：`test_stage32f_intelligent_autopilot_resume.py` **3 passed**；公开 outer resume
  exact、cursor/ingress/context lineage、request/turn/constraint hash、required constraints
  和 changed-memory 排除全部通过；冲突 project 在 executor 前阻断；provider/network/project
  mutation=0。结果见 `PHASE_32F_INTELLIGENT_AUTOPILOT_RESUME_RESULT.md`。
- 剩余限制：仍是 provider-free routing/integrity canary，不证明 Provider 语义质量、token
  usage 或 mutation 收益；真实 OpenAI Phase46D 仍受凭据门禁限制。

### Context experiment host boundary：openpilot-air/worke

- 观察到的环境变化：当前 checkout 中的 `experiments/` harness 被迁移到另一台
  `openpilot-air` 机器的 `worke` 工作区，导致本机完整 Code suite 在 collection 阶段找不到
  `stage25`/Phase28–31 runners；这不是实验失败，也不能用本机缺失文件推断 Provider 结果。
- 处理方式：不恢复或覆盖用户已删除的实验目录；新增
  `docs/context_management/EXPERIMENT_EXECUTION_HOST_PROTOCOL.md`，区分生产代码审查主机与
  实验执行主机，规定 source SHA、provider/profile、usage/finish、side-effect counters 和
  receipt integrity 的转移契约；证据索引和完成审计明确历史 469-test 计数来自上一执行主机。
- 验证证据：当前 checkout 的 `Code/src` compileall 通过；排除依赖已迁移 harness 的四个测试文件后
  `Code/tests` **1147 passed**。完整 1163/469 结果仍保留为历史 artifact，待从
  `openpilot-air/worke` 按协议转移并在本机复核。
- 剩余限制：本机无法访问 `openpilot-air/worke`，也无法执行真实 OpenAI Phase46D；下一步需要
  从实验主机转移受校验的结果 artifact，或在该主机继续执行后回传 receipt。

### Context Phase 33：deterministic Compact versus controlled LLM summary

- 观察到的问题：LLM summary 只能是 source-linked derived view；仅有 JSON 或“调用成功”不能证明
  usage、finish reason、source/evidence、token cap 和 compression gain 合法。33B 首轮 harness 将
  `max_retries=0` 误作“一次尝试”，实际跳过了响应循环，暴露了实验入口语义错误。
- 验证证据：33A 9 个注入 case 中仅 valid/valid-chain 接受，其余 unknown usage、length、stale、
  unknown evidence、authority field、over-budget、no-gain 均 typed fallback；44 个 focused
  contract tests 通过。修正 harness 后，真实 DeepSeek 33B 3/3 `stop`、无 tools、3/3 adapter
  accepted，summary 28–31 words、≤80 cap，usage 1,009 tokens，reasoning 未暴露而保持 null，
  project/memory mutation=0。
- 实施修复：新增 33A/33B 实验计划、离线 adapter matrix、真实 Provider summary runner；失败
  receipt 记录异常携带的 response preview/usage/finish reason；将 `max_retries=1` 明确为一次
  Provider/parse attempt；结果坚持 accepted 与 deterministic fallback 分开统计。
- 剩余限制：summary 尚未接入默认 Compact，也未证明语义任务质量、长会话 required constraint
  替换安全、多 Provider 或真实收益；stale fallback 只保留 captured source view，需上游 refresh。

### Context Phase 34：Reasoning routing and Provider adapter matrix

- 观察到的问题：需要把 Compact 与 reasoning 分开，验证通用 intent、显式 profile/adapter、
  usage/finish 归一化和多 Provider 边界；不能把 DeepSeek 的 reasoning 耗尽误判为 Compact schema
  失败，也不能用 DeepSeek credential 冒充 OpenAI。
- 验证证据：offline resolver/adapter/native transport 30 focused tests 通过；DeepSeek real shadow
  四臂中 default128=419/128、reasoning128、length；disabled128=340/65、stop、summary accepted；
  default256=419/256、reasoning256、length；enabled-high128=419/128、reasoning128、length。
  OpenAI profile 在当前 DeepSeek endpoint 下明确 blocked；所有 shadow runs 无 project/memory mutation。
- 实施修复：新增阶段34 matrix runner/manifest 与离线回归；OpenAI readiness 增加 endpoint/credential
  不复用门禁；结果保留完整 provider usage/finish/reasoning evidence，未改变默认 reasoning 或 Compact。
- 剩余限制：真实 OpenAI/Anthropic/Gemini transport 尚未执行；当前结论只适用于显式
  `deepseek-chat-known` profile + endpoint，不能推广成全局 reasoning 默认。

### Context Phase 35：controlled mutation real-task experiment

- 观察到的问题：完整 Provider-native mutation 链路在四次 fresh disposable run 中都未能完成
  “两次授权读取 → 一次受限写入 → 一次精确 pytest 验证”。第一次把 `cat` 发送到
  `command_executor`，被 exact validation admission 拒绝；第二、三次在完整 evidence 已经到达后
  重复 `file_reader`，被 duplicate/no-progress stop；第四次形成了有界候选修改，但缺少已有文件所需
  的 `operation_kind=file_replace`，`file_writer` 在写前以 `FileExistsError` fail closed。
- 实施修复：引入显式 `real_mutation` budget profile，mutation 任务强制 typed read/write scope、
  用户确认、exact validation command/cwd/mode、环境 sentinel、mutation phase/rollback/replay
  receipt；provider-native prompt 固定 file_reader/command_executor/file_writer 的角色，并禁止
  whole-file fallback/replay。补充 admission、round-trip、event-loop、receipt 和 focused stage35
  contract tests。
- 验证证据：四个真实 DeepSeek receipts 均记录 `project_mutation=false`、
  `replay_count=0`、无验证成功；第一条越界检查、重复读取停止、已有文件覆盖拒绝和外部
  `.venv`/checkout sentinel 均按预期生效；Phase35 focused **60 passed**（随后任何补丁需重跑），
  真实运行不计为成功 mutation。
- 阶段结论：安全条件 **conditional pass**，Provider 执行质量 **NO-GO/failed_pre_mutation**。
  这不是 Compact 或 Reasoning 失败证据；授权 evidence 已完整到达，问题集中在 tool-role、
  evidence-to-action 和 writer operation-shape 三个转换边界。禁止立即重跑真实 mutation。
- 下一步：Phase35A 只做离线失败/恢复契约回放，补齐 symlink authority 双向检查和 receipt integrity
  证据；只有负向 arm 全部 fail-closed、正向 fake control 精确写入并验证后，才拟定新的真实
  mutation canary。剩余限制是尚无真实 Provider mutation 成功样本，不能宣称 mutation 收益或
  Compact token 收益。

### Context Phase 35A：mutation failure/recovery contract replay

- 观察到的问题：Phase35 暴露的 inspection-command、duplicate-read 和缺少
  `file_replace` 三类错误如果只靠真实 Provider 重跑，无法区分模型输出方差与工具契约缺口，且会
  引入不必要的真实写入风险。
- 实施修复：补充读/写 scope 的反向 symlink authority 检查；为 Phase35 receipt 增加不含自身字段的
  canonical `receipt_hash`，覆盖预检阻断与正常结果；保留 round-trip duplicate/no-progress、
  exact validation 和 writer replacement 的 typed fail-closed/positive control。
- 验证证据：admission、round-trip、writer、Phase35 receipt/sentinel focused **66 passed**；无
  Provider transport、网络、仓库写入或环境 setup。错误臂均在执行前阻断或停止，正向 replace
  control 只修改目标文件；receipt hash 对 key reorder 稳定、对篡改敏感。
- 阶段结论：Phase35A **PASS（离线契约门禁）**。这只证明失败/恢复边界安全，不证明真实 Provider
  会生成正确的 `file_replace` operation。
- 下一步：先写 Phase35B 单次真实 mutation canary 计划，要求 fresh disposable workspace、显式
  replace 示例、同一 scope/精确 pytest、首个 hard failure 停止；不得直接进入三次 mutation
  收益实验。

### Context Phase 35B：single real-provider mutation canary

- 观察到的问题：显式告知 `operation_kind=file_replace` 后，DeepSeek 确实完成了目标文件的有界
  修改，但 Provider-native loop 在 exact validation 之前调用了通用 RuntimeVerifier，执行了
  `calculator.py --help`；随后精确 pytest 被 `ProviderValidationOrderViolation` 拒绝。实验 snapshot
  还把 runtime 生成的根级 `sketch.json` 误列为用户变更。
- 验证证据：receipt `20260807T015740-1-190f7f07` 显示两次授权读取、一次成功
  `file_replace`、目标行为成立、API 未变、diff=2 行，但 exact validation count=0、项目变更
  含 runtime sidecar、`mutation_phase=failed_after_mutation`、`replay_count=0`。因此不能计为
  mutation 成功或收益样本。
- 根因修复：Provider-native mutation 在存在 typed `Task.validation_command` 时跳过 generic
  post-write verifier，把 exact command 留给下一轮 Provider；Phase35 fixture snapshot 将
  `sketch.json` 归类为 runtime-owned。新增 lifecycle deferral、snapshot 和 receipt regression
  tests。
- 阶段结论：Phase35B **NO-GO**；这是验证交接和观测边界失败，不是 Compact/Reasoning 失败。
  修复后必须先通过 Phase35C 离线门禁，再考虑新的单次真实 canary。

### Context Phase 35C：Provider validation handoff and runtime artifact boundary

- 计划：只验证 writer 后 defer generic verifier、exact validation order boundary、runtime
  `sketch.json` snapshot 分类和 receipt sealing；不调用 Provider、不改变 Compact/Reasoning/profile。
- 当前实现：`ToolEventLoopRunner` 在 provider-native mutation 任务有 typed validation command
  时不再自动执行 entrypoint `--help` fallback；新增 direct regression test；snapshot 排除
  `sketch.json`；receipt 继续使用 canonical self-excluded hash。
- 离线证据：provider admission、round-trip、Phase35 receipt/snapshot focused **67 passed**，
  `PYTHONPATH=Code/src pytest -q Code/tests` **1143 passed**，compileall 和 `git diff --check`
  通过；无网络、Provider transport 或仓库写入。
- 阶段结论：待完成完整 offline gate 后，才允许下一次 single canary；若发现 exact command 仍被
  替代或状态顺序错误，继续停在诊断阶段。

### Context Phase 35D：post-handoff single real mutation canary

- 实验目的：在 35C offline gate 后，仅用一个 fresh disposable workspace 验证完整 Provider-native
  mutation handoff；不估计稳定性、不宣称收益。
- 验证证据：receipt `20260807T020203-1-6b1683de` 通过；两次授权读取、一次显式
  `file_replace`、只修改 `calculator.py`、API 不变、3 行 bounded diff；只运行一次 exact
  `python -m pytest -q tests/test_calculator.py` 且 exit=0；无替代 command、duplicate/no-progress、
  scope violation、fallback 或 replay；4 个 response 的 usage/finish reason 完整；checkout/.venv
  sentinel 未变；`mutation_phase=validated`、`replay_count=0`、receipt hash 完整。
- 阶段结论：single canary **PASS**。这证明当前 mutation 权限/验证契约能安全跨过一次真实
  Provider 任务，但不是三次稳定性、mutation 收益或 Compact token 收益结论。
- 下一步：先进行阶段8的完整架构只读收益实验，固定任务集比较 current、Compact-only、
  Reasoning-only、Provider-adapter-only 和 full context-control bundle；mutation stability/benefit
  仍需独立计划和 fresh workspace。

### Context Phase 36：full-architecture read-only baseline observation

- 实验设计：固定 DeepSeek `real_read_only`、disabled routine reasoning、当前 deterministic
  projection、file_reader-only allowlist 和相同 checkout；`single_file_symbol`、
  `two_file_linkage`、`adaptive_window_evidence` 各重复 3 次。此阶段不切换变量，不作因果收益结论。
- 验证证据：9/9 quality pass、9/9 task completed、project mutation=0、duplicate/no-progress/scope
  violation=0；每次 3 个 Provider requests，第三轮 finalization；usage/finish reason 完整、
  reasoning tokens 未报告。三类任务的 prompt round trajectories 分别为 201→802→1619、
  249→977→3306、311→1041→2339；总 response tokens 为 43,112。
- 观察结论：当前完整只读链路在小型 fully-scoped 矩阵上稳定，但跨轮 prompt 增长确实存在，
  尤其是两文件关系任务；该增长尚不能归因于 Compact 或 Provider adapter，也不能用当前 arm
  宣称收益。
- 下一步：Phase36B 先做离线 controlled-context ablation 契约，再做相同任务/provider/repetition
  的 paired shadow；同时比较总 token（而非只看调用数）、质量、请求、重复/停止和证据覆盖。

### Context Phase 36B：controlled-context ablation contract

- 实验设计：同一 10/20/50-turn session source replay 三臂：full truth、compact without typed
  state、compact with confirmed typed state；不接 Provider、不改变生产策略。
- 验证证据：full-history prompt 16,387→108,064 chars，两个 compact arms 固定 2,200 chars；
  typed constraint arm 在所有长度保留 allowed/forbidden file、exact validation、public API，
  active constraint recall=1.0；negative arm 不带 typed authority；revoked scope、source/turn/
  constraint/write-scope lineage、fallback exactly once、feature-off/kill-switch 和 pretransport
  zero-side-effect gates 全部通过。
- 阶段结论：Phase36B **PASS（offline ablation contract）**。它证明压缩和对话内持久约束有清晰
  prompt-growth/retention 信号，但没有 real-provider quality/total-token causal 结论。
- 下一步：Phase36C paired real read-only shadow；固定 Phase36A 任务、Provider、Reasoning、scope
  和 repetition，显式定义 current/raw-history control 与 controlled arm，比较 total tokens、
  quality、request/no-progress 和 evidence coverage。

### Context Phase 36C：paired real read-only context shadow

- 实验设计：相同 Phase36 任务、scope、DeepSeek `real_read_only` 和 disabled reasoning，raw-history
  与 compact-history 两臂各 3 次；只读 file_reader，无 command/write/network。
- 验证证据：compact-history `single_file_symbol`、`two_file_linkage` 共 6/6 quality pass，均
  3 requests、project mutation=0；raw-history 6/6 在第一轮 typed read-scope failure，未读取源、
  未产生答案、project mutation=0。raw token 较低仅因提前失败，不计 efficiency gain。
- 阶段结论：Phase36C **CONDITIONAL/NO-GO for causal benefit**。受控投影保留质量，膨胀历史臂
  触发 path grounding/scope failure；但当前 receipt 未保存 provider rejected path 的完整 typed
  input，无法区分 context overflow、instruction conflict 与 path hallucination。
- 下一步：Phase36D 做 raw path-grounding diagnosis，补 provider attempt input/response structured
  evidence，并先用离线回放定位根因；在此之前不进入 factorial benefit study，不改变默认策略。

### Context Phase 36D：raw-history path-grounding diagnosis

- 观察到的问题：raw shadow provider 发出相对路径 `Code/src/ui/cli.py`；provider round-trip 未把
  已知 project root 绑定进 event-loop input，二次 scope guard 将其解析为
  `.../Code/src/ui/Code/src/ui/cli.py`。修复该问题后，raw 读取成功但下一轮因长历史占据一个
  required user message，触发 `Required context cannot fit within the configured prompt budget`；
  compact control 正常完成。
- 实施修复：在 `ProviderToolRoundTripRunner` admission→event-loop 边界绑定已知 `project_path`
  到 provider tool input；不改 canonical read scope、不重写 provider path、不放宽权限。实验
  receipt 增加有界 `tool_event_evidence`（tool/path/command/status/typed failure details）。
- 验证证据：project-root binding focused **41 passed**；raw/compact fresh diagnostic 明确记录
  pre-fix duplicated path 与 post-fix context-budget failure；无 mutation。raw token 较低仍是提前
  失败，不计效率收益。
- 阶段结论：路径绑定 defect 已修复；剩余根因是 raw 历史被建模为不可分割 required user message，
  不能直接截断。整体收益实验保持 **CONDITIONAL**，不改变默认 Compact。
- 下一步：Phase36E 做 message-level segmented history offline contract，required constraints 与
  compactable dialogue 分离后，再做小规模 real shadow。

### Context Phase 36E：segmented history offline contract

- 实验设计：required task/constraints、optional dialogue segments、source-linked artifact summary
  四类候选；对照 unsegmented required history、segmented source、segmented compact 三臂；不接
  Provider、不产生副作用。
- 验证证据：unsegmented 9,282-char required candidate 在 1,800-char policy 下 typed
  `budget_insufficient`（不静默截断）；segmented source ready、prompt=1,672 chars；segmented
  compact ready、prompt=398 chars，四个 history segments 均由 `artifact:history-summary` 原子
  compact；required task/constraint 保留且 source IDs/trust 正确；provider/network/project=0；
  focused **3 passed**。
- 阶段结论：Phase36E **PASS（offline segmented-history contract）**。它证明缺失的是候选分段
  和替换边界，不是把 required user message 线性缩短。
- 下一步：新增/接入 provider-native initial-context projection 的最小入口，先做一对 fresh real
  read-only shadow；在此之前不改默认 Compact、不进入 factorial benefit study。

### Context Phase 36F：provider-native initial-context projection

- 观察问题：把 typed segmented history 只用于首轮后，后续普通 message builder 会把 optional
  history 重新视为 required；首次真实 shadow 中 raw 臂首轮读成功、第二轮因 required context
  budget failure 停止，compact 臂正常完成。这暴露的是 retention 语义在 round-trip 边界丢失，
  不是 provider scope 或 Compact 事实错误。
- 实施修复：新增 `initial_context_candidates` 可选入口；请求构造器扣除 provider tool schema
  token 并记录 selected candidate IDs；后续 provider 轮次按同一 typed candidates 重新装配，
  用 structured message mapping 保留 assistant tool-call IDs、reasoning content 和 `role=tool`
  结果，不把 optional history 提升为 required。默认无 projection 路径保持不变。
- 验证证据：focused context/round-trip/Phase36F tests **71 passed**；fresh DeepSeek
  `real_read_only` paired shadow 两臂均 quality pass、3 requests、project mutation=0、完整
  usage/finish receipts。raw segmented provider prompt tokens=7,952，compact segmented=3,130；
  completion tokens 均为334；两臂读同一文件并保持 sentinel 不变。
- 阶段结论：Phase36F **PASS（integration gate；descriptive one-pair shadow）**。约61% input
  token reduction 是当前样本的信号，不能外推为多任务收益或改变默认策略；provider-reported
  reasoning tokens 仍为 null。
- 下一步：Phase36G 做三类 read-only 任务的 paired 多重复 initial-projection shadow，固定
  provider/reasoning/budget/tool contract，并将 provider cache、prompt/completion/total tokens、
  quality、停止与证据覆盖纳入比较；通过后再进入 8C factorial 收益实验。

### Context Phase 36G：multi-task initial-projection shadow

- 实验设计：固定 DeepSeek `real_read_only`、disabled reasoning、file_reader-only、同一 checkout
  和 fully-scoped quality contracts；`single_file_symbol`、`two_file_linkage`、
  `adaptive_window_evidence` 各做两次 raw-segmented/compact-segmented paired shadow。
- 验证证据：离线 focused **74 passed**；12/12 task-arm executions quality pass，36 provider
  requests，project mutation=0，scope/context/duplicate/no-progress failure=0；每份 receipt 有
  provider usage、finish reason、cache hit/miss、selected candidate IDs、evidence coverage 和
  sentinel hashes。reasoning tokens 仍为 provider `null`。
- Provider prompt token：`single_file_symbol` raw 7,943/7,944 对 compact 3,175/3,174；
  `two_file_linkage` raw 9,747/9,747 对 compact 5,052/5,052；`adaptive_window_evidence` raw
  8,749/8,748 对 compact 4,133/4,133。每个任务的每次重复 compact 都低于 raw，约下降
  48.2%–60.1%，request count 与质量没有恶化。
- 阶段结论：Phase36G **PASS（descriptive shadow gate）**。该信号已跨三个只读任务复现，足以
  进入 factorial 收益实验的前置门禁；仍不能外推到多 Provider、mutation 或默认策略切换。
- 下一步：进入 Phase 8C factorial benefit study，固定 task/repetition 并分别比较 current/raw、
  Compact-only、Reasoning-only、Provider-adapter-only、full bundle；同时报告 total input/output
  tokens、quality non-inferiority、calls、recovery、cache 和 evidence coverage。

### Context Phase 37：context/reasoning/provider-adapter factorial pilot

- 设计决策：不把 legacy JSON planner 当作 provider-adapter control，因为它有不同 tool wire
  contract；五个 arm 均走同一 provider-native read-only loop。`provider_adapter_only` 使用 raw
  segmented typed candidates，`compact_only` 使用同一 bounded summary 的普通 user-message 版本，
  从而能诊断 typed initial-context boundary，而不把 planner route 混入收益。
- 验证证据：离线 focused **74 passed**；两个只读任务各五个 arm，10/10 cells quality pass，30
  provider requests，project mutation=0，scope/context/duplicate/no-progress/suspicious-success=0。
  receipts 记录 prompt/completion/total、cache hit/miss、finish reason、reasoning usage、selected
  candidate IDs、evidence coverage 和 sentinel。
- Usage：`single_file_symbol` 的 reasoning_only/full_bundle/current_raw/compact_only/provider_adapter_only
  prompt tokens 为 3,198/3,186/3,362/3,850/8,547；`adaptive_window_evidence` 为
  4,218/4,133/5,044/5,549/9,166。disabled cells reasoning=0；provider-default cells 实际
  reasoning tokens 为 35/25/210 与 223/198/491，未耗尽 completion ceiling。
- 阶段结论：Phase37 **PASS（pilot gate）**。full bundle 在两个任务上有效且方向与 36G 一致，
  但仍不能声明 global quality non-inferiority 或切换默认策略。
- 下一步：执行完整 Phase 8C factorial study，预注册任务/重复数，报告 paired total input/output
  tokens、cache、quality、calls、recovery 和 evidence coverage；第二 Provider 仅在 typed tool
  wire 支持时加入，否则记录 `blocked`。Mutation 仍独立留到 Phase9。

### Context Phase 38：full factorial benefit experiment（首轮停止）

- 预注册矩阵：三个只读任务、五个 arm、每 cell 三次重复；固定 DeepSeek/native tool loop/
  `real_read_only`/same scopes，quality、token、cache、reasoning、calls、evidence 全量记录。
- 停止证据：`single_file_symbol` 15 cells 全部通过；`two_file_linkage` 的 reasoning_only 与
  full_bundle 首 cell 通过；第13个 cell `current_raw/repetition1` tool execution 通过但 quality
  `failed_quality`，漏答 `--once` 关系。该 cell prompt/completion/total=4,737/805/5,542，
  reasoning=190；无 scope/context/mutation/no-progress failure。按 stop gate 未继续、未重放。
- 阶段结论：Phase38 **CONDITIONAL/NO-GO for quantitative benefit claim**。部分单文件 cells
  仍显示 full bundle 相比 raw typed adapter 的输入下降，但 denominator 不完整，不能外推；
  该 failure 是 cross-file provider answer quality miss，不能直接归因 Compact。
- 下一步：Phase39 cross-file quality stability diagnosis，固定 `two_file_linkage` context，
  只切换 provider-default/disabled reasoning，先验证 quality checker 与 `--once` 关系证据；
  质量稳定后用新 campaign root 续跑剩余 Phase38 cells。

### Context Phase 39：cross-file quality-oracle calibration

- 根因证据：Phase38 stopped receipt 的答案已经写出 `args.once` 和 `_run_once_mode`，但旧
  quality fixture 只接受 literal `--once`，造成 `missing_requirements=[["--once"]]` 的 false
  negative。该问题属于实验质量 oracle，不是 provider tool execution、Compact 或 scope。
- 实施修复：`two_file_linkage` requirement 改为 `("--once", "args.once")`；保持
  `args.once -> _run_once_mode` relation；加入正向 alias 与负向缺失回归测试。保存的原始 receipt
  仍保持 `failed_quality`，另写 `oracle_recheck.json` 标记 `quality_oracle_false_negative`，
  无 provider replay。
- 验证证据：offline quality tests **4 passed**；fresh two-file disabled/default pair 均
  quality pass、3 requests、project mutation=0、scope/context/no-progress=0；disabled
  prompt/completion/total=5,057/651/5,708，default=5,231/734/5,965，default reasoning=124。
- 阶段结论：Phase39 **PASS（oracle-calibration gate）**。原 Phase38 仍不能回填成完整收益
  实验，必须新 campaign root 续跑并保持 denominator 透明。
- 下一步：Phase40 factorial continuation，使用修正 oracle 重新执行预注册矩阵；不隐式合并
  Phase38 partial receipts。

### Context Phase 40：corrected-oracle factorial continuation

- 实验设计：新 campaign root、3 tasks × 5 arms × 3 repetitions，45 cells/135 provider requests；
  不合并或 replay Phase38 partial receipts。唯一变化是 Phase39 已验证的 `--once`/`args.once`
  quality alias vocabulary。
- 验证证据：45/45 quality pass、project mutation=0、scope/context/duplicate/no-progress/
  suspicious-success=0；quality non-inferiority、efficiency、request/total-token gates 全部通过；
  receipts/campaign_result 保存 usage、cache、finish、reasoning、selected candidates、evidence
  coverage 和 sentinels。
- Paired medians：full bundle 对 raw typed adapter 的 prompt reduction 为
  `single_file_symbol` 62.8%、`two_file_linkage` 49.0%、`adaptive_window_evidence` 55.8%；
  total reduction 为 59.7%、47.0%、53.6%。full 对 current raw total-token change 为 −9.2%、
  +4.2%、−19.4%，request delta=0。disabled reasoning=0；provider-default arms 的 reasoning
  usage 可归因且未 exhaustion。
- 阶段结论：Phase40 **PASS（pre-registered read-only factorial benefit gate）**。在测试任务和
  DeepSeek native read-only lane 中，compact typed projection 保持质量并降低 input/total tokens；
  不能外推到第二 provider、mutation 或自动切换默认。
- 下一步：制定 feature-flagged read-only bundle canary，current path 可回滚；随后另立 Phase9
  mutation benefit 实验，fresh workspace + explicit scopes + exact validation。

### Context Phase 41：feature-flagged read-only bundle canary

- 实验设计：在 `OPENPILOT_PROVIDER_TOOL_INITIAL_CONTEXT_PROJECTION_ENABLED=false` 的默认契约下，
  对同一个 DeepSeek/native-tool `two_file_linkage` read-only 任务运行 current arm、关闭 flag
  但注入 candidates 的 fail-closed arm、显式开启 compact typed bundle 的 arm；只允许
  `file_reader`，不改变 reasoning、scope、budget 或 mutation authority。
- 验证证据：fresh v3 campaign root 完成：`off_current` 3 requests、quality pass；
  `off_candidate_rejected` 在 provider transport 前失败，request_count=0，错误为
  `Typed initial-context candidates require the explicit read-only projection flag.`；
  `on_full_bundle` 3 requests、quality pass；两条执行臂 project mutation=0，sentinel hashes
  不变。usage 分别为 current `5,056/641/5,697` 和 bundle `5,051/674/5,725`
  （prompt/completion/total）。
- 实施修复：flag-off rejection 聚合从不存在的 `error_type` 字段改为匹配稳定 error text；实验
  run-id 增加微秒，避免同秒相邻 cell 复用 receipt 目录。新增 config/env/API 契约、executor
  fail-closed 检查及 focused canary/flag regression tests。
- 早期问题：v1 的 timestamp collision 和 v2 的 aggregate-only false negative 均为 harness
  问题，没有 provider side effect；v3 fresh root 为正式判定，旧 receipts 保留作诊断证据。
- 阶段结论：Phase41 **PASS（feature-flagged read-only canary gate）**。允许小流量、可回滚的
  read-only bundle 使用，默认仍关闭；不授权 mutation 或未测试 task/provider。
- 剩余限制：Phase9 mutation benefit/safety 仍未完成，必须另立计划并使用 fresh disposable
  workspace、explicit write scope、exact validation、rollback 和 suspicious-success gate，
  不能从 read-only token savings 推断写入安全。

### Context Phase 42 / Phase 9A：mutation stability canary

- 首次观察：三次 fresh mutation receipt 表面为 `passed`，但 `command_executor` 的
  `mode=null` 触发底层默认 `dry_run`，stdout 为 `[DRY RUN] Would execute ...`；旧 oracle 只看
  `success=true`/`exit_code=0`，构成 suspicious success。该证据不计入成功，且审计确认原
  Phase35D receipt 具有同一问题，原 35D PASS 已撤销。
- 实施修复：provider-native typed validation 缺省 mode 归一为 `automatic`，显式 dry_run/
  interactive fail-closed；Phase35 mutation receipt 只有在 automatic、非 dry-run marker、
  exact argv/cwd/exit code 同时满足时才计 validation；snapshot 将任意深度的 `__pycache__`、
  `.pytest_cache` 等 runtime-owned 目录排除，避免真实 pytest 生成物造成越界误报。
- 第二次观察：v2 首次真实 pytest 确实输出 `2 passed`，但 `tests/__pycache__` 被旧递归规则
  误算用户变更，按 hard stop 失败；该 receipt 保留为 harness failure，未重放同一 workspace。
- 验证证据：focused admission/snapshot/receipt tests **22 passed**；fresh v3 root 的 3/3
  repetitions 均 `validated`，每次 2 reads + 1 explicit `file_replace` + 1 automatic exact
  pytest，唯一用户路径为 calculator.py，API unchanged、bounded diff、external sentinel
  unchanged、`replay_count=0`、receipt hash/usage/finish complete。prompt/completion/total
  分别为 `8,504/983/9,487`、`8,982/1,192/10,174`、`8,527/1,031/9,558`。
- 阶段结论：Phase9A **PASS（mutation execution-stability gate）**。该结论只证明固定最小
  mutation lane 的执行与证据链稳定，不证明 Compact/context benefit，也不打开 mutation
  initial-context projection。
- 下一步：Phase9B 另立计划，做 fresh paired current-vs-compact mutation benefit study；固定
  provider/task/reasoning/budget/scopes/validation，独立报告 quality、实际 diff、验证、calls、
  input/output/total/reasoning tokens 和 suspicious-success，不能复用只读 canary 分母。

### Context Phase 43 / Phase 9B：mutation benefit plan

- 计划边界：不复用 Phase41 的 read-only projection flag；新增一个默认关闭的 mutation-specific
  projection gate，只有显式 mutation treatment 才能携带 typed initial-context candidates。
- 配对设计：同一 DeepSeek/provider/task/reasoning/real_mutation budget/scope/validation/fixture
  做三组独立 fresh pairs；raw arm 使用 required candidates + uncompressed optional dialogue，
  compact arm 使用同源 required candidates + source-linked bounded summary replacement。两臂分别满足完整 9A safety oracle，不能把一臂
  失败的 pair 当部分收益样本。
- 预先门禁：flag-off zero-transport rejection、candidate lineage/atomic replacement、tool-call
  round-trip、automatic validation、nested runtime snapshot、receipt integrity 和 unknown
  usage hard-stop；usage/finish/reasoning 缺失不归零。
- 待执行：先补 metadata/config/API 与 focused offline tests，再运行三组 paired real mutation；
  报告 validated quality、input/output/total/reasoning tokens、calls、cache、wall time、证据覆盖
  和 suspicious-success。通过仅授权更大确认性实验，不默认开启 mutation projection。

### Context Phase 43 / Phase 9B：mutation benefit result and continuation fix

- 观察到的失败：带 initial-context candidates 的前两轮真实诊断在写入前因
  `ProviderToolNoProgress` 停止。receipt 的逐消息证据显示，历史工具结果压缩在二分裁剪时原地
  修改了 `preview`；较大的 lineage envelope 使完整文件退化为 `truncated=false,
  preview=""`。随后 provider 误判证据不完整并重复读取。修复前的诊断 receipts 保留在独立
  roots，未重放、未计入正式 paired denominator。
- 实施修复：`_fit_tool_result_payload` 保留原始 bounded text，在 compact fallback 中保留可用
  preview；完整且短的 file artifact 使用明确的 inline `content` 字段，只有真正受限的证据才
  使用 `preview`。新增 initial-context round-trip、历史压缩 lineage 和 inline-content 回归。
- 验证证据：focused provider round-trip/admission/deepseek tests **72 passed**；随后 fresh
  `phase43_mutation_benefit_v3` 完成 3 raw/compact pairs、6/6 validated receipts。两臂每次均为
  2 reads + 1 `file_replace` + 1 automatic exact pytest，目标文件/API/diff/sentinel/receipt/replay
  门禁全部通过；raw→compact paired median prompt `11,959→5,560`（53.5%），total
  `12,459→6,050`（51.4%），request delta=0。
- 阶段结论：Phase43 **PASS（exploratory mutation benefit gate）**；证明的是一个 DeepSeek
  单文件 mutation lane 的描述性输入/总 token 收益，不是默认 rollout 或跨 Provider 结论。
- 剩余限制：reasoning 在本实验显式 disabled，未产生可比较 reasoning usage；任务形状和模型
  数量仍有限。下一步进入 Phase44/Phase9C confirmatory mutation matrix，增加第二种 mutation
  shape/多文件关联任务；第二 Provider 仅在 typed tool-wire capability profile 存在时加入，
  mutation projection flag 继续 default-off。

Phase44/Phase9C 阶段计划已记录在
`docs/context_management/PHASE_44_CONFIRMATORY_MUTATION_MATRIX_PLAN.md`，在下一阶段实现前先
经过 offline fixture/patch-writer/lineage/flag-off 门禁，不把 Phase43 的单一 fixture 结果外推
为默认策略。

### Phase44A observation: symbol-patch provider contract gap

- Observed failure: the first fresh Phase44 raw arm stopped before mutation with
  `file_patch_writer modify_symbol requires replacement_text or patch.replacement_text`. A read-only
  probe confirmed the provider schema exposed only `file_path`, `encoding`, and `operation_kind`, while
  admission accepted the incomplete call. The provider therefore had no legal way to emit the required
  symbol/replacement fields.
- Boundary: this is a provider-schema/admission contract failure, not a Compact quality or token result.
  Receipt `20260807T033129-1-327b07c9` is preserved as immutable failed evidence; the workspace was not
  replayed and is excluded from all denominators.
- Planned repair: extend the existing `ToolContractMetadata` conditional contract and the derived
  provider schema/admission validator; do not add a new metadata kind or bypass typed validation. The
  impact note and implementation order are recorded in `PHASE_44_CONFIRMATORY_MUTATION_MATRIX_PLAN.md`.

### Phase44A repair and Phase44 stratum-1 confirmation

- Contract repair: `file_patch_writer` provider schema now exposes the typed
  `modify_symbol` conditional fields (`symbol_name` plus `replacement_text`/`patch`), and provider
  admission applies input defaults before enforcing the same condition. Incomplete calls fail closed;
  executor fallback is not used.
- First post-schema canary observation: raw passed, but compact stopped after a valid mutation and
  exact pytest because context exact-duplicate governance removed a repeated tool-wire projection.
  The provider then received an assistant tool-call without its matching tool message. This was
  diagnosed offline, not attributed to Compact quality, and the failed workspace was not replayed.
- Second repair: required `role=tool` results and required `role=assistant` messages with forbidden
  truncation are excluded from exact-content deduplication. Their wire identity is part of the active
  provider round-trip even when the compact payload text is identical. A regression now preserves
  duplicate empty assistant tool-call turns and duplicate tool results in source order.
- Validation: focused schema/admission/round-trip/DeepSeek/context/shape tests **119 passed**.
  Fresh canary v4 completed both arms. Final fresh matrix v2 completed 3 raw/compact pairs, **6/6
  passed**; every receipt had valid `modify_symbol/divide` shape evidence, real target mutation,
  unchanged API, bounded diff, one automatic exact pytest, no forbidden paths, sealed usage/finish
  evidence, and replay=0.
- Final effect: raw median prompt/total tokens `12,547/13,230`; compact `8,155/8,767`, giving
  **35.00% prompt** and **33.73% total** median reduction. Completion direction was mixed and
  request count was not a benefit (raw median 4, compact median 5; paired deltas mixed). Reasoning
  was explicitly disabled and remains unknown/not-applicable rather than zero.
- Decision: Phase44 stratum-1 is **PASS** for a DeepSeek single-target symbol-patch input/total-token
  benefit with preserved safety. It does not authorize default mutation projection, call-count claims,
  cross-provider rollout, or the pending cross-file-linkage stratum.

### Phase45 plan and offline gate: cross-file linkage mutation

- Plan: `docs/context_management/PHASE_45_CROSS_FILE_MUTATION_PLAN.md` freezes a new three-file read
  relationship (`calculator.py` → `consumer.py` → `tests/test_calculator.py`) while retaining one
  `file_patch_writer modify_symbol/divide` write to `calculator.py`. Raw/compact arms, fresh roots,
  exact validation, and hard-stop receipt rules are independent of Phase44.
- Implementation: the experiment fixture now includes `consumer.safe_divide`, and the new
  `cross_file_symbol_patch` shape declares all three read-scope files while preserving the existing
  typed writer and mutation oracle. A dedicated Phase45 candidate builder supplies required linkage
  facts and an atomic compact history replacement.
- Offline validation: fixture/linkage, shape, lineage/compact, line-range refusal, readiness no-transport,
  context, admission and round-trip suites passed **110 tests**. No provider transport was attempted
  in this gate.
- Next gate: run one fresh DeepSeek raw/compact canary. Do not alter Compact or budgets in response to
  a provider contract failure; stop and diagnose the contract first.

### Phase45 runner recovery and cross-file result

- Observed failure: the first fresh canary compact arm read all three declared files, then emitted a
  duplicate test read and stopped with `ProviderToolNoProgress` before mutation. No file changed and no
  validation was attempted; the receipt is diagnostic only and was not replayed or counted.
- Root cause: duplicate-read governance correctly blocked execution, but mutation rounds had no bounded
  provider-facing cue to consume the already-complete evidence and select the declared writer.
- Implemented fix: after all declared reads are complete, one duplicate-only mutation round may append a
  single provider-facing guidance message. It only requests the existing typed writer and exact
  validation; it does not execute, widen scope, or bypass admission. A second duplicate-only mutation
  round still fails closed. Added a focused round-trip regression for the guidance and writer path, and
  documented the behavior in `API.md`.
- Validation: the Phase45 canary v2 passed both arms. Fresh matrix
  `phase45_cross_file_mutation_matrix_v1` completed 3 raw/compact pairs, **6/6 validated receipts**.
  Each arm read all three paths, made exactly one valid `modify_symbol/divide` patch only to
  `calculator.py`, passed exact automatic pytest, preserved API and bounded diff, changed no forbidden
  path, sealed the receipt, and had replay=0.
- Effect: raw→compact median prompt `22,244→9,845` (**55.35%**), completion `1,666→1,053`
  (**36.79%**), total `23,910→10,898` (**54.02%**), and requests `6→5` (**16.67%**). Observed
  provider-reported reasoning usage was `863→286` (**60.87%**), while the request policy remained
  explicitly disabled; this is recorded usage, not a global reasoning conclusion.
- Decision: Phase45 **PASS** for the DeepSeek cross-file-linkage mutation stratum. Remaining limits are
  task/provider breadth, default-off mutation projection, and the need for a separately planned
  cross-provider/Reasoning experiment.

### Phase46A / Phase10A: offline capability and wire gate

- Plan: `docs/context_management/PHASE_46_CROSS_PROVIDER_REASONING_PLAN.md` separates provider wire
  compatibility, reasoning policy behavior, and raw-vs-compact mutation benefit. It forbids hostname or
  model-name capability inference and requires typed-block receipts for unavailable lanes.
- Validation: the deterministic reasoning policy/adapter/native transport, DeepSeek continuation,
  provider schema/admission, and context assembly suite passed **94 tests**. Exact/mapped/clamped/
  unsupported reasoning resolution, transport rendering, unknown usage semantics, tool-call identity,
  and required-context preservation are covered.
- Boundary: no Compact, budget, default flag, or provider transport was changed; this result does not
  prove real provider compatibility or reasoning quality/token benefit.
- Next gate: run one fresh reasoning-disabled read-only readiness canary per provider with an explicit
  capability profile and a tool-capable endpoint; missing credentials/profile must be a zero-transport
  typed block.

### Phase46B / Phase10B: provider readiness and read-only canary

- Readiness evidence: `phase46b_provider_readiness_v1/readiness.json` records DeepSeek as ready with
  explicit `deepseek-chat-known/v1`, official tokenizer, and treatment admissible. OpenAI is a typed
  block with explicit `openai-chat-known/v1` but `missing_credentials` and `tokenizer_unavailable`;
  transport attempts are zero.
- Real canary: `phase46b_deepseek_readonly_canary_v1` passed the current/off and full compact bundle
  arms (3 requests each, quality pass, project mutation=0). The flag-off candidate arm failed closed
  before transport as expected (0 requests, mutation=0). Tool continuation and read scope remained
  valid; disabled reasoning exposed no token count and remains unknown, not zero.
- Decision: Phase46B **PASS for the DeepSeek readiness stratum**; OpenAI remains typed-blocked. Only
  DeepSeek may proceed to the isolated reasoning policy experiment, and no mutation/provider comparison
  is inferred from this gate.

### Phase46C / Phase10C: isolated reasoning policy diagnosis

- Frozen factors: full iteration-goal JSON candidates, source snapshot, completion reservation, and
  provider were fixed; only DeepSeek `disabled` versus `provider_default` reasoning intent changed.
- Evidence: `phase46c_deepseek_reasoning_pairs_v1/result.json` recorded 3 interleaved pairs. Disabled
  was **3/3** valid JSON/goal with `stop`; provider-default was **1/3** valid after recovery and **2/3**
  invalid/empty JSON with `finish_reason=length`. In the failed pairs reasoning consumed the full
  initial `840` completion tokens and the full bounded-recovery `1,140` ceiling. Total attempts were 9;
  project and memory mutations were zero.
- Root cause: routine structured decisions let provider-default reasoning consume the same completion
  budget needed for visible JSON. This is a reasoning/completion interaction, not a Compact/context
  selection failure; increasing the retry ceiling alone does not guarantee visible completion.
- Decision: Phase46C is **diagnostic stopped**, not a benefit pass. Before another canary, write a
  reasoning-policy repair plan with provider-neutral routine routing, profile-specific mapping, visible
  completion reserve, and usage/finish hard gates. Do not enter mutation or cross-provider benefit
  experiments until that canary passes.

The repair plan is recorded in
`docs/context_management/PHASE_46C_REASONING_REPAIR_PLAN.md`. It requires an R1 metadata/contract
review and offline tests before any behavior change, then one fresh canary before a three-pair
confirmation. No Compact or budget adjustment is authorized as a response to this diagnostic.

### Phase46C-R: structured reasoning/completion repair

- Implementation: for `response_format=json_object`, the LLM transport now maps `provider_default` to
  explicit disabled only when the configured capability profile proves disabled support; generic profiles
  remain conservative and omit provider controls. Cache identity and effective telemetry use the same
  structured-output condition. No new metadata kind, Compact change, or budget ceiling change was made.
- Offline evidence: reasoning policy/adapter/native transport and canary tests **39 passed**.
- Real evidence: R2 `phase46c_repaired_canary_v1` passed both arms in one request with valid JSON/goal,
  `stop`, and `provider_default → disabled/mapped` for the standard arm. R3
  `phase46c_repaired_pairs_v1` passed **6/6** arms, one attempt each, aggregate total tokens 11,950,
  with no recovery/no-progress or project/memory mutation.
- Decision: Phase46C-R **PASS for structured JSON safety/stability**. It is not a reasoning-benefit
  result because structured provider-default is intentionally disabled; explicit high/free-form effort
  remains a separate experiment.

### Phase46C-R4: explicit high/free-form boundary

- Scope: one DeepSeek `deepseek-chat-known/v1` `enabled/high` request with text output, fixed context,
  `max_tokens=1024`, no tools, and no mutation.
- Evidence: `phase46c_r4_high_freeform_v1/result.json` recorded prompt/completion/total
  `1,920/1,024/2,944`; reasoning usage was 949; `finish_reason=length`; visible content was only a
  partial JSON-like prefix. Project and memory mutations were zero.
- Decision: R4 **diagnostic stopped**. Explicit high is not safe at this ceiling, and increasing
  `max_tokens` alone is not a reasoning budget. No repetitions, mutation comparison, or global effort
  mapping is allowed until a provider-specific reasoning-token control or separate visible-completion
  reserve exists.

### Phase46D / Phase10D plan and current block

- Plan: `docs/context_management/PHASE_46D_CROSS_PROVIDER_MUTATION_PLAN.md` freezes the Phase45
  cross-file task and requires provider-by-provider readiness, reasoning, typed wire, and mutation
  gates before raw/compact comparison.
- Current block: the OpenAI lane has an explicit profile but readiness is a zero-transport typed block
  (`missing_credentials`, `tokenizer_unavailable`). No OpenAI request was attempted. DeepSeek's
  Phase45 result remains a provider/task stratum and is not relabeled as cross-provider evidence.
- Fresh readiness recheck `phase46b_provider_readiness_v2/readiness.json` reproduced the same typed
  block with `transport_attempted=false`; no external request was made.

### Phase46B-R: exact OpenAI tokenizer repair

- Root cause: `ProviderTokenCounter` only recognized the reviewed DeepSeek tokenizer, so a known
  OpenAI model was blocked as `tokenizer_unavailable` before its credential gate could be evaluated.
- Implementation: added the explicit `openai-chat-known` `tiktoken` adapter and declared
  `tiktoken>=0.8.0`. Unknown models remain unavailable; no character/token estimate or arbitrary
  encoding fallback is allowed. `ContextSelectionMetadata` continues to carry the tokenizer identity.
- Validation: tokenizer/context/reasoning/metadata/readiness suite **111 passed**; actual
  `gpt-4o-mini` resolution produced `tiktoken:o200k_base` and exact local counts.
- Readiness: fresh `phase46b_provider_readiness_v3/readiness.json` changed the OpenAI blockers to only
  `missing_credentials`; `tokenizer_available=true`, `transport_attempted=false`, treatment still
  inadmissible. DeepSeek remained ready.
- Decision: Phase46B-R **PASS**. A credential is still required before any OpenAI request or Phase46D
  mutation canary.
- Final regression after the adapter was installed: token/context/reasoning/provider/mutation suite
  **174 passed**; `git diff --check` passed. The remaining gate is external credential availability,
  not tokenizer or schema implementation.
- The readiness runner now accepts the first non-empty provider-scoped process variable among
  `OPENPILOT_OPENAI_API_KEY` and `OPENAI_API_KEY`; it never reuses the active
  `OPENPILOT_LLM_API_KEY` from a different provider. Offline tests cover both the
  ready/no-transport path and the DeepSeek-key isolation path.
- Fresh recheck `phase46b_provider_readiness_v4/readiness.json` reproduced the typed OpenAI
  `missing_credentials` block with `tokenizer_available=true` and `transport_attempted=false`.

### Phase46D: isolated OpenAI execution entry

- Implemented `stage46d_openai_cross_file_mutation.py` without changing the DeepSeek Phase45
  runner. The entry freezes the OpenAI endpoint/model/profile, exact `tiktoken` tokenizer,
  disabled routine reasoning, zero transport retries, real-mutation budget, cross-file symbol
  patch shape, and provider-scoped credential source.
- Default and explicit `--execute-provider` runs fail closed without a key. Fresh
  `phase46d_openai_cross_file_mutation_v2/result.json` records `typed_blocked`,
  `transport_attempted=false`, `project_mutation=false`; no secret is serialized.
- The runner rejects `--repetitions 3` unless the same run root contains a passed two-arm canary
  with matching OpenAI identity, exact validation, bounded diff, sealed receipt, and zero replay.
- Offline combined regression after the entry, canary-order, and receipt-integrity gates were added: **183 passed**;
  `git diff --check` passed.
- Full project-environment regression then passed **1162 tests** with one non-failing pytest
  deprecation warning. `compileall -q Code/src experiments/full_architecture_context_observation`
  and `git diff --check` passed. The external OpenAI credential gate remains unchanged.

### Full experiment-suite consistency repair

- Observed seven failures when running the complete experiment directory: one stale project-improvement
  prompt-budget assertion (`3967` versus the protocol's `4096-128=3968`), one test that expected
  DeepSeek reasoning capability to be inferred from endpoint/model names, and five Stage9 failures
  caused by a frozen offline report/source-gate hash drift.
- Repair: align the assertion with the hash-locked effective budget; make the reasoning identity test
  provide the explicit `deepseek-chat-known` profile and add the same explicit profile fixture to
  Stage9 provider-sentinel tests; regenerate the deterministic Stage9 offline report and update its
  paired-shadow source hash. No provider request or project mutation was used.
- Validation: Stage9 grouped tests **227 passed**; Phase32/32D/checkpoint focused regression **44 passed**;
  complete experiment directory **461 passed**;
  the report snapshot equals runtime output and all paired source gates match.
- Remaining limitation: these are offline/contract results; the real OpenAI Phase46D canary remains
  blocked only by missing credentials.
- Remaining limitation: no real OpenAI read-only canary or mutation request has been attempted.
- Final readiness rerun `phase46b_provider_readiness_v5/readiness.json` reproduced the same
  provider-scoped `missing_credentials` block; OpenAI `tiktoken:o200k_base` remained available and
  `transport_attempted=false`.

### Completion audit

`docs/context_management/CONTEXT_MANAGEMENT_COMPLETION_AUDIT.md` records the requirement-level
evidence: Phase32 constraint persistence, Phase40–45 Compact/mutation strata, Phase46 provider and
reasoning gates, and the remaining OpenAI/high-effort limitations. A current focused constraint/Compact/
provider suite passed **86 tests**; the latest provider/mutation/reasoning/readiness suite passed **178 tests**.

### Phase H0: moved harness and source-alignment diagnosis

- Observed failure: the local checkout no longer contains the experiment harness because it was moved to
  `openpilot-air:/Users/abaaba/work/openpilot-worker`; the remote worker has the harness as an untracked
  2703-file directory but its `Code/src` is an older `76b910b` tree missing current context/checkpoint,
  tokenizer, and reasoning modules. The remote worker also has roughly 207 user-owned dirty entries.
- Decision: do not restore local deletions and do not modify or reset the remote worker. Add
  `PHASE_H0_MATCHED_EXECUTION_WORKSPACE_PLAN.md` and create the isolated
  `/Users/abaaba/work/openpilot-context-experiment-20260808-h0` snapshot with source commit
  `a044d79c8893a6ad326dd8a4ead129f34593601b`, source tree hash, and harness hash.
- Validation: Python 3.12.13 isolated runtime passed compileall, `git diff --check`, 469-test harness
  collection, and the Phase32 focused gate (**10 passed**). The remote worker was unchanged; Provider
  transport and project mutation were both zero. Receipt `H0_RECEIPT.json` has SHA-256
  `aadad1473908a048e5630abbd30ada99495899c6f87fccc1d32f4fa3419af70b`.
- Remaining limitation: H0 proves only source/harness alignment. It does not prove task quality, token
  benefit, reasoning behavior, or mutation safety.

### Phase H1 / H1-R: complete offline gate and portability repair

- H1 first run exposed two environment-bound failures after tokenizer preparation: a Stage21 negative
  fixture inherited the host DeepSeek profile, and one Stage9 resume reference pointed to a cleaned-up
  historical `/private/var/.../calculator.py`. Four additional Stage9 sentinel failures were fixed by
  injecting the protocol's explicit DeepSeek identity without a key; no Provider call was made.
- H1-R repaired only the isolated experiment harness: explicit `None` profile binding in Stage21, typed
  `blockers` assertion in Stage34, and a Stage9 test that records the historical absolute-path resume as
  fail-closed. It did not recreate the old project, rewrite the receipt, or change `Code/src`.
- Validation: focused H1-R **137 passed**; complete offline suite **469 passed in 18.58s**; compileall and
  `git diff --check` passed. H1R receipt `H1R_RECEIPT.json` SHA-256 is
  `5a11e76982d3c885623bb9b85d645ed569e714102178e7c90c7665f070bb2c7a`; Provider transport and project
  mutation counters are zero.
- Remaining limitation: the historical Stage9 resume prefix is not portable without a content-addressed
  final project fixture. It is explicitly refused and cannot support a quality or mutation claim.

### Phase H2: Provider readiness

- Executed the Phase46B readiness runner against explicit DeepSeek and OpenAI identities with
  provider-scoped credentials absent. DeepSeek used the verified official tokenizer; OpenAI resolved
  `tiktoken:o200k_base`.
- Both lanes returned `typed_blocked` with only `missing_credentials`; no active key was reused across
  providers, no credential was serialized, and transport/project/network counters remained zero.
- Focused readiness tests: **4 passed**. Receipt `H2_RECEIPT.json` SHA-256 is
  `2619f9921938ca556f420adc55d6c7e7d08900af3147b24b46b4f028b5c476bb`.
- Decision: adapter and tokenizer readiness is **PASS**, but the real-provider canary is blocked until a
  provider-scoped credential is available on `openpilot-air`; no quality or reasoning claim is made.

### Phase H3: reasoning-disabled read-only canary preflight

- Invoked the full provider-native read-only recovery entry with DeepSeek
  `deepseek-chat-known:v1`, explicit `disabled` reasoning, `real_read_only` budget, and `file_reader`
  only. No `OPENPILOT_LLM_API_KEY` was available, so it stopped with typed `provider_not_ready` before
  constructing a request.
- Validation: run-root file count **0**, Provider transport/requests **0**, project/memory/network
  side effects **0**. Receipt `H3_RECEIPT.json` SHA-256 is
  `43e5e5dbfff6cc9a1494f05b911d9abece8ed2ca645b37deadfc059fda6e67f1`.
- Decision: H3 is an external credential block, not a model failure. No reasoning, quality, or token
  result is counted; the exact one-canary plan remains ready for a later credentialed run.

### Final audit: moved harness and current route

- Observed issue: the local checkout's experiment directory was absent because it had been moved to
  `openpilot-air:/Users/abaaba/work/openpilot-worker/experiments/full_architecture_context_observation`.
  That worker is an older `76b910b` tree with approximately 207 user-owned dirty entries, so using it
  would mix source generations and could overwrite user work.
- Fix/evidence: retained the moved worker untouched and used the independent
  `/Users/abaaba/work/openpilot-context-experiment-20260808-h0` snapshot. H0/H1-R/H2/H3 receipt hashes
  and their sequential references match; local production-only tests passed **1147**, compileall and
  diff-check passed.
- Remaining limitation: four local tests still require the moved harness and are intentionally excluded;
  the isolated H1-R suite is the evidence for those contracts. H2 has no provider credentials and H3
  stops before transport, so no real-provider or benefit claim is promoted.

### Phase H3-C: credentialed DeepSeek canary

- Configuration: wrote the user-provided DeepSeek key only to the isolated H0 worktree `.env` with mode
  `0600`; configured `https://api.deepseek.com`, `deepseek-v4-flash`, typed profile `deepseek-chat-known`
  (version `v1`), disabled routine reasoning, `real_read_only`, eight-round ceiling, and file-reader-only
  execution. The old dirty worker and all receipts remain key-free.
- Observed setup failure: the first credentialed attempt used an invalid versioned profile string, then a
  corrected profile reached the Provider but stopped at an accidental two-round override. Both failures
  occurred within the bounded canary setup path; the first made no request, the second made two successful
  tool-call requests and mutated no files.
- Validated result: after fixing the max-round setting to the `real_read_only` eight-round ceiling, the
  single corrected canary passed with 3 requests, 2 tool-call rounds plus a final stop, usage
  `prompt=5865`, `completion=1010`, `total=6875`, zero reasoning content, zero sentinel drift, and zero
  exact API-key matches in receipt/log artifacts. Receipt SHA-256:
  `7e6388f75f88dfef13d334fbf0c5f39c49497e62856072fe33253674263eb3a6`.
- Remaining limitation: this is transport/tool/scope evidence for one read-only task stratum. It does not
  establish semantic quality, reasoning optimality, Compact benefit, or mutation benefit.

### Phase H4: DeepSeek reasoning strategy matrix

- R0 validation: isolated the real `.env` from offline fixtures, then ran the focused reasoning/adapter/
  token/provider/telemetry gate; **108 passed**. The secret file was restored with mode `0600` and no
  Provider transport occurred during R0.
- R1 disabled arm: passed with 3 requests, 3 tool rounds, 4 completed reads, prompt/completion/total
  `5865/965/6830`, zero reasoning content, zero mutation. Receipt SHA-256:
  `00603fe73af6942fefed162844552e1d73102613d6c8bbc5db278c6ce41cc72f`.
- R1 explicit-high arm: three Provider responses emitted valid tool calls and exposed reasoning tokens
  `861 + 277 + 211 = 1349`; the next required round failed closed with `Required context cannot fit
  within the configured prompt budget`. Total observed usage was `7388/2064/9452`; no mutation or
  sentinel drift occurred. Receipt SHA-256:
  `15c6b5a856bf43cf833c31dcf06c108f0c1324fa96f7227a52f5106686bd6b95`.
- Decision: stop H4 before R2. This is a reasoning/context-budget compatibility finding, not a Compact
  failure or model-quality verdict. A separate repair plan is required before another high-effort request.

### Phase H4-R: reasoning/context-budget repair R0

- Offline replay reproduced the H4-B boundary with the exact DeepSeek tokenizer: a three-round synthetic
  history was ready at 754 prompt tokens with no reasoning, ready at 3751 with 2000 reasoning characters
  per round, and budget-blocked at 5000 characters per round. No provider transport or credential access
  occurred.
- Contract inventory confirmed that the current DeepSeek enabled adapter must preserve assistant
  `reasoning_content` alongside tool calls; dropping it is correctly rejected, while disabled mode can
  omit it without losing call/result IDs.
- Candidate B (high effort only in a separate final free-form request) fit at 622 prompt tokens in the
  synthetic projection; Candidate C (256-character bounded projection) fit at 1125 but remains only a
  hypothesis because active DeepSeek continuation cannot truncate the required reasoning field.
- Decision: R0 passed and Candidate B is the only candidate admitted to a future focused canary. No
  production change, Compact change, or new Provider request was made in R0.

### Phase H4-R/B: two-stage high-reasoning canary

- Offline runner gate passed: both policies resolved exactly through the DeepSeek adapter, assembled the
  same evidence hash at 1757 prompt tokens, and reused the H4-A read-only evidence boundary.
- Disabled control passed one final free-form request (`1761/561/2322` prompt/completion/total tokens,
  no reasoning content). Explicit-high treatment passed one final free-form request
  (`1840/1315/3155`, 788 provider-reported reasoning tokens). Both had `finish_reason=stop`, all four
  expected path mentions, and zero mutation.
- Decision: Candidate B is technically viable for a purpose-aware, feature-flagged single synthesis
  request, but it costs 833 additional total tokens in this pair. Multi-round high reasoning remains
  disallowed; three-pair confirmation needs its own plan.

### Phase H4-R/B confirmation

- Ran the frozen interleaved order `disabled→high`, `high→disabled`, `disabled→high`; each pair first
  completed a full-architecture disabled read stage, then both single-request synthesis arms.
- All 6 synthesis arms passed `finish_reason=stop`, the four-path evidence oracle, usage capture, and
  zero mutation. Disabled total tokens were `6850`; high total tokens were `8431` with `1778` observed
  reasoning tokens, a `+23.1%` paired aggregate cost. Aggregate receipt SHA-256:
  `719d3dec38d59d68704e4eee8eaeebf40e5f7bcdaaa6bf889b5cb644b6947683`.
- Decision: high is admitted only for one final free-form synthesis request after disabled evidence
  collection. The next Compact experiment must freeze this routing and keep high out of tool/read phases.

### Phase H5: full-architecture read-only Compact canary

- Observed question: the segmented initial-context Compact bundle needed a real-provider check after
  reasoning policy was frozen, but the short linkage task might not expose history accumulation.
- Validation: H5-R0 focused offline gate **110 passed**. The credentialed DeepSeek three-arm campaign
  completed `off_current` and `on_full_bundle` with the same two-file read scope, two tool rounds, zero
  duplicate-only rounds, one finalization request, equal semantic-quality oracle results, and zero
  sentinel drift. The flag-off candidate arm was typed-blocked before transport with zero requests.
- Measured usage: raw/current prompt/completion/total `5265/680/5945`; full Compact
  `5298/662/5960`; reasoning was disabled in all arms. The Compact arm therefore used 15 more total
  tokens (+0.25%) on this short task; this is not a benefit claim and is not treated as a regression.
- Receipts: campaign SHA-256 `d2f54d643d294cba749ec0d5e38c938183fc7e14d84041951c1d048da9a8dfc9`;
  arm SHA-256 values are recorded in `PHASE_H5_READ_ONLY_COMPACT_CANARY_RESULT.md`.
- Decision: safety/equivalence gate passed, but Compact remains opt-in. The next required stage is the
  separately planned three-pair history-bearing read-only confirmation; no reasoning, budget, task,
  schema, fallback, or mutation behavior was changed to rescue this canary.
- Remaining limitation: this result cannot distinguish Compact benefit on a short task with little
  accumulated history. Any lower-token or quality conclusion beyond this stratum is unsupported.

### Phase H5-R: history-bearing Compact confirmation

- Observed limitation: the H5 short task proved projection safety but did not contain enough accumulated
  history to measure Compact efficiency.
- Validation: ran three interleaved DeepSeek pairs comparing raw segmented history with the exact governed
  summary replacement. All six executing arms passed the same semantic-quality/evidence oracle, the
  flag-off calibration was typed-blocked before transport, duplicate-only rounds were zero, and project
  mutations were zero. Reasoning was explicitly disabled for every request.
- Measured result: raw aggregate prompt/completion/total `40284/2243/42527`; Compact aggregate
  `15894/1953/17847`. Prompt tokens fell **60.6%** and total tokens **58.0%** with equal quality.
  Campaign receipt SHA-256 is `14f2f58fbe5dfb2b34c553a2b6b6bfbef01099a04a903d9fdcaa403785e51320`.
- Decision: Compact has a validated mechanism-level efficiency signal for this DeepSeek read-only
  stratum and may advance to a broader feature-flagged read-only task matrix. It remains opt-in; no
  global default, mutation, generated summary, or cross-provider claim is authorized.
- Remaining limitation: one task/provider only. Broader strata must preserve the same quality and safety
  gates before policy promotion.

### Phase H5-S: multi-task read-only Compact matrix

- Observed limitation: H5-R established a strong Compact signal on one linkage task, but task-level
  generalization was untested.
- Validation: the focused R0 gate passed **110 tests**. Three task strata (`single_file_symbol`,
  `two_file_linkage`, `adaptive_window_evidence`) ran two interleaved raw/Compact pairs each. All 12
  executing arms passed the same quality/evidence oracle, the flag-off calibration was typed-blocked
  before transport, duplicate-only rounds were zero, and project mutations were zero.
- Measured result: aggregate raw/Compact prompt tokens `67696/25949` (61.67% reduction) and total tokens
  `71715/29859` (58.36% reduction). Per-task total reductions were 54.2%, 58.0%, and 61.4%; quality
  was 6/6 in both arms. Campaign receipt SHA-256 is
  `6596c8175319e4ad7159f9f85ba80f96dded61e003b49bb37e2704e78c0354cb`.
- Decision: the Compact mechanism generalizes across the tested DeepSeek read-only strata and may
  advance to a separately gated feature-flagged mutation canary. It remains opt-in; no global default,
  cross-provider claim, or generated summary is authorized.
- Remaining limitation: synthetic history projection and one provider; mutation safety and real-task
  benefit still require their own stage.

### Phase H6: feature-flagged Compact mutation canary

- Observed failure/diagnosis: the first mutation attempt was correctly stopped by read-scope enforcement
  because the `symbol_patch` fixture omitted `consumer.py` while its test imported that file. The task
  wording and candidate constraint were repaired, but the raw baseline still exposed the same fixture
  dependency. Those v1/v2 attempts made no writer call and are excluded from benefit claims.
- Repair: switched to the existing typed `cross_file_symbol_patch` shape, explicitly reading
  `calculator.py`, `consumer.py`, and `tests/test_calculator.py` while retaining calculator.py as the
  sole write target. Focused offline gate after repair: **31 passed**.
- Validation: v3 ran two interleaved raw/Compact pairs in disposable fixtures. All four arms performed one
  valid symbol patch, changed only calculator.py, preserved the public API, observed the requested
  ValueError behavior, ran the exact pytest command successfully, and recorded no forbidden paths.
- Context signal: prompt tokens fell `34962→19246` (45.0%). However the effective Provider policy in
  every receipt was `ReasoningMode.PROVIDER_DEFAULT` despite the harness setting disabled, because the
  generic router classifies a three-read-file mutation as non-routine. DeepSeek reported reasoning
  tokens `1303` raw versus `602` Compact, so the total reduction `37801→21323` is not attributed to
  Compact alone.
- Decision: qualified mutation-safety pass; Compact remains opt-in and total-benefit promotion is
  blocked pending the separate typed reasoning-control plan. No source checkout mutation occurred.

### Phase H6-R: mutation reasoning-control canary

- Observed issue: H6 v3's cross-file mutation task was effectively `provider_default` despite a disabled
  settings field because generic routing treats a three-read-file implement task as non-routine. This
  produced raw/Compact reasoning `1303/602`, so its total-token result was not attributable to Compact.
- Repair: exposed an experiment-only typed `ReasoningPolicy` override on the Phase35 harness, leaving
  production task routing unchanged. The override is recorded in each request receipt and fails closed
  if effective policy is not disabled. Focused R0: **53 passed**; the real `.env` was isolated and
  restored at mode 0600.
- Validation: v1 ran two interleaved cross-file symbol-patch pairs. All four arms reported effective
  disabled reasoning and zero reasoning tokens; each changed only calculator.py, preserved the API,
  passed exact pytest, and had no forbidden path.
- Measured result: raw/Compact prompt `32190/19619` (39.1% reduction) and total `33637/21199` (37.0%
  reduction). Campaign receipt SHA-256:
  `fe9b83b7649da7ce9c24bff3f6f141929edf961be13fac32e40ee9c2e615e1ff`.
- Decision: controlled mutation Compact benefit is supported for this bounded DeepSeek stratum and may
  advance to a larger feature-flagged mutation matrix. Compact remains opt-in; no global reasoning or
  permission policy was changed.

### Phase H7: feature-flagged mutation confirmation matrix

- Observed limitation: H6R used two pairs and one history size; stability under larger history and repeated
  order interleaving was still unverified.
- Validation: H7-R0 passed **98 tests** with the real credential file isolated and restored at mode 0600.
  H7-R1 completed all 12 planned arms across medium (1x) and long (2x) history. Every arm reported
  effective disabled reasoning, one valid bounded symbol patch, exact pytest success, API preservation,
  and no forbidden/source/environment mutation.
- Measured result: medium raw/Compact total `54221/30595` (43.57% reduction), long `54091/32941`
  (39.10% reduction). Prompt reductions were 45.31% and 40.85%; reasoning was zero in every arm.
  Campaign receipt SHA-256:
  `35109655751ccd36cbd1c07ba83bc643d9ad95282628b42f6f018ae19ce56691`.
- Decision: H7 passed and authorizes planning a real-project mutation shadow. Compact remains opt-in;
  no global reasoning, permission, or default policy changed.

### Phase H8: real-project mutation shadow

- Plan/result: selected the existing adaptive code-window round-trip test gap and froze its read/write
  scope, exact pytest command, source hash, DeepSeek identity, disabled reasoning, and `real_mutation`
  budget in the H8 task-selection receipt and H8 plan.
- Validation: H8-R0b passed **333 offline tests**. The first H8-R1 raw arm ran in a disposable
  source-isolated workspace and made eight Provider requests, all read-only. Source/environment
  sentinels were unchanged; there was no writer, command executor, project mutation, or validation
  success. Receipt SHA-256 is
  `0faf4e50e2842fceb10c1dac7bf695efd5ab3c2929255412ba6859a209c2d9d5`.
- Observed failure: the roughly 2,116-line target test file was repeatedly returned as bounded 60-line
  previews with a page cap. DeepSeek repeated file reads and hit `provider tool round limit exceeded (8)`
  before reaching the relevant test/API region. This is a file-window/context routing failure, not a
  Compact quality or benefit result; the Compact arm was correctly not run.
- Follow-up plan: `PHASE_H8R1B_GUIDED_ADAPTIVE_WINDOW_PLAN.md` adds repository-grounded location evidence
  and explicit adaptive windows in a new, separately sealed canary. Do not replay the failed arm or
  increase budgets as a substitute for the missing location/window contract.
- H8-R1b validation: the new location-evidence receipt was valid and the first raw arm made six
  Provider requests/seven file-reader attempts, then stopped with `ProviderToolNoProgress after 2
  round(s)` when `file_reader.py` reached the three-page cap. No writer, validation, mutation, source
  change, or environment change occurred. Receipt SHA-256 is
  `2a027a6b4c475a334c0358909b8ad987a00f479ca6a84f3950660b9827a3d9c9`.
- Root-cause refinement: the current round-trip contract treats every truncated window as partial,
  including an explicitly declared location window. The provider therefore lacks a typed completion
  signal for sufficient bounded evidence and keeps reading until the safety stop. The next plan is
  `PHASE_H8R2_BOUNDED_WINDOW_PROGRESS_PLAN.md`; no budget increase or Compact claim is made.

### Phase H8-R2: bounded-window progress contract — offline gate

- Observed failure: the first local full-suite invocation used the wrong
  `PYTHONPATH` for the `Code/src` setuptools layout and failed at collection;
  the corrected run then exposed a real compatibility regression where an
  omitted `read_mode` was converted through `str(FileReadMode.FULL)` and became
  the invalid wire value `filereadmode.full`.
- Implemented fix: preserve typed `FileReadMode` inputs and use the enum's
  `.value` for the default; added a regression test for an ordinary full read
  with no explicit mode. No permission, mutation, budget, or Compact policy was
  changed.
- Validation evidence: the corrected local gate passed **1150 tests** with the
  four legacy tests that import experiment scripts moved to `openpilot-air`
  explicitly excluded. Focused provider round-trip passed 46 tests; the
  bounded-window and full-read compatibility paths passed.
- Remaining limitation: remote source-isolated deployment, fresh receipt
  hashes, and the raw/Compact real-project canary remain pending. The offline
  gate makes no Provider quality or token-efficiency claim.

### Phase H8-R2B: bounded code-artifact handoff

- Observed failure: H8-R2A exposed only a bounded code preview and an
  `artifact_ref`, so the provider could not supply the full generated unit to
  `file_patch_writer`; the raw arm stopped before mutation.
- Implemented fix: added the typed `ToolInputMetadata.artifact_ref`, a
  runtime-only code-artifact ledger with hash/lineage checks, provider writer
  schema support, and bounded handoff instructions. The full generated unit is
  injected only at the typed writer boundary; ordinary provider-facing results
  remain bounded.
- Validation evidence: the R2B focused production suite passed **259 tests**;
  the isolated H0 experiment suite passed **468 tests** with one historical
  Stage34 credential-assumption failure; the complete Code suite passed
  **1171 tests** with one classified H0 absolute-path failure. A fresh DeepSeek
  raw arm observed one valid artifact-reference handoff and exactly one scoped
  writer mutation, with disabled reasoning and complete usage telemetry.
- Remaining limitation: the same raw arm stopped before the exact pytest
  command because the post-mutation continuation could not fit required
  context. The mutation is therefore not a successful task result and is
  excluded from Compact/token claims. Follow-up plan:
  `PHASE_H8R2C_POST_MUTATION_COMPLETION_PLAN.md`.

### Phase H8-R2N: Provider context override boundary

- Observed failure: H8-R2M reached a scoped writer but Provider-supplied
  generator `context` bypassed the authoritative completed declared-read
  projection, allowing an unbound `llm` name to reach exact pytest.
- Implemented fix: at the existing provider-tool preparation boundary, when
  completed declared evidence exists, always replace Provider `context` with
  the typed declared-read projection and set the existing generator grounding
  handles. Calls without authoritative declared evidence retain the explicit
  context compatibility behavior. No new metadata kind, permission, scope,
  budget, reasoning, or fallback layer was added.
- Validation evidence: local focused suites passed **198 + 48** tests, the
  wider Code suite passed **1184 tests** with one warning and four archival
  harness files excluded, `compileall Code/src` passed, and the remote
  focused suite passed **246 tests** under an explicit test-only reasoning
  profile. Ready-only resolved DeepSeek v4 flash, tokenizer availability,
  disabled reasoning, and no transport.
- Raw evidence: the single H8-R2N arm recorded all four declared windows,
  `declared_read_evidence` grounding with four source IDs, one valid in-scope
  artifact-reference writer, eight requests with complete usage/finish
  telemetry, and unchanged source/environment sentinels. Raw receipt:
  `9d7273048054f8149189c8ef9964c3be0c785446b9d8e88cb6be8838b9b6d537`.
- Stop/root cause: exact pytest executed once in the evidenced disposable cwd
  and reported **72 passed, 1 error** because the generated regression test
  declared a nonexistent `runner` pytest fixture. The runner marked the task
  failed; this is not a suspicious-success claim. The Provider-context
  authority boundary is fixed, but generated executable-test contracts are
  still under-specified by the semantic name-binding gate.
- Remaining limitation: do not rerun this raw identity, relax grounding, add
  ambient fixture names, widen windows, increase budget, change reasoning, or
  run Compact. The next separately planned phase must add a narrow fixture /
  executable-test contract admission check before writer execution while
  preserving the current evidence, permission, validation, and stop gates.

### Phase H8-R2O: executable-test fixture contract

- Observed failure/diagnosis: H8-R2N fixed Provider-context precedence but the
  generated pytest unit declared an ambient `runner` fixture. Function
  parameters were treated as local bindings by the existing grounding check,
  so the error appeared only after writer and exact pytest.
- Implemented fix: in evidence-enforced mode, `test_*` function parameters are
  now accepted only when source-visible, standard pytest fixtures, or ordinary
  parameters with defaults; unknown fixture-like parameters fail closed before
  writer. Non-test function parameters and standalone generation retain their
  prior behavior. Added four deterministic regression cases, including the
  existing `tmp_path` compatibility path.
- Validation evidence: local focused **249 passed**; wider Code **1187
  passed, 1 warning** with four archival harness files excluded; remote
  focused **249 passed**; `compileall Code/src` and diff check passed; ready-only
  confirmed DeepSeek v4 flash, disabled reasoning, tokenizer availability,
  `missing_fields=[]`, and no transport.
- Raw evidence: all four declared windows completed and the typed
  `declared_read_evidence` grounding remained authoritative. The generator
  rejected `fixture:runner` before writer; source/environment sentinels were
  unchanged, with zero writer, command, validation, or mutation events. Raw
  receipt SHA-256:
  `017f14df2d186b2489e503bb63643efa82736c3e1d5cfe5bfc9124593848c9ed`.
- Remaining limitation: this is a safe pre-writer stop, not a completed real
  task. Do not rerun the same raw identity, widen evidence, add an ambient
  fixture allowlist, increase budget, change reasoning, or run Compact. A
  future phase must address any remaining generator quality contract before a
  complete `generator → writer → exact pytest` pass can authorize Compact.

### Phase H8-R2P: bounded callable construction projection

- Observed failure: the real arm exposed bounded
  `MODULE_CALLABLE_CANDIDATE` entries and reached `code_unit_generator`, but
  the generated unit selected an invalid direct construction of
  `ProviderToolRoundTripRunner` (`llm`, `registry`, and `executor` keyword
  arguments) instead of the visible owner/task/tools construction contract.
  The subsequent `file_patch_writer` proposal also supplied an artifact
  reference whose `provider_call_id` did not match the registered code-artifact
  ledger entry, so writer admission stopped before mutation.
- Implemented fix: added a bounded callable-signature projection derived only
  from completed declared-read windows, stored in the existing structured
  projection lineage. It preserves source authority, clipping fail-closed
  behavior, fixture admission, scope, budget, reasoning, and writer gates; no
  new metadata kind or ambient symbol source was introduced.
- Validation evidence: local focused callable/patch suites passed **94**;
  the wider local Code suite passed **1190 tests with 1 warning** after the
  four documented archival harness tests were excluded; `compileall` and
  `git diff --check` passed; the remote focused gate passed **252** and
  ready-only resolved DeepSeek v4 flash with disabled reasoning and no
  transport.
- Raw evidence: all four declared windows completed, callable candidates were
  visible in generator grounding, and a code artifact was produced. Eight
  Provider responses used **32,829 prompt / 2,461 completion / 35,290 total**
  tokens. The raw receipt records zero writer calls, zero exact validation,
  zero project mutation, unchanged source/environment sentinels, and a
  fail-closed `provider_call_id` handoff mismatch. Receipt file SHA-256:
  `98b70b3c7bf5d289915985475caded43b84ded73b595cd6e4969980bc516f3ba`;
  internal receipt hash: `c523bc6192a0a0c352df1cef2c45f1efe14c97a98e0e355cde4b77fb291738f6`.
- Remaining limitation: callable visibility alone did not supply a complete
  construction recipe, and the artifact handoff remains independently
  unproven beyond fail-closed rejection. Do not rerun H8-R2P, widen windows,
  increase budget, change reasoning, relax grounding/fixture/scope, use
  fallback as success, or run Compact. The next phase is H8-R2Q bounded
  construction recipe/call-site projection; any remaining handoff lifecycle
  gap must be handled by a separate H8-R2R plan.

### Phase H8-R2Q: bounded construction recipe projection

- Observed failure: the real arm completed all four declared windows and the
  new call-site projection code was present, but the 320-line header window was
  syntactically incomplete. The call-site parser therefore failed closed and
  emitted no `MODULE_CALLSITE_HINT` into generator context. The Provider still
  generated `ProviderToolRoundTripRunner()` with missing `owner` and `task`
  arguments.
- Implemented fix: added bounded source-derived call-site extraction for
  complete parseable windows, rendered through the existing declared-read
  projection; added AST safety, name-binding, clipping, count, size, context,
  and generator-prompt tests. The artifact ledger and writer handoff were left
  unchanged.
- Validation evidence: local focused **97 passed**, wider local **1193 passed,
  1 warning** with the four archival harness tests excluded, remote focused
  **97 passed**, and ready-only resolved DeepSeek v4 flash with disabled
  reasoning and no transport. Remote wider tests had six known host
  `.env`/reasoning-profile drift failures and were not used as code evidence.
- Raw evidence: one scoped writer call and exact command execution occurred;
  artifact reference validation passed, but exact pytest failed **81 passed, 1
  failed** with `TypeError: ProviderToolRoundTripRunner.__init__()` missing
  `owner` and `task`. Eight requests used **30,722 prompt / 2,860 completion /
  33,582 total** tokens; reasoning was disabled and source/environment
  sentinels were unchanged. Raw receipt file SHA-256:
  `d2f3a9dc1b70236aadc7eeb2e52a8f94277f3062ef4d04fecd897201a4ef24e3`;
  internal receipt hash: `4416101eb43672132e82106e29a3c8a6e89fc98307cb266bce2c9f5d65e6db0c`.
- Remaining limitation: this raw arm did not test Provider use of a call-site
  hint because no hint was present in the actual generator context. The next
  phase must add a narrow fail-closed fragment recovery path for complete
  assignments/calls inside syntactically partial windows, with its own
  tests-first/ready-only/one-raw gates. Do not rerun H8-R2Q, widen windows,
  increase budget, change reasoning, relax grounding/fixture/scope, use
  fallback as success, or run Compact. H8-R2R handoff repair is not indicated
  by this arm because the handoff passed.

### Phase H8-R2Q1-D: frozen raw receipt diagnosis

- Observed evidence: the sole H8-R2Q1 raw receipt completed all four exact
  declared windows and passed four source IDs into the generator's enforced
  grounding context, but contained **zero actual `MODULE_CALLSITE_HINT:`
  lines**. The marker `...[local call-site hints]` was only part of a bounded
  source excerpt, not the structured projection. The same context did not
  contain the task target `ProviderToolBoundedWindowMismatch` as a declared
  symbol; it appeared only in task prose.
- Diagnostic result: primary classification is `projection_missing` at the
  raw-page → structured call-site projection → generator-context boundary.
  A secondary `declared_evidence_mismatch` caused the generator to fail closed
  on `ProviderToolBoundedWindowMismatch`. The receipt does not retain the
  intermediate candidate tuple, so parser-vs-recording loss is not claimed.
- Validation evidence: six Provider responses recorded **23,732 prompt / 2,207
  completion / 25,939 total** tokens; reasoning was disabled; writer calls,
  exact validation attempts, and project mutation were all zero; source and
  external sentinels were unchanged. Raw receipt file SHA-256:
  `b88cd470fcdc71ab5b54f028181e2aa6eec56f6d90e943329f3cfffbb89866e3`;
  internal receipt hash:
  `05aeb78c70cef37bb91d5045714ed5dc488201a03e194b281beea12c706b2e2e`.
- Remaining limitation: this diagnosis does not prove whether the parser
  returned no candidates or a later handoff dropped them, and it does not
  authorize a raw retry or Compact. The next phase must test the exact
  `_record_attempts → _declared_generator_context` path offline and align the
  task target with declared evidence before one new canary is considered.

### Phase H8-R2Q1-R: projection repair offline result

- Observed failure reproduced: a four-window regression derived bounded
  call-site candidates for the partial header, but the existing per-entry
  context cap clipped after `MODULE_CALLABLE_CANDIDATE` lines. Structured
  `MODULE_CALLSITE_HINT` lines were ordered later and disappeared from the
  generator context, matching the frozen raw receipt.
- Implemented fix: in both existing declared-generator projection branches,
  render bounded call-site hints immediately after import candidates and before
  symbol/callable lists. This is a projection-order change only; no new store,
  metadata kind, permission, scope, budget, reasoning, fallback, or validation
  authority was added.
- Validation evidence: tests-first single-page path passed after insertion;
  the new realistic four-window clipping regression failed before the fix and
  passed after it. Provider round-trip **85 passed**, code generator/patch
  **16 passed**, focused call-site subset **10 passed**, `compileall Code/src`,
  and `git diff --check` all passed.
- Remaining limitation: the remote task description still needs an explicit
  quoted typed-string contract for `ProviderToolBoundedWindowMismatch` and a
  fresh source/runner-bound selection receipt. No raw retry or Compact arm is
  authorized until task/evidence alignment and ready-only gates pass.

### Phase H8-R2Q1-R raw canary

- Positive evidence: the new raw arm completed all four exact windows and the
  actual generator context contained **9 structured `MODULE_CALLSITE_HINT`
  lines**, including the `_Owner(runtime)` and `_runtime(...)` construction
  hints. This proves the context-entry ordering repair reached the real
  DeepSeek request, not just offline state.
- Stop/root cause: the Provider proposed an unknown pytest `runner` fixture;
  the typed generator grounding gate rejected `fixture:runner` before writer.
  This is a safe generator-quality stop, not a suspicious success and not a
  reason to relax the fixture gate.
- Validation evidence: six responses used **22,573 prompt / 2,044 completion /
  24,617 total** tokens; reasoning was disabled; writer, exact validation, and
  project mutation were zero; source and external sentinels were unchanged.
  Raw receipt file SHA-256:
  `9c4fe52781cec1f2e914d0679be8910341e4e186c2626326cfde64ba7997dea0`;
  internal receipt hash:
  `3a90dca28bebf0c4f685737eda8a468d7b1426daefa540b001b98695fd25fd48`.
- Remaining limitation: basic helper hints are now visible, but the raw
  Provider still lacked a complete executable-test construction recipe or
  ignored the no-fixture rule. The next separately planned H8-R2S phase must
  audit evidence sufficiency and choose task simplification or a bounded
  same-file cross-window recipe. Do not rerun this arm or run Compact.

### Phase H8-R2S construction-recipe evidence audit

- Observed failure: H8-R2Q1-R stopped before writer because the Provider
  proposed an unknown `runner` pytest fixture even though the four declared
  windows delivered helper call-site hints.
- Diagnostic result: the exact production recording/projection path exposes a
  source-linked `_registry → _Executor → _runtime → _Owner` construction chain
  sufficient for a small no-fixture regression test. The target
  `ProviderToolBoundedWindowMismatch` token is intentionally not a Python
  symbol and must remain a quoted `error_type` string.
- Implemented fix: added tests-first assertions to the existing four-window
  regression, including a valid no-fixture construction and the unchanged
  unknown-fixture rejection. No production metadata, permission, scope,
  budget, reasoning, fallback, or Compact behavior changed.
- Validation evidence: provider round-trip **85 passed**, code generator/patch
  **16 passed**, focused audit **4 passed**, `compileall`, and `git diff
  --check` passed.
- Remaining limitation: this is an offline audit only. A fresh ready-only
  receipt and exactly one raw canary are still required to test whether the
  simplified task is followed by the real Provider. Any fixture, grounding,
  writer, scope, or exact-validation failure must stop the phase; no fallback
  success or Compact claim is allowed.

### Phase H8-R2S no-fixture construction raw canary

- Observed evidence: the fresh DeepSeek arm completed all four declared
  windows, carried structured call-site hints into the generator, used no
  ambient `runner` fixture parameter, produced one code artifact, and passed
  one scoped writer with a valid artifact reference.
- Root cause: exact pytest failed because the generated test called
  `ProviderToolRoundTripRunner()` without its required `owner` and `task`
  arguments, then used `declare_adaptive_window` and `execute_tool_call`
  methods not visible in the declared windows. This is an evidence-surface/API
  construction gap, not a reasoning, budget, permission, or writer problem.
- Implemented fix: none in the raw arm; the fail-closed validation boundary
  correctly stopped after `85 passed, 1 failed`. No retry, fallback success, or
  Compact run was performed.
- Validation evidence: exact command executed once with matching cwd;
  `writer_contract.valid=true`, `exact_validation_count=0`, target-only
  mutation, no source/external sentinel change. Eight requests used
  **29,953 prompt / 2,836 completion / 32,789 total** tokens with reasoning
  disabled and complete usage/finish telemetry. Raw receipt SHA-256:
  `87c0ed01b0faad78602890b04e06b7f4df61afabce0f56f4c08e21bde3a84e75`;
  internal receipt hash:
  `55c486b7135285586654f4dcf0bffe2eba9a1e86044ac0630b92b3c3b673a147`.
- Remaining limitation: helper call-site hints alone do not expose the
  target class constructor or attribute-level API contract. The next phase
  must add the smallest source-linked target construction/API evidence or
  select a task whose complete API is already visible, then repeat offline and
  ready-only gates before any new raw arm.

### Phase H8-R2T target constructor/API evidence

- Observed failure reproduced: the H8-R2S raw arm exposed the class name but
  not a usable bounded constructor. The complete constructor exceeded the
  callable-candidate limit and the Provider emitted `ProviderToolRoundTripRunner()`.
- Implemented fix: added the minimum source-derived bounded constructor
  candidate, preserving `owner`, `task`, required keyword-only `tools`, and the
  bounded `max_rounds=3` hint. Added tests through the real
  `_record_attempts → _declared_generator_context` path. No new metadata kind,
  permission, scope, budget, reasoning, fallback, or API authority was added.
- Harness repair: the first prepared raw command stopped before transport due
  to a temporary runner indexing three windows into two declared files. The
  runner was repaired to bind each window by its declared `file_path`; source,
  target tests, and external sentinels were unchanged. That empty run directory
  is classified as a pre-transport harness defect, not a Provider canary.
- Validation evidence: remote focused suite **103 passed**, compile checks and
  ready-only passed. The ready-only identity was DeepSeek v4 flash with an
  available tokenizer, `missing_fields=[]`, disabled reasoning, and zero
  transport.

### Phase H8-R2T raw canary

- Positive evidence: one fresh raw DeepSeek arm completed all three declared
  windows and generated a source-grounded test artifact asserting the bounded
  `_Runner(owner, task, *, tools, max_rounds=3)` candidate. Reasoning was
  disabled on all five requests; source/external sentinels and project files
  remained unchanged.
- Stop/root cause: after the generator response, the next round failed with
  `Required context cannot fit within the configured prompt budget`. No
  `file_patch_writer` call, exact pytest command, or mutation occurred. The
  failure is in the generator-to-writer context handoff: accumulated history
  and tool-result projection left the required context unable to fit. It is not
  a reasoning or permission failure.
- Validation evidence: **17,684 prompt / 3,670 completion / 21,354 total**
  tokens; finish reasons were `tool_calls` × 4 then `stop`; writer calls,
  validation count, and project mutation were all zero. Raw receipt SHA-256:
  `dba376aee24e39e3b6c651c3d1b8bd4e7f72e6720cecc9e1b6a5a3ae944cc3a6`;
  internal receipt hash:
  `b93b29f9d5085888eb984977086ecf81dd46194555cf4b656899b3e4573c8d61`.
- Remaining limitation: constructor evidence is now visible, but the real
  path still cannot complete the generator-to-writer handoff under the current
  required-context assembly policy. Do not retry this raw receipt, increase
  budgets, change reasoning, or run Compact. The next phase requires a new
  tests-first plan to measure and repair handoff retention/compaction before a
  fresh canary.

### Phase H8-R2U handoff-budget diagnosis (U1)

- Observed failure reproduced offline: the real runner's
  `_initial_context_with_dynamic_messages()` marks every dynamic assistant and
  tool exchange as required; tool exchanges are also non-truncatable. A
  deterministic assembler trace with task, artifact, read, and prior decision
  facts raised `ContextAssemblyBudgetError` and identified a
  `provider:round-message:*` candidate as omitted-required.
- Implemented fix: none yet. U1 added only a regression/diagnostic test and
  recorded candidate-level failure evidence; production retention semantics
  remain unchanged until the U2 contract is approved by tests.
- Validation evidence: provider/code-patch **104 passed**, context/memory **84
  passed**, code-generation/execution/delta **69 passed**, compileall and diff
  check passed. No Provider request or filesystem mutation occurred.
- Remaining limitation: the handoff still conflates active wire state with
  superseded history, and structured message rendering may preserve raw
  assistant/user content. U2/U3 must preserve active tool-call/result pairs and
  required task/scope/artifact/validation facts while making superseded wire
  history compactable or omittable. H8-R2T remains frozen; no retry or Compact.

### Phase H8-R2U handoff retention repair (U2/U3)

- Implemented fix: `_initial_context_with_dynamic_messages()` now identifies
  the latest assistant tool-call as the active wire boundary. Superseded rounds
  are represented by one bounded derived summary with optional retention; the
  active assistant/tool exchange and trailing handoff guidance remain required.
- Contract evidence: the old generated/tool payload is absent from structured
  messages, while the active `file_patch_writer` call and matching tool result
  remain intact. Required task/artifact facts remain selected and no required
  candidates are omitted in the repaired synthetic handoff.
- Validation evidence: provider/code-patch **105 passed**; context/memory/
  code-generation/execution/delta **153 passed**; compileall and diff check
  passed. No Provider request or filesystem mutation occurred.
- Remaining limitation: this is still offline evidence. A fresh remote
  source/runner-bound ready-only gate and exactly one raw canary are required to
  verify the real DeepSeek generator-to-writer continuation. Do not replay
  H8-R2T, raise budgets, change reasoning, or run Compact.

### Phase H8-R2U U5 fresh raw canary

- Observed failure: the source/runner-bound DeepSeek arm issued six requests
  with disabled reasoning, completed three distinct declared reads, then
  repeated those exact normalized windows. The duplicate ledger preblocked the
  repeated calls and the task stopped with `ProviderToolNoProgress after 2
  round(s)` before generator, writer, pytest, or mutation.
- Implemented fix: none in the raw arm. The duplicate and no-progress boundary
  failed closed; no fallback or alternative validation command was treated as
  success.
- Validation evidence: usage was **20,132 prompt / 1,452 completion / 21,584
  total** across six DeepSeek v4 flash requests; all finish reasons were
  `tool_calls`, reasoning was disabled, source/external sentinels were
  unchanged, `target_changed=false`, `writer_calls=0`, and
  `exact_validation_count=0`. Raw receipt hash:
  `afc2639bc46ed599b58d042746a58c2430caa32ca2e77aa87bbf855b7a240af9`.
- Remaining limitation: the receipt lacks per-candidate context-selection
  decisions and cannot distinguish stale/misaligned wire projection from a
  Provider decision failure. The next tests-first phase is
  `PHASE_H8R2V_DUPLICATE_READ_STOP_PLAN.md`; no Compact or benefit claim is
  authorized.

### Phase H8-R2V raw receipt diagnosis

- Observed failure: the source/runner-bound DeepSeek arm kept context assembly
  `ready` with no omitted required candidates, completed three declared reads,
  and successfully produced a typed `code_artifact` (`code_unit_bc160e5c`).
  Before any writer call, however, the mutation-capable Provider surface still
  exposed `code_unit_generator`, `file_patch_writer`, and `command_executor`.
  The Provider selected two command attempts; the first was
  `sed -n '1,35p' Code/tests/test_provider_tool_roundtrip.py` and was rejected
  by the exact validation contract.
- Implemented fix: none in this diagnostic phase. The typed admission failed
  closed; no fallback, alternate command, writer, pytest, or mutation was
  counted as success.
- Validation evidence: six outer request diagnostics reported
  `assembly_status=ready` and `omitted_required_candidate_ids=[]`; all recorded
  requests used disabled reasoning. Across the six outer decisions plus one
  nested generator request, usage was **23,109 prompt / 1,574 completion /
  24,683 total**. `writer_calls=0`, `exact_validation_count=0`,
  `project_mutation=false`, and `target_changed=false`. Raw receipt internal
  hash: `c71164f023ca2362f330a71b35cdb3b2149ee22756889467f2bb55371fd90cbf`.
- Root-cause classification: primary `writer_route_missing` (the next action
  surface was not narrowed after evidence/generator progress), secondary
  `validation_boundary_violation` (the non-exact command was correctly
  refused). The receipt does not persist the exact projected artifact-ref
  payload sent to the outer Provider, so artifact-consumption telemetry is a
  remaining limitation rather than a claimed missing artifact.
- Remaining limitation: this raw arm proves neither mutation quality nor
  token benefit. The next phase is the tests-first purpose-aware routing
  repair in `PHASE_H8R2V_TOOL_SURFACE_ROUTING_REPAIR_PLAN.md`; do not replay
  this arm, change budgets/reasoning, or run Compact.

### Phase H8-R2V purpose-aware routing repair (offline)

- Observed failure addressed: a mutation-capable pre-writer Provider surface
  included `command_executor`, allowing an exploratory command before the
  required generator-to-writer handoff.
- Implemented fix: `_tools_for_request()` now removes both `file_reader` and
  `command_executor` after declared reads complete while mutation tools remain
  active; the existing post-mutation route still exposes only
  `command_executor`. Read-only command routes are unchanged. The round-trip
  result and task evidence envelope now record bounded code-artifact handoff
  lineage (checksum and source/provider IDs, never generated code).
- Validation evidence: the new route test first failed on the old surface and
  then passed after the change; provider round-trip **95 passed**; the full
  offline suite **1207 passed, 1 warning** with the four archived
  script-dependent tests excluded; compileall and diff check passed. No
  Provider request, filesystem mutation, or Compact run occurred.
- Remaining limitation: the frozen H8-R2V raw receipt remains failed and cannot
  be replayed. A new source-bound ready-only check and exactly one raw canary
  are required to verify DeepSeek's real generator → writer → exact pytest
  behavior before any Compact or benefit claim.

### Phase H8-R2V-RC routing-repair raw canary

- Observed result: after the purpose-aware routing repair, one fresh source-bound
  DeepSeek v4 flash arm completed three declared read windows, produced a valid
  `code_artifact`, applied one scoped `file_patch_writer`, and ran the exact pytest
  command successfully. The previous pre-writer `command_executor` escape path did
  not recur; after reads the Provider saw only generator/writer, and after writer it
  saw command-only validation.
- Implemented fix validated: the existing `_tools_for_request()` phase boundary and
  bounded artifact handoff diagnostics are effective in a real mutation round trip.
  Duplicate reads were refused by `ProviderToolDuplicateAttempt`; no fallback or
  alternate validation was used.
- Validation evidence: all 8 context diagnostics were `ready` with
  `omitted_required_candidate_ids=[]`; writer contract was valid, target-only diff
  contained one new test, exact pytest passed once in the bound cwd, and source/
  external sentinels were unchanged. Nine Provider responses used **25,763 prompt /
  2,055 completion / 27,818 total** tokens with complete usage/finish telemetry and
  disabled reasoning. Raw receipt file SHA-256:
  `22093bb4fd5f6da65d327c9dc672c1aabfb645f200c16f7a1e57cc2e6eabcaae`; internal
  receipt hash: `9c06f5177ed32e7182db189e0a5ef28d728421ab813e9049873945be6a827b2c`.
- Remaining limitation: this is one single-file, single-test, DeepSeek disabled-
  reasoning canary. It proves the repaired route's execution chain for this task,
  not universal Provider/task quality or Compact benefit. The next phase must have a
  separate Compact paired-canary plan; no Compact arm was run here.

### Phase H8-R2V-CM repaired-route Compact paired canary

- Observed result: one raw/Compact pair used the same source-bound task, provider,
  disabled reasoning, budget, tool routing, write scope, and exact pytest oracle. Both
  arms completed declared reads, generator/artifact handoff, one scoped writer, and
  exact validation with unchanged source/external sentinels.
- Compact behavior: the four optional history candidates were atomically governed by
  `h8:artifact:history-summary`; required task/constraint/anchor candidates stayed
  selected and all context diagnostics were `ready` with no omitted required IDs.
  Pre-writer routing excluded `file_reader`/`command_executor`; post-writer routing was
  command-only in both arms.
- Validation evidence: offline focused tests **145 passed**; raw receipt file SHA-256
  `3715691538c02041358ae88f3d83db78365d4286748682c9e95cc395e25658cb`; Compact
  receipt file SHA-256 `5a7b688dd5cfad0f214f0836b7a0629a35708b46300ce061ad4353e61ffbdbb7`;
  campaign internal hash `18898237c874a10e11503fde04ced3d9485b7332cfcc0ccbc1bc92c1cf20ac9e`.
  Prompt tokens fell **25,758→14,715 (42.87%)** and total tokens
  **27,877→17,665 (36.63%)**, while completion tokens rose **2,119→2,950
  (39.22%)** and response count fell 9→7. No LLM-generated summary or fallback was
  used.
- Remaining limitation: this proves one-pair safety and a directional cost signal,
  not byte-identical semantic output, statistical confirmation, cross-task/provider
  quality, or default-on readiness. The next phase requires a multi-pair confirmation
  plan with alternating arm order and a stronger behavioral/AST quality oracle.

### Phase H8-R2V-CF Compact confirmation matrix

- Observed result: three alternating raw/Compact pairs (six arms) all completed the
  repaired read → generator → writer → exact pytest chain. A bounded AST quality oracle
  checked the task-specific generated test for the required constructor candidate,
  callable discovery, and 256-character bound; all six quality observations passed.
- Context evidence: every arm was `assembly_status=ready` with no omitted required
  candidates. Compact arms atomically replaced the four optional history segments with
  the governed summary; raw arms did not. No pre-writer command attempt occurred, and
  P3's two duplicate reads were refused before a distinct third window completed.
- Validation evidence: **145 offline focused tests passed**; all six receipts had valid
  canonical hashes, one valid scoped writer, one exact pytest pass, zero forbidden
  paths, and unchanged source/external sentinels. Aggregate raw usage was **77,740
  prompt / 6,898 completion / 84,638 total**; Compact was **42,488 / 7,233 / 49,721**:
  prompt −45.35%, completion +4.86%, total −41.25%. Campaign internal hash:
  `7166fe2c40272dec334ed805f591c6bc40cd29c822b66f7156075b5b288d967e`.
- Remaining limitation: this is one task/provider/reasoning stratum and the quality
  oracle is task-specific; it does not prove arbitrary semantic equivalence or global
  default-on safety. Compact remains feature-flagged. A next task stratum or provider
  gate requires a new plan before execution.

### Phase H8-R2W read-only Compact suspicious-success diagnosis

- Observed failure: the first second-task raw/Compact read-only pair was marked passed
  with zero mutation and lower Compact tokens, but both final responses omitted two
  required CLI symbols (`build_parser`, `_run_openpilot`). The original pair is frozen
  and excluded from all benefit/quality denominators.
- Root cause: the selection task encoded three independent required symbols as one
  alternatives tuple, `("build_parser", "main", "_run_openpilot")`. The existing
  stage25 oracle correctly treats tuple members as alternatives for synonym/path
  requirements, so `main` alone incorrectly satisfied the malformed contract.
- Diagnosis evidence: corrected stage30 `single_file_symbol` re-evaluation marked both
  old receipts `passed=false`, with missing `build_parser` and `_add_run_parser`;
  offline valid, missing-symbol, missing-main, and wrong-path fixtures passed the
  corrected positive/negative gate. No Provider replay or production change occurred.
- Remaining limitation: the second-task Compact signal is untrusted until a new
  source-bound selection receipt uses the corrected task contract and a fresh pair
  passes the strict quality oracle. The old `8386→3574` token observation is not a
  result claim.

### Phase H8-R2W-R corrected read-only Compact offline contract gate

- Observed gap addressed: the frozen H8-R2W task selection encoded three independent
  required CLI symbols as one alternatives tuple, allowing a response that mentioned
  only `main` to pass. The experiment wrapper now binds `provider:task` and the
  stage25 quality oracle to the corrected Phase 30 `single_file_symbol` contract.
- Validation evidence: the remote focused offline suite passed **4 tests**. Both
  raw and Compact projections contain `build_parser`, `_add_run_parser`, `main`, and
  the module-path synonym; required candidates are identical across arms; Compact
  summary lineage covers all optional history candidates; positive and each
  single-missing-fact fixture fail closed. No Provider transport or mutation occurred.
- Remaining limitation: this is only an offline contract gate. Provider quality,
  token usage, and Compact benefit remain unproven until a fresh source-bound
  selection receipt passes ready-only and a new raw/Compact pair is executed. The
  old suspicious-success receipts remain excluded.

### Phase H8-R2W-R corrected read-only Compact paired canary

- Observed result: a fresh source-bound raw → Compact pair completed against
  DeepSeek v4 flash with disabled reasoning. Both arms used the corrected four-part
  quality contract, file-reader-only scope, and the same declared `cli.py` read.
- Validation evidence: **59 focused tests** passed before transport; both arms
  passed quality and safety gates, had complete usage/finish telemetry, unchanged
  source sentinels, and `project_mutation=false`. Raw used **7,893 prompt / 846
  completion / 8,739 total** tokens; Compact used **3,050 / 703 / 3,753**. Prompt
  fell 61.36% and total fell 57.05%. Raw and Compact receipt file hashes are
  recorded in the evidence index and result document. No command, writer, fallback,
  retry, or reasoning content occurred.
- Compact evidence: required system/task/constraint candidates were identical;
  Compact selected one governed summary with lineage to all four optional history
  segments. Each arm made two identical full-file reads before finalization;
  `duplicate_only_rounds=0`, so this is recorded as a follow-up efficiency signal,
  not a quality failure.
- Remaining limitation: this confirms one corrected read-only task/provider/
  reasoning stratum only. It does not authorize default-on Compact or establish
  cross-task/provider generalization; the next action is receipt-level audit and
  selection of another independent task stratum.

### Phase H8-R2X two-file linkage Compact offline contract gate

- Observed gaps addressed: the initial two-file wrapper did not enumerate all
  required facts in the Provider task candidate, and its first negative relation
  fixture was too local for the intentionally bounded relation oracle. The
  experiment-local wrapper now carries five independent facts, three required
  relations, and an explicit denial of the forbidden `args.once` relation; the
  negative fixture separates symbols beyond the oracle's local window.
- Validation evidence: **61 focused tests passed**. Required raw/Compact
  projections are identical, Compact summary lineage covers four optional history
  segments, positive quality passes, and missing facts/relations/forbidden
  relation all fail closed. No Provider transport or mutation occurred.
- Remaining limitation: this is only the X1 offline contract gate. A new
  source-bound ready-only receipt and one real raw → Compact pair are still
  required; no two-file quality or token claim is made.

### Phase H8-R2X-D two-file linkage quality-oracle diagnosis

- Observed failure: the first fresh H8-R2X raw arm completed both declared file
  reads with file_reader-only scope and zero mutation, but was marked
  `failed_quality`; Compact was correctly not executed. The final answer did
  explicitly deny `args.once → _execute_agent_generator` using Markdown backticks.
- Root cause: literal required-phrase matching did not normalize backticks, while
  the forbidden-relation check treated the same line as positive unless it also
  contained the narrow phrase `direct callee`. This was an experiment oracle
  false negative, not a Provider scope or evidence failure.
- Validation evidence: D1 reproduced the mismatch, D2 repaired only the
  experiment wrapper to normalize formatting and honor an explicit negative
  denial, and D3 passed **61 focused tests**. A genuinely positive forbidden
  relation and a missing denial still fail closed. Frozen raw receipt file SHA-256
  is recorded in the evidence index; no Compact/token claim is made.
- Remaining limitation: the failed raw receipt is permanently excluded and must
  not be replayed. A fresh source-bound selection/ready-only gate and new pair are
  required under the repaired oracle.

### Phase H8-R2X-R corrected two-file linkage raw → Compact canary

- Observed result: after the offline oracle repair, a new source-bound v2 pair
  completed the two-file read-only relation task in the prescribed raw → Compact
  order. The v1 raw failure was not replayed; v1 Compact remained unexecuted.
- Validation evidence: both v2 arms passed five facts, three relations, explicit
  forbidden-relation denial, file_reader-only scope, unchanged two-file sentinels,
  complete usage/finish telemetry, and zero mutation. Raw used **9,814 prompt /
  667 completion / 10,481 total** tokens; Compact used **5,072 / 682 / 5,754**.
  Prompt fell 48.32% and total fell 45.10%; completion rose 2.25%. Receipt and
  campaign hashes are recorded in the evidence index.
- Compact evidence: required candidates were identical; Compact selected one
  governed summary with lineage to all four optional history segments. Both arms
  reread the two files on the second tool round; `duplicate_only_rounds=0`, so
  this remains an efficiency follow-up rather than a quality failure.
- Remaining limitation: this is one corrected DeepSeek disabled-reasoning,
  multi-file read-only stratum. It does not authorize default-on Compact or
  establish mutation/cross-provider generalization.

### Phase H8-R2Y adaptive-window evidence offline contract gate

- Observed scope: the next task strata covers typed adaptive/full-read/bounded
  window semantics and line/truncation metadata across `file_reader.py` and
  `tooling.py`.
- Validation evidence: **63 focused tests passed**. The experiment-local task
  candidate explicitly lists independent metadata facts; raw/Compact required
  projections match; Compact lineage covers four optional history segments; each
  missing-fact fixture and the negative-marker fixture fail closed. No Provider
  transport or mutation occurred.
- Remaining limitation: only the Y1 offline gate is complete. A fresh source-
  bound ready-only check and one raw → Compact Provider pair are still required;
  no adaptive-window quality or token claim is made.

### Phase H8-R2Y adaptive-window evidence Compact pair

- Observed result: the metadata-heavy adaptive-window task completed a fresh raw
  → Compact pair with DeepSeek v4 flash and disabled reasoning. Both arms covered
  the two declared source files and passed the corrected independent metadata
  contract.
- Validation evidence: **63 focused tests** passed before transport; both arms
  passed facts/relations, file_reader-only scope, unchanged sentinels, complete
  usage/finish telemetry, and zero mutation. Raw used **12,508 prompt / 913
  completion / 13,421 total** tokens over 4 requests; Compact used **4,138 /
  647 / 4,785** over 3 requests. Prompt fell 66.92%, total 64.35%, completion
  29.13%. Receipt and campaign hashes are in the evidence index.
- Compact evidence: required candidates were identical and the governed summary
  covered all four optional history segments. Raw issued one bounded
  `max_lines=200` read and later reread the target; `duplicate_only_rounds=0`,
  retained as an efficiency follow-up rather than a quality failure.
- Remaining limitation: this is one DeepSeek disabled-reasoning, metadata-heavy
  read-only stratum. It does not authorize default-on Compact or infer
  mutation/cross-provider generalization.

### Phase H8-R2Z three-strata Compact confirmation

- Observed objective: determine whether the corrected H8-R2W, H8-R2X, and
  H8-R2Y read-only Compact signals persist across three alternating pairs per
  stratum, without reusing any prior receipt.
- Execution evidence: the isolated `openpilot-air:/Users/abaaba/work/openpilot-context-experiment-20260808-h0`
  workspace was bound to the H0 source snapshot (`6de5f4c48e7d90e9fb2a7dda4f831115522e5959`;
  workspace commit `a044d79c8893a6ad326dd8a4ead129f34593601b`). The v3 gate had
  9 source-bound selections, zero Provider calls, and zero mutations. The
  focused W/X/Y/Z plus initial-context suite passed **12 tests**.
- Validation evidence: the v2 campaign completed **9 pairs / 18 arms** with
  DeepSeek v4 flash, disabled reasoning, `file_reader`-only scope, complete
  usage/finish telemetry, unchanged sentinels, `quality.passed=true`, and
  `project_mutation=false` for every arm. Compact projections atomically
  selected one governed history summary whose lineage covered all four optional
  dialog segments; required system/task/constraint candidates remained intact.
- Usage evidence: provider prompt tokens fell `90,901→36,773` (59.55%), total
  tokens `97,487→43,083` (55.81%), and requests `30→27` (10.0%). Completion
  tokens fell slightly overall (`6,586→6,310`, 4.19%), but increased in the
  single-file stratum (`1,848→2,398`, 29.76%) while total tokens still fell
  54.81% there. Cache hit/miss was raw `83,712/7,189` versus Compact
  `31,488/5,285`; provider total is therefore not treated as a direct billing
  claim. The median of pair-level reductions was 61.35% for prompt and 53.80%
  for total; the small-sample bootstrap intervals remain descriptive.
- Decision: **conditional confirmation**. Compact may advance to a separately
  planned controlled experiment, but remains feature-flagged and opt-in. The
  plan did not pre-register a numeric primary threshold/non-inferiority margin,
  and single-file completion expanded; this does not establish mutation,
  cross-provider, or arbitrary semantic equivalence.
- Remaining limitations: adaptive raw pair 1 reached the typed reader page-cap
  signal but completed quality/evidence safely; the runner's selection-path
  loader is repository-root-sensitive and must be fixed/validated before a
  rerun from another cwd. The campaign JSON does not carry host/source fields
  at top level, so the result is bound through the separately hashed H0 receipt;
  no secret is copied or serialized.

### Phase H8-R2AA A runner and evidence-envelope repair

- Observed failure: the prior H8-R2Z loader depended on process cwd, did not
  verify its own gate hash or exact selection set, and accepted a truncated
  selection list when an old hash was retained. The campaign also lacked a
  strict outer evidence envelope.
- Implemented fix: selection paths are root-bound and traversal-safe; gate,
  selection, receipt, source, runner, envelope, and harness hashes are checked;
  the exact 9-key stratum/pair set and excluded prior phases are required;
  prepared gates are mandatory; and a strict experiment-only typed envelope
  records source/host/provider/policy/side-effect/claim-boundary information.
  Unknown usage and unknown side effects cannot be zero-filled. The envelope
  helper is included in the harness hash. No production metadata or Compact
  default changed.
- Validation evidence: active H0 gate v3 loaded successfully from `/tmp` with
  9 selections and outer gate hash
  `sha256:69bc92c72343b769b338e363645040f3b5c57e38f883f05e65f39f6c747284a5`.
  The focused H8-R2AA envelope/runner plus W/X/Y/Z/36F suite passed **27
  tests**, with zero Provider transport and zero mutation. Tampered gate,
  selection, receipt, envelope, harness, and path-escape cases fail closed.
- Remaining limitation: this proves only the offline experiment boundary. The
  v1/v2 gate attempts are superseded and excluded. A fresh source-bound
  ready-only mutation selection and a separately planned raw/Compact mutation
  pair are still required; no mutation or token-benefit claim is made.

### Phase H8-R2AA B ready-only mutation selection

- Observed objective: freeze one new mutation task after the A evidence gate,
  without allowing readiness preparation to become an execution attempt.
- Implemented selection: a disposable calculator symbol-patch reproduction with
  one declared write target, typed `file_patch_writer` operation/symbol fields,
  exact pytest command, `real_mutation` policy, explicit mutation projection
  opt-in, user confirmation, and disabled reasoning. Raw and Compact candidate
  arms share the same required task/constraint and policy.
- Validation evidence: active H0 generated and independently revalidated the v2
  receipt from `/tmp`; the receipt binds seven source files, runner/harness
  hashes, source commit, task contract hash
  `sha256:85bd9c5ca38dd9490f790d7c2e7967471c6b1c91647eafd7a964aaf4e457c821`,
  and a strict outer-receipt envelope. Provider calls, transport, project,
  memory, network, writer, verification, retry and fallback counters are all
  known zero. Secret scan passed.
- Remaining limitation: ready-only proves only that C is admissible. It does
  not prove writer correctness, exact pytest success, mutation safety, or
  Compact token benefit. The next stage must execute exactly one raw/Compact
  pair with no retry or replacement denominator.

### Phase H8-R2AA C exactly-one raw/Compact mutation pair

- Observed objective: test the first real mutation pair after the repaired
  runner/evidence and ready-only gates, without mixing in another task or
  changing provider/reasoning/budget policy.
- Execution evidence: active H0 ran exactly `raw_segmented` then
  `compact_segmented` in separate disposable workspaces. Both arms used one
  valid symbol-scoped `file_patch_writer` call on `calculator.py`, preserved
  the public API, changed only the target, and ran the exact requested pytest
  command successfully. External sentinels were unchanged; no fallback,
  retry, replay, or suspicious-success signal occurred.
- Usage evidence: raw `7273/485/7758` prompt/completion/total tokens versus
  Compact `3945/519/4464`; prompt fell **45.76%**, total fell **42.46%**, and
  completion rose **7.01%**. Requests were `4/4`; cache hit/miss was raw
  `384/6889` versus Compact `1152/2793`. Reasoning was disabled; all responses
  had complete usage/finish telemetry and reasoning remained typed unknown.
- Decision: **one-pair canary passed** the pre-registered strict margin
  `Compact total <= raw total` (`4464 <= 7758`). This authorizes only a
  separately planned confirmation matrix; Compact remains feature-flagged and
  no distribution-wide or cross-provider claim is made.
- Remaining limitation: the task is a single disposable symbol-patch
  reproduction and one pair is not a stable estimate. A multi-pair mutation
  confirmation with pre-registered sample/stop rules is still required before
  any default-policy decision.

### Phase H8-R2AA D paired-canary decision

- Observed decision input: sealed C campaign contained two passing disposable
  arms with complete writer, scope, API, exact-pytest, sentinel, lineage and
  receipt evidence.
- Decision evidence: primary strict margin passed (`Compact 4464 <= raw
  7758`); prompt fell 45.76%, total fell 42.46%, completion rose 7.01%, and
  requests stayed 4/4. Cache hit/miss and typed disabled-reasoning observations
  were kept separate from the total-token claim.
- Decision: **CONDITIONAL**. The safety/quality canary passed, but one task and
  one DeepSeek profile cannot estimate variance or cross-provider behavior.
  Compact remains opt-in/feature-flagged; no production default or policy was
  changed.
- Remaining limitation: a fresh multi-pair mutation confirmation with fixed
  sample/stop rules is required before any default-policy discussion.

### Phase H8-R2AA E three-pair mutation confirmation matrix

- Observed objective: determine whether the H8-R2AA C raw/Compact mutation
  signal survives three fresh pairs with alternating arm order, without
  changing Compact, budget, reasoning, Provider, or permission policy.
- Implemented gate: the experiment now uses a strict matrix selection envelope
  with exactly 3 pairs/6 arms, a canonical schedule hash, H8-R2AA B v2 as the
  frozen task input, an explicit historical exclusion manifest, raw H0 receipt
  binding, and typed per-arm pair/workspace/usage/cache/reasoning evidence.
  The runner refuses retry, replay, replacement, denominator repair, or a
  partial campaign presented as passed. This is experiment-only; no production
  metadata or default changed.
- Validation evidence: active H0 focused suite passed **15 tests**. The
  ready-only receipt recorded zero Provider transport, project/memory/network
  mutation, writer, verification, retry, and fallback actions. The real matrix
  completed exactly **6/6** arms; independent revalidation confirmed campaign
  and all arm receipt hashes, one typed writer per arm, exact pytest, unchanged
  sentinels, zero forbidden paths, complete usage/finish telemetry, and no
  suspicious-success signal.
- Usage evidence: raw/Compact totals were `23,393/13,241` (−43.40%); prompt
  tokens were `21,877/11,753` (−46.27%); completion tokens were `1,516/1,488`
  (−1.85%); requests stayed `12/12`; all three pairs had Compact total <= raw.
  Cache hit/miss was raw `16,512/5,365` versus Compact `7,936/3,817` and is
  not treated as a billing claim. Reasoning was disabled and typed unknown.
- Decision: **bounded confirmation**. The primary rule passed (aggregate
  Compact <= raw and 3/3 pair-level passes), authorizing only a separately
  planned broader task/provider matrix. Compact remains feature-flagged and
  opt-in; no global default or cross-provider claim is made.
- Remaining limitations: one task stratum, one DeepSeek profile, disabled
  reasoning, and a fixed mutation budget are still held constant. The next
  experiment must choose whether to expand task strata or providers, and must
  keep reasoning/permission factors separate.

### Phase H8-R2AA F cross-strata evidence normalization

- Observed objective: determine whether the E single-file confirmation and the
  existing Phase45 cross-file-linkage confirmation can be reported together
  without silently merging runner authorities, failed/suspicious receipts, or
  mismatched task shapes.
- Implemented offline normalizer: E's six arm receipts and Phase45's six
  immutable arm receipts are validated independently for canonical hash,
  safety predicates, exact validation, usage/finish completeness, provider and
  disabled-reasoning policy, and pair cardinality. Cache/reasoning gaps remain
  typed unknown; no receipt is zero-filled or rewritten. Diagnostic canaries
  and failed/suspicious/old artifacts are explicitly excluded.
- Validation evidence: focused normalizer suite passed **3 tests**; transport
  attempted and project mutation were both false. E and Phase45 each retained
  3/3 pair-level Compact <= raw results.
- Usage evidence: E raw/Compact totals were `23,393/13,241`; Phase45 totals
  were `67,594/33,221`. Descriptively pooled totals were `90,987/46,462`
  (−48.94%); this is not a billing claim and does not replace per-stratum
  receipt authority.
- Decision: **PASS for offline normalization**. Two DeepSeek mutation strata
  support a bounded Compact token-reduction signal. This does not authorize a
  production default, cross-provider generalization, or a reasoning-policy
  conclusion.
- Remaining limitations: Phase45 uses an older runner/evidence shape and is
  intentionally reported separately. A future task/provider matrix must use
  equivalent typed-wire and completion contracts, with reasoning and
  permission factors held separate.

### Phase H8-R2AA G strict-envelope cross-file mutation matrix

- Observed failure and diagnosis: G v2 stopped on a harness validator bug that
  looked for `fixture_keys` in the receipt shape instead of the manifest shape.
  G v3 then exposed a real policy-routing defect: a three-file `implement`
  task converted configured disabled reasoning to provider-default because the
  routine policy classified only tasks with at most two reads as routine. The
  Compact arm reached `finish_reason=length` with `reasoning_tokens=3296` and
  performed no writer action. Both attempts are immutable diagnostics and are
  excluded from the denominator.
- Implemented fix: the G validator reads the frozen manifest shape, and G v4
  passes an explicit typed `ReasoningPolicy(mode=DISABLED)` into the executor.
  New selection/runner hashes and an exclusion manifest bind the repaired run;
  no production default or permission boundary changed.
- Validation evidence: G v4 focused contract suite passed **3 tests** and its
  ready-only gate recorded zero Provider calls/project mutation. The real v4
  matrix completed **6/6**; independent checks confirmed all three declared
  files were read, one valid writer per arm, exact pytest, unchanged API and
  sentinels, zero forbidden paths, complete usage/finish telemetry, disabled
  request policy, no duplicate-only/no-progress round, and valid receipt hashes.
- Usage evidence: raw/Compact totals were `41,795/19,456` (−53.45%); prompt
  `39,596/17,270` (−56.38%); completion `2,199/2,186` (−0.59%); requests
  `18/16`; pair-level pass was 3/3. Cache hit/miss remains secondary and
  reasoning observations remain typed unknown/not applicable.
- Decision: **PASS for the strict cross-file DeepSeek stratum**. This confirms
  that stronger three-file evidence can retain Compact savings once reasoning
  policy is actually held fixed. It does not authorize global Compact,
  mutation projection, cross-provider behavior, or a general reasoning claim.
- Remaining limitation: the v3 failure shows that requested reasoning mode and
  effective provider policy must be audited per request for every task shape;
  future provider/task matrices must not infer routine complexity from a file
  count without an explicit typed override.

### Phase H8-R2AB reasoning policy routing repair

- Observed failure: G v3 exposed that a cross-file `implement` task reached
  provider-default reasoning and exhausted the completion ceiling before a tool
  decision. A first local repair attempt that globally preserved `disabled` for
  complex tasks contradicted the established Stage 7E/7F production baseline and
  failed existing code-generation contracts; that path was discarded.
- Implemented fix: preserve the intended routine `disabled` versus
  standard/complex `provider_default` semantics, but route task planning through
  typed `ReasoningDecisionComplexity` values. Tool-event requests now record the
  complexity route; tool-loop fallback, code generation, and enhancement
  completion use the provider-neutral resolver rather than an unlabelled boolean
  branch.
- Validation evidence: local and staged remote focused suites each passed **255**;
  the wider local suite passed **1210** after excluding four stale tests whose
  deleted experiment scripts are absent. Compileall, diff check, and remote
  DeepSeek ready-only passed with `missing_fields=[]`, tokenizer available, and
  zero transport.
- Remaining limitations: no real provider request was made in H8-R2AB, no budget
  or effort policy was changed, and no Compact or quality/token benefit claim is
  made. The next phase is a fixed-context reasoning-only canary.

### Phase H8-R2AC reasoning telemetry envelope repair

- Observed failure: the first H8-R2AB pair executed successfully, but its
  experiment receipt serialized `ReasoningPolicy` as a Python string and omitted
  `trace_info`; the provider-native round-trip path also failed to emit the typed
  complexity route. The pair was retained as an immutable diagnostic and not
  admitted to a broader denominator.
- Implemented fix: stage35 now serializes requested and resolved policies as JSON,
  requires bounded `routine|standard|complex` complexity, and fails closed on
  missing fields. `ProviderToolRoundTripRunner` now carries the typed complexity
  in every provider-round trace.
- Validation evidence: local production focused suite **350 passed**; remote
  stage35 tests **6 passed** and provider-roundtrip tests **95 passed**. Remote
  ready-only reported DeepSeek v4 flash, tokenizer available, `missing_fields=[]`,
  zero transport, and structured policy/resolution fields. One fresh disabled
  cross-file replay passed exact pytest and emitted complete per-request evidence;
  receipt hash is `sha256:0416959a64059b12d7f8470d59067309e759b771bd01f3b3c938e18f85b3e281`.
- Remaining limitations: the repaired single arm validates evidence integrity but
  makes no reasoning/Compact/budget benefit claim. A fresh multi-pair reasoning
  matrix is the next authorized experiment and must keep all other factors frozen.

### Phase H8-R2AD reasoning-only full-architecture matrix

- Observed result: the fresh matrix selection passed its zero-transport gate and
  pair-01 completed both typed routes. Pair-02 provider-default reached one scoped
  writer but generated `cd <project> && python -m pytest -q tests/test_calculator.py`;
  the exact validation contract rejected that command before pytest ran.
- Safety behavior: the runner marked the arm `failed_after_mutation`, preserved
  complete policy/resolution/complexity/usage/finish evidence, and stopped without
  retry, replacement, or denominator repair. Independent canonical-hash checks
  passed for all three receipts.
- Descriptive usage: pair-01 disabled total was `7,282` with zero observed
  reasoning tokens; provider-default total was `8,475` with `517` reasoning tokens.
  This is not a route benefit claim because the matrix stopped before three
  complete pairs and one provider-default arm failed validation.
- Remaining limitation: the `cd ... &&` command-prefix boundary needs a separate
  diagnosis/contract decision. Do not rerun H8-R2AD or change Compact/budget until
  that validation semantics is settled.

### Phase H8-R2AE validation command canonicalization

- Observed failure: the H8-R2AD provider-default arm rendered the typed validation
  command with a `cd ... &&` shell prefix. Admission correctly refused it before
  pytest, but admission, ToolEventLoop, and round-trip verification used separate
  command comparisons. A first staged replay also exposed a source-binding error
  (the receipt required `reasoning_complexity` while the selected runtime copy did
  not emit it), followed by a baseline mutation-shape error where an existing file
  was offered as `create_file` and was denied by the zero-create budget.
- Implemented fix: added a shared typed argv normalization/match helper, wired all
  three validation decision points to it, and strengthened the command tool schema
  and mutation prompt to pass `cwd` separately and prohibit `cd`, shell chaining,
  pipes, redirection, and substitutions. Existing-file `file_replace` was supplied
  only through the experiment's explicit baseline hint; write permissions and
  budgets were not widened.
- Validation evidence: local helper/admission/round-trip suite **131 passed**;
  event-loop/planning validation suite **30 passed**; remote staged provider
  focused suite **10 passed**; compileall, diff check, and secret scans passed.
  Ready-only confirmed DeepSeek v4 flash, tokenizer available, complete fields,
  and zero transport.
- Real evidence: one fresh valid DeepSeek mutation replay passed with exact
  `python -m pytest -q tests/test_calculator.py`, one validation execution,
  disposable-root cwd binding, unchanged public API, and complete per-request
  routine/disabled reasoning telemetry. Canonical receipt hash is
  `sha256:8486574b368fd2716cc7e1d50e14bd07d6cc1b0933f00bc54009d1544fd1f553`.
- Remaining limitation: this is one validation-contract arm only. It establishes
  command safety and evidence closure, not a Compact, reasoning, budget, or task
  success-rate benefit. The next reasoning matrix must freeze this command
  contract and the staged-source binding.

### Phase H8-R2AG provider-default tool continuation repair

- Observed failure: H8-R2AF stopped its complex/provider-default arm after three
  DeepSeek tool calls because the response had no `reasoning_content`. The
  round-trip helper treated every resolved mode other than explicit disabled as
  requiring that field, even when the request was `provider_default` and the
  Provider had legitimately omitted it. The failure receipt remained immutable;
  no mutation, validation, or fallback occurred.
- Implemented fix: `_requires_reasoning_content(response)` now branches on the
  typed resolved mode. Disabled never requires the field; provider-default only
  requires it when the current response actually carries it; explicit enabled
  and adaptive-enabled remain fail-closed. Tool-call identity and ordering
  checks are unchanged, and no metadata field was added.
- Validation evidence: tests-first local and remote staged focused suites each
  passed **147 tests**, including a full runner integration case for a
  provider-default response without reasoning content; compileall and corrected
  grep-based secret scans passed.
  The remote ready-only receipt recorded `transport_attempted=false`,
  `project_mutations=0`, DeepSeek v4 flash, tokenizer available, and complete
  disabled/provider-default route resolution. Independent receipt verification
  passed; ready-only receipt hash is
  `sha256:a8d5eb956ae16e468b1edba670f950165d2004c6955df30403b08a541e70039e`.
- Decision: **PASS for provider-default continuation semantics**. This authorizes
  a separately planned new reasoning matrix only; it does not repair or augment
  H8-R2AF's denominator and does not claim a token, quality, Compact, or default
  reasoning benefit.
- Remaining limitations: the current evidence is recorded/focused and ready-only,
  not a new real mutation matrix. A future matrix must use a new run root and
  receipt denominator, freeze Compact/projection/budget/permission/validation,
  and keep any new credential setup in a separately reviewed phase.

### Phase H8-R2AH reasoning matrix restart

- Observed objective: after H8-R2AG, verify in the complete architecture that a
  DeepSeek provider-default tool continuation without/with reasoning content can
  complete the same cross-file mutation task, without changing Compact, budget,
  permission, projection, fixture, or validation factors.
- Implemented gate: added a new selection-driven remote runner with separate
  selection and execute roots, a fixed 3-pair/6-arm interleaved schedule, explicit
  task/fixture/budget/validation/source/provider bindings, and an immutable
  exclusion manifest for ten H8-R2AF/H8-R2AG artifacts. The runner validates each
  request route and stops on the first failed arm without retry or denominator
  repair. Two pre-selection import errors were retained as harness diagnostics;
  neither created a selection or contacted the Provider.
- Validation evidence: offline/source and ready-only gates passed with zero
  transport/mutation; independent selection verification passed. The real DeepSeek
  matrix completed **6/6 arms and 3/3 pairs**, with one scoped symbol writer and
  one exact passing pytest per arm. Independent canonical-hash, scope, API,
  sentinel, validation, route, usage, finish, and secret checks passed.
- Usage evidence: disabled route total/prompt/completion was
  `22,153/19,951/2,202` across 12 requests; provider-default was
  `26,147/22,300/3,847` across 12 requests, with 1,725 known reasoning tokens.
  Disabled reasoning remained typed unknown rather than zero. Provider-default
  therefore used +18.03% total, +11.77% prompt, and +74.70% completion tokens in
  this fixed task, with no request-count reduction.
- Decision: **PASS for continuation correctness and full-matrix safety; no global
  reasoning benefit**. Provider-default no longer blocks the task, but this sample
  is evidence of higher reasoning/completion cost, not an optimization. Compact,
  dynamic budget, and cross-provider defaults remain separate experiments.
- Remaining limitations: one DeepSeek model/profile and one mutation fixture were
  tested. Reasoning tokens for disabled responses are unknown. The next experiment
  must isolate budget/value routing or another provider; it must not mix Compact or
  permission changes into this conclusion.

### Phase H8-R2AI-0 typed budget contract remote sync and gate

- Observed failure: the local typed outcome-feedback implementation was ahead of the
  remote staged workspace, while the remote worktree also contained unrelated dirty
  Provider/harness changes. A whole-file copy would have overwritten user-owned work.
  The first hand-written remote patch was rejected by `git apply` as corrupt and made
  no remote change.
- Implemented fix: created a non-reused remote backup, generated a minimal patch from
  isolated copies, and applied only the typed `ToolEventCompletionOutcome` contract,
  budget observation hook, Provider response classification, and focused tests. No
  reset, checkout, staged commit, fixture, permission, Compact, transport, or
  credential change was performed.
- Validation evidence: remote metadata, Provider budget/outcome, enhancement-budget,
  execution-budget, and Provider execution focused gates passed **35 tests** in total;
  compileall and target diff-check exited 0. Backup hashes matched all five pre-sync
  target files. No selection/execute root, Provider transport, or project mutation was
  created by this phase.
- Secret-scan evidence: a broad `sk-` regex was intentionally rejected as too noisy
  after finding 78 synthetic historical tokens. A redacted hash-intersection check
  found zero overlap between the configured `.env` token and Code/docs/experiments;
  no configured credential was serialized into source, receipt, log, or patch.
- Decision: **PASS for remote contract synchronization and offline gate**. H8-R2AI-1
  recorded replay is now authorized; real budget/value diagnostics remain blocked until
  replay proves that typed budget facts, cap-hit, usage and recovery evidence are bound
  correctly.
- Remaining limitations: the remote worktree remains dirty by design; the broad secret
  scanner needs a fixture-aware calibration, and this phase makes no reasoning or token
  benefit claim.

### Phase H8-R2AI-1 typed budget recorded replay

- Observed failure: the first v1 recorded replay stopped before writing an arm receipt
  because a Pydantic `str` enum was treated as an object with `.value`. The v1 selection
  and empty replay root remain immutable and are explicitly excluded; no result was
  counted or retried.
- Implemented fix: v2 freezes a new A/B/C selection, binds H8-R2AH task/fixture/budget/
  validation/source baseline hashes, excludes all prior H8-R2AH and failed-v1 artifacts,
  records full redacted response wire facts and budget before/after state, and writes a
  stopped result on future replay exceptions. The scope is explicitly classifier/budget
  signal replay, not a complete Provider tool-loop continuation.
- Validation evidence: remote ready-only created a new selection with execute and replay
  roots absent. The v2 replay passed all three arms; A/B requested limits were
  `750,750,800,600`, C was `750,950,800,800`; truncation/empty feedback added one
  bounded `200` step only in C, while unknown usage remained unknown. The independent
  verifier recomputed hashes, wire facts, finish/cap, budget reconciliation and all-zero
  side-effect counters. Selection, campaign, and arm hashes are recorded in the phase
  result.
- Decision: **PASS for typed budget decision-value replay**. H8-R2AI-2 may now run a
  fresh real diagnostic, but this phase makes no task-quality, request-count, or token
  benefit claim and does not cover `no_progress` history.
- Remaining limitations: the replay does not invoke `ProviderToolRoundTripRunner.run`,
  does not execute tool results, and uses four response-classifiable outcomes only.

### Phase H8-R2AI-2A/2B/2C budget evidence and local ready-only gate

- Observed failures: the provider tool runner had no typed settings switch for
  outcome feedback; route evidence existed only in an outer executor dict; a
  transport exception after reservation could lose the attempt's budget trace;
  and no-progress stops did not update the typed completion outcome. The first
  2C selection became stale after the contract patch and was retained as an
  excluded artifact rather than rewritten.
- Implemented fixes: added the default-off typed
  `OPENPILOT_PROVIDER_TOOL_COMPLETION_OUTCOME_FEEDBACK_ENABLED` setting and
  propagated it for every budget profile; added strict `ProviderBudgetDiagnostic`
  validation; preserved unknown or known error usage/finish metadata without raw
  exception text; bound feedback, reasoning complexity/mode, execution mode and
  credential-free budget contract hash to provider results; and recorded
  `no_progress` only when the bounded stop predicate fires.
- Validation evidence: provider round-trip focused suite **101 passed**;
  reasoning/metadata/provider-execution/executor suite **175 passed**;
  ready-only script tests **2 passed**; `compileall` and `git diff --check` passed.
  Fresh local selection `phase_h8r2ai_2c_selection_v2` has independent verifier
  hash `sha256:9e1873c17004b57f6e036d367c8948170514b5785fff89436f1d7dd87241427a`,
  `transport_attempted=false`, all side-effect counters zero, and absent execute
  root.
- Decision: **PASS for local 2A/2B contract and 2C zero-transport gate**. No real
  Provider, credential setup, project mutation, quality, token, or Compact benefit
  claim is made.
- Remaining limitations: the real execution target on `openpilot-air` is unresolved;
  `/Users/abaaba/worke/openpilot` does not exist and two dirty candidate workspaces
  remain unconfirmed. H8-R2AI-2D must stop until the user confirms the unique target.

### Phase H8-R2AI-1R recorded replay verifier hardening

- Observed failure: the previous independent verifier trusted several receipt-owned
  hashes and shallow counters. It did not independently recompute the dynamic budget
  chain, enforce arm order/uniqueness, constrain replay roots, or verify selection
  side effects and Provider/model wire facts.
- Implemented fix: the verifier now recalculates canonical fixture/budget/schedule
  hashes, uses `RuntimeBudgetMetadata` for every recorded decision, checks exact A/B/C
  ordering and route, validates root scope, provider wire identity, unknown usage,
  cap-hit, recovery, final counters, and complete zero-side-effect schemas. Positive
  budget-chain and negative side-effect tests were added.
- Validation evidence: verifier-focused offline tests **2 passed**; py_compile and
  diff-check passed. No replay artifact was rewritten or added to the denominator.
- Decision: **local hardening PASS; remote re-verification pending**. Existing H8-R2AI-1
  result remains a historical signal only until the hardened verifier runs against the
  remote v2 artifacts.
- Remaining limitations: the remote replay root and target workspace are not available
  in the current checkout, so no stronger remote result is claimed.

### Phase H8-R2AI-2D/2E real DeepSeek campaign and post-run verification

- Observed failures: the first real campaign stopped at B1 because DeepSeek emitted
  the invalid `command_executor.mode=standard`; the campaign harness then failed to
  seal its receipt because the execute root already existed. During the successful
  rerun, the pre-run target validator correctly rejected post-run verification once
  the execute root existed, and the old verifier falsely treated the source id
  `h8r2ai-task-v1` as a secret-shaped value.
- Implemented fixes: constrained the provider-facing command mode schema to the
  accepted enum; made campaign sealing idempotent for an existing parent but
  exclusive for the receipt file; aggregated campaign side effects from immutable
  per-arm receipts; added a post-run verifier with execute-root containment checks,
  exact successful-command evidence, and token-shaped secret detection.
- Validation evidence: local focused campaign/schema tests passed; remote focused
  campaign/schema/post-run tests passed. Fresh remote v4 selection and v3 target
  binding passed; credential-free and credentialed readiness had zero provider calls
  and zero project mutations. The new real campaign completed A1/B1/C1/A2/B2/C2
  (6/6), and the independent post-run verifier passed with 24 provider calls,
  25,634 prompt tokens, 3,657 completion tokens, 798 reasoning tokens, zero cap-hit,
  zero unknown usage, and zero failed attempts. The exact validation command passed
  once per arm and all six writes stayed within scope.
- Decision: **PASS for this controlled full-architecture real-provider campaign**.
  Context projection retained all required candidates and reduced projected context
  by approximately 77–79% in this task; provider-default reasoning increased output
  cost relative to the disabled routine route.
- Remaining limitations: there was no no-compact control, the fixture runner and
  readiness receipt are not yet fully hash-bound into the execution contract, and
  the campaign does not establish general gains for long conversations or
  `project_improvement`. The exposed test key must be rotated/revoked before any
  further real-provider run.

### Phase H8-R2AJ-0：50-turn required-constraint Compact offline gate

- Observed failure signal: `SessionConstraintState.canonical_hash` included the ordinary
  ingress cursor, so each assistant/user noise turn changed the model-facing required
  candidate identity even when no active constraint changed. This could create stale
  projections and falsely attribute cursor churn to constraint revision.
- Implemented fix: preserved `canonical_hash` as the complete checkpoint/replay snapshot
  hash and added cursor-independent `authority_hash`; model-facing SessionConstraint
  candidate IDs, source IDs, and projection state hash now use `authority_hash`. The AJ
  fixture now drives the real `SessionIngress.open_turn → confirm_proposal` lifecycle,
  checks noise/revision/revoke transitions, and includes raw/Compact/negative arms.
- Validation evidence: AJ offline receipt passed with 50 turns, required constraint kept
  in both arms, Compact lineage=50, negative typed-constraint recall=0, and zero Provider,
  network, project, or memory side effects. Focused session/context/AJ suite **53 passed**;
  compileall and diff-check passed. Receipt SHA-256 is recorded in
  `PHASE_H8R2AJ_OFFLINE_RESULT.md`.
- Decision: **PASS for AJ-0 offline contract gate**. This authorizes only construction of
  a fresh AJ-1 source-bound read-only selection and zero-transport readiness; no real
  Provider or token/quality claim is made.
- Remaining limitations: replay/request-integrity guards still use the complete snapshot
  hash by design; the new `openpilot-air` AJ copy and read-only readiness have not yet
  been created, and the previously configured remote `.env` belongs to a different
  mutation lane and must not be reused as AJ evidence.

### Phase H8-R2AJ-1：source-bound read-only selection/readiness

- Observed setup gap: the canonical H0 workspace was an older dirty snapshot with an
  existing `.env` and no AJ artifacts; reusing it would mix a mutation-lane credential and
  stale source. The first generic target-binding scan also treated ordinary `task-` IDs as
  secret-shaped `sk-` values.
- Implemented fix: created fresh source/target copies on `openpilot-air`, excluded `.env`,
  `.venv` contents, keys and historical runs, froze an AJ selection with a 50-turn
  authority/snapshot/turn contract, and tightened secret-shaped scanning to require a
  realistic token length. AJ readiness enforces `real_read_only`, projection=true,
  mutation=false, disabled reasoning and file_reader-only scope.
- Validation evidence: source-sync, target-bound selection and credential-free readiness
  all passed with zero Provider/network/project/memory/writer/command/verification side
  effects and absent execute root. Hashes and remote paths are recorded in
  `PHASE_H8R2AJ_1_SOURCE_BOUND_READ_ONLY_RESULT.md`. No key was injected and no Provider
  request was made.
- Decision: **PASS for AJ-1 readiness gate**. The source/target is now eligible for a
  process-only credentialed readiness check; real R1 remains blocked until that check and
  the dedicated read-only runner evidence gate pass.
- Remaining limitations: the AJ real runner/verifier still needs its offline contract gate;
  no provider quality, usage, or Compact benefit claim exists yet. H0 `.env` remains
  excluded and must not be used as AJ evidence.

### Phase H8-R2AJ-2：credentialed DeepSeek 50-turn raw/Compact read-only pair

- Observed failure: the first credentialed R1 request reached the provider but the runner
  failed to seal its receipt because the previous receipt hash was included in the new
  canonical-hash input. The failed attempt was excluded from the result denominator.
- Implemented fix: added a process-only DeepSeek wrapper with explicit `_env_file=None`,
  stdin/environment-only credential handling, verified tokenizer injection, strong target
  binding validation, provider/usage/finish/scope/constraint/target gates, body/secret
  redaction, and R1-failure stop-before-K1 semantics. Added the K1 arm only after the
  wrapper contract tests passed; fixed receipt hashing to exclude any prior hash.
- Validation evidence: AJ runner/provider/readiness focused suite **11 passed**. On the
  final target-bound selection, R1 and K1 each completed 3 provider calls with complete
  usage and finish telemetry, disabled reasoning, `file_reader`-only scope, required
  constraint retention, Compact lineage=50, unchanged target hash, and zero mutation,
  writer, command, verification, retry or fallback effects. Prompt tokens fell
  `7,090→3,935` (44.50%); total tokens fell `7,621→4,493` (41.05%); calls stayed `3/3`.
  Receipts and hashes are recorded in `PHASE_H8R2AJ_2_REAL_READ_ONLY_PAIR_RESULT.md`.
- Decision: **PASS for this scoped DeepSeek read-only pair; Compact remains provider/task
  scoped and feature-flagged.** No call-count reduction, cross-provider, default-on, or
  independent semantic-answer-equivalence claim is made because final response text was
  intentionally not persisted.
- Remaining limitations: add a response-quality envelope containing only fact-coverage
  booleans and response hashes, then repeat across multiple tasks/providers before making
  a stronger semantic-quality or rollout claim.

### Phase H8-R2AJ-3：脱敏 response-quality envelope 与真实复验

- Observed gap: AJ-2 deliberately omitted final response text, but its receipt therefore
  could not expose a compact, independently checkable semantic-quality signal.
- Implemented fix: added a fixed `h8r2aj-r1-quality-v1` envelope with semantic status,
  boolean fact coverage, request/response/tool-event/candidate evidence IDs, and
  response hashes. Content, reasoning text, tool arguments, prompts, and source bodies
  are hashed in memory only and are never written to receipt or campaign files.
- Validation evidence: accepted, failed-stop, non-boolean coverage, response-hash mismatch,
  body redaction and secret redaction contracts are covered by the offline AJ suite;
  provider/readiness/runner/fixture focused suite **18 passed** and py_compile passed. Because
  the runner is source-bound, the `openpilot-air` source/target selection and readiness were
  regenerated. Credentialed readiness had zero Provider calls and zero mutation; R1 and K1
  each completed 3 DeepSeek calls with `semantic_status=accepted`, 8/8 fact-coverage true,
  required constraint/Compact lineage retained, and zero mutation/writer/command/verification/
  retry/fallback effects. Prompt was `7,090→3,935`; total was `7,621→4,691`; calls stayed
  `3→3`.
- Decision: **PASS for the envelope contract and this scoped real recheck**. The key was
  injected only through a closed-echo one-time stdin process environment and was not written
  to `.env`, argv, logs or receipts. Old AJ-2 receipts are not retrofitted.
- Remaining limitations: the envelope proves typed fact coverage and response-hash linkage,
  not full-answer semantic equivalence. Completion expanded `531→756` in this pair, so the
  Compact signal is prompt/total descriptive only; multi-task and cross-provider confirmation
  remain required before broader quality or rollout claims.

### Phase H8-R2AK：多任务 DeepSeek Compact confirmation

- Observed failure: extending the single `cli.py` task exposed two evidence-contract issues.
  The readiness/runner path had `cli.py` hard-coded, and the first context-assembly quality
  oracle required the literal `def assemble(`. The provider supplied the semantic symbol but
  not that declaration spelling. One early implementation raised before sealing a receipt.
- Implemented fix: generalized the read-only task contract to one declared read file, added
  per-task source-bound selections and task-specific answer-fact contracts, added identifier-
  boundary matching (`assemble` does not match `assemble_candidates`), and changed answer-fact
  failure to a sealed `status=failed` receipt that stops K1. Added multi-task selection/runner,
  quality coverage tests and stale-contract diagnostics; failed v4/v5 diagnostics are excluded.
- Validation evidence: local multi-task/provider focused suite **16 passed**. On `openpilot-air`,
  final cli, reasoning-policy and context-assembly selections each passed credential-free and
  credentialed readiness; all six final R/K arms passed `semantic_status=accepted`, 8/8
  execution facts and 4/4 answer facts, required constraint/Compact lineage=50, disabled
  reasoning, file-reader-only scope, unchanged target and zero mutation/writer/command/
  verification/retry/fallback effects. Aggregate prompt `22,008→12,555` (−42.95%), total
  `23,779→14,474` (−39.13%), completion `1,771→1,919` (+8.36%), calls `9→9`.
- Decision: **PASS for three DeepSeek read-only task pairs; Compact remains feature-flagged.**
  The key was injected only through a closed-echo one-time stdin process environment and was
  not written to `.env`, argv, logs or receipts.
- Remaining limitations: answer-fact coverage is a typed lexical oracle, not full natural-
  language semantic equivalence; calls did not fall and completion rose. The result is one
  provider, three tasks and one 50-turn fixture; cross-provider confirmation and a stronger
  semantic oracle remain open.

### Phase H8-R2AL：OpenAI provider-neutral readiness

- Observed gap: DeepSeek AJ-4 evidence still could not support a cross-provider claim; the
  current route lacked a fresh OpenAI readiness artifact tied to the new experiment path.
- Implemented fix: added an explicit OpenAI readiness gate using only the provider-scoped
  `OPENPILOT_OPENAI_API_KEY`/`OPENAI_API_KEY`, `openai-chat-known:v1`, exact local tiktoken
  counting, disabled reasoning and `real_read_only` controls. It never reuses the DeepSeek key.
- Validation evidence: local readiness contract suite **3 passed**. On `openpilot-air`, readiness
  resolved OpenAI `gpt-4o-mini`, `tiktoken:o200k_base`, and the explicit profile; status was
  `typed_blocked` only on `missing_credentials`, with provider/network/project/memory/writer/
  command/verification side effects all zero.
- Decision: **Readiness gate PASS with typed external block**. No OpenAI transport was attempted;
  no OpenAI quality, Compact, token or mutation result is claimed.
- Remaining limitations: an OpenAI-scoped credential is required before the first canary. Once
  supplied, run one disabled-reasoning raw/Compact read-only pair before any multi-task matrix.

### Phase H8-R2AM：typed answer-fact 与 source-grounding 质量契约

- Observed failure: H8-R2AK 的 lexical answer-fact coverage 没有确认回答事实确实来自声明
  源文件；第一轮新 runner 还暴露了 `def symbol(` identifier 模式在参数名紧跟开括号时
  被边界 matcher 误判的问题。K1 也可被直接调用，缺少 R1 admission。
- Implemented fix: answer facts now use typed `fact_id`/`match_type`/`patterns`/
  `source_patterns`; regex 具备编译和长度边界；grounding 要求成功、精确路径的
  `file_reader` 事件和 source locator/body 命中；transport 前校验 task/answer-fact 和
  initial-context candidate projection contract；K1 只接受同 selection 下已通过且哈希有效
  的 R1。receipt 仅保留 ID、布尔值、contract hash、usage/finish/evidence 与 response hash，
  不保存 pattern/source body。
- Validation evidence: 本地与 `openpilot-air` focused suite 各 **18 passed**，compileall
  与 diff-check 通过。最终 candidate-projection-hardened source-bound selection/readiness
  后，`openpilot-air` 三个 DeepSeek read-only task pairs 全部 R1/K1 通过并由独立 verifier
  重验；每臂 3 calls，aggregate prompt `22,010→12,552`（−42.97%），total
  `23,785→14,342`（−39.70%），completion `1,775→1,790`（+0.85%），calls `9→9`；
  grounding 与 answer coverage 皆 3×4/4。
- Secret/environment evidence: key 只通过一次性 stdin 进程环境注入 `openpilot-air`，未
  写入 `.env`、argv、日志、selection、receipt 或文档；远端扫描无 secret-shaped value，
  无 live key-bearing process。source 使用新建 Python 3.12 venv，target 使用并验证其已有
  的 Python 3.12 venv symlink；setup side effect 与 provider transport 分离。
- Decision: **PASS for typed evidence and this scoped DeepSeek confirmation; Compact remains
  feature-flagged and provider/task scoped.**
- Remaining limitations: typed lexical/source grounding 不是完整语义等价性；调用次数未降、
  completion 增加；OpenAI credential 仍缺失，跨 provider 质量/token claim 未完成。

### Phase H8-R2AN-C：DeepSeek credential rotation 与 task-candidate rebinding

- Observed failure: the first R1 attempt with the newly supplied DeepSeek credential stopped
  before transport on `initial context candidates do not match the selection`. The reused
  target-bound selection contained the stale task source id `h8r2ak-cli-symbols:task-v1`,
  while the current fixture used `h8r2aj-cli-symbols:task-v1`. After regenerating a selection,
  the runner still defaulted to the generic fixture when a multi-task candidate view was not
  explicitly passed, so the admission gate exposed a second contract mismatch.
- Implemented fix: added `_selection_candidates()` to the real provider runner. A typed
  multi-task `task_id` now selects the task-specific candidate view and the exact same view is
  passed into the production executor; legacy selections without `task_id` retain the generic
  fixture. Added regression coverage for task-specific source ids and ran the focused suite
  before remote execution.
- Validation evidence: local and `openpilot-air` focused suites **19 passed**. Fresh target
  selection hash is `sha256:eaad65b539297ce41719f7474f46667b154ab1de48ad3e9230aff9d9ec769841`;
  credentialed readiness had zero Provider calls; R1 and K1 each passed 3 real DeepSeek calls.
  Typed answer/source grounding was 4/4 on both arms, execution facts 8/8, target unchanged,
  file_reader-only scope, and zero writer/command/verification/retry/fallback effects. Prompt
  `7,093→3,931`, total `7,728→4,547`, completion `635→616`, calls `3→3`.
- Secret/environment evidence: the key was injected only through one-time stdin process
  environment on `openpilot-air`; receipts and selection contain no credential value, and the
  old drift artifacts remain excluded rather than rewritten.
- Decision: **PASS for this scoped DeepSeek raw/Compact pair and candidate admission repair.**
  Compact remains feature-flagged and provider/task scoped.
- Remaining limitations: this does not unlock OpenAI; the supplied key is treated as DeepSeek
  and cannot be reused for OpenAI. Cross-provider wire/quality evidence, full semantic
  equivalence and call-count benefit remain open.

### Phase H8-R2AN-AN-A/B：OpenAI provider-neutral offline adapter

- Observed failure: the existing `openai-chat-known:v1` adapter rendered
  `reasoning_effort=none` for every disabled request, although `gpt-4o-mini` may reject that
  field. The provider-tool loop also called a DeepSeek-named round-trip helper for all
  profiles, making OpenAI continuation semantics depend on the wrong provider abstraction.
- Implemented fix: added typed `openai-chat-no-reasoning-known:v1` profile and adapter. It
  omits reasoning transport fields and rejects explicit enabled reasoning; the old OpenAI
  profile remains unchanged and opt-in. Added provider-neutral `append_tool_round_trip()`;
  the DeepSeek wrapper retains its reasoning-content requirement, while the execution loop
  uses the generic call/result identity contract. OpenAI readiness now binds the new profile,
  does not reuse the DeepSeek environment, and recognizes hyphenated `sk-proj-…` secret shapes.
- Validation evidence: synthetic `gpt-4o-mini` raw/Compact pair passed typed answer/source
  grounding, call-ID round-trip, Compact candidate view, and zero Provider/project side effects
  (`sha256:51f46f89adeeb892a858e4b98fcce9d1e050e4bb0defce071055c0ceb481514`). The same
  code snapshot was rerun on `openpilot-air` with campaign hash
  `sha256:92b923a056b2a94e0653260443a265510328da003ec9afaf9a3d6a2d1fdcbac6`. Focused suite
  **162 passed**; experiment suite **68 passed**; `compileall` and `git diff --check` passed.
  Core suite reached **1250 passed**; 12 legacy Phase28–31 tests still fail collection because
  their deleted script dependencies are absent from the user-dirty worktree and were not
  restored.
- Decision: **PASS for offline provider-neutral OpenAI adapter contract.** No OpenAI network
  request, quality claim, token claim or rollout was made.
- Remaining limitations: an OpenAI-specific credential is still required for readiness and a
  real R1→K1 pair; o-series `max_completion_tokens` is intentionally outside this gpt-4o-mini
  lane and remains unimplemented.

### Phase H8-R2AN：typed provider-lane identity

- Observed gap: OpenAI readiness still assembled provider identity and credential names inside
  one experiment script. A caller could change endpoint/model/profile or accidentally make the
  OpenAI lane read `OPENPILOT_LLM_API_KEY` without a single typed contract detecting the drift.
- Implemented fix: added immutable `ProviderLane` constants for OpenAI `gpt-4o-mini` and
  DeepSeek v4 flash, provider-scoped credential lookup, lane-derived `LLMSettings`, and exact
  settings/tokenizer validators. OpenAI readiness and synthetic canary now derive from the lane
  and persist a non-secret `lane_id`; tamper and DeepSeek-only environment tests fail closed.
- Validation evidence: lane/readiness/adapter/provider-tool/synthetic suite **166 passed**
  locally and on `openpilot-air`; remote synthetic campaign hash is
  `sha256:c682bc4628bf5034ff89d54a3ac1e88bc6db9c0f47a0ee289fabe2b4a64d0b74`. Missing credentials
  and tokenizer/profile/endpoint/model drift remain zero-transport. The
  same synthetic OpenAI raw/Compact contract remains accepted after lane integration.
- Decision: **PASS for typed identity and pre-transport admission.** No real OpenAI request or
  cross-provider quality claim was made.
- Remaining limitations: OpenAI credentialed readiness and real R1→K1 still require a
  provider-specific credential; historical receipts are not retroactively rebound to lanes.

### Phase H8-R2AN-C2：`openpilot-air` credential admission 与 selection 重绑定复验

- Observed failure: the newly supplied DeepSeek credential was safely accepted by the
  zero-transport readiness gate, but the first real-arm attempt stopped before Provider
  transport because the reused target-bound selection had stale hashes for
  `provider_tool_roundtrip.py` and `reasoning.py`. A second pre-transport attempt exposed a
  harness contract issue: the low-level single-arm CLI did not expose the selection's typed
  `required_answer_facts`, so it rejected an otherwise valid multi-task selection.
- Implemented fix: generated a fresh source selection from the current source snapshot and
  target-bound it to the current `openpilot-air` target; retained fail-closed binding checks and
  used the existing multi-task wrapper, which supplies typed answer facts and task-specific
  candidate projections from the selection. No permission, scope, or credential boundary was
  relaxed; the stale and pre-transport failure artifacts remain excluded.
- Validation evidence: remote host `abaabadeMacBook-Air.local`; source selection hash
  `sha256:1381995c42a28c3e11e1c3c418ecb16a87504548958fc8ac549582ccd59c132c`; target selection
  hash `sha256:edec77d3cb57cf1f792b7f7bee4e924898ec4a2d6823b6eb537db3a39735c931`; readiness hash
  `sha256:894bc9ba812d0cfa95cb96514920da3280a796ea6775ad94ec596d0c9f8bbfc3`; R1 and K1 both
  passed 3 real DeepSeek calls. R1/K1 prompt usage was `7,092→3,933`, total
  `7,703→4,538`, completion `611→605`; both arms had complete usage/finish telemetry,
  semantic acceptance, 8/8 execution facts, 4/4 answer/source-grounding facts, file-reader-only
  scope, unchanged target, and zero writer/command/verification/retry/fallback effects.
- Secret/environment evidence: the key was injected only through remote stdin into a transient
  process environment; readiness and campaign receipts contain no key value, and no `.env` or
  live key-bearing process remained. This key is recorded as DeepSeek-scoped only and was not
  reused for OpenAI.
- Decision: **PASS for this scoped DeepSeek raw/Compact recheck.** Compact remains
  feature-flagged and provider/task scoped; no OpenAI, cross-provider, call-count, or default-on
  conclusion is made.
- Remaining limitations: the low-level provider CLI still requires a caller that supplies typed
  answer facts for multi-task selections; the existing wrapper is the admitted path. A future
  CLI cleanup should expose that contract explicitly with offline regression coverage rather than
  relying on an empty default. OpenAI credentialed R1/K1 remains pending.

### Phase H8-R2AO-1：OpenAI tokenizer/profile readiness 修复

- Observed failure: current `openpilot-air` OpenAI readiness reported both
  `tokenizer_unavailable` and `missing_credentials`, although the target Python 3.12 environment
  imported `tiktoken`. The OpenAI lane had intentionally moved to
  `openai-chat-no-reasoning-known`, while `ProviderTokenCounter` still admitted only the legacy
  `openai-chat-known` profile.
- Implemented fix: decoupled tokenizer capability admission from the reasoning transport profile
  by allowing both known OpenAI chat profile IDs through the exact `tiktoken` branch. Unknown
  profile/model behavior remains fail-closed; no endpoint, model, credential scope, reasoning
  payload, budget, or Compact behavior changed.
- Validation evidence: test-first regression failed before the fix and passed afterward; local
  tokenizer/reasoning/provider/lane suite passed **203 tests**, and the synced `openpilot-air`
  target suite passed **175 tests**. Current remote readiness hash is
  `sha256:6776bf4186ddd2738bcd8d805d62e2c311adcff7a9062711e1ea342a6e2fcc51`; tokenizer is now
  `available=true` with `tiktoken:o200k_base`, status is `typed_blocked` only on
  `missing_credentials`, and provider/network/project/memory/writer/command/verification side
  effects are all zero.
- Secret/environment evidence: the repair and readiness recheck used no OpenAI or DeepSeek key;
  remote source/target backups were created before sync, and no credential was serialized.
- Decision: **PASS for tokenizer/profile pre-transport repair.** This removes an environment
  false blocker but does not authorize an OpenAI request or cross-provider conclusion.
- Remaining limitations: an OpenAI-specific credential is still required for credentialed
  readiness and the real disabled-reasoning R1→K1 canary. DeepSeek credentials remain scoped to
  the DeepSeek lane and cannot be reused.

### Phase H8-R2AP：OpenAI provider-neutral real canary runner

- Observed failure: the existing real-provider wrapper could not safely admit an OpenAI arm;
  receipt validation and R1→K1 admission assumed DeepSeek endpoint/model/profile, and the
  readiness CLI raised on a missing OpenAI key instead of sealing an auditable blocker.
- Implemented fix: added explicit provider/schema/campaign expectations to the shared runner,
  added `stage_h8r2ap_openai_real_provider.py` as a thin OpenAI lane adapter, and added a
  zero-transport `typed_blocked/missing_credentials` readiness receipt. Shared context projection,
  file-reader-only scope, typed quality/grounding, side-effect gates and K1 admission remain one
  implementation; no fallback to DeepSeek is possible.
- Validation evidence: local focused suite **22 passed**, extended context/provider suite
  **59 passed**, and synced `openpilot-air` focused suite **20 passed**. Remote readiness is
  `typed_blocked` with exact `tiktoken:o200k_base`, `provider_calls=0`, and hash
  `sha256:9b48e9882559b221713e32e45438422a840bd8433e0fce3db1c2b9141ea71a7e`.
- Secret/environment evidence: the newly supplied `sk-...` value was not written to remote
  `.env`, shell profile, argv, logs or receipts and was not used for the OpenAI lane; no provider
  transport occurred in this phase.
- DeepSeek lane note: the same value was admitted only through one-time stdin on `openpilot-air`
  with explicit DeepSeek process settings. Credentialed readiness passed with zero transport at
  `phase_h8r2ap_deepseek_readiness_new_key_v3/readiness.json` (hash
  `sha256:b37d22052024b0919da2b8be00f35eafb8581f8fb9510af6a6b256621a7f0c80`); only a credential
  fingerprint was serialized. This is separate DeepSeek evidence, not OpenAI evidence.
- Decision: **PASS for provider-neutral runner and preflight only; STOP before real OpenAI R1/K1**
  because the OpenAI credential is absent.
- Remaining limitations: no real OpenAI usage/finish/tool-round-trip/quality or Compact token
  evidence; obtain an OpenAI-scoped key before R1, then require a passed sealed R1 before K1.

### Phase H8-R2AQ：DeepSeek provider-neutral runner 回归 pair

- Observed failure: before the real run, the CLI/API path did not automatically pass a
  multi-task selection's typed `quality_contract.answer_facts`; a valid selection would stop
  before Provider transport with an answer-fact contract mismatch.
- Implemented fix: when no explicit override is supplied, the shared runner derives effective
  answer facts from the already validated selection and still compares the canonical contract
  hash; explicit drift remains fail-closed.
- Validation evidence: `openpilot-air` focused runner/readiness suite **22 passed**. Fresh
  selection/readiness and real R1→K1 both passed. R1 had 3 calls, prompt/completion/total
  `7,092/728/7,820`; K1 had 3 calls, `3,932/528/4,460`. All four typed answer facts and
  source grounding passed; required constraint, 50/50 Compact lineage, exact read path,
  usage/finish evidence and zero mutation/writer/command/verification/retry/fallback gates
  passed. Receipts were copied to the local remote-evidence archive.
- Decision: **PASS for the scoped DeepSeek regression pair.** The observed token reduction is
  descriptive evidence for this task/provider stratum; calls stayed `3→3` and no global or
  cross-provider conclusion is allowed.
- Remaining limitations: OpenAI real R1/K1 still lacks an OpenAI credential; mutation safety,
  default-on rollout and broader task/provider replication remain gated.
- Full-suite note: the complete experiment directory suite passed **76 tests**. The repository
  `Code/tests` collection remains unavailable in this dirty worktree because an existing deletion
  of `experiments/full_architecture_context_observation/stage25_budget_profile_task_matrix.py`
  makes `test_phase28_quality.py` raise `FileNotFoundError`; the deleted user file was not restored.

### Phase H8-R2AR：Compact 动态 summary budget

- Observed gap: the strict LLM rolling-summary adapter and DeepSeek Phase33B evidence already
  existed, but `MemoryContextBuilder` always passed a fixed summary cap. Remaining prompt space,
  required reserves and recent suffix could not shrink the derived summary slot.
- Implemented fix: reused `calculate_summary_budget` to derive a dynamic cap from exact provider
  tokenizer evidence, preserving the static cap as a hard ceiling. The calculation reserves
  required context, the latest two dialog messages and a 64-token response-schema slot. A zero
  slot skips the summary factory and leaves deterministic observation compaction authoritative.
- Validation evidence: context/compaction/governance suite **135 passed**; full experiment
  directory suite **76 passed**. Dynamic integration observed cap `80→11`; exhausted budget
  produced zero factory calls and deterministic fallback. Default rolling-summary flag remains off.
- Decision: **PASS for offline dynamic-budget semantics**. This improves budget discipline but
  does not claim generated-summary semantic quality or default-on rollout.
- Remaining limitations: production autonomous runtime still needs a separately planned,
  feature-flagged provider factory/shadow before dynamic LLM summaries are exercised end to end.

### Phase H8-R2AS：Provider summary factory

- Observed gap: the validated `RollingSummaryAdapter` and dynamic summary budget existed, but
  the complete autonomous runtime had no provider-neutral factory or typed runtime gate. A
  generated summary could not yet be exercised without bypassing the existing context authority
  and fallback boundary.
- Implemented fix: added a strict `memory_compression` JSON factory with no tools, temperature
  zero, disabled reasoning, source IDs/fingerprint, and provider attempt evidence. Added
  default-off typed `LLMSettings` flags and injected the factory plus existing adapter into
  `IntelligentAutopilot` only when explicitly enabled. Required constraints, user dialog,
  permissions, write targets, and validation commands remain outside the replaceable source
  segment.
- Validation evidence: factory/settings/context focused suite **69 passed**; extended
  context/session/reasoning/provider suite **332 passed**; complete experiment directory
  suite **76 passed**; `git diff --check` passed. Tests cover valid parsed JSON, malformed/empty
  payload, unknown usage, `length` finish, no-provider-at-zero-cap, disabled reasoning, no
  tools, source binding, typed flags, and default-off runtime construction.
- Decision: **PASS for feature-flagged offline runtime wiring**. Deterministic observation
  compaction remains the default authority and all provider failures fall back to the source
  view.
- Remaining limitations: no real summary shadow or semantic-quality/token-benefit claim yet;
  run a separate read-only DeepSeek shadow on `openpilot-air` only after the offline gate. The
  supplied DeepSeek credential remains one-time stdin-only and must not be persisted; OpenAI
  still requires an OpenAI-scoped key.

### Phase H8-R2AT：DeepSeek provider summary shadow

- Observed question: after the feature-flagged factory was wired, it was still unknown whether
  a real provider summary would pass the strict schema/usage/source gates and whether the
  production `MemoryContextBuilder` would actually select it without displacing required or
  recent dialog context.
- Implemented experiment: added a body-free, source-bound shadow harness. It invokes the
  production builder with a 50-turn `SessionIngress` fixture, `max_prompt_chars=7000`, exact
  DeepSeek tokenizer, dynamic summary cap `256`, no tools, disabled reasoning, zero transport
  retries, and an observation-only compaction sink returning `None`. The harness records each
  factory source fingerprint/cap, request/response hashes, usage/finish, adapter result and
  builder selection outcome; it never stores prompt/source/summary bodies.
- Validation evidence: local experiment suite **79 passed**, `git diff --check` passed; remote
  zero-transport readiness confirmed `abaabadeMacBook-Air.local`, selection hash
  `sha256:44337cccd493f06dc5df4d761e1817e1ec40a0bdbe3fd38d18b67613920bc514`, and exact
  DeepSeek tokenizer availability. R0 builder baseline receipt hash is
  `sha256:75d6ff510c9105a4d9422a43351fb4f8357fa63c5f872dbe19c0bc6db781d74d`; S1 summary
  observation receipt hash is `sha256:33a62a2c79ad18d1062f4e69883c63257334b4f0d93fbbd6544912fc2484c119`.
  S1 made one provider call with prompt/completion/total `2,522/114/2,636`, finish `stop`,
  `max_retries=1`; adapter accepted a 504-character/114-token summary. The builder sink still
  observed the deterministic record, final prompt remained 7,000 chars, and no artifact or
  generated summary entered the prompt.
- Decision: **PASS for real provider safety and fail-closed selection boundary; no benefit claim**.
  The summary was accepted as a derived candidate but not selected by the builder's atomic
  trial, which is safe and currently yields no token reduction.
- Remaining limitations: builder telemetry does not yet distinguish summary-fit failure from
  recent-suffix displacement or another trial decision. Add typed selection/fallback reason
  evidence and calibrate schema/dynamic cap before any default-on, real tool-task, mutation, or
  cross-provider experiment. The DeepSeek key was one-time stdin-only and was not persisted.

### Phase H8-R2AU：Compact attempt/selection telemetry（offline implementation）

- Observed failure: H8-R2AT receipts could show a provider-accepted summary and a deterministic
  sink observation, but could not distinguish atomic fit rejection, recent-suffix displacement,
  observation-only sink return, or sink exception. The builder also dropped its local trial
  outcome when the sink returned `None`.
- Implemented fix: extended the existing nested `ContextCompactionAttempt` value with optional,
  body-free source/summary sizes and fingerprints, typed trial status/decision, displaced
  candidate IDs, adapter fallback reason, selection outcome, artifact binding, and prompt-use
  evidence. `_compact_dialog_prefix()` now records each provider and deterministic attempt while
  keeping deterministic source authority and strict sink failure semantics unchanged. A sink
  `None` is recorded as `generated_observed_only`; an exception remains
  `artifact_sink_failure`/fail-closed in strict mode.
- Validation evidence: context/compaction/governance/checkpoint/metadata focused suite **199
  passed**; experiment H8-R2AT contract tests **3 passed**; py_compile and `git diff --check`
  passed. Local mock replay produced explicit `generated_recent_suffix_displaced` with displaced
  IDs followed by `generated_observed_only`, with no summary/source body in the serialized value.
- Contract boundary: `ContextCompactionRecord`/`ContextCompactionBinding` remain the only compact
  authority; telemetry is optional, excluded from request identity, and old receipts/checkpoints
  remain readable. Default rolling summary remains off and no provider/task/mutation claim is made.
- Remaining limitation: run the source-bound AU-3R `openpilot-air` zero-transport/read-only
  DeepSeek shadow with the supplied one-time credential before using telemetry to calibrate
  summary schema or rollout.

### Phase H8-R2AU：Compact attempt/selection telemetry hardening

- Additional observed failures: provider factory exceptions dropped usage/finish evidence and
  did not preserve the requested dynamic summary cap; partial, negative, boolean, or malformed
  usage could either be misreported as complete or crash while appending diagnostics.
- Implemented fix: provider failure evidence is normalized and carried into the body-free
  `ContextCompactionAttempt`; cap-hit finish reasons map to a typed truncation fallback. Strict
  non-negative integer usage and accepted-provider evidence gates are enforced in the adapter,
  builder, and metadata contract. Invalid evidence remains a deterministic fallback and cannot
  widen authority or crash non-strict assembly.
- Validation evidence: context/metadata/compaction/governance/checkpoint/session focused suite
  **222 passed**; H8 replay suite **8 passed**; compileall and `git diff --check` passed.
- Remaining limitations: full `Code/tests` is blocked by pre-existing missing stage25/28/30/31
  experiment modules in the dirty tree; AU-3R source-bound remote read-only shadow is pending.
  No provider,
  token, quality, or rollout benefit claim is made.

### Phase H8-R2AU-3R：AU-3 remote read-only shadow after source-binding repair

- Observed failure and repair: AU-3's target-bound selection became invalid after source
  synchronization because `stage_h8r2ak_multi_task_selection.py` changed. AU-3R created a fresh
  target-bound selection from the current source snapshot and passed source fingerprint,
  candidate-ID, required/recent-suffix, and code-manifest checks before transport. The full
  selection retains the dialog snapshot; the builder received the validated actual 22-candidate
  compaction subset, not an unverified fixture.
- Validation evidence: local source-binding focused tests **10 passed** and the synced
  `openpilot-air` focused suite **15 passed**. The AJ readiness receipt (file SHA-256
  `a413c7c185f71b55e83f0028da1793e0448eaa0035e0c281c7a32fa0640d47c3`) has readiness hash
  `sha256:515a6eca2b8a3db8b6bebc4bb651767e5147dd4ded99049baf2fddfce0460e44`; credentialed
  readiness (file SHA-256 `f39eb2077c96e5fdc6a918de9c3475c69e4ad1b35252c6d92aa55e309f1737da`)
  has readiness hash `sha256:144e2a25af5fee8f9e99e9d871a78726940ef334b734d61f5c7c276e3d176fda`.
  Target selection file SHA-256 is
  `effa1643176e6e4ff9fc0e6d80f9f4250029c87cac9e7d1198ad90807d843dc7`, canonical selection
  hash `sha256:2d0a025d26d44e86565b84be5f1cbb484e1d6f4dd81c9cc1e2f46ed86669e067`. R0 file SHA-256
  `d103c54ab6a883c3ff02fa8c1204f6a3616710935860a664e2ef2793f1f9b300`, canonical receipt hash
  `sha256:cfa49ee4c69176baa3d6de2b0522d0eae9db67cb2e3a6dde254b6d3a31a68782`; S1 file SHA-256
  `ccfb44f48fa81ed4db8380c5e80954299a0e0782d537a240c3960b07d61f205e`, canonical receipt hash
  `sha256:0c172d437911f4b692b1b56a54c497e5fc5db6a42912717fe2e3d1d8f63683bc`.
- Observed result: R0 made no provider call. S1 made one bounded DeepSeek summary request
  (`prompt/completion/total=2,522/113/2,635`, `finish_reason=stop`); the adapter accepted the
  derived summary, but the builder did not select it because it displaced a recent suffix.
- Implemented outcome: telemetry records
  `provider_status=accepted`, `selection_status=not_selected`,
  `selection_outcome=generated_recent_suffix_displaced`, and
  `fallback_reason=recent_suffix_displaced`; generated summary IDs were absent from the
  selected prompt, `used_in_prompt=false`, and no authority artifact was persisted. The
  deterministic follow-up records the observation sink's no-artifact result as `sink_failed`.
- Validation: both receipts have valid canonical hashes, no nested prompt/source/summary body or
  secret-shaped values, and zero project/memory/network/writer/command/verification side effects.
  The dirty-tree full gate remains blocked by missing historical stage25/28/30/31 modules; this
  does not invalidate the focused source-binding gate. AU-3R closes the safety telemetry loop
  only; no semantic-equivalence, token-benefit, call-count, or default-on claim is made.
### Phase H8-R2AU-4：Source binding 与 receipt acceptance 修复

- Observed failure: AU-3R shadow source derivation could construct the default `MemoryStore`,
  arm entry points accepted caller-supplied candidate lists without a validated binding, fixture
  state was rebuilt inside builder arms, and receipt checks did not independently bind canonical
  hash/code manifest or all nested body/secret aliases.
- Implemented fix: added inert read-only memory dependencies, `ValidatedSourceSnapshot`, fixture
  turn-ledger/candidate-contract binding, current Compact code-manifest binding, and independent
  `verify_receipt()` with typed side-effect and case-insensitive body/secret checks. All shadow arms
  now consume the same validated snapshot.
- Validation evidence: AU-3R focused **14 passed**, experiment suite **86 passed**, context/
  metadata/compaction/session focused **156 passed**, compileall and `git diff --check` passed.
- Remaining limitations: old remote selections must be regenerated because their source manifest
  predates this repair; the full `Code/tests` suite remains blocked by missing historical
  stage25/28/30/31 modules. No provider quality, token, call-count, or default-on claim is made.

### Phase H8-R2AU-5：远端当前源码重绑定与 zero-transport admission

- Observed failure: the prior AU-3R receipts were tied to an older remote source selection and
  could not prove that the repaired source-binding/receipt contract was present in the current
  execution workspace.
- Implemented execution: synced the repaired source to a new `openpilot-air` disposable workspace,
  ran the focused source-binding/provider gate, generated fresh ready-only and target-bound
  selections, and passed credential-free DeepSeek readiness with the execute root absent. A
  second disposable sync was required after the pre-arm snapshot revalidation fix; only the
  second receipt set is current.
- Validation evidence: remote focused **14 passed**, compileall passed; target selection canonical
  `sha256:2d66a183c4ae6fc68a7635ddc13001f129ffd458f76762b9e394f8f0a0493d7a`; readiness canonical
  `sha256:ab36e70f86c42d0384d5b40a94537391c1e95c01ded080e6a2436ad0aba4e78d`; provider transport,
  mutation, writer, command and verification counters all zero.
- Remaining limitations: no remote credential is currently available, so credentialed readiness
  and R0/S1 provider shadow are typed-blocked. No provider quality, token, call-count or default-on
  claim is made.

### Phase H8-R2AU-6：Credentialed paired shadow offline preflight

- Observed need: the next real shadow requires a process-only credential path; using ambient env or
  a persistent `.env` would weaken the experiment's secret boundary.
- Implemented fix: added `stage_h8r2au6_credentialed_shadow.py`, which accepts exactly one
  non-TTY stdin line, constructs fixed DeepSeek disabled-reasoning settings in memory, then runs
  deterministic R0 followed by provider-summary S1. `run_builder_summary()` now accepts explicit
  provider settings without changing its default environment behavior.
- Validation evidence: wrapper/source focused **20 passed**, experiment suite **92 passed**,
  compileall and `git diff --check` passed; tests cover empty/multi-line/TTY input and paired
  snapshot sharing.
- Remaining limitations: `openpilot-air` has no credential, so no provider request or real paired
  shadow was executed. No usage/finish, selection, quality, token or call-count claim is made.

### Phase H8-R2AU-6：Credentialed paired DeepSeek shadow

- Observed question: after the process-only credential path and fresh target binding passed, it was
  still unknown whether the real DeepSeek summary request would satisfy usage/finish/source and
  selection safety contracts.
- Implemented execution: one-time stdin credential injection ran fresh R0/S1 on the current
  target-bound selection. R0 made zero provider calls; S1 made one disabled-reasoning DeepSeek
  request and the independent verifier reloaded both receipts from the current source.
- Validation evidence: S1 usage `2522/127/2649`, `finish_reason=stop`, adapter accepted;
  `selection_outcome=generated_recent_suffix_displaced`, `selection_status=not_selected`,
  `used_in_prompt=false`, `artifact_binding=false`; canonical R0
  `sha256:afde23b5cdd924e67100872c3d539ab824faf606de5a0f5653695636efb0be49`, canonical S1
  `sha256:3efca627c93ded6463206c042692926e64d5721a6e0eca00eb1ec9c035374fd7`. All mutation,
  writer, command, verification and retry side effects were zero.
- Remaining limitations: this is a single DeepSeek builder-level safety shadow with no semantic
  equivalence, token/call-count benefit, cross-provider or default-on claim. Deterministic Compact
  remains authoritative and generated summary remains feature-flagged/observational.

### Phase H8-R2AV：多窗口 summary quality/cost 离线门禁

- Observed gap: H8-R2AU had only one real provider-summary source window. It did not yet show
  whether required constraints, recent suffix, exact answer facts and dynamic summary budget stay
  aligned as the compacted history prefix grows.
- Implemented experiment: added
  `stage_h8r2av_multi_window_summary_quality.py` and its focused tests. The harness independently
  reconstructs tight/balanced/wide source snapshots, validates a bounded structured summary with
  the production `RollingSummaryAdapter`, and runs the production `ContextAssembler` in a
  non-authoritative trial. It records source/fixture hashes, required/recent IDs, lineage,
  dynamic-cap inputs, usage/finish evidence and typed selection reasons without writing an
  artifact or calling a Provider.
- Validation evidence: H8-R2AV focused **8 passed** (window, readiness and body-free receipt
  tests); context/rolling-compaction integration
  **50 passed**; `git diff --check` passed. A production-eligibility mismatch was found before
  real transport: the first draft compacted user and assistant dialog, while
  `MemoryContextBuilder` only compacts eligible assistant sources. The harness was repaired to
  bind the same assistant-only source semantics; the regenerated three windows (4/12/24 assistant
  sources) passed required/recent retention, exact evidence grounding, lineage, summary budget and
  zero-side-effect checks. Prompt proxy reduced from `9,514` raw tokens to `9,013`, `7,529` and
  `5,298`; canonical result hash is
  `sha256:b1dde2de266f37e3659c1e57b9b27cce6fe2a5d34f8b7457cc9c21e3a561ecfd`.
- Remaining limitations: the token counter is an offline four-character proxy; provider summary
  generation cost is recorded as a diagnostic, not a billing result; no real DeepSeek request,
  semantic-equivalence claim, call-count benefit, cross-provider result, mutation safety or
  default-on authorization is established. Next gate is a fresh target-bound DeepSeek
  multi-window observational shadow with deterministic Compact still authoritative.

### Phase H8-R2AV-R：真实 DeepSeek 多窗口 shadow admission

- Implemented admission: a fresh `openpilot-air` disposable workspace was created and synced with
  current source, tests and harness only. The zero-transport readiness runner binds the three
  window selection hashes, source fingerprints, assistant-only compact lineage, fixture ledger and
  11-file code manifest to the same offline result.
- Validation evidence: remote offline/readiness tests **8 passed**; offline hash
  `sha256:b1dde2de266f37e3659c1e57b9b27cce6fe2a5d34f8b7457cc9c21e3a561ecfd`; readiness hash
  `sha256:c43d6286bcaa4332f6ddd71094a644decee343ec6d1cecda9b9a46d7dc1b3291`; transport,
  provider calls, network, project, memory, writer, command and verification counters were zero.
- Stop reason: the remote workspace has no `OPENPILOT_LLM_API_KEY` or `DEEPSEEK_API_KEY`. The
  provider shadow was correctly not started; no real usage/finish/quality/token/call-count claim
  is made. A one-time process-only credential is still required for the next gate.

### Phase H8-R2AZ：Reusable summary artifact admission

- Observed gap: H8-R2AY stabilized narrow provider summaries at 256/320 completion caps, but
  single-use summary generation remained a positive token cost. The missing question was whether a
  previously generated summary could be reused safely and whether stale source/guard drift would
  fail closed.
- Implemented experiment: added `stage_h8r2az_reusable_summary_artifact.py` and focused tests. The
  harness keeps the `ContextCompactionRecord` body in memory only, persists a body-free artifact
  receipt, checks source IDs/fingerprint, source binding hash, required IDs, recent suffix IDs,
  session constraints hash, artifact kind and integrity, then runs a real `ContextAssembler` trial.
- Validation evidence: H8-R2AZ focused **9 passed**; AV/AX/AY/AZ adjacent focused **19 passed**;
  context/rolling focused **99 passed**; compileall, body/secret scan and `git diff --check`
  passed. Official receipt
  `phase_h8r2az_reusable_summary_artifact_20260809_v2/aggregate/receipt.json` has canonical hash
  `sha256:78d3b27fcb09465227d0b39722a91e892a2c8e7b7ff9d30c76ae681469fc81cf`. Two same-source
  reuses amortize the H8-R2AY generation cost: `1914 - 2*1203 = -492` tokens. Source, required,
  recent, session, kind and integrity drift all reject.
- Remaining limitations: this is experiment-only admission evidence. It does not enable production
  reuse, semantic equivalence, cross-provider behavior, mutation/tool-task safety or default-on.

### Phase H8-R2BA：Production-shaped reusable admission shadow

- Observed gap: H8-R2AZ proved admission logic, but production metadata still had no body-free place
  to record reusable-summary admission without pretending the artifact had entered the prompt.
- Metadata impact: extended existing `ContextSelectionMetadata` with the owned nested
  `ContextCompactionReuseAdmission` value. No new `MetadataKind` was added. The value records source
  IDs/fingerprint, source binding hash, required/recent guard IDs, session constraint hash,
  artifact identity/checksum, typed status and typed rejection reason. It is shadow-only:
  `used_in_prompt=true` is invalid, admitted entries cannot carry a rejection reason, rejected
  entries require one, and duplicate admission IDs are rejected. `ContextCompactionRecord` and
  `ContextCompactionBinding` remain the compact authority and prompt-bound artifact authority.
- Validation evidence: metadata + AZ/BA focused **62 passed**; AV/AX/AY/AZ/BA adjacent focused
  **21 passed**; context/rolling focused **99 passed**; compileall, body/secret scan and
  `git diff --check` passed. Official receipt
  `phase_h8r2ba_production_binding_shadow_20260809_v1/aggregate/receipt.json` has canonical hash
  `sha256:6efa909ce7d9786f5288db7fe1af858d83f23db3ddb3d0318cee2a1b686fd78c`, with 1 admitted shadow
  and 7 rejected drift cases, no candidate decisions, no compaction attempts, no binding and no
  prompt use.
- Remaining limitations: production builder still does not read reusable artifacts by default.
  This closes the metadata surface, not prompt-use behavior or real-task semantic/token benefit.

### Phase H8-R2BB：Default-off builder shadow injection

- Observed gap: after H8-R2BA, the production metadata surface existed but `MemoryContextBuilder`
  still had no path to produce `compaction_reuse_admissions` during an actual `build()` call.
- Implemented fix: added an explicit default-off `compaction_reuse_shadow_provider` hook to
  `MemoryContextBuilder`. The hook receives only body-free candidate digests, prompt hash, request
  hash, session turn hash and constraint hash, then appends validated
  `ContextCompactionReuseAdmission` values to the selection metadata. It cannot change prompt text,
  request hash, selected candidates, context compactions, artifact bindings or source omissions.
  Non-strict failures fail closed with no admissions; strict source mode raises
  `ContextSourceError("context_compaction_reuse")`.
- Validation evidence: builder integration focused **13 passed**; metadata + builder + BB focused
  **65 passed**; metadata/context/rolling focused **153 passed**; AV/AX/AY/AZ/BA/BB adjacent
  focused **22 passed**; compileall, body/secret scan and `git diff --check` passed. Official
  receipt `phase_h8r2bb_builder_shadow_injection_20260809_v1/aggregate/receipt.json` has canonical
  hash `sha256:24b2c1e4a63ec6f79c9a80af2f25718349f8effb3a9cbb880d816cc0adcf1ab7`, with unchanged
  request hash, prompt hash, selected candidates and context compactions.
- Remaining limitations: the hook is not connected to a real artifact store/source adapter by
  default and prompt-use behavior remains unimplemented. No semantic equivalence, cross-provider,
  mutation/tool-task or long-session benefit claim is made.

### Phase H8-R2BC：Default-off artifact source adapter

- Observed gap: H8-R2BB proved that `MemoryContextBuilder` can accept a shadow provider, but the
  provider was still hand-written in tests and experiments. There was no reusable memory-layer
  adapter for turning body-free compaction artifact references into admissions.
- Implemented fix: added `memory.compaction_reuse` with `ReusableCompactionArtifactCandidate`,
  `source_binding_hash_from_shadow_payload`, `admit_reusable_compaction_candidate`, and
  `build_compaction_reuse_shadow_provider`. The adapter can reduce an existing
  `ContextCompactionBinding` to artifact identity/checksum, source IDs/fingerprint, source binding
  hash, guard IDs, session hash and generated-summary fingerprint without retaining
  `record.summary`. It fails closed on source ID, source binding, required/recent, session,
  artifact kind and artifact integrity mismatches.
- Validation observation: the old v1 receipt was not reproducible in the current checkout. The
  positive fixture reconstructed an ID-only `source_fingerprint`, while the production builder
  authoritative source index is content-aware; the positive candidate therefore failed closed
  with `source_fingerprint_mismatch`. Prompt/request/selected/context invariants stayed true, but
  the old receipt could not remain a PASS artifact.
- Implemented repair: the experiment-only `_binding_for_sources` fixture now requires and carries
  the captured builder `source_fingerprint_by_candidate_ids` value; missing authority fails
  closed. A regression assertion rejects ID-only fingerprints. Production adapter/metadata
  validation was not widened.
- Validation evidence after repair: adapter + builder + BC focused **54 passed**; metadata/context/
  adapter focused **195 passed**; compileall, body/secret scan and `git diff --check` passed.
  Fresh receipt `phase_h8r2bc_artifact_source_adapter_20260810_v2/aggregate/receipt.json` has
  file SHA-256 `sha256:bb582a75da7c6c907691b462a4cb3c380b7924bdc9edb6765d2337c610aedc29` and
  canonical hash `sha256:1ec7205d4ffe9c88b7681142f700580f0333c512e25e1e9e0cd0f4d62ec977b5`,
  with unchanged request hash, prompt hash, selected candidates and context compactions, plus
  one admitted and one `artifact_integrity_mismatch` rejected shadow admission.
- Remaining limitations: candidate discovery remains explicit/injected. The adapter is not yet
  wired to checkpoint-store discovery, and prompt-use behavior remains unauthorized.

### Phase H8-R2BD：Checkpoint discovery shadow

- Observed gap: H8-R2BC could convert explicit body-free candidates or reduced
  `ContextCompactionBinding` facts into admissions, but checkpoint prompt-context snapshots were not
  yet a discovery source. Directly admitting checkpoint bindings would be unsafe because the current
  binding contract does not persist the old source-binding hash; recomputing one from the current
  prompt payload would hide source drift.
- Implemented fix: added `build_checkpoint_compaction_reuse_shadow_provider(...)` in
  `memory.compaction_reuse`. The helper reads checkpoint-owned compaction binding identity/checksum
  and requires an external body-free source-binding hash index keyed by compaction ID. Missing index
  returns a rejected `artifact_contract_invalid` admission; stale source hash and artifact checksum
  drift fail closed. The helper remains default-off and can only append
  `used_in_prompt=false` shadow admissions through the existing builder hook.
- Validation observation: the old v1 receipt was not reproducible in the current checkout. The
  positive fixture reconstructed an ID-only `source_fingerprint`, while the production builder
  authoritative source index is content-aware; the matching candidate therefore failed closed
  with `source_fingerprint_mismatch`. Prompt/request/selected/context invariants stayed true, but
  the old receipt could not remain a PASS artifact.
- Implemented repair: all four experiment-only checkpoint binding fixtures now require and carry
  the captured builder `source_fingerprint_by_candidate_ids` value; missing authority fails
  closed. A regression assertion rejects ID-only fingerprints. Production helper/metadata
  validation was not widened.
- Validation evidence after repair: helper + builder + H8-R2BD focused **54 passed**;
  metadata/context/reuse focused **195 passed**; compileall, body/secret scan and
  `git diff --check` passed. Fresh receipt
  `phase_h8r2bd_checkpoint_discovery_shadow_20260810_v2/aggregate/receipt.json` has file
  SHA-256 `sha256:2ad9eed58bbebb062aba84b88c0659c120a4b78e28bafe41a11ee2c2c9b50f30` and
  canonical hash `sha256:97fc3093e55a81e8b23af90fba0f8e9d09ca8c56867a87b3cbae163645dff503`,
  unchanged request hash, prompt hash, selected candidate digests and context compaction count,
  with admitted/missing-hash/stale-hash/checksum-drift matrix passing. Receipt body/secret scan
  passed.
- Remaining limitations: this does not add a persisted source-binding hash to checkpoint metadata,
  does not read artifact bodies, does not authorize prompt-use or default-on behavior, and makes no
  semantic equivalence, token-benefit, cross-provider or mutation/tool-task claim.

### Phase H8-R2BE：Source-binding hash persistence

- Observed gap: H8-R2BD correctly failed closed without an external source-binding hash index, which
  meant checkpoint discovery remained half-automatic. The reusable compaction lineage needed the
  old body-free source projection hash inside the prompt-bound binding itself, without copying source
  bodies or creating a new compact authority.
- Metadata impact: extended existing `ContextCompactionBinding` with
  `source_binding_hash`. No new `MetadataKind` or public contract was added. Historical bindings
  default to `""` and remain readable; new `MemoryContextBuilder` compaction bindings write a
  `sha256:` value computed by `source_candidate_binding_hash(...)`. The field complements
  `ContextCompactionRecord.source_fingerprint`: the record fingerprint binds source content for the
  summary, while the binding hash guards the body-free prompt/source projection view for reuse.
- Implemented fix: added `source_candidate_binding_hash(...)`, persisted it when the compaction
  artifact sink returns a reference, taught `ReusableCompactionArtifactCandidate.from_binding(...)`
  to use the persisted hash, and updated checkpoint discovery to prefer persisted binding hashes.
  External hashes are compatibility-only for historical bindings; persisted/external conflicts reject
  as `artifact_contract_invalid`.
- Validation evidence: source-binding persistence focused **110 passed**. Official receipt
  `phase_h8r2be_source_binding_hash_persistence_20260809_v1/aggregate/receipt.json` has canonical
  hash `sha256:69930bcbd8f534655651a7933dac6cbb0e14d3548c8006a22533093e370fa1ec`. It proves a real
  builder-generated binding carries a persisted hash; checkpoint discovery admits without an
  external index, admits a historical binding only with explicit compatibility hash, rejects a
  conflicting external hash, preserves request hash/prompt hash/selected candidates/context
  compactions, and keeps every admission `used_in_prompt=false`. Receipt body/secret scan passed.
- Remaining limitations: no prompt-use transition, semantic quality/oracle, token-benefit,
  cross-provider, mutation/tool-task or default-on claim is made.

### Phase H8-R2BF：Reusable compact prompt-use preflight

- Observed gap: after H8-R2BE, reusable bindings had enough source-binding evidence for discovery,
  but there was still no typed gate proving that a reusable summary could safely replace source
  candidates in a future prompt-use transition without dropping required constraints, recent suffix,
  or explicit semantic facts.
- Implemented fix: added runtime-only memory-layer preflight helpers:
  `ReusableCompactionSemanticFact`, `ReusableCompactionPromptUsePreflightStatus`,
  `ReusableCompactionPromptUseRejectionReason`, `ReusableCompactionPromptUsePreflight`, and
  `preflight_reusable_compaction_prompt_use(...)`. The helper requires an admitted shadow record,
  matching source-binding hash, artifact integrity, required/recent retention, deterministic
  semantic fact coverage, and a trial assembly where the summary candidate is kept and all governed
  sources are compacted. It does not alter the real prompt and keeps
  `ContextCompactionReuseAdmission.used_in_prompt=false`.
- Validation evidence: BF focused **28 passed**. Official receipt
  `phase_h8r2bf_prompt_use_preflight_20260809_v1/aggregate/receipt.json` has canonical hash
  `sha256:4389babece9548e6310ce6b57f3730d9d3077a7a8aa2ea9baf652e8a61c44652`. It proves the pass
  case replaces both source IDs in trial and rejects non-admitted shadow evidence, source drift,
  missing semantic fact, invalid semantic evidence, recent-suffix omission, and trial summary
  non-selection. Receipt body/secret scan passed.
- Remaining limitations: semantic quality is deterministic fact coverage rather than an LLM judge or
  real-task equivalence proof. The reusable summary still does not enter production prompts, and no
  token-benefit, cross-provider, mutation/tool-task or default-on claim is made.

### Phase H8-R2BG：Reusable compact prompt-use simulation

- Observed gap: H8-R2BF proved that a reusable binding can pass a dry-run preflight, but it still
  did not compare a raw assembly against a reusable projection or prove that the compactor actually
  governed source omission instead of falling back to raw sources.
- Implemented fix: added runtime-only memory-layer simulation helpers:
  `ReusableCompactionPromptUseSimulationStatus`,
  `ReusableCompactionPromptUseSimulationRejectionReason`,
  `ReusableCompactionPromptUseSimulation`, and
  `simulate_reusable_compaction_prompt_use(...)`. The helper requires a passed preflight, verifies
  source binding against current candidates, runs raw and reusable assemblies, requires the summary
  candidate to be kept, requires every source to be omitted with `reason="compacted"` and governed
  by the summary candidate, keeps required/recent IDs, and rejects summaries that do not reduce
  prompt characters. Rejected preflights short-circuit without prompt hashes.
- Validation evidence: BG focused **33 passed**; metadata/context/checkpoint + BG focused
  **295 passed**; AV/AX/AY/AZ/BA/BB/BC/BD/BE/BF/BG adjacent focused **27 passed**; compileall,
  `git diff --check`, official receipt body/secret scan and trailing whitespace scan passed.
  Official receipt
  `phase_h8r2bg_prompt_use_simulation_20260809_v1/aggregate/receipt.json` has canonical hash
  `sha256:8fa0e644e8356dacba34c2f2b0ab4d2c73fecabebdb083c5a8b00baeeba06c31`. It proves the pass
  case has positive prompt-char reduction, exact source replacement and required/recent retention;
  rejected preflight, source drift, summary fallback and non-beneficial summary cases fail closed.
- Remaining limitations: the prompt reduction is fixture character evidence, not provider token or
  billing evidence. Semantic quality is still deterministic fact coverage inherited from BF. The
  reusable summary still does not enter production prompts, and no real-task, cross-provider,
  mutation/tool-task or default-on claim is made.

### Phase H8-R2BH：Builder-sourced reusable compact prompt-use simulation

- Observed gap: H8-R2BG used a hand-built candidate fixture. Before moving toward any
  builder-adjacent canary, the same preflight + simulation chain needed to consume actual
  `MemoryContextBuilder.build()` selected candidates without changing builder output.
- Implemented fix: added `stage_h8r2bh_builder_sourced_simulation.py`. The harness builds a
  deterministic short-memory context through `MemoryContextBuilder`, validates the returned
  `selected_context_candidates` as `ContextCandidate` values, selects non-required dialog sources,
  required instruction IDs and a recent suffix, then constructs an in-memory reusable binding and
  runs H8-R2BF preflight plus H8-R2BG simulation. The builder prompt hash and context request hash
  are recorded, but prompt/source/summary bodies are excluded from the receipt.
- Validation evidence: BG/BH focused **34 passed**; metadata/context/checkpoint + BG/BH focused
  **296 passed**; AV/AX/AY/AZ/BA/BB/BC/BD/BE/BF/BG/BH adjacent focused **28 passed**; compileall,
  `git diff --check`, and official receipt body/secret scan passed. Official receipt
  `phase_h8r2bh_builder_sourced_simulation_20260810_v1/aggregate/receipt.json` has canonical hash
  `sha256:cef18b6c863117329809bbbfab49b58246355eb3830ee5e9d42b7bf3e45d88b7`. It proves builder
  output shape readiness, unchanged builder context compactions, positive simulation prompt-char
  reduction, exact replacement of builder-selected sources, and required/recent retention.
- Remaining limitations: this is still offline and character-based. It does not connect production
  artifact discovery, does not mutate builder prompts, does not call a provider, and makes no
  semantic-equivalence, token-benefit, real-task, mutation/tool-task, cross-provider or default-on
  claim.

### Phase H8-R2BI：Token-aware builder-adjacent opt-in canary

- Observed gap: H8-R2BH proved builder-selected candidates can feed the reusable simulation path,
  but it only measured prompt characters. Before real-provider benefit experiments, the same
  default-off path needed explicit token-accounting plumbing through `ContextAssembler` token mode.
- Implemented fix: extended the runtime-only simulation helper with optional token fields and a
  `token_counter` argument. When token accounting is requested, raw and reusable assemblies use the
  same token policy/counter and record final prompt tokens, token delta, token count method,
  tokenizer ID and model. Added `stage_h8r2bi_token_aware_opt_in_canary.py`, which consumes
  `MemoryContextBuilder.build()` selected candidates, runs preflight and simulation with an
  explicit offline token counter, and writes body-free receipt evidence.
- Validation evidence: BI focused **34 passed**; BG/BH/BI focused **36 passed**;
  metadata/context/checkpoint + BG/BH/BI focused **298 passed**; AV/AX/AY/AZ/BA/BB/BC/BD/BE/BF/BG/BH/BI
  adjacent focused **29 passed**; compileall, `git diff --check`, and official receipt body/secret
  scan passed. Official receipt
  `phase_h8r2bi_token_aware_opt_in_canary_20260810_v1/aggregate/receipt.json` has canonical hash
  `sha256:ea3d3016dc20ff21a59a55300c87e2b8296117707e606bafc95e8e09114d75cb`; raw/reusable prompt
  tokens were `234→45`, delta `189`, with positive char delta, exact source replacement and
  required/recent retention.
- Remaining limitations: token counts are deterministic offline accounting, not provider billing
  usage. The reusable binding is still harness-constructed in memory; production artifact discovery
  and production prompt mutation remain unimplemented. No semantic-equivalence, real-task,
  mutation/tool-task, cross-provider or default-on claim is made.

### Phase H8-R2BJ：Discovered persisted binding opt-in canary

- Observed gap: H8-R2BI still constructed the reusable binding inside the harness. Before a
  real-provider paired canary, the opt-in path needed to consume a binding produced by
  `MemoryContextBuilder` compaction/sink and rediscovered from checkpoint lineage.
- Implemented fix: added `stage_h8r2bj_discovered_binding_opt_in.py`. The harness seeds one
  short-memory conversation, builds a high-budget raw context for complete source candidates, builds
  a low-budget compact context with a real compaction sink to produce one persisted
  `ContextCompactionBinding`, creates a prompt-context snapshot, runs
  `build_checkpoint_compaction_reuse_shadow_provider(...)` against raw candidate digests, then feeds
  the discovered shadow admission and persisted binding into preflight + token-aware simulation.
- Validation evidence: BJ focused **1 passed**; compaction reuse + BI/BJ focused **35 passed**;
  BG/BH/BI/BJ focused **37 passed**; metadata/context/checkpoint + BG/BH/BI/BJ focused
  **299 passed**; AV/AX/AY/AZ/BA/BB/BC/BD/BE/BF/BG/BH/BI/BJ adjacent focused **30 passed**;
  compileall, `git diff --check`, and official receipt body/secret scan passed. Official receipt
  `phase_h8r2bj_discovered_binding_opt_in_20260810_v1/aggregate/receipt.json` has canonical hash
  `sha256:966b6693355dbf3943f2c8c24db70e75cd9d860515bc9e03c8ae27b685bf590e`; raw/reusable prompt
  tokens were `789→239`, delta `550`, with discovered binding admission, exact source replacement
  and required/recent retention.
- Remaining limitations: token counts are offline accounting, not provider usage. Reusable summary
  prompt-use remains simulation-only, not production builder output. Semantic facts remain
  deterministic summary-coverage checks. No real-provider quality, net benefit, mutation/tool-task,
  cross-provider or default-on claim is made.

### Phase H8-R2BK：Real-provider read-only paired canary

- Observed gap: H8-R2BJ proved discovered persisted bindings could enter the default-off
  preflight + simulation chain, but it still lacked real provider usage, finish reason and
  answer-quality evidence. Before moving toward mutation/tool-task benefit experiments, the same
  chain needed a minimal real-provider read-only paired canary.
- Implemented fix: added `stage_h8r2bk_real_provider_read_only_paired_canary.py`. The harness seeds
  deterministic short-memory facts, builds raw and compact `MemoryContextBuilder` contexts, admits
  the compact/sink binding through checkpoint discovery, runs reusable preflight + token-aware
  simulation, then constructs raw/reusable projections in memory for two read-only JSON provider
  calls. Receipts persist only hashes, IDs, usage, finish reasons and boolean quality facts; prompt,
  source, summary, response bodies and credentials remain transient.
- Validation evidence: BK focused **2 passed**; compaction reuse + BF/BG/BH/BI/BJ/BK focused
  **40 passed**; compileall, `git diff --check`, and BK receipt/code/doc body/secret scan passed.
  Official receipt
  `phase_h8r2bk_real_provider_read_only_paired_canary_20260810_v1/aggregate/receipt.json` has
  canonical hash `sha256:023074faf47e1bf0ab54d0b2d5a78b1da507536c1ae63a487e3ac0085927dba8`.
  DeepSeek prompt tokens were `4,424→1,176`, delta `3,248`; total tokens were `4,474→1,233`,
  delta `3,241`; both arms finished `stop`, usage was complete, deterministic facts were covered,
  and side effects were limited to two provider calls.
- Remaining limitations: this is one DeepSeek read-only quality canary with structured fact
  coverage, not semantic equivalence, mutation/tool-task proof, OpenAI/cross-provider evidence,
  production builder prompt-use, or default-on authorization.

### Phase H8-R2BL：Read-only provider confirmation matrix

- Observed gap: H8-R2BK was a single real-provider read-only canary. Before entering
  mutation/tool-task shadow work, the same reusable-projection chain needed a small confirmation
  matrix. The first BL real runs also exposed a Compact quality bug: deterministic summaries kept
  line prefixes such as `ASSISTANT: Decision ...` and truncated away bounded `key=value` facts.
- Implemented fix: added `stage_h8r2bl_read_only_confirmation_matrix.py` with three deterministic
  read-only fact-contract scenarios. Each scenario builds raw/compact `MemoryContextBuilder`
  contexts, admits the persisted binding through checkpoint discovery, runs preflight + simulation,
  and then calls DeepSeek raw/reusable JSON arms. `MemoryContextBuilder._dialog_compaction_record`
  now detects bounded `key=value` markers and projects them before per-line truncation, while
  preserving the existing natural-language signal extraction path. Added
  `test_memory_context_segmented_compaction_preserves_structured_markers`.
- Validation evidence: BL focused **2 passed**; marker preservation + compaction reuse + BK/BL
  focused **39 passed**. The initial BL receipts are retained as failure observations; the official
  passing v3 receipt
  `phase_h8r2bl_read_only_confirmation_matrix_20260810_v3/aggregate/receipt.json` has canonical hash
  `sha256:e706653b341de451d3f237fee80c8928b4cf732fb987e343075a1d7520a69766`. Aggregate DeepSeek prompt
  tokens were `28,623→2,054`, delta `26,569`; total tokens were `28,719→2,150`, delta `26,569`.
  All three cases passed discovery, preflight, simulation, usage/finish, prompt-reduction and fact
  coverage gates; side effects were limited to six read-only provider calls.
- Remaining limitations: this confirms the DeepSeek read-only fact-contract lane only. It is not
  semantic equivalence, mutation/tool-task proof, OpenAI/cross-provider evidence, production
  prompt-use, or default-on authorization.

### Phase H8-R2BM：Mutation/tool-task shadow gate

- Observed gap: H8-R2BL proved reusable context projection for DeepSeek read-only fact contracts,
  but did not prove the same compacted context could safely drive a provider-tool mutation without
  losing permissions, exact validation, or suspicious-success evidence.
- Implemented fix: added `stage_h8r2bm_mutation_tool_shadow.py`, which creates a temporary
  calculator fixture and runs raw/reusable arms through the production
  `ToolPlanningTaskExecutor.execute_provider_tool_task(...)` entry. The task is explicitly
  `real_mutation`, `allow_mutations=True`, `user_confirmed=True`, uses only `file_reader`,
  `file_patch_writer` and `command_executor`, limits writes to `calculator.py`, and requires the
  exact validation command `python -m pytest -q tests/test_calculator.py`. The receipt records only
  hashes, IDs, usage, finish reasons, tool names, and boolean gates; it excludes prompt/source/
  summary/response bodies, patch body, stdout/stderr, and credentials.
- Validation evidence: BM focused **4 passed**; adjacent compaction/provider-tool focused
  **140 passed**; compileall, `git diff --check`, and receipt body/secret scan passed. Official
  receipt `phase_h8r2bm_mutation_tool_shadow_20260810_v1/aggregate/receipt.json` has canonical hash
  `sha256:f3b2719a95ca6164156440d92c793e5ce60a6d79542cecce00ca711e5c00dba8`. Both raw and
  reusable DeepSeek arms executed `file_reader → file_patch_writer → command_executor → final`,
  passed scoped writer evidence, provider exact pytest, independent exact pytest, and no
  out-of-scope source-change/suspicious-success gates. Prompt tokens were `17,433→7,127`, total
  tokens were `17,936→7,651`, provider calls stayed `4/4`.
- Remaining limitations: this is one isolated calculator fixture, not a real-project mutation
  rollout or cross-provider proof. The reusable arm used 21 more completion tokens, so completion
  inflation still needs tracking. Reusable projection remains explicit harness opt-in and
  `ContextCompactionReuseAdmission.used_in_prompt=false`; no production prompt-use or default-on
  claim is made.

### Phase H8-R2BN：Mutation/tool-task confirmation matrix

- Observed gap: H8-R2BM was one calculator fixture. A broader isolated mutation matrix was needed
  before treating reusable projection as stable enough for later real-project mutation shadow work.
  The first BN real runs also exposed a provider-tool contract bug: requests contained `tools`, but
  most arms did not enter native tool calls because `LLMRequest` had no typed `tool_choice` field and
  the extra value was dropped before transport.
- Implemented fix: added `stage_h8r2bn_mutation_confirmation_matrix.py` with three temporary
  single-file mutation cases and raw/reusable DeepSeek arms through the production
  `ToolPlanningTaskExecutor.execute_provider_tool_task(...)` entry. Added provider-neutral
  `LLMRequest.tool_choice`, request-builder propagation, transport payload/cache/diagnostic
  handling, and provider-tool runner policy that sends `tool_choice=required` only while a tool
  action is expected. After scoped writer and exact validation evidence, finalization omits tools
  and tool choice so the provider can return a no-tool final answer.
- Validation evidence: BN focused **5 passed**; adjacent compaction/provider-tool gate
  **147 passed**; full DeepSeek/provider round-trip regression **122 passed**; compileall,
  `git diff --check`, and receipt body/secret scan passed. Official receipt
  `phase_h8r2bn_mutation_confirmation_matrix_20260810_v3/aggregate/receipt.json` has canonical hash
  `sha256:06bc71d3fc740c59a3a36aad7bda9c9387001cca605e282c41da27330ac8e003`. All 3 cases / 6 arms
  completed `tool_calls → tool_calls → tool_calls → stop`, observed scoped writer evidence, ran
  exact provider validation, passed independent exact validation, and had no fallback or suspicious
  success. Aggregate prompt tokens were `41,288→14,223`; total tokens were `42,633→15,251`;
  completion tokens were `1,345→1,028`.
- Remaining limitations: this is still DeepSeek on temporary isolated fixtures, not a real-project
  mutation rollout, OpenAI/cross-provider proof, production prompt-use, or default-on authorization.
  Reusable projection remains harness-level explicit opt-in and
  `ContextCompactionReuseAdmission.used_in_prompt=false`. The shared `tool_choice`/finalization
  contract needs independent review before any fixed local commit is called accepted.

### Phase H8-R2BO：Real-project mutation tool-choice admission replay

- Observed gap: BN proved the `tool_choice=required`/finalization contract on isolated fixtures, but
  the old H8 real-project shadow line had failed before mutation and the latest shared contract still
  needed a real-project-shaped admission replay before any credentialed provider arm.
- Implemented fix: added `stage_h8r2bo_real_project_tool_choice_admission.py`. It copies `Code/src`
  and `Code/tests/test_provider_tool_roundtrip.py` into a temporary source-isolated workspace, then
  drives the production `ToolPlanningTaskExecutor.execute_provider_tool_task(...)` entry with a
  deterministic provider-shaped mock. The task reads the real provider round-trip files, writes only
  the temporary test file, runs the exact validation command, and records request-shape, mutation and
  validation gates without persisting prompt/source/response/patch/stdout/stderr bodies or credentials.
- Validation evidence: BO focused **4 passed**; adjacent provider-tool/tool-choice gate **135 passed**;
  compileall, `git diff --check`, and receipt body/secret scan passed. Official receipt
  `phase_h8r2bo_real_project_tool_choice_admission_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:8464bb2b19ac70460ac9a397683d15d9abcabc9a6d46fcda8d4b2d07cae90a6d`. The replay made
  four mock completion calls and zero provider/network calls. The first three requests carried
  `tool_choice=required`; the finalization request exposed no tools and no tool choice. Scoped writer,
  exact provider validation, independent exact validation and suspicious-success gates all passed.
- Remaining limitations: this is a source-isolated mock replay, not a real provider/token benefit
  result. It authorizes planning a separate credentialed real-project raw/Compact provider arm only
  after independent review of the dirty tree and shared contract; it does not authorize production
  prompt-use, OpenAI/cross-provider claims or default-on Compact.

### Phase H8-R2BP：Real-project provider raw/Compact mutation pair

- Observed gap: BO proved only the mock provider replay. A minimal credentialed DeepSeek raw/Compact
  pair was needed to see whether the same real-project-shaped mutation task can complete under real
  provider transport without losing tool-choice, finalization, scope, validation or suspicious-success
  gates.
- Implemented fix: added `stage_h8r2bp_real_project_provider_pair.py`. The runner builds raw and
  reusable candidate projections through the discovered-binding/preflight/simulation chain, then runs
  raw and Compact arms in independent temporary copies of `Code/src` plus
  `Code/tests/test_provider_tool_roundtrip.py`. The task writes only the temporary test file and runs
  the exact provider round-trip pytest command. Receipts store hashes, request shapes, usage, finish
  reasons and boolean gates only.
- Diagnosis during real run: v1 stopped before tool execution because DeepSeek provider-default
  thinking rejects `tool_choice=required`. That run was excluded and moved to local quarantine because
  its receipt contained raw provider error text. The BP runner now uses an experiment-only executor
  override that classifies this frozen task as routine so settings-level disabled reasoning is
  preserved; no production reasoning policy was changed.
- Validation evidence: BP focused **5 passed**; adjacent provider-tool/tool-choice gate **140 passed**;
  compileall, `git diff --check`, and mock/real receipt body-secret scans passed. Mock preflight
  receipt hash is `sha256:1ddb3233aff901bcd21f3570c76d96b4f980c44e20b66830117615993068884b`.
  Official real-provider v2 receipt
  `phase_h8r2bp_real_project_provider_pair_20260810_v2/aggregate/receipt.json` has canonical hash
  `sha256:da4c0e0db7697fe4b1b3bc3774fd120e0fedfddd4eefca8d3635f9368d582fe0`. Both arms completed
  `tool_calls → tool_calls → tool_calls → stop`, used disabled reasoning, passed scoped writer,
  exact provider validation, independent exact validation and request-shape gates, with no retry,
  fallback or suspicious success. Prompt tokens were `19,673→11,127`; total tokens were
  `20,091→11,592`; completion tokens were `418→465`; calls stayed `4/4`.
- Remaining limitations: this is one small DeepSeek real-project-shaped mutation pair, not a larger
  real-project matrix, OpenAI/cross-provider proof, production prompt-use, production reasoning policy
  change, call-count benefit or default-on authorization. The quarantined v1 run must not be used as
  accepted evidence.

### Phase H8-R2BQ：Real-project provider mutation confirmation matrix

- Observed gap: BP was one real-project-shaped mutation pair. Before expanding toward broader real
  tasks, the same DeepSeek provider-tool lane needed a minimal confirmation matrix while preserving
  source isolation, exact validation, disabled reasoning and body-free receipts.
- Implemented fix: added `stage_h8r2bq_real_project_provider_matrix.py` with two sentinel mutation
  cases: `Code/tests/test_provider_tool_roundtrip.py` and
  `Code/tests/test_deepseek_tool_roundtrip.py`. Each case runs raw and reusable arms in independent
  temporary workspaces, using discovered-binding/preflight/simulation for reusable projection and the
  production provider-tool entry for execution. The matrix stores only hashes, request shapes, usage,
  finish reasons, tool names and boolean gates.
- Validation evidence: candidate exact validations passed locally (**103** and **19** tests);
  BQ focused **5 passed**; adjacent provider-tool/tool-choice gate **145 passed**; compileall,
  `git diff --check`, and mock/real receipt body-secret scans passed. Mock preflight receipt hash is
  `sha256:a5d077de12d6bef2d6312b2e536ff1e938563138086cdd77db5fe026a67e7e6a`. Official real-provider
  receipt `phase_h8r2bq_real_project_provider_matrix_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:e36bbb9c1efec73bb96ccf703b65d00836099d1626e480ed9ccbd719560a77e1`. All 2 cases / 4 arms
  passed scoped writer, exact provider validation, independent validation, request-shape and
  suspicious-success gates. Aggregate prompt tokens were `51,138→24,797`; total tokens were
  `52,050→25,722`; completion tokens were `912→925`.
- Remaining limitations: provider calls were 22 because some arms spent extra read rounds before the
  writer, so no call-count benefit is claimed. This is still DeepSeek-only sentinel test mutation
  evidence, not production prompt-use, OpenAI/cross-provider proof, production reasoning policy change
  or default-on authorization.

### Phase H8-R2BR：Extra-read reduction

- Observed gap: BQ passed safety and token-reduction gates, but its `Task.read_files` treated the
  target test file plus support files as required read-before-write scope. Since the provider-tool
  runner only narrows to writer after all scoped reads complete, three arms spent extra read rounds
  and the matrix needed 22 provider calls instead of the ideal 16.
- Implemented fix: added `stage_h8r2br_extra_read_reduction.py` with the same two real-project
  sentinel mutation cases, but with `Task.read_files` narrowed to the target file. Support files are
  represented as required body-free support-context metadata, and a narrow mutation recipe carries
  enough target-specific intent to avoid support-source reads for this sentinel. The harness also adds
  a response-shape gate requiring finalization `stop`, after a v2 diagnostic exposed one `length`
  finalization despite successful mutation/validation.
- Validation evidence: BR focused **5 passed**; adjacent provider-tool/tool-choice gate **150 passed**;
  compileall, `git diff --check`, and mock/real receipt body-secret scans passed. Mock v3 receipt hash
  is `sha256:75fef9ce3a6992024583a878c5673fd644bc7f80f69637338ccb382f47c8bf41`. Official real-provider
  v3 receipt `phase_h8r2br_extra_read_reduction_20260810_v3/aggregate/receipt.json` has canonical hash
  `sha256:ee8ffba20334083b4d86eaf4f2f0171c93df832c6627aac8dae5a82fb6ba81a9`. All 2 cases / 4 arms
  passed scoped writer, exact provider validation, independent validation, request-shape,
  response-shape and suspicious-success gates. Provider calls fell `22→16`; aggregate prompt tokens
  were `31,951→15,347`; total tokens were `32,522→15,850`; completion tokens were `571→503`.
- Remaining limitations: this is an experiment-only DeepSeek sentinel matrix and evidence for a
  future required-read vs support-context contract split. It is not production prompt-use, a production
  `Task` contract change, a production provider-routing change, OpenAI/cross-provider proof, broad real
  task evidence or default-on authorization.

### Phase H8-R2BS：Support-context task contract

- Observed gap: BR proved that separating required read-before-write evidence from support context
  reduces extra provider reads, but that distinction existed only inside the experiment harness.
  Production task/task-graph contracts still had only `read_files` and `write_files`, encouraging
  future planners to put helpful reference files back into `read_files` and reintroduce the BQ
  call-count inflation.
- Implemented fix: added `support_context_files` to runtime `Task` and persisted
  `TaskGraphNodeMetadata`, preserved it through runtime session and autopilot task-graph conversion,
  and taught `ExecutionTaskDecomposer` to parse/expose it. Provider-native execution now projects
  non-empty support context into required body-free `ContextCandidate` metadata under the existing
  explicit projection flags. The projection records path/hash/role facts with
  `routing=not_required_read_before_write`, `read_authority=false`, `write_allowed=false`,
  `validation_authority=false`, and `payload_omitted=true`; `read_scope` remains exactly
  `Task.read_files`.
- Validation evidence: focused support-context contract **5 passed**; provider/tool focused
  **108 passed**; adjacent metadata/task/provider regression **190 passed**; BR/BQ focused
  **10 passed**; compileall, `git diff --check`, and changed-file secret-shaped scans passed.
  Tests prove field round-trip/default compatibility, runtime task-graph preservation, body-free
  provider projection, mutation projection flag enforcement, and support-only read rejection before
  tool execution.
- Remaining limitations: BS is a production-facing contract hardening step, not a new real-provider
  benefit claim. The next stage must rerun a BR-style real-provider matrix using
  `Task.support_context_files` directly instead of harness-local support candidates before claiming
  production-route call-count benefit. It is still not production prompt-use, source-body inclusion,
  production runner read-completion change, OpenAI/cross-provider proof, broad real-task evidence or
  default-on authorization.

### Phase H8-R2BT：Production support-context matrix

- Observed gap: BS added the production `support_context_files` contract, but the BR call-count gain
  was still only proven with experiment-local support candidates. Without a production-field replay,
  it was unclear whether the provider-entry projection would preserve the ideal 4-call mutation shape.
- Implemented fix: added `stage_h8r2bt_production_support_context_matrix.py` and a focused test suite.
  The runner reuses the BR two-case real-project sentinel mutation matrix, but each task now carries
  `Task.support_context_files=SUPPORT_FILES` and raw/reusable initial candidates deliberately exclude
  harness-local support candidates. The receipt validates that selected support IDs use the production
  `provider-support-context:` prefix and no `h8r2br-support-context:` IDs appear.
- Validation evidence: BT focused **5 passed**; adjacent BT/BR/BQ/provider/metadata/runtime regression
  **184 passed**; compileall, `git diff --check`, and mock/real receipt body-secret scans passed.
  Mock receipt hash is `sha256:8eb6970991d3259c4d1f1d50b3315af70a5529563728a7ec6c285ef2e1397a83`.
  Official real-provider receipt
  `phase_h8r2bt_production_support_context_matrix_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:0015ab0fc791526a4d265f063b9a1279ec08211a44663ec96563c9586de6b1b3`. All 2 cases / 4 arms
  passed scoped writer, exact provider validation, independent validation, request-shape,
  response-shape and suspicious-success gates. Provider calls stayed ideal `16`; aggregate prompt
  tokens were `31,230→18,364`; total tokens were `32,000→18,857`; completion tokens were `770→493`.
- Remaining limitations: this proves the production provider-entry support-context path for the two
  DeepSeek sentinel mutation cases only. It is still not production prompt-use/default-on Compact,
  source-body support inclusion, a production runner read-completion change, OpenAI/cross-provider
  proof, broad real-task evidence or accepted commit evidence.

### Phase H8-R2BU：Real code mutation with production support context

- Observed gap: BT proved the production `Task.support_context_files` route for sentinel test-file
  mutations, but it still had not shown that the same context split could support an actual
  production-code refactor while preserving target-only write scope, exact validation and
  suspicious-success gates.
- Implemented fix: added `stage_h8r2bu_real_code_mutation.py` and a focused test suite. The runner
  executes one scoped refactor of `Code/src/core/validation_command.py` in independent temporary
  source-isolated workspaces, with `Code/src/core/provider_tool_admission.py`,
  `Code/src/core/provider_tool_roundtrip.py` and `Code/tests/test_validation_command.py` carried as
  production `support_context_files`. The expected edit is checked by an explicit refactor marker gate
  in addition to scoped writer, exact provider validation and independent exact validation gates.
- Validation evidence: BU focused **5 passed**; adjacent BU/BT/BR/BQ/provider-roundtrip/
  validation-command regression **134 passed**; compileall and mock/real receipt body-secret scans
  passed. Mock receipt hash is
  `sha256:1da677e1b2a3cf3232b816cbe18cca6fb43b52aa368d48da17fef1a2a8aa1bd9`. Official real-provider
  receipt `phase_h8r2bu_real_code_mutation_20260810_v1/aggregate/receipt.json` has canonical hash
  `sha256:01abe72dfb94c37848a10bd6a10f99f4b4725a75086bde62da7484d70d70618b`. Both raw/reusable
  DeepSeek arms completed the ideal 4-call shape, selected production `provider-support-context:`
  IDs, selected no harness-local support IDs, changed only the target file, passed exact provider
  validation, independent validation, request/response shape, explicit refactor and
  suspicious-success gates. Prompt tokens fell `11,843→7,769`; total tokens fell `12,217→8,145`;
  provider calls stayed ideal `8`; retry and fallback stayed zero.
- Remaining limitations: this is one DeepSeek production-code mutation stratum, not production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence, production reasoning-policy change or accepted commit evidence.

### Phase H8-R2BV：Support-context consolidation review

- Observed gap: BS/BT/BU together provided shared-contract, production-route and one production-code
  mutation stratum evidence, but there was still no deterministic consolidation audit separating
  acceptable shared surfaces from experiment-only or unaccepted claims. Without that boundary, a later
  commit could accidentally include run artifacts, unrelated dirty-tree changes, or overclaim
  default-on/cross-provider readiness.
- Implemented fix: added `stage_h8r2bv_support_context_consolidation_review.py` and focused tests.
  The audit checks typed `Task` and `TaskGraphNodeMetadata` fields/defaults, shared runtime/autopilot/
  decomposer/provider projection markers, support-context documentation markers, focused test markers,
  and validates BT/BU real-provider receipts through their own stage validators. It emits a body-free
  receipt and records zero Provider calls, zero project mutations, zero memory mutations and no commit.
- Validation evidence: BV focused **4 passed**; adjacent BV/BU/BT/provider-roundtrip/runtime-session/
  metadata/execution-planning regression **280 passed**; compileall, `git diff --check`, and BV receipt
  body-secret scans passed. Official audit receipt
  `phase_h8r2bv_support_context_consolidation_review_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:bd19fb4f91c4091654eab95997941c1c8c31f298e26d8074a300125cc5a2453b`.
- Remaining limitations: BV is review-readiness evidence only. It does not create or accept a commit,
  does not accept the dirty tree, does not authorize production prompt-use/default-on Compact,
  source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2BW：Support-context commit-candidate preparation

- Observed gap: BV said the support-context shared surface was ready for a separate review candidate,
  but the current dirty tree remained too large and mixed to safely stage or commit. Without an exact
  manifest, run artifacts, data memory, sketches, legacy experiment deletions or unrelated
  core/memory/reasoning changes could be accidentally folded into the support-context package.
- Implemented fix: added `stage_h8r2bw_support_context_commit_candidate.py` and focused tests. The
  manifest groups the support-context package into production/shared files, regression tests and
  experiment evidence files; classifies git dirty paths as candidate, excluded or non-candidate; checks
  the exclusion policy for runs/data memory/tmp/sketch/legacy deleted experiments; validates the BV
  receipt as evidence while explicitly excluding it from the commit candidate; and keeps
  `commit_ready=false`.
- Validation evidence: BW focused **5 passed**; adjacent BW/BV/BU/BT/provider-roundtrip/
  runtime-session/metadata/execution-planning regression **285 passed**; compileall, `git diff --check`
  and BW receipt body-secret scans passed. Official manifest receipt
  `phase_h8r2bw_support_context_commit_candidate_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:b7926e9790b6d8c540d648d2a9f463e23da4711b1cbd24bb287bea51fff3dc5f`. The manifest records
  32 candidate paths, 997 excluded dirty paths and 558 non-candidate dirty paths at generation time,
  with zero stage/commit/push/provider/project/memory side effects.
- Remaining limitations: BW prepares a commit-candidate manifest only. It does not stage files, create
  or accept a commit, accept the dirty tree, authorize production prompt-use/default-on Compact,
  source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2BX：Exact staging review simulation

- Observed gap: BW identified the exact 32-path support-context candidate package, but it had not yet
  simulated the actual review boundary for that fixed set. In particular, untracked candidate files
  are not fully covered by `git diff --check`, and audit artifacts can accidentally inflate the
  candidate set if every subsequent result is recursively included.
- Implemented fix: added `stage_h8r2bx_exact_staging_review_simulation.py` and focused tests. The
  simulation validates the BW receipt, extracts the fixed 32 candidate paths, verifies every path
  exists and is not excluded, runs exact `git diff --check`, records body-free diff stats, scans all
  candidate files for secret-shaped values and trailing whitespace, and emits review evidence tables
  for state/effects, resource bounds and boundary behavior. BX explicitly does not add BX artifacts to
  the BW candidate set.
- Validation evidence: BX focused **5 passed**; adjacent BX/BW/BV/BU/BT/provider-roundtrip/
  runtime-session/metadata/execution-planning regression **290 passed**; compileall, `git diff --check`
  and BX receipt body-secret scans passed. Official simulation receipt
  `phase_h8r2bx_exact_staging_review_simulation_20260810_v1/aggregate/receipt.json` has canonical hash
  `sha256:4939a02e4063524ea5f0e0957a29a187ebfb9ab7bae7840cb41c622a9e60e9e9`. Body-free diff stats are
  32 files, 15,498 added lines and 39 deleted lines; candidate body scan found zero secret-shaped and
  zero trailing-whitespace hits; stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: BX is still simulation/review-readiness evidence. It does not stage files,
  create or accept a commit, accept the wider dirty tree, authorize production prompt-use/default-on
  Compact, source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or
  production reasoning-policy changes.

### Phase H8-R2BY：Support-context review split audit

- Observed gap: BX proved the 32-path candidate can pass exact staging simulation, but its body-free
  diff stat is over 15k changed lines. Under the repository review sizing policy, that is too large
  for one review/commit and risks burying the actual shared metadata/runtime contract inside
  experiment evidence.
- Implemented fix: added `stage_h8r2by_support_context_review_split.py` and focused tests. The audit
  validates BW/BX receipts, applies review sizing thresholds, rejects the 32-path package as a single
  commit, and emits three proposed packages: production/shared runtime, regression tests, and
  experiment evidence. It also records review evidence tables and a required finding to split before
  commit.
- Validation evidence: BY focused **5 passed**; adjacent BY/BX/BW/BV/BU/BT/provider-roundtrip/
  runtime-session/metadata/execution-planning regression **295 passed**; compileall, `git diff --check`
  and BY receipt body-secret scans passed. Official split receipt
  `phase_h8r2by_support_context_review_split_20260810_v1/aggregate/receipt.json` has canonical hash
  `sha256:bb17048198714d3cc1aa0f70dca3bc29332a8db4b2f063670d4810ab5959784d`. The split covers all 32
  paths exactly once with zero excluded paths: production/shared runtime 9 paths / 1,036 changed lines,
  regression tests 3 paths / 5,565 changed lines, experiment evidence 20 paths / 8,936 changed lines.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: BY provides split-strategy evidence only. It does not stage files, approve
  any package, create or accept a commit, accept the wider dirty tree, authorize production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2BZ：Support-context runtime package review

- Observed gap: BY split out `support_context_contract_runtime` as the actual shared
  metadata/runtime package, but that package still had 9 paths and 1,036 changed lines. That exceeds
  the package-specific review threshold and puts the provider projection behavior in the same review
  unit as typed fields and docs.
- Implemented fix: added `stage_h8r2bz_support_context_runtime_package_review.py` and focused tests.
  The audit validates the BY receipt, reviews only the runtime package, verifies typed contract,
  propagation and provider projection markers, and emits three smaller review slices:
  typed contract/docs, runtime propagation, and provider projection. It records a required finding to
  split the runtime package before commit.
- Validation evidence: BZ focused **5 passed**; adjacent BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/
  runtime-session/metadata/execution-planning regression **300 passed**; compileall, `git diff --check`
  and BZ receipt body-secret scans passed. Official runtime package review receipt
  `phase_h8r2bz_support_context_runtime_package_review_20260810_v1/aggregate/receipt.json` has
  canonical hash `sha256:ae636fed2e5505212f0a5f928836d452bed177d957fefa4aada7ebda4320f562`.
  The proposed slices are typed contract/docs 5 paths / 438 changed lines, runtime propagation
  3 paths / 51 changed lines, and provider projection 1 path / 547 changed lines. Marker audit found
  zero missing markers. Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: BZ provides runtime-package split strategy only. It does not approve any
  slice, stage files, create or accept a commit, accept the wider dirty tree, authorize production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2CA：Runtime propagation slice review

- Observed gap: BZ identified `support_context_runtime_propagation` as the smallest review slice, but
  path-level slicing was still too coarse. A direct diff inspection showed unrelated rolling-summary
  initialization in `intelligent_autopilot.py` and unrelated checkpoint ingress/session binding in
  `runtime_controller.py` mixed into the same 3-path slice.
- Implemented fix: added `stage_h8r2ca_runtime_propagation_slice_review.py` and focused tests. The
  audit validates the BZ receipt, classifies changed lines into support-context propagation or stable
  unrelated classes, verifies support-context propagation markers, and rejects the path-level slice as
  a support-context commit while requiring hunk-level selective staging.
- Validation evidence: CA focused **5 passed**; adjacent CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/
  runtime-session/metadata/execution-planning regression **305 passed**; compileall, `git diff --check`
  and CA receipt body-secret scans passed. Official review receipt
  `phase_h8r2ca_runtime_propagation_slice_review_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:b2eee4c86239c6cba5c32c860c74b744c69236053114fed57391563f5056a90b`. The audit records
  9 support-context changed lines and 42 unrelated changed lines. Contaminated files are
  `runtime_controller.py` and `intelligent_autopilot.py`; `execution_task_decomposer.py` is pure
  support-context propagation. Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: CA provides hunk-contamination evidence only. It does not approve any hunk,
  stage files, create or accept a commit, accept the wider dirty tree, authorize production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2CB：Runtime propagation hunk candidate manifest

- Observed gap: CA proved the 3-path runtime propagation slice was contaminated, but it still only
  said "split by hunk" without a deterministic candidate boundary. Without a body-free manifest,
  a later selective-staging step could accidentally include rolling-summary or checkpoint-ingress
  hunks alongside the support-context propagation lines.
- Implemented fix: added `stage_h8r2cb_runtime_propagation_hunk_candidate.py` and focused tests. The
  manifest validates the CA receipt, parses the current `git diff --unified=0` hunk boundaries,
  classifies changed lines with the same stable classes used by CA, and records only body-free hunk
  metadata: file path, old/new ranges, counts, class ids and hashes. Candidate hunks are admitted
  only when all changed lines are `support_context_propagation`; unrelated or mixed hunks are excluded.
- Validation evidence: CB focused **6 passed**; adjacent CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **311 passed**;
  compileall, `git diff --check` and CB receipt body-secret scans passed. Official manifest receipt
  `phase_h8r2cb_runtime_propagation_hunk_candidate_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:6e5c579af9a37bec4c3fb4a80245297f3e5273270e6a3f0cac2146525f8c610e`. The manifest
  records 8 candidate support-context hunks and 6 excluded unrelated hunks. Candidate support-context
  coverage matches CA at 9/9 changed lines; excluded unrelated coverage matches CA at 42/42 changed
  lines; candidate unrelated lines and excluded support-context lines are both zero. Stage/commit/
  push/provider/project/memory side effects stayed zero.
- Remaining limitations: CB provides hunk-candidate boundary evidence only. It does not approve any
  hunk, claim selective-staging readiness, stage files, create or accept a commit, accept the wider
  dirty tree, authorize production prompt-use/default-on Compact, source-body support inclusion,
  OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2CC：Runtime propagation hunk review

- Observed gap: CB identified the 8 support-context propagation candidate hunks, but it deliberately
  stopped before deciding whether those hunks form a single acceptable runtime propagation slice.
  Without that review, a later staging simulation would know the mechanical boundary but not whether
  the slice preserves the intended contract and authority semantics.
- Implemented fix: added `stage_h8r2cc_runtime_propagation_hunk_review.py` and focused tests. The
  review validates the CB receipt, maps the 8 candidate hunks to required semantic roles, verifies
  complete decomposer parse/serialize/prompt-hint, autopilot task-graph and runtime task/node
  propagation coverage, checks that no candidate hunk contains unrelated classes or authority-control
  terms, and keeps the 6 unrelated hunks outside the approved slice. The receipt stores only
  body-free role, range, class and hash metadata.
- Validation evidence: CC focused **6 passed**; adjacent CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **317 passed**;
  compileall, `git diff --check` and CC receipt body-secret scans passed. Official review receipt
  `phase_h8r2cc_runtime_propagation_hunk_review_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:aa1a597371a0a5463570426b18090ea6bd8b53322170b7eddf36582826da5d9d`. The review
  approves 8/8 candidate hunks, finds zero missing/duplicate/unknown roles, records zero
  authority-control hits, and keeps the excluded unrelated classes outside the slice. Stage/commit/
  push/provider/project/memory side effects stayed zero.
- Remaining limitations: CC approves the hunk-level slice for review only. It does not stage files,
  create or accept a commit, accept the wider dirty tree, authorize production prompt-use/default-on
  Compact, source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or
  production reasoning-policy changes.

### Phase H8-R2CD：Selective staging simulation

- Observed gap: CC review-approved the 8 support-context runtime propagation hunks, but it still did
  not prove that those hunks can be isolated as a staged-tree boundary without accidentally including
  the six excluded rolling-summary/checkpoint-ingress hunks. Direct staging would be premature without
  that simulation and without explicit user authorization.
- Implemented fix: added `stage_h8r2cd_selective_staging_simulation.py` and focused tests. The
  simulation validates the CC receipt and its source CB receipt, parses current `git diff --unified=0`,
  selects exactly the 8 approved hunk boundaries, applies them to HEAD content in memory, computes
  per-file simulated hashes plus an aggregate simulated staged-tree hash, checks that no excluded
  hunk was selected, and records the before/after cached-diff name hash to prove the git index was
  not written. The receipt stores only body-free path/count/hash metadata.
- Validation evidence: CD focused **6 passed**; adjacent CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **323 passed**;
  compileall, `git diff --check` and CD receipt body-secret scans passed. Official simulation receipt
  `phase_h8r2cd_selective_staging_simulation_20260810_v1/aggregate/receipt.json` has canonical hash
  `sha256:dcc18f16ca387ac773ed82f49f5c6c803b1aa7fd450e104819ffd80b6ddb46bd`. It records 8/8 selected
  approved hunks, 0/6 selected excluded hunks, 9 support-context additions, 0 deletions, 0 unrelated
  selected lines, 3 simulated files, simulated tree hash
  `sha256:1ecfdc3611ef8fb788d2e342c6b2be02980bd66df8fefd13d0d2e27d51edd32f`, and unchanged cached-diff
  name hashes before/after. Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: CD is still a simulation. It does not stage files, create or accept a commit,
  accept the wider dirty tree, authorize production prompt-use/default-on Compact, source-body
  support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes. Actual selective staging requires explicit user authorization.

### Phase H8-R2CE：Provider projection slice review

- Observed gap: BZ split `support_context_provider_projection` as a single path-level slice, but
  `tool_planning_executor.py` carried 547 changed lines. That was too large and too mixed to treat as
  a support-context provider projection commit without inspecting whether provider-native execution,
  reasoning, budget and telemetry work were mixed into the same file diff.
- Implemented fix: added `stage_h8r2ce_provider_projection_slice_review.py` and focused tests. The
  review validates the BZ receipt, extracts the provider projection slice, classifies the current
  `git diff --unified=0` changed lines into stable support-context and unrelated provider/runtime
  classes, verifies support-context provider projection markers, and rejects the path-level slice
  while requiring hunk/feature-level split. The receipt stores only body-free counts, class ids,
  marker ids and evidence tables.
- Validation evidence: CE focused **6 passed**; adjacent CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **329 passed**;
  compileall, `git diff --check` and CE receipt body-secret scans passed. Official review receipt
  `phase_h8r2ce_provider_projection_slice_review_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:989ae079a08985311355e1863b0388a306fcc007e1a53123bed7a96711022658`. The review records
  23 support-context provider projection lines and 524 unrelated lines across provider budget,
  initial context projection, mutation boundary, native execution, telemetry, reasoning migration,
  runtime state/scope wiring, imports and unclassified provider changes. Stage/commit/push/provider/
  project/memory side effects stayed zero.
- Remaining limitations: CE provides path-level contamination evidence only. It does not approve any
  hunk, stage files, create or accept a commit, accept the wider dirty tree, authorize production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes. Provider projection needs a hunk/feature
  candidate manifest next.

### Phase H8-R2CF：Provider projection candidate manifest

- Observed gap: CE proved the provider projection path-level slice was contaminated, but did not yet
  determine whether a normal hunk-level split could isolate the support-context projection part. A
  direct hunk staging path would be unsafe if support-context lines were embedded inside larger
  provider-native execution hunks.
- Implemented fix: added `stage_h8r2cf_provider_projection_candidate_manifest.py` and focused tests.
  The manifest validates the CE receipt, parses current `git diff --unified=0` hunks for
  `tool_planning_executor.py`, reuses CE's changed-line classifier, and separates pure candidate
  hunks, mixed support-context hunks and unrelated-only hunks. It records body-free hunk ranges,
  class counts, line counts and hashes, and explicitly keeps `ready_for_selective_staging_simulation`
  false when no pure candidate exists.
- Validation evidence: CF focused **6 passed**; adjacent CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **335 passed**;
  compileall, `git diff --check` and CF receipt body-secret scans passed. Official manifest receipt
  `phase_h8r2cf_provider_projection_candidate_manifest_20260810_v1/aggregate/receipt.json` has
  canonical hash `sha256:1bd9e12a2ed2dcdd755f90635725bb62b104526296bfff6ca1ef346b94fdefba`.
  The manifest records 0 pure support-context hunks, 2 mixed support-context hunks and 9
  unrelated-only hunks. All 23 support-context projection lines are inside the two mixed hunks, which
  also contain 492 unrelated lines; the remaining 32 unrelated lines are in unrelated-only hunks.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: CF provides candidate-boundary evidence only. It does not approve any hunk,
  stage files, create or accept a commit, accept the wider dirty tree, authorize production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes. Provider projection now needs a
  line/feature extraction plan before any staging simulation.

### Phase H8-R2CG：Provider projection feature extraction plan

- Observed gap: CF proved that provider projection cannot be isolated by ordinary hunk staging, but
  it did not yet say how to split the feature safely. The support-context helper code and call-site
  wiring were embedded in mixed provider-native execution changes, so a concrete stacked extraction
  plan was needed before any production refactor or staging simulation.
- Implemented fix: added `stage_h8r2cg_provider_projection_extraction_plan.py` and focused tests. The
  planner validates the CF receipt, parses `ToolPlanningTaskExecutor` with AST, records body-free
  function boundaries, classifies extraction units, and emits a recommended stack: provider-native
  execution base first, support-context helper bundle, generic initial-context prompt helper, then
  support-context call-site wiring after the base is accepted. It explicitly blocks direct hunk
  staging and direct selective-staging simulation.
- Validation evidence: CG focused **6 passed**; adjacent CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **341 passed**;
  compileall, `git diff --check` and CG receipt body-secret scans passed. Official extraction receipt
  `phase_h8r2cg_provider_projection_extraction_plan_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:7e5959d6003cf465d7fc9ed1ec89f15b3573bc550b67a0e784983b49c6b42c24`. It records the
  support-context helper bundle as 4 functions / 69 lines, generic initial-context helper as 1
  function / 34 lines, provider-native execution base as 1 function / 402 lines, and a call-site
  support-context feature blocked until that base is accepted. Stage/commit/push/provider/project/
  memory side effects stayed zero.
- Remaining limitations: CG is an extraction plan only. It does not implement production extraction,
  approve any hunk, stage files, create or accept a commit, accept the wider dirty tree, authorize
  production prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider
  proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2CH：Provider execution base review

- Observed gap: CG identified `execute_provider_tool_task(...)` as a provider-native execution base
  prerequisite, but did not determine whether that 402-line base could be accepted as one
  non-support-context package. Accepting it wholesale would risk carrying provider admission,
  budget, mutation permissions, runtime state, prompt setup, roundtrip invocation, telemetry and
  support-context call-site wiring into one review boundary.
- Implemented fix: added `stage_h8r2ch_provider_execution_base_review.py` and focused tests. The
  review validates the CG receipt, parses `ToolPlanningTaskExecutor.execute_provider_tool_task(...)`
  with AST, records body-free function and top-level statement spans, classifies behavior domains,
  and emits a required split stack. The review explicitly keeps `base_single_package_accepted=false`,
  `base_split_required=true`, `support_context_callsite_staging_blocked=true`, and
  `ready_for_selective_staging_simulation=false`.
- Validation evidence: CH focused **6 passed**; adjacent CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/
  BT/provider-roundtrip/runtime-session/metadata/execution-planning regression **347 passed**;
  compileall, `git diff --check` and CH receipt/doc body-secret scans passed. Official review receipt
  `phase_h8r2ch_provider_execution_base_review_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:23d05164c83c99e7e6dc42abd3a5c1a89e2760520638bf917882b588ceb92a16`. It records 8
  required behavior domains, 35 top-level statements, 10 mixed-domain statements and 2 large
  mixed-domain statements. The recommended stack is admission/budget, mutation scope boundary,
  runtime/prompt setup, roundtrip invocation, result telemetry/completion mapping, then
  support-context call-site after provider base pieces are independently reviewed.
- Remaining limitations: CH is a review/diagnosis stage only. It does not accept any provider base
  package, implement production extraction, approve support-context call-site staging, approve any
  hunk, stage files, create or accept a commit, accept the wider dirty tree, authorize production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2CI：Provider admission/budget review

- Observed gap: CH recommended `provider_admission_budget_policy` as the first provider base
  package, but that recommendation still needed proof that admission/budget was cohesive enough to
  accept as one package. A broad admission/budget package could still smuggle mutation-profile
  guards, mutation capability detection, telemetry, roundtrip or support-context call-site behavior
  into the first provider-base review boundary.
- Implemented fix: added `stage_h8r2ci_provider_admission_budget_review.py` and focused tests. The
  review validates the CH receipt, reuses CH's body-free top-level statement classification, filters
  statements containing `provider_admission` or `budget_policy`, separates pure admission/budget
  statements from mutation-mixed and forbidden-domain mixed statements, and emits a smaller split
  stack. The review explicitly keeps `provider_admission_budget_policy_accepted=false`,
  `admission_budget_split_required=true`, and `support_context_callsite_staging_blocked=true`.
- Validation evidence: CI focused **6 passed**; adjacent CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/
  BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning regression **353 passed**;
  compileall, `git diff --check` and CI receipt/doc body-secret scans passed. Official review receipt
  `phase_h8r2ci_provider_admission_budget_review_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:3512c2de9ab92b8420c0001676701ba4eb51169822f5957076e656b98d5a0bf8`. It records 16
  admission/budget target statements / 286 lines, 10 pure statements / 48 lines, 5 mutation-mixed
  statements and 3 forbidden-domain mixed statements. The next candidate package is
  `provider_entry_flag_and_budget_profile_core`; mutation-profile guards, tool registry allowlist
  and mutation capability detection remain separate.
- Remaining limitations: CI is a review/diagnosis stage only. It does not accept any provider base
  package, implement production extraction, approve support-context call-site staging, approve any
  hunk, stage files, create or accept a commit, accept the wider dirty tree, authorize production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2CJ：Provider entry/budget core review

- Observed gap: CI identified `provider_entry_flag_and_budget_profile_core` as the next smaller
  package, but it had not yet proven whether that core was independently acceptable. The selected
  core could still have hidden dependencies on local helper contracts, especially the provider
  failure-result branch used by entry/admission and budget validation failures.
- Implemented fix: added `stage_h8r2cj_provider_entry_budget_core_review.py` and focused tests. The
  review validates the CI receipt, selects the narrow pure statement subset
  `stmt-04/05/07/08/11/12/13/14`, verifies that selected statements contain only
  `provider_admission` and `budget_policy` domains, excludes unrelated pure statements, and records
  the unresolved helper dependency on `stmt-06`. The review explicitly keeps
  `entry_budget_core_package_accepted=false`, `entry_budget_core_split_required=true`, and
  `next_candidate_package_id=provider_failure_result_helper_contract`.
- Validation evidence: CJ focused **6 passed**; adjacent CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/
  BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning regression **359 passed**;
  compileall, `git diff --check` and CJ receipt/doc body-secret scans passed. Official review receipt
  `phase_h8r2cj_provider_entry_budget_core_review_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:c173d9d86241b241f285b340cbb84f1c59c108aac4fb8fce6851e81715b42dc8`. It records 8
  selected statements / 44 lines, selected domains `provider_admission` and `budget_policy`, zero
  forbidden domain hits, excluded pure statements `stmt-20` and `stmt-32`, and unresolved
  `stmt-06` helper dependency with domains `provider_admission` and `result_telemetry`.
- Remaining limitations: CJ is a review/diagnosis stage only. It does not accept any provider base
  package, implement production extraction, approve support-context call-site staging, approve any
  hunk, stage files, create or accept a commit, accept the wider dirty tree, authorize production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2CK：Provider failure-result helper review

- Observed gap: CJ found that the narrow `provider_entry_flag_and_budget_profile_core` candidate was
  clean by domain, but its admission/budget error branches still depended on local
  `failure_result(...)` helper semantics. Accepting the core before reviewing that helper would risk
  smuggling result telemetry, recovery semantics or body-carrying failure evidence into the first
  provider-base package.
- Implemented fix: added `stage_h8r2ck_provider_failure_helper_review.py` and focused tests. The
  review validates the CJ receipt, locates `stmt-06` / `failure_result(...)` in
  `ToolPlanningTaskExecutor.execute_provider_tool_task(...)`, accounts for positional and keyword-only
  signature arguments, verifies the typed return contract, fail-closed statuses, body-free
  attributes and forbidden-marker absence, and emits the next candidate as
  `provider_entry_flag_and_budget_profile_core`. The receipt remains body-free and records only
  helper shape, type/status ids, attribute keys, hashes and side-effect counters.
- Validation evidence: CK focused **6 passed**; adjacent CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/
  BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning regression **365
  passed**; compileall, `git diff --check` and CK receipt/doc body-secret scans passed. Official
  review receipt `phase_h8r2ck_provider_failure_helper_review_20260810_v1/aggregate/receipt.json`
  has canonical hash `sha256:6f7bbb93d3b4886691b6824bb27d7a0fff25aa20e743b84d70a78d10d674b161`.
  The review records a 21-line helper returning `TaskExecutionResult`, typed constructs
  `FailureMetadata`/`TaskResultMetadata`, fail-closed `TaskStatus.FAILED`/`ResultStatus.FAIL`,
  body-free provider-execution attributes and zero forbidden marker hits. Stage/commit/push/provider/
  project/memory side effects stayed zero.
- Remaining limitations: CK review-approves only the helper prerequisite and resolves CJ's helper
  dependency at review level. It does not accept the entry/budget core, accept any provider base
  package, implement production extraction, approve support-context call-site staging, approve any
  hunk, stage files, create or accept a commit, accept the wider dirty tree, authorize production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2CL：Provider entry/budget core acceptance review

- Observed gap: CK resolved the failure-helper dependency, but the earlier CJ receipt still had to be
  joined with that CK proof before `provider_entry_flag_and_budget_profile_core` could be
  review-approved. Without this dependency join, later provider-base work would rely on an informal
  assumption that the helper blocker had been cleared.
- Implemented fix: added `stage_h8r2cl_provider_entry_budget_core_acceptance.py` and focused tests.
  The review validates both CJ and CK receipts, joins CJ's selected 8 body-free core statements with
  CK's helper dependency-resolution gate, verifies the selected domains remain limited to
  `provider_admission` and `budget_policy`, verifies zero forbidden/unexpected domains, keeps
  `stmt-20`/`stmt-32` outside the core, and emits `provider_mutation_scope_boundary` as the next
  candidate. The receipt records review approval only; it explicitly keeps commit acceptance,
  production extraction, selective staging readiness and support-context call-site staging false.
- Validation evidence: CL focused **7 passed**; adjacent CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/
  BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning regression **372
  passed**; compileall and `git diff --check` passed. Official review receipt
  `phase_h8r2cl_provider_entry_budget_core_acceptance_20260810_v1/aggregate/receipt.json` has
  canonical hash `sha256:327fde6670a1771461552db8b3621825d9e2c8d36ddefffb9c3911da735f3fda`. It
  records 8 selected statements / 44 lines, selected domains `provider_admission` and
  `budget_policy`, zero forbidden/unexpected domain hits, CK helper resolution true and
  `entry_budget_core_commit_accepted=false`. Stage/commit/push/provider/project/memory side effects
  stayed zero.
- Remaining limitations: CL review-approves only the entry/budget core prerequisite. It does not
  accept a commit, implement production extraction, approve the mutation scope boundary, approve
  runtime/prompt setup, roundtrip invocation, telemetry/completion mapping, support-context call-site
  staging, any hunk, any file staging, the wider dirty tree, production prompt-use/default-on Compact,
  source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2CM：Provider mutation scope boundary review

- Observed gap: CL made `provider_mutation_scope_boundary` the next provider-base candidate, but CH
  showed mutation-boundary logic spread across pure guards, budget/admission dependencies and large
  runtime/roundtrip/telemetry/support-context mixed statements. Accepting the whole mutation boundary
  package would blur permission checks with provider invocation and result-output behavior.
- Implemented fix: added `stage_h8r2cm_provider_mutation_scope_boundary_review.py` and focused tests.
  The review validates CH and CL receipts, verifies CL's entry/budget core prerequisite, extracts all
  CH statements carrying `mutation_boundary`, classifies pure mutation, dependency-mixed,
  budget-mixed, admission-mixed, forbidden-mixed and large-mixed statements, rejects the single
  mutation-scope package, and emits `provider_budget_mutation_profile_guards` as the next smaller
  candidate.
- Validation evidence: CM focused **7 passed**; adjacent CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/
  BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning regression **379
  passed**; compileall and `git diff --check` passed. Official review receipt
  `phase_h8r2cm_provider_mutation_scope_boundary_review_20260810_v1/aggregate/receipt.json` has
  canonical hash `sha256:2efaf32c356ff1d8dc520f2ac87152540c19518f0c30ed596b4deb28fafe0afd`. It
  records 8 mutation target statements / 235 lines, 3 pure mutation statements / 18 lines, 5
  dependency-mixed statements, 2 forbidden-mixed statements, 2 large mixed statements and
  `mutation_scope_boundary_package_review_approved=false`. Stage/commit/push/provider/project/memory
  side effects stayed zero.
- Remaining limitations: CM is a review/diagnosis stage only. It does not approve the mutation
  scope boundary package, budget mutation profile guards, projection mutation guard, tool capability
  detection, mutation confirmation gate, production extraction, support-context call-site staging,
  any hunk, any file staging, any accepted commit, the wider dirty tree, production prompt-use/
  default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad real-task
  evidence or production reasoning-policy changes.

### Phase H8-R2CN：Provider budget mutation profile guards

- Observed gap: CM identified `provider_budget_mutation_profile_guards` as the next smaller candidate
  but did not yet prove that the two budget/mutation profile guards could be accepted independently.
  Without a dedicated dependency join, later mutation-boundary work would rely on an informal
  assumption that `stmt-09` and `stmt-10` are safe to carry forward.
- Implemented fix: added `stage_h8r2cn_provider_budget_mutation_guards.py` and focused tests. The
  review validates CM, CL and CK receipts, verifies CM points to the target package, verifies the
  entry/budget core and failure helper prerequisites, selects only `stmt-09` and `stmt-10`, checks
  that the selected domains are exactly `budget_policy` and `mutation_boundary`, rejects forbidden
  carry domains, and emits `provider_initial_context_mutation_projection_guard` as the next candidate.
- Validation evidence: CN focused **7 passed**; adjacent CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/
  BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning regression
  **386 passed**; compileall and `git diff --check` passed. Official review receipt
  `phase_h8r2cn_provider_budget_mutation_guards_20260810_v1/aggregate/receipt.json` has canonical
  hash `sha256:1dd3c7ddb4ee14ddd9372bb72c6d441cc0c0bd99db3d7fa89786bc29344706ef`. It records 2
  selected statements / 10 lines, selected domains `budget_policy` and `mutation_boundary`, zero
  forbidden/unexpected domain hits, dependency on the entry/budget core plus failure helper, and
  `budget_mutation_guards_commit_accepted=false`. Stage/commit/push/provider/project/memory side
  effects stayed zero.
- Remaining limitations: CN review-approves only the budget/mutation profile guard prerequisite. It
  does not approve the broader mutation scope boundary, projection mutation guard, tool capability
  detection, mutation confirmation gate, production extraction, support-context call-site staging,
  any hunk, any file staging, any accepted commit, the wider dirty tree, production prompt-use/
  default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad real-task
  evidence or production reasoning-policy changes.

### Phase H8-R2CO：Provider initial-context mutation projection guard review

- Observed gap: CN made `provider_initial_context_mutation_projection_guard` the next candidate, but
  `stmt-19` uses projection setup state produced by earlier statements. Accepting `stmt-19` alone
  would hide dependencies on projection flag lookup and support-context request detection, which is
  exactly the kind of permission/context coupling this provider-base split is intended to prevent.
- Implemented fix: added `stage_h8r2co_provider_initial_context_mutation_projection_guard.py` and
  focused tests. The review validates CH, CM and CN receipts, verifies CN's prerequisite approval,
  AST-inspects `ToolPlanningTaskExecutor.execute_provider_tool_task(...)` without serializing source
  bodies, confirms `stmt-19` is CM-classified pure mutation, derives setup dependencies
  `stmt-15`–`stmt-18`, records support-context dependencies `stmt-17`/`stmt-18` and
  roundtrip-adjacent dependency `stmt-18`, rejects the standalone projection guard package, and emits
  `provider_initial_context_projection_guard_dependencies` as the next candidate.
- Validation evidence: CO focused **8 passed**; adjacent CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/
  CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning regression
  **394 passed**; compileall and `git diff --check` passed. Official review receipt
  `phase_h8r2co_provider_initial_context_mutation_projection_guard_20260810_v1/aggregate/receipt.json`
  has canonical hash `sha256:3e866422d1226c675ea73c17ce41a2b0296b5b4475f95afbd99dea0d423ac507`.
  It records target `stmt-19` / 11 lines / `mutation_boundary`, unreviewed setup dependencies
  `stmt-15`–`stmt-18`, support-context dependencies `stmt-17`/`stmt-18`, roundtrip-adjacent dependency
  `stmt-18`, and `initial_context_mutation_projection_guard_package_review_approved=false`.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: CO is a review/diagnosis stage only. It does not approve the projection
  guard package, projection setup dependencies, support-context request detection, production
  extraction, support-context call-site staging, any hunk, any file staging, any accepted commit, the
  wider dirty tree, production prompt-use/default-on Compact, source-body support inclusion,
  OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2CP：Provider initial-context projection guard dependencies review

- Observed gap: CO identified setup dependencies `stmt-15`–`stmt-18`, but that dependency set still
  mixed two different concerns: neutral projection flag lookup and support-context/request-detection
  logic. Accepting that set as one package would again bind context projection setup to
  support-context call-site behavior.
- Implemented fix: added `stage_h8r2cp_provider_initial_context_projection_dependencies.py` and
  focused tests. The review validates CH and CO receipts, selects `stmt-15`–`stmt-18`, records
  body-free assigned-name metadata, classifies `stmt-15`/`stmt-16` as flag lookup,
  `stmt-17`/`stmt-18` as support-context request detection, and `stmt-18` as roundtrip-adjacent. It
  rejects the single dependency package and emits `provider_initial_context_projection_flag_lookup_core`
  as the next candidate.
- Validation evidence: CP focused **7 passed**; adjacent CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/
  CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning
  regression **401 passed**; compileall and `git diff --check` passed. Official review receipt
  `phase_h8r2cp_provider_initial_context_projection_dependencies_20260810_v1/aggregate/receipt.json`
  has canonical hash `sha256:ee5c68620516c967564e919293b663217e48c97b15c8a1867e26351d5687885d`.
  It records 4 selected statements / 10 lines, flag lookup statements `stmt-15`/`stmt-16`,
  support-context request-detection statements `stmt-17`/`stmt-18`, roundtrip-adjacent statement
  `stmt-18`, and `projection_dependencies_package_review_approved=false`. Stage/commit/push/
  provider/project/memory side effects stayed zero.
- Remaining limitations: CP is a review/diagnosis stage only. It does not approve the projection
  dependencies package, projection flag lookup core, support-context request detection package,
  initial-context mutation projection guard, production extraction, support-context call-site
  staging, any hunk, any file staging, any accepted commit, the wider dirty tree, production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2CQ：Provider initial-context projection flag lookup core

- Observed gap: CP correctly split `stmt-15`/`stmt-16` from `stmt-17`/`stmt-18`, but the neutral
  flag lookup pair still needed its own review-approved package before support-context request
  detection or mutation projection guard work could safely build on it.
- Implemented fix: added `stage_h8r2cq_provider_initial_context_projection_flag_lookup.py` and
  focused tests. The review validates CH and CP receipts, verifies CP points to
  `provider_initial_context_projection_flag_lookup_core`, selects only `stmt-15` and `stmt-16`,
  records body-free assigned-name metadata, rejects support-context/roundtrip assigned names and
  forbidden domains, and emits `provider_support_context_request_detection` as the next candidate.
- Validation evidence: CQ focused **7 passed**; adjacent CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/
  CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning
  regression **408 passed**; compileall and `git diff --check` passed. Official review receipt
  `phase_h8r2cq_provider_initial_context_projection_flag_lookup_20260810_v1/aggregate/receipt.json`
  has canonical hash `sha256:d8f101f7fe9c78f2a597458e89170e36d31ff9e6fcd1bd46f469650df2872fcd`.
  It records 2 selected statements / 8 lines, no selected domains, assigned names
  `projection_flag` and `mutation_projection_flag`, and
  `projection_flag_lookup_package_review_approved=true` with
  `projection_flag_lookup_commit_accepted=false`. Stage/commit/push/provider/project/memory side
  effects stayed zero.
- Remaining limitations: CQ review-approves only the neutral flag lookup prerequisite. It does not
  approve support-context request detection, the initial-context mutation projection guard,
  production extraction, support-context call-site staging, any hunk, any file staging, any accepted
  commit, the wider dirty tree, production prompt-use/default-on Compact, source-body support
  inclusion, OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy
  changes.

### Phase H8-R2CR：Provider support-context request detection

- Observed gap: CQ made `provider_support_context_request_detection` the next candidate, but
  `stmt-18` still carries a mixed `roundtrip_invocation` classification. Accepting the package
  without an explicit adjacency boundary would blur request detection with provider roundtrip
  invocation and support-context call-site readiness.
- Implemented fix: added `stage_h8r2cr_provider_support_context_request_detection.py` and focused
  tests. The review validates CH, CP and CQ receipts, verifies CP split `stmt-17`/`stmt-18` as
  support-context request detection, verifies CQ points to the package, selects only `stmt-17` and
  `stmt-18`, records body-free domain/assigned-name/direct-call metadata, accepts `stmt-18`'s
  `roundtrip_invocation` only as adjacency, and emits
  `provider_initial_context_mutation_projection_guard` as the next candidate.
- Validation evidence: CR focused **7 passed**; adjacent CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/
  CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning
  regression **415 passed**; compileall and `git diff --check` passed. Official review receipt
  `phase_h8r2cr_provider_support_context_request_detection_20260810_v1/aggregate/receipt.json`
  has canonical hash `sha256:efd48d9ac46d012ca678570c164b81f686962c0d56cb93f3aacf2d7fc7209c5a`.
  It records 2 selected statements / 2 lines, domains `support_context_projection` and
  adjacency-only `roundtrip_invocation`, assigned names `task_support_context_files` and
  `projected_context_requested`, direct calls `self._task_support_context_files` and `bool`, and
  `support_context_request_detection_package_review_approved=true` with
  `support_context_request_detection_commit_accepted=false`. Stage/commit/push/provider/project/
  memory side effects stayed zero.
- Remaining limitations: CR review-approves only request detection. It does not approve the
  initial-context mutation projection guard, support-context candidate construction, provider
  roundtrip invocation, production extraction, support-context call-site staging, any hunk, any file
  staging, any accepted commit, the wider dirty tree, production prompt-use/default-on Compact,
  source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2CS：Provider initial-context mutation projection guard acceptance

- Observed gap: CO had already shown `stmt-19` was a pure mutation-boundary guard, but rejected the
  package because `stmt-15`–`stmt-18` setup dependencies were unresolved. After CQ and CR split and
  approved those dependencies, the guard needed a fresh acceptance review that did not accidentally
  accept support-context construction, provider roundtrip, telemetry or staging.
- Implemented fix: added `stage_h8r2cs_provider_initial_context_mutation_projection_guard_acceptance.py`
  and focused tests. The review validates CH, CM, CN, CO, CQ and CR receipts; verifies CO's previous
  setup-dependency blocker; verifies CQ approved flag lookup and CR approved request detection;
  selects only `stmt-19`; records body-free domain, loaded-name, assignment, direct-call and control
  count metadata; and emits `provider_mutation_tool_capability_detection` as the next candidate.
- Validation evidence: CS focused **7 passed**; adjacent CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/
  CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning
  regression **422 passed**; compileall and `git diff --check` passed. Official review receipt
  `phase_h8r2cs_provider_initial_context_mutation_projection_guard_acceptance_20260810_v1/aggregate/receipt.json`
  has canonical hash `sha256:d8e64236686d45929f355172768715afb92c6c1b3ffcbbefd20c35c96610c575`.
  It records selected `stmt-19` / 11 lines / `mutation_boundary`, no assignments, direct call
  `failure_result`, two return statements, three if statements, setup dependencies resolved by CQ/CR,
  and `initial_context_mutation_projection_guard_package_review_approved=true` with
  `initial_context_mutation_projection_guard_commit_accepted=false`. Stage/commit/push/provider/
  project/memory side effects stayed zero.
- Remaining limitations: CS review-approves only the mutation projection guard. It does not approve
  mutation tool capability detection, mutation confirmation gate, support-context candidate
  construction, provider roundtrip invocation, result telemetry/completion mapping, production
  extraction, support-context call-site staging, any hunk, any file staging, any accepted commit, the
  wider dirty tree, production prompt-use/default-on Compact, source-body support inclusion,
  OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2CT：Provider mutation tool capability detection dependency review

- Observed gap: CS correctly advanced to `provider_mutation_tool_capability_detection`, but `stmt-23`
  reads `registry`, whose owner is `stmt-20` / `stmt-21`. CI had already listed
  `provider_tool_allowlist_registry_core` as a separate candidate, and CL explicitly excluded
  `stmt-20` from the accepted entry/budget core. Accepting `stmt-22` / `stmt-23` now would therefore
  implicitly accept registry authority.
- Implemented fix: added `stage_h8r2ct_provider_mutation_tool_capability_detection_dependency.py` and
  focused tests. The review validates CH, CI, CL, CM and CS receipts; verifies CM's candidate
  statements are `stmt-22` / `stmt-23`; verifies CS points to the package; records body-free target
  and dependency metadata; detects that `registry` is assigned by `stmt-20`, guarded by `stmt-21`,
  and unresolved; and emits `provider_tool_allowlist_registry_core` as the next candidate.
- Validation evidence: CT focused **8 passed**; adjacent CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/
  CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/
  execution-planning regression **430 passed**; compileall and `git diff --check` passed. Official
  review receipt
  `phase_h8r2ct_provider_mutation_tool_capability_detection_dependency_20260810_v1/aggregate/receipt.json`
  has canonical hash `sha256:4fbb61e3a80e4a9f9bf993e0e9e7740a8624337926448ed14b3145ef72eb9a83`.
  It records selected `stmt-22` / `stmt-23` / 6 lines, unresolved dependency `stmt-20` / `stmt-21`
  / 3 lines, `registry_dependency_blocker_found=true`,
  `mutation_tool_capability_detection_package_review_approved=false`, and
  `next_candidate_package_id=provider_tool_allowlist_registry_core`. Stage/commit/push/provider/
  project/memory side effects stayed zero.
- Remaining limitations: CT is a review/diagnosis stage only. It does not approve mutation tool
  capability detection, registry core, mutation confirmation gate, provider roundtrip invocation,
  result telemetry/completion mapping, production extraction, support-context call-site staging, any
  hunk, any file staging, any accepted commit, the wider dirty tree, production prompt-use/default-on
  Compact, source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or
  production reasoning-policy changes.

### Phase H8-R2CU：Provider tool allowlist registry core

- Observed gap: CT proved `stmt-22` / `stmt-23` could not be accepted before `registry` authority was
  reviewed. The unresolved owner was `stmt-20` / `stmt-21`, with `stmt-21` also depending on the
  CK-approved `failure_result(...)` helper.
- Implemented fix: added `stage_h8r2cu_provider_tool_allowlist_registry_core.py` and focused tests.
  The review validates CH, CI, CK, CL and CT receipts; verifies CT points to registry core; verifies
  CI/CL kept registry core out of the earlier entry/budget package; verifies CK approved the helper;
  selects only `stmt-20` / `stmt-21`; records body-free domain/assigned-name/loaded-name/direct-call
  and control-count metadata; and emits `provider_mutation_tool_capability_detection` as the next
  candidate.
- Validation evidence: CU focused **7 passed**; adjacent CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/
  CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/
  execution-planning regression **437 passed**; compileall and `git diff --check` passed. Official
  review receipt
  `phase_h8r2cu_provider_tool_allowlist_registry_core_20260810_v1/aggregate/receipt.json`
  has canonical hash `sha256:732d309e5a49ca9723563005978a487d872bd12c636b116ce2f1dbde27e32cf8`.
  It records selected `stmt-20` / `stmt-21` / 3 lines, `stmt-20` domain `provider_admission`,
  `stmt-20` assignment `registry`, `stmt-21` direct call `failure_result`,
  `tool_allowlist_registry_core_package_review_approved=true`, and
  `tool_allowlist_registry_core_commit_accepted=false`. Stage/commit/push/provider/project/memory
  side effects stayed zero.
- Remaining limitations: CU review-approves only the registry core. It does not approve mutation
  tool capability detection, mutation confirmation gate, provider roundtrip invocation, result
  telemetry/completion mapping, production extraction, support-context call-site staging, any hunk,
  any file staging, any accepted commit, the wider dirty tree, production prompt-use/default-on
  Compact, source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or
  production reasoning-policy changes.

### Phase H8-R2CV：Provider mutation tool capability detection acceptance

- Observed gap: CT had already shown `stmt-22` / `stmt-23` were the correct capability-detection
  shape, but rejected the package because `stmt-23` reads `registry`. After CU review-approved
  `provider_tool_allowlist_registry_core` and CL had already review-approved the `normalized_tools`
  setup, the capability-detection package needed a fresh acceptance review that did not accidentally
  accept mutation confirmation, provider roundtrip, telemetry or staging.
- Implemented fix: added `stage_h8r2cv_provider_mutation_tool_capability_detection_acceptance.py`
  and focused tests. The review validates CH, CL, CM, CS, CT and CU receipts; verifies CT's blocker
  was resolved by CU; verifies CL's normalized-tools dependency; selects only `stmt-22` / `stmt-23`;
  records body-free domain, loaded-name, assignment, direct-call and control-count metadata; and
  emits `provider_mutation_confirmation_gate` as the next candidate.
- Validation evidence: CV focused **7 passed**; adjacent CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/
  CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/
  execution-planning regression **444 passed**; compileall and `git diff --check` passed. Official
  review receipt
  `phase_h8r2cv_provider_mutation_tool_capability_detection_acceptance_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:39839bbee500e8592adeea353385577a1db10e58e07ec9878b1ac48ff7fb9a93`.
  It records selected `stmt-22` / `stmt-23` / 6 lines, domains limited to `mutation_boundary` and
  `provider_admission`, assignments limited to `mutation_tools`, `capabilities`, `definition` and
  `tool_name`, direct calls limited to `getattr`, `hasattr`, `mutation_tools.append`,
  `registry.get` and `set`, `mutation_tool_capability_detection_package_review_approved=true`, and
  `mutation_tool_capability_detection_commit_accepted=false`. Stage/commit/push/provider/project/
  memory side effects stayed zero.
- Remaining limitations: CV review-approves only mutation tool capability detection. It does not
  approve mutation confirmation gate, provider roundtrip invocation, result telemetry/completion
  mapping, production extraction, support-context call-site staging, any hunk, any file staging, any
  accepted commit, the wider dirty tree, production prompt-use/default-on Compact, source-body
  support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2CW：Provider mutation confirmation gate

- Observed gap: CV review-approved `provider_mutation_tool_capability_detection` and pointed to
  `provider_mutation_confirmation_gate`, but `stmt-24` still needed a separate review because it is
  a permission boundary: mutation-capable provider tools must fail closed unless the typed task
  carries both `allow_mutations` and `user_confirmed=True`.
- Implemented fix: added `stage_h8r2cw_provider_mutation_confirmation_gate.py` and focused tests.
  The review validates CH, CK, CM and CV receipts; verifies CV accepted capability detection;
  verifies CK accepted the `failure_result(...)` helper; verifies CM lists `stmt-24` as the
  standalone confirmation-gate candidate; selects only `stmt-24`; records body-free domain,
  loaded-name, assignment, direct-call and control-count metadata; and emits
  `provider_runtime_state_and_prompt_setup` as the next candidate.
- Validation evidence: CW focused **7 passed**; adjacent CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/
  CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/
  execution-planning regression **451 passed**; compileall and `git diff --check` passed. Official
  review receipt
  `phase_h8r2cw_provider_mutation_confirmation_gate_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:e1699345af8861556746baa4aadc8e3968bf52503f6a7f8505711a8948bb1157`.
  It records selected `stmt-24` / 6 lines / `mutation_boundary`, no assignments, loaded names
  limited to `allow_mutations`, `failure_result`, `mutation_tools` and `user_confirmed`, direct call
  limited to `failure_result`, `mutation_confirmation_gate_package_review_approved=true`, and
  `mutation_confirmation_gate_commit_accepted=false`. Stage/commit/push/provider/project/memory
  side effects stayed zero.
- Remaining limitations: CW review-approves only the mutation confirmation gate. It does not approve
  provider roundtrip invocation, result telemetry/completion mapping, production extraction,
  support-context call-site staging, any hunk, any file staging, any accepted commit, the wider dirty
  tree, production prompt-use/default-on Compact, source-body support inclusion,
  OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2CX：Provider runtime/prompt setup split review

- Observed gap: CW pointed to `provider_runtime_state_and_prompt_setup`, but CH's top-level evidence
  shows the apparent candidate is `stmt-25`, a 141-line `try` block carrying 8 domains. Accepting it
  directly would over-accept provider admission, budget, mutation, provider roundtrip, telemetry and
  support-context projection behavior together with runtime/prompt setup.
- Implemented fix: added `stage_h8r2cx_provider_runtime_prompt_setup_split.py` and focused tests.
  The review validates CH and CW receipts; verifies CW points to the runtime/prompt setup package;
  rejects direct acceptance of `stmt-25`; extracts first-level try-body and exception-handler
  metadata without source bodies; proves full inner coverage; and emits
  `provider_tool_definition_construction_core` as the next candidate.
- Validation evidence: CX focused **7 passed**; adjacent CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/
  CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/
  execution-planning regression **458 passed**; compileall and `git diff --check` passed. Official
  review receipt
  `phase_h8r2cx_provider_runtime_prompt_setup_split_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:f17b4a41aedc4201ce3c78e3bed2c98469efa89f687b85b3e384b12cc402b9d2`.
  It records selected `stmt-25` / 141 lines / 8 domains, `runtime_prompt_setup_single_package_review_approved=false`,
  `runtime_prompt_setup_split_required=true`, 27 try-body statements, 1 handler, 140 inner covered
  lines, one structural `try:` line gap, zero unassigned/unknown/duplicate split IDs, and
  `next_candidate_package_id=provider_tool_definition_construction_core`. Stage/commit/push/provider/
  project/memory side effects stayed zero.
- Remaining limitations: CX is a split review only. It does not approve runtime/prompt setup, tool
  definition construction, provider roundtrip invocation, result telemetry/completion mapping,
  production extraction, support-context call-site staging, any hunk, any file staging, any accepted
  commit, the wider dirty tree, production prompt-use/default-on Compact, source-body support
  inclusion, OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy
  changes.

### Phase H8-R2CY：Provider tool definition construction core

- Observed gap: CX split `stmt-25` and selected `provider_tool_definition_construction_core` as the
  next candidate, but that inner statement still needed its own acceptance review because it depends
  on previously approved `normalized_tools` and registry semantics and must not pull in prompt,
  roundtrip, support-context or telemetry behavior.
- Implemented fix: added `stage_h8r2cy_provider_tool_definition_construction.py` and focused tests.
  The review validates CH, CL, CU and CX receipts; verifies CL accepted `stmt-13` / `stmt-14`;
  verifies CU accepted `stmt-20` / `stmt-21`; verifies CX selected `stmt-25.try-01`; rechecks the
  current AST; selects only `stmt-25.try-01`; records body-free line, node, assigned-name,
  loaded-name, direct-call, assignment-target and call-argument metadata; and emits
  `provider_context_goal_project_scope_setup` as the next candidate.
- Validation evidence: CY focused **7 passed**; adjacent CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/
  CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/
  metadata/execution-planning regression **465 passed**; compileall and `git diff --check` passed.
  Official review receipt
  `phase_h8r2cy_provider_tool_definition_construction_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:203fa317f86c39279bf7a94615e1803fdb6364f7cf0c5eee7b76f92e0d35a9e3`.
  It records selected `stmt-25.try-01` / 1 line / `Assign`, assigned name `tools`, loaded names
  `build_provider_tool_definitions`, `normalized_tools` and `registry`, direct call
  `build_provider_tool_definitions`, value args `registry` and `normalized_tools`,
  `tool_definition_construction_package_review_approved=true`, and
  `tool_definition_construction_commit_accepted=false`. Stage/commit/push/provider/project/memory
  side effects stayed zero.
- Remaining limitations: CY review-approves only tool definition construction. It does not approve
  context/goal/project/scope setup, prompt construction, runtime controller setup,
  support-context call-site staging, provider roundtrip invocation, result telemetry/completion
  mapping, production extraction, any hunk, any file staging, any accepted commit, the wider dirty
  tree, production prompt-use/default-on Compact, source-body support inclusion,
  OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2CZ：Provider context/goal/project/scope setup

- Observed gap: CY pointed to `provider_context_goal_project_scope_setup`, but those assignments sit
  immediately before mutation scope guards. They needed a separate acceptance review to avoid
  treating input/scope projection as permission validation.
- Implemented fix: added `stage_h8r2cz_provider_context_goal_scope_setup.py` and focused tests. The
  review validates CX and CY receipts; verifies CY points to the target package; verifies CX selected
  exactly `stmt-25.try-02` through `stmt-25.try-05`; rechecks the current AST; selects only those
  four inner statements; records body-free line, node, assigned-name, loaded-name, direct-call,
  assignment-target and value-shape metadata; and emits `provider_mutation_scope_runtime_guards` as
  the next candidate.
- Validation evidence: CZ focused **7 passed**; adjacent CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/
  CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/
  metadata/execution-planning regression **472 passed**; compileall and `git diff --check` passed.
  Official review receipt
  `phase_h8r2cz_provider_context_goal_scope_setup_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:014453ff62a4d0626794a9cbfb6bb03dfb9f4309bbf481fd2a5d6c03bbe671a1`.
  It records selected `stmt-25.try-02` / `stmt-25.try-03` / `stmt-25.try-04` / `stmt-25.try-05`,
  8 lines total, assigned names `goal`, `project_path`, `read_scope` and `write_scope`,
  `context_goal_scope_setup_package_review_approved=true`, and
  `context_goal_scope_setup_commit_accepted=false`. Stage/commit/push/provider/project/memory side
  effects stayed zero.
- Remaining limitations: CZ review-approves only input/scope projection. It does not approve
  mutation scope guard, prompt construction, runtime controller setup, support-context call-site
  staging, provider roundtrip invocation, result telemetry/completion mapping, production extraction,
  any hunk, any file staging, any accepted commit, the wider dirty tree, production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2DA：Provider mutation scope runtime guards

- Observed gap: CZ review-approved input/scope projection and pointed to
  `provider_mutation_scope_runtime_guards`, but the adjacent permission checks still needed their own
  acceptance review. Without a separate DA gate, input projection could be mistaken for permission
  validation, or permission validation could accidentally approve runtime controller, prompt,
  provider roundtrip or support-context call-site behavior.
- Implemented fix: added `stage_h8r2da_provider_mutation_scope_runtime_guards.py` and focused tests.
  The review validates CK, CX and CZ receipts; verifies CK accepted the `failure_result(...)` helper;
  verifies CZ points to the target package; verifies CX selected exactly `stmt-25.try-06` and
  `stmt-25.try-07`; rechecks the current AST; selects only those two inner statements; records
  body-free line, node, loaded-name, direct-call, control-count, error-type and task-kind allowlist
  metadata; and emits `provider_runtime_controller_state_setup` as the next candidate.
- Validation evidence: DA focused **7 passed**; adjacent DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/
  CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/
  metadata/execution-planning regression **479 passed**; compileall and `git diff --check` passed.
  Official review receipt
  `phase_h8r2da_provider_mutation_scope_runtime_guards_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:011ade9852945f83da7b48d9292b96db00f08cfeb7bc360fbbb33b8df9f6dac0`.
  It records selected `stmt-25.try-06` / `stmt-25.try-07`, 10 lines total, node type `If`, no
  assignments, typed error IDs `ProviderTaskScopeMissing` and `ProviderMutationTaskKindInvalid`,
  `mutation_scope_runtime_guards_package_review_approved=true`, and
  `mutation_scope_runtime_guards_commit_accepted=false`. Stage/commit/push/provider/project/memory
  side effects stayed zero.
- Remaining limitations: DA review-approves only mutation scope/task-kind fail-closed guards. It does
  not approve runtime controller setup, prompt construction, support-context call-site staging,
  provider roundtrip invocation, result telemetry/completion mapping, production extraction, any
  hunk, any file staging, any accepted commit, the wider dirty tree, production prompt-use/default-on
  Compact, source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or
  production reasoning-policy changes.

### Phase H8-R2DB：Provider runtime controller state setup

- Observed gap: DA review-approved mutation scope/task-kind guards and pointed to
  `provider_runtime_controller_state_setup`, but the next `stmt-25` slice mixes state wiring,
  budget policy and a bounded session-constraint text projection. It needed a separate acceptance
  review to avoid treating runtime setup as prompt instruction construction, provider roundtrip
  invocation, support-context call-site readiness or telemetry mapping.
- Implemented fix: added `stage_h8r2db_provider_runtime_controller_state_setup.py` and focused tests.
  The review validates CX and DA receipts; verifies DA points to the target package and that DA's
  transitive dependencies remain resolved; verifies CX selected exactly `stmt-25.try-08` through
  `stmt-25.try-18`; rechecks the current AST; selects only those eleven inner statements; records
  body-free line, node, loaded-name, direct-call, control-count, error-type, budget-key,
  budget-profile and runtime state assignment-target metadata; and emits
  `provider_prompt_instruction_setup` as the next candidate.
- Validation evidence: DB focused **7 passed**; adjacent DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/
  CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/
  metadata/execution-planning regression **486 passed**; compileall and `git diff --check` passed.
  Official review receipt
  `phase_h8r2db_provider_runtime_controller_state_setup_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:209bca473ddf6c345ea502960a02b27541ef3335cf1f2621455648853758f948`.
  It records selected `stmt-25.try-08` through `stmt-25.try-18`, 43 lines total, runtime state
  assignment targets `controller.state`, `controller.state.budget`,
  `controller.state.session_constraints` and `controller._active_task_id`, expected budget keys,
  `runtime_controller_state_setup_package_review_approved=true`, and
  `runtime_controller_state_setup_commit_accepted=false`. Stage/commit/push/provider/project/memory
  side effects stayed zero.
- Remaining limitations: DB review-approves only runtime/session/budget/mode setup. It does not
  approve prompt instruction setup, support-context call-site staging, provider roundtrip invocation,
  result telemetry/completion mapping, production extraction, any hunk, any file staging, any
  accepted commit, the wider dirty tree, production prompt-use/default-on Compact, source-body
  support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2DC：Provider prompt instruction setup

- Observed gap: DB review-approved runtime/session/budget/mode setup and pointed to
  `provider_prompt_instruction_setup`, but the next slice introduces prompt instruction text. It
  needed a separate review to approve only system/user prompt construction while ensuring the receipt
  does not serialize prompt literal bodies and does not approve support-context candidate setup,
  provider roundtrip invocation, support-context call-site readiness or telemetry mapping.
- Implemented fix: added `stage_h8r2dc_provider_prompt_instruction_setup.py` and focused tests. The
  review validates CX and DB receipts; verifies DB points to the target package and that DB's
  transitive dependencies remain resolved; verifies CX selected exactly `stmt-25.try-19` through
  `stmt-25.try-23`; rechecks the current AST; selects only those five inner statements; records
  body-free line, node, loaded-name, direct-call, control-count, joined-string, formatted-value and
  string-literal length/count metadata; and emits `support_context_candidate_setup` as the next
  candidate.
- Validation evidence: DC focused **7 passed**; adjacent DC/DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/
  CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/
  runtime-session/metadata/execution-planning regression **493 passed**; compileall and
  `git diff --check` passed. Official review receipt
  `phase_h8r2dc_provider_prompt_instruction_setup_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:03d2d27115f0b90a97c0ac094a8a630e7b9e225927c3ebd57a2df6baad90f645`.
  It records selected `stmt-25.try-19` through `stmt-25.try-23`, 30 lines total, assignments to
  `system_prompt` and `user_prompt`, direct call `json.dumps`, seven string literals by count and
  total literal length 1203, `prompt_literal_bodies_serialized=false`,
  `prompt_instruction_setup_package_review_approved=true`, and
  `prompt_instruction_setup_commit_accepted=false`. Stage/commit/push/provider/project/memory side
  effects stayed zero.
- Remaining limitations: DC review-approves only provider prompt instruction setup. It does not
  approve support-context candidate setup, support-context call-site staging, provider roundtrip
  invocation, result telemetry/completion mapping, production extraction, any hunk, any file staging,
  any accepted commit, the wider dirty tree, production prompt-use/default-on Compact, source-body
  support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2DD：Support-context candidate setup

- Observed gap: DC review-approved provider prompt instruction setup and pointed to
  `support_context_candidate_setup`, but this next slice introduces support-context candidate
  construction and composition. It needed a separate review to approve only body-free candidate setup
  while keeping provider roundtrip invocation, result telemetry mapping and support-context call-site
  staging blocked.
- Implemented fix: added `stage_h8r2dd_support_context_candidate_setup.py` and focused tests. The
  review validates CX and DC receipts; verifies DC points to the target package and that DC's
  transitive dependencies remain resolved; verifies CX selected exactly `stmt-25.try-24` through
  `stmt-25.try-26`; rechecks the current AST; selects only those three inner statements; records
  body-free line, node, loaded-name, direct-call, keyword-arg, control-count, failure-ID and
  list/starred composition metadata; and emits `provider_roundtrip_invocation_core` as the next
  candidate.
- Validation evidence: DD focused **7 passed**; adjacent DD/DC/DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/
  CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/
  runtime-session/metadata/execution-planning regression **500 passed**; compileall and
  `git diff --check` passed. Official review receipt
  `phase_h8r2dd_support_context_candidate_setup_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:5792cb70953cede38082601921163ab66dff14a1497dfdb09a9d3cec5168434d`.
  It records selected `stmt-25.try-24` through `stmt-25.try-26`, 24 lines total, assignments to
  `effective_initial_context_candidates`, `support_context_candidate_count`,
  `support_context_candidates` and `support_context_error`, direct calls to
  `_support_context_candidates_for_task`, `_provider_task_prompt_candidates`, `failure_result`,
  `len` and `list`, error ID `ProviderSupportContextInvalid`,
  `support_context_candidate_setup_package_review_approved=true`, and
  `support_context_candidate_setup_commit_accepted=false`. Stage/commit/push/provider/project/memory
  side effects stayed zero.
- Remaining limitations: DD review-approves only support-context candidate setup. It does not approve
  provider roundtrip invocation, result telemetry/completion mapping, support-context call-site
  staging, production extraction, any hunk, any file staging, any accepted commit, the wider dirty
  tree, production prompt-use/default-on Compact, source-body support inclusion,
  OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2DE：Provider roundtrip invocation core

- Observed gap: DD review-approved support-context candidate setup and pointed to
  `provider_roundtrip_invocation_core`, but the next slice constructs `ProviderToolRoundTripRunner`
  and calls `.run(...)`. It needed a separate review to approve only the static roundtrip
  construction/invocation boundary while keeping actual provider transport execution, setup exception
  mapping, result telemetry mapping and support-context call-site staging blocked.
- Implemented fix: added `stage_h8r2de_provider_roundtrip_invocation_core.py` and focused tests. The
  review validates CX and DD receipts; verifies DD points to the target package and that DD's
  transitive dependencies remain resolved; verifies CX selected exactly `stmt-25.try-27`; rechecks
  the current AST; selects only that inner statement; records body-free line, node, loaded-name,
  direct-call, constructor-arg, constructor-kwarg, message-role and message-payload-reference
  metadata; and emits `provider_setup_exception_mapping` as the next candidate.
- Validation evidence: DE focused **7 passed**; adjacent DE/DD/DC/DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/
  CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/
  runtime-session/metadata/execution-planning regression **507 passed**; compileall and
  `git diff --check` passed. Official review receipt
  `phase_h8r2de_provider_roundtrip_invocation_core_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:4356bc02ef50b156f1c83bf8eeafc2a76e78584b3dda145f4c855681d58c6ce0`.
  It records selected `stmt-25.try-27`, 22 lines total, assigned name `roundtrip`, constructor
  kwargs for tools/budget/scope/validation/context, message roles `system` and `user`,
  message payload references `system_prompt` and `user_prompt`,
  `provider_roundtrip_invocation_core_package_review_approved=true`,
  `runtime_provider_transport_executed=false`, and
  `provider_roundtrip_invocation_core_commit_accepted=false`. Stage/commit/push/provider/project/
  memory side effects stayed zero.
- Remaining limitations: DE review-approves only provider roundtrip construction/invocation core at
  static review level. It does not execute runtime provider transport, approve setup exception
  mapping, result telemetry/completion mapping, support-context call-site staging, production
  extraction, any hunk, any file staging, any accepted commit, the wider dirty tree, production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2DF：Provider setup exception mapping

- Observed gap: DE review-approved the static provider roundtrip construction/invocation boundary
  and pointed to `provider_setup_exception_mapping`, but the surrounding `try` block's exception
  handler still needed a separate review. Without that split, setup-failure handling could be
  over-accepted together with result telemetry, support-context call-site staging, runtime provider
  transport or commit acceptance.
- Implemented fix: added `stage_h8r2df_provider_setup_exception_mapping.py` and focused tests. The
  review validates CX and DE receipts; verifies DE points to the target package and that DE's
  transitive dependencies remain resolved; verifies CX selected exactly `stmt-25.handler-01`;
  rechecks the current AST; selects only that handler; records body-free line, handler-type,
  handler-name, loaded-name, direct-call, control-count and typed failure-ID metadata; and emits
  `provider_result_telemetry_mapping` as the next candidate.
- Validation evidence: DF focused **7 passed**; adjacent DF/DE/DD/DC/DB/DA/CZ/CY/CX/CW/CV/CU/CT/
  CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **514 passed**;
  compileall passed. Official review receipt
  `phase_h8r2df_provider_setup_exception_mapping_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:9d88ec18eaf2ad54ae400f1e05c536e4d22d9f3955c0b99f9746f5c52326b05e`.
  It records `provider_setup_exception_mapping_package_review_approved=true`,
  `raw_exception_text_serialized=false`, `runtime_provider_transport_executed=false`,
  `result_telemetry_mapping_accepted=false` and
  `provider_setup_exception_mapping_commit_accepted=false`. Stage/commit/push/provider/project/
  memory side effects stayed zero.
- Remaining limitations: DF review-approves only setup exception fail-closed mapping. It does not
  approve result telemetry/completion mapping, support-context call-site staging, production
  extraction, any hunk, any file staging, any accepted commit, the wider dirty tree, production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2DG：Provider result telemetry prelude

- Observed gap: DF pointed to `provider_result_telemetry_mapping`, but the next result telemetry
  area is not a single safe package. The first four top-level statements only compute telemetry
  prelude values, while later statements construct the broad `output` payload, budget contract hash
  and failure/success `TaskExecutionResult` mappings. Accepting the whole result mapping in one pass
  would over-accept observability, completion and support-context-adjacent boundaries.
- Implemented fix: added `stage_h8r2dg_provider_result_telemetry_prelude.py` and focused tests. The
  review validates CH and DF receipts; verifies DF points to `provider_result_telemetry_mapping`;
  verifies CH still catalogs `stmt-26` through `stmt-29`; rechecks the current AST; selects only
  those four top-level statements; records body-free line, node, top-level-assignment, loaded-name,
  direct-call, attribute-name, string-literal and control-count metadata; and emits
  `provider_result_output_payload_split_review` as the next candidate.
- Validation evidence: DG focused **7 passed**; adjacent DG/DF/DE/DD/DC/DB/DA/CZ/CY/CX/CW/CV/CU/
  CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **521 passed**;
  compileall passed. Official review receipt
  `phase_h8r2dg_provider_result_telemetry_prelude_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:6abe951c6f779578e0098057aa9f033618b8cf87f9bb4c21b1c5096f231de343`.
  It records selected `stmt-26` through `stmt-29`, 14 lines total,
  `provider_result_telemetry_prelude_slice_review_approved=true`,
  `provider_result_telemetry_mapping_package_review_approved=false`,
  `output_payload_mapping_accepted=false`, `completion_result_mapping_accepted=false`,
  `runtime_provider_transport_executed=false` and
  `provider_result_telemetry_mapping_commit_accepted=false`. Stage/commit/push/provider/project/
  memory side effects stayed zero.
- Remaining limitations: DG review-approves only telemetry prelude setup. It does not approve the
  full result telemetry package, output payload mapping, budget contract mapping, failure/success
  completion result mapping, support-context call-site staging, production extraction, any hunk, any
  file staging, any accepted commit, the wider dirty tree, production prompt-use/default-on Compact,
  source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2DH：Provider result output payload split review

- Observed gap: DG pointed to `provider_result_output_payload_split_review`, but the candidate
  output area includes a tiny `reasoning_mode` assignment and a broad `output` assignment. `stmt-31`
  carries 61 lines, 23 top-level output keys, nested budget-limit and attempt keys, and CH domains
  across budget policy, mutation boundary, roundtrip invocation, result telemetry and
  support-context projection. Accepting it as one payload package would over-accept observability,
  budget, mutation and support-context-adjacent boundaries.
- Implemented fix: added `stage_h8r2dh_provider_result_output_payload_split_review.py` and focused
  tests. The review validates CH and DG receipts; verifies DG points to
  `provider_result_output_payload_split_review`; verifies CH still catalogs `stmt-30` and `stmt-31`
  with expected spans, domains and mixed flags; rechecks the current AST; records body-free
  assignment, loaded-name, direct-call, output key group and large-mixed metadata; rejects
  `provider_result_output_payload` as a single accepted package; and emits
  `provider_result_reasoning_mode_prelude` as the next candidate.
- Validation evidence: DH focused **7 passed**; adjacent DH/DG/DF/DE/DD/DC/DB/DA/CZ/CY/CX/CW/CV/
  CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **528 passed**;
  compileall passed. Official review receipt
  `phase_h8r2dh_provider_result_output_payload_split_review_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:17f4ecd5ad3addd76d5b3da9b1edd9e9e9ddeed3176d9625084856c467b2be10`.
  It records `provider_result_output_payload_split_review_completed=true`,
  `provider_result_output_payload_single_package_review_approved=false`,
  `provider_result_output_payload_split_required=true`, `budget_contract_mapping_accepted=false`,
  `completion_result_mapping_accepted=false`, `support_context_output_payload_accepted=false`,
  `runtime_provider_transport_executed=false` and
  `provider_result_output_payload_commit_accepted=false`. Stage/commit/push/provider/project/memory
  side effects stayed zero.
- Remaining limitations: DH is a split review only. It does not approve output payload mapping,
  budget contract mapping, failure/success completion result mapping, support-context output payload,
  support-context call-site staging, production extraction, any hunk, any file staging, any accepted
  commit, the wider dirty tree, production prompt-use/default-on Compact, source-body support
  inclusion, OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy
  changes.

### Phase H8-R2DI：Provider result reasoning mode prelude

- Observed gap: DH rejected the broad output payload as a single accepted package and pointed to
  `provider_result_reasoning_mode_prelude`, but `stmt-30` still needed its own acceptance boundary
  before the large `stmt-31` output payload could be reviewed. Without this slice, reasoning-mode
  extraction would remain coupled to the wider output payload review.
- Implemented fix: added `stage_h8r2di_provider_result_reasoning_mode_prelude.py` and focused tests.
  The review validates CH and DH receipts; verifies DH points to
  `provider_result_reasoning_mode_prelude`; verifies CH still catalogs `stmt-30` as a 1-line
  non-mixed assignment; rechecks the current AST; selects only `stmt-30`; records body-free
  assignment, loaded-name, direct-call, string-literal, `None` fallback and no-payload metadata; and
  emits `provider_result_output_payload_core_fields` as the next candidate.
- Validation evidence: DI focused **7 passed**; adjacent DI/DH/DG/DF/DE/DD/DC/DB/DA/CZ/CY/CX/CW/
  CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **535 passed**;
  compileall passed. Official review receipt
  `phase_h8r2di_provider_result_reasoning_mode_prelude_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:57a3487675d7976c1ef841a78b6dc92a1ef98a4ff802096668750224adea3f1e`.
  It records selected `stmt-30`, 1 line total,
  `provider_result_reasoning_mode_prelude_package_review_approved=true`,
  `output_payload_mapping_accepted=false`, `budget_contract_mapping_accepted=false`,
  `completion_result_mapping_accepted=false`, `support_context_output_payload_accepted=false`,
  `runtime_provider_transport_executed=false` and
  `provider_result_reasoning_mode_prelude_commit_accepted=false`. Stage/commit/push/provider/
  project/memory side effects stayed zero.
- Remaining limitations: DI review-approves only reasoning-mode prelude extraction. It does not
  approve output payload mapping, budget contract mapping, failure/success completion result mapping,
  support-context output payload, support-context call-site staging, production extraction, any hunk,
  any file staging, any accepted commit, the wider dirty tree, production prompt-use/default-on
  Compact, source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or
  production reasoning-policy changes.

### Phase H8-R2DJ：Provider result output payload core fields

- Observed gap: DI pointed to `provider_result_output_payload_core_fields`, but the candidate core
  groups still include value risks: `final_response` can carry response body text, and diagnostic
  arrays can carry large provider/runtime payload values. Accepting these as actual value mapping
  would weaken the context-management boundary that this route is trying to make explicit.
- Implemented fix: added `stage_h8r2dj_provider_result_output_payload_core_fields.py` and focused
  tests. The review validates DH and DI receipts; verifies DI points to
  `provider_result_output_payload_core_fields`; rechecks current `stmt-31` AST key membership;
  review-approves only the body-free core field manifest for provider identity, roundtrip telemetry,
  diagnostics, reasoning and attempts; and keeps actual value mapping, body-bearing values,
  budget/mutation fields, support-context output payload and completion mapping blocked. It emits
  `provider_result_output_payload_core_value_bounds` as the next candidate.
- Validation evidence: DJ focused **7 passed**; adjacent DJ/DI/DH/DG/DF/DE/DD/DC/DB/DA/CZ/CY/CX/
  CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **542 passed**;
  compileall passed. Official review receipt
  `phase_h8r2dj_provider_result_output_payload_core_fields_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:97d86ff105b8437bf2daba709c3f5d34e329cfa4db2f378d4254dade6f4d8d83`.
  It records selected `stmt-31`, `provider_result_output_payload_core_field_manifest_review_approved=true`,
  `provider_result_output_payload_core_value_mapping_accepted=false`,
  `provider_result_output_payload_full_mapping_accepted=false`,
  `final_response_content_value_accepted=false`, `diagnostic_payload_values_accepted=false`,
  `budget_mutation_output_fields_accepted=false`, `support_context_output_payload_accepted=false`,
  `runtime_provider_transport_executed=false` and
  `provider_result_output_payload_commit_accepted=false`. Stage/commit/push/provider/project/memory
  side effects stayed zero.
- Remaining limitations: DJ review-approves only the body-free core key manifest. It does not
  approve actual output value mapping, `final_response` body values, diagnostic payload values,
  budget contract mapping, budget/mutation output fields, support-context output payload,
  failure/success completion result mapping, support-context call-site staging, production
  extraction, any hunk, any file staging, any accepted commit, the wider dirty tree, production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2DK：Provider result output payload core value bounds

- Observed gap: DJ approved the core field manifest but intentionally left actual value mapping
  blocked. The next required distinction is value risk: scalar fields can be treated separately from
  structured evidence that needs bounded projection and body/diagnostic values that must remain
  blocked.
- Implemented fix: added `stage_h8r2dk_provider_result_output_payload_core_value_bounds.py` and
  focused tests. The review validates DJ receipt; verifies DJ points to
  `provider_result_output_payload_core_value_bounds`; rechecks current `stmt-31` AST value shapes;
  classifies scalar value candidates, bounded structured projection-required values and blocked
  unbounded/body/diagnostic values; and emits `provider_result_output_payload_core_scalar_mapping`
  as the next candidate while keeping actual value mapping blocked.
- Validation evidence: DK focused **7 passed**; adjacent DK/DJ/DI/DH/DG/DF/DE/DD/DC/DB/DA/CZ/CY/
  CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/
  provider-roundtrip/runtime-session/metadata/execution-planning regression **549 passed**;
  compileall passed. Official review receipt
  `phase_h8r2dk_provider_result_output_payload_core_value_bounds_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:716c874974a221554b18372ce3f831666f61cb23f7d0587f840464ae96f249ca`.
  It records `provider_result_output_payload_core_value_bounds_policy_review_approved=true`,
  `provider_result_output_payload_core_actual_value_mapping_accepted=false`,
  `provider_result_output_payload_full_mapping_accepted=false`,
  `final_response_content_value_accepted=false`, `diagnostic_payload_values_accepted=false`,
  `budget_mutation_output_fields_accepted=false`, `support_context_output_payload_accepted=false`,
  `runtime_provider_transport_executed=false` and
  `provider_result_output_payload_core_value_bounds_commit_accepted=false`. Stage/commit/push/
  provider/project/memory side effects stayed zero.
- Remaining limitations: DK review-approves only the value-bound policy. It does not approve actual
  output value mapping, final-response content mapping, diagnostic payload mapping, bounded
  structured projection value mapping, budget/mutation output fields, support-context output payload,
  budget contract mapping, failure/success completion result mapping, support-context call-site
  staging, production extraction, any hunk, any file staging, any accepted commit, the wider dirty
  tree, production prompt-use/default-on Compact, source-body support inclusion, OpenAI/
  cross-provider proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2DL：Provider result output payload core scalar mapping

- Observed gap: DK classified scalar value candidates but intentionally left actual value mapping
  blocked. The next safe step was to prove that the current `stmt-31` scalar value expressions still
  fit the DK scalar class without serializing actual runtime scalar values or accepting the wider
  output payload.
- Implemented fix: added `stage_h8r2dl_provider_result_output_payload_core_scalar_mapping.py` and
  focused tests. The review validates DK receipt; verifies DK points to
  `provider_result_output_payload_core_scalar_mapping`; rechecks current `stmt-31` AST value shapes;
  review-approves only body-free scalar value-expression mapping shapes for
  `provider_tool_execution`, `rounds_used`, `provider`, `model`, `budget_profile`,
  `outcome_feedback_enabled`, `reasoning_complexity` and `reasoning_mode`; and emits
  `provider_result_output_payload_bounded_structured_projection` as the next candidate while keeping
  actual scalar values un-serialized.
- Validation evidence: DL focused **7 passed**; adjacent DL/DK/DJ/DI/DH/DG/DF/DE/DD/DC/DB/DA/CZ/
  CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/
  BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning regression
  **556 passed**; compileall passed. Official review receipt
  `phase_h8r2dl_provider_result_output_payload_core_scalar_mapping_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:09903a7d43da374ed0567b23008b45f13013161d269853169ada7b004b2e1851`.
  It records `provider_result_output_payload_core_scalar_mapping_review_approved=true`,
  `actual_scalar_values_serialized=false`,
  `bounded_structured_projection_accepted=false`,
  `provider_result_output_payload_full_mapping_accepted=false`,
  `final_response_content_value_accepted=false`, `diagnostic_payload_values_accepted=false`,
  `support_context_output_payload_accepted=false`, `runtime_provider_transport_executed=false` and
  `provider_result_output_payload_core_scalar_mapping_commit_accepted=false`. Stage/commit/push/
  provider/project/memory side effects stayed zero.
- Remaining limitations: DL review-approves only scalar mapping shape. It does not approve actual
  scalar value serialization, bounded structured projection mapping, actual output value mapping,
  full output payload mapping, final-response content mapping, diagnostic payload mapping,
  budget/mutation output fields, support-context output payload, budget contract mapping,
  failure/success completion result mapping, support-context call-site staging, production
  extraction, any hunk, any file staging, any accepted commit, the wider dirty tree, production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2DM：Provider result output payload bounded structured projection

- Observed gap: DL approved only scalar mapping shape and kept structured projection blocked. The
  next risk was to ensure `tool_loops`, `evidence_coverage` and `attempts` are bounded projections
  rather than raw loop objects, response bodies, diagnostics payloads or open-ended provider output.
- Implemented fix: added
  `stage_h8r2dm_provider_result_output_payload_bounded_structured_projection.py` and focused tests.
  The review validates DL receipt; verifies DL points to
  `provider_result_output_payload_bounded_structured_projection`; rechecks current `stmt-31` AST
  projection shapes; traces `tool_loops` back to the earlier `loop_payload` projection; verifies
  `attempts` uses the fixed 7-field projection; and emits
  `provider_result_output_payload_budget_mutation_support_context_split_review` as the next
  candidate while keeping actual structured values un-serialized.
- Validation evidence: DM focused **7 passed**; adjacent DM/DL/DK/DJ/DI/DH/DG/DF/DE/DD/DC/DB/DA/
  CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/CB/CA/
  BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning regression
  **563 passed**; compileall passed. Official review receipt
  `phase_h8r2dm_provider_result_output_payload_bounded_structured_projection_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:81d39d89468d5f7e838ace545981a8aa04d11af789639432ddb543a5e6919618`.
  It records `provider_result_output_payload_bounded_structured_projection_review_approved=true`,
  `actual_structured_values_serialized=false`,
  `provider_result_output_payload_full_mapping_accepted=false`,
  `final_response_content_value_accepted=false`, `diagnostic_payload_values_accepted=false`,
  `budget_mutation_output_fields_accepted=false`, `support_context_output_payload_accepted=false`,
  `runtime_provider_transport_executed=false` and
  `provider_result_output_payload_bounded_structured_projection_commit_accepted=false`.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: DM review-approves only bounded structured projection shape. It does not
  approve actual structured value serialization, actual output value mapping, full output payload
  mapping, final-response content mapping, diagnostics payload mapping, budget/mutation output
  fields, support-context output payload, budget contract mapping, failure/success completion result
  mapping, support-context call-site staging, production extraction, any hunk, any file staging, any
  accepted commit, the wider dirty tree, production prompt-use/default-on Compact, source-body
  support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2DN：Provider result output payload budget/mutation/support-context split review

- Observed gap: DM approved bounded structured projection, leaving the remaining non-core output
  fields unclassified. These fields carry mutation-boundary, support-context and budget semantics,
  and accepting them as one package would risk mixing permission evidence, budget policy and output
  telemetry.
- Implemented fix: added
  `stage_h8r2dn_provider_result_output_payload_budget_mutation_support_context_split_review.py` and
  focused tests. The review validates DM receipt; verifies DM points to
  `provider_result_output_payload_budget_mutation_support_context_split_review`; rechecks current
  `stmt-31` AST shapes for mutation boundary, support-context output and budget/round-limit fields;
  verifies the fixed `budget_limits` key manifest; identifies `stmt-32` `budget_contract_sha256` as
  a separate dependency on `output["budget_limits"]`; and emits
  `provider_result_output_payload_mutation_boundary_fields` as the next candidate while keeping all
  non-core field mappings blocked.
- Validation evidence: DN focused **7 passed**; adjacent DN/DM/DL/DK/DJ/DI/DH/DG/DF/DE/DD/DC/DB/
  DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/CC/
  CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning
  regression **570 passed**; compileall passed. Official review receipt
  `phase_h8r2dn_provider_result_output_payload_budget_mutation_support_context_split_review_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:37179b863fef3cb354278791befcf5dce6fc65f109e71484da0cdb56cc42f797`.
  It records `provider_result_output_payload_budget_mutation_support_context_split_review_completed=true`,
  `provider_result_output_payload_budget_mutation_support_context_split_required=true`,
  `provider_result_output_payload_budget_mutation_support_context_single_package_review_approved=false`,
  `mutation_boundary_fields_accepted=false`, `support_context_output_payload_accepted=false`,
  `budget_limit_mapping_accepted=false`, `budget_contract_mapping_accepted=false`,
  `provider_result_output_payload_full_mapping_accepted=false`,
  `completion_result_mapping_accepted=false` and `runtime_provider_transport_executed=false`.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: DN review-approves only split/classification. It does not approve mutation
  boundary field mapping, support-context output payload mapping, budget limit mapping, budget
  contract mapping, actual output value mapping, full output payload mapping, final-response content
  mapping, diagnostics payload mapping, failure/success completion result mapping, support-context
  call-site staging, production extraction, any hunk, any file staging, any accepted commit, the
  wider dirty tree, production prompt-use/default-on Compact, source-body support inclusion,
  OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2DO：Provider result output payload mutation boundary fields

- Observed gap: DN split out mutation boundary fields but intentionally kept them unaccepted. These
  fields are permission-boundary projections, so they needed a narrower review that accepts only
  their mapping shape without serializing runtime values or treating the projected values as the
  permission decision itself.
- Implemented fix: added
  `stage_h8r2do_provider_result_output_payload_mutation_boundary_fields.py` and focused tests. The
  review validates DN receipt; verifies DN points to
  `provider_result_output_payload_mutation_boundary_fields`; rechecks current `stmt-31` AST shapes
  for `execution_mode`, `allow_mutations` and `user_confirmed`; review-approves only their
  body-free mapping shape; and emits `provider_result_output_payload_support_context_fields` as the
  next candidate while keeping actual mutation-boundary values and mutation permission decision
  completion blocked.
- Validation evidence: DO focused **7 passed**; adjacent DO/DN/DM/DL/DK/DJ/DI/DH/DG/DF/DE/DD/DC/
  DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/CD/
  CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-planning
  regression **577 passed**; compileall passed. Official review receipt
  `phase_h8r2do_provider_result_output_payload_mutation_boundary_fields_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:cba4e55ad0539a6a4a252cc839d008b88b25ecb084093947609bf606a8104f45`.
  It records `provider_result_output_payload_mutation_boundary_fields_review_approved=true`,
  `actual_mutation_boundary_values_serialized=false`,
  `mutation_permission_decision_accepted=false`,
  `support_context_output_payload_accepted=false`, `budget_limit_mapping_accepted=false`,
  `budget_contract_mapping_accepted=false`, `provider_result_output_payload_full_mapping_accepted=false`,
  `completion_result_mapping_accepted=false` and `runtime_provider_transport_executed=false`.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: DO review-approves only mutation boundary field mapping shape. It does not
  approve actual mutation-boundary value serialization, mutation permission decision completion,
  support-context output payload mapping, budget limit mapping, budget contract mapping, actual
  output value mapping, full output payload mapping, final-response content mapping, diagnostics
  payload mapping, failure/success completion result mapping, support-context call-site staging,
  production extraction, any hunk, any file staging, any accepted commit, the wider dirty tree,
  production prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider
  proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2DP：Provider result output payload support-context fields

- Observed gap: DO pointed to support-context fields, but these fields can carry support-context
  identities and routing evidence. They needed their own review so their mapping shape could be
  separated from actual file-list values, candidate bodies and provider call-site staging.
- Implemented fix: added
  `stage_h8r2dp_provider_result_output_payload_support_context_fields.py` and focused tests. The
  review validates DO receipt; verifies DO points to
  `provider_result_output_payload_support_context_fields`; rechecks current `stmt-31` AST shapes
  for `support_context_files` and `support_context_candidate_count`; review-approves only their
  body-free mapping shape; and emits `provider_result_output_payload_budget_round_limit_fields` as
  the next candidate while keeping actual support-context values, output payload and call-site
  staging blocked.
- Validation evidence: DP focused **7 passed**; adjacent DP/DO/DN/DM/DL/DK/DJ/DI/DH/DG/DF/DE/DD/
  DC/DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/CE/
  CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-
  planning regression **584 passed**; compileall passed. Official review receipt
  `phase_h8r2dp_provider_result_output_payload_support_context_fields_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:fdb1436af52875803cf08126bc101d18aebd944a23d90998bde8033549e67b8f`.
  It records `provider_result_output_payload_support_context_fields_review_approved=true`,
  `actual_support_context_values_serialized=false`,
  `support_context_output_payload_accepted=false`,
  `support_context_callsite_staging_blocked=true`,
  `budget_limit_mapping_accepted=false`, `budget_contract_mapping_accepted=false`,
  `provider_result_output_payload_full_mapping_accepted=false`,
  `completion_result_mapping_accepted=false` and `runtime_provider_transport_executed=false`.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: DP review-approves only support-context field mapping shape. It does not
  approve actual support-context value serialization, support-context output payload mapping,
  support-context provider call-site staging, budget limit mapping, budget contract mapping, actual
  output value mapping, full output payload mapping, final-response content mapping, diagnostics
  payload mapping, failure/success completion result mapping, production extraction, any hunk, any
  file staging, any accepted commit, the wider dirty tree, production prompt-use/default-on Compact,
  source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2DQ：Provider result output payload budget/round limit fields

- Observed gap: DP pointed to budget/round limit fields, but these fields carry budget-control
  semantics and feed the next budget contract hash. They needed their own review so the nested
  budget key/source shape could be accepted without serializing actual values or accepting the
  contract hash.
- Implemented fix: added
  `stage_h8r2dq_provider_result_output_payload_budget_round_limit_fields.py` and focused tests. The
  review validates DP receipt; verifies DP points to
  `provider_result_output_payload_budget_round_limit_fields`; rechecks current `stmt-31` AST shapes
  for `requested_max_rounds`, `effective_max_rounds` and `budget_limits`; verifies the fixed nested
  `budget_limits` key manifest and subvalue source shapes; and emits
  `provider_result_output_payload_budget_contract_mapping` as the next candidate while keeping
  actual budget values and budget contract mapping blocked.
- Validation evidence: DQ focused **7 passed**; adjacent DQ/DP/DO/DN/DM/DL/DK/DJ/DI/DH/DG/DF/DE/
  DD/DC/DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/CG/CF/
  CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/execution-
  planning regression **591 passed**; compileall passed. Official review receipt
  `phase_h8r2dq_provider_result_output_payload_budget_round_limit_fields_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:3ff69001ce5a1a510943e8a6891a2b0404fd74da3a3acf2673dcf3ac8fc5aad3`.
  It records `provider_result_output_payload_budget_round_limit_fields_review_approved=true`,
  `actual_budget_round_values_serialized=false`, `budget_limit_mapping_accepted=false`,
  `budget_contract_mapping_accepted=false`,
  `provider_result_output_payload_full_mapping_accepted=false`,
  `completion_result_mapping_accepted=false` and `runtime_provider_transport_executed=false`.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: DQ review-approves only budget/round field mapping shape. It does not
  approve actual budget/round value serialization, budget limit value mapping, budget contract
  mapping, actual output value mapping, full output payload mapping, final-response content mapping,
  diagnostics payload mapping, failure/success completion result mapping, support-context call-site
  staging, production extraction, any hunk, any file staging, any accepted commit, the wider dirty
  tree, production prompt-use/default-on Compact, source-body support inclusion, OpenAI/
  cross-provider proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2DR：Provider result output payload budget contract mapping

- Observed gap: DQ accepted the budget/round field mapping shape and fixed the nested
  `budget_limits` key/source manifest, but intentionally left `budget_contract_sha256` blocked.
  The remaining risk was accepting a contract hash without proving that it is only a canonical
  shape-level dependency on `budget_limits`, or accidentally serializing actual budget values or an
  actual contract hash in evidence.
- Implemented fix: added
  `stage_h8r2dr_provider_result_output_payload_budget_contract_mapping.py` and focused tests. The
  review validates DQ receipt; verifies DQ points to
  `provider_result_output_payload_budget_contract_mapping`; rechecks current `stmt-32` AST facts;
  verifies the target field, dependency field, canonical JSON option shape, hash call shape and
  prefix shape; review-approves only the body-free budget contract mapping shape; and emits
  `provider_result_completion_mapping` as the next candidate while keeping actual values and
  completion mapping blocked.
- Validation evidence: DR focused **7 passed**; adjacent DR/DQ/DP/DO/DN/DM/DL/DK/DJ/DI/DH/DG/
  DF/DE/DD/DC/DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/CH/
  CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/metadata/
  execution-planning regression **598 passed**; compileall passed. Official review receipt
  `phase_h8r2dr_provider_result_output_payload_budget_contract_mapping_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:101eb6d5d6f6207cded8f6da75b92c14528cbd2ee859b6b0a84e516500da790f`.
  It records `provider_result_output_payload_budget_contract_mapping_review_approved=true`,
  `budget_contract_mapping_shape_accepted=true`,
  `actual_budget_contract_hash_serialized=false`,
  `actual_budget_values_serialized=false`,
  `provider_result_output_payload_full_mapping_accepted=false`,
  `completion_result_mapping_accepted=false` and `runtime_provider_transport_executed=false`.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: DR review-approves only budget contract mapping algorithm/dependency
  shape. It does not approve actual budget value serialization, actual budget contract hash
  serialization, budget limit runtime value mapping, actual output value mapping, full output
  payload mapping, final-response content mapping, diagnostics payload mapping, failure/success
  completion result mapping, support-context call-site staging, production extraction, any hunk, any
  file staging, any accepted commit, the wider dirty tree, production prompt-use/default-on Compact,
  source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2DS：Provider result completion mapping

- Observed gap: DR accepted the budget contract mapping shape and pointed to
  `provider_result_completion_mapping`, but the final provider-native execution result still mixed
  failure and success construction paths. These paths include final text, result-summary text and
  error-message references, so accepting them without a dedicated body-free review could leak actual
  content or accidentally claim full output/result acceptance.
- Implemented fix: added `stage_h8r2ds_provider_result_completion_mapping.py` and focused tests.
  The review validates DR receipt; verifies DR points to `provider_result_completion_mapping`;
  rechecks current `stmt-33`, `stmt-34` and `stmt-35` AST facts; review-approves only the
  `FailureMetadata`/failed `TaskExecutionResult` and successful `TaskExecutionResult`/
  `TextArtifactMetadata` construction shape; verifies both branches reference `output` instead of
  expanding full output values; and emits `provider_result_mapping_integration_review` as the next
  candidate.
- Validation evidence: DS focused **7 passed**; adjacent DS/DR/DQ/DP/DO/DN/DM/DL/DK/DJ/DI/DH/
  DG/DF/DE/DD/DC/DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/
  CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/
  metadata/execution-planning regression **605 passed**; compileall passed. Official review receipt
  `phase_h8r2ds_provider_result_completion_mapping_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:ca7e07a2d45e0ef2e0a999458d18fe73bd8ba109c88be591a45252b38393bb54`.
  It records `provider_result_completion_mapping_review_approved=true`,
  `completion_mapping_shape_accepted=true`, `failure_branch_shape_accepted=true`,
  `success_branch_shape_accepted=true`, `output_referenced_not_expanded=true`,
  `actual_final_text_serialized=false`, `actual_result_summary_text_serialized=false`,
  `actual_error_text_serialized=false`, `raw_provider_response_serialized=false`,
  `raw_provider_error_serialized=false`, `full_output_payload_values_serialized=false` and
  `runtime_provider_transport_executed=false`. Stage/commit/push/provider/project/memory side
  effects stayed zero.
- Remaining limitations: DS review-approves only completion mapping construction shape. It does not
  approve actual final text serialization, actual result-summary text serialization, actual error
  text serialization, raw provider response/error serialization, full output payload value
  acceptance, runtime provider transport execution, support-context call-site staging, production
  extraction, any hunk, any file staging, any accepted commit, the wider dirty tree, production
  prompt-use/default-on Compact, source-body support inclusion, OpenAI/cross-provider proof, broad
  real-task evidence or production reasoning-policy changes.

### Phase H8-R2DT：Provider result mapping integration review

- Observed gap: DG→DS had individually validated provider-result slices, but the combined route
  still needed a body-free integration review. The first focused run correctly exposed that DH's
  broad `TARGET_PACKAGE_ID` is `provider_result_output_payload` while the actual accepted review
  slice is `provider_result_output_payload_split_review`; comparing only target package ids would
  falsely treat the rejected broad package as the continuity node.
- Implemented fix: added `stage_h8r2dt_provider_result_mapping_integration_review.py` and focused
  tests. The review validates every DG→DS source receipt with its authoritative validator, records
  only body-free source manifests, compares continuity using `review_package_id` when present,
  confirms the full provider-result slice coverage, verifies combined blocked claims remain
  blocked, and emits `provider_result_extraction_candidate_review` as the next candidate without
  accepting extraction, staging, provider transport or commit.
- Validation evidence: DT focused **7 passed**; adjacent DT/DS/DR/DQ/DP/DO/DN/DM/DL/DK/DJ/DI/DH/
  DG/DF/DE/DD/DC/DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/CI/
  CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/
  metadata/execution-planning regression **612 passed**; compileall passed. Official review receipt
  `phase_h8r2dt_provider_result_mapping_integration_review_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:0beb7c8a41465d27c58182eb16f3196018f850e8c22742fd52885e7d1b94d76f`.
  It records `provider_result_mapping_integration_review_approved=true`,
  `source_chain_continuous=true`, `coverage_complete=true`, `blocked_claims_preserved=true`,
  `full_output_payload_values_serialized=false`, `actual_final_text_serialized=false`,
  `actual_error_text_serialized=false`, `raw_provider_response_serialized=false`,
  `raw_provider_error_serialized=false`, `runtime_provider_transport_executed=false`,
  `provider_result_extraction_accepted=false` and `provider_result_runtime_transport_accepted=false`.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: DT review-approves only source receipt composition. It does not approve
  provider-result production extraction, runtime provider transport execution, support-context
  call-site staging, full output payload value acceptance, actual final/error text serialization,
  raw provider response/error serialization, any hunk, any file staging, any accepted commit, the
  wider dirty tree, production prompt-use/default-on Compact, source-body support inclusion,
  OpenAI/cross-provider proof, broad real-task evidence or production reasoning-policy changes.

### Phase H8-R2DU：Provider result extraction candidate review

- Observed gap: DT proved provider-result mapping slice composition, but production code still held
  the result mapping inline in `execute_provider_tool_task`. Before proposing any hunk, the route
  needed a body-free candidate-boundary review to prove the extractable region starts after provider
  roundtrip invocation and does not include provider transport/setup logic.
- Implemented fix: added `stage_h8r2du_provider_result_extraction_candidate_review.py` and focused
  tests. The review validates DT receipt; verifies DT points to
  `provider_result_extraction_candidate_review`; inspects current AST statements `stmt-26` through
  `stmt-35`; confirms the candidate starts after `stmt-25`, where the provider roundtrip runner and
  `.run(...)` call remain; records dependency, assignment, call and return-shape facts; and emits
  `provider_result_extraction_hunk_candidate` as the next candidate while keeping production
  extraction and hunk acceptance blocked.
- Validation evidence: DU focused **7 passed**; adjacent DU/DT/DS/DR/DQ/DP/DO/DN/DM/DL/DK/DJ/DI/
  DH/DG/DF/DE/DD/DC/DB/DA/CZ/CY/CX/CW/CV/CU/CT/CS/CR/CQ/CP/CO/CN/CM/CL/CK/CJ/
  CI/CH/CG/CF/CE/CD/CC/CB/CA/BZ/BY/BX/BW/BV/BU/BT/provider-roundtrip/runtime-session/
  metadata/execution-planning regression **619 passed**; compileall passed. Official review receipt
  `phase_h8r2du_provider_result_extraction_candidate_review_20260810_v1/aggregate/receipt.json`
  has receipt hash `sha256:86a1416813dc4c43fb82aca696d47190dbfe2a1848cce5158096bad1405b7741`.
  It records `provider_result_extraction_candidate_review_approved=true`,
  `candidate_boundary_review_approved=true`, `candidate_shape_review_approved=true`,
  `candidate_starts_after_provider_roundtrip=true`,
  `candidate_contains_provider_transport_calls=false`, `actual_output_values_serialized=false`,
  `actual_final_text_serialized=false`, `actual_error_text_serialized=false`,
  `raw_provider_response_serialized=false`, `raw_provider_error_serialized=false`,
  `production_extraction_implemented=false`, `provider_result_extraction_accepted=false`,
  `provider_result_extraction_hunk_accepted=false` and `runtime_provider_transport_executed=false`.
  Stage/commit/push/provider/project/memory side effects stayed zero.
- Remaining limitations: DU review-approves only the extraction candidate boundary. It does not
  approve production extraction, provider-result extraction hunk acceptance, runtime provider
  transport execution, support-context call-site staging, full output payload value acceptance,
  actual final/error text serialization, raw provider response/error serialization, any file
  staging, any accepted commit, the wider dirty tree, production prompt-use/default-on Compact,
  source-body support inclusion, OpenAI/cross-provider proof, broad real-task evidence or production
  reasoning-policy changes.

### Phase H8-R2DV：Provider result extraction hunk candidate

- Observed gap: DU identified `stmt-26`..`stmt-35` as an extraction candidate, but a
  reviewable hunk boundary was still needed before any refactor or staging could be considered.
- Implemented fix: added `stage_h8r2dv_provider_result_extraction_hunk_candidate.py` and focused
  tests. The offline review validates the DU receipt, confirms a single contiguous hunk at
  lines 862–984 after `stmt-25`, records body-free input/output/return-shape facts, and keeps
  provider transport/setup outside the candidate. It does not modify production code.
- Validation evidence: DV focused **6 passed**; adjacent H8-R2BT..H8-R2DV plus
  provider-roundtrip/runtime-session/metadata/execution-planning regression **625 passed**;
  compile check, receipt validator, `git diff --check`, and body/secret scan passed. The official
  receipt is `phase_h8r2dv_provider_result_extraction_hunk_candidate_20260810_v1/aggregate/receipt.json`
  with hash `sha256:25ddcc1f4be4864a2262e11e0029c9f2a3561a72266eebe5df29a76d1d94b885`.
  The first adjacent invocation omitted `PYTHONPATH=Code/src:.` and failed at collection;
  the correctly configured rerun passed and no product failure was inferred. Provider calls,
  project/memory mutations, stage, commit and push actions remained zero.
- Remaining limitations: DV approves only a body-free hunk candidate. It does not approve
  production extraction/refactor, hunk acceptance, runtime provider transport, support-context
  call-site staging, actual output/final/error text or raw provider payloads, default-on Compact,
  cross-provider proof, broad real-task benefit, selective staging, an accepted commit, or the
  wider dirty tree. The next candidate is `provider_result_extraction_hunk_review`.

### Phase H8-R2DW：Provider result extraction hunk review

- Observed gap: DV supplied a contiguous candidate, but the candidate's interface breadth
  could have been mistaken for a safe production helper. The current AST needed an independent
  review against the DV receipt and an explicit authority/transport exclusion.
- Implemented fix: added `stage_h8r2dw_provider_result_extraction_hunk_review.py` and focused
  tests. The review revalidates the DV receipt, reparses the current AST, confirms the exact
  `stmt-26`..`stmt-35` / lines 862–984 boundary after the roundtrip, checks that no provider,
  file, command or authority-control calls are inside it, and classifies the helper interface
  as wide (27 input names, 13 output names). It keeps production helper acceptance and staging
  blocked.
- Validation evidence: DW focused **7 passed**; adjacent H8-R2BT..H8-R2DW plus
  provider-roundtrip/runtime-session/metadata/execution-planning regression **632 passed**;
  compile check, receipt validator, `git diff --check`, and body/secret scan passed. The official
  receipt is `phase_h8r2dw_provider_result_extraction_hunk_review_20260810_v1/aggregate/receipt.json`
  with hash `sha256:ec2f996c977c7c2af6da20888c524f21bfaa953f5b8c4561606d162fc8c3c187`.
  Provider calls, project/memory mutations, stage, commit and push actions remained zero.
- Remaining limitations: DW approves only the hunk-level review/diagnosis. It does not approve
  production extraction/refactor, a helper with the wide interface, selective-staging simulation,
  support-context call-site staging, actual output/final/error text or raw provider payloads,
  default-on Compact, cross-provider proof, broad real-task benefit, an accepted commit, or the
  wider dirty tree. The next candidate is
  `provider_result_extraction_selective_staging_simulation`, which must first define a narrower
  split.

### Phase H8-R2DX：Provider result extraction selective-staging simulation

- Observed gap: DW found the full extraction hunk too wide for a helper. A safe next step
  required a statement-level split and a simulation that could not write the git index or
  silently include the monolithic payload mapping.
- Implemented fix: added `stage_h8r2dx_provider_result_extraction_selective_staging_simulation.py`
  and focused tests. The simulation revalidates DW, parses the current AST, records three
  contiguous segments (telemetry, payload, completion), selects only the two narrow segments,
  and keeps the 20-input/3-output payload segment excluded. No production source or git index
  is modified.
- Validation evidence: DX focused **7 passed**; adjacent H8-R2BT..H8-R2DX plus
  provider-roundtrip/runtime-session/metadata/execution-planning regression **639 passed**;
  compile check, receipt validator, `git diff --check`, and body/secret scan passed. The official
  receipt is `phase_h8r2dx_provider_result_extraction_selective_staging_simulation_20260810_v1/aggregate/receipt.json`
  with hash `sha256:d46a007b6b743c099b229815380d0b16af2a8f001f831ebca5bbe194a4862a6f`.
  Provider calls, project/memory mutations, git index writes, stage, commit and push actions
  remained zero.
- Remaining limitations: DX approves only the no-index split simulation. It does not approve
  production extraction, actual selective staging, the monolithic payload statement split,
  support-context call-site staging, actual output/final/error text or raw provider payloads,
  default-on Compact, cross-provider proof, broad real-task benefit, an accepted commit, or the
  wider dirty tree. The next candidate is `provider_result_completion_helper_candidate`; the
  payload remains a separate `provider_result_output_payload_statement_split` follow-up.

### Phase H8-R2DY：Provider result completion helper candidate

- Observed gap: DX selected telemetry and completion segments, but completion still needed an
  independent candidate review before any helper contract could be considered. The payload
  mapping had to remain outside that candidate.
- Implemented fix: added `stage_h8r2dy_provider_result_completion_helper_candidate.py` and
  focused tests. The review validates DX, reparses the current AST, confirms the exact
  `stmt-33`..`stmt-35` / lines 941–984 boundary, records 11 inputs, 3 output names, one
  failure return and one success return, and verifies zero provider/authority-control calls.
  It keeps the payload follow-up and production helper/refactor blocked.
- Validation evidence: DY focused **7 passed**; adjacent H8-R2BT..H8-R2DY plus
  provider-roundtrip/runtime-session/metadata/execution-planning regression **646 passed**;
  compile check, receipt validator, `git diff --check`, and body/secret scan passed. The official
  receipt is `phase_h8r2dy_provider_result_completion_helper_candidate_20260810_v1/aggregate/receipt.json`
  with hash `sha256:918330a7391e950b3d3f732523aaa4bfc06f2dc2bacc1b2f273fd77a4a104461`.
  Provider calls, project/memory mutations, stage, commit and push actions remained zero.
- Remaining limitations: DY approves only a body-free completion candidate. It does not approve
  production helper generation/refactor, actual final/error/output values, raw provider payloads,
  selective staging, support-context call-site staging, runtime provider transport, default-on
  Compact, cross-provider proof, broad real-task benefit, an accepted commit, or the wider dirty
  tree. The next candidate is `provider_result_completion_helper_contract_review`; payload remains
  the separate `provider_result_output_payload_statement_split` route.

### Phase H8-R2ED：Provider result completion helper production implementation

- Observed failure: provider completion result construction was an inline mixed block in
  `execute_provider_tool_task`, making typed result mapping harder to review and encouraging
  accidental mixing of provider transport, payload assembly, or authority logic.
- Implemented fix: after test-first focused failures, added the minimal static
  `_build_provider_task_result(task, roundtrip, duration, loop_payload, output)` helper and
  replaced only the completion construction with one call. Added focused coverage for success,
  failure, empty final responses, typed/mapping evidence coverage, and bounded summaries.
  No metadata/runtime contract, provider transport, budget/reasoning, Compact, or permission
  behavior was changed.
- Validation evidence: H8-R2ED receipt
  `phase_h8r2ed_provider_result_completion_helper_production_implementation_20260810_v1/aggregate/receipt.json`
  has hash `sha256:342eec4461d778ecdb5db28f91c87b39a26f76b67431d07c0f6db51d43649184`;
  focused **4 passed**, adjacent **274 passed**, compileall, AST shape, file scan, receipt
  validation and diff checks passed. The available full suite remains blocked in collection by
  the pre-existing missing `stage25_budget_profile_task_matrix.py`; no full-pass claim was made.
- Side-effect evidence: provider calls, project/memory mutations and git stage/commit/push
  actions were all zero. The implementation candidate remains pending independent acceptance;
  no staging or commit was performed. The current dirty tree and unrelated shared metadata/
  runtime changes remain outside this phase.
- Remaining limitations: independent production acceptance, selective staging, accepted commit,
  real provider execution, and real-task benefit are not yet established. The next candidate is
  `provider_result_completion_helper_production_independent_acceptance`.

### Phase H8-R2EE：Provider result completion helper production independent acceptance

- Observed risk: H8-R2ED implementation gates passed, but production acceptance still required
  an independent review of the dirty-tree baseline, exact tracked test hunk, source hash, helper
  purity and non-acceptance boundary.
- Implemented fix: added the read-only H8-R2EE acceptance harness and tamper-negative tests.
  The independent reviewer revalidated the ED receipt, current source hash, tracked test-hunk
  manifest, target index state, purity and zero side effects.
- Validation evidence: H8-R2EE focused **4 passed**; official receipt
  `phase_h8r2ee_provider_result_completion_helper_production_independent_acceptance_20260810_v1/aggregate/receipt.json`
  has hash `sha256:a72d76b4fcdbc998efacf9269a08887f2ca3356ad921785d561d8426f7b233ad`.
- Acceptance boundary: `production_helper_accepted=true`, while `stage_created=false`,
  `commit_created=false`, `push_created=false`; provider/project/memory/stage/commit/push
  counters are zero. This is independently accepted production implementation, not an accepted
  commit or dirty-tree-wide acceptance.
- Remaining limitations: full Code/tests collection remains blocked by the pre-existing stage25
  experiment source; no real provider benefit, Compact, reasoning, budget or cross-provider
  conclusion is claimed. Next step, if requested, is a separate consolidation/commit plan.

### Phase H8-R2DZ：Provider result completion helper contract review

- Observed gap: DY proved a narrow completion candidate, but its helper parameter and typed
  result contract had not been fixed. Implementing without this contract could accidentally
  widen authority or serialize provider bodies.
- Implemented fix: added `stage_h8r2dz_provider_result_completion_helper_contract_review.py`
  and focused tests. The review validates DY, reparses the current AST, fixes five runtime
  parameters for proposed `_build_provider_task_result`, records six typed dependencies,
  checks failure/success status branches and a 500-character result-summary bound, and confirms
  that no helper is implemented. Payload remains a separate follow-up.
- Validation evidence: DZ focused **7 passed**; adjacent H8-R2BT..H8-R2DZ plus
  provider-roundtrip/runtime-session/metadata/execution-planning regression **653 passed**;
  compile check, receipt validator, `git diff --check`, and body/secret scan passed. The official
  receipt is `phase_h8r2dz_provider_result_completion_helper_contract_review_20260810_v1/aggregate/receipt.json`
  with hash `sha256:abd18ed7a666f1846ac9f7584119601cf943adc24261f64a9a73103d112a85a9`.
  Provider calls, project/memory mutations, stage, commit and push actions remained zero.
- Remaining limitations: DZ approves only the proposed contract. It does not approve helper
  implementation/refactor, actual final/error/artifact values, raw provider payloads, selective
  staging, support-context call-site staging, runtime provider transport, default-on Compact,
  cross-provider proof, broad real-task benefit, an accepted commit, or the wider dirty tree.

### Phase H8-R2EA：Provider result completion helper implementation candidate

- Observed gap: H8-R2DZ fixed a narrow typed contract, but no behavior-level candidate had
  demonstrated that success/failure `TaskExecutionResult` construction, bounded result summary,
  and completion evidence could be separated without widening provider or authority boundaries.
- Implemented fix: added the offline-only
  `stage_h8r2ea_provider_result_completion_helper_implementation_candidate.py` harness and
  focused tests. It exercises an equivalent five-parameter `_build_provider_task_result`
  candidate against synthetic success/failure round-trips, reuses existing typed metadata,
  preserves round/tool-loop evidence in runtime attributes, and keeps actual body/error values
  out of the receipt. The current production AST is rechecked at the exact 3-statement /
  lines 941–984 boundary; no production source or git index was changed.
- Validation evidence: H8-R2EA focused **12 passed** after independent-review remediation;
  adjacent H8-R2BT..H8-R2EA plus provider/runtime/metadata/execution-planning regression
  **665 passed**; candidate compileall,
  receipt validator, body/secret scan and `git diff --check` passed. Provider calls,
  project/memory mutations and git stage/commit/push actions remained zero. The official receipt
  is `phase_h8r2ea_provider_result_completion_helper_implementation_candidate_20260810_v1/aggregate/receipt.json`
  with hash `sha256:402fbacf0ad8d96aeaa5684b08b1cea55df22838a474838b650d9dbbf2fc7c4b`.
- Independent review findings were closed before progression: receipt validation now fails
  closed on any gate/interface/purity drift, and the candidate failure path preserves the
  production `runner_error` plus typed `evidence_coverage.to_json_dict()` shape. Negative drift
  tests were added and passed.
- Full-gate limitation: the complete `Code/tests` collection is blocked by the pre-existing
  missing `stage25_budget_profile_task_matrix.py`; after excluding that collector, **1317
  passed / 12 failed** because old Phase28–31 tests reference missing
  `stage28_fully_scoped_task_repetition.py`, `stage30_fully_scoped_projection_matrix.py` and
  `stage31_scope_boundary_refusal.py`. Those sources are outside H8-R2EA and were not restored.
- Remaining limitations: this phase approves only an offline implementation candidate. It does
  not accept a production helper/refactor, provider-result payload mapping, actual final/error/
  artifact values in receipts, provider transport, Compact/reasoning/budget/permission changes,
  real-task or cross-provider benefit, staging, an accepted commit, or the wider dirty tree.
  The next candidate is `provider_result_completion_helper_production_patch_review`; payload
  remains the separate `provider_result_output_payload_statement_split` route.

### Phase H8-R2EB：Provider result completion helper production patch review

- Observed gap: H8-R2EA proved equivalent helper behavior, but a production extraction could
  still accidentally absorb provider roundtrip, payload mapping or shared metadata changes.
- Implemented fix: added the shadow-only
  `stage_h8r2eb_provider_result_completion_helper_production_patch_review.py` harness and
  focused tests. It builds an in-memory class patch that replaces only `stmt-33`..`stmt-35` /
  lines 941–984 with a call to a static five-parameter helper, compiles the shadow source,
  checks the provider callsite remains at the entrypoint, and proves the production source hash
  and git index are untouched.
- Validation evidence: H8-R2EB focused **8 passed** after the independent-review counter
  hardening; adjacent H8-R2BT..H8-R2EB plus provider/runtime/metadata/execution-planning
  regression **673 passed**; compileall, receipt
  validator, body/secret scan and `git diff --check` passed. Provider calls, project/memory
  mutations and git stage/commit/push actions remained zero. The official receipt is
  `phase_h8r2eb_provider_result_completion_helper_production_patch_review_20260810_v1/aggregate/receipt.json`
  with hash `sha256:cc2a54c036881d907833c90903f23b9d2742579b82f23dee5495545c93970047`.
- Remaining limitations: EB approves only a shadow production patch shape. It does not accept
  a production source change, metadata/runtime contract change, provider result payload mapping,
  real provider/task benefit, Compact/reasoning/budget/permission change, staging, an accepted
  commit, or the wider dirty tree. The next candidate is
  `provider_result_completion_helper_independent_acceptance`; payload remains the separate
  `provider_result_output_payload_statement_split` route.

### Phase H8-R2EC：Provider result completion helper independent acceptance

- Observed gap: EB proved a shadow patch shape, but handoff needed an independent recheck of
  the EA→EB receipt chain, exact hunk/contract shape, current production source integrity and
  non-acceptance boundary before any production owner could act.
- Implemented fix: added the read-only
  `stage_h8r2ec_provider_result_completion_helper_independent_acceptance.py` harness and
  focused tests. It validates EA/EB receipts, reparses the current production AST for
  `stmt-33`..`stmt-35` / lines 941–984, compares current source hash and staged-index state to
  EB, and keeps production implementation authorization false.
- Validation evidence: H8-R2EC focused **8 passed** after independent-review shape hardening;
  adjacent H8-R2BT..H8-R2EC plus provider/runtime/metadata/execution-planning regression
  **681 passed**; compileall, receipt validator, body/secret scan and `git diff --check` passed.
  Provider calls, project/memory mutations and git stage/commit/push actions remained zero.
  The official receipt is
  `phase_h8r2ec_provider_result_completion_helper_independent_acceptance_20260810_v1/aggregate/receipt.json`
  with hash `sha256:b5468401c5d63e67d74339d1bc562bda24d8f60c08490180cebfb77bc22de6a3`.
- Remaining limitations: EC approves only shadow-patch handoff to a named production
  implementation owner. It does not approve production source or shared metadata/runtime
  changes, provider result payload mapping, real provider/task benefit, Compact/reasoning/
  budget/permission changes, staging, an accepted commit, or the wider dirty tree. The next
  candidate is `provider_result_completion_helper_production_implementation`; payload remains
  the separate `provider_result_output_payload_statement_split` route.
  The next candidate is `provider_result_completion_helper_implementation_candidate`; payload
  remains the separate `provider_result_output_payload_statement_split` route.

### Phase H8-R2BA：Production reusable summary binding shadow review

- Observed failure: reusable-compaction admission compared source IDs and binding hash but not
  the candidate's `source_fingerprint`; a changed fingerprint could still be admitted. The BA
  harness also mapped an experiment-only artifact kind into production-shaped metadata without
  exercising the real `MemoryContextBuilder` hook, and its AZ drift matrix omitted fixture-turn
  ledger and source-binding drift.
- Implemented fix: added body-free source-fingerprint indexing to the builder shadow payload and
  fail-closed admission comparison; a non-empty current session constraint now requires a matching
  candidate guard. Added tracked negative tests, explicit production artifact-kind normalization,
  AZ ledger/source-binding drift cases, and a BA harness that invokes the real builder and verifies
  prompt/selection/request/compaction identity invariants.
- Validation evidence: production/context/metadata focused **144 passed**; H8-R2AZ/H8-R2BA stage
  suite **15 passed**; compileall and `git diff --check` passed. Local ephemeral BA receipt hash is
  `sha256:741a1e59721500902a8cd20cd27fd4afe996585c77bbf689c6843ac5b7f2ebc2`. Full `Code/tests`
  collection remains blocked by the pre-existing missing `stage25_budget_profile_task_matrix.py`.
- Independent acceptance: a read-only subagent re-ran the seven BA gates, receipt validator,
  self-excluding hash/body scan and source/session negative cases; all passed, with **64 focused
  tests passed**. No files were modified and no provider/stage/commit/push action occurred.
- Acceptance boundary: default-off shadow review only; no prompt-use, default-on summary, real
  provider canary, project/memory mutation, staging, commit, or push is authorized. The worktree
  remains dirty and is not an accepted commit.
- Remaining limitations: guard provenance is still externally injected, session-turn ledger is not
  yet a production reusable-candidate field, malformed artifact/fallback telemetry needs H8-R2BB,
  and no token/quality/real-task benefit is claimed.

### Phase H8-R2BB：Shadow failure telemetry and artifact contract hardening

- Observed failure: non-strict `MemoryContextBuilder` shadow-provider exception, empty result, and
  malformed result were silently dropped; malformed checkpoint artifact checksums could also abort
  candidate construction and disappear without typed evidence. Directly constructed admitted
  metadata could bypass production artifact kind and summary identity requirements.
- Implemented fix: extended `ContextSelectionMetadata` with body-free
  `compaction_reuse_shadow_failures`; builder now records typed exception/empty/invalid-result
  fallback while preserving prompt/request/selected candidates, and strict mode still raises the
  existing source error. Checkpoint adapters now convert malformed artifact checksums to typed
  `artifact_contract_invalid` rejections with a safe zero checksum. Admitted reuse values require
  `context_compaction` plus generated summary fingerprint; candidate algorithms are constrained to
  existing typed compaction algorithms. API/catalog/exports were synchronized.
- Validation evidence: focused **151 passed**; H8-R2AZ/H8-R2BA/H8-R2BB stage suite **17 passed**;
  full tests excluding four historical missing-stage collectors **1330 passed**; compileall and
  `git diff --check` passed. Local ephemeral receipt hash is
  `sha256:94234fd7ec6a4fc3bfb6384c66457d35c5be27269de00f6c102fd141e0e33c56`. Unfiltered full
  collection remains blocked by missing stage25/28/30/31 sources.
- Acceptance boundary: no real provider, prompt-use, default-on, project/memory mutation,
  staging, commit, or push. Dirty-tree-wide acceptance is not claimed.
- Remaining limitations: guard provenance and ledger identity are still externally injected;
  artifact body loader/checksum re-read and real-task/provider benefit remain future work. Next
  candidate is H8-R2BC guard provenance and artifact integrity preflight review.
- Independent acceptance: the read-only subagent confirmed 5/5 H8-R2BB gates, receipt
  validator/self-excluding hash/body scan, all four failure telemetry paths, strict/non-strict
  invariants and malformed-artifact zero-hash rejection. Independent focused was **122 passed**;
  full-excluded was **1330 passed** with one pre-existing warning. No files, provider, runs/data,
  staging, commit, or push actions occurred.
