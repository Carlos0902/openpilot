# OpenPilot 主动迭代与模型自主 Agent 的比较边界

## 1. 文档作用

本文回答的不是“OpenPilot 做过哪些实验”，而是三个更具体的问题：

1. 已有实验分别解决了哪一个可信度或机制问题；
2. 这些实验允许 OpenPilot 与主流模型自主 Agent 比较到什么程度；
3. 还缺少什么实验，才能判断 OpenPilot 的结构化主动迭代是否真的优于模型自行决策。

本文应当保留，因为仓库里的其他文档不能替代这个作用：

- [ACTIVE_ITERATION_EXPERIMENT_REVIEW.md](../ACTIVE_ITERATION_EXPERIMENT_REVIEW.md)
  是完整实验事实和证据等级的总评审；
- [ACTIVE_ITERATION_EXPERIMENT_LOG.md](../ACTIVE_ITERATION_EXPERIMENT_LOG.md)
  是追加式审计日志；
- 本文负责把内部证据映射到“固定流程、模型自主循环、OpenPilot 主动迭代”三种决策系统，
  并指出尚未完成的直接比较。

本文不是产品排名，也不是新的实验结果。任何数字和有效性判断仍以 `experiments/` 下的冻结 artifact
和实验总评审为准。

## 2. 当前最准确的结论

当前证据已经证明：

> 在冻结的 provider/model、八个 runtime-contract 任务、四个任务 strata 和 disposable sandbox
> 边界内，失败后向同一个模型提供新鲜 E2 测量，再执行 E3 修复，可以把独立评估成功率从
> fixed control 的 `2/8` 提高到 `8/8`；六个任务发生配对改善，没有配对退步或虚假成功。

这个结果有明确价值，但它只回答：

> **失败后的新鲜测量能否对受限修复产生增量效果？**

它没有回答：

> **OpenPilot 主动迭代是否优于一个拥有相同工具、预算和反馈能力、并由模型自行选择下一步的
> model-directed agent loop？**

因此，RS-V5 既不能被贬低成“没有意义”，也不能被扩大成“OpenPilot 已经优于主流 Agent”。
它是主动测量到修复收益的受控机制证据，是后续直接比较实验的前置依据。

## 3. 为什么不能只看 RS-V5 的总分

RS-V5 的 `fixed` 和 `active` 并不是两个能力完全相同、只改变决策者的通用 Agent：

- 两臂最多只有两次 provider 调用；
- `active` 第二次尝试会收到当前修改内容、公开测试、support contract 和第一次验证输出；
- `fixed` 不接收这组新测量，并在重试提示中继续看到原始内容；
- 模型不能像常见 coding agent 那样自行选择 read/search/test/write/finish 工具序列。

这正是 RS-V5 能隔离“新测量是否有用”的原因，也是它不能代表主流自主 Agent 基线的原因。

换言之，RS-V5 的 treatment 同固定 control 之间存在有意设计的信息差。该信息差适合验证 E2→E3
机制，却不足以证明结构化 Controller 比模型自主工具循环更强。

## 4. 已有实验各自起什么作用

以下实验不是重复堆叠。它们分别排除不同的替代解释。

### 4.1 E0 和基础合同：保证结果不是不可复核的演示

| 实验组 | 作用 | 没有它时可能出现的错误解释 |
|---|---|---|
| E0-H1 | 验证 trajectory 和 EvidenceBundle 对跨 run 引用、漂移、篡改和过期证据 fail closed | 一次终端输出被误当成可回放证据 |
| Phase 0 fixture harness | 冻结任务、run、预算和 fixture 的 typed boundary | 不同实验臂实际拿到不同或畸形输入 |
| Shadow conformance | 验证内存结论与持久化 artifact 一致 | 只在运行时出现的数字无法离线重算 |
| Observed receipt provenance | 把 observation 绑定到 attempt 和 effect | 模型或 runner 可以声称一个未发生的效果 |
| Manifest/cache provenance | 绑定模型、Prompt、工具、evaluator 和 cache 来源 | 缓存输出、协议漂移或模型变化被混入同一实验 |

这些实验不证明主动迭代更有效；它们证明后续“有效”结论具备可信的测量装置。

### 4.2 E2：证明主动选择测量本身有价值，并找出其失效条件

| 实验组 | 作用 |
|---|---|
| E2-V1 | 在执行前否决定义不完整的协议，证明协议完整性是实验前置条件 |
| E2-V2 | 建立 unknown-first 主动测量的合成可行性，诊断相同而逻辑成本 `62 -> 53` |
| E2-N1 | 暴露缺失、陈旧和 nuisance 测量会让简单主动策略更危险 |
| E2-D3 / E2-T1 | 修复已知失败后再用新 timing fault 检验，证明 development replay 不能冒充确认实验 |
| E2-D4 / E2-C4 | 用 guarded fixed prefix 保留固定策略长处，并在新 27-case suite 上独立确认 |
| E2-D5 | 修复联合草案暴露的长路径 freshness 缺口，但只算 development evidence |
| RT-E2-S0 / X1 | 建立真实轨迹盲包边界，并否决证据范围小于 freshness 声明的方案 |
| RT-E2-R2 | 在 24 条真实轨迹上保持诊断、对齐和 freshness `24/24`，逻辑测量成本 `264 -> 216` |

E2 的作用不是证明“任何生产测量都更便宜”，而是证明主动测量在测试过的 nuisance 和真实轨迹
范围内可以保持诊断质量并减少逻辑测量工作。

### 4.3 E3：证明动作、恢复和停止不能只靠模型自报

| 实验组 | 作用 |
|---|---|
| J-V1 | 否决“action 直接写最终 outcome”的错误因果模型 |
| J-V2 / E3-A1 | 建立诊断驱动动作的合成可行性，并初步隔离 action policy 的成本贡献 |
| E3-L1 | 在完整 residual lattice 上发现早期 Controller 的严重失败增加 `14 -> 26` |
| E3-C2 / E3-T1 | 修复 closure 后用 unseen no-op 检查；即使 aggregate 改善，只要有 treatment-only severe case 仍拒绝提升 |
| E3-M3 / E3-P1 / E3-R1 | 依次处理 fault-aware 选择、partial effect、fault timing、位置性 no-op 和 retry |
| E3-R2 | 证明物理恢复不等于可宣布成功：缺 receipt 或延迟验证时必须 evidence-blocked |
| E3-D4 / C4 / D5 / C5 | 建立共享 reconciliation 实现，否决 runner 绕过 adapter 的伪确认，并验证正负 evidence probes |
| RT-E3-D1 / H0-C1 | 在新 provider corpus 上确认 mechanism-aware proposal selection 改善 |
| RT-E3-C1 V1/V2 | 把 proposal 的权限调用和 runner 身份纳入审计，但仍只证明只读 proposal selection |

E3 的作用是把“模型建议采取动作”推进到“动作是否生效、证据是否闭合、是否应重试或停止”。
它不单独证明真实仓库任务的最终成功率。

### 4.4 联合、真实输入与执行安全：逐级打开下一道权限门

| 实验组 | 作用 |
|---|---|
| J-X1 | 无效 E3 前置条件会阻止联合实验，证明依赖有效性不可补偿 |
| J-N2 | 冻结 E2 V5 和 E3 V5，在 unseen cross-layer nuisance 上完成独立 synthetic joint exit |
| RTS-X1 / X2 / X3 | 依次暴露 provider 超时、截断、JSON 和自由文本证据不稳定，并用 candidate ID 闭合 provider harness |
| RT-P0 / RT-R1 | 建立永久只读工具边界，并确认真实 provider trajectory corpus 的完整性和多样性 |
| S1 / S2 | 证明执行目标位于源项目之外，attempt/effect/retry/receipt 不能跨 run 或漂移 |
| S3 | 在注入写故障时验证 disposable state rollback；cleanup 不确定时不得宣布成功 |
| C1 | 验证 fresh action observation 能驱动 recovery 或安全停止 |
| C2 | 验证 budget、no gain、regression 和 external block 下的长时停止 |
| C3 | 验证 targeted gain 不能靠破坏已知长处来补偿 |
| R1 | 只证明专家特化实验 harness 在合成条件下可辨识，不证明真实专家路由收益 |

这些实验的共同作用是：在允许真实 provider 和沙箱写入之前，先证明证据、权限、回滚、停止和
非补偿门不会因平均成功率提高而被绕过。

### 4.5 RS-V1 至 V5：从无效演示走到受限任务结果

| 实验 | 结果 | 独立作用 |
|---|---|---|
| RS-V1 | `0/4 -> 0/4`，三个 false success；无效 | 暴露公开规格不完整和写入内容不可回放 |
| RS-V2 | `4/4 -> 4/4`；有效负结果 | 暴露 control ceiling，说明“没有改善”也必须保留 |
| RS-V3 | provider JSON 缺 `file_content`；基础设施无效 | 暴露 provider malformed output 不能让整个实验无记录中止 |
| RS-V4 | 空响应触发异常；基础设施无效 | 进一步冻结异常记账边界，任务和门槛不因失败而调整 |
| RS-V5 | `2/8 -> 8/8`，六个改善、零退步、零 false success；通过 | 首次把新鲜 E2 测量到 E3 修复连接到独立评估的 provider-backed disposable-sandbox 任务结果 |

RS-V5 最重要的作用不是同主流 Agent 排名，而是排除以下解释：

- E2/E3 只在纯 synthetic 状态机里有效；
- active 只是更早宣布成功；
- 成功来自修改隐藏 evaluator；
- 成功依赖写入源仓库或遗留未清理沙箱；
- 任务或阈值在看到结果后被调整。

它仍没有排除“一个拥有同等工具和反馈预算的 model-directed loop 也能做到 `8/8`”这一解释。

## 5. 与主流 Agent 的机制比较

这里的“主流 Agent”不是一个统一产品，而是模型主导下一步的常见工具循环，包括 ReAct、
plan-and-execute、图状态机 agent 和 coding agent。公开系统通常组合以下能力：

- 模型根据对话和工具结果选择下一工具；
- 运行测试或命令后，把 observation 返回模型；
- 模型决定继续修改、重新规划或停止；
- harness 提供工具 schema、权限、checkpoint、trace 和部分 guardrail。

OpenPilot 的差异不在于“是否有循环”，而在于试图把部分循环判定从自由文本推理提升为显式合同：

| 决策问题 | Model-directed agent 常见方式 | OpenPilot 主动迭代目标 |
|---|---|---|
| 下一步看什么 | 模型选择工具或由 Prompt 建议检查 | E2 根据 unknown、conflict、freshness 和 failure exposure 选择测量 |
| 下一步做什么 | 模型根据当前上下文选择修改或命令 | E3 根据 residual、effect 和风险选择 action/recovery/stop |
| 动作是否成功 | 工具返回、测试结果或模型总结 | attempt/effect/receipt/validation 共同闭合 |
| 是否继续 | 模型判断、固定轮数或图节点 | budget/no-gain/regression/external-block/closure 显式停止 |
| 是否允许成功 | 最终回答或测试通过 | freshness、false-success、安全和已知长处是非补偿门 |

公开机制差异不等于效果差异。只有直接的 matched experiment 才能判断哪一种在同一任务分布上更好。

## 6. 当前缺失的关键实验

下一优先级不是先扩大“active vs fixed”，而是建立同模型、同任务、同工具和同预算的三臂比较：

| 实验臂 | 决策方式 | 研究作用 |
|---|---|---|
| `fixed_order` | 冻结非自适应的读取、测试、修改和验证顺序 | 机制下限与实验灵敏度检查 |
| `model_directed` | 模型自行选择 read/search/test/write/retry/finish | 主流自主 Agent 的可复现基线 |
| `active_iteration` | E2/E3 依据 typed evidence 选择测量、动作、恢复和停止 | OpenPilot treatment |

主要比较必须是：

> `active_iteration` 对 `model_directed` 的任务配对差异。

`active_iteration` 对 `fixed_order` 只能作为次要机制比较，不能继续充当“优于主流方法”的证据。

## 7. 三臂实验的公平性条件

三臂必须共享：

- 同一冻结模型、endpoint、temperature 和 cache policy；
- 同一任务描述、初始仓库快照和初始可见信息；
- 同一 read/search/test/write/finish 工具能力与权限；
- 同一 token、LLM call、tool call、iteration、wall-time 和成本上限；
- 同一 public validation 和完全隔离的 hidden evaluator；
- 每个 task-arm 独立 disposable sandbox；
- 同一失败留分母、source fingerprint、cleanup 和 provider provenance 规则。

OpenPilot E2/E3 的模型调用、测量、测试、reconciliation 和 retry 成本必须全部计入，不能被当成免费
harness。`model_directed` 也不能由 runner 免费注入它没有主动读取的测试或 support 信息。

现有 RS-V3/V5 八个任务已经暴露并参与开发，只能用于 development smoke，不能重新标记为新假设的
独立 confirmation。确认实验必须在 runner 和分析计划冻结后使用新的真实仓库任务 holdout。

## 8. 如何解释未来结果

| 结果 | 可以支持的解释 |
|---|---|
| Active > Model-directed > Fixed | 结构化主动迭代在该冻结任务分布和预算下有额外收益 |
| Active ≈ Model-directed > Fixed | 主要收益来自有反馈的工具循环，不是 OpenPilot 特有机制 |
| Model-directed > Active > Fixed | OpenPilot 的显式控制在该范围内限制了模型 |
| Active 与 Model-directed 成功率相同但成本更低 | 可以进一步研究效率优势，但必须预注册成本主张 |
| Active 与 Model-directed 成功率相同但安全失败更少 | 可以支持受限安全/可控性优势，而不是能力优势 |
| Active 只优于 Fixed | 保留 RS-V5 的窄机制结论，不能形成相对主流 Agent 的优势主张 |

无论结果是哪一种，都应报告 objective success、paired improvements/regressions、false success、
严重副作用、token、工具调用、测试次数、时间和完整成本。平均总分不能补偿 treatment-only severe
failure 或已知长处退化。

## 9. 当前保留与更新规则

本文有必要保留，但应保持为“比较边界和缺失实验入口”，不再复制全部实验细节：

- 新增实验事实时，先更新实验总评审和冻结 artifact；
- 当事实改变 OpenPilot 与 `fixed_order` 或 `model_directed` 的可比较范围时，再更新本文；
- 三臂 development smoke 不能写成确认结论；
- 只有新 holdout 的 matched comparison 通过预注册门槛后，才能修改“尚未证明优于模型自主 Agent”
  这一边界。

## 10. 复核入口

- [当前实验总评审](../ACTIVE_ITERATION_EXPERIMENT_REVIEW.md)
- [追加式实验日志](../ACTIVE_ITERATION_EXPERIMENT_LOG.md)
- [下一实验矩阵](./ACTIVE_ITERATION_NEXT_EXPERIMENT_MATRIX.md)
- [信号决策日志](../ACTIVE_ITERATION_SIGNAL_DECISION_LOG.md)
- [任务轨迹证据设计](../../task_trajectory/TASK_TRAJECTORY_EVIDENCE.md)
- [ReAct](https://arxiv.org/abs/2210.03629)
- [Reflexion](https://arxiv.org/abs/2303.11366)
- [LangGraph](https://langchain-ai.github.io/langgraph/)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [AutoGen](https://microsoft.github.io/autogen/stable/)
- [OpenHands](https://docs.all-hands.dev/)
- [SWE-agent](https://github.com/SWE-agent/SWE-agent)
