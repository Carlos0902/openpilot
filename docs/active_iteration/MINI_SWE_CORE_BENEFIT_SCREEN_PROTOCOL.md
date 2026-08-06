# mini-SWE 核心主动迭代收益筛查协议

## 1. 文档状态与决策用途

- 状态：review-plane 已部分冻结；task-arm 执行协议仍未冻结
- 生命周期：exploratory screening
- 当前证据资格：`hypothesis_evidence_eligible=false`
- 当前生产授权：`production_execution_authorized=false`
- 不覆盖：`MINI_SWE_ACTIVE_ITERATION_EXPERIMENT_PROTOCOL.md`、既有
  acquisition V4、Phase 0 smoke V1--V4，或任何历史结果

本文定义一个**最短但仍可审计的 12-task 强信号筛查**。它不试图证明完整
OpenPilot、专家路由或生产 Controller 的总体价值；只决定是否值得继续投资
OpenPilot 的核心 E2--E3 主动闭环。

任务臂的 provider execution 在筛查执行协议冻结前仍不被授权。经单独冻结的
`CORE_BENEFIT_SCREEN_REVIEW_PROVIDER_PROTOCOL_V1.json` 授权后，允许使用无状态模型 API
作为**二审 review-plane**：它不是 agent，不进入 Bash/sandbox，不获得一审或任一 arm outcome，
也不产生任务 outcome。

目标结论仅限于：

> 在预先界定的诊断、验证或恢复密集型真实仓库修复任务上，核心主动闭环是否优于
> stock mini-SWE-agent 的普通 model-directed loop。

## 2. 为什么需要独立筛查路线

已完成的 mini-SWE Phase 0 V4 证明了两臂可在 mini-SWE 原生 harness 内公平运行、
完整记账并由 host-side hidden evaluator 验证；它只有一个已暴露 development task，
两臂均成功，不能形成收益结论。

既有 acquisition V4 则为约 20 个 exploratory task 和至少 60 个 eligible pool 设计。
它适合完整阶段 1，但不是最低成本的投资决策工具。该筛查以一个 12-task、配对、
单阶段设计，先回答“核心 treatment 是否出现足够强的正信号”。若未出现该信号，
结果只能是不确定或负向诊断，不是对所有收益假说的否定。

筛查为负或不确定时，不得以添加 OpenPilot 其他模块来掩盖结果；应先诊断 E2、E3 或
两者的设计。筛查为正也不授权生产接入或“完整 OpenPilot 已优于主流 Agent”的表述。

## 3. 待验证主张与非补偿顺序

主要问题：在共同预算下，`active_iteration` 的 hidden-evaluator
`verified_task_success` 是否高于 `ordinary`。

比较顺序固定如下：

1. 任务成功；
2. false success、严重回归、hidden-evaluator 泄漏、源仓库 mutation、sandbox cleanup
   failure 和不完整 receipt 的安全门；
3. 仅当 1--2 通过后，比较总 token、provider 调用、工具调用和墙钟时间。

任何成本节省均不得补偿任务失败或安全失败。

## 4. 实验臂与不变量

| Arm | 定义 |
|---|---|
| `ordinary` | 使用 stock mini-SWE-agent `DefaultAgent` 的 model-directed 工具、重试与停止循环；只增加对共同预算的外部计量和硬限制。 |
| `active_iteration` | 使用同一 mini-SWE agent/environment，接入冻结的最小 E2--E3 controller：`MEASURE`、`ACT`、`VERIFY`、`RECOVER`、`STOP` 与 `DELEGATE`。 |

两臂必须共享任务文本、初始仓库快照、模型/provider/endpoint、temperature、cache policy、
上下文上限、Bash 能力、sandbox、网络边界、公开验证、timeout 和共同预算。

controller prompt/schema 是 treatment 的一部分，必须指纹化，并将所有 controller 调用和
token 计入 active 总额。evaluator 不得收到 arm 标签；agent 不得读取 hidden evaluator、
gold/test patch 或任务的分层标签。

本路线不加入 `fixed_order` 臂，也不做 E2/E3 消融。它们属于随后解释机制的实验，而非
本次最低成本决策。

## 5. 任务分布、选择与隔离

### 5.1 目标分布

任务必须是公共问题描述下的真实仓库修复任务，且在 arm outcome 前由独立审核归为至少一类：

- 初始失败不能唯一定位根因，存在可区分的额外公开测量；
- 修改后需要验证、可能出现 partial effect 或回归；
- 需要恢复或正确停止，而不是简单直线式 read-edit-test。

这是一项**机制对齐的 scoped distribution**，不代表所有 SWE 任务。任务不得因为已观察到
ordinary 或 active outcome 而被纳入、排除或重新标注。

### 5.2 候选来源

候选仅可来自既有 mini-SWE acquisition V4 的、无 project exposure 的候选链。每个入选任务
在进入 manifest 前必须具备：

- 固定 dataset、harness、base commit 和 instance image identity；
- 两次一致的 base-failure 与两次一致的 gold-pass receipt；
- 网络隔离、资源和 cleanup gate 通过；
- 两次独立、blind-to-future-arm-outcomes 的 stratum review；若不一致，先完成 adjudication；
- 对每个入选任务单独完成 outcome-blind mechanism review，至少标记为诊断测量、动作后验证、
  恢复/安全停止中的一类；该标签不得从 task-arm outcome 推导；
- 不向 agent 或公共 artifact 暴露 gold/test patch、hidden test identity 或私有 rationale。

第二位审核者可以是人，也可以是经独立 review-provider protocol 冻结的无状态 API。若使用 API，
每个候选必须为独立单次请求；模型、endpoint、temperature、prompt hash、输入字段、私有输出目录和
public receipt schema 均需在第一次调用前固定。API reviewer 不得读取一审的 stratum/rationale，
也不得把输出传递给任一任务臂。

本次 12-task 快速筛查只从两份 review 已一致的候选中抽样，因此不需要为补样本执行第三方
adjudication；若未来使用分歧候选，adjudicator 必须与两名原 reviewer 身份不同，并保留两份原始
decision 的哈希绑定。机制审核可复用已经独立于一审的二审身份，但必须使用独立、冻结的机制审核
prompt，且不得读取 task-arm outcome；它不得复用 adjudicator 身份。两类 receipt 的私有 rationale
留在 private root，公共 artifact 仅保存摘要字段和哈希。相关机器协议为
`CORE_BENEFIT_SCREEN_ADJUDICATION_PROTOCOL_V1.json` 与
`CORE_BENEFIT_SCREEN_MECHANISM_REVIEW_PROTOCOL_V1.json`。

若当前候选不足以满足这些条件，筛查停止在 acquisition/review 阶段；不得为了凑样本改写
既有 receipt 或放宽 V4 的 hidden-data 规则。

### 5.3 冻结顺序

在任何新 provider/arm outcome 前，必须依次冻结：

1. 由符合条件候选构成的 pool receipt；
2. 12-task Stage A manifest，包含固定排序、选择 seed、stratum 和每仓库最多 4 个任务的 cap；
3. 同一机器可读执行协议，包含预算、模型、prompt/tool/runner/evaluator 指纹、平衡的随机 arm 顺序
   和分析计划；
4. 空的、不覆盖既有 artifact 的输出目录。

Stage A outcome 后不得替换、删减或新增任务。任何后续 20-task exploratory 或 confirmation
都必须使用新的、未运行任务集。

机器协议使用 `screen_execution_protocol.py` 的 `ScreenExecutionProtocol`：它必须精确绑定
Stage A manifest 与 pool hash、12 个 task id、同一 provider fingerprint、共同预算、单次模型调用、
6 个 active-first/6 个 ordinary-first 顺序以及 fail-closed 的 provider/timeout 政策。没有通过
`validate_screen_execution_protocol_bindings` 的 protocol 不得授权 task-arm execution。

## 6. 单阶段强信号设计

### 6.1 Stage A：12 个配对任务

对每个 `task_id + seed`，独立运行 ordinary 和 active 两臂。机器协议必须预先生成并冻结
6 个 active-first 与 6 个 ordinary-first 的顺序表。所有任务、
失败和超时保留在分母。每臂只运行一次；这是低成本 screening，不是对 provider 随机性的
正式估计。

记：

- `I`：active 成功而 ordinary 失败的配对数；
- `R`：ordinary 成功而 active 失败的配对数；
- `T`：成功状态相同的配对数。

### 6.2 Stage A 决策规则

仅在以下全部条件满足时，形成“值得继续”的强正信号：

- `I >= 6` 且 `R = 0`；
- 两臂均无安全门失败；
- 所有 source、receipt、trajectory 和 evaluator-completeness checks 通过。

六个同向 discordant pairs 的双侧 exact paired sign test 为 `p = 0.03125`。该阈值只支持
继续投入的决策，不构成广泛确认性结论。

若 active 出现任何安全门失败，筛查停止并回到 E2/E3 安全诊断。其余未达到强正信号的
结果（包括 `R > I`）均停止本次筛查，报告全部 `I/R/T`、失败原因与精确配对检验；结论是
“未观察到预注册的大效应”，不是 active 在一般任务上无收益。不得按该结果扩充、替换或重跑
这 12 个任务。

强正信号也仍只是 exploratory screening。后续必须在全新的任务集上运行独立的 20-task
exploratory 或 confirmation 实验，不能重用本次任务。

## 7. 共同预算、运行与并行规则

执行前的机器协议必须固定每臂的 provider calls、total tokens、tool calls、iterations 和
wall-clock 上限；不得为 active 或 ordinary 单独调高预算。预算应以当前 Phase 0 V4 的共同预算
为最低基线，并在任务首次运行前冻结最终数值。

默认并发为 `1`。只有在 outcome-free Air 压测已经冻结以下证据后，才允许在**不同 task pair**
之间提升并发：

- Docker、物理内存、磁盘和 provider 限流余量；
- 每 worker 的独立 work/report/output namespace；
- 资源超限、cleanup failure 和 Docker 异常的 fail-closed 行为；
- 在目标并发度下不发生 image、报告或 receipt 交叉污染。

同一 task 的 ordinary/active 两臂始终顺序运行，以冻结的随机 arm order 消除共享资源、缓存和
provider 限流导致的不公平。

## 8. 必须持久化的 artifact

- 冻结的 pool、Stage A manifest 和选择 receipt；
- 每个入选候选的原 review、adjudication（如适用）与 mechanism-review receipt；
- protocol、model/prompt/tool/runner/evaluator fingerprints；
- 每 task-arm 独立 native trajectory、usage sidecar、final files 与 cleanup receipt；
- arm-neutral host-side hidden-evaluator receipt；
- 完整配对结果、失败、超时、中断和 exclusion receipt；
- 分析脚本和不可变 result summary。

不得持久化 hidden test 名称、gold/test patch 内容、agent-arm outcome 之外的私有审核理由，或
可使 agent 访问 evaluator 的路径信息。

## 9. 解释与后续决策

| 结果 | 可作出的决定 |
|---|---|
| Stage A 强正信号 | 核心主动闭环值得进入全新 20-task exploratory 或机制消融；不要添加无关 OpenPilot 模块。 |
| 未达到强正信号、无安全问题 | 仅说明未观察到预注册的大效应；先诊断 E2/E3，或以全新任务集做更大样本实验。 |
| 成功打平但 active 明显低成本 | 形成效率假设；在新的预注册任务集确认后再称为收益。 |
| ordinary 更好、active 无优势或安全失败 | 暂停完整系统扩展，回到 E2/E3 机制诊断。 |

## 10. 预计工作量

复用已经完成技术预检的候选链时，预计为：二审与冻结 0.5--1 天、协议/runner 验证约 1 天、
Stage A 执行 1--3 天、分析半天。Air 资源不足或未通过 outcome-free 并行压测时，保持串行。

## 11. 启动提示词

在任务臂执行协议冻结前，后续工作只能准备 review、manifest、测试和资源预检。推荐使用：

```text
准备 mini-SWE 核心主动迭代收益筛查，不得产生 provider 或 agent-arm outcome。
先完整阅读 AGENTS.md、MINI_SWE_CORE_BENEFIT_SCREEN_PROTOCOL.md、
MINI_SWE_ACTIVE_ITERATION_EXPERIMENT_PROTOCOL.md、现有 acquisition V4 rules 和 README。
审计已有候选的 base/gold/hidden-data/review receipt；补齐第二次独立、outcome-blind 的
stratum review。随后生成可审计的 eligible-pool receipt 与 12-task Stage A manifest 草案。
不得读取或持久化 hidden test identity、gold/test patch 或私有 rationale；不得改写 V4 artifact。
除非 `CORE_BENEFIT_SCREEN_REVIEW_PROVIDER_PROTOCOL_V1.json` 已冻结，否则不得运行任何 provider；
无论如何都不得运行 ordinary 或 active arm。先写测试，再实现，并报告冻结执行前仍缺失的 gate。
```
