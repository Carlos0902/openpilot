# Context management experiment roadmap

Active-stage evidence mapping is maintained in
`EXPERIMENT_EVIDENCE_INDEX.md`; historical pre-route plans are not active
gates. The experiment harness executes in the matched
`openpilot-air:/Users/abaaba/work/openpilot-context-experiment-20260808-h0`
workspace; host and artifact transfer rules are in
`EXPERIMENT_EXECUTION_HOST_PROTOCOL.md`.

## 目的

这份路线图用于回答一个核心问题：在完整 OpenPilot 架构中，Context
Assembly、Compact、证据投影、Reasoning 和 Provider 适配是否同时提高了
任务质量、降低了无效调用，并且没有扩大权限或制造“假成功”。

实验必须按依赖顺序执行。每个阶段开始前建立独立的阶段计划，阶段结束后
记录 receipt、失败原因、修复、验证和剩余限制；未通过门禁时不得进入下一阶段。

## 总体原则

1. 先离线契约，再 shadow，再真实 Provider，再真实 mutation。
2. 一次只改变一个主要变量：Compact、Reasoning、Provider、预算和权限不能在
   同一实验中同时变化。
3. 所有控制变量使用 typed metadata；实验 receipt 不是 runtime authority。
4. 所有真实实验默认 `transport_retries=0`、显式 profile、独立 run root、文件
   sentinel 和 mutation 检查。
5. unknown usage、缺失 finish reason、缺失 evidence 或质量无法判定时 fail closed。
6. 任务成功必须同时满足：执行状态正确、请求动作真实发生、质量契约通过、验证
   证据存在；不能用替代命令或 fallback 工具冒充成功。
7. `blocked` 表示 readiness、环境或 manifest 未满足且没有发出 Provider 请求；
   `failed` 表示执行、质量或安全失败；两者都不能计为 pass。

## 统一观测指标

每个实验至少记录：

- 任务质量：typed quality gate、人工抽样、required facts recall、矛盾率；
- 上下文：requested/final prompt tokens、required context、compact 次数、投影类型、
  source lineage、evidence coverage；
- Provider：request count、tool-call rounds、finalization count、finish reason、
  completion tokens、reasoning tokens、缓存命中/未命中；
- 执行：重复读取、no-progress、scope violation、fallback、runner error；
- 安全：read/write scope、mutation tools、project hash、验证命令、project mutation；
- 成本：总 input/output/total tokens、wall time、每个成功任务的调用数。

每个真实实验 manifest 还必须冻结 endpoint、provider/model、capability profile、
ReasoningPolicy、Compact mode/hash、tool allowlist、mutation flags、task/read-scope
和 source hashes；不能只记录“same provider”。

## 统一阶段门禁

### 离线门禁

- 相关 focused tests 全部通过；
- `PYTHONPATH=Code/src pytest -q Code/tests` 通过；
- `python -m compileall -q Code/src experiments/...` 通过；
- `git diff --check` 通过。

### 真实只读门禁

- 明确 Provider/model/capability profile；
- 只暴露必要的 read-only tools；
- `Task.read_files` 是 canonical scope；
- 至少 3 次重复，质量与 scope 结果可复现；
- `project_mutation=false`；
- unknown usage 或 evidence 缺口不能计为成功。

### 真实写入门禁

- 代码级 `allow_mutations`、用户确认、`write_files` 三者同时成立；
- 只能修改声明文件；
- 运行请求指定的验证命令，而不是 fallback 替代命令；
- diff、测试结果和 completion evidence 均存在；
- 失败必须可恢复，不能覆盖原文件或重复生成完整文件。

## 阶段路线

依赖不是简单线性链，而是一个 DAG：

```text
阶段1 file_reader
   ├── 阶段2/3 evidence + scope Provider 分支
   ├── 阶段4/5 对话 Compact 分支
   └── 阶段6A 固定 Provider 的 Reasoning 分支
阶段6A + 固定 Compact ──> 阶段6B 多 Provider adapter
阶段2/3 + admission ────> 阶段7 mutation
阶段4/5/6A/6B/2/3 ─────> 阶段8 只读收益
阶段7 ──────────────────> 阶段9 mutation 收益（独立）
```

### 阶段 0：路线图与基线冻结

**假设**：后续实验可以使用同一套可比较的 receipt 和门禁。

**动作**：冻结实验目录、任务 manifest、profile、环境变量、质量分类规则和
  统一报告模板；记录 Phase 28 作为当前 baseline。

**通过条件**：路线图入库；所有阶段都有输入、对照、指标和停止条件；不执行
  Provider 请求。

### 阶段 1：`file_reader` adaptive 窗口语义

**问题**：代码文件的 `adaptive` 模式可能忽略显式 `offset/max_lines`，导致分页
  结果与 evidence coverage 不一致。

**实验设计**：离线对 `.py`、文本和日志文件分别测试：

- `adaptive` 无窗口：保持原有完整/策略行为；
- `adaptive + offset + max_lines`：必须只返回窗口；
- `range`、`offset`、`full` 作为对照。

**关键指标**：`lines_read`、`total_lines`、`truncated`、offset、重复 evidence key、
  page count。

**停止条件**：任意显式窗口返回完整文件，或 metadata 标记与实际内容不一致时
  停止真实实验，先修复并回归。

### 阶段 2：fully-scoped 多文件 evidence projection 矩阵

**假设**：Phase 28 的 local call-site projection 能在不同长文件和关系问题上稳定
  保留必要证据。

**任务矩阵**：

1. 单文件符号定位；
2. 两文件直接调用关系；
3. 长文件中段符号；
4. 一个文件内多个 guard/call 分支；
5. 两个长文件的交叉关系。

**对照**：当前 projection、旧 projection replay（只用于离线对照，不作为生产行为）。

**执行**：每项至少 3 次，DeepSeek `real_read_only`，同一 scope、同一 profile、同一
  reasoning 设置。

**通过条件**：每项质量通过率至少 3/3；scope 内 evidence 完整；无 mutation；不能
  用扩大 page cap 或扩大 scope 掩盖失败。

### 阶段 3：scope 边界与拒答分类

**假设**：系统能区分“任务无法回答，因为信息不在授权 scope”与“模型没有读懂”。

**实验设计**：对同一个问题建立两臂：

- fully scoped：所有 required facts 都在 `read_files`；
- deliberately out-of-scope：刻意要求一个未授权文件事实。

**通过条件**：fully scoped 成功；out-of-scope 必须安全拒答并标记
  `scope_limited_refusal`，不能计为任务成功，也不能继续尝试越界读取。

**失败处理**：若拒答被计为成功，先修复 quality classifier；若发生越界调用，先
  修复 admission/runtime boundary。

### 阶段 4：对话内持久约束与长会话 Compact

**假设**：明确、稳定、确认过的约束在长对话压缩后仍保持 required/non-truncatable，
  普通对话噪声可以被压缩。

**实验设计**：构造 20–50 轮离线会话，加入：只能修改指定文件、必须使用指定验证
  命令、不得改变 API 等约束；插入 assistant noise、重复请求和中间结果。比较：

- 无结构化约束投影；
- 当前 session constraint projection；
- projection + compact。

**指标**：constraint recall、hash 稳定性、冲突率、required context token、compact
  token、summary source lineage。

**通过条件**：确认约束 recall=1.0；assistant noise 不改变约束状态 hash；compact 不
  替换 required constraint；普通历史可压缩且能回溯 source。

**Phase32D 补充结果**：针对原阶段遗留的 checkpoint/resume 缺口，新增
`PHASE_32D_SESSION_CHECKPOINT_RESUME_PLAN.md` 与对应 result。turn 25 保存、加载后继续到
50-turn 的 ingress state 与 uninterrupted state 完全一致；tamper、cross-run、cross-project
均 fail-closed；恢复后的 Compact 保留 required scope、验证命令、当前失败和 typed source。
该补充实验还验证了 resumed ledger/request hash 与 uninterrupted assembly 一致；它是 direct
`RuntimeCheckpointStore` + `MemoryContextBuilder` offline/runtime boundary，不替代
`AgentRuntimeController.resume` exact continuation 或 provider-native long-session 质量实验。

**Phase32E 补充结果**：Controller canary 先发现并修复 checkpoint `session_id` 与 ingress
execution `run_id` 未绑定的问题（计划/结果见 `PHASE_32E0_CHECKPOINT_INGRESS_IDENTITY_*`）。修复后，
`AgentRuntimeController.resume` 的合法 `SessionExecutionCursor` canary 通过：恢复 ingress、
ContextBuilder derived projection、request/turn/constraint lineage 全部一致，替换进程的 memory
噪声未进入 prompt，且 provider/network/project mutation 均为 0。该结果见
`PHASE_32E_CONTROLLER_RESUME_CONTEXT_RESULT.md`。随后 Phase32E-A 使用真实子进程退出和替换进程
恢复 bootstrap-only checkpoint：goal hash/identity 一致、无 execution cursor、阶段只执行一次，
非法 goal hash 在 executor 前被拒绝，且 provider/network/project mutation 均为 0；计划/结果见
`PHASE_32E_A_BOOTSTRAP_RESUME_PLAN.md` 与 `PHASE_32E_A_BOOTSTRAP_RESUME_RESULT.md`。真实
Provider 质量、token usage 和 mutation 收益仍不在本补充实验的 claim boundary 内。

**Phase32F 补充结果**：为完成完整架构入口验证，新增
`PHASE_32F_INTELLIGENT_AUTOPILOT_RESUME_PLAN.md` 与对应 result。实验实际实例化生产
`IntelligentAutopilot` 并调用公开 `resume()`，再进入 `AgentRuntimeController`；cursor、ingress、
request/turn/constraint lineage 全部一致，替换 memory 噪声未进入 prompt，冲突 project identity
在 executor 前阻断，provider/network/project mutation 均为 0。该实验仍是 provider-free，不
宣称真实 Provider 质量、token usage 或 mutation 收益。

### 阶段 5：确定性分段 Compact 与 LLM summary

**假设**：PDF 中“分段压缩后调用 LLM 总结”可以降低 token，但必须有预算上限、来源
  链接和 source fallback。

**实验三臂**：

1. deterministic segmented compact；
2. segmented compact + 每段 bounded LLM summary；
3. 全量 LLM summary。

**强约束**：每段 summary 有固定 token 上限；summary 必须携带 source IDs；required
  facts 不允许只存在于不可验证的 summary；summary 失败时原始 source view 必须可用。

**指标**：token reduction、required fact recall、事实冲突、summary latency、重放
  成功率、fallback 率。

**停止条件**：summary 无 source lineage、required fact 丢失、预算超限或无法回退，
  立即停止 LLM summary 臂。

### 阶段 6A：固定 Provider 的 Reasoning 路由

**假设**：通用 Reasoning intent 基座 + provider capability adapter 能在不同模型上
  保持相同业务语义，不需要业务层判断模型名。

**实验设计**：固定 DeepSeek、Compact projection、Context/Task 和 tool contract，只切换：

- Reasoning：disabled、low/medium/high 或 provider default；
- Provider：DeepSeek、OpenAI 及其他已配置模型。

Routine read/verify 任务与 ambiguous recovery/plan 任务分别测试，不能混合 Compact 或
Provider 变量。

**指标**：reasoning tokens、empty response、finish reason、JSON/tool-call 成功率、
  request count、质量和 latency。

**通过条件**：reasoning exhaustion 与 context failure 可区分；不同 intent 的质量和
  usage 可归因；不能把 provider default 当作统一语义。

### 阶段 6B：固定 Reasoning 的多 Provider adapter

**前置条件**：阶段 6A 通过；固定 Compact、Reasoning intent 和任务输入，只切换 Provider
  capability adapter。

**实验设计**：对每个 Provider 记录 endpoint/model/capability profile。需要 tool wire 的
  Provider 若不支持该能力，必须 typed-block；不能 fallback 到 OpenAI/JSON planner 后
  仍计为该 Provider 的成功。

**指标与门禁**：tool-call round-trip、JSON/schema、finish reason、usage、质量和
  latency；unsupported capability 只能标记 `blocked`，不能计入成功率。

### 阶段 7：受控 mutation 真实任务

**假设**：只读任务稳定后，完整权限边界、fallback 和验证契约可以安全支持小型写入任务。

**实验设计**：选择一个小型真实修复任务，固定 `write_files`、目标 symbol、验证命令，
  运行：

- dry-run/read-only shadow；
- mutation canary（显式 opt-in + confirmation）；
- failure/recovery replay。

**指标**：实际 diff、越界写入、工具调用、验证命令是否真实执行、fallback 是否继承
  原权限和任务类型、completion evidence、回滚能力。

**停止条件**：任何越界写入、替代命令冒充成功、完整文件覆盖、权限丢失或 suspicious
success，立即停止 mutation 实验。

Phase 35 的真实结果是安全条件通过、Provider 质量 `NO-GO`：四次运行均在写入前停止，分别暴露
了 inspection command 越权、完整 evidence 后重复读取，以及缺少 `file_replace` operation 的
writer contract mismatch。它们不能计为 mutation 成功，也不能通过重放来“补样本”。

### 阶段 7A：mutation 失败/恢复契约回放

**目的**：在再次接触真实 Provider 前，隔离并验证 Phase 35 暴露的三个 transition failure。使用
normalized provider selections 的离线回放和 fake executor，不调用网络、不改变仓库、不改变
Compact、Reasoning 或预算。

**矩阵**：错误 inspection command；授权读取完成后的 duplicate reads；缺少
`operation_kind=file_replace` 的已有文件写入；以及一个正向 fake control（合法 replace 后精确
pytest）。

**通过条件**：负向臂均在执行前 typed fail-closed、无写入/替代命令/whole-file fallback、
`replay_count=0`；正向 fake control 只写声明文件一次并执行一次精确验证；receipt 的 scope、
confirmation、snapshot 和 request/response integrity evidence 可 canonicalize/校验。通过后
单独建立 7B 的一次真实 mutation canary 计划；失败则只修复对应契约，不得扩大真实实验。

### 阶段 7B：单次真实 mutation canary

Phase35B 的唯一变量是把已有文件的 `operation_kind=file_replace` 与 `overwrite=true` 写入任务
契约。结果为 `NO-GO`：Provider 成功完成了 bounded target write，但通用 RuntimeVerifier 先执行了
未经请求的 `<entrypoint> --help`，随后 exact validation 被判定为 order violation；fixture 的
runtime `sketch.json` 也暴露了 snapshot 分类遗漏。该运行不能计为 mutation 成功，也不能重放。

### 阶段 7C：Provider validation handoff 与 runtime artifact 边界

**目的**：离线验证 provider-native mutation 在 writer 后不触发通用验证 fallback，而是把 typed
`Task.validation_command` 留给下一轮 Provider `command_executor`；同时将已知 runtime sidecar 从
实验 snapshot 排除但继续检测真实用户文件变化。

**通过条件**：focused lifecycle/admission/round-trip/snapshot/receipt tests 全部通过，且不调用
Provider。只有通过后才允许下一次 single canary；若仍出现替代命令、order violation 或错误副作用
分类，mutation 实验继续停止。

### 阶段 7D：修复后单次真实 mutation canary

保持 35B 的显式 `file_replace` 提示和所有控制变量，只使用 fresh disposable workspace 验证
35C 修复。目标是观察 writer 后是否只执行 exact pytest，且 runtime sidecar 不再计入用户 diff。
首个 hard failure 立即停止，不重放；即使通过，也只能允许另立三次 mutation 稳定性实验，不能
直接宣称 mutation 收益。

后续审计发现原 35D receipt 的 `command_executor` 为 `mode=null`，输出是 `[DRY RUN] Would execute`，
旧 oracle 将其误记为 exact validation success；因此 35D 的原始 PASS 已撤销并保留为
`suspicious_success` 证据。Phase42 修复 typed validation mode、dry-run 识别和递归 runtime
snapshot 后，在全新根目录完成 3/3 真实 mutation 稳定性通过，才允许进入 mutation benefit 的
独立配对实验。

### 阶段 8：完整架构只读收益实验

**假设**：在完整 `IntelligentAutopilot → RuntimeController → context assembly →
  provider/tool loop` 链路中，上下文控制减少无效调用和总 token，同时不降低质量。

**实验设计**：固定任务集，做 paired/factorial 实验：

- current baseline；
- Compact-only；
- Reasoning-only；
- Provider-adapter-only；
- full context-control bundle。

每个任务至少 3–5 次重复，先只包含 read、analysis、validation；mutation 收益另立阶段。

**主要结论指标**：任务成功率、质量分、总 input/output/total tokens、tool calls、
  duplicate/no-progress、recovery、wall time、mutation safety。

**最终通过条件**：质量满足 non-inferiority margin，至少一个核心效率指标达到预先
  设定的 paired effect threshold，并在多任务、至少两个 Provider 或明确 typed-block
  的情况下复现。沿用 `GO / CONDITIONAL / NO-GO`：低样本或单 fixture 只能给描述性结论，
不能改变默认策略；若只减少调用但增加总 token，不能判定为最终收益。

阶段8先拆为三个可验收子阶段：

- **8A / Phase36 baseline observation**：固定当前完整 Provider-native read-only 路径和
  fully-scoped 任务矩阵，记录质量、prompt/completion/reasoning、调用和 no-progress 基线；
  不做因果收益结论。
- **8B**：在相同任务、Provider、Reasoning 和工具契约下，单独切换受控 Context/Compact
  ablation，先离线验证 source lineage、required constraint 和 prompt budget，再做 shadow。
- **8C**：paired 多任务、多重复收益实验，比较 current、Compact-only、Reasoning-only、
  Provider-adapter-only 和 full bundle；只有达到 non-inferiority/effect 门禁才改变默认策略。

Phase36C 的 paired shadow 先给出 `CONDITIONAL/NO-GO`：compact-history 6/6 quality pass，
raw-history 6/6 在首轮越界读取被 typed scope 拒绝。raw 臂更低的 token 不能计为收益，因为它没有
完成读取或答案；在进入 factorial study 前必须先完成 **8D / raw path-grounding diagnosis**，并
补齐 provider rejected-input 的结构化 receipt evidence。

**8D / Phase36D 诊断结果**：先修复了 provider 相对路径的 project-root 绑定；修复后 raw 读取虽
成功，第二轮仍因整段历史被塞进一个 required user message 而触发 context budget failure。该
required message 不能被静默截断，说明下一步应做 message-level segmented history，而不是放宽
scope 或丢弃任务约束。

**8E / Phase36E**：离线验证 required task/constraints、compactable dialogue、evidence summary
和 artifact reference 的分段边界，再做小规模 real shadow；只有通过后才回到 8C factorial
收益实验。

Phase36E 已通过离线门禁：unsegmented required history 在预算不足时 fail-closed，segmented
source/compact 保留 required authority 并原子替换历史。下一阶段需要一个 provider-native
initial-context projection 入口，不能继续把完整历史拼进单条 required user message。

**8F / Phase36F：provider-native initial-context projection**：新增可选
`initial_context_candidates` 入口，将 required system/task/constraint、optional dialogue 和
source-linked artifact 投影送入真实 provider round-trip；后续轮次重新按候选 retention 装配，同时
保留 assistant tool-call 与 `role=tool` 的结构化状态。第一次真实 shadow 暴露了“后续普通
message builder 把 optional history 提升为 required”的缺陷，修复后 fresh pair 两臂均通过：
raw segmented 与 compact segmented 都是 3 requests、quality pass、mutation=0；provider prompt
tokens 分别为 7,952 与 3,130，completion tokens 均为 334。该约 61% input reduction 只是单任务、
单 provider 的描述性信号，不能改变默认策略。

下一阶段为 **8G / Phase36G multi-task initial-projection shadow**：固定同一 provider、reasoning、
预算和工具契约，对 `single_file_symbol`、`two_file_linkage`、`adaptive_window_evidence` 做
paired raw-segmented/compact-segmented 多重复实验，记录 provider cache、prompt/completion/total
tokens、质量、调用、停止、证据覆盖和任何 context/scope failure。通过后才进入 8C factorial
收益实验；失败则只修复投影/上下文契约，不扩大真实任务范围。

Phase36G 已完成：三个任务各两次 raw/compact paired shadow，12 个 task-arm executions、36 个
provider requests，12/12 quality pass、project mutation=0、scope/context/no-progress failure=0。
raw→compact 的 provider prompt tokens 在三个任务上分别约下降 59.9–60.1%、48.2%、52.8%，且
request count 与 completion quality 没有恶化。该结果通过 8G 描述性门禁，但仍只有一个 Provider、
六对样本，不能作为全局 cost/quality 结论。下一步进入 **8C factorial benefit study**，比较
current/raw、Compact-only、Reasoning-only、Provider-adapter-only 与 full bundle。

**Phase37 / 8C pilot** 先把五个 arm 的可比性固定下来：所有 arm 使用同一 provider-native
read-only loop；`current_raw` 是无额外历史投影+provider default，`compact_only` 是普通 user message
中的 bounded summary，`reasoning_only` 是无投影+disabled，`provider_adapter_only` 是 raw
segmented typed candidates+provider default，`full_bundle` 是 compact typed candidates+disabled。
两个稳定任务各跑一遍，10/10 cells quality pass、30 requests、mutation=0；provider-default
reasoning token 在两个任务上实际出现（单文件 35/25/210，多文件 223/198/491），但未耗尽 ceiling。
`full_bundle` prompt tokens 为 3,186/4,133，显著低于 raw typed adapter 8,547/9,166，方向与
Phase36G 一致。该 pilot **PASS**，但仍是描述性结果；下一步才是预注册任务集和多重复的完整 8C
factorial study，第二 Provider 只有在 typed tool wire 支持时加入，否则 typed-block。

**Phase38 / 8C full factorial 首次运行**按预注册 stop gate 在第13个 cell 停止：
`two_file_linkage/current_raw/repetition1` 的三轮 tool execution 全部成功、无 scope/context/
mutation 问题，但最终答案漏掉必需的 `--once` 关系，quality=`failed_quality`。此前完整的
`single_file_symbol` 15 cells 均通过，`two_file_linkage` 的 reasoning_only/full_bundle 也通过；
但 denominator 不完整，不能宣称 factorial benefit。该结果为 **CONDITIONAL/NO-GO for
quantitative claim**，不重放失败 cell，不改变默认策略。

下一步先做 **Phase39 cross-file quality stability diagnosis**：固定 `two_file_linkage` 的
context/projection，只分别比较 provider-default 与 disabled reasoning，验证 quality checker、
`--once` 关系证据和 provider response variance；质量稳定后再以新 campaign root 续跑 Phase38
剩余 cells。失败原因没有确认前，不把它归因于 Compact 或 token 预算。

**Phase39 结果**：离线重检确认原 stopped receipt 是 quality-oracle false negative：答案包含
`args.once` 与 `_run_once_mode`，旧 fixture 只接受字面量 `--once`。修正 quality vocabulary 为
`("--once", "args.once")` 并加入负向回归测试后，保存答案离线通过；新鲜 two-file pair 的
disabled/provider-default 两臂均 quality pass、3 requests、无 scope/context/mutation failure，
provider-default reasoning tokens=124。Phase39 **PASS（oracle-calibration gate）**；不重写原始
receipt、不重放失败请求。

下一步是 **Phase40 factorial continuation**：使用修正后的 quality oracle、新 campaign root，
重新执行完整预注册矩阵；原 Phase38 的 partial campaign 仍单独保留，不能与续跑结果隐式合并。

**Phase40 结果**：新 root 完成 45/45 cells、135 provider requests，三个任务所有 arm 均 quality
pass、mutation=0、scope/context/no-progress/suspicious-success=0，五个门禁全部通过。full bundle
相对 raw typed adapter 的 median prompt reduction 为 `single_file_symbol` 62.8%、`two_file_linkage`
49.0%、`adaptive_window_evidence` 55.8%；相对 current raw 的 median total-token change 为
−9.2%、+4.2%、−19.4%，request delta=0。disabled arms reasoning=0，provider-default arms 有
可归因 reasoning usage，均未 exhaustion。Phase40 **PASS（pre-registered read-only factorial
benefit gate）**。

这允许进入一个 feature-flagged read-only canary，但不直接改默认：先做小流量 bundle canary、保留
current rollback、继续记录 quality/token/cache/reasoning/evidence。Mutation benefit 仍必须另立
Phase9，使用 fresh disposable workspaces、显式权限和 exact validation，不能从 read-only savings
推断写入安全。

**Phase41 结果**：fresh v3 `two_file_linkage` read-only canary 的普通 current arm 和显式开启
bundle arm 均 3 requests、quality pass、project mutation=0；关闭 flag 但注入 8 个 typed
initial-context candidates 的 arm 在 provider transport 前 fail-closed，request_count=0、mutation=0。
两条执行臂的 provider usage 分别为 prompt/completion/total `5,056/641/5,697` 与
`5,051/674/5,725`；该阶段验证的是开关边界和可回滚性，不新增收益结论。期间修复了微秒级
run-id 防碰撞和 receipt error-text 聚合误判，v3 fresh root 才是正式判定依据。Phase41 **PASS
（feature-flagged read-only canary gate）**，默认仍保持关闭。

下一步不自动扩大真实任务范围：为 **Phase9 mutation benefit and safety** 单独写计划，使用
fresh disposable workspace、explicit write scope、exact validation、sentinel/rollback 和
suspicious-success gate；只读收益不能直接推断 mutation 安全。

**Phase42 / Phase9A 结果**：三次全新 disposable workspace 的 calculator mutation 均通过真实
automatic pytest、目标文件唯一变更、API/diff/sentinel/receipt 全部门禁；projection flag 保持关闭。
首轮 dry-run 假成功和第二轮嵌套 `tests/__pycache__` 快照误报均保留为失败证据并修复后重跑，不能
计入成功分母。Phase9A **PASS（mutation execution-stability gate）**；下一步是单独制定并执行
Phase9B current-vs-compact mutation benefit paired study。

Phase9B 的阶段计划已写入 `PHASE_43_MUTATION_BENEFIT_PLAN.md`：必须新增独立的、默认关闭的
mutation projection flag，不能复用 Phase41 的 read-only flag；配对比较 raw provider-facing
history 与其 compact replacement，先做 offline fail-closed/candidate-lineage/round-trip/receipt
门禁，再做三组 fresh paired mutation。
任一臂的实际写入、exact automatic validation、scope、quality 或 suspicious-success 失败，整对
不计收益。

**Phase43 / Phase9B 结果**：三组 fresh raw/compact mutation pairs 全部通过完整 9A safety
oracle。raw 与 compact 每臂均为 4 provider requests（2 reads、1 file_replace、1 exact
automatic pytest），唯一用户变更为 `calculator.py`，API、bounded diff、sentinel、receipt
integrity 和 replay 门禁均通过。compact 相对 raw 的 paired median prompt tokens 从
`11,959` 降至 `5,560`（53.5%），total tokens 从 `12,459` 降至 `6,050`（51.4%），request
delta=0；reasoning 在该 disabled lane 中保持 unknown/not applicable，不当作 0。Phase43
**PASS（exploratory mutation benefit gate）**，但只能授权更大确认性实验，mutation projection
flag 继续默认关闭。

Phase43 先后暴露并修复了 provider continuation 的两个上下文投影问题：历史工具结果压缩会把
完整证据原地裁成 `preview=""`；完整短文件使用 `preview` 字段会让 provider 误判为不完整并
重复读取。修复后新增 round-trip/compaction 回归，focused provider/admission/deepseek tests
为 **72 passed**。失败诊断 receipts 保留在独立 roots，不回填正式 paired denominator。

下一步进入 **Phase44 / Phase9C confirmatory mutation matrix**，阶段计划见
`PHASE_44_CONFIRMATORY_MUTATION_MATRIX_PLAN.md`：固定 Phase43 的 compact contract，增加第二种
mutation shape/多文件关联任务；第二 Provider 仅在已有 typed tool-wire capability profile 时加入，
否则显式 `blocked`。保持 mutation flag default-off，继续以质量 non-inferiority、scope/
validation/receipt safety 和 paired input/total-token effect 作为门禁。

**Phase44 / Phase9C stratum-1 result**：首个 symbol-patch canary 暴露了
`file_patch_writer` provider schema/admission 缺少 `symbol_name` 与
`replacement_text/patch` 条件字段；修复后又由离线审计发现 exact-duplicate governance 会丢失
重复的空 assistant tool-call 投影，破坏 DeepSeek continuation。两处均按现有 typed contract
修复，新增 schema/admission、round-trip、duplicate-wire regression，focused suite 达到
119 passed；失败 roots 保留且不 replay。

最终 fresh `phase44_symbol_patch_canary_v4` 两臂通过，随后 fresh
`phase44_symbol_patch_matrix_v2` 完成 3 对/6 臂全部 PASS。raw→compact 的 median provider
prompt tokens `12,547→8,155`（35.00% reduction），total tokens `13,230→8,767`
（33.73% reduction）；completion median 没有稳定的收益方向，request count 也没有一致下降
（raw median 4，compact median 5；paired deltas mixed）。每臂均完成真实
`file_patch_writer modify_symbol divide`、目标文件唯一 bounded diff、API unchanged、一次
automatic exact pytest、无 forbidden path、sealed receipt、replay=0。Reasoning 显式 disabled，
unknown/not-applicable，不归零。

Phase44 stratum-1 **PASS（confirmatory single-target mutation gate）**：确认 compact 对该
DeepSeek symbol-patch lane 有输入/总 token 收益并保持安全契约，但不能宣称调用次数收益、跨
Provider 收益或默认 mutation projection rollout。下一步只进入计划中的 cross-file-linkage
mutation stratum；若无等价 typed tool-wire profile，第二 Provider 记录 typed-block。

**Phase45 / Phase9C cross-file-linkage plan** 已记录在
`PHASE_45_CROSS_FILE_MUTATION_PLAN.md`。它新增 `calculator.py → consumer.py →
tests/test_calculator.py` 的三文件读取关系，仍只允许 `calculator.py` 的 `divide` symbol patch，
并重新执行 raw/compact canary 与三对确认矩阵。离线 gate 已通过 110 项；真实 provider canary
尚未开始，不能把 Phase44 的单目标结果扩展到跨文件任务。

**Phase45 诊断与修复**：首个 canary 的 compact arm 完成三文件读取后重复读取测试文件，触发
`ProviderToolNoProgress`，没有写入，因此该 root 只作为诊断证据。runner 增加了一次性、mutation-
scoped provider guidance；它只提示使用既定 typed writer，不执行工具、不扩大 scope，后续仍由
admission/no-progress fail-closed。新增 round-trip regression 覆盖该行为。

**Phase45 / Phase9C 结果**：fresh `phase45_cross_file_canary_v2` 两臂通过；随后
`phase45_cross_file_mutation_matrix_v1` 完成 3 对/6 臂全部 PASS。所有 arm 都读完
`calculator.py`、`consumer.py`、`tests/test_calculator.py`，只对 `calculator.py` 的
`divide` 执行一次合法 `file_patch_writer modify_symbol`，通过精确 automatic pytest，API
unchanged、bounded diff、无 forbidden path、sealed receipt、replay=0。raw→compact median
prompt tokens `22,244→9,845`（55.35% reduction），completion `1,666→1,053`（36.79%），
total `23,910→10,898`（54.02%），requests `6→5`（16.67% median reduction）；观察到的
provider reasoning usage median `863→286`（60.87%），但请求 policy 仍显式 disabled，不据此
宣称全局 reasoning 策略收益。Phase45 **PASS（DeepSeek cross-file-linkage mutation
stratum gate）**，结果详见 `PHASE_45_CROSS_FILE_MUTATION_RESULT.md`。该结果仍不授权默认
mutation projection、跨 provider 推广或更广泛任务收益；下一步进入 cross-provider/Reasoning
矩阵。该阶段计划已写入 `PHASE_46_CROSS_PROVIDER_REASONING_PLAN.md`，先执行离线 capability/wire
contract gate，再逐 provider 做 reasoning-disabled read-only canary，之后才允许隔离 reasoning
策略实验，最后才做固定 policy 下的 raw/compact mutation 对照；缺少明确 profile 或真实凭据时
记录 typed-block，不做隐式 provider fallback。

**Phase46A / Phase10A 结果**：离线 capability/wire gate 已通过，focused reasoning policy、
reasoning adapter、native transport、DeepSeek tool round-trip、provider schema/admission 与
context assembly suite 共 **94 passed**。该阶段确认 provider-neutral resolution、unsupported
行为、工具调用身份和 required context wire 契约，没有改动 Compact、预算或默认 flag，也没有把
真实 provider 兼容性或 reasoning 收益提前判定为通过。下一步是逐 provider 的
reasoning-disabled read-only readiness canary；缺 profile/凭据时记录 zero-transport typed-block。

**Phase46B / Phase10B 结果**：DeepSeek 使用显式 `deepseek-chat-known/v1`、官方 tokenizer 与
真实凭据完成 readiness；fresh read-only canary 的 current/off 与 full compact bundle 两个执行
臂均 3 requests、quality pass、project mutation=0，candidate-with-flag-off 臂按契约 zero-
transport fail-closed。OpenAI 使用显式 `openai-chat-known/v1` 但当前无凭据且 tokenizer 不可用，
记录为 typed-block，未发请求。该阶段只证明 DeepSeek read-only wire/readiness 边界，不产生
reasoning 或 mutation 收益结论；下一步只对 DeepSeek 进入隔离 reasoning policy 实验。

**Phase46C / Phase10C 首轮结果**：按计划固定上下文和 completion reservation，只比较 DeepSeek
`disabled` 与 `provider_default`。disabled arm 3/3 valid JSON/goal、`stop`；provider-default
只有 1/3 在 bounded recovery 后成功，另两次 `length` + invalid/empty JSON，reasoning token
分别占满初始 `840/840` 和恢复后的 `1,140/1,140` ceiling。阶段按 hard stop 停止，没有扩大样本或
修改预算。根因是 routine structured decision 中 provider-default reasoning 与可见 completion
共享上限，非 Compact/context selection 问题。Phase46C **诊断停止**；下一步先写 reasoning
policy repair plan，定义 provider-neutral routine 策略、profile 映射、可见 completion reserve
和 usage/finish hard gate，再重新做一对 canary；未通过前不进入跨 provider mutation 矩阵。
该 repair plan 已写入 `PHASE_46C_REASONING_REPAIR_PLAN.md`，先做 R1 metadata/contract review 与
离线测试，再做一对 canary；不通过不扩大样本、不改 Compact、不上调预算强行重试。

**Phase46C-R 结果**：R1 离线 suite 39 passed。R2 一对真实 canary 与 R3 三对确认矩阵均通过；
共 6/6 structured JSON goal arm 一次完成、`finish_reason=stop`、无 recovery/no-progress，且
`provider_default` 在已知 profile 的 `json_object` 请求上稳定解析为 `disabled/mapped`，没有
改变 `max_tokens=840`。该修复解决了可见 JSON 被 provider-default reasoning 吃满的问题，但不
提供 reasoning 开启后的质量/成本收益结论；explicit high/free-form 仍需独立阶段。结果详见
`PHASE_46C_REASONING_REPAIR_RESULT.md`，下一步是单独写 explicit high/free-form 边界实验计划，
之后才考虑固定 policy 的跨 provider mutation。

**Phase46C-R4 结果**：按独立计划执行一条 DeepSeek explicit `enabled/high`、text/free-form、
无工具无 mutation 请求。`max_tokens=1,024` 被 reasoning 使用 `949` 占满，`finish_reason=length`，
只有部分可见 JSON-like 文本，不能通过完整输出门禁；prompt/completion/total 为
`1,920/1,024/2,944`，project/memory mutation=0。该阶段诊断停止，不证明 high 的质量收益；
也再次证明上调 max_tokens 不是 reasoning budget。当前不进入 high 的重复矩阵或 mutation 对照，
保持 high opt-in，等待 provider-specific reasoning-token 控制或独立可见 completion reserve。

**Phase46D / Phase10D 计划**已写入 `PHASE_46D_CROSS_PROVIDER_MUTATION_PLAN.md`：只有通过 46B
readiness、46C policy、等价 typed tool-wire 和 mutation oracle 的 provider 才能按 provider
分别做固定 policy raw/compact mutation canary 与三对矩阵。当前 OpenAI readiness 是
早期 `phase46b_provider_readiness_v2` 曾记录 `missing_credentials + tokenizer_unavailable`
的 zero-transport typed-block，故当时 Phase46D 不执行 OpenAI 请求；该 tokenizer blocker 已由
Phase46B-R 修复，当前 v4 只剩 `missing_credentials`。DeepSeek 的 Phase45 结果保持为单独
stratum，不能改标为跨 provider 结果。

**Phase46B-R tokenizer repair 结果**：新增严格的 `openai-chat-known` + local `tiktoken` adapter，
未知 OpenAI model 不猜 encoding，DeepSeek tokenizer 路径保持不变；focused suite **111 passed**。
fresh `phase46b_provider_readiness_v4` 显示 OpenAI `tokenizer_available=true`、
`tokenizer_id=tiktoken:o200k_base`，只剩 `missing_credentials`，仍 `transport_attempted=false`。
该阶段 PASS，下一步仍需真实 OpenAI credential 才能进入 46B read-only canary 和 46D mutation。

**Phase H8-R2AO-1 复验**：provider lane 已采用
`openai-chat-no-reasoning-known` 后，发现 tokenizer 判定仍只接受旧
`openai-chat-known`，导致远端 readiness 出现错误的 `tokenizer_unavailable`。修复后两个已知
OpenAI chat profiles 共享 exact `tiktoken:o200k_base` 分支；本地 focused **203 passed**、
远端 focused **175 passed**，当前远端 readiness 只剩 `missing_credentials`，且
`transport_attempted=false`。该修复不改变 reasoning transport 或 Compact；仍需 OpenAI 专属
credential 才能进入真实 cross-provider canary。

**Phase H8-R2AP 结果**：完成 OpenAI provider-neutral real read-only runner。DeepSeek
runner 的 receipt validator 现在接受显式 provider identity/schema 参数，默认行为保持
兼容；OpenAI runner 只替换 typed lane settings/readiness，继续复用相同的 context
projection、tool scope、quality、side-effect 和 R1→K1 admission。新增缺凭据时的
`typed_blocked` readiness receipt。远端 focused **20 passed**，zero-transport readiness
为 `missing_credentials`、exact `tiktoken:o200k_base`、`provider_calls=0`；没有 OpenAI
专属 key，因此不执行真实 R1/K1，也不把 DeepSeek key 当作 OpenAI 证据。结果见
`PHASE_H8R2AP_OPENAI_PROVIDER_NEUTRAL_CANARY_RESULT.md`。

**Phase H8-R2AQ 结果**：在 `openpilot-air` 用 freshly bound typed multi-task selection
和当前 provider-neutral DeepSeek runner 完成真实 R1→K1 pair。两 arm 各 3 次调用，
prompt `7,092→3,932`（−44.55%），total `7,820→4,460`（−42.97%），completion
`728→528`，调用次数 `3→3`；typed answer/source grounding、required constraint、
50-turn Compact lineage、exact read path 和全部 zero-side-effect gates 通过。期间修复
了 CLI 未自动传 selection typed answer facts 的输入契约遗漏。结果见
`PHASE_H8R2AQ_DEEPSEEK_PROVIDER_NEUTRAL_REGRESSION_RESULT.md`；该 pair 仍只属于
DeepSeek 单任务描述性证据，不外推为 OpenAI、默认开启或调用次数收益。

**Phase H8-R2AR 结果**：将已有 `calculate_summary_budget` 接入
`MemoryContextBuilder`。summary cap 现在由静态上限与动态剩余 prompt 槽共同决定，
扣除 required context、最近 suffix 和 response schema reserve；预算耗尽时不调用
summary factory，继续 deterministic fallback。相关 context suite **135 passed**、
实验目录 **76 passed**；默认 summary flag 仍关闭。结果见
`PHASE_H8R2AR_DYNAMIC_COMPACTION_BUDGET_RESULT.md`。

**Phase H8-R2AS 结果**：新增 provider-neutral rolling summary factory，并以
`OPENPILOT_ROLLING_SUMMARY_ENABLED=false` 默认关闭的 typed settings 接入
`IntelligentAutopilot`。factory 固定 `memory_compression`、`json_object`、无 tools、
temperature 0、disabled reasoning；adapter/原子选择失败继续 deterministic fallback。
factory/settings/context **69 passed**、extended context/provider **332 passed**、实验目录
**76 passed**。结果见 `PHASE_H8R2AS_PROVIDER_SUMMARY_FACTORY_RESULT.md`。

**Phase H8-R2AT 结果**：在 `openpilot-air` 以 DeepSeek v4 flash 做一次 builder-level
provider summary shadow。1 次 summary 请求返回完整 usage/finish，adapter accepted，
但 builder 最终仍选择 deterministic record；Prompt 保持 7,000 chars，摘要未进入
Prompt/authority artifact，side effects 为零。该结果是“安全通过、当前无收益”的信号；
后续先补 builder selection/fallback reason telemetry，再调整 schema/dynamic cap，不进入
默认开启、真实 tool-task 或 mutation。结果见
`PHASE_H8R2AT_PROVIDER_SUMMARY_SHADOW_RESULT.md`。

**Phase H8-R2AU-4 结果**：修复 AU-3R shadow 的 source identity 边界。selection producer
与 builder source derivation 显式使用 inert read-only memory dependencies；所有 shadow arm
改为接收同一个 `ValidatedSourceSnapshot`，绑定 candidate contract、session turn-ledger、
生产 compaction subset 和 Compact code manifest；新增独立 receipt hash/manifest/side-effect/
body/secret verifier。focused **13 passed**，实验目录 **85 passed**，context/metadata/
compaction/session **156 passed**。该阶段只证明实验基础设施可审计，不产生 Compact 收益结论。

**Phase H8-R2AU-5 结果**：将当前修复同步到 `openpilot-air` 新 disposable workspace，重新
生成 ready-only/target-bound selection，并通过 DeepSeek credential-free zero-transport
readiness；source snapshot 为 54 个 compact candidates、22 个 production eligible source，
execute root 保持不存在。由于远端没有 credential，credentialed readiness 与 R0/S1 provider
shadow typed-block，未发送请求。结果见 `PHASE_H8R2AU5_REMOTE_REBIND_ADMISSION_RESULT.md`。

**Phase H8-R2AU-6 结果**：在 fresh target-bound selection 上完成一次真实 DeepSeek R0/S1
paired builder shadow。S1 usage `2522/127/2649`、`finish=stop`，adapter accepted；builder
因 `generated_recent_suffix_displaced` 保持 deterministic record，summary 未进入 prompt 或
authority artifact，所有 mutation/writer/command/verification side effects 为 0。该阶段只
证明真实 provider 的安全接入与 selection telemetry，不证明摘要语义、token/call-count 收益、
跨 provider 泛化或 default-on。计划与结果分别见
`PHASE_H8R2AU6_CREDENTIALED_PAIRED_SHADOW_PLAN.md` 与
`PHASE_H8R2AU6_CREDENTIALED_PAIRED_SHADOW_RESULT.md`。

当前逐项证据审计见 `CONTEXT_MANAGEMENT_COMPLETION_AUDIT.md`；它明确区分已证明的 DeepSeek
stratum、offline constraint persistence、已停止的 high reasoning 和仍缺失的 OpenAI
cross-provider gate。

### 阶段 9：mutation 收益实验

仅在阶段 7 的安全门禁和阶段 8 的只读收益通过后执行。每次使用 fresh disposable
workspace；真实 mutation 不自动 replay。若副作用状态未知，只允许 no-op/recorded
replay，不允许重新写入；每次 replay 都必须重新满足 `allow_mutations` 与用户确认。

## 每阶段固定执行模板

1. 写阶段计划与假设；
2. 读取相关代码、测试、metadata contract 和上一阶段结果；
3. 先补离线测试，再做最小实现；
4. 运行 focused gate；
5. 运行真实实验或明确说明为何被 gate 阻止；
6. 保存 receipts、usage、finish reason、evidence coverage 和 sentinel；
7. 更新阶段 result 与 `IMPLEMENTATION_LOG.md`；
8. 只有通过门禁后，更新 Goal plan 进入下一阶段。

## 当前执行入口

阶段 1–9C、Phase32D–F、Phase46A–C-R4 已完成或按硬门禁停止，并分别保留计划、结果和 receipt。
当前入口是 `PHASE_46D_CROSS_PROVIDER_MUTATION_PLAN.md` 的 OpenAI 分支：H8-R2AQ 已完成
最新 DeepSeek runner 回归，但 OpenAI 只有在显式 profile、凭据、tokenizer 和
reasoning-disabled canary 全部就绪后，才可进入固定 policy raw/compact mutation。任何
typed-block 都只记录 zero-transport，不回退到 DeepSeek 或把单一 provider 结果扩展为
通用结论。Phase46D 的隔离入口已实现为
`stage46d_openai_cross_file_mutation.py`；当前无凭据运行已记录
`phase46d_openai_cross_file_mutation_v3/result.json` 的 typed-block 证据，
最近一次 provider readiness 复核为
`phase46b_provider_readiness_v5/readiness.json`，
阶段结论见 `PHASE_46D_CROSS_PROVIDER_MUTATION_RESULT.md`。
