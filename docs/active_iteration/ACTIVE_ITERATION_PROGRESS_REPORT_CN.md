# OpenPilot 主动迭代阶段进展汇报

> 汇报日期：2026-07-29  
> 当前阶段：核心主动迭代闭环已完成 provider-backed disposable-sandbox 范围验证；
> 与模型自主 Agent 的直接对比、通用仓库任务、跨模型迁移和生产接入尚未完成。

## 一、项目目标

OpenPilot 希望解决的不是“让模型多试几次”，而是让任务失败后的下一步具备可审计的决策依据：

1. 先判断还缺少什么证据；
2. 主动选择最有价值的测量；
3. 根据测量结果选择修复、恢复、重试或停止；
4. 用独立验证和动作 receipt 判断修改是否真的生效；
5. 不允许平均收益掩盖严重回归、虚假成功或越权副作用。

核心闭环可以概括为：

```text
任务失败
  -> E2 主动测量
  -> E3 动作 / 恢复 / 停止
  -> 新鲜验证与 receipt 闭合
  -> 独立任务结果评估
```

## 二、当前结论

当前最强、同时也是最窄的有效结论是：

> 在冻结的 provider/model、八个 runtime-contract 任务、四个任务类别和一次性沙箱边界内，
> 失败后向同一模型提供新鲜 E2 测量，再执行 E3 修复，可以把独立评估成功率从固定控制组的
> `2/8` 提高到 `8/8`，没有配对退步、虚假成功、源仓库污染或沙箱清理失败。

RS-V5 最终结果：

| 指标 | Fixed control | Active iteration |
|---|---:|---:|
| 客观任务成功 | `2/8` | `8/8` |
| 配对改善 | - | `6` |
| 配对退步 | - | `0` |
| 测量辅助成功 | - | `8` |
| 虚假成功 | `0` | `0` |
| 得到改善的任务类别 | - | `4/4` |
| Provider 执行完整 | 是 | 是 |
| 源仓库保持不变 | 是 | 是 |
| 沙箱全部清理 | 是 | 是 |

这证明了“新鲜测量能够给后续修复带来实际增量”，但还不能证明 OpenPilot 优于一个工具和预算
相同、由模型自行选择下一步的主流 Agent loop。

## 三、已完成的工作

### 1. 证据和实验基础

已建立严格的 trajectory、EvidenceBundle、manifest、receipt 和 artifact 回放边界：

- 跨 run 引用、内容漂移、篡改和过期证据 fail closed；
- 模型、Prompt、工具集、evaluator、任务 suite 和 runner 身份可冻结；
- 缓存输出与真实 provider 输出可以区分；
- 内存结果与持久化结果可以离线复算；
- 无效和失败实验保留，不允许用后续成功覆盖。

作用：保证实验结论不是一次无法复核的终端演示。

### 2. E2 主动测量

E2 经历了协议否决、nuisance 失败、timing holdout 失败、guarded prefix 和 balanced prefix 修复。
最终支持的范围性结论包括：

- 在独立 synthetic nuisance suite 上保持基线安全；
- 在 24 条真实轨迹上，两组诊断、对齐和 freshness 均为 `24/24`；
- 主动测量的逻辑成本从 `264` 降至 `216`。

作用：证明主动选择“下一步测什么”可以在保持诊断质量的同时减少无效测量，而不是固定地检查全部项目。

### 3. E3 动作、恢复和停止

E3 已覆盖：

- action no-op 和 partial effect；
- fault timing、位置性故障和可选 retry；
- receipt 缺失、延迟验证和 reconciliation；
- budget、no gain、regression、external block 和 evidence closure 停止；
- treatment-only severe failure 的非补偿拒绝。

作用：把“模型建议做了什么”推进到“动作是否生效、证据是否闭合、是否应继续”。

### 4. 联合闭环与真实 Provider

- 冻结 E2 V5 与 E3 V5 在 60 case/arm 的 unseen cross-layer nuisance 上通过联合 synthetic exit；
- Provider shadow 依次解决超时、截断、JSON 和自由文本证据不稳定；
- 新 provider corpus 上的 mechanism-aware proposal match 从 `12/16` 提高到 `16/16`；
- 真实轨迹采集保持只读、无工具网络和无源仓库修改。

作用：证明 E2/E3 可以组合，并且 provider 传输、证据格式和真实输入边界可被严格处理。

### 5. 执行安全与控制

- S1/S2：沙箱路径、源仓库指纹和 immutable receipt 边界通过；
- S3：注入写故障时完成回滚，清理不确定时不宣布成功；
- C1：fresh observation 驱动恢复或安全停止；
- C2：正确停止从 `12/30` 提高到 `30/30`，unsafe continuation 从 `18` 降到 `0`；
- C3：已知长处保持 `16/16`，目标弱项从 `0/16` 提高到 `16/16`。

作用：保证任务收益不能通过破坏已知能力、忽略副作用或无限重试获得。

### 6. Provider-backed disposable sandbox

RS-V1 至 V4 分别暴露了规格不公平、control ceiling、Provider JSON 缺字段和空响应记账问题。
任务、门槛和无效记录在看到最终结果前保持冻结，RS-V5 才形成接受结果。

作用：首次把 E2 新鲜测量到 E3 修复连接到独立评估的实际沙箱任务结果。

## 四、RS-V5 的价值与边界

RS-V5 排除了以下解释：

- E2/E3 只在纯 synthetic 状态机中有效；
- active 只是模型自报成功；
- 成功来自查看或修改 hidden evaluator；
- 收益依赖污染源仓库或遗留沙箱；
- 任务和阈值在看到结果后被调整。

RS-V5 没有排除以下解释：

- 一个拥有同等工具和反馈预算的 model-directed loop 也可能达到相同结果；
- 八个隔离 runtime-contract 任务不能代表一般软件工程任务；
- 结果可能不迁移到其他模型、Provider 或模型规模；
- disposable sandbox 安全不等于生产外部副作用安全。

因此，当前可以汇报“主动测量到修复的受限闭环有效”，不能汇报“OpenPilot 已经优于主流 Agent”。

## 五、与主流自主 Agent 的当前差距

常见 coding agent 允许模型自行选择 read/search/test/write/retry/finish。现有 RS-V5 只有 fixed 和 active
两臂，最多两次 provider 调用；active 第二次收到新鲜测试、support contract 和验证结果，而 fixed 不收到。

这个设计适合隔离“新测量是否有用”，不适合回答“结构化主动迭代是否优于模型自主工具循环”。

当前最重要的缺口不是继续扩大 active 对 fixed 的差异，而是完成公平的三臂确认实验。

两任务 development preflight 已经完成：`fixed_order`、`model_directed` 和
`active_iteration` 均为 `2/2`。因此当前没有主动迭代成功率更高的信号。active 使用
`4` 次 provider 调用和 `3419` token，model-directed 使用 `14` 次和 `10409` token，
只形成待确认的效率假设；任务已经暴露，不能作为独立证据。

## 六、下一阶段：三臂直接对比

建议冻结以下实验：

| 实验臂 | 决策方式 | 作用 |
|---|---|---|
| `fixed_order` | 固定读取、测试、修改和验证顺序 | 机制下限 |
| `model_directed` | 模型自行选择工具、重试和停止 | 主流自主 Agent 基线 |
| `active_iteration` | E2/E3 依据 typed evidence 决策 | OpenPilot treatment |

主要比较是 `active_iteration` 对 `model_directed`，不是对 `fixed_order`。

公平性条件：

- 同一模型、endpoint、temperature、cache policy 和上下文上限；
- 同一任务、初始仓库快照、工具权限和初始可见信息；
- 同一 token、LLM call、tool call、iteration、时间和成本预算；
- 同一 public validation 与隔离 hidden evaluator；
- E2/E3 的测量、测试、reconciliation 和 retry 成本全部计入；
- 每个 task-arm 使用独立沙箱，所有失败留在分母；
- 现有八个 RS-V5 任务只用于 development smoke；确认实验使用新的真实仓库任务 holdout。

结果解释：

| 结果 | 结论 |
|---|---|
| Active > Model-directed > Fixed | 结构化主动迭代有额外任务收益 |
| Active ≈ Model-directed > Fixed | 收益主要来自反馈循环，不是 OpenPilot 特有机制 |
| Model-directed > Active | 当前显式控制限制了模型 |
| 成功率相同但 Active 成本更低 | 可进一步验证效率优势 |
| 成功率相同但 Active 严重失败更少 | 可支持受限安全和可控性优势 |

## 七、风险与限制

当前仍未验证：

- 任意仓库任务上的总体成功率；
- 跨 Provider、跨模型和跨模型规模迁移；
- 真实模型专家路由的因果收益；
- 生产 Controller 的安全和净收益；
- 任意命令、源仓库写入、远程服务和生产数据副作用；
- 异步或并发 receipt 到达；
- 完整经济成本和生产故障概率。

任何一个方向都需要单独预注册实验，不能从 RS-V5 自动外推。

## 八、下一里程碑

1. 冻结新的真实仓库任务 holdout、Prompt、工具 schema、预算和分析计划；
2. 扩展真实仓库快照物化和多文件 evaluator，不再使用已暴露微任务；
3. 运行 `fixed_order / model_directed / active_iteration` 三臂配对实验；
4. 主要检验 active 对 model-directed 的任务成功差异，同时报告完整成本；
5. 根据结果决定后续优先研究任务收益、效率优势还是安全优势；
6. 在三臂确认结论形成前，不启动生产 Controller 接线。

## 九、汇报口径

建议使用：

> OpenPilot 已完成主动测量、动作恢复、证据闭合、停止和一次性沙箱执行的分层验证。
> 在冻结的八个 provider-backed runtime-contract 任务上，主动闭环相对固定控制从 `2/8`
> 提高到 `8/8`，没有配对退步和虚假成功。该结果证明新鲜测量能够改善受限修复，
> 但尚未证明优于模型自主 Agent。下一阶段将以同模型、同任务、同工具、同预算的三臂实验
> 直接验证这一差异。

不建议使用：

- “OpenPilot 已经优于主流 Agent”；
- “已经适用于任意真实仓库任务”；
- “已经可以安全接入生产”；
- “八个任务 `8/8` 证明总体成功率为 100%”。

## 十、证据入口

- [技术实验综述](./ACTIVE_ITERATION_EXPERIMENT_REVIEW.md)
- [追加式实验日志](./ACTIVE_ITERATION_EXPERIMENT_LOG.md)
- [当前架构设计](./ACTIVE_ITERATION_EXPERT_ROUTING_ARCHITECTURE.md)
- [RS-V5 协议](../../experiments/real_sandbox_execution/REAL_SANDBOX_ACTIVE_ITERATION_PROTOCOL_V5.json)
- [RS-V5 任务套件](../../experiments/real_sandbox_execution/REAL_SANDBOX_TASK_SUITE_V3.json)
- [RS-V5 结果摘要](../../experiments/real_sandbox_execution/v5/result_summary.json)
- [RS-V5 完整执行](../../experiments/real_sandbox_execution/v5/execution.json)
