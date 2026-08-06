# Root Task / Subtask State Isolation and Evidence-Backed Completion Plan

## 1. 目标

本计划修复真实 coding 任务中出现的状态与证据不一致：根任务要求修改文件，首个
`inspect` 子任务却把共享 runtime state 永久标记为只读；后续写入需求被安全路由
静默裁掉，仅剩的文件读取成功后，执行器又把整个 implement 子任务标记为完成。

完成本计划后，系统必须满足：

- 根任务执行模式是权威状态，子任务不能扩大或缩小根任务权限；
- 子任务的阶段、阻断原因和临时判断不会污染其他子任务；
- Guard 拒绝是显式执行结果，不会因返回空 selection 而消失；
- 子任务成功表示必需需求和交付物有执行证据，而不只是剩余工具没有报错；
- `changed_files` 只表示观察到的文件副作用，不再复制计划中的 `write_files`；
- UI、runtime state、checkpoint 和 trajectory 对同一次执行给出一致状态；
- 同一个隔离真实任务能够实际修改文件、运行测试并留下可解释轨迹。

本切片不重写完整任务编排框架，不引入新的状态机框架，也不扩大工具权限。

## 2. 已确认的真实失败

真实运行 `6e76e501a419417abb4f6d712ea5a827` 提供了以下证据：

1. 根任务被识别为 `coding`，预期交付物是修改后的 `calculator.py` 和测试结果；
2. 模型为 implement 子任务返回了 `file_read`、`code_symbol_modify` 和
   `command_check`，没有声称已经完成；
3. 首个带 `inspect` tag/kind 的子任务通过 `_planning_runtime_state()` 向共享
   `RuntimeStateMetadata.assumptions` 写入 `runtime_mode:read_only_analysis`；
4. `ToolRouter.route()` 对写入需求返回空列表并把共享 state 置为 blocked；
5. 同一批需求中先路由成功的 `file_reader` 仍被执行；
6. `ToolEventLoopRunner` 将“没有 recoverable tool error”解释为成功，没有检查
   被拒绝需求和预期交付物；
7. implement 子任务被标记 completed，但文件没有变化；
8. `ExecutionStateMetadata.changed_files` 仍从计划中的 `task.write_files` 推导出
   `calculator.py`；
9. validate 子任务因所有动作都不可路由，最终以 empty decision-needs 错误失败。

## 3. 核心不变量

### 3.1 权限所有权

- 根任务执行模式只能由用户约束、根任务卡或根目标决定。
- 子任务的 `kind`、tag 和描述只能约束本子任务的动作，不得改写根任务执行模式。
- `read_only` 根任务不得因子任务要求而升级为可写。
- `mutation_allowed` 根任务中的 inspect 子任务可以只读，但后续 implement 仍可写。
- checkpoint 必须持久化并恢复根任务执行模式及其来源。

### 3.2 状态作用域

根任务共享：

- 执行模式与权限来源；
- run/root task/session 身份；
- 项目指纹；
- 总预算与已消耗预算；
- 已观察工具结果、文件副作用和验证事实。

子任务局部：

- 当前 phase；
- 当前阻断/失败原因；
- 当前 planning needs 和 guard decisions；
- 当前 task 的临时 assumptions、unknowns 和 completion reason。

本切片优先用窄改动实现所有权边界，不立即复制整份 runtime state。若现有共享
对象无法安全约束，再增加最小的 scoped metadata，而不是复制新的状态机。

### 3.3 计划与事实分离

- `Task.write_files` 是计划意图，不能作为已发生副作用。
- 文件写入事实必须来自成功工具结果和 `ObservedFileMutationResult`/状态更新。
- 验证事实必须来自实际 command/tool result。
- 任意汇总字段不得把 planned 值提升为 observed 值。

### 3.4 成功语义

一个子任务只有在以下条件全部成立时才能 completed：

- 所有必需 decision needs 都有明确处置；
- 没有被 Guard 拒绝或无法路由但被静默忽略的必需 need；
- 至少有一项实际执行证据，除非是明确允许 synthesis 的只读任务；
- implement/write 任务存在成功且已应用的目标文件副作用；
- validate 任务存在实际验证结果；
- 预期交付物能够引用工具结果、文件证据或验证证据。

## 4. Metadata 设计

### 4.1 根任务执行模式

优先在现有 `RuntimeStateMetadata` 增加严格字段：

- `execution_mode`: `read_only | mutation_allowed`
- `execution_mode_source`: `user_constraint | root_task_card | root_goal | default`
- `execution_mode_reason`: 简短可解释文本

字符串 assumption 仅作为旧 checkpoint 输入的兼容入口；新运行的权限判断不得继续
依赖自由文本 assumption。加载旧 payload 时，将旧 marker 迁移为 typed 字段。

### 4.2 路由处置

需要一个严格、窄职责的路由结果，至少区分：

- approved selections；
- blocked guard decisions；
- unresolved decision needs。

先评估是否可扩展现有 `GuardDecisionMetadata` 和 task result attributes；只有跨模块
确实需要稳定传输时才新增公开 metadata。不得把 blocked need 塞进无约束字符串。

### 4.3 文件事实

保留以下语义：

- `Task.write_files`: planned；
- `RuntimeStateMetadata.modified_files`: observed/applied；
- `ObservedFileMutationResult`: 单次文件动作的前后哈希事实；
- `ExecutionStateMetadata.changed_files`: 仅汇总 observed/applied 文件。

若现有结果 metadata 已能表达来源，不新增重复的 `verified_modified_files` 字段；验证
状态继续由 `verification_status` 和验证结果承担。

## 5. 实施阶段

### Phase 0：锁定真实故障回归

先增加失败测试，覆盖：

1. coding 根目标 + inspect 子任务不会改变根 execution mode；
2. 同一计划中 read 被批准、write 被拒绝时，子任务不能 completed；
3. implement task 只有 file_reader 成功时不能 completed；
4. 没有写入工具结果时 `changed_files` 为空；
5. Guard 拒绝在结果/轨迹中可见；
6. validate task 没有 command result 时不能 completed；
7. 旧 read-only checkpoint/assumption 兼容迁移。

停止条件：测试能在未修代码上稳定复现，不依赖网络或真实模型。

### Phase 1：根任务权威执行模式

- 为 runtime state 增加 typed execution mode 字段及兼容迁移；
- `AgentRuntimeController.run()` 只在根任务入口解析执行模式；
- task card 产生后允许以根任务证据补充模式，但不能由子任务 tag 改写；
- `_planning_runtime_state()` 不再向共享 state 应用子任务 read-only mode；
- prompt 仍可以告诉 inspect 子任务只使用只读工具，但这只是局部规划约束；
- checkpoint/resume 保留 typed 模式。

停止条件：根 coding 任务经过 inspect 子任务后仍为 mutation allowed，根 read-only
任务经过任意子任务后仍保持 read-only。

### Phase 2：Guard 拒绝显式传播

- 路由一批 decision needs 时保留 approved/blocked/unresolved 三类结果；
- Guard 拒绝不能只返回空 selection；
- blocked need 记录原 need、选择的工具、Guard 理由和作用域；
- 必需 need 被拒绝时，tool loop 返回 blocked/failed，不执行后续会造成错误成功的
  部分计划；
- 不改变现有高风险确认和预算策略。

停止条件：部分路由成功不能掩盖同批关键动作被拒绝。

### Phase 3：证据化完成判定

- tool loop 成功需要验证 routed needs 的处置完整性；
- 空工具列表只允许明确的只读 synthesis 分支成功；
- implement/write task 检查实际文件副作用；
- validate task 检查实际 command/verification result；
- TaskExecutionResult 明确区分 completed、failed、blocked/unverified（优先复用现有
  TaskStatus；不为 UI 单独发明状态）；
- UI 继续消费 TaskExecutionResult，不在展示层推断业务成功。

停止条件：不存在“绿色完成但无对应工具/副作用证据”。

### Phase 4：修正 changed_files 和轨迹一致性

- `ExecutionStateMetadata.changed_files` 从成功结果中的 observed file evidence 或
  runtime state `modified_files` 汇总；
- 移除从所有 `task.write_files` 无条件推导 changed_files 的行为；
- Guard 拒绝和 completion rejection 进入 trajectory；
- task finished、checkpoint 和 summary 对 phase/status/modified files 保持一致；
- 不记录密钥、完整环境变量或无关模型内容。

停止条件：计划写入但未执行时，所有 observed changed-file 字段为空；真实写入后只
包含实际目标文件。

### Phase 5：文档和真实任务验收

同步检查：

- `API.md`
- `docs/metadata/CONTRACT_CATALOG.md`
- `docs/task_trajectory/README.md`
- `docs/task_trajectory/TASK_TRAJECTORY_EVENT_ALIGNMENT.md`
- `docs/task_trajectory/TASK_TRAJECTORY_ID_STRATIFICATION.md`
- `docs/task_trajectory/IMPLEMENTATION_LOG.md`
- `docs/testing/TEST_DESIGN_GUIDE.md`
- 根目录 loop/recovery 协议文档

真实验收沿用隔离 calculator fixture：

1. 基线 `1 failed, 1 passed`；
2. 根任务要求只修改 `calculator.py` 并运行 pytest；
3. 轨迹应出现 inspect read、实际 file mutation、verification command；
4. 文件内容应抛出包含 `zero` 的 `ValueError`；
5. pytest 应全部通过；
6. implement/validate 状态必须有对应证据；
7. `changed_files` 只能包含 `calculator.py`；
8. run 最终状态为 completed；
9. checkpoint 的 execution mode、预算、modified files 与轨迹一致。

若真实模型产生不同但合法的计划，以不变量判定，不绑定具体 prompt 文本或调用次数。

## 6. 测试矩阵

| 场景 | 预期 |
| --- | --- |
| coding root → inspect → implement | inspect 不污染根权限，implement 可写 |
| read-only root → model 提议 write | Guard 显式 blocked，根权限不升级 |
| approved read + blocked write | 整个 implement 不得 completed |
| write task only reads file | unverified/failed，modified files 为空 |
| write succeeded, verification pending | modified file 可观察，任务不得声称 verified |
| validation command succeeded | validate completed，验证证据可引用 |
| planned write only | changed_files 为空 |
| observed write | changed_files 包含实际目标且无重复 |
| old checkpoint with read-only marker | 迁移为 typed read-only mode |
| resume after inspect checkpoint | 根 coding execution mode 保持可写 |

## 7. 风险与约束

- 不把默认模式简单设成 unrestricted；缺少根任务证据时沿用现有风险策略并记录来源。
- 不通过关闭 Guard 来让真实任务通过。
- 不允许 prompt 层约束代替 runtime 权限检查。
- 不把每个子任务完整复制成新的 runtime/controller，除非窄所有权修复无法满足测试。
- 不用 UI 状态修补业务状态。
- 不因真实模型输出差异放宽 metadata 校验。
- 保留现有 checkpoint fail-closed 语义和用户项目漂移保护。

## 8. 完成定义

本 Goal 只有同时满足以下条件才完成：

- 真实失败的四段因果链均有确定性回归测试；
- typed 根执行模式和旧数据兼容通过 metadata 序列化测试；
- Guard 拒绝不会静默消失；
- 完成状态与真实副作用一致；
- changed-files 语义修正；
- 定向测试和全量测试通过；
- 隔离真实模型任务成功；
- 文档和实施日志同步；
- 未修改或清理用户无关工作树内容。

## 9. 实施结果（2026-08-02）

- 已增加 typed 根执行模式、来源、原因和旧 assumption 迁移；
- 子任务规划不再根据自身 inspect/validate tag 改写共享权限；
- Guard 拒绝进入 `guard_history` 与 `decision_need_blocked` 轨迹，并使必需 need 失败；
- implement/write 与 validate 完成状态改为检查实际 mutation/command 证据；
- `changed_files` 与 written-files 汇总只消费 observed mutation；
- symbol 修改计划缺少持久化 writer 时，按同一目标合成一次受 Guard 控制的 patch writer；
- 子任务写入受 `Task.write_files` 范围约束，inspect/validate 不允许 mutation need；
- 修复 fallback cwd 选择和修复任务误触发 README 自动生成的越界副作用；
- 隔离 calculator 真实任务已实际修改唯一目标文件并通过 `2 passed`，轨迹、checkpoint
  和 execution state 均只记录 `calculator.py`。

最终全量测试与静态检查结果记录在实施日志中。

### 跨进程端到端补充验收

后续真实中断测试发现并修复了两个恢复缺口：原 tool loop 的 required validation
未进入 checkpoint，以及新进程 recorder 的内存 alias 为空时错误创建第二个 run。
修复后，file mutation checkpoint 持久化 typed `pending_verification`，恢复进程执行
原 pytest；recorder 显式绑定 checkpoint run，恢复事件序号连续且不再分叉。
