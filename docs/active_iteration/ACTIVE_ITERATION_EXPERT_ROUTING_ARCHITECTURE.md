# OpenPilot 主动迭代、主动评测与专家路由架构

> 状态：架构方向与分阶段研究计划。核心主动迭代闭环已在 RS-V5 的 provider-backed
> disposable-sandbox 范围内完成验证；本文中的专家路由、跨模型迁移和生产接管假说仍未验证。
>
> 本文整理当前讨论形成的共识。它不替代 `THOUGHT_ARCHITECTURE.md` 和
> `Thought.md`，而是把其中的主动迭代、虚拟专家与 BTX 类比进一步收敛为可实施、
> 可对照、可证伪的路线。
>
> 日期式实施记录和实验结果不再保存在本文；当前结果见
> `ACTIVE_ITERATION_EXPERIMENT_REVIEW.md`，完整历史见
> `ACTIVE_ITERATION_EXPERIMENT_LOG.md` 和
> `../task_trajectory/IMPLEMENTATION_LOG.md`。

## 1. 核心判断

OpenPilot 的目标不是用一个更强模型充当“大脑”、让较弱模型充当“手”。目标是构造一个
**模型能力同质、模型尺度无关、可以迁移的主动迭代系统**：

- 第一组实验可以全部使用同一 8B 模型；
- 后续可以把整组模型统一替换为 32B、70B 或其他同等级模型；
- 系统收益应来自诊断、控制、专家特化和证据闭环，而不是暗中引入更强 Judge；
- 如果架构只在“强模型指挥弱模型”时有效，就没有验证本项目的核心设想。

项目希望验证的是：能力相近的模型经过精心设计的多轮主动迭代，能否以更高的任务成功率、
更低的严重失败率和更好的可恢复性，逼近更强的单次执行能力；并且这套增益能否跨模型尺度保留。

这只是研究目标，不是当前已经成立的结论。

## 2. 主动迭代不等于重复执行

普通 Agent 循环通常是：

```text
任务 -> 分解 -> 逐项执行 -> 通用验证 -> 继续或结束
```

OpenPilot 所说的主动迭代应当是：

```text
目标与当前状态
      ↓
主动诊断：长处、短板、未知、冲突、风险
      ↓
控制决策：继续测量 / 局部行动 / 恢复 / 换方向 / 停止
      ↓
专家路由：选择当前最合适的虚拟或真实专家
      ↓
局部执行 + 运行时验证
      ↓
更新证据、能力画像、残余问题和净增益判断
      └───────────────────────────────↺
```

“主动”的关键不在于轮数更多，而在于每一轮都基于新证据重新决定下一单位预算该花在哪里。
没有诊断和控制的多轮循环，只是更昂贵的重复尝试。

## 3. 三层评测体系

### 3.1 Runtime Verification：运行时验证

运行时验证是执行链路的守门层，回答：

- 这个动作是否符合权限和工具契约？
- 修改是否通过确定性测试、状态检查和非回归验证？
- 是否出现越权、副作用、幂等性或安全问题？
- 当前结果是否满足继续执行的最低条件？

它通常紧贴动作发生，很多 Agent 都有类似 verifier。OpenPilot 不需要把“有 verifier”本身
当作创新点；需要做的是让验证结果成为后续诊断与控制的可靠证据。

### 3.2 Iteration-time Active Diagnostic Evaluation：迭代时主动诊断评测

这是主动迭代的主要驱动力。它不像静态 grader 一次跑完固定清单，而像诊断过程：先做低成本、
高确定性的检查，再根据当前证据选择下一项最有价值的测量。

它需要同时保留：

- **长处**：已经稳定成立、后续不得破坏的能力与结果；
- **短板**：已有充分证据确认的问题；
- **未知**：缺少证据，不能默认为通过或失败；
- **冲突**：不同来源的证据互相矛盾；
- **风险**：一旦判断错误，后果大小和可恢复程度；
- **建议方向**：下一项检查或下一项局部改进候选，而不是直接替 Controller 做决定。

主动评测像“大脑”这个比喻只对了一部分：它负责认识现状、暴露盲区并提出方向，但不应同时垄断
行动权、路由权和最终停止权。更准确地说，它是系统的**诊断与测量中枢**。

### 3.3 Formal Independent Evaluation：正式独立评测

正式评测位于被测 Agent 系统之外，模型在运行时看不到隐藏任务、隐藏 oracle 和最终评价细则。
它回答的是整个系统的能力：

- 完成率和可靠性是否真的提高？
- 增益是否只是来自更多 token、更多时间或更多工具调用？
- 是否减少严重失败，同时保留已有长处？
- 主动路由是否优于固定流程和自由循环？
- 架构增益能否迁移到不同模型尺度？

正式评测不能被运行时自评替代。运行时评测帮助系统工作，正式评测判断系统是否真的更会工作。

## 4. 主动评测与主动迭代的职责边界

| 组件 | 核心问题 | 不应拥有的权力 |
|---|---|---|
| Active Diagnostic Evaluator | 现在知道什么、不知道什么，下一项测量是什么？ | 不直接决定所有行动，不修改正式外部评测规则 |
| Controller | 下一步测量、行动、恢复、转向还是停止？ | 不伪造验证证据，不兼任所有专家 |
| Router | 哪个专家最适合当前局部问题？ | 不自行改写顶层目标 |
| Expert / Actor | 如何完成当前受限局部任务？ | 不拥有独立长期目标和无限权限 |
| Verifier | 执行是否正确、安全且无回归？ | 不用单一主观意见覆盖确定性失败 |
| Trajectory / Runtime State | 实际发生了什么，依据是什么？ | 不把推断伪装成事实 |
| Independent Evaluator | 整个系统能力是否提高？ | 不向运行中 Agent 泄露隐藏答案 |

主动评测与主动迭代紧密相连，但不等价：前者形成可行动的认识，后者还必须完成控制、路由、
执行、验证、状态更新和停止。

## 5. 避免“同一模型总赞同自己”

同一底座模型可以扮演不同角色，但角色分离本身不能保证独立性。第一版应使用以下约束降低
自我确认偏差：

1. 先保存 Actor 的动作、产物和理由，再启动诊断；诊断不能改写原始轨迹。
2. Evaluator 接收面向证据的上下文，不默认继承 Actor 的完整自我辩护。
3. 确定性测试、环境状态和权限违规优先于模型意见。
4. 诊断输出必须引用证据，并允许 `unknown`、`disputed` 和 `inconclusive`。
5. 可对同一证据采用顺序扰动、Prompt 扰动或独立采样，检测判断稳定性。
6. 正式评测使用隐藏 oracle，且不由被测系统写入或覆盖。

这里追求的不是假装同一模型变成了完全独立的人，而是用信息隔离、权限边界、确定性证据和
可追溯记录，构造可审计的功能分工。

## 6. 虚拟专家：系统级 MoE / BTX 类比

### 6.1 类比成立的部分

同一底座模型可以通过以下因素形成虚拟特化。**这七项是每个专家 Spec 的强制填写项，不是
举例**：未填写的维度视为与基线专家相同，必须显式写成"同基线"，不允许留空。

| # | 特化维度 | 强制填写内容 | 留空的后果 |
|---|---|---|---|
| 1 | Role Prompt 与任务边界 | 该专家被允许处理的问题类别，以及明确排除的类别 | 边界不可审计，Router 命中无法归因 |
| 2 | 可见上下文与证据视图 | 上下文投影的确定性定义（见 §16.5） | 投影不可复现，专家间差异无法归因 |
| 3 | 工具与权限 | 允许调用的工具集、目标作用域、参数约束、网络边界与权限档 | 越权无法判定 |
| 4 | 记忆范围 | 可读写的持久记忆范围 | 长期记忆污染不可追溯 |
| 5 | 输出契约 | 结构化输出 schema 与必填字段 | 判定层无法读取（违反 §16.1） |
| 6 | token / 时间 / 重试预算 | 三者的上限 | 成本归因失效（违反 §10 第四步） |
| 7 | 解码与输入扰动策略 | 采样参数与扰动方式 | 稳定性检测（§5 第 5 条）无基准 |

这张表与 §17 的字段登记表是两张不同的表：前者登记**专家**，后者登记**被测字段**。

Router 根据当前任务残余和能力画像选择专家；后续某个虚拟专家可以被 LoRA、Adapter 或不同但
能力同等级的模型替换。因此必须坚持：

```text
Expert Role != Model Identity
```

### 6.2 类比的边界

BTX（Branch-Train-MiX）是在模型权重层从共同 seed 分支训练专家，将专家 FFN 组成 MoE，
再训练 token-level router。OpenPilot 当前设想是在系统运行时进行上下文、工具、权限和任务级
路由，不是直接复刻 BTX，也不能借用 BTX 的实验结论证明本架构有效。

BTX 提供的主要启发是：专家可以增量进入网络，路由器可以作为组合能力的关键学习对象；
OpenPilot 是否也能做到“增加专家时主要更新 Router，而不重构主链路”，必须到相应阶段再验证。

## 7. 专家是持久的还是临时的

采用三层生命周期：

### 持久身份

`Expert Spec / Profile` 持久保存：

- 专家角色、版本和适用条件；
- 工具权限、上下文策略和输出契约；
- 当前模型或 Adapter 绑定；
- 历史评测、路由命中和失败统计；
- 已知能力边界及退役条件。

### 临时执行

每次任务创建临时 `Expert Instance`，只持有：

- 当前局部目标和证据切片；
- 本轮预算与授权；
- scratch context；
- 本轮工具调用和局部产物。

实例完成、失败或超时后结束，不保留独立长期目标。

### 选择性学习

不是所有执行痕迹都进入长期专家经验。只有通过验证、来源清晰、适用边界明确的经验，才可以
进入持久记忆、提示模板或后续训练数据。

一句话概括：**持久身份，临时执行，选择性学习。**

## 8. 这不是厚重的多 Agent 社会

系统保持以下单一权威边界：

- 一个顶层目标；
- 一份权威 `RuntimeState`；
- 一个版本化 Expert Registry；
- 一个负责全局推进和停止的 Controller；
- 按需创建、受限运行、完成即释放的专家实例。

专家不各自维护长期自治目标，也不形成互相不可见的独立状态机。这仍然可以作为一个通用 Agent
系统对外工作；“单 Agent / 多 Agent”更像部署和观察粒度，不应反过来支配内部架构。

## 9. 主动诊断如何选择下一项检查

医学检查的启发不是”多做检查”，而是根据已有证据选择能最大程度改变决策的下一项检查。

**意图陈述（非规范）。** 下面这个乘式只用来说明想要什么方向：决策影响大、关键度高、当前
不确定、证据互相矛盾的测量优先，而测量成本抬高则压低优先级。

```text
MeasurementPriority(m)
  = ExpectedDecisionImpact
    × Criticality
    × CurrentUncertainty
    × EvidenceDisagreement
    / ExpectedMeasurementCost
```

它不是本文的规范内容，任何实现都不应当把它当作待拟合的目标函数。理由有三条：五个因子
里至少三个（决策影响、不确定性、证据分歧）只能由模型自评产生，而自评读数不允许进入判定
层（§16.1）；乘式对任一因子的零值过度敏感，一个估计失误即可让关键检查排到末位；连续
打分掩盖了非补偿性——高风险未知不该被”成本很高”这一项抵掉。

**规范内容是下面的分档规则。** 实现必须按这六条排序，不得用连续打分替代：

1. 先执行便宜、确定性的契约与状态检查；
2. 若硬失败已足以决定停止，不调用昂贵 Judge；
3. 若关键目标仍未知，优先补覆盖它的环境或集成证据；
4. 若证据冲突，优先选择能区分竞争解释的检查；
5. 只有剩余问题主要是开放语义质量时，才使用模型 Judge 或人工复核；
6. 低风险未知可以在预算边界内保留未知，高风险未知不能猜测通过。

主动诊断不仅寻找短板，也要建立长处画像。否则系统可能在修复一个问题时破坏已经稳定的能力。

## 10. 净增益如何判断

净增益不能简化成一个加权总分，否则严重安全回归可能被功能得分抵消。采用非补偿式判断：

### 第一步：硬门槛

任何不可接受的权限、安全、数据完整性或明确契约违规，都使本轮不能判定为正增益。

### 第二步：长处保留

检查基线中已成立的关键能力是否仍然成立。修复局部短板但破坏更重要长处，属于负增益。

### 第三步：目标能力与认识增益

- **能力增益**：目标结果、可靠性、恢复性或关键维度获得了有证据支持的改善；
- **认识增益**：尚未改善结果，但显著缩小关键不确定性、排除错误方向或定位真实失败原因。

认识增益可以支持继续迭代，但不能在正式能力评测中冒充任务成功。

### 第四步：成本与因果归因

记录 token、时延、工具调用、失败动作和人工介入。比较同预算对照，确认改善来自主动诊断或路由，
而不只是花费了更多资源。尽量使用单变量、消融或配对任务确定哪项机制产生了变化。

本轮输出采用离散结论：

| 结论 | 条件 |
|---|---|
| `positive` | 通过硬门槛，保留关键长处，目标能力有可信改善，成本可接受 |
| `neutral` | 没有实质改善，也没有重要回归；新增证据不足以改变决策 |
| `negative` | 出现硬失败、关键长处回归，或成本显著增加但能力下降 |
| `inconclusive` | 证据不足、相互冲突，或无法把变化归因到本轮行动 |

`positive` 不是由执行模型自行宣布，而应由结构化前后证据和验证规则导出；不确定时必须保留
`inconclusive`。

## 11. 优先复用现有契约

第一版不先创建大型“主动评测”对象。应先验证现有契约是否足够表达闭环：

| 需要表达的内容 | 优先复用 |
|---|---|
| 顶层目标与成功条件 | `ProjectObjectiveMetadata`, `SuccessMetricMetadata` |
| 长处、短板、未知和维度判断 | `ProjectDimensionAssessmentMetadata`, `ProjectDiagnosisMetadata` |
| 改进方向与局部候选 | `ImprovementCandidateMetadata`, `ImprovementAnalysisMetadata` |
| 验证问题和下一项检查 | `ValidationIssueMetadata`, `VerificationPlanMetadata` |
| 当前控制状态与待决问题 | `RuntimeStateMetadata`, `DecisionNeedMetadata` |
| 单轮结果 | `IterationResult` |
| 事实、证据、父子关系和信用分配 | task trajectory records |

只有当某个信息满足以下条件时才新增或扩展 metadata：

1. 已经有两个或以上模块需要稳定交换它；
2. 现有字段无法在不歪曲语义的情况下表达；
3. 字段的生产者、消费者、生命周期和兼容策略已经明确；
4. 有序列化、边界条件和失败路径测试；
5. API 与相关文档同步更新。

是否需要独立的 Expert Profile、Diagnostic Snapshot 或 Routing Decision 契约，应在相应阶段做
现有契约盘点后决定，而不是由本文预先宣布。

## 12. 分阶段实现与假说验证

核心原则是：**假说只有在其依赖机制实现、可观测、可重复，并且存在有效对照之后，才具备验证
条件。完成更早阶段不能提前验证更晚阶段的假说。**

### Phase 0：设计映射与基线

实现内容：

- 将本文概念映射到现有状态机、metadata、trajectory 和 verifier；
- 定义任务集、隐藏 oracle、预算口径和失败分类；
- 建立三组基线：单轮、普通自由循环、当前 OpenPilot；
- 固定模型、Prompt、工具、环境和评测版本。

阶段出口：基线可重复运行，成本和轨迹可采集，契约缺口有源码证据。

此阶段**不能验证任何核心架构增益假说**，只能确认设计能否映射到当前项目并形成有效实验基线。

### Phase 1：稳定能力画像与主动诊断基础

实现内容：

- 从运行证据形成长处、短板、未知、冲突和风险画像；
- 支持诊断重放、证据引用和版本追踪；
- 建立确定性证据优先与 `unknown/disputed` 保留规则。

阶段出口：相同冻结轨迹的诊断结果达到预定重放一致性；关键结论具备证据覆盖；未知不会被静默
转成通过。

此阶段只能验证“诊断结果是否稳定、可追溯、覆盖关键证据”。它**不能**验证虚拟专家增益，也
不能验证主动路由优于固定流程。

### Phase 2：同模型虚拟专家与固定 Router

实现内容：

- 定义少量、边界清晰的虚拟专家；
- 使用同一底座模型，通过上下文、工具、权限和输出契约进行特化；
- 先使用固定或规则 Router，记录选择理由；
- 保持总预算和基线可比。

阶段出口：专家实例可隔离运行，路由和结果可回放，专家边界有失败测试。

此阶段之后才可以验证 **H1：同模型虚拟特化是否优于无角色的自由 Agent**。

### Phase 3：诊断—控制—行动—停止闭环

实现内容：

- Controller 根据主动诊断选择继续测量、行动、恢复、换方向或停止；
- 每轮执行前后进行非补偿式净增益判断；
- 支持预算停止、无增益停止、严重回归停止和不确定性升级；
- 做固定工作流、自由循环和主动闭环的消融对照。

阶段出口：决策有因果证据链；停止条件可重复；循环不会因自评赞同而无限继续。

此阶段之后才可以验证 **H2：主动诊断驱动的控制与路由是否优于固定工作流和普通自由循环**。

### Phase 4：Router shadow 学习与专家生命周期

实现内容：

- 建立 Expert Registry、版本、统计和退役规则；
- 新 Router 先以 shadow 模式给出建议，不直接控制生产动作；
- 收集路由选择、反事实候选、结果与成本；
- 验证新增专家时主状态机和执行链路是否保持稳定。

阶段出口：shadow 建议可离线评估；专家增删不会破坏既有契约；Router 更新具备回滚路径。

此阶段之后才可以验证 **H3：能否增量增加专家并主要更新 Router，而不重构主执行链路**。

### Phase 5：真实专家替换与跨尺度迁移

实现内容：

- 将部分虚拟专家替换为 Adapter、LoRA 或不同但能力同等级的模型；
- 在 8B、32B、70B 等可用尺度上重复同预算实验；
- 分析绝对能力、相对增益、成本曲线和失败类型是否迁移。

阶段出口：每个尺度具有相同任务分层、对照、预算口径和独立评测；结果具有置信区间和失败分析。

此阶段之后才可以验证 **H4：架构增益是否跨模型尺度保持为正，以及虚拟专家能否被真实专家渐进
替换**。

## 13. 实验协议重新设计

此前的 A/B/C/D 四模式矩阵是 Phase 0 harness 的可比性脚手架，不再视为已经冻结的正式实验设计。
它证明 manifest、预算、轨迹、隐藏评测和成对结果可以闭环，但不能反过来规定研究问题必须采用
哪四组。正式实验允许推翻并重做；旧 harness 可复用的只是执行与证据基础设施。

每个实验必须按以下顺序预注册，不能先跑结果再挑对照：

1. **研究问题与假说。** 一次实验只回答一个主要问题，例如虚拟特化增益、主动控制增益或跨尺度
   迁移；H1-H4 不塞进同一个总实验。
2. **处理变量与机制边界。** 明确唯一主要变量，以及 Prompt、上下文投影、工具权限、预算和重试中
   哪些属于该处理。不能把多个机制一起打开后把收益归给 Router。
3. **主要 estimand。** 预先指定要估计的是同预算成功率差、严重失败率差、成本达到某成功率的差，
   还是配对任务上的恢复率差；每个实验只设一个主要 estimand。
4. **对照组。** 根据研究问题选择最小充分对照，不强制沿用 A/B/C/D。对照必须除处理变量外尽量
   相同，并说明无法保持相同的部分。
5. **预算政策。** token、墙钟时延、provider 成本、工具调用和人工介入分别冻结；指定主预算轴、
   截尾规则、超预算 run 的分母归属和不可观测成本的 fail-closed 处理。
6. **任务与随机化。** 冻结任务分层、隐藏 oracle、seed、重复次数、配对方式、排除规则和 benchmark
   污染检查。不同模型尺度共享任务定义，但允许分别报告不可执行任务，不静默删除。
7. **分析计划。** 预先写明置信区间、最小有意义效应、多重比较处理、缺失/失败 run 处理和停止采样
   条件。`pass^k` 必须写明 k、独立运行单位和估计方法，不能只写符号。
8. **机制证据。** 正向结果还必须检查中介量，例如诊断是否改变了测量选择、Router 是否命中预定
   适用域、停止规则是否减少误停止；否则只能声称系统组合有效，不能声称具体机制有效。

第一批正式实验建议按因果问题拆开：

| 实验 | 主要变量 | 最小对照 | 主要结果 | 不允许声称 |
|---|---|---|---|---|
| E1 虚拟特化 | 同模型下的专家 Spec | 无角色但预算、工具相同 | 配对任务成功率差 | 主动控制有效 |
| E2 主动测量 | 测量选择策略 | 固定测量清单 | 误停止率或同预算成功率差 | 专家路由有效 |
| E3 主动控制 | Controller 的行动/停止策略 | 使用同诊断读数的固定控制策略 | 严重失败率或恢复率差 | 跨尺度迁移 |
| E4 Router | 专家选择策略 | 固定或 oracle-free 随机路由 | 同预算成功率差与路由 regret | 新增专家可无成本扩展 |
| E5 跨尺度 | 冻结机制在不同尺度的相对效应 | 各尺度自己的同模型基线 | 尺度分层效应与交互项 | 绝对能力相同 |

探索实验可以调整任务、阈值和对照，但必须标为 exploratory，不能与 confirmatory 结果混报。正式
协议冻结后，任何修改都生成新版本并说明原因，不覆盖原协议。

当前 E2 同域 pilot 的预注册协议位于
`experiments/active_measurement/E2_PILOT_PROTOCOL_V1.md`。其五个候选来自历史 trajectory，但旧
轨迹只作 provenance；在独立 annotator 冻结新 snapshot、字段真值和完整测量 outcome matrix 之前，
候选不得执行，也不得产生 E2 假说证据。候选 manifest 记录协议原始字节的 SHA-256；协议内容变化
必须生成并显式绑定新 hash，不能只沿用原有 protocol id/version；loader 需要显式协议路径并在加载时
校验原始字节，协议缺失、symlink 或 hash 漂移均 fail-closed。

V1 在任何 policy outcome 生成前经两路独立标注审查否决：它没有冻结字段命题语义、C1-C8 的
coverage/前置条件、C5-C8 所需的 case 输入、冲突规则和合法省略机会，直接补 outcome 会形成事后设计。
V1 文件和 manifest 保留为审计记录，不执行。替代设计位于
`experiments/active_measurement/E2_PILOT_PROTOCOL_V2_DRAFT.md`；它仍是 draft，在机器可读协议、checker、
独立标注 suite 与 policy/runner fingerprint 冻结前同样不得执行或产生假说证据。

上述 V2 feasibility slice 现已完成：机器协议、五个自包含内容寻址 fixture、确定性 C1-C8 checker、
suite/case/policy/runner/result fingerprint 和 gate-first comparison 均可重放。冻结结果为 fixed `62`、
active `53` 逻辑成本，两组均 `5/5` exact diagnosis、`0` critical error，cost delta `-9`。该结果只支持
synthetic same-domain 机制可行性，不支持真实任务平均效应或通用 E2 假说；因此允许进入 E2+E3
closed-loop pilot 的工程阶段，但 isolated E3 结论仍需独立对照实验。

E2+E3 joint pilot V1 因把 `synthesis_ready` 等派生字段写成 action 的直接 effects 而在正式落盘前否决；
V1 草案与机器协议保留为审计记录，不解释其试运行数字。V2 将 action effects 收窄为原子字段，每次
action 后由六个前置字段重算 `synthesis_ready`，并把发生变化的派生字段纳入 invalidation 与
post-action validation。冻结的五案例回放中，control 与 treatment 均恢复 `5/5`、严重失败 `0`、
fresh success `5/5`；总逻辑成本分别为 `82` 与 `68`，gate-first delta `-14`。摘要位于
`experiments/active_control/e2_e3_joint_v2/result_summary.json`。

该结果只支持 `synthetic_same_domain_joint_feasibility`：它验证 E2 诊断读数可以驱动离线 E3 控制并保持
恢复、安全与新鲜度门禁，同时联合处理减少了该 fixture 集的逻辑成本。由于 treatment 同时改变测量与
控制策略，差异不能单独归因于 E3，且 `hypothesis_evidence_eligible=false`。下一阶段只开放固定相同
diagnosis trace、仅改变 action policy 的 isolated E3 ablation；生产 Controller 仍未接线。

isolated E3 V1 已在相同五案例上完成 post-joint exploratory ablation。每个案例只运行一次
`unknown-first-v2` diagnosis，并以 fingerprint 固定后同时交给 fixed-action 与 residual-controller；
两臂测量成本均为 `53`，恢复、安全与 freshness 均相同，动作成本分别为 `20` 与 `15`，因此隔离出的
action-policy 成本差为 `-5`。这说明该 synthetic fixture 上 residual controller 的联合优势中有 `5`
个逻辑成本单位来自动作选择，但由于案例和 policy 行为已在 joint pilot 中揭示，该分析不是独立确认性
E3 证据，仍固定 `hypothesis_evidence_eligible=false`。下一步若要提升证据等级，必须先冻结未揭示的
holdout 任务/案例，再运行同一 matched-diagnosis protocol。

作为进入真实 holdout 前的控制器边界检查，E3 actionable-residual lattice V1 在 outcome 生成前冻结了七个
可动作原子字段的全部 `2^7=128` 个二值组合、九字段 severity、`synthesis_ready` 派生规则和三动作预算。
首次运行得到 fixed/residual recovery `92/95`，但 severe failure 为 `14/26`；因此 recovery gate 通过、
safety gate 失败，原始逻辑成本 `1079/971` 不具备可比较资格，pilot claim 为 false。

失败集中在预算受限且同时包含关系、证据/只读和 deliverable 残差的状态。V1 residual policy 只计算 action
直接覆盖字段：A2 直接修复 major relationship，却还会通过 dependency closure 恢复 critical
`synthesis_ready`；当前排序看不到这个间接收益，可能把预算给更便宜的 A5，最终留下 critical 派生残差。
因此不能把五案例的 E3 `-5` 外推为控制策略普遍安全。下一轮必须把 closure-aware projected residual
写成新 policy 版本，并把本 lattice 只作为开发回归；确认性验证仍需新的未揭示 holdout。

closure-aware residual controller V2 已完成同一 revealed lattice 上的开发回归。它对每个候选 action 先投影
原子 effect，再重算所有 derived field，以投影后实际消除残差的最高 severity、消除数量、cost、action ID
排序。结果 fixed/closure recovery 为 `92/95`，severe failure 为 `14/12`，V1 的 14 个 residual-only
severe 全部修复，且没有 closure-only severe；recovery、safety、freshness 三个开发 gate 均通过。

原始动作成本为 `1079/1029`，但两臂 recovery 不相等，所以 cost gate 仍禁止生成 delta。由于 V2 明确由
V1 failure 设计并重放已揭示 lattice，这只能证明修复满足开发目标，不能证明未见分布上的 E3 效果。
closure-aware V2 仍未接入生产 Controller；下一步必须先冻结新的 holdout diagnosis 集，再比较 fixed 与
V2，且 holdout outcome 揭示前不得继续调整 ranking。

首次未揭示 transition holdout 在 V2 冻结后增加了新的执行维度：128 个 diagnosis 状态分别交叉
`none/A1/A2/A3/A4/A5` 单动作 validated no-op，共 768 case。no-op 消耗动作成本与三步预算、动作标记
used、完成 post-action validation，但所有原子与派生字段保持不变。预注册聚合结果 fixed/V2 recovery
`343/352`、severe `320/314`、fresh success `343/352`，所以 recovery/safety/freshness aggregate gate
通过；recovery 不等，原始成本 `6578/6202` 仍不可比较。

该 holdout 同时暴露 gate 缺口：`holdout-mask-044-fault-A2` 与 `holdout-mask-108-fault-A2` 是两个
V2-only severe regression。聚合 severe 总数下降会掩盖这种 case-level critical 回归。不能事后改写 V1
gate 或声称原协议失败，但也不能据此接生产。下一协议必须增加 zero treatment-only severe gate；这两个
case 只能作为开发回归，新的确认集还需引入未揭示的 fault timing/partial-effect 维度。

fault-aware minimax controller V3 已将上述两个 case 固定为失败证据，并在执行前加入
`zero_treatment_only_severe_cases` 门禁。控制器不读取真实 fault target；它维护与已验证观测一致的故障
假设集合。动作成功会排除“该动作 no-op”，动作经验证无变化则耗尽本轮唯一故障预算。每一步在剩余三步
预算内递归投影成功/no-op 分支，依次最小化最坏 critical 残差数、severity 加权残差负担和总残差数；这些
安全量打平后，先沿用 fixed-action 的安全优先级，再比较无故障分支的剩余成本和 action ID。

在已经揭示的 768 case 上，fixed/V3 recovery 为 `343/349`，severe failure 为 `320/314`，fresh success
为 `343/349`；两个 A2 no-op 回归都改走 `A1,A3,A4`，V3-only severe case 为 `0`，新增 case-level gate
通过。原始动作成本 `6578/6462` 因 recovery 不等仍禁止比较。该结果的作用域严格固定为
`post_holdout_development_replay_on_revealed_transition_faults`：它证明已知门禁缺口已被开发修复，不是第二次
使用同一 holdout 获得确认。V3 不接入生产 Controller；下一步必须在 V3 policy fingerprint 冻结后创建
新的 fault-timing 或 partial-effect holdout。

V3 完全冻结后，partial-effect holdout V1 在任何 outcome 生成前绑定 V3 protocol、development result 和
policy fingerprint，并引入未用于 V3 开发的新故障维度。128 个 diagnosis 状态分别交叉 `none` 与五种
A1/A2 单字段 omission，共 768 case。目标 action 除被省略字段外的原子 effect 正常生效，随后重算
derived fields；动作仍消耗 cost/预算、标记 used 并完成 post-action validation。该设计专门检查“验证看到
部分变化，控制器可能误认为完整成功”的边界。

预注册结果 fixed/V3 recovery 为 `417/425`，severe failure 为 `254/246`，fresh success 为 `417/425`；
V3-only severe case 为 `0`，recovery、aggregate safety、case-level safety、freshness 门均通过。六个 fault
切片内 V3 都没有新增 severe。原始动作成本 `6578/6520` 因 recovery 不等仍不可比较。

这次结果可确认 V3 在冻结的 `synthetic_partial_action_effect_holdout` 上保持非劣恢复与安全，但不能外推到
真实 executor、transient retry、多故障或 fault timing。生产 Controller 仍不接线；进一步 promotion 至少
需要新的时序/重试维度，以及在真实执行 receipt 上验证 partial success 的检测与重规划语义。

E2 此后完成了两次失败驱动迭代和一次新 suite 确认。unknown-first V2 在 30-case nuisance holdout 中因
critical error `28 -> 31` 被否决；failure-exposure V3 修复该 revealed set 后，又在独立 timing holdout
因 exact diagnosis `8 -> 5` 被否决。guarded V4 只固定第一个 applicable measurement，之后沿用 V3，
在两组 revealed data 上达到 fixed baseline safety。随后冻结的三个全新 base case × 九 nuisance 的 27-case
确认集得到 fixed/V4 correct `23/24`、critical `3/3`、false success `0/0`、fresh correct `14/16`，全部
主门通过；质量不等，成本 `326/302` 不可比较。完整逐 case execution 已落盘。该结果允许 V4 进入最后的
联合 synthetic holdout，但仍不授权生产 measurement routing。

E3 positional timing/retry V1 的 896-case 窄结果为 fixed/V3 recovery `291/299`、severe `474/462`，
zero treatment-only severe 通过；它共享 fault position/kind，而非相同 action target，retry 由 policy
选择。由于没有 missing receipt/delayed validation 和逐 attempt artifact，V1 不算完整退出门。后续 V2
冻结 56-case receipt/validation holdout 与 typed 284-entry ledger；结果 recovery `29/38`、severe
`19/11`、false success `0/0`，但 evidence-blocked `5/8` 使预注册 freshness gate 失败。该失败证明系统
没有把缺证据的物理恢复误报为成功，也说明原 reconciliation 尚未闭环。

新建的 receipt reconciliation V4 保持 action policy、effects 和 cost 不变，只允许同幂等键 retry 的成功
receipt + 完整字段验证或一次 terminal validation checkpoint 闭合缺口。在 revealed V2 集上 fresh success
恢复为 `29/38`。首次 56-case confirmation 虽报告 fixed/V3 recovery `49/49`、severe `7/7`、fresh
success `49/49`，但 semantic audit 发现 runner 绕过共享 V4 adapter、从 variant label 生成验证、允许两个
checkpoint，并且两个 receipt variants 未形成不同机制。该 trial 已作废，E3 单项 synthetic gate 仍未
关闭；必须先以共享 adapter、外生 observation schedule、最多一次 checkpoint 和 typed provenance
validator 完成 V2 confirmation，才能启动最终 E2×E3 joint nuisance holdout。

该修复已以 receipt reconciliation V5 完成：development 与 confirmation 共用
`reconcile-case-evidence-v5`，只消费 typed attempts 和最多一个外生 observation，对 stale、
partial、missing 及跨 case/arm provenance fail closed。已揭示 E3-R2 开发回放上的
retry/terminal reconciliation 为 `24/7`；随后冻结的 V2 confirmation 在八个新 mask、
四个 positive 和三个 negative variant 上得到 fixed/V3 recovery `33/33`、severe
`18/18`、positive fresh `22/22`，48 个负向 case-arm probe 全部 fail closed，cost
`584 -> 564`。这关闭了 E3 synthetic component prerequisite，但仍是
`hypothesis_evidence_eligible=false`，不是生产 Controller 或真实 executor 证据。

joint synthetic-exit V1 因绑定无效 E3-C4 而在执行前 fail closed，从未产生 48-case
联合结果。其 measurement-only diagnostic 只暴露了 V4 长路径在
`attempt-5-unavailable` 下 fresh correct `11 -> 10`，因此只能作 E2 开发输入。E2
balanced-prefix V5 冻结 fixed-v2 applicable order 的前三次尝试，再切换到 V3 suffix；
它在 nuisance、timing、旧 confirmation 和 invalid joint diagnostic 四个 revealed set 上全部过门，
其中 diagnostic fresh `11 -> 12`。这同样只是 post-failure development，不是独立 E2 结论。

joint V2 的前两个 revision 在 outcome 采信前被 exposure audit 否决：一个 action terminal
位置不可达，另一个 suite 在三次 measurement 内结束，使 attempt-5/6 nuisance 零曝光。
最终 revision 绑定三组 long-horizon base，并把 E2 accuracy/critical/freshness 与每臂 nuisance
exposure 加入非补偿门。60 case/arm 得到 diagnosis/recovery/freshness 全等、severe/false success
为零；measurement/retry/terminal/stale exposure 为 `45/20/12/12`，全部主门通过。
240 attempts、48 observations、64 reconciliations 和 120 EvidenceBundle 已持久化并完成逐层
fresh replay。该结果只开放 read-only real-task shadow；production Controller、真实写操作、
命令、网络和 sandbox execution 仍未获授权。

## 14. 当前非目标

- 不引入“强模型大脑 + 弱模型执行器”作为核心依赖；
- 不把增加 Agent 数量视为自动增加能力；
- 不在没有真实消费者前发明完整的新 metadata 层；
- 不让模型 Judge 覆盖确定性失败或安全门槛；
- 不用单一总分掩盖长处回归、风险和未知；
- 不在 Phase 0/1 的文档或 fixture 结果上宣称主动迭代已经优于基线；
- 不把系统级专家路由直接表述为已经复现 BTX/MoE。

## 15. 仍需通过实施回答的问题

1. 第一批虚拟专家应按任务阶段、失败类型还是工具能力划分？
2. 当前 metadata 在表达 `unknown/disputed`、前后能力画像和路由因果时是否真的存在缺口？
3. 哪些运行时验证结果可以直接进入诊断，哪些需要校准或隔离？
4. 同一底座模型的上下文隔离能在多大程度上降低自我确认偏差？
5. 认识增益如何影响继续迭代，但又不污染正式任务成功指标？
6. Router 的 shadow 反馈如何避免使用事后不可获得的信息？
7. 在不同模型尺度下，最优专家粒度和迭代预算是否会变化？

这些问题不是设计阶段可以凭概念回答的。它们应成为各阶段的观测项、失败记录和后续决策依据。


## 16. 信号层约束

> 状态：设计约束，非实施状态。本节内容由
> `ACTIVE_ITERATION_SIGNAL_DECISION_LOG.md` 的 D11-D19 及后续补记导入，那份文档明确不授权任何实现
> 改动，本节同样不授权。本节没有验证证据行，因为它没有对应的已跑测试。
>
> 本节只收敛"信号怎么算"的约束。它不改动 §3.2 对主动诊断评测的定义——那一节的开放问题
> 只能由 §13 的对照实验关闭，讨论关不掉。

### 22.1 双层结构：判定层与建议层

判定层只读规定性字段的登记定义、确定性观察和由二者导出的离散状态。它不读任何分数、置信度
或模型自评。建议层可以读全部信息，但它的输出不绑定任何决策。

这条约束的来源是 §3.3 和 H4：主动迭代的收益必须能跨模型尺度迁移，因此判断责任必须落在
harness 一侧，由确定性代码计算。模型自评只能待在建议层。

`score: float = 0.5` 和 `confidence: float = 0.5` 这类默认值是被禁止的：它让"没人填"和
"评估为一半"在数值上无法区分。缺测必须表达为 `None`，并计入 `unknown`。

### 22.2 验证深度标注（D11）

字段登记时必须分别声明检查能否证明满足、能否证明不满足，以及两种结论各自要求的最低验证
深度。低于对应深度的检查不能产出该方向的结论，只能产出 `unknown`。只有登记为具有单侧
可靠性的浅检查，才允许在达不到满足深度时确定地产出 `unmet`。

这条约束堵的是同一个字段被浅检查"顺手判过"：一个需要集成证据的字段，不允许被一次静态
检查标成满足。证明能力偏序同时充当 §16.7 的测量升级来源；测量侧共用这一份偏序，但它不承担
干预策略排序。

### 22.3 正交状态与 anti-windup（D12）

规定性字段不是 `unmet / unknown / uncontrollable` 单一三态。它由两个正交维度组成：

```text
satisfaction    = met | unmet | unknown
controllability = controllable | uncontrollable | unknown
```

残差三元组是从这两个维度派生的分类，不是字段状态枚举：`unmet` 指已知不满足且可控，
`unknown` 包含满足性缺测或阻止行动决策的可控性缺测，`uncontrollable` 指已知不满足且有
确定性证据或人工判断证明当前授权主体不可控制。`met` 不进入残差，但仍保留为长处与非回归
基线。非法组合必须 fail-closed；尤其不能把未测量直接登记为不可控，也不能用不可控覆盖满足性事实。

**从 `unmet` 转 `uncontrollable` 必须有确定性证据或人的判断，绝不允许因为"试了很多次没成"
自动转。** 这是本节最重要的一句。自动转换等于给系统一个把未解决问题重命名为不可控问题的
出口，`unmet == 0` 的停止判据会随之失效。

修复尝试次数只作为记录量与升级触发条件（§16.7），不作为状态转换条件。已冻结字段的解冻
用事件驱动，不用时间驱动：等到相关证据发生变化才解冻，而不是等若干轮之后自动解冻。

### 22.4 分类残差与停止判据

残差是三元组 `(unmet, unknown, uncontrollable)`，每一维按 severity 分档
`{critical, major, minor}`。仲裁是字典序的、非补偿的：`minor` 的大量满足不能抵掉一个
`critical` 的未满足。这与 §10 的四步非补偿判定是同一原则。

循环处置与任务结果必须分开。Controller 的处置至少区分：

```text
continue | stop_success | stop_budget | stop_regression | handoff_required | blocked_external
```

任务结果至少区分 `success / partial / failed / inconclusive`。`uncontrollable` 可以允许
`blocked_external` 或交还给人，但绝不自动推出 `success`。只剩 minor 项时是否允许预算停止
由任务协议决定；允许停止也只能得到协议规定的结果，不能与零残差成功混同。

零残差稳定性使用连续 k 个**独立有效验证点**，不使用没有新证据的空转轮次。一个验证点必须绑定
新的测量 attempt 或新的环境/项目版本，并且其依赖自上次验证后未发生未结算失效。独立性的最小
定义是不同 attempt ID；若两个 attempt 复用同一 provider/cache 输出或同一未变化证据，只能计一个。
具体 k 仍是待标定量。

### 22.5 上下文投影（D13）

上下文投影必须有确定性定义：给定同一轮次记录，投影结果可复现。投影覆盖率作为观测项记录。
字段登记必须同时声明 `dependency_refs`、`invalidation_triggers` 和 `freshness_scope`。
相关 mutation 发生后，旧的 `met`/`unmet` 观察立即失去当前判定资格，直到新观察覆盖该
mutation；历史证据仍保留，不能被覆盖或改写。现有 `ToolInputMetadata.freshness` 是自由文本
提示，不承担这一判定语义。

投影同时配一份**探索配额**：一部分测量不受当前投影支配，用于发现投影之外的问题。

这是一条贯穿本节的设计原则的第二个实例：**任何从自身读数估计出来的收紧动作，都必须留一条
不受该估计支配的通道。** 三个实例分别是标定窗口、本节的探索配额、以及 §16.3 的事件驱动
解冻。

### 22.6 失败模式只承认确定性来源（D14）

失败模式的取值只能来自确定性来源：测试框架的错误类型、进程退出码、契约违反类别、超时。
模型对失败原因的叙述属于建议层，不进入失败模式字段。

理由与 §14 的非目标一致：模型 Judge 不得覆盖确定性失败。一个"自信的错值"比 `None` 更坏，
因为 `None` 会计入 `unknown` 并逼出一次测量，而错值会直接关掉测量需求。

### 22.7 测量升级与干预策略分离（D14 / D17）

同一失败模式在同一字段上重复 N 次后，测量升级到能提供更强证明能力的一档。测量深度描述
“能证明什么”，干预策略描述“怎样改变项目”；二者不是同一条阶梯。更深的测试可以提高结论
强度，但不会自动成为新的修复方法。

**计数键是 `(字段 id, 失败模式, 手段档位)`，升级时按档位归零（D18）。** 不带档位会让 N
永不复位，越深的档位分到的尝试次数越少，与"更深意味着更多开销与更高置信度"正好相反。归零
不会让旧档位复活：耗尽的档位由本节的偏序永久标记，归零只发生在新档位的计数器上。

计数单位是 §16.10 记录里的一条采样事件，即"一次完整的测量尝试产出了一个确定性失败模式"，
**不是底层调用次数**——一次尝试内部的重试受环境影响，计进 N 会让升级时机随网络和框架波动。
N 的数值仍是待标定量 C6。

测量升级的偏序来源是 §16.2 的证明能力与验证深度。它不要求所有检查形成全序；两个检查证明
不同性质时可以不可比。干预侧单独记录
`(字段 id, 失败模式, strategy_id, strategy_version)`，耗尽的策略不得在依赖和失败模式未变化时
被 Router 再选。策略切换不能伪装成验证深度升级。

**最低验证深度是结论下界，不是手段上界。** 可比的更强测量耗尽或干预策略耗尽时，终态是交还
给人或按预算/外部阻塞处置；`unmet → uncontrollable` 依然禁止（§16.3）。

### 22.8 改动面预算（D15）

改动面的主度量是本轮**发生翻转变化**的规定性字段数（口径见 §16.9），首现变化不计入预算。

单文件数不行：一次重命名能扫过几十个文件而语义改动为零。依赖闭包太贵，且需要静态分析。
主度量与延迟事件数都能从 §16.10 的记录里算出，不需要新数据结构。

**已知盲区与其指示器。** 若改了代码而没有字段覆盖它，改动面读数为零。因此必须配一个盲区
指示器，用 diff 规模作代理量。

本轮允许的改动面是当前最大未结算延迟的单调不增函数。执行点在 Controller 的预算里，不在
信号层——信号层只提供读数，不做收紧动作，因此 §16.1 的双层结构没有被违反。

### 22.9 记录层与派生层分离、变化分型（D16）

记录层如实记录每一次采样事件，包括两次采样取值相同的情况。"变化"不是记录层的概念，而是
记录之上的派生函数。

变化分两型：

- **首现变化**：`None` → 有值。这是信号出现，不是抖动。
- **翻转变化**：有值 → 另一个不同的值。这是抖动的唯一来源。
- **置信度变化**：`True@Level1` → `True@Level2`。如实记录，但不计为变化。

两型变化都进入眼图。§16.8 的改动面预算与抖动判据只看翻转，否则第一轮的改动面读数结构性
地必然最大——第一轮所有字段都是 `None` → 有值。

**变化判定函数第一版（D19）：先剥掉 §16.2 的验证深度标注，再按严格相等比较取值。**
`None` → 有值判为首现，有值 → 不同值判为翻转，第一版不做容差、不做归一化、不做排序。

第一版可以取最朴素的形式，是因为它不写入任何东西：判定每次都从 §16.10 的记录重算，换函数
的代价只是重算。**因此本节"记录层不做任何判断"必须严格守住**——一旦判定结果回写进记录层，
换函数就会让历史眼图读数失效，那不是重构而是数据作废。

唯一预留的接口是**取值的相等性判断按字段类型可替换**。会先出问题的地方一定在浮点字段的
相等、集合类字段的元素顺序、结构化取值的字段序上；现在没有真实字段可判断哪种处理是对的，
所以只把相等性做成可替换的一小块，其余写死。

本条目前只是定义，不是已验证结论。关闭它还需要一次真实轨迹上"首现/翻转分开"与"不分开"的
眼图曲线对照，这一件还不存在。

### 22.10 追加式提取记录（D10）

唯一事实来源复用现有 task trajectory 与 evidence binding，不再建立平行证据库。信号采样写成
trajectory event；较大原始结果继续写 artifact；进入判定层的观察必须通过 `EvidenceBundle`
绑定到同一 `run_id` 下的 event/artifact 内容与 SHA-256。严格连续 `sequence` 提供顺序，
bundle 提供不可变输入回放。

四元组 `(轮次, 事件序号, 字段 id, 取值)` 只作为派生视图，不是足够的持久化合同。信号观察
事件至少还要绑定 `field_schema_version`、`extractor_id/version`、`attempt_id`、
验证深度、证明方向、`evidence_refs` 和字段登记表 fingerprint。项目/环境版本和 mutation 边界
由所引用轨迹事件给出，不在信号层重复保存。残差、眼图和状态变化率全部从这些已绑定观察派生，
不各自维护状态。

命名上避开 `snapshot`——这个词在本仓库已被 git safety commit 占用。

### 22.11 眼图与残差不融合

眼图是跨轮的 O(n×k) 信号，对每个规定性字段保留最近 k 个取值。它刻意不与残差融合：

```text
残差决定继续 / 停止，眼图决定继续的方式
```

融合成单一总分会同时破坏 §14 的非目标（不用单一总分掩盖长处回归、风险和未知）和 §16.4 的
非补偿仲裁。

### 22.12 事件触发采样的别名风险

采样是 mutation boundary 触发的，不是定周期的。凡是可能影响字段 `dependency_refs` 的复合
动作，必须在可观察的提交边界后提取；不能先依赖提取结果判断动作是否改变了字段。这仍带来一个
必须记录的风险：动作内部若发生多次中间翻转而边界不可观察，眼图可能出现**假张开**——曲线
看上去收敛，实际是欠采样造成的别名。

因此眼图读数必须与该字段本轮的采样事件数一同呈现，单看眼图不构成收敛证据。

### 22.13 新增规定性字段的可观测性检验（D9）

新增一个规定性字段时，必须说明它把哪两个**此前无法区分**的状态分开了。说不出这两个状态的
字段不予登记。

这条检验的填写结果落在 §17 字段登记表的对应列里。

### 22.14 Router 回放信封与执行期授权

路由决定要达到可回放，不能只保存选中的 role 和 `rule_version`。每条决定还必须绑定：

- Expert Registry fingerprint 和选中 role Spec 的 fingerprint；
- Model Binding fingerprint，不能在重放时查询“当前绑定”代替历史绑定；
- 路由输入、上下文投影和候选集合的 fingerprint；
- 每个候选的命中证据或拒绝原因，以及这些证据的稳定 trajectory 引用；
- Router 版本、模式、预算和决定发生时的授权快照。

`shadow` 决定只能形成建议和反事实记录，不能创建可执行授权。active 决定创建实例时，将
工具名、目标路径/资源、参数范围、网络域、权限档、调用/成本上限冻结为不可变
`authorization_snapshot`。ToolEventLoop 在每次调用前根据这个快照 fail-closed 校验；Router
阶段的 allowlist 检查不能替代执行期检查。历史决定缺少任一关键 fingerprint 或授权快照时可以用于
观察性分析，但不能重新执行，也不能作为正式 route-outcome 证据。

## 17. 字段登记表（强制）

> 状态：设计约束，非实施状态。本节同样不授权实现改动。

§16 的多条约束最终都落在同一张表上。这张表是整套体系的重心：**其他章节引用这张表，不各自
重述字段属性**。一个规定性字段没有完整登记全部适用列，就不允许进入判定层。

### 23.1 登记列定义

| 列 | 来源 | 填写内容 | 缺失时的后果 |
|---|---|---|---|
| 规定性 / 描述性 | D1 | 该字段是否参与判定 | 判定层读到描述性字段，违反 §16.1 |
| severity 档 | D2 / D3 | `critical` / `major` / `minor` | §16.4 的字典序仲裁无法排序 |
| 可观测性检验说明 | D9 | 它分开了哪两个此前无法区分的状态 | 字段可能只是重复既有信息 |
| 最低验证深度 | D11 | 阶梯上的一档 | 浅检查可以把字段判成满足（§16.2） |
| 证据来源 | D13 | 确定性来源的具体标识 | 取值可能来自模型自评 |
| 升级空间说明 | D17 / D24 | 可达的更强测量后继，包括不可比分支 | §16.7 的测量升级无处可去 |
| 证明能力 | D24 | 能否证明 met/unmet 及各自最低深度 | 浅检查的单侧可靠性无法判定 |
| 依赖引用 | D22 | 能影响该字段的项目/环境对象 | 无法传播证据失效 |
| 失效触发器 | D22 | 哪类 mutation 使旧观察失效 | 陈旧结论可能参与停止 |
| 新鲜度作用域 | D22 | 观察覆盖的项目/环境版本边界 | 无法判断验证点是否仍有效 |
| 提取器身份 | D23 | extractor id/version 与字段 schema version | 历史读数无法按原语义回放 |

### 23.2 填写规则

**最低验证深度是相应证明方向的结论下界。** 满足与不满足可以有不同下界；检查只能产生登记为
可证明的结论。该列应填写真正必要的最低档，而不是“最稳妥的一档”。

“升级空间说明”必须从当前已登记的证明能力偏序派生；检查不可比时分别列出可达后继，不能强行
压成一个阶梯高度。它与证明能力不一致时记为登记错误并 fail-closed。

severity 档一旦登记，本轮内不得因为"这次不重要"而临时下调。需要下调时走登记变更，留记录。

### 23.3 与专家 Spec 表的关系

§6.1 的七项特化维度表登记**专家**，本节登记**被测字段**。两张表不共用行，也不互相
替代。一个专家可以看不到某个字段，但字段的 severity 不因专家而变。

## 18. 待标定量

> 状态：占位符清单。本文中的任何数值若没有标注来源实验，一律视为占位符。

以下量只能由实验给出数值，设计阶段不填：

| 编号 | 待标定量 | 先决条件 |
|---|---|---|
| C1 | 抖动判据的分位数阈值 | 取样点定在残差走平轮次，属晚期轮次，不受 §16.9 的变化分型影响 |
| C2 | 停止判据的连续有效验证点数 k | 需要 §13 中专门针对误停止/回弹的预注册实验 |
| C3 | 眼图窗口长度 | 与 C2 同批标定 |
| C4 | 眼图闭合的度量维度 | 连维度都未定；可选变化字段数、集合 Jaccard 距离、单字段翻转次数 |
| C5 | 改动面—延迟函数的形式 | 依赖记录层／派生层分离，该先决条件已由 §16.9 满足 |
| C6 | 升级触发的重复次数 N | 需分别按 measurement method 与 intervention strategy 标定 |

数值补记时必须连同实验标识一起写入，不允许只写数值。
