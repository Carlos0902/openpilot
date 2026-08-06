# Runtime Checkpoint and Recovery Plan

> 文档定位：checkpoint、原子存储和文件副作用协议的详细设计与历史实施记录。后续实施
> 顺序以 `COMPREHENSIVE_RECOVERY_BOUNDARY_PLAN.md` 为准。

## 1. 目标

本计划用于把 OpenPilot 的运行时断点恢复做成一个可验证、可解释、不会重复执行副作用的正式能力。

目标不是让失败任务简单地“重新跑一遍”，而是在进程退出、模型调用失败、预算停止或人工暂停后：

- 恢复到最近一个经过校验的安全边界；
- 保留原任务身份、运行状态和已经消耗的预算；
- 已经成功的步骤不重复执行；
- 写文件、运行命令等副作用在恢复前先对账；
- 项目发生外部变化时不覆盖用户修改；
- 每次恢复选择都有持久化、可阅读的理由。

当前阶段暂停扩展记忆和上下文管理。本计划不包含新的记忆架构，也不依赖 mini-SWE 或大规模 Agent 测评。

## 实施状态（2026-08-02）

Phase 0–6 的首个受控版本已经实现并通过全量离线测试。当前开放范围：

- 显式启用 checkpoint 的运行；
- `task_normalized` 只读恢复；
- 已完成 checkpoint 直接返回；
- 文件 create/replace/delete 的 prepared/observed/applied 哈希对账；
- 写入已发生时不重放，继续执行验证；
- 项目或目标文件漂移时阻塞，不覆盖用户修改；
- 外部命令只记录检查点，除已验证完成状态外不自动重放；
- CLI 显式 `run_id + checkpoint_id + project_path` 恢复入口。

仍未开放：任意 monolithic session 中间 stage 的继续、无可靠探针的命令重放、网络写操作自动恢复，以及分布式调度。后续扩展必须继续按本计划的 fail-closed 原则逐工具增加。

## 2. 当前能力与缺口

### 2.1 可以复用的现有能力

- `RuntimeStateMetadata` 已包含阶段、事实、未知项、文件选择、修改记录、工具历史、验证状态和结束原因。
- `RuntimeBudgetMetadata` 已记录工具调用、文件读写、验证、恢复和重规划的额度及消耗。
- 轨迹系统已经持久化 `RunRecord`、顺序化 `EventRecord` 和大对象 `ArtifactRecord`。
- 现有 ID 分层已经区分 `run_id`、`root_task_id`、`subtask_id`、`step_id` 和工具/LLM `execution_id`。
- 工具事件包含 `call_id` 和部分 Git 快照信息；任务执行器会在部分高风险写入前创建 Git 快照。
- `StateUpdater` 已经能够在进程内把工具结果吸收到运行状态，并在写入后要求验证。

### 2.2 实现前的主要缺口（现作为回归背景）

- `IntelligentAutopilot.execute()` 每次创建新 `session_id`，没有显式恢复入口。
- `AgentRuntimeController.run()` 每次创建新 `RuntimeStateMetadata`，不能从持久化状态继续。
- 轨迹记录是观察证据，不是原子检查点；事件存在不等于对应状态已经应用完成。
- 诊断记录器的部分运行索引和事件序号依赖进程内存，重启后不能仅凭 session 别名稳定定位旧运行。
- 目前没有“写入准备完成、工具已执行、结果已记录、状态已应用”的持久化边界，崩溃后无法判断副作用是否应该重放。
- 预算会随新运行重建，无法证明恢复后没有重置或重复计费。
- 没有项目指纹、检查点校验和、schema 兼容策略及损坏检查点回退策略。
- `AGENTS.md` 引用的 `AGENT_LOOP_PROTOCOL.md`、`AGENT_LOOP_SUPERVISOR.md`、`AGENT_LOOP_GOAL.md`、`AGENT_LOOP_SESSION_RESUME.md` 当前均不存在；实现前需要补齐或修正这些协议入口。

## 3. 核心设计原则

### 3.1 恢复不是重试

重试是在同一执行点再次调用；恢复是在进程边界之后重建受控状态。恢复必须先回答：

1. 最后一个可信状态是什么；
2. 是否存在结果不确定的副作用；
3. 项目是否被外部修改；
4. 剩余预算是否允许继续；
5. 下一步是继续、对账、重规划还是阻塞。

### 3.2 三种信息源职责分离

| 信息 | 职责 | 是否是恢复真相源 |
| --- | --- | --- |
| Runtime checkpoint | 可继续执行的最小完整状态 | 是 |
| Task trajectory | 发生过什么的追加式审计证据 | 否，作为交叉校验 |
| Git snapshot / file fingerprint | 判断项目副作用和外部漂移 | 否，作为对账依据 |

轨迹写入失败不能改变运行决策；检查点写入失败则不能声称该边界可恢复。

### 3.3 不盲目重放副作用

- 只读调用可以在输入和项目指纹一致时重放。
- 文件写入、删除、命令执行、网络写操作默认不可直接重放。
- 无法确认副作用是否发生时，进入 `reconcile`；仍无法确认则 `blocked`。
- 不使用 `git reset`、`checkout` 等方式静默覆盖用户修改。

### 3.4 恢复不重置预算

所有已消耗额度、恢复轮次、重规划轮次和验证次数必须从检查点恢复。预算耗尽的运行默认仍保持停止；只有用户明确增加预算后才能继续。

## 4. 建议的状态所有权

### 4.1 `autonomous_iteration`

拥有：

- 何时形成安全边界；
- 当前阶段和下一动作；
- 检查点创建、加载、兼容性验证和恢复决策；
- 对工具执行器发出继续、对账、重规划或阻塞指令。

建议新增一个窄职责的 `RuntimeCheckpointStore`，放在 `autonomous_iteration/` 内。它只负责原子存取和校验，不负责业务规划。

### 4.2 `metadata`

只定义严格类型契约。经字段审查后，建议增加一个 `RuntimeCheckpointMetadata`，而不是把检查点生命周期字段塞进 `RuntimeStateMetadata`。

`RuntimeStateMetadata` 继续表达“Agent 当前业务状态”；检查点额外表达“这个状态为何可信、保存在哪个边界、如何恢复”。

### 4.3 工具执行层

拥有：

- 调用的唯一 `call_id`；
- 工具输入的规范化摘要和哈希；
- 是否只读、是否可幂等重放、是否有外部副作用；
- 工具结果和针对该工具的对账探针。

### 4.4 `runtime_diagnostics`

继续作为证据层，镜像：

- 检查点已创建；
- 恢复已请求；
- 指纹检查结果；
- 恢复模式和理由；
- 恢复成功、失败或阻塞。

它不负责决定下一步，也不作为唯一检查点存储。

## 5. 检查点契约

以下是计划中的最小字段集合；实现前先以 metadata 测试锁定字段语义：

- `schema_version`
- `checkpoint_id`
- `run_id`
- `root_task_id`
- `session_id`
- `resume_attempt_id`（首次运行为空，恢复时新建）
- `created_at`
- `checkpoint_reason`
- `safe_boundary`
- 完整的 `runtime_state`，包含预算消耗
- `last_durable_event_id` 和 `last_durable_event_sequence`
- 当前 subtask、step 和 execution/call 标识
- 最近或待确认工具动作
- 副作用状态：`none | prepared | observed | applied | indeterminate`
- 工具名、规范化输入哈希、mutation class
- 项目指纹和可用的 Git 快照引用
- 验证状态
- 运行时/应用版本兼容信息
- 内容完整性校验和

不写入：

- API key、token、完整环境变量；
- 不可序列化的客户端、文件句柄和线程对象；
- 可以通过 artifact 引用的大模型输出或上下文全文；
- 与恢复无关的大体积记忆内容。

## 6. 安全检查点边界

首版只在明确边界保存：

1. 任务输入规范化且根任务身份确定后；
2. 路由选择完成后；
3. 任务分解已持久化后；
4. 副作用工具调用前，写入 `prepared` 检查点；
5. 工具结果持久化后，写入 `observed` 检查点；
6. 工具结果已吸收到 runtime state、预算已计费后，写入 `applied` 检查点；
7. 写入后进入验证前；
8. 验证结果已应用后；
9. 预算停止、等待批准/用户输入、可恢复异常或受控退出时。

“工具正在执行中”不是可直接继续的安全边界。恢复时必须从最近的持久边界判断是否需要对账。

## 7. 副作用提交协议

对每次可能修改状态的工具调用采用简化的 write-ahead 状态机：

```text
planned
  -> prepared（调用意图和执行前指纹已落盘）
  -> tool executes
  -> observed（工具结果和执行后证据已落盘）
  -> applied（runtime state 与预算已更新）
  -> verified（如需要，验证结果已应用）
```

关键崩溃窗口：

| 崩溃位置 | 恢复处理 |
| --- | --- |
| `prepared` 前 | 从上一个安全边界重新规划或继续 |
| `prepared` 后、结果未知 | 标记 `indeterminate`，先对账，不自动重放 |
| `observed` 后、`applied` 前 | 按 `call_id` 将同一结果恰好应用一次，预算恰好计费一次 |
| `applied` 后、验证前 | 不重复写入，直接进入验证 |
| 验证结果落盘后 | 不重复验证，继续汇总或下一步 |

首版对账优先支持项目内文件操作：比较目标路径、文件存在性、内容哈希、Git diff 和快照引用。任意命令执行若没有可靠探针，保持阻塞并向用户说明不确定性。

## 8. 项目指纹与漂移策略

检查点至少记录：

- 解析后的项目根目录；
- Git 仓库身份、HEAD、分支和 dirty 摘要；
- 相关文件的路径、存在性和内容哈希；
- 副作用前的 Git 快照引用（如有）；
- 执行 cwd、解释器和必要的非敏感运行环境标识。

恢复模式分为：

- `exact_resume`：检查点完整、无待确认副作用、项目指纹一致；
- `reconcile_then_resume`：存在不确定副作用，但能够通过文件/Git/工具探针确认；
- `replan`：项目发生可解释的外部变化，保留目标和预算，重新建立执行计划；
- `blocked`：检查点损坏、版本不兼容、项目错误、命令副作用无法确认或权限不足。

外部漂移不能被自动回滚。进入 `replan` 时要保留原检查点、差异摘要和决策原因。

## 9. 身份和恢复入口

恢复必须显式触发，建议最终提供：

- `resume(checkpoint_id)`；或
- `execute(..., resume_from=checkpoint_id)`。

不建议默认扫描目录并自动恢复“最近任务”，以免恢复错误项目或旧意图。

身份规则：

- 原 `run_id`、`root_task_id` 和 `session_id` 保持不变；
- 每次恢复新建 `resume_attempt_id`；
- subtask、step、call ID 沿用原层级；
- 被重放的只读调用使用新的 execution ID，并显式关联 `replay_of`；
- 不允许恢复过程把 subtask ID 提升为根任务 ID。

用户可见的恢复预检必须说明：恢复点、最后成功步骤、待对账动作、项目漂移、剩余预算以及将执行的恢复模式。

## 10. 持久化与完整性

建议布局：

```text
Code/data/task_trajectory/<run_id>/
  run.json
  events.jsonl
  artifacts/
  checkpoints/
    <checkpoint_id>.json
  latest_checkpoint.json
```

写入要求：

- 版本化 JSON；
- 临时文件写入、flush/fsync 后原子替换；
- 检查点内容校验和；
- `latest_checkpoint.json` 是经过校验的指针，不是唯一副本；
- 最新检查点损坏时，只能回退到前一个完整、兼容的检查点，并记录警告；
- 同一 run 的并发写使用锁或 compare-and-swap generation，拒绝旧 generation 覆盖新状态；
- 检查点成功落盘后再发出对应轨迹事件。

## 11. 分阶段实施

### Phase 0：冻结语义和建立故障注入骨架

范围：

- 补齐或修正缺失的 loop/session resume 文档入口；
- 画清 controller、session executor、tool loop、task executor 的状态所有权；
- 定义安全边界、副作用分类、恢复模式和 ID 规则；
- 建立不执行真实外部命令的 deterministic fake tool；
- 建立可在指定边界抛出模拟进程中断的 failure-injection harness。

退出条件：

- 每个恢复边界都有唯一名称和所有者；
- 每类工具都有 read-only / idempotent / mutating / externally-indeterminate 分类；
- 测试能稳定复现至少五个崩溃窗口。

### Phase 1：检查点 metadata 与原子存储，只写不恢复

TDD 顺序：

1. metadata 序列化、严格字段、版本和秘密排除测试；
2. 原子写入、校验和、损坏回退、并发 generation 测试；
3. 再实现 `RuntimeCheckpointMetadata` 和 `RuntimeCheckpointStore`；
4. 更新 metadata catalog 和 API 文档。

退出条件：

- runtime state 和预算可无损 round-trip；
- 半写文件不会成为 latest；
- 损坏 latest 可安全回退并产生证据；
- 还没有任何执行路径依赖恢复功能。

回滚：关闭 checkpoint writer，不影响现有 runtime。

### Phase 2：接入只读安全边界

范围：

- 在任务接收、路由、分解和只读工具结果应用后保存检查点；
- 轨迹层镜像 checkpoint 事件；
- 检查点失败时明确降级为“当前运行可继续，但不可承诺可恢复”，并记录状态。

退出条件：

- 正常执行产出顺序正确的检查点；
- 关闭功能开关时行为和现有版本一致；
- 诊断记录故障不会篡改 checkpoint 真相。

### Phase 3：只读路径精确恢复

范围：

- 增加显式 `resume(checkpoint_id)` 入口；
- 校验身份、schema、checksum、项目根和预算；
- 从无 pending mutation 的检查点恢复；
- 已应用结果不重复应用，已计预算不重复计费。

退出条件：

- 只读任务在每个注入点恢复后与不中断运行得到等价最终状态；
- 任务身份不漂移；
- 预算计数完全一致；
- 不支持的检查点明确拒绝，不静默从头运行。

### Phase 4：文件副作用对账与写后验证

范围：

- 接入 `prepared -> observed -> applied -> verified`；
- 文件写入、替换、创建、删除支持目标文件和 Git 对账；
- 崩溃后能够识别“写入已完成但结果未应用”；
- 外部修改触发 `replan` 或 `blocked`；
- 命令执行仍按默认不可自动重放处理。

退出条件：

- 故障注入矩阵中重复文件 mutation 次数为 0；
- 写入后恢复一定进入验证，不直接宣告成功；
- 用户改动在所有测试中均不被覆盖。

### Phase 5：命令和其他外部副作用的有限恢复

范围：

- 按工具逐个定义幂等键或对账探针；
- 只有证明安全的命令允许自动继续；
- 网络写、包安装、部署、删除等无可靠探针行为保持人工确认或阻塞；
- 恢复预检展示风险和证据。

退出条件：

- 每个开放自动恢复的工具都有独立契约测试；
- 未登记工具默认 fail closed；
- 不存在通用的“重新运行上一条命令”分支。

### Phase 6：验收、迁移和默认策略

范围：

- 子进程级 kill/restart 端到端测试；
- 旧 schema 迁移或明确拒绝策略；
- 检查点保留与清理策略；
- CLI/TUI 恢复提示；
- 3–5 个本地典型任务的人工审查。

退出条件见第 13 节。通过前保持 feature flag 默认关闭；通过后先只对只读和文件工具开放。

## 12. 测试矩阵

必须先写测试再改行为，至少覆盖：

### 契约与存储

- 严格 schema、未知字段、版本不兼容；
- runtime state、phase、budget、verification 完整 round-trip；
- secrets 和运行时句柄不会序列化；
- 临时文件残留、截断 JSON、错误 checksum；
- latest 损坏回退到上一代；
- 两个 writer 竞争时拒绝 stale generation。

### 恰好一次的状态应用

- 只读结果落盘前后中断；
- `observed` 后、state apply 前中断；
- state apply 后、checkpoint 前中断；
- 工具历史、预算、modified files 不重复追加；
- 同一 `call_id` 的重复结果被去重并留下说明。

### 文件副作用

- 写入前中断：允许执行一次；
- 写入完成但 tool event 前中断：识别文件已变化，不再写；
- tool event 后、state apply 前中断：只应用结果；
- 写后验证前中断：只运行验证；
- 用户在暂停期间编辑同一文件：不覆盖，进入重规划或阻塞；
- 创建、替换、删除分别测试；
- Git 不可用时使用文件指纹并降低恢复置信度。

### 命令与不可确认副作用

- 无探针命令停在 `blocked`；
- 已登记的幂等命令按幂等键恢复；
- 超时命令结果未知时不盲目重跑；
- 权限或审批状态不会因恢复被绕过。

### 身份、预算和终止

- run/root task/session 保持不变，resume attempt 新建；
- subtask 不覆盖 root task；
- 所有预算消耗和不中断基线一致；
- 预算耗尽后恢复仍停止；
- 只有显式预算扩展才允许继续；
- 已完成任务恢复返回“已完成”，不再次执行；
- 错项目根、错误 run、丢失 artifact 明确拒绝。

### 端到端

- 用独立子进程在指定边界强制退出，再启动新进程恢复；
- 对相同 deterministic fake task 比较不中断与恢复后的最终状态、文件结果、预算和事件；
- 在多个连续恢复轮次后仍能收敛，不形成恢复循环。

## 13. 最终验收标准

第一阶段生产可用门槛：

- 故障注入矩阵中，重复 mutation 工具执行次数为 0；
- 恢复后预算计数与不中断基线完全一致；
- 已应用工具结果和验证结果不会重复应用；
- 外部文件漂移不会被静默覆盖；
- 损坏、错误项目和不兼容检查点均 fail closed；
- 每次恢复均记录：恢复点、检查证据、模式、原因、剩余预算和下一动作；
- 只读任务和文件修改任务各至少有一个真实本地端到端样例；
- 全部测试离线、确定性、可重复；
- feature flag 关闭时现有行为不变。

暂不把“所有命令均可自动恢复”作为验收条件。对无法可靠对账的命令，安全阻塞就是正确结果。

## 14. 可观察性和可解释记录

建议新增或规范以下轨迹事件，但事件 payload 优先复用 metadata，不为事件名重复造模型：

- `checkpoint_created`
- `checkpoint_write_failed`
- `resume_requested`
- `resume_preflight_completed`
- `side_effect_reconciliation_started`
- `side_effect_reconciliation_finished`
- `runtime_resumed`
- `runtime_resume_blocked`

一次恢复预检应形成结构化结论：

- `decision`: exact / reconcile / replan / blocked
- `checkpoint_id`
- `last_safe_boundary`
- `pending_action`
- `project_drift`
- `budget_remaining`
- `evidence_refs`
- `reason`

## 15. 发布和回滚策略

- 使用独立 feature flag 控制 checkpoint 写入和 resume 入口；
- 先开启“只写检查点”，观察存储完整性；
- 再开启只读 exact resume；
- 再开放文件副作用对账；
- 命令类按工具白名单逐个开放；
- 任一阶段发现不一致，关闭 resume 入口但保留轨迹和检查点供诊断；
- 不通过删除旧检查点来掩盖兼容问题，使用 schema 版本和明确迁移。

## 16. 非目标

- 不建设分布式调度器或通用事务框架；
- 不在本阶段重构记忆或上下文系统；
- 不自动恢复破坏性命令、部署或外部系统写操作；
- 不把 Git 当数据库，也不自动回滚用户工作区；
- 不依赖终端文本反推状态；
- 不开展 mini-SWE 等大规模 Agent benchmark；
- 不为了未来可能性抽象所有工具，只覆盖当前真实恢复边界。

## 17. 文档同步清单

实现过程中按实际变化同步：

- `AGENTS.md`
- `API.md`
- `README.md`
- `Code/README.md`
- `AGENT_LOOP_PROTOCOL.md`
- `AGENT_LOOP_SUPERVISOR.md`
- `AGENT_LOOP_GOAL.md`
- `AGENT_LOOP_SESSION_RESUME.md`
- `docs/testing/TEST_DESIGN_GUIDE.md`
- task trajectory 的 event alignment、ID stratification、architecture 和 implementation log
- metadata catalog（若新增 checkpoint metadata）

其中四个根目录 loop 文档当前缺失。Phase 0 应先决定是恢复这些文档，还是修正 `AGENTS.md` 的引用；在此之前不能让实现依赖不存在的协议。

只有某个行为问题真正实现并通过验证时，才更新 `docs/task_trajectory/IMPLEMENTATION_LOG.md`。本计划本身不登记为已完成修复。

## 18. 建议的首个生产切片

第一刀只做 Phase 0–3：

1. 严格 checkpoint contract；
2. 原子 checkpoint store；
3. 只读安全边界落盘；
4. 显式 checkpoint ID 恢复；
5. 状态、身份和预算恰好恢复；
6. 不允许 pending mutation 的检查点自动继续。

这个切片已经能验证断点恢复的骨架是否可靠，同时把风险控制在最低。通过后再进入文件副作用对账，避免一开始同时修改 controller、工具执行、Git 保护和 CLI 多个核心边界。
