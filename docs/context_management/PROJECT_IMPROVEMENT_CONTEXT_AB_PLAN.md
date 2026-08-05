# Project Improvement Context A/B Plan

## 1. 实验问题

在不放松权限、精确命令证据或顶层完成语义的前提下，验证
purpose-specific candidate assembly 是否解决 `project_improvement` 阶段的结构性上下文膨胀，
并测量它对可执行性、输入/输出 Token、重试、fallback 和任务质量的影响。

本实验不使用 mini-SWE，不通过扩大全局预算制造成功，也不把上游随机分解差异计为上下文收益。

## 2. 两层证据

### 2.1 冻结边界反事实

权威来源为完整架构 run `20260803T172926Z` 的单一 trajectory `events.jsonl`：

- seq 45：context loader 产生的 `ProjectStateSnapshot`；
- seq 48：project improvement report；
- seq 51：Goal Maker 选择的 `ImprovementGoal`；
- completed iteration：0；
- prompt policy：4096 requested、128 reserved、3968 effective tokens；
- model/tokenizer：`deepseek-v4-flash` / `deepseek-official-api-tokenizer`。

A 使用实验目录中冻结的 `legacy_task_design_prompt_v1` 模板和历史 project-state 投影；B 调用当前生产
`build_iteration_task_design_candidates`。两侧使用相同输入、tokenizer、assembler 和预算。旧模板作为实验资产
锁定 hash，不在运行时执行 `git reset`、切换 worktree 或 import 历史代码。

该层回答机制问题：单块 required Prompt 是否失败、候选装配是否 ready、哪些信息被保留或省略。由于旧请求
在 provider artifact 生成前已失败，该层不声称比较旧 Task Designer 的输出质量或 provider Token。

### 2.2 固定 decomposition 的完整架构运行

完整架构 arm 只冻结观察目标之外的 task decomposition，复用 run `20260803T172926Z` 已验证的四任务形状：

1. inspect：只读 `calculator.py`、`test_calculator.py`；
2. implement：唯一写目标 `calculator.py`；
3. validate pytest：精确执行 `python -m pytest -q`；
4. validate compile：精确执行 `python -m compileall -q calculator.py`。

分解之后仍走生产 ToolPlanningExecutor、Guard、工具执行、side-effect evidence、completion evidence 和
project-improvement pipeline。禁止注入成功的 `TaskExecutionResult` 或伪造命令结果。

## 3. 阶段 1 审计结论

同源内存复现结果：

- A legacy：约 96,209 chars、29,342 exact tokens、`budget_insufficient`；
- B current：修复安全事实前为 17,926 original tokens；恢复三条权威约束后为 17,981 original tokens，
  仍在相同预算内选择 3,968 tokens 并保持 `ready`，9 kept、8 omitted、0 required omitted。

但 B 暴露质量缺陷：历史 report 没有 `prompt_context`，当前 required safety candidate 因此为空；真实三条
非回归约束位于 `project_state.validation_context.product_intent.non_regression_constraints`。实验质量门必须
先稳定暴露并修复该事实丢失，不能只以 ready 或 Token 降低判定成功。

另外两个 post-change run 不进入 A/B 样本：

- `20260803T180924Z`：task decomposition/controller 输出被 reasoning budget 打满，未形成写入；
- `20260803T181040Z`：compound validation 声明与两次独立命令 evidence 不等价，正确地被 completion guard 拒绝。

## 4. 指标与硬门

### 4.1 Instrumentation validity

- 对齐键至少包含 variant、requirement、run ordinal、purpose、improvement iteration 和 purpose ordinal；
- 三个 purpose：`project_improvement`、`iteration_goal`、`iteration_task_design`；
- assembly decision coverage 为 100%；
- assembled content tokens 与 provider input tokens 分开统计；
- reasoning usage 缺失必须为 unknown，不能记作 0；
- 若 `json_repair_attempts` 或 transport attempt 多于一次且没有完整 attempt usage，成本结论标记 invalid。

### 4.2 Hard correctness and safety

- 三个 purpose `assembly_status=ready`、required representation 100%、required 不得 partial；
- 原始 goal、schema、三条非回归约束、delivery surface 和紧凑验证证据均存在于 selected required candidates；
- inspect 无 mutation；implement 写集合是 `{calculator.py}` 的子集；测试文件 hash 不变；
- pytest 和 compileall 分别具有 argv-equivalent、exit 0 的真实 evidence；
- report/goal/task schema 完整，task target files 属于 safe targets，acceptance criteria 非空；
- optional/required 失败矩阵继续保留 `core_success` 和 typed failure evidence。

任何 hard gate 失败都拒绝该 arm；不得用更低 Token 抵消质量或权限错误。

### 4.3 Context and cost metrics

- original/final assembled tokens、chars 和 UTF-8 bytes；
- candidate keep/partial/omit，按 retention/kind/reason 聚合；
- required omission、optional omission 和 token retention ratio；
- provider input/output/reasoning/total tokens、finish reason、visible response bytes；
- logical requests、可观察 provider attempts、JSON repair、transport retry、fallback；
- pipeline 是否到达三个 target purpose、terminal status、completed improvements 和外部独立验证。

## 5. 分阶段实施

### 阶段 2：失败测试、fixture 与离线 harness

实施前测试：

- 冻结 source event/hash/tokenizer 不匹配时 fail closed；
- legacy baseline 重现 budget failure；
- current adapter 重现 ready 和 candidate decisions；
- current safety candidate 必须包含 validation-context 中的权威非回归约束；
- required safety 丢失时整个实验判失败，而不是只记录 warning。

实现范围：新增冻结 fixture、protocol、离线 A/B runner 和窄测试；对 safety 投影做最小修复，优先复用已有
ProjectState/validation facts，不新增 metadata owner。

退出闸门：离线结果可重复、hash/token count 稳定、hard quality gate 全部通过。

### 阶段 3：固定 decomposition harness

实施前测试：

- fixture decomposition 的 ID、依赖、kind、read/write scope 和独立 validation command hash 稳定；
- 只替换 task decomposer，后续生产 executor/guard/evidence 不被 stub；
- 任一上游 gate 失败标记 `upstream_invalid/censored`，不得进入 improvement 成本统计。

退出闸门：不调用 provider 的 harness 测试证明注入边界窄且不会伪造执行成功。

### 阶段 4：最小 provider 实验

先运行一个 candidate arm。只有上游 gate 通过且三个 purpose 形成完整证据，才继续 paired/campaign 运行。
同一上游阶段连续失败两次，或五次中三次 upstream invalid，立即停止 provider 重试并修 harness。

相同预算下 legacy Task Designer 在 transport 前失败，因此主张仅限“恢复可执行性/结构缩减”。若另设
legacy-full-reference，必须显式提高到能容纳约 29.4k tokens，且标记为不等预算 shadow，不能用于纯 Token 因果。

### 阶段 5：验收与文档

运行定向测试、`Code/tests`、compileall 和 diff check；更新 experiment report、context plan 和
`docs/task_trajectory/IMPLEMENTATION_LOG.md`。只有 both-success paired samples 才比较总成本；其他情况报告
到达率、结构性恢复和阻断原因。

## 6. 停止与声明边界

- 单 run 上游 gate 首次失败即保留证据并停止，不放松 Guard；
- provider safety limit 保持 20 calls、100k tokens、900 seconds；
- 单一冻结轨迹只证明该失败机制，不代表任务总体分布；
- provider 即使 temperature 0 仍可能波动，真实实验报告 median/range，不宣称统计因果；
- 动态 completion budget 不与本次 context-selection A/B 同时改变。

## 7. 执行结果（2026-08-04）

阶段 2 的冻结边界反事实稳定复现：legacy Task Designer 单块为 96,209 chars / 29,342 tokens，
`budget_insufficient`；current candidates 为 60,059 chars / 17,981 original tokens，在 3,968 effective
budget 内 `ready`。required candidates 全部完整保留，三条 validation-context 非回归约束、
`project_native`、goal、schema 和紧凑验证摘要均通过 hard gate。

阶段 3 使用 hash-locked `calculator-four-stage-decomposition-v1`，只重绑定生产 decomposer 的
`decompose`。graph、order、result assembly、executor、Guard、工具和完成证据未替换；fixture 不含任何
`TaskExecutionResult` 或工具 outcome。

阶段 4 只运行一个 optional arm：`20260803T183221Z`。上游 gate valid，四个核心任务全部完成；inspect
只读、implement 只写 `calculator.py`，测试文件 hash 不变，独立 pytest 报告 `3 passed`，compileall
通过。两个实际发送的 target requests 均 ready：`project_improvement` 为 878 → 878 tokens，
`iteration_task_design` 为 9,895 → 3,968 tokens，且 required representation 完整。

该 arm 的 `iteration_goal` 使用 report-derived `seed_action_1` 确定性路径，没有 provider request，故
target purpose coverage 为 2/3、`iteration_goal_mode=deterministic`、成本结论为 `incomplete` 且
`eligible=false`。改进任务随后在 `code_generator` transport 前因 required context 超预算失败；optional
政策正确保留 `core_success=true`、顶层 success 和 typed enhancement failure。

新版离线 analyzer 对该不可变 run 重算得到：8 logical requests，7 responded、1 failed；responded usage
22,511 tokens，失败 attempt 3,642 tokens，可观察总量 26,153 tokens，logical/reasoning usage coverage
均为 100%。原 run 的历史 `analysis.json` 保持原样；准确口径来自修复后的 analyzer 与实验结果报告。

停止条件已触发：不启动第二次 provider 抽样，不做 legacy-full-reference，也不宣称 paired Token 因果。
下一独立问题是 improvement execution 的 `code_generation` context owner；它必须先完成自己的候选拆分和
质量门，再考虑完整三-purpose/campaign 实验。
