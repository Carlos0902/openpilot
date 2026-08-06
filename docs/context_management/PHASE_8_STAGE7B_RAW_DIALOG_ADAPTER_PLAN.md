# Stage 7B 计划：typed raw-dialog adapter 与真实候选消费

## 目标

让已被 `SessionIngress` 接收的 raw `SessionTurn` 进入 ContextLoader 的**派生视图**，
并证明该视图被真实 model-facing request 消费。原始 `SessionIngressState` 仍是唯一
事实源；不把整份 ingress ledger 写入 LongTerm/ShortMemory，也不把 assistant 文本
提升为约束。

本阶段先做零 Provider、零目标源码 mutation 的生产链路验证，不扩大 canary。

## 契约与 metadata 影响评审

```text
复用：SessionIngressState、SessionTurn、ConversationIdentity、ContextCandidate、
      ContextSelectionMetadata、现有 request_hash/replay snapshot。
新增字段：无。raw turn 的 message_id、turn_index、conversation_id、project_root
      均来自现有 typed ingress；derived candidate 的 source_id 只引用这些 ID。
权威性：SessionIngressState.turns 是原始事实；ContextCandidate 是一次 request
      的只读 DIALOG 投影；SessionConstraintState 仍独立作为 required constraint。
持久化：不写 MemoryStore/LongTerm/ShortMemory；checkpoint 继续保存 ingress snapshot。
安全：assistant 只产生 DIALOG candidate；任何 constraint 只能来自已确认的
      SessionConstraintState；conversation/project identity 不匹配 fail-closed。
预算：raw dialog 是可省略/可 compact 的 preferred 输入，不得改变 required
      constraint 的 retention/truncation；request hash 必须包含 ingress turn-ledger
      digest，防止同约束不同 raw turns 复用 replay artifact。
```

## 分阶段实施计划

### 7B-1：memory 边界适配

- `ContextLoaderAgent.run` 接受可选 `SessionIngressState`，向
  `MemoryContextBuilder.build` 传递同一快照。
- builder 从 turns 生成 source-linked DIALOG candidates；candidate ID 稳定且以
  `conversation_id/message_id/turn_index` 为依据。只保留有界的最近后缀，选择/省略
  仍由现有 assembler 决定。
- request hash 纳入 canonical ingress turn-ledger digest；同约束不同 turns 必须
  得到不同 hash，同一 snapshot resume 必须得到相同 hash。

### 7B-2：生产链路传播与消费证明

- `AutonomousIterationAgent`、pipeline、project-improvement analyzer、Goal Maker、
  Task Designer 都传递同一 ingress snapshot。
- 明确 `project_state.memory_context` 的消费边界：至少在 ContextLoader request
  selection 中验证 derived dialog candidate 被选中；若要宣称 Goal/Task 决策收益，
  必须在对应 candidate request 中看到该 candidate ID，不能只证明字段被赋值。
- analyzer、Goal、Task 三个 purpose 的 context selection 都要保留 required
  session constraint 与 raw-dialog source evidence 的可审计 ID。

### 7B-3：安全与回归门

- 同源 current/compact、feature-off、kill-switch、fallback、checkpoint resume
  均走相同 ingress digest；不触发 Provider/network/目标源码写入。
- 使用临时项目验证 ContextLoader 不创建 `sketch.json` 或 `.openpilot/file_indexes`；
  source unavailable、snapshot/compaction failure 必须 typed fail-closed。
- raw turns 不进入 LongTerm/MemoryStore，assistant 不进入 constraint reducer；
  empty constraints 也必须校验 conversation/project ownership。

## 进入下一阶段的判据

通过 7B-1/7B-3 后，才允许进入 7C provider-attempt 输出/reasoning 派生遥测；
通过 7B-2 的真实 candidate consumption 证明后，才允许重新评估 full-session
Provider canary。任何只显示 `memory_context` 被赋值、但没有 candidate ID 消费证据
的结果，都不能算 full-session context 收益。

## 7B-1/7B-2a/7B-2b 当前证据

- `ContextLoaderAgent` 与 `MemoryContextBuilder` 已接收 typed
  `SessionIngressState`，raw turns 以最近有界 suffix 形成 source-linked DIALOG
  candidates；message ID 进入 `source_id`，assistant 不形成 constraint。ingress、
  project root、explicit constraint hash 不一致时 fail closed，turn-ledger digest
  进入 `context_request_hash`。
- 同一 derived projection 已贯通 project-improvement analyzer、Goal Maker 和
  Task Designer candidate builders；三者都保留独立 required session constraints，
  只追加 DIALOG view，不携带 raw ledger 或累计 `prompt_text`。analyzer tool input
  记录 typed `session_turn_source_hash`，executor 用 runtime ingress handle 重算并
  校验该 hash。
- `IterationAgent._load_context` 不再把 `ContextSourceError`/预算/治理失败转换为
  空成功；严格失败会交给 project-improvement runtime 的 typed interruption path。
- 验证：Code 全量 **965 passed**；新增候选/分析 request 证明为零 Provider，且无
  目标源码 mutation。7B-3 仍需补齐 current/compact、checkpoint resume、flag/kill
  switch 和全链路 no-mutation 组合门。

## 7B-3a 当前证据：同源 projection 与 checkpoint raw-ledger 门

- `stage13_session_compact_pipeline_offline.py` 让 current/compact 两臂都从同一
  `SessionIngressState` 生成 Goal/Task request。每个 request 记录 bounded DIALOG
  source IDs、`session_turn_source_hash` 和 dialog recall；两臂的 user/assistant
  message IDs、ledger hash 与 active constraint recall 均保持一致，仍是 zero
  Provider/network/project mutation。
- `stage14_full_entry_admission.py` 通过真实 `IntelligentAutopilot.execute()` →
  `AgentRuntimeController.run()` 保存包含 user 与 assistant raw turns 的 checkpoint，
  并对 checkpoint 重算 raw-ledger hash；`stage15_full_session_canary_gate.py` 将该
  hash 与 current/compact projection 证据组合到 pre-transport gate。该 gate 仍明确
  `provider_execution_admitted=false`，不能被误报为 Provider 收益。
- 验证：Stage 7B-3a 定向回归 **12 passed**；dialog recall 两臂均为 `1.0`，checkpoint
  raw-turn hash 与 ingress 相同，provider/network/project mutation 均为 `0`。

## 7B-3b 待办

### 实施前计划

1. 增加零 Provider full-context sentinel：用真实 `ContextLoaderAgent` 的
   `read_only + strict_sources` 装配 raw dialog，再把同一 ingress snapshot 传入
   analyzer、Goal、Task 的生产候选边界。
2. analyzer 使用本地 deterministic LLM stub，仅返回 bounded delta；记录 request 中
   的 DIALOG source IDs、required constraint 和 ledger hash，但不把 stub 调用计作
   Provider usage。
3. 运行前后冻结临时项目文件清单、MemoryStore/ShortMemory 状态和 checkpoint 之外的
   写入；任何隐式索引、Memory 或源码 mutation 都 fail closed。
4. 分别验证 compact assembly failure 的 fallback receipt、feature-off 与 kill-switch
   的提前停止；fallback 必须复用同一 ingress hash 且保持约束/对话召回。

只有这些门通过，才进入 provider-attempt output/reasoning telemetry。

遥测阶段的独立计划见
`docs/context_management/PHASE_8_STAGE7C_PROVIDER_ATTEMPT_TELEMETRY_PLAN.md`。

## 7B-3b 当前证据

- 新增 `stage7b3b_full_context_offline.py`：真实 `ContextLoaderAgent` 使用注入到临时
  memory store 的 `MemoryContextBuilder`，以 `read_only + strict_sources` 装配 raw
  dialog；随后 project-improvement analyzer、Goal、Task 都使用同一 ingress snapshot
  和 turn-ledger hash。analyzer 只使用本地 deterministic stub，Provider calls 仍为 `0`。
- 临时项目、MemoryStore、`sketch.json` 和 `.openpilot/file_indexes` 都做了 before/after
  内容快照；正常与 compact-failure fallback 两种路径均为零 project/memory mutation。
  compact failure 现在产生 typed `CompactFallbackReceipt`，关联失败臂、current fallback、
  source snapshot、turn hash、constraint hash 和零副作用计数。
- Stage 15 pre-transport gate 现在还校验锁定 protocol 的 feature flag/kill switch 与
  运行参数一致，并 fail closed 检查 `run_no_provider_preflight()` 的 `status` 与
  `controls_admitted`。
- 验证：7B-3b 定向回归 **16 passed**；Stage 10/12/13/14/15/16 加 7B-3b 组合回归
  **47 passed**。正常 full-context sentinel 的 offline analyzer call 为 `1`、Provider/
  network/project/memory mutation 均为 `0`；dialog 与 constraint recall 均为 `1.0`。

## 7B-3b 剩余限制

该 fallback sentinel 注入的是候选构造失败，不等同于真实 compactor 的 source-binding
回退；真实 `ContextAssembler` atomic compaction 仍由 Stage 10/Code 契约测试覆盖。此处
证明的是 full-session ingress、read-only ContextLoader、analyzer/Goal/Task 消费与
orchestration fallback 的安全边界，尚不是 Provider 质量/Token 收益或多模型结论。

## 测试先行清单

- `SessionIngressState` → DIALOG candidate 的 source identity、角色和稳定排序；
- user/assistant raw turn 不改变/污染 active constraint state；
- raw turns 不写 MemoryStore/ShortMemory/LongTerm；
- 同约束不同 turns 的 request hash 不同，同 snapshot resume hash 相同；
- ContextLoader、analyzer、Goal/Task request 的 candidate selection 消费证据；
- budget compact 只替换允许的 assistant/dialog prefix，required constraint 不可省略；
- identity conflict、missing ingress、unknown source、feature-off/kill-switch 均
  fail-closed 或按显式兼容路径处理。
