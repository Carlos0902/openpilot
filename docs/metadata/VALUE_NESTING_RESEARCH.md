# 当前值嵌套 Metadata 骨架梳理

> **状态：研究记录 / 非规范。** 本文记录当前值嵌套形态和未来可能出现的压力，不能作为
> 三层架构、图化或跨树引用改造的实施授权，也不是新增 metadata 的规范。项目级规则见
> `docs/metadata/DEVELOPMENT_CONVENTIONS.md`，全量目录见
> `docs/metadata/CONTRACT_CATALOG.md`。

## 1. 目的

本文只回答两个问题：

1. 当前项目里，基于**值嵌套**的 metadata 骨架实际上长什么样。
2. 这种树状组织方式，是否已经出现你担心的那种：
   - 跨域信息不好表达；
   - 为了聚合局部视图不断新增 metadata；
   - 相同叶子在多个分支里反复长出，导致结构臃肿。

本文基于当前代码直接整理，主要来源：

- `Code/src/metadata/agent_runtime.py`
- `Code/src/metadata/project.py`
- `Code/src/metadata/results.py`
- `Code/src/metadata/runtime.py`
- `Code/src/metadata/tooling.py`
- `Code/src/metadata/artifacts.py`

---

## 2. 先给结论

### 结论 1：当前项目并不是“纯平铺 metadata”

项目里已经有比较明显的**值嵌套骨架**，而且已经形成了几条主干：

- 运行时控制主干
- 项目诊断 / 项目状态主干
- 工具执行 / 结果封装主干
- 数据处理 / 产物主干

所以现在不是“有没有骨架”的问题，而是：

> 当前骨架主要还是**树状值组合**，不是图状关系表达。

### 结论 2：你的理解基本是对的

如果一直坚持值嵌套，那么一旦遇到：

- 跨域搜索；
- 多来源证据聚合；
- 一个 prompt 需要从很多分支各抽一点信息；
- 一个事实需要同时服务于 runtime / evidence / report / route；

通常会出现两种做法：

1. 把一些分散字段上提，做成更大的父 metadata；
2. 把这些分散字段重新聚成一个新的局部 metadata，再嵌回树里。

这样做的代价就是：

> 同一个语义叶子，可能以“值复制”的方式出现在多个节点下面。

而图结构可以把这些东西拆成：

- 节点：事实 / 证据 / 路径 / 文件 / 任务 / 路由判断
- 边：依赖、支持、引用、派生、选择、消费

于是共享关系不需要靠值复制表达。

### 结论 3：当前代码里已经有“重复语义”，但还没有严重到“树爆炸”

当前更明显的问题是：

> **语义分散、消费路径分散**，已经先于“结构爆炸”出现了。

也就是说，目前已经能看到重复叶子的苗头，但总体还没臃肿到不可维护。

---

## 3. 当前值嵌套骨架

下面不是按文件列，而是按“骨架主干”列。

### 3.1 运行时控制骨架

最核心的是：

```text
RuntimeStateMetadata
├── budget: RuntimeBudgetMetadata
├── path_intents: [PathIntentMetadata]
├── path_resolutions: [PathResolutionMetadata]
├── planned_edits: [EditPlanMetadata]
├── decision_history: [ToolDecisionMetadata]
├── candidate_files: dict[str, list[str]]
├── selected_files: dict[str, list[str]]
└── tool_history: list[dict]
```

对应的收束输出：

```text
RuntimeReportMetadata
├── path_resolutions: [PathResolutionMetadata]
├── planned_edits: [EditPlanMetadata]
├── tool_decisions: [ToolDecisionMetadata]
└── tool_history: list[dict]
```

这条主干的特点：

- 很适合表达**阶段推进**；
- 很适合表达**读 -> 判定 -> 改 -> 验证**；
- 很适合承载局部决策历史；
- 但它天然更像“单任务单线程树”，不擅长跨域共享事实。

---

### 3.2 项目状态 / 诊断骨架

项目状态主干：

```text
ProjectStateMetadata
├── dependencies: [ProjectDependencyMetadata]
├── dependency_strategy: DependencyStrategyMetadata | None
├── stack_preset: ProjectStackPresetMetadata | None
├── diagnostic_evidence: dict
├── runtime_evidence: [str]
└── test_evidence: [str]
```

项目诊断主干：

```text
ProjectDiagnosisMetadata
├── objective: ProjectObjectiveMetadata
├── success_metrics: [SuccessMetricMetadata]
├── dimension_assessments: [ProjectDimensionAssessmentMetadata]
├── improvement_candidates: [ImprovementCandidateMetadata]
├── selected_candidate: ImprovementCandidateMetadata | None
├── reference_insights: [ReferenceInsightMetadata]
├── dependencies: [ProjectDependencyMetadata]
├── dependency_strategy: DependencyStrategyMetadata | None
└── stack_preset: ProjectStackPresetMetadata | None
```

项目改进分析主干：

```text
ImprovementAnalysisMetadata
├── diagnosis: ProjectDiagnosisMetadata | None
├── improvement_candidates: [ImprovementCandidateMetadata]
└── selected_candidate: ImprovementCandidateMetadata | None
```

环境同步主干：

```text
EnvironmentSyncMetadata
├── dependencies: [ProjectDependencyMetadata]
├── dependency_strategy: DependencyStrategyMetadata | None
├── stack_preset: ProjectStackPresetMetadata | None
├── git_repository: GitRepositoryMetadata | None
└── git_snapshot: GitSnapshotMetadata | None
```

这条主干说明：

- 项目域已经不是单点 metadata，而是开始形成**值聚合树**；
- `dependencies / strategy / stack_preset` 已经在多个父节点中重复出现；
- 这正是树结构开始出现“共享语义无法共享身份”的地方。

---

### 3.3 文件 / 路径 / 解析骨架

```text
TaskFileResolutionMetadata
├── related_files: [RelatedProjectFileMetadata]
└── primary_file: RelatedProjectFileMetadata | None

FileContentIndexMetadata
└── sections: [FileContentSectionMetadata]

RuntimeStateMetadata
├── path_intents: [PathIntentMetadata]
└── path_resolutions: [PathResolutionMetadata]
```

这块目前仍然比较“局部树化”：

- 每个上层节点只包自己关心的局部结果；
- 还没有形成统一的“文件节点 / 路径节点 / 证据节点”共享图。

---

### 3.4 工具执行 / 事件 / 结果骨架

结果封装主干：

```text
ToolResultMetadata
├── result: MetadataBase | None
└── failure: FailureMetadata | None

TaskResultMetadata
├── result: MetadataBase | ToolResultMetadata | None
└── failure: FailureMetadata | None
```

工具执行主干：

```text
ToolExecutionEnvelopeMetadata
├── input_metadata: ToolInputMetadata
├── output_metadata: ToolResultMetadata | None
├── failure: FailureMetadata | None
├── tool_context: ToolContextMetadata | None
└── tool_events: [ToolEventMetadata]
```

事件循环主干：

```text
ToolLoopMetadata
├── events: [ToolEventMetadata]
├── tool_invocations: [ToolCallMetadata]
├── recoverable_errors: [ToolErrorMetadata]
├── tool_contexts: [ToolContextMetadata]
├── final_output: ToolResultMetadata | None
└── final_error: FailureMetadata | None
```

单事件主干：

```text
ToolEventMetadata
├── input_metadata: ToolInputMetadata | None
├── output_metadata: ToolResultMetadata | None
├── tool_context: ToolContextMetadata | None
├── tool_call: ToolCallMetadata | None
├── tool_error: ToolErrorMetadata | None
└── failure: FailureMetadata | None
```

这部分的重复很多，但大多是**协议封装重复**，不是业务树爆炸。

---

### 3.5 数据 / 产物骨架

```text
CollectedDataMetadata
├── artifact: MetadataBase
└── tool_result: ToolResultMetadata | None

ProcessedDataMetadata
├── input_artifacts: [MetadataBase]
└── summarizer_result: ToolResultMetadata | None

BugFixResultMetadata
├── attempts: [BugFixAttemptMetadata]
└── final_command_result: CommandArtifactMetadata | None
```

这部分已经体现出一个特点：

> 上层 metadata 会把“下游产物”整个包进来。

这对审计和回放很好，但对“横向抽片段给 prompt”不够自然。

---

## 4. 为什么说它本质上还是树，而不是图

虽然项目里已经有 `TaskGraphNodeMetadata` / `TaskGraphEdgeMetadata`，但它们目前更像：

- 某个任务分解场景下的业务对象；
- 不是整个 metadata 系统的底层组织方式。

当前主流 metadata 关系仍然是：

> 父节点直接持有子节点的值。

这意味着：

1. 子节点没有稳定全局身份；
2. 一个事实如果被多个上层需要，通常会被复制；
3. 跨域关系只能靠：
   - 重复字段；
   - dict/attributes；
   - 或者再造一个新的聚合 metadata。

所以现在的系统更像：

> “很多局部树”，而不是“一个统一图”。

---

## 5. 你的理解是否成立：值嵌套为什么会逼出新的聚合 metadata

你的理解是成立的。

当一个 prompt、路由器、验证器、审计器要消费的信息来自：

- runtime state 的一部分；
- project diagnosis 的一部分；
- file resolution 的一部分；
- tool loop 的一部分；
- evidence layer 的一部分；

树结构下常见做法就是：

### 做法 A：上提父节点

把分散字段尽量上提到一个更大的 metadata。

问题：

- 父节点会越来越胖；
- 很多消费者其实只需要其中 10%；
- prompt 拼接更容易变成“大包里挑字段”。

### 做法 B：新造局部聚合 metadata

例如为了一个特殊 prompt / route / review 视图，再做一个新的聚合对象。

问题：

- 同一事实会在多个分支重复出现；
- 需要额外维护一致性；
- 语义上会出现“这些字段明明是同一件事，但在不同树枝下各有一份”。

这正是你说的：

> 在整本书不同节点上，长出很多相同叶子。

---

## 6. 当前项目里，重复叶子有没有已经出现

### 6.1 已经出现了“字段语义重复”

直接从当前 metadata 定义统计，重复较多的字段包括：

| 字段 | 出现次数 |
|---|---:|
| `project_path` | 13 |
| `confidence` | 12 |
| `evidence` | 11 |
| `reason` | 11 |
| `tool_name` | 11 |
| `goal` | 8 |
| `warnings` | 8 |
| `task_id` | 8 |
| `step_id` | 8 |
| `dependencies` | 5 |
| `stack_preset` | 4 |

这里最值得注意的是：

- `project_path`
- `goal`
- `evidence`
- `dependencies`
- `stack_preset`

这些都不是纯协议噪音，而是**高价值业务语义**。

这说明当前已经存在：

> 同一类事实，在多个 metadata 契约里各自携带一份。

---

### 6.2 已经出现了“部分共享叶子被多个父节点重复持有”

当前多父持有较明显的嵌套叶子有：

| 叶子 metadata | 被几个父位置持有 |
|---|---:|
| `FailureMetadata` | 8 |
| `ToolResultMetadata` | 8 |
| `ToolInputMetadata` | 5 |
| `ToolContextMetadata` | 5 |
| `ImprovementCandidateMetadata` | 4 |
| `ProjectDependencyMetadata` | 2 |
| `DependencyStrategyMetadata` | 2 |
| `ProjectStackPresetMetadata` | 2 |
| `EditPlanMetadata` | 2 |
| `PathResolutionMetadata` | 2 |
| `ToolDecisionMetadata` | 2 |

但这里要分两类看。

#### 第一类：合理重复

例如：

- `FailureMetadata`
- `ToolResultMetadata`
- `ToolInputMetadata`
- `ToolContextMetadata`

这些本质上是**执行协议封装件**，在不同 envelope 里重复出现是合理的。

#### 第二类：需要警惕的业务重复

例如：

- `ImprovementCandidateMetadata`
- `ProjectDependencyMetadata`
- `DependencyStrategyMetadata`
- `ProjectStackPresetMetadata`

这些已经不是单纯的协议包装，而是**业务含义对象**。

它们在多个父节点中重复出现，说明项目域已经开始碰到：

> 一个业务事实要同时服务多个上层视图，但还没有统一身份机制。

---

### 6.3 结论：已经有苗头，但还没到“严重臃肿”

当前更准确的判断是：

> **已经出现“语义分散 + 局部重复”，但还没有出现“全局树爆炸”。**

原因：

1. 大部分嵌套关系仍然比较浅；
2. 真正多父复用的业务叶子还不算太多；
3. 目前很多复杂关系还被 `dict`、`attributes`、`evidence`、`summary` 这些松散容器吸收掉了。

所以现在的状态不是“已经晚了”，而是：

> 现在正好处在一个适合观察、归纳、再决定是否图化的阶段。

---

## 7. 和 prompt 拼接的关系

这点很关键。

### 7.1 当前项目还没有证明“值嵌套会让 prompt 变慢”

目前代码库里没有专门的 prompt 组装性能基准测试。

所以严格地说，现有仓库**不能直接证明**：

- 值嵌套一定慢；
- 或者值嵌套一定不慢。

### 7.2 但当前项目已经在主动避免“把整棵树直接塞给 prompt”

已有测试说明，工具规划 prompt 已经在做**局部投影**，而不是直接把 metadata 全量倾倒进去。

相关测试文件：

- `/Users/abab/Documents/openpilot/Code/tests/test_execution_tool_planning_executor.py`

其中有测试明确约束：

- 新 prompt 长度不超过旧版全量工具 prompt 的 55%
- prompt 中不出现 `Available Tools`
- prompt 中不出现 `Input metadata:`

这说明当前 prompt 侧已经在走一个正确方向：

> prompt 实际上消费的应该是“局部视图”，不是原始 metadata 树本身。

所以短期内，即使底层还是值嵌套，prompt 也不一定会立刻臃肿；
真正会先变复杂的，通常是**视图组装逻辑**。

---

## 8. 当前骨架的阶段性判断

如果只讨论“现在要不要立刻把 metadata 全面重构成图”，我的判断是：

### 8.1 现在还不适合直接全图化

因为当前问题更突出的是：

- 语义分散；
- 字段重复；
- 消费路径不统一；
- prompt / route / guard / evidence 都在各取各的局部事实。

如果直接全图化，改动面会很大，而且未必立刻收益最大。

### 8.2 当前更适合先做这一步

先承认底层还是值嵌套骨架，同时明确：

1. 哪些 metadata 是控制主干；
2. 哪些 metadata 是结果封装；
3. 哪些字段是“稳定共享语义”；
4. prompt / route / guard 到底应该消费哪一层投影。

也就是先把：

> **树上的稳定骨架 + 关键共享语义**

梳理清楚。

### 8.3 什么时候值得引入图

当下面这些情况越来越频繁时，就说明图化开始有价值：

1. 一个事实需要被 3 个以上域重复携带；
2. 一个 prompt 需要跨 4 个以上 metadata 分支抽片段；
3. 证据、路径、任务、文件之间需要稳定可追溯关系；
4. “哪个结论由哪些证据支持”开始成为高频问题；
5. 为了局部视图不断新增聚合 metadata。

---

## 9. 一句话总结

当前项目已经有了可工作的**值嵌套 metadata 骨架**，但它本质上仍然是“多棵局部树”。

你担心的问题是成立的：

> 当跨域、多来源、复用关系继续增强时，树会逼着系统通过“字段上提”或“新聚合 metadata”来表达共享关系，进而出现相同叶子在多处长出的情况。

不过就当前代码来看：

> 这种问题已经有苗头，但还没有严重到结构爆炸；更早暴露出来的是“语义分散、消费路径分散”。

因此，现阶段最合理的判断不是“立刻全图化”，而是：

1. 先把值嵌套骨架明确下来；
2. 先把共享语义收敛清楚；
3. 再挑最痛的跨域场景，局部引入图关系或引用关系。
