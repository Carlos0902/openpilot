# mini-SWE 核心收益筛查：冻结前就绪性审计 V1

## 1. 状态

- 审计日期：2026-08-01
- 范围：仅限 `MINI_SWE_CORE_BENEFIT_SCREEN_PROTOCOL.md` 的准备与冻结阶段
- DeepSeek review-plane 已完成；task-arm outcome 未运行
- 结论：**尚未执行就绪；必须 fail closed**

本审计不评估 agent 成功率，也不读取或复述任何 private rationale、gold/test patch 或 hidden
test identity。

## 2. 已核对的公开 V4 证据

| 项目 | 结果 |
|---|---:|
| execution-preflight receipt | 31 |
| image-acquisition receipt | 31（对应技术候选链） |
| nonexecution-evidence receipt | 31 |
| 一审 redacted decision | 31 |
| 私有一审 rationale 文件 | 31（仅核对文件存在与数量） |
| DeepSeek 二审 public decision | 31 |
| 双审直接一致 | 18 |
| 双审分歧、待 outcome-blind adjudication | 13 |
| 已完成独立 mechanism review 的直接一致候选 | 12 |
| 可进入核心筛查 eligible pool 的任务 | 12 |
| 12-task Stage A manifest | 1 |

pool 与 manifest 均保持 `agent_task_outcomes_generated=false`、
`provider_execution_authorized=false`、`production_execution_authorized=false`；它们冻结候选身份，
不授权任何 ordinary 或 active task arm。

审计绑定：

- V4 acquisition rules SHA-256：`834e6f776798870d65de462ef89a2c5d804f002d456ffd6034723a484d5ecdcc`
- V6 host preflight SHA-256：`4a72c2488582a066fef1b47b6159df81ad4dc2fde678b3c17561c52a0e90946c`
- 核心筛查协议 SHA-256：`d6843ebb80c45fbb30dffc91766242e2e47e14d242f83ddaf1e39c98b751b2c6`

## 3. Air outcome-free 资源预检

[AIR_SERIAL_PREFLIGHT_V2.json](../../experiments/mini_swe_active_iteration/core_benefit_screen_v1/AIR_SERIAL_PREFLIGHT_V2.json)
绑定 V6 的 Air runtime：Docker daemon 与 smoke 通过、约 180 GiB 可用存储、Docker 内存约 4 GiB、
物理内存 8 GiB。

它授权的仅是**串行容量假设**：`maximum_parallel_task_pairs=1`。任何大于 1 的并行仍未授权，
原因是尚未有 outcome-free parallel pressure preflight；该审计不把旧的候选执行或 agent outcome
当作并发证据。

## 4. 已实现的 fail-closed 准备工具

- `core_benefit_screen.py`：只接受两份 reviewer identity 不同、对同一 stratum 一致且 blind to
  future arm outcomes 的 review，才能组成 pool；分歧必须先 adjudicate。
- `review_provider.py` 与 `run_core_benefit_screen_review_provider.py`：按
  `CORE_BENEFIT_SCREEN_REVIEW_PROVIDER_PROTOCOL_V1.json` 为每个候选发起一次独立的
  无状态 DeepSeek 二审。请求仅含 `instance_id`、问题描述、gold patch 和 test patch；一审、
  task-arm trajectory/outcome、hidden evaluator 结果与 FAIL/PASS selector 均不允许进入请求。
  原始请求和回答仅写入 private review directory；public repo 只写 redacted decision 与哈希收据。
- `generate_core_benefit_screen.py`：把 V4 的 image / execution / nonexecution / review receipt
  逐项绑定，并且只接受有独立 mechanism review 的 resolved candidate；对于分歧还要求独立
  adjudication receipt，随后生成 redacted pool 及 12-task Stage A 草案。
- 对生成的 manifest 固定 SHA-256 排序、每仓库最多 4 个任务、12-task 分割与所有 execution-authorization
  字段为 false。
- `swebench_agent_environment.py`、`swebench_task_runner.py` 与
  `swebench_network_runner.py`：实现并离线验证了“固定实例镜像 → 无网络、无 host mount 的 agent
  容器 → 仅导出 agent patch → 无网络官方 evaluator”的桥接边界。public evaluator receipt 仅保存
  model patch 的 SHA-256；evaluator callback 不接收 arm 标签。

除二审 review-plane API 外，这些工具不执行 Docker evaluator、ordinary 或 active_iteration arm。

## 5. 剩余冻结门

DeepSeek V4 Flash 的 31 个无状态二审均已形成 public decision 与 hash receipt；二审 reviewer identity
与一审不同，原始输入/输出和 rationale 均留在 `openpilot-private`，public repo 仅写入 redacted decision
和 hash。

私有 reviewer handoff 位于
`openpilot-private/mini_swe_acquisition/v4/reviews/reviewer-independent-v1/README.md`；它明确要求
二审者不读取一审决定或任何 future arm outcome。

二审不得参考 future agent-arm outcome；对于同一任务的一审/二审 stratum 不一致，必须单独作
outcome-blind adjudication，不能静默选择更适合 active 的标签。

本次快速筛查将每仓库上限固定为 4；现有 18 个直接一致 review 可在四个 repository 内组成
12 个 Stage A 位置，因此不再需要为补样本裁决分歧。分歧仍保留且不得覆写；若以后进入候选池，
必须通过独立、outcome-blind adjudication。12 个入选候选均已完成独立 mechanism review，且全部仅标记
`post_action_validation=true`（`diagnostic_measurement=false`、`recovery_or_safe_stop=false`）。因此当前
manifest 的结论范围明确限于**动作后验证密集型**任务，不能外推为诊断或恢复密集型任务的收益。

[`SCREEN_EXECUTION_PROTOCOL_V1.json`](../../experiments/mini_swe_active_iteration/core_benefit_screen_v1/SCREEN_EXECUTION_PROTOCOL_V1.json)
已冻结真实 task-arm provider、共同预算、6/6 arm 顺序、失败政策和当前 runner/evaluator 指纹，且已通过
manifest 绑定测试。它仍明确保持 `task_arm_provider_execution_authorized=false`：首个固定 digest 实例镜像
必须完成无模型调用的 Docker bridge smoke（启动、命令边界、patch 导出、cleanup）后，才可另行解锁 task arm。
