# Phase H8-R2BM：Mutation/tool-task shadow gate 计划

## 背景

H8-R2BL 已经在 3 个 DeepSeek read-only fact-contract 场景中确认：

- discovered persisted binding；
- reusable preflight；
- token-aware simulation；
- explicit harness opt-in projection；
- real provider usage/quality。

下一步需要验证上下文收益能否进入真实工具任务，而不只是只读问答。由于 mutation
风险更高，本阶段只做安全隔离 shadow gate：临时 fixture 项目、单文件 scoped patch、
exact validation，不碰仓库源码。

## 目标

1. 新增 H8-R2BM mutation/tool-task shadow harness。
2. 构造临时 calculator 项目：
   - `calculator.py`；
   - `tests/test_calculator.py`；
   - 明确任务：只修改 `calculator.py` 的 `divide`，使除零抛出 `ValueError`；
   - exact validation：`python -m pytest -q tests/test_calculator.py`。
3. 每个 arm 都通过 production provider-tool entry：
   `ToolPlanningTaskExecutor.execute_provider_tool_task(...)`。
4. 两个 arms：
   - raw arm：完整 builder raw candidates；
   - reusable arm：discovered binding + preflight + simulation 后的 explicit opt-in candidates。
5. Gate 必须同时验证：
   - provider/tool usage；
   - scoped writer evidence；
   - exact validation evidence；
   - target diff/behavior；
   - no out-of-scope writes；
   - body/secret-free receipt。

## 非目标

- 不修改真实仓库源码。
- 不启用 production prompt-use。
- 不启用 default-on。
- 不修改 public metadata contract。
- 不扩大工具权限到任意 shell/file writes。
- 不把 token 下降单独视为成功。
- 不保存 prompt/source/summary/response bodies or credentials。

## Metadata impact note

Fact:
Feature-flagged reusable projection may be offered to a real provider-tool
mutation task only after discovered admission, preflight and simulation pass.

Authoritative producer:
`MemoryContextBuilder` produces raw candidates and compact binding; checkpoint
discovery produces shadow admission; `ToolPlanningTaskExecutor` and
`ProviderToolRoundTripRunner` produce tool execution evidence.

Consumers:
H8-R2BM receipt and next benefit/rollout decision.

Lifecycle:
Experiment-only. Temporary project files are created under the experiment run
root and are not production state.

Control impact:
Mutation is limited to a throwaway fixture. Provider tool allowlist is
`file_reader`, `file_patch_writer`, `command_executor`; write scope contains
only `calculator.py`; validation command is exact and cwd-bound.

Existing contracts reviewed:
`ContextCandidate`, `ContextCompactionBinding`, reusable preflight/simulation,
`ToolPlanningTaskExecutor.execute_provider_tool_task`,
`ProviderToolRoundTripRunner`, `file_patch_writer`, `command_executor`,
`ProviderToolExecutionBudgetProfile.REAL_MUTATION`。

Decision:
Add experiment harness/tests. Reuse existing provider-tool execution path and
context assembly contracts. Do not add a production builder flag.

Why no duplicate source of truth is created:
Task scope, validation command and expected target behavior live in the local
case spec. Receipt stores hashes, IDs, usage, event summaries, diff hashes and
boolean gates only.

Tests:
Mock mutation success, body-free receipt scan, missing-settings blocked
envelope.

Documentation updates:
H8-R2BM result doc, evidence index, completion audit, implementation log.

## 实施计划

1. Add `stage_h8r2bm_mutation_tool_shadow.py`.
2. Add deterministic fixture project builder and source hashing.
3. Reuse BJ/BK/BL discovery + preflight + simulation helpers to produce raw and
   reusable initial-context candidate sets.
4. Build a narrow provider-tool runtime with file reader, patch writer and
   command executor.
5. Execute raw and reusable arms through the production executor.
6. Validate exact mutation result and exact pytest.
7. Persist body-free receipts.
8. Add focused tests and run one real DeepSeek shadow if credentials are present.

## 通过标准

- Both arms complete the scoped mutation.
- Both arms run the exact validation command successfully.
- Only the temporary `calculator.py` changes.
- `README`/unscoped files remain absent/unchanged.
- Provider usage and finish/evidence diagnostics are present.
- Reusable arm reduces prompt tokens versus raw arm.
- Receipt body/secret scan passes.

## 阻塞/降级语义

- Missing credential/tokenizer: typed blocked, zero provider calls.
- Setup/preflight/simulation failure: needs-followup, zero provider calls.
- Provider fails to call required tools or validation: needs-followup/stopped,
  not a benefit claim.
- Any out-of-scope write or suspicious success: failed and excluded from benefit
  claims.
