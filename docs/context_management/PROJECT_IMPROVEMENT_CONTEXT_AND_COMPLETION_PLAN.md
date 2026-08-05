# Project Improvement Context and Completion Plan

## 1. 目标与范围

本计划处理两个相互关联、但所有权不同的问题：

1. `project_improvement` 流水线尚未按调用目的拆分模型上下文，导致后续阶段把大量项目事实作为一个不可截断整体；
2. 核心任务成功后，改进流水线失败是否应令顶层任务失败，目前没有明确、类型化的完成语义。

本轮只收紧已有架构，不增加通用关系层、第二套上下文存储或新的
`MetadataKind`。实施继续遵守测试先行、最小变更和阶段闸门；每个阶段开始前应写出该阶段的具体测试与修改计划。

## 2. 已确认的真实失败点

完整架构观察实验中，`project_improvement_tool` 已经返回改进分析。真正抛出
`ContextAssemblyBudgetError` 的调用是随后 `AutonomousIterationAgent._design_tasks`
发出的 `iteration_task_design` 请求，而不是先前的改进分析请求。

当前 `_design_tasks` 把以下内容拼成一个 user message：

- 固定 Task Designer 指令和输出 schema；
- 已选择的改进目标；
- `ProjectStateSnapshot` 的 JSON 投影；
- 完整 `improvement_report` JSON；
- 已完成改进次数和其他控制文本。

`_complete_json` 又为该 message 设置 `user_truncation=forbidden`。共享请求构造器以
message 为候选粒度，因此这整块内容成为一个 required、不可截断候选。任一低价值部分增长，都会使整个控制请求无法装入，而不是只省略文件预览、历史证据或重复的 Prompt Context。

`project_improvement_tool` 本身也使用相同的单 message 形态，并在 message 内混合目标、
validation、rubric、README 和多个文件 preview。它目前不是这次实验的直接异常位置，但与
`iteration_goal`、`iteration_task_design` 共同缺少 purpose-specific 上下文所有权，必须在同一
改进流水线边界内审视，不能把异常笼统归因为“给 project improvement 的预算太小”。

## 3. Inventory 与重复性审计

### 3.1 已审阅的既有契约

上下文选择与质量：

- `ContextRequestPurpose.PROJECT_IMPROVEMENT`
- `ContextRequestPurpose.ITERATION_GOAL`
- `ContextRequestPurpose.ITERATION_TASK_DESIGN`
- `ContextCandidate` 及其 kind、retention、truncation、trust、freshness
- `ContextAssemblyPolicy`
- `ContextAssemblyResult`
- `ContextSelectionMetadata`
- `ContextQualityExpectation`
- `ContextQualityEvaluation`
- `DurableArtifactReference`

项目、任务与证据：

- `ProjectStateMetadata`
- `ProjectObjectiveMetadata`
- `ProductIntentMetadata`
- `SuccessMetricMetadata`
- `GitDiffContextMetadata`
- `ImprovementCandidateMetadata`
- `ImprovementAnalysisMetadata`
- `ExecutionStateMetadata`
- `TaskResultMetadata`
- `ModuleExecutionMetadata`

实际 producer/consumer：

- `ImprovementContextHelper` 生产派生的 Prompt Context；
- `project_improvement_tool_executor` 读取目标、验证、README 和项目文件并请求改进分析；
- `AutonomousIterationAgent` 的 Goal Maker 与 Task Designer 消费项目状态、分析报告和选择目标；
- `ProjectImprovementRuntime` 生产改进流水线结果；
- `IntelligentAutopilot` 和 `_RuntimeSessionExecutor` 合并核心执行与改进结果；
- enhanced CLI、iteration dashboard、trajectory 与日志消费改进次数和失败信息。

### 3.2 复用决定

上下文拆分继续使用现有 `ContextCandidate`、`ContextAssemblyPolicy`、
`ContextSelectionMetadata` 和离线质量契约。项目文件、diff、验证结果、改进分析和 artifact
仍由各自已有模型或存储拥有；专属 adapter 只生成 model-facing 派生投影。

不新增以下内容：

- 不新增 project-improvement 专用 `MetadataKind`；
- 不新增第二个 context selection 或 budget owner；
- 不把完整 `ProjectStateMetadata`、`improvement_report` 或历史日志复制进新的持久化树；
- 不用 `annotations`、`details`、异常文本或 Prompt 文本控制完成状态。

`SuccessMetricMetadata.required` 不可复用为改进阶段必要性。它表示领域成功指标是否为硬指标，
而不是“改进流水线失败是否阻断本次顶层任务成功”。`AutonomyDecisionMetadata.decision` 也是自由文本，
不适合作为完成分支权威。

## 4. Metadata impact note

```text
Fact:
  核心任务成功后的 project improvement 是 disabled、optional 还是 required；
  以及该政策的来源、目标成功轮数和最大尝试数。

Authoritative producer:
  IntelligentAutopilot 的运行配置解析。用户显式选择或目标验收要求可改变政策，
  但 ProjectIterationHelper、IterationAgent、controller 和 UI 只能消费同一政策，不能各自改写副本。

Consumers:
  ProjectImprovementRuntime、AutonomousIterationAgent、_RuntimeSessionExecutor、
  IntelligentAutopilot fast path、enhanced CLI/dashboard、trajectory/result projection。

Lifecycle:
  runtime-only policy；必要时随现有运行/轨迹结果按值序列化，不形成独立项目状态或新存储。

Control impact:
  completion、routing、budget。

Existing contracts reviewed:
  RuntimeStateMetadata、RuntimeReportMetadata、ExecutionStateMetadata、
  ModuleExecutionMetadata、ImprovementAnalysisMetadata、AutonomyDecisionMetadata、
  SuccessMetricMetadata、ContextCandidate、ContextAssemblyPolicy、ContextSelectionMetadata。

Decision:
  新增 strict owned ProjectImprovementPolicy 和相关 enums；
  上下文继续复用既有 candidate/policy/quality contracts；不新增 MetadataKind。

Why no duplicate source of truth is created:
  policy 由配置解析一次；enable_iterative_improvement、required_successful_improvements
  和 max_iteration_attempts 仅作为兼容输入或从 policy 派生的兼容 view。
  运行结果、项目事实和上下文选择仍由原有契约拥有。

Serialization and migration:
  owned values 使用严格序列化并拒绝非法组合；历史构造参数保留读取映射。
  新 producer 不再并行维护 bool/count/policy 三套权威状态。

Tests:
  enum/policy 合法组合、JSON round trip、兼容映射、唯一 owner、完成矩阵、
  purpose-specific candidate selection、预算失败前置、离线质量 fixtures、完整架构复验。

Documentation updates:
  API.md、docs/metadata/CONTRACT_CATALOG.md、AGENT_LOOP_GOAL.md、
  AGENT_LOOP_PROTOCOL.md、docs/testing/TEST_DESIGN_GUIDE.md、
  docs/task_trajectory/IMPLEMENTATION_LOG.md；若 CLI 表面变化，再同步 Code/README.md。
```

## 5. 目标契约与合法状态

新增 owned value，而不是公共 Metadata owner：

- `ProjectImprovementRequirement`：`disabled | optional | required`；
- 一个类型化的 policy source enum，例如 `default | runtime_config | user_selection | goal_acceptance`；
- `ProjectImprovementPolicy`：至少拥有 requirement、source、目标成功改进数和最大尝试数。

合法组合应 fail closed：

- `disabled` 的目标成功数必须为 0，且不会调度改进流水线；
- `optional` / `required` 的目标成功数必须大于 0；
- 最大尝试数不得小于目标成功数，并继续满足有界 repair buffer；
- 自由文本 reason 可以解释政策，但不能改变分支；
- controller、fast path 和 UI 不得根据“required”字样、失败文案或次数是否非零推断政策。

兼容映射：

- `enable_iterative_improvement=False` 或目标次数为 0：`disabled`；
- 用户显式选择大于 0 的次数，或目标验收明确要求达到改进质量门：`required`；
- 自动默认执行的增强轮次：`optional`；
- 若需要严格保持旧调用方语义，显式传入旧的正数 `required_successful_improvements`
  可迁移为 `required`，但默认行为变化必须在 API 和测试中明确记录。

`ProjectImprovementPolicy` 是唯一完成政策。旧字段在兼容期只能是 constructor input 或只读 derived
property；禁止 `ProjectIterationHelper` 同时修改 autopilot 和 nested iteration agent 中的多个独立计数。

## 6. Purpose-specific 上下文分层

专属 adapter 应直接向 `ContextRequestBuilder.build` 提交候选，而不是先拼接大 Prompt 后调用
message-level adapter。候选建议如下：

| 内容 | kind | retention | truncation | 说明 |
| --- | --- | --- | --- | --- |
| 固定角色、输出 schema、禁止泄露推理 | `instruction` | required | forbidden | 必须完整 |
| 当前改进目标与可观察验收 | `task` | required | forbidden | 必须完整 |
| 权限、非回归、产品表面和 stack 安全约束 | `constraint` | required | forbidden | 必须完整 |
| 确定性压缩后的核心任务结果与验证摘要 | `runtime_evidence` | required | forbidden | producer 先压缩；仍放不下才是真预算不足 |
| 选中 diagnosis、quality rubric、当前 preset 摘要 | `runtime_evidence` | preferred | head | 不重复完整 Prompt Context |
| README 摘要 | `project_file` | preferred | head | 独立候选 |
| 每个相关代码文件或 diff 投影 | `project_file` / `runtime_evidence` | preferred/optional | head/tail | 按相关性逐项选择 |
| 历史报告、完整日志、低价值 memory | `artifact` | optional | head | 优先省略或只保留引用 |

required 候选必须自身紧凑、边界稳定。不能把任意增长的完整 JSON 标记 required，再依赖增加预算解决。
候选应保留已有 source ID 或 artifact reference；adapter 不成为事实来源。

首个直接修复目标是 `iteration_task_design`。随后用同一候选规范审视 `iteration_goal` 和
`project_improvement`，避免上游分析成功后在下游再次复制完整项目状态和报告。

## 7. 顶层完成语义

顶层结果必须区分核心执行和增强结果。`overall success` 是组合结果，不应覆盖或丢失
`core success`：

| 核心结果 | 改进政策 | 改进结果 | 顶层结果 |
| --- | --- | --- | --- |
| fail | 任意 | 任意 | fail |
| success | disabled | skipped | success |
| success | optional | success | success |
| success | optional | fail / budget insufficient | success，并记录 improvement warning/failure evidence |
| success | required | success | success |
| success | required | fail / budget insufficient | fail |

`partial_success` 不能替代该矩阵。改进流水线的 module success、失败阶段和失败证据继续保留；
policy 只决定其是否阻断 overall success。fast path 与标准 session 必须使用同一个纯组合函数，
避免再次出现两套完成语义。

首阶段复用 `ModuleExecutionMetadata`、`ImprovementAnalysisMetadata` 和现有运行结果作为执行证据，
不创建 `ProjectCompletionMetadata`。只有 checkpoint 或 supervisor 出现独立持久化、恢复 consumer 时，
才重新评估扩展现有 runtime report/session owner，而不是预先增加新 contract。

## 8. 分阶段实施计划

### 阶段 A：完成政策契约与组合函数

实施前先写：

- policy/enums 的合法与非法组合测试；
- 历史参数到 policy 的兼容映射测试；
- 六种核心/改进完成矩阵测试；
- fast path 与 standard session 一致性测试。

实现：新增 owned policy values；建立唯一配置 producer；用一个纯函数派生 overall success；旧字段降为兼容 view。

退出闸门：所有完成分支只读取 typed policy；optional 失败不再抹掉核心成功；required 失败仍 fail closed。

### 阶段 B：`iteration_task_design` 专属候选装配

实施前先写：

- oversized project state/report 不再形成单个 required candidate；
- goal、schema、安全约束和验证摘要完整保留；
- 大型可选文件/历史被逐项省略后 assembly 仍为 ready；
- required safety 或紧凑验证摘要确实放不下时，provider 前返回 typed budget failure；
- request purpose、selection evidence 和候选 source ID 完整。

实现：把 Task Designer 固定指令、目标、约束、验证摘要和按需项目证据拆为 typed candidates；删除该路径的单块 forbidden user message。

退出闸门：复现实验输入时不再在 `iteration_task_design` 因低价值内容增长失败，且 schema/约束无截断。

### 阶段 C：统一改进流水线上下文边界

实施前先分别为 `iteration_goal` 和 `project_improvement` 写候选清单与 fixture，不把阶段 B 的选择策略盲目复制。

实现：复用共享的小型 adapter/helper，但每个 purpose 明确自己的 required/preferred/optional 候选；移除完整
`prompt_context`、project state 和 improvement report 的重复 model-facing 副本。

退出闸门：三个 purpose 都有候选级 selection evidence；不存在新的生产单 message 大块回退。

### 阶段 D：离线质量标定

使用既有 `ContextQualityExpectation/Evaluation`，建立固定语料：

- 紧预算仍选中全部 required 候选；
- oversized optional history 被省略且 assembly 为 ready；
- 代码、README、diff 可独立选择；
- 低价值历史不能挤掉验证摘要；
- required constraint 超预算在 transport/cache 前失败；
- 相同输入、预算和策略产生稳定 selection。

这些 fixtures 只声明显式结构期望，不宣称自动判断开放语义相关性。

退出闸门：质量 fixture、metadata round trip、静态生产 caller inventory 全部通过。

### 阶段 E：完整架构 A/B 与收尾

冻结同一任务、provider、预算和随机条件，至少比较：

- 核心任务成功率与真实验证证据；
- 改进各阶段成功/失败位置；
- 每次请求输入、输出、reasoning 和总 Token；
- 候选 keep/partial/omit 分布；
- optional/required 两种顶层完成语义；
- 调用次数、fallback、重试和改进结果质量。

收益判定不能只看 Token 或调用次数下降；权限、验证和完成语义必须先正确。若仍失败，使用 typed
selection/trajectory evidence 定位具体 required candidate，不通过提高全局预算掩盖问题。

退出闸门：目标实验不再在 `iteration_task_design` 因单块上下文失败；optional 与 required
完成矩阵均有真实或确定性端到端证据；全量测试、compileall 和 diff check 通过。

## 9. 测试、迁移与文档门

每个行为修改必须先有失败测试。最低测试集合：

- metadata construction、assignment validation、JSON round trip、历史参数读取；
- context candidate 的 retention/truncation/source identity 和 selection evidence；
- pre-transport budget failure；
- runtime controller 与 fast path 的完成矩阵；
- UI/trajectory 不把 optional improvement failure 显示成核心任务失败；
- provider failure、fallback 和空分析结果不绕过 required policy；
- 完整架构观察实验与独立产物验证。

文档同步门：

- `API.md`：政策、候选边界、overall success 语义和兼容行为；
- `docs/metadata/CONTRACT_CATALOG.md`：owned policy values 与 purpose-specific context alignment，公共 contract 数不变；
- `AGENT_LOOP_GOAL.md`：核心验收与 optional/required enhancement；
- `AGENT_LOOP_PROTOCOL.md`：policy owner 和完成组合规则；
- `docs/testing/TEST_DESIGN_GUIDE.md`：候选质量及完成矩阵；
- `docs/task_trajectory/IMPLEMENTATION_LOG.md`：观察失败、验证证据、修复和剩余限制；
- `Code/README.md`：仅在 CLI/config 表面增加或改变 requirement 选项时同步；
- `AGENT_LOOP_SUPERVISOR.md`：仅在 supervisor 实际消费改进 outcome 进行调度时同步；
- `AGENTS.md`：本计划不改变项目级开发约定，无需修改。

## 10. 非目标与停止条件

本轮不做：

- 通用语义检索器或新的关系图；
- 通过扩大默认上下文窗口解决单块 Prompt；
- 把任意完整日志持久化进 selection metadata；
- 重写 project improvement 的业务目标生成算法；
- 把 optional failure 静默丢弃。

若 required 目标或安全约束本身超过预算，应保留 typed budget failure 并停止该改进阶段；
若 policy 为 optional，核心结果仍成功但必须暴露增强失败证据；若 policy 为 required，则顶层失败。

## 11. 实施结果（2026-08-04）

- 阶段 A 已完成：`ProjectImprovementPolicy` 成为完成政策权威；运行状态和报告分别保留
  `core_success`、改进 status 与失败证据。自动默认是 optional，用户显式正数是 required，0 为 disabled。
- 阶段 B/C 已完成：`iteration_task_design`、`iteration_goal` 和 `project_improvement` 均改为
  purpose-specific candidates；固定指令、目标、安全约束和紧凑验证摘要为 required，文件、README、
  diagnosis、memory 和历史证据可独立选择或省略。
- 阶段 D 已完成：真实 adapter 的确定性 fixture 验证 oversized optional evidence 不会挤掉 required
  内容，required 内容确实放不下时仍在 provider 前 fail closed；三个 purpose 均保留 selection evidence。
- 阶段 E 的确定性验收和全量回归已完成，`Code/tests` 为 `719 passed`。真实 provider 复验
  `20260803T180924Z` 与 `20260803T181040Z` 分别在上游 task decomposition、compound validation evidence
  阶段提前结束，没有到达 project improvement，因此不能作为改进前后的 Token 或质量反事实。

当前结论只覆盖结构性上下文膨胀和完成语义：单块不可截断 project-improvement Prompt 已移除，optional
增强失败也不会抹掉核心成功。

- 阶段 F 已完成：在既有 `RuntimeBudgetMetadata` 下增加独立的 post-core enhancement completion pool，
  覆盖 `project_improvement`、`iteration_goal`、`iteration_task_design` 与 improvement-owned
  `code_generation`。purpose floor/ceiling 与 complexity、remaining value、prompt size、remaining calls、
  remaining total 共同决定预留；logical key、reservation/reconciliation ledger 与 provider replay hash
  支持 checkpoint apply-once。known failure usage 会计费，unknown usage 保守占用；length 只允许一次窄
  JSON 恢复，截断代码禁止写入。required 语义 fail closed，optional 才允许受控 fallback。

尚未证明真实 provider 下的总 Token 收益；后续实验必须先固定可重复的上游任务轨迹，或采用录制回放
隔离本次变量。controller decision pool 与 enhancement pool 是两个独立预算，不能把二者误写成一个全局
12,000-token 上限。
