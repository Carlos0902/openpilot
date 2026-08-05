# Code Generation Context Plan

## 1. 目标

修复 autonomous project improvement 在 `code_generator` transport 前发生的
`ContextAssemblyBudgetError`，同时完整保留任务目标、写权限、产品意图、非回归约束、目标代码和输出要求。

本轮只治理 model-facing 输入投影，不扩大默认预算、不降低 Guard、不放宽文件写范围、不把整文件生成成功
当作文件修改证据，也不同时调整 completion budget。

## 2. 已确认失败

完整架构 run `20260803T183221Z` 已通过核心执行、pytest、compileall、project-improvement analysis 和
Task Designer。Task Designer 生成的改进任务只允许修改 `calculator.py`，但 improvement execution 的 full
code-generation attempt 将约 36,586 字符的上下文作为单个 required/forbidden message 装配，在 provider
transport 前失败，完成改进 0/1。

当前 `CodeGenerator._build_contextual_prompt`：

1. 将完整 `prompt_context` 以格式化 JSON 全量复制；
2. 再从同一对象重复展开 quality rubric；
3. 再重复展开 product intent、dependency strategy、stack preset 和 UI contract；
4. 与固定工具说明合并为一个不可截断 user message。

因此低价值 diagnosis、environment、product judgment 和重复历史只要增长，就会把任务、安全边界、当前代码
与 schema 一起阻断。full/compact/surgical retry 虽在上层生成不同 prompt context，但首个 assembly budget
异常当前不一定进入后续 retry，且三种模式仍会经过相同单块 prompt builder。

## 3. Inventory 与重复性审计

已审阅：

- `Task`：任务 kind、read/write files、依赖和 validation command 的计划权威；
- `ToolInputMetadata`：现有工具兼容输入及 operation kind、target file、prompt context；
- `CodeGenerationRequest`：tool-local task、language、allowed imports、forbidden operations、max lines；
- `ProductIntentMetadata`：delivery surface、runtime mode、capabilities、non-regression constraints、
  disallowed substitutions；
- `ContextCandidate`、`ContextAssemblyPolicy`、`ContextSelectionMetadata`、`ContextQualityExpectation`；
- `ImprovementContextHelper`：project/product/stack/UI 派生上下文；
- `AutonomousTaskExecutor`：full/compact/surgical attempt、target selection、tool routing、review/write/validation；
- `CodeGenerator`：tool Prompt、provider request、response parsing；
- run `20260803T183221Z` 的 Task Designer、iteration failure、retry history 和 provider evidence。

## 4. Metadata impact note

```text
Fact:
  没有新的权威事实。现有 code-generation task、target/write scope、operation kind、产品意图、
  生成约束和当前代码需要形成一个有选择证据的 model-facing 投影。

Authoritative producer:
  Task/ToolInputMetadata/CodeGenerationRequest/ProductIntentMetadata 继续分别拥有原事实；
  CodeGenerator adapter 只生产派生 ContextCandidate。

Consumers:
  CodeGenerator provider request、runtime diagnostics、离线 context-quality fixture。

Lifecycle:
  runtime-only derived request projection；selection 继续由现有 trajectory evidence 记录。

Control impact:
  budget、permission representation；不改变实际 permission owner 或 completion rule。

Existing contracts reviewed:
  Task、ToolInputMetadata、CodeGenerationRequest、ProductIntentMetadata、ContextCandidate、
  ContextAssemblyPolicy、ContextSelectionMetadata、ContextQualityExpectation、DurableArtifactReference。

Decision:
  reuse + derived view。新增 tool-local candidate adapter，不新增 MetadataKind、字段或 owned value。

Why no duplicate source of truth is created:
  candidates 保留 source identity，只投影现有 request/prompt-context 字段；不持久化第二份 task、代码、
  product intent 或权限状态。ContextSelectionMetadata 继续是唯一选择证据。

Serialization and migration:
  无 metadata shape 或 schema-version 变化。非 contextual code-generation 保留现有 message adapter；
  contextual request 改用 candidate adapter。

Tests:
  冻结失败 replay、required/optional 分类、source IDs、budget/quality、permission representation、
  legacy non-context path、provider request、syntax/write/validation 端到端。

Documentation updates:
  API.md、docs/metadata/CONTRACT_CATALOG.md、docs/testing/TEST_DESIGN_GUIDE.md、
  docs/task_trajectory/IMPLEMENTATION_LOG.md、本计划及实验结果。
```

结论：不需要新的 metadata。新增 contract 会把一次 Prompt 投影误建模为新的权威事实，并与现有 Task、
ProductIntent 和 ContextCandidate 重复。

## 5. Candidate 边界

Contextual code generation 使用以下候选：

| 内容 | retention | truncation | 说明 |
| --- | --- | --- | --- |
| 固定工具角色、操作规则、只返回代码 | required | forbidden | 完整保留 |
| task description、language、operation kind | required | forbidden | 当前工具任务 |
| target file、允许写范围、acceptance criteria | required | forbidden | 权限和完成意图的派生表示 |
| product intent、non-regression、disallowed substitutions、stack/UI 硬边界 | required | forbidden | 安全与产品边界 |
| 当前目标代码或生成新文件声明 | required | forbidden | file replacement 必须看到完整安全代码视图；放不下则 fail closed |
| max lines、allowed imports、forbidden operations、output format | required | forbidden | 生成约束 |
| 紧凑 validation errors/warnings | preferred | head | 与当前修改相关 |
| dependency strategy | preferred | head | 不重复完整 diagnosis |
| quality rubric / selected candidate / report summary | preferred | head | 逐项选择 |
| environment、diagnosis、product judgment、历史 project context | optional | head | 可省略或 artifact 化 |

existing-file replacement 不允许为了 fit 截断当前完整代码后仍标记 ready。大文件应 fail closed，或由上层明确
路由到 symbol/patch editor；本轮不以隐式截断制造不安全整文件覆盖。

## 6. 分阶段实施

### 阶段 2：冻结 replay 与失败测试

实施前测试：

- 用 `183221` 同源 state/report/task 重建 improvement code-generation request；
- legacy 单 message 稳定复现 budget failure；
- expected required facts 包含 task、target、operation、三条非回归约束、delivery surface、acceptance、代码；
- oversized optional diagnosis 不得挤掉 required；
- oversized required current code 必须 transport 前 fail closed；
- non-context code generation 保持现有行为。

退出闸门：测试先红，且能够区分“重复 optional 膨胀”和“required 代码确实放不下”。

### 阶段 3：最小 adapter 实现

新增 stateless tool-local candidate builder，`CodeGenerator` 对带 prompt context 的 request 使用
`build_context_candidate_request`；无 prompt context 的简单生成继续使用现有 message adapter。删除 contextual
路径中完整 JSON + 派生 guidance 的重复输出，不改变 tool input/output contract。

退出闸门：冻结输入 ready、required 全 kept、quality fixture 通过；权限、fallback、非 contextual 回归通过。

### 阶段 4：确定性反事实与真实 arm

先运行冻结离线 A/B，记录 original/final tokens、candidate decisions 和 hard gates。通过后只跑一个 fixed
decomposition optional arm；必须验证核心四任务、两个精确命令、测试文件 hash、改进文件写集合、改进后
pytest/compileall、completed improvements 和 optional terminal outcome。

若 provider 前仍失败，定位具体 required candidate；若 provider 后质量失败，保留为模型/任务质量问题，
不通过扩大预算或替代验证命令掩盖。

### 阶段 5：验收与文档

运行 Code/tests、实验测试、compileall、JSON 和 diff check；同步 API、catalog、测试指南、实施日志和实验报告。

## 7. 停止条件

- required 当前代码本身超预算：停止并建议 patch/symbol routing，不截断后整文件替换；
- 写目标超出 Task 声明、测试文件变化、精确验证缺失：立即拒绝；
- 相同上游 gate 连续失败两次：停止 provider 重试；
- 动态 completion budget、通用代码检索、全局关系层和新 scheduler 均不在本轮范围。

## 8. 实施结果

- Metadata 决策保持 `reuse + derived view`，没有新增 kind、字段或第二权威 owner。
- 冻结 run `20260803T183221Z` 重放：旧 contextual prompt 为 49,781 chars / 13,324 tokens，
  `budget_insufficient`；新候选投影为 8,430 chars / 2,076 tokens，`ready`，全部 required kept。
- 首轮候选实现仍会用未压缩 diagnosis 填满 3,968-token budget；质量门因此失败。改为字段级摘要后
  降至 2,076 tokens，保留 1,892-token 余量，证明不能把“刚好装得下”误判为高效。
- fixed-decomposition provider arm `20260803T200118Z` 的 improvement code generation 为
  1,942 assembled / 2,025 provider input / 342 provider output tokens，全部 required kept；仅修改
  `calculator.py`，测试文件 hash 不变，pytest 3 passed、compileall 和直接运行均通过，轨迹记录
  `completed_improvements=1`。
- 剩余限制：`iteration_task_design` 仍因 optional memory 填满 3,968 tokens；tool-event controller 仍有
  reasoning/output 膨胀；临时 `.venv` 不含 pytest，实验精确命令依赖 host interpreter。这些是独立后续项。
