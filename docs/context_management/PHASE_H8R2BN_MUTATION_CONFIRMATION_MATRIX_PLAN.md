# Phase H8-R2BN：Isolated mutation/tool-task confirmation matrix 计划

## 背景

H8-R2BM 已经证明 discovered binding + preflight + simulation 的 reusable projection
可以进入一个安全隔离的 mutation/tool-task shadow：

- raw/reusable 两臂都走生产 `ToolPlanningTaskExecutor.execute_provider_tool_task(...)`；
- 两臂都观察到 scoped writer；
- 两臂都运行 exact validation；
- 两臂都通过独立 exact validation；
- total tokens `17,936→7,651`。

但 BM 仍是单一 calculator/divide fixture。下一阶段需要判断这是否是稳定的 mutation
收益信号，而不是单点偶然结果。

## 目标

1. 新增 H8-R2BN isolated mutation confirmation matrix。
2. 构造 3 个临时 fixture case，每个 case 都是单文件 scoped mutation：
   - `divide_zero_value_error`：`divide` 除零抛 `ValueError`；
   - `clamp_bounds`：`clamp` 返回上下界内的值；
   - `safe_get_default`：`safe_get` 缺 key 时返回 default。
3. 每个 case 跑 raw/reusable 两臂，全部通过生产 provider-tool entry。
4. 每个 arm 必须验证：
   - provider/tool usage；
   - scoped writer evidence；
   - exact validation evidence；
   - independent exact validation；
   - only scoped source file changed；
   - no suspicious success；
   - body/secret-free receipt。
5. 聚合比较 raw/reusable provider usage，记录 prompt/total delta 与 completion inflation。

## 非目标

- 不修改仓库源码。
- 不启用 production prompt-use。
- 不启用 default-on。
- 不新增 public metadata contract。
- 不扩大工具权限。
- 不把 token 下降单独视为成功。
- 不声明 OpenAI/cross-provider。
- 不保存 prompt/source/summary/response bodies、patch body、stdout/stderr 或 credentials。

## Metadata impact note

Fact:
Feature-flagged reusable projection can be tested across several isolated mutation tasks only after
discovered admission, preflight and simulation pass.

Authoritative producer:
`MemoryContextBuilder` produces raw candidates and compact binding; checkpoint discovery produces
shadow admission; `ToolPlanningTaskExecutor` / `ProviderToolRoundTripRunner` produce tool execution
evidence; the BN harness produces aggregate matrix evidence.

Consumers:
BN result receipt and the next decision about real-project mutation shadow readiness.

Lifecycle:
Experiment-only. Each fixture project is temporary. Receipt stores hashes, IDs, usage, tool names,
event booleans and gates only.

Control impact:
Mutation remains scoped to temporary single-file fixtures. Tool allowlist remains
`file_reader`, `file_patch_writer`, `command_executor`; write scope contains only the case target
file; validation command is exact and cwd-bound.

Existing contracts reviewed:
`ContextCandidate`, `ContextCompactionBinding`, reusable preflight/simulation,
`ToolPlanningTaskExecutor.execute_provider_tool_task`, `ProviderToolRoundTripRunner`,
`file_patch_writer`, `command_executor`, and
`ProviderToolExecutionBudgetProfile.REAL_MUTATION`。

Decision:
Add experiment harness/tests and docs. Reuse the BM production execution boundary. Do not add a
production builder flag.

Why no duplicate source of truth is created:
Each case spec owns its temporary task/fixture facts. Receipt stores only body-free proof of what
happened.

Tests:
Mock matrix success, body-free receipt scan, missing-settings blocked envelope, and suspicious
validation rejection.

Documentation updates:
H8-R2BN result doc, evidence index, completion audit, implementation log.

## 实施计划

1. Add `stage_h8r2bn_mutation_confirmation_matrix.py`.
2. Reuse BM helper functions for runtime, provider-tool settings, context setup, tool-event
   summaries, usage aggregation, and receipt validation where possible.
3. Add case-specific fixtures and deterministic mock clients.
4. Run mock matrix focused tests.
5. Run adjacent compaction/provider-tool focused tests.
6. Run one real DeepSeek 3-case matrix if credentials are present.
7. Persist body-free aggregate receipt and update documentation.

## 通过标准

- 3/3 cases pass.
- 6/6 arms pass scoped mutation gate.
- Every arm runs exact validation through provider `command_executor`.
- Every arm passes independent exact validation.
- Only the temporary target file changes in each arm.
- No suspicious success.
- Provider usage and finish evidence are complete.
- Reusable aggregate prompt and total tokens are lower than raw.
- Receipt body/secret scan passes.

## 阻塞/降级语义

- Missing credential: `blocked`, zero provider calls.
- Setup/preflight/simulation failure: `needs_followup`, zero provider calls.
- Provider fails writer or exact validation: `needs_followup`, excluded from benefit claims.
- Any out-of-scope write or suspicious success: `failed`/`needs_followup`, excluded from benefit claims.
