# Stage 6：paired canary 分析与全会话边界审计

## 结论

当前有效样本支持一个**机制信号**：在同一 source snapshot、同一 Task
Designer schema、Provider-default reasoning 和既有 2,200 completion ceiling
下，compact 臂减少了输入和总 usage，且没有观察到质量或权限回归。但样本只有
一个 compact/current pair，不能外推到完整会话、其他任务类型或其他 Provider。

有效 paired 结果：

| 指标 | compact | current | 变化 |
|---|---:|---:|---:|
| rendered input | 1,258 | 1,719 | -461 (-26.8%) |
| Provider input | 1,361 | 1,822 | -461 (-25.3%) |
| Provider output | 1,866 | 2,069 | -203 (-9.8%) |
| reasoning tokens | 1,674 | 1,856 | -182 (-9.8%) |
| total tokens | 3,227 | 3,891 | -664 (-17.1%) |
| Provider calls | 1 | 1 | 0 |

两臂均返回合法 Task JSON，目标均为授权的 `calculator.py`，约束召回通过，
`finish_reason=stop`，无项目 mutation。完整实验记录见
`STAGE5B_3C_TASK_DESIGNER_PROVIDER_CANARY_RESULT_V1.md`。

## 失败尝试与解释

早期诊断尝试不计入 paired 样本，但保留为边界证据：

- runner 传入 `max_retries=0` 时，当前 `LLMClient` 实际执行零次 Provider
  attempt；这不是“执行一次且不重试”。
- 512 completion reserve 导致 Provider 以 `length` 结束并产生空响应，说明
  预算不足会制造执行语义问题，而不只是影响成本。
- 已知 usage 的截断/JSON 无效与 unknown usage 必须区分。前者可以记录一次有界
  的当前臂恢复，后者必须停止；不能把失败响应整段重生成，也不能把 unknown
  usage 记成 0。
- campaign state 必须跨进程持久化；当前采用原子替换，但尚未提供并发锁，后续
  多 worker canary 仍需补齐。

## 全会话生产链路审计

当前 `SessionIngressState` 已进入 `execute → RuntimeController → checkpoint`
并用于约束投影，但还不是完整 raw-dialog 上下文：

1. `IntelligentAutopilot` 每次执行都会创建新的 `MemoryContextBuilder`/
   `ShortMemory`，没有从 ingress turns hydrate；`ContextLoaderAgent` 只接收
   `SessionConstraintState`。
2. `AutonomousIterationAgent._load_context` 的 `memory_context` 目前只写入
   `project_state` 并发进度事件；Goal/Task project-improvement candidate
   builder 不消费其中的 `dialog_context`/`prompt_text`。因此即使 hydrate，
   也不能宣称 raw dialog 已影响模型决策。
3. project-improvement 的 analyzer 在 Goal/Task 之前调用，当前
   `_analyze_project_improvements → project_improvement_tool` 的分析候选也没有
   接收 `SessionConstraintState`；后续 Goal/Task 的 required candidate 不能弥补
   这个前置决策缺口。
4. enhanced CLI 在 task route 分类后才接受 turn；agent-generator 路由只传任务
   字符串，可能绕过 ingress/context/constraint 链路。原始 turn 的接受应先于
   路由分类。
5. `_load_context` 捕获所有异常并返回空 context；required candidate 或预算失败
   不能被吞掉后继续宣称成功，必须进入 typed stop/fallback。
6. 若未来把 raw turns 接入 builder，`request_hash` 必须纳入 canonical ingress
   snapshot/turn-ledger digest；同约束不同对话不能复用陈旧 replay artifact。
7. `MemoryContextBuilder.build(project_path=...)` 可能通过 `ProjectManager` 更新
   `sketch.json`/file-index artifacts；因此真正走 ContextLoader 后，当前“zero
   mutation”断言并不成立，Stage 7 必须显式采用 read-only index/snapshot，或把这些
   非目标源码副作用纳入 typed evidence 与 canary policy。
8. 另发现 ingress ownership 边界：当 `SessionIngressState` 没有 active
   constraints 时，冲突的 `conversation_id`/`project_path` 可能被
   `context.setdefault` 保留并成功执行。只要 raw ingress 存在，身份不应依赖是否
   有 active entry；conversation/project（以及按生命周期定义的 run）必须无条件
   校验，冲突时 fail closed。

这些缺口意味着当前 Stage 5B-3c 是 **Task Designer request-boundary 机制样本**，
不是 full-session 或 project-improvement 质量证据。

## reasoning 独立实验边界

现有 `ReasoningPolicy`/`core/reasoning.py` 已提供 provider-neutral 控制面。业务
模块不应发 provider-specific payload，也不应从模型名称字符串猜能力。`disabled`
只有在 capability profile 明确支持并解析为 `effective=disabled` 时才是有效 treatment；
generic/未知 profile 的 omitted/provider-default 是单独的 unsupported/control strata，
不能当作关闭思考。

下一次 reasoning 实验应冻结 source、schema、completion ceiling、温度、cache、
endpoint/profile 和 compact 策略，只比较 provider-default 与**精确解析为
disabled** 的 routine route，至少三组交错 pair。质量门先于成本门：合法 JSON、授权目标、
验证命令、无 mutation、无 fallback 替代成功；同时记录 input/output/total、reasoning
tokens（未知保持 null）、finish class、截断/空 JSON、恢复与延迟。不要因为一次截断
自动升级到 high/max 或扩大整个规划预算。

## Stage 7 进入条件

在扩大真实流量前，先实现并验证：

- memory 边界的 typed raw-dialog adapter：ingress 仍是唯一事实源，只生成带 source
  identity 的 DIALOG candidate，不写 LongTerm/MemoryStore；同一 ingress 在 resume
  后保持 hash/prompt snapshot 一致；
- 从 CLI 接收 turn、路由、ContextLoader、project-improvement candidate builder
  到真实 request 的端到端传播；raw dialog-derived candidate 必须被实际消费；
- ContextLoader/预算异常 fail closed 或进入有证据的 typed fallback；agent-generator
  路由不能绕过 ingress；
- provider attempt 的输出分类与 reasoning usage 派生遥测，保留原始 usage/finish
  reason，不把未知值归零；
- 之后再做 reasoning 对照、多任务和多 Provider 扩展。

离线验证：本阶段实验与审计回归 **45 passed**，Code 全量 **947 passed**。
