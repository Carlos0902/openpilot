# mini-SWE-agent 主动迭代分阶段实验协议

## 1. 文档状态

- 状态：development planning
- 日期：2026-07-30
- 当前证据资格：`hypothesis_evidence_eligible=false`
- 当前生产授权：`production_execution_authorized=false`

本文定义如何验证 OpenPilot 主动迭代核心相对于普通
model-directed coding-agent loop 的价值。它不是确认实验的结果文档，也不授权把
E2/E3 接入生产 Controller。

正式确认实验必须在首次运行前另外冻结机器可读 protocol、任务 manifest、
runner/model/prompt/tool/evaluator fingerprint、预算和分析计划。development 任务和结果不得混入
确认集。

## 2. 当前事实与待验证主张

当前仓库已经完成 E2 主动测量、E3 动作/恢复/停止、typed evidence、receipt、真实轨迹
shadow 和 disposable-sandbox 的分层验证。现有 comparative sandbox V4 在两个已暴露任务上
`fixed_order`、`model_directed`、`active_iteration` 均为 `2/2`；active 使用更少的 provider
调用和 token，但该结果仅是 development 信号。

仍未得到支持的主张是：

> 在相同模型、任务、工具、初始信息、隐藏评测和总预算边界下，结构化主动测量与主动控制，
> 能让小模型比普通 model-directed loop 获得更好的任务成功—成本前沿。

“价值”按非补偿顺序定义：

1. hidden evaluator 验证的任务成功；
2. 不增加 false success、严重回归或边界违规；
3. 在前两项通过后比较 token、模型调用、工具调用和时间成本。

低成本不得补偿任务失败或严重安全失败。

## 3. 总体阶段

| 阶段 | 不变量 | 新增变量 | 允许形成的结论 |
|---|---|---|---|
| 0. 核心冻结 | 现有 E2/E3 语义 | mini-SWE 实验适配和最小运行时状态 | runner 可测试、可记账 |
| 1. 净增益 | mini 原生 trajectory/environment | ordinary 对 active | scoped 任务收益或效率收益 |
| 2. 机制消融 | 同一 mini harness 与内部轻量证据 | 分别消融 E2、E3 | 区分主动测量与主动控制 |
| 3. 迁移 | 冻结策略和字段 | 新模型、任务分布、harness | scoped 迁移价值 |
| 4. 正式集成 | 已验证主动核心 | OpenPilot 完整 trajectory/evidence/safety | 审计和生产候选价值 |

任一阶段未通过，不自动进入下一阶段。

## 4. 阶段 0：冻结最小主动迭代核心

### 4.1 运行边界

阶段 0 不修改 OpenPilot 生产 Controller。实现应位于独立实验边界，通过 mini-SWE-agent
公开接口或窄适配器接入。

mini-SWE-agent 原生 `messages` 是运行时事实输入，原生 trajectory 是实验原始记录。阶段 0
不把它转换成 OpenPilot `RunRecord/EventRecord`，也不生成正式 `EvidenceBundle`。

### 4.2 最小闭环

```text
读取 mini 原生 messages
  -> E2 选择 MEASURE 或确认现有信息足够
  -> 同一个 Bash environment 执行测量
  -> 更新轻量 ActiveState
  -> E3 选择 ACT / VERIFY / RECOVER / STOP / DELEGATE
  -> 结果追加回 mini 原生 messages
  -> 下一轮
```

### 4.3 最小 ActiveState

第一版只允许保存 E2 到 E3 必需的运行时认识：

- `condition_id`
- `status`: `met | unmet | unknown`
- `source_message_ids`
- `freshness`: `valid | invalidated | unknown`
- `controllability`: `controllable | uncontrollable | unknown`
- `last_action_effect`: `applied | no_effect | partial | unknown`

这些字段可以只在内存中存在。不得把 hidden evaluator、隐藏测试内容或事后评分写入
`ActiveState`。

### 4.4 决策集合

- `MEASURE`: 选择一项可执行测量；
- `ACT`: 执行局部修改；
- `VERIFY`: 验证动作效果或非回归；
- `RECOVER`: 对失败、partial effect 或回归采取恢复；
- `STOP`: 在证据闭合、预算耗尽、外部阻塞或不可恢复时停止；
- `DELEGATE`: 在结构化控制没有额外约束时交回普通模型步骤。

每次决策必须有稳定序号、原因和预算记账。第一版不引入专家 Router、长期记忆或生产权限。

### 4.5 阶段 0 验收

- deterministic fake model/environment 可完整走通 ordinary 与 active；
- active 决策只消费 task、mini messages、公开工具结果和预算；
- hidden evaluator 在提交前不可见；
- controller 调用、Prompt token、测量、验证和重试全部计费；
- budget exhausted、格式错误、工具失败和 STOP 均产生明确终态；
- `hypothesis_evidence_eligible` 固定为 false。

## 5. 阶段 1：mini-SWE 原生轨迹上的净增益

### 5.1 实验臂

#### `ordinary`

使用 stock mini-SWE-agent 控制流。模型自行选择 Bash/read/search/test/write/retry/finish。

#### `active_iteration`

使用相同 mini-SWE agent、模型和环境，额外启用阶段 0 冻结的 E2/E3 主动控制。

`fixed_order` 只允许作为 development 灵敏度检查，不是正式主比较。

### 5.2 预算比较

“A-budget”不是第三种 agent 架构。正式比较采用共同预算上限：

- 相同最大总 token；
- 相同最大 provider 调用；
- 相同最大工具调用；
- 相同最大迭代；
- 相同 wall-clock timeout；
- active controller 的全部开销计入 active 总额。

同时报告自然停止后的实际用量。若需要成功—成本曲线，应在后续冻结多个共同预算档，不得根据
已观察结果为某一臂单独调预算。

### 5.3 公平性

两臂必须共享：

- task text、附件和初始仓库快照；
- model/provider/endpoint/temperature/context limit/cache policy；
- 基础 system prompt 中的任务、工具和权限信息；
- Bash 工具能力、sandbox、网络边界和 timeout；
- public validation 和隔离 hidden evaluator；
- 失败、超时、格式错误和空响应政策；
- 独立 task-arm sandbox；
- 运行顺序随机化规则。

active 不得免费读取 ordinary 未请求的文件、测试或 support 信息。ordinary 必须拥有请求这些信息的
相同工具能力。controller 特有 Prompt/schema 属于 treatment，必须冻结并计费。

hidden evaluator 必须位于 agent 无法读取的文件系统或进程边界之外，不能仅以隐藏文件名放在同一个
Bash sandbox。evaluator 应接收最终 patch/submission，不接收实验臂名称；模型上下文也不应暴露
无必要的 arm 标签。

### 5.4 任务生命周期

1. `development`: 6--8 个已暴露任务，仅用于调通；
2. `exploratory`: 约 20 个新任务，用于估计失败模式、效应方向和确认实验规模；
3. `confirmation`: 若 exploratory 为正，再冻结 30--50 个全新任务。

confirmation 任务数量最终由 exploratory 的配对差异和成本方差决定，并在确认任务首次执行前冻结。
任务应从更大的 eligible pool 按固定随机种子抽取，覆盖定位、单文件、多文件接口、配置/CLI、
测试回归、测量后才能区分根因、错误停止风险等类型。

以下任务应在抽样前按统一规则排除：

- 需要工具网络或不可冻结外部服务；
- 安装或测试明显不稳定；
- 超出预注册资源边界；
- hidden evaluator 无法与 agent 工作区隔离。

不得按 ordinary/active 结果事后筛任务。

### 5.5 指标

主要指标：

- `verified_task_success`: hidden evaluator 通过。

安全门：

- `false_success`;
- hidden evaluator 泄漏；
- source repository mutation；
- sandbox cleanup failure；
- 未恢复严重回归；
- provider/trajectory 不完整。

成本指标：

- provider calls；
- input/output/total tokens；
- Bash/tool calls；
- wall time；
- 每个 verified success 的总成本。

机制诊断指标：

- 重复命令和重复测试；
- 无增益连续轮数；
- 首次有效诊断前成本；
- 修改后漏测；
- recovery 成功率；
- STOP 校准。

机制指标不能替代 verified success。

### 5.6 分析

- 以相同 `task_id + seed` 为配对单位；
- 二元成功使用 exact paired test，并报告改善/退步/tie；
- 成本报告配对差值、比率和 bootstrap 区间；
- 所有失败保留在分母；
- 先过成功和安全门，再比较成本；
- exploratory 与 confirmation 不合并成一个确认性样本；
- 不用单一加权总分掩盖成功、成本和安全之间的差异。

### 5.7 阶段 1 继续门

满足以下任一预注册路径：

1. **任务收益**：共同预算下 active 的 verified success 配对结果优于 ordinary，且安全门通过；
2. **效率收益**：active 的 verified success 达到预注册非劣门槛，安全门通过，且总成本有实质下降。

若 active 在成功、成本和安全上均无优势，停止扩展证据和生产集成，回到 E2 测量选择、E3
过度控制或停止策略诊断。

## 6. 阶段 2：E2/E3 机制消融

阶段 2 仍使用 mini-SWE-agent 原生 trajectory，不引入独立证据投影臂，也不以 OpenPilot 完整轨迹
替换它。E2 测量产生的轻量证据继续作为 treatment 内部状态传给 E3。

### 6.1 E2 消融

两组使用同一个冻结 E3，只改变测量策略：

| 组别 | 测量 | 控制 |
|---|---|---|
| E2-control | 固定或普通 model-directed 测量 | 同一个 E3 |
| E2-active | E2 主动选择下一项测量 | 同一个 E3 |

它回答：在控制策略和预算相同的条件下，E2 是否更会选择下一项测量。

### 6.2 E3 消融

两组接收同一份冻结的 diagnosis/evidence 输入，只改变行动策略：

| 组别 | 输入 | 行动、恢复与停止 |
|---|---|---|
| E3-control | 相同 evidence packet | ordinary model-directed |
| E3-active | 相同 evidence packet | E3 结构化控制 |

它回答：在证据相同的条件下，E3 是否更会行动、恢复和停止。

### 6.3 联合闭环

最后比较：

- ordinary mini；
- E2-only；
- E3-only；
- E2 + E3 full。

机制实验必须使用新的 development/exploratory 任务，不能在阶段 1 confirmation outcome 后通过反复
消融同一 holdout 形成新的确认性主张。若提升仅存在于 E2-only，应把主张收缩为主动测量价值；若仅
存在于 E3-only，应收缩为主动控制价值；只有 full 在新任务上保留净增益，才能支持联合闭环价值。

## 7. 阶段 3：迁移

冻结阶段 2 胜出的 policy、Prompt、字段和预算算法，不针对新模型逐项调参。

### 3A. 跨模型

- 至少两个不同小模型家族；
- 一个较强模型作为 ceiling reference；
- 相同任务分层和 evaluator；
- 报告每个模型的独立结果，不只报告合并平均。

### 3B. 跨任务分布

至少新增一个不同任务分布，例如测试补全、配置修复、小型功能实现或代码库理解后修改。

### 3C. 跨 harness

只替换 trajectory/environment adapter，主动策略本身保持不变。若每换一个 harness 都需要重写
E2/E3，不能主张模块已经具有迁移价值。

## 8. 阶段 4：OpenPilot 完整集成

通过前述门后，才依次接入：

- OpenPilot durable trajectory；
- `EvidenceBinding/EvidenceBundle`；
- `SignalObservation`；
- `ActionEffectReceipt`；
- freshness/invalidation 严格合同；
- 权限、风险、回滚、重放和生产 Controller。

顺序固定为：

```text
read-only shadow
  -> disposable sandbox
  -> 有限写入
  -> 故障注入与回滚
  -> 小流量生产候选
```

阶段 4 的问题是完整基础设施能否保留任务收益并提高审计与安全能力，不再用于首次证明主动迭代
是否有价值。

## 9. Artifact 与变更纪律

每次 development/confirmation 执行至少保存：

- protocol 和 task manifest 原文及 SHA-256；
- mini-SWE 版本、model/provider/endpoint、Prompt 和工具 schema fingerprint；
- 每个 task-arm 的原生 `.traj.json`；
- provider usage 和预算终态；
- 最终 patch/submission；
- public/hidden evaluator 结果；
- runner fingerprint；
- 无效、失败和被中断运行；
- 汇总结果及其输入 artifact hash。

首次 confirmation outcome 后不得修改同一 protocol。实现、Prompt、任务或 evaluator 变化必须新建版本，
旧 artifact 保留并标记资格，不得覆盖。

## 10. 当前实施顺序

1. 核对并固定 mini-SWE-agent 版本与公开扩展接口；
2. 先写阶段 0 protocol/状态/预算/隐藏评测隔离测试；
3. 实现 experiment-local ordinary/active 适配；
4. 使用 deterministic fake model 跑离线 conformance；
5. 使用 1--2 个已暴露任务做 provider development smoke；
6. 冻结 exploratory task acquisition 规则；
7. 在任何新任务 outcome 之前冻结机器可读 exploratory protocol。
