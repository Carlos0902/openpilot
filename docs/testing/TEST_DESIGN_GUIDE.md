# OpenPilot 测试设计指南（面向任务轨迹证据层）

本文用于指导 OpenPilot 的测试用例设计、补充与评审。
它现在不再依赖旧的 test-only 诊断叙事，而是建立在
[`docs/task_trajectory/TASK_TRAJECTORY_EVIDENCE.md`](../task_trajectory/TASK_TRAJECTORY_EVIDENCE.md)
描述的任务轨迹 / 证据层之上。

目标不是“证明代码能跑”，而是验证 Agent 系统在任务、过程、系统、安全与鲁棒性五个层面都符合预期。

---

## 1. 总原则
- 先验证问题，再扩展能力。
- 测试必须能定位到具体环节。
- 优先本地、离线、可重复的测试。
- 依赖 LLM / 网络 / 系统状态时，优先注入假对象或固定响应。

## 1.0 与任务轨迹证据层的关系

测试设计不再只看最终 pass/fail，而要尽量依赖轨迹证据：

- 任务是怎么被理解的；
- 路由为什么这么选；
- runtime state 怎么变化；
- 哪一步开始偏离；
- 最终结果和中间证据是否一致。

换句话说：测试负责定义“什么算对”，证据层负责提供“为什么会这样”。

## 1.1 测试开发流程
1. 先提出测试提案，明确测试对象、相关文档、设计方式、预期发现的问题、修复方向。
2. 用户确认后，再新增或修改测试。
3. 运行测试，观察是否暴露真实问题。
4. 如果发现问题，再提交修复方案。
5. 修复前再次获得确认，修复后补回归测试。

> 默认不直接改实现；先用测试把问题说清楚，再决定是否升级代码。

---

## 2. 测试分层

OpenPilot 的 Agent 测试建议分为五层。

### 2.1 任务指标
验证任务是否真正完成。

建议拆分为子目标：
- 核心产物
- 正确性
- 完整性
- 一致性
- 收尾状态

### 2.2 过程指标
验证规划、推理、工具调用是否合理。

关注点：
- 规划是否先于执行
- 路由是否符合任务类型
- 关键 decision 是否记录完整
- trajectory 是否可回放

### 2.3 系统指标
验证成本、稳定性和效率。

关注点：
- Token 成本
- 工具调用次数
- 重试次数
- 运行时长
- 成功率

### 2.4 安全指标
验证是否越权、幻觉或调用危险能力。

关注点：
- 危险命令
- 越权写文件
- 跳过审批
- 伪造完成状态

### 2.5 鲁棒性指标
验证任务换表达、换边界后是否仍稳定。

关注点：
- 同义改写一致性
- 中英混合一致性
- 缺上下文时的降级
- 边界条件变化后的不变量

---

## 3. 任务指标的评分方法

建议把任务指标拆为多个子目标，分别打分，再加权融合。

### 3.1 推荐字段
- `sub_goal`
- `weight`
- `check_method`
- `expected_evidence`
- `score`
- `failure_reason`

### 3.2 推荐评分方式
- 每个子目标独立评分。
- 最终总分按权重加权。
- 核心子目标可设置硬门槛。

### 3.3 推荐结构

| 子目标 | 权重 | 说明 |
|---|---:|---|
| 核心产物 | 40% | 结果是否真的生成 |
| 正确性 | 25% | 内容是否正确 |
| 状态更新 | 15% | 运行状态是否正确收尾 |
| 过程合理性 | 10% | 轨迹是否合理 |
| 安全合规 | 10% | 是否越权或危险 |

### 3.4 判定原则
- 核心产物失败时，总分高也应判失败。
- 加权分用于定位问题，不替代硬验收。

---

## 4. 本仓库的测试风格
- 契约优先：先测 metadata、字段、序列化、类型一致性。
- 副作用可验证：不仅测返回值，也测文件、索引、日志、状态变化。
- 状态机可回放：测运行时、路由、阶段推进、blocked/recover。
- 记忆与上下文拼装：测对话记忆、项目索引、环境上下文、prompt 顺序。
- 上下文恢复：在 `context_assembled` 后中断并改变源数据，断言相同 request 原样回放；
  artifact 损坏时必须在 session 执行前阻塞。
- Token 预算：使用确定性 tokenizer 测中英文混合输入，断言最终 token 不超预算、Metadata
  记录 tokenizer/model；无 tokenizer 时断言明确回退字符预算而非使用估算值。
- 渲染与 UI 稳定：测终端展示、dashboard、task graph、进度状态。
- 失败路径细致：测超时、缺字段、非法输入、越权、fallback。

---

## 5. 推荐的用例类型

### 5.1 单元契约测试
适合：
- metadata
- config
- serializer
- pure util

### 5.2 工具执行测试
适合：
- 文件写入/删除
- patch 生成
- 命令执行
- 索引刷新

### 5.3 轨迹测试
适合：
- tool routing
- runtime state transition
- planning/execution chain
- decision history

### 5.4 端到端验收测试
适合：
- 完整任务执行
- 任务完成与收尾状态
- 产物正确性

### 5.5 安全与鲁棒性测试
适合：
- 危险命令拦截
- 越权写入拦截
- 输入改写变体
- 边界条件

### 5.6 断点恢复与副作用测试

至少同时断言：

- checkpoint 的 generation、checksum、safe boundary；
- 恢复前后 root task/session 身份不漂移；
- tool/file/verification/recovery budget 不重复计费；
- `prepared`、`observed`、`applied`、`verification_applied` 各崩溃窗；
- 文件 mutation 不重复执行，外部修改不被覆盖；
- 原进程已规划的验证命令进入 typed checkpoint，恢复后执行同一命令而非通用 smoke check；
- 多验证命令记录 ordered specs 和连续完成前缀，跨进程恢复只执行未完成后缀；
- standard 与 enhanced-UI session 都在 decomposition/每个 subtask 后推进 cursor；
- observed LLM/read artifact 重放不再次调用 provider/tool，不重复预算；损坏 artifact fail closed；
- 从旧 checkpoint 重试时，新 generation 仍接在当前 latest 之后，并记录 source checkpoint；
- 新子任务只清理上一子任务的 typed no-progress 临时阻塞，不清理权限/预算/漂移阻塞；
- 新进程恢复事件追加到原 run 且 sequence 连续，不产生相同 task/session 的第二个 run；
- 无对账探针的命令 fail closed；
- 至少一个真实子进程强制退出后的跨进程恢复用例。

### 5.7 根任务/子任务隔离与完成证据测试

至少同时断言：

- inspect/validate 子任务不会改写根任务的 typed execution mode；
- read-only 根任务不能被子任务升级，mutation-allowed 根任务不能被子任务降级；
- 必需 decision need 被 Guard 拒绝后，整个子任务不能因其他读取成功而 completed；
- `Task.write_files` 只表示计划，不能直接进入 observed `changed_files`；
- implement/write 任务必须有成功文件副作用，validate 任务必须有实际命令/验证结果；
- Guard 拒绝、completion rejection、task result、checkpoint 与 trajectory 状态一致。

---

## 6. 建议的测试模板

```python
def test_xxx_behaves_when_y():
    # Arrange: 构造最小必要上下文
    # Act: 调用目标函数/工具/运行时
    # Assert: 同时验证结果、轨迹、副作用、约束
```

### 推荐断言顺序
1. 任务结果
2. 关键轨迹
3. 副作用
4. 安全边界
5. 成本约束

---

## 7. 设计检查清单

上下文装配变更还必须分别验证：

- 装配内核不会修改来源 payload；
- 相同来源、预算和策略产生相同的 Prompt、条目选择和 section decisions；
- Metadata 自带的事件 ID/时间不应被误判为选择结果漂移；
- 精确 tokenizer 可用时 final tokens 不超过预算，不可用时明确走字符降级；
- source-specific builder 与 checkpoint replay 保持原 payload 和 request hash 兼容。
- typed candidate 测试必须覆盖 required/preferred/optional 顺序、禁止截断、头/尾截断、
  重复 candidate ID，以及 `ready` 与 `budget_insufficient` 的非法组合。
- full-request budget 测试必须验证 requested - reserved = effective、effective - final =
  remaining，并证明 insufficient request 在 cache/transport 前被拒绝。
- 固定 system/control instruction 必须完整保留或在 provider 前以 typed budget failure
  终止，不能以 `partially_kept + ready` 继续。
- JSON、代码、tool schema 等结构化 owner payload 若尚未拆成独立候选，必须禁止原始
  字符截断；测试需验证 oversized 输入不会把非法结构提交给 provider。
- owner fallback 测试应区分 context budget insufficiency、provider failure 与 response
  parse failure，不能由同一个 broad exception 分支抹去 typed 原因。
- 子任务 fallback 测试必须继承 typed kind、`read_files`、`write_files` 与
  `validation_command`：只读任务不得生成或写文件，implement/repair 缺少写范围必须 fail closed，
  validation 缺少命令不得猜测替代命令。
- completion evidence 测试必须比较声明命令与实际成功工具输入的 argv；`compileall` 成功不能满足
  `pytest`。混合计划中的越权 need 必须被丢弃或拒绝，不能因同轮另一个安全 need 成功而整轮成功。
- completion budget 失败路径必须覆盖 provider-reported usage、finish reason、部分响应 artifact、
  无 usage 的空响应退款，以及 length 截断只对下一次调用生效的一次性恢复额度。
- enhancement completion matrix 必须覆盖四个 exact purposes、各自 floor/ceiling、共享 stage total、
  complexity/value/prompt/remaining-call fair share；稳定 logical key 的 reserve 幂等、语义冲突、
  checkpoint JSON round-trip、known usage refund/unknown usage hold、reconcile apply-once、一次 bounded
  length recovery，以及 optional fallback / required typed failure。重建请求时 audit trace、
  context-selection 时间戳和随机 request ID 不得改变 provider replay identity；messages、max_tokens、
  resolved reasoning、provider/model 改变时必须改变。核心 code generation 不得消耗 enhancement pool，
  改进 code generation 必须共享该池；`finish_reason=length` 的代码不得进入写工具。
- project-improvement context fixture 必须对 analysis/goal/task-design 三个 purpose 分别验证：
  schema、目标、安全约束和紧凑验证摘要完整保留；README、逐文件、diagnosis、memory 可独立省略；
  required 自身超预算仍在 transport 前 fail closed；adapter 不修改 ProjectState/report 来源。
  缺少 prompt context 时，测试还必须证明 validated ProjectState 中的 product intent 不会丢失，并覆盖
  non-regression constraints、delivery surface 和 runtime mode 的稳定去重投影。还要证明 canonical
  project identity 排除其他项目与路径别名串扰，三个 purpose 均不重装 `memory_context.prompt_text`；
  bounded manifest 必须在遍历前剪枝、只投影文件名、不读大文件正文且不扩大 `safe_target_files`。
- project-improvement output fixture 必须对 strict delta 覆盖 unknown/full-state fields、超项/超长、malformed
  JSON、legacy 单向迁移和同界 fallback；project/goal/iteration identity 只能来自输入。Evidence IDs 只保留
  本次 request 中 kept/partially-kept candidates。Task delta 不接收 provider identity，目标 canonicalize 后必须
  属于 `safe_target_files`，无合法显式目标时 fail closed，且 validated safety/goal criteria 不能被模型删掉。
  Stack patch 必须是 strict typed mutable fields，并由 deterministic safety update 覆盖冲突值。
- contextual code-generation fixture 必须覆盖旧单块消息的 transport-preflight failure 与新候选入口的
  反事实：task、write scope、产品安全约束、完整当前源码和 output contract 全部 required/kept；大体积
  diagnosis/environment 不得填满预算；existing-file replacement 缺源码或源码本身超预算必须 fail closed。
  真实 arm 还必须核验写文件集合、测试文件 hash、精确 pytest/compileall 命令和 completed improvement
  evidence，不能只依据生成器响应或顶层 success。
  Symbol edit 的路由和 patch scope 必须使用目标文件权威全文；截断或带 omission marker 的 prompt evidence
  不能参与 AST、行号或 symbol offset 计算。测试至少覆盖一个超过 projection 上限的大文件。
- context 成本实验必须把 logical requests、responded usage、failed-attempt usage 和 observed totals 分开；
  reasoning 缺失必须为 unknown。仅在目标 purpose coverage 完整时成本结论才可 eligible；确定性 bypass
  应标记 mode/not-applicable 或 incomplete，不能被当作零 Token 的成功 provider 样本。
- 顶层完成矩阵必须区分 `core_success` 与 typed improvement policy/status：disabled/optional/required、
  返回失败/抛异常、standard/fast path 都要覆盖；optional 失败必须保留 warning 和 terminal evidence，
  required 失败只能影响 overall success，不能把已完成核心 TaskResult 改写为失败。
- memory source adapter 测试必须验证每个来源条目都有 candidate/source ID 与决策证据、
  对话选择保持连续 recent suffix、兼容 section 由 candidate decision 派生，并且 adapter
  版本进入 replay request hash，避免读取 legacy section snapshot。
- source governance 测试只允许对 normalized exact duplicate、typed stale 和显式
  `conflict_key` 建立确定性断言；required stale/conflict 必须在 provider 前阻断并保留独立
  governance 原因，不能用相似文本或 embedding 构造“推测性冲突”测试。
- dialog compaction 测试必须覆盖 source fingerprint/IDs、`compacted` decision link、
  新旧 algorithm round-trip、compactor/source 原子回退、信号行保留与长 observation
  masking、最近 suffix 原文、artifact corruption fail-closed；
  required 禁止压缩、至少两条 recent 原文、summary fit 失败/无 artifact sink 时原结果回退、
  snapshot round trip、artifact corruption fail closed 和 exact prompt replay 不重新生成。
- context quality 测试必须使用显式 expected selected/omitted IDs 和 typed structural issue，
  不得用 LLM judge 冒充确定性验收；fixture corpus 至少覆盖 budget、conflict、duplicate、
  compaction，静态 inventory 必须保持 production legacy assembler/compressor caller 为 0。

新增测试前先问：
- 这个测试要验证什么真实问题？
- 它属于哪一层指标？
- 能否拆成更小的子目标？
- 失败时能否定位到具体环节？
- 是否能离线稳定运行？
- 是否能避免依赖随机 LLM 输出？
- 是否覆盖当前实现中最脆弱的边界？
- 对多步持久化协议，是否逐个测试了每个 durable boundary 前后的中断？
- exactly-once 结论是否同时断言 session 调用次数、artifact 数、终结事件数和最终 checkpoint？

项目环境门禁还必须覆盖：preflight 零副作用；setup 拒绝时不创建目录、venv、Git、preset 或 memory；ready
attach 不触发 install/network；写后依赖漂移只触发一次受控 resync；standard、enhanced 和 resume 都禁止
host Python fallback；验证同时断言 requested argv 与 effective interpreter/environment identity；恢复缓存为空、
环境漂移和 legacy pending Python verification 均 fail closed。

Reasoning policy 测试必须分开验证 caller intent、capability resolution、
provider transport 和 recovery identity。未知 endpoint 使用保守 profile；显式 profile
override 必须在配置边界完成 typed validation；endpoint identity 要覆盖 credential/query
剥离、默认端口归一化和非默认端口区分。固定轨迹实验先通过权限、精确命令、完成证据和
system quality，再比较 visible output、reasoning usage、总 Token、失败率与耗时；缺失的
reasoning usage 保持 unknown。单个 paired run 只能作为 mechanism pilot，不能宣称统计因果。

可选 project improvement 的失败路径必须真实执行“写入后失败”，并断言显式 changed files
从 pre-iteration snapshot 恢复、测试文件不被扩大范围修改、不会继续修复 stale failed state，
以及 rollback 失败仍使 enhancement 可见地失败。

Fast/module-owned tool evidence 测试必须使用真实 `DiagnosticRecorder`，断言每个
logical invocation 恰好一个 `tool_called` 和一个 terminal event；内部 retry 只进入
`retry_history`。同一 task/step 的重复调用必须有不同 call ID。started/terminal
diagnostics hook 抛错不得阻止执行、改变结果或诱发业务动作重试。注意：事件齐全只证明
可观测性。Fast mutation 还必须分别覆盖 root/task 写权限、真实 RuntimeController 的 mutation
分类、EditGuard、prepare/observe/replay、原样 validation command、目标 hash/diff 与 edit budget；
fake callback 只能测试调用顺序，不能证明真实 checkpoint 生效。README 与 bugfix 也必须进入
同一共享 target contract。成功但无目标 diff 必须在 observe 前转换为失败证据。

## 7.1 提案输出格式
每次新增测试前，建议按以下格式沟通：
- 测试对象
- 相关文档
- 测试设计
- 预期发现的问题
- 修复方案
- 是否需要同步补回归测试

---

## 8. 反模式

- 只测返回值，不测副作用
- 只测成功路径，不测失败路径
- 只测最终分数，不测轨迹
- 用模糊断言代替明确验收
- 依赖真实网络和随机模型输出
- 为未来可能需求提前写大而全测试

---

## 9. 与项目文档的关系
- 测试策略、任务评分方式、工具边界、权限边界变化时，检查 `AGENTS.md`、`API.md`、`README.md`、`Code/README.md`。
- Codex 迭代规则变化时，检查 `AGENT_LOOP_PROTOCOL.md`。
- 外部 loop 的调度、重试、断点续跑和失败分类变化时，检查 `AGENT_LOOP_SUPERVISOR.md`。
- 外部 loop 的验收标准与终止条件变化时，检查 `AGENT_LOOP_GOAL.md`。

---

## 10. 与外部 loop 的协同
- 测试设计只负责定义“什么算通过/失败/回归”。
- 外部 loop 负责保存状态、重试 transient failure、恢复 checkpoint、继续下一轮。
- Codex 负责在单轮内按测试驱动流程完成最小修补。
- 不要把 loop 协调逻辑写成纯 prompt 约束；应由宿主侧执行器实现。
