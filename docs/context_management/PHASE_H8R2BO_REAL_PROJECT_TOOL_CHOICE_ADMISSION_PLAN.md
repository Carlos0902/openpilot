# Phase H8-R2BO：Real-project mutation tool-choice admission replay 计划

## 背景

H8-R2BN 在 3 个 isolated mutation fixtures 上通过了 DeepSeek provider-tool matrix，并暴露/修复了
一个 shared runtime contract gap：仅发送 `tools` 不足以保证 provider 进入 native tool-call
phase；`LLMRequest.tool_choice=required` 必须作为 typed surface 进入 transport，而 scoped writer
和 exact validation 完成后的 finalization 必须不再暴露 tools/tool choice。

BN 仍不是 real-project mutation rollout。旧 H8 real-project shadow 的第一臂曾在 mutation 前停止，
主要原因是文件窗口/定位证据不足；后续 H8-R2 线路已经陆续修复窗口、artifact handoff、post-mutation
continuation、validation/suspicious-success 等问题。现在需要把 BN 的 tool-choice/finalization 合同
接回真实项目 mutation shadow 的准入层，先用 disposable replay gate 验证“真实项目任务定义 + 生产
provider-tool entry”不会再次退化成普通文本、错误 finalization 或越权写入。

## 目标

1. 新增 H8-R2BO replay runner 与 focused tests。
2. 使用真实项目任务形状，而不是 toy calculator semantics：
   - target read/write file: `Code/tests/test_provider_tool_roundtrip.py`;
   - supporting read files: `Code/src/core/provider_tool_roundtrip.py`,
     `Code/src/tools/file_reader.py`;
   - exact validation command:
     `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_provider_tool_roundtrip.py`.
3. 在 temporary source-isolated workspace 中执行，不修改当前 checkout。
4. 通过生产 `ToolPlanningTaskExecutor.execute_provider_tool_task(...)` entry 和 provider-tool
   admission/runtime 语义。
5. 用 deterministic mock provider 驱动完整 tool loop：
   - first request must carry `tool_choice=required`;
   - writer/validation requests must remain required while tools are expected;
   - after exact validation, finalization request must expose no tools and no `tool_choice`;
   - finalization must not be allowed to call tools.
6. 记录 body-free receipt：只保存 hashes、tool names、finish reasons、usage counters、scope/validation
   booleans 和 request-shape diagnostics，不保存 prompt/source/response/patch/stdout/stderr/credential。

## 非目标

- 不发起真实 provider request。
- 不修改当前仓库源码。
- 不提交。
- 不启用 production prompt-use 或 default-on Compact。
- 不证明真实 provider 质量/token 收益。
- 不重跑旧 H8 失败 arm，也不通过提高预算掩盖窗口问题。
- 不把 dirty tree 或 run artifacts 当作 accepted commit。

## Metadata impact note

Fact:
BN 的 typed `tool_choice`/finalization contract must hold when the task has a real-project
scope, validation command, mutation budget, and production provider-tool entry.

Authoritative producers:
`ToolPlanningTaskExecutor` and `ProviderToolRoundTripRunner` produce request diagnostics,
tool execution evidence, mutation gates and finalization outcome. The BO harness produces
only a body-free replay receipt.

Consumers:
The next decision about whether to run a credentialed real-project raw/Compact provider arm.

Lifecycle:
Experiment-only replay. The source-isolated workspace is temporary. Receipt stores no bodies and
does not become a production memory artifact.

Control impact:
No new production contract is introduced. Existing `LLMRequest.tool_choice`,
`ProviderToolRoundTripRunner` finalization state, mutation opt-in, scope, and validation gates are
reused. Any future real provider arm still needs its own plan and explicit gates.

Existing contracts reviewed:
`LLMRequest`, context request builder, `ProviderToolRoundTripRunner`,
`ToolPlanningTaskExecutor.execute_provider_tool_task`, `Task.read_files`, `Task.write_files`,
`Task.validation_command`, `ProviderToolExecutionBudgetProfile.REAL_MUTATION`,
`file_reader`, `file_patch_writer`, and `command_executor`.

Decision:
Add an experiment harness and tests. Do not add metadata fields or change production runtime behavior
unless the replay exposes a contract failure that cannot be represented by existing typed fields.

Why no duplicate source of truth is created:
The task spec owns only this replay's frozen scope. The receipt records request-shape and gate evidence;
source code and validation semantics remain authoritative in the temporary workspace.

Tests:
Mock success replay, finalization tool-call rejection, receipt body-free scan, and scoped mutation gate.

Documentation updates:
H8-R2BO result doc, evidence index, completion audit and implementation log after execution.

## 实施计划

1. Add `stage_h8r2bo_real_project_tool_choice_admission.py`.
2. Add `test_stage_h8r2bo_real_project_tool_choice_admission.py`.
3. Reuse BN/BM body-free receipt validators and provider-tool execution helpers where practical.
4. Run focused BO tests.
5. Run adjacent provider round-trip/tool-choice regression.
6. Run the replay runner locally with mock provider in a temporary workspace.
7. If passed, write result docs and update evidence tables; if failed, record the typed stop and do not
   claim readiness for a real provider arm.

## 通过标准

- replay status `passed`;
- all non-finalization provider requests with exposed tools have `tool_choice=required`;
- finalization request has no tools and no `tool_choice`;
- tool-call finalization is rejected in tests;
- exactly the temporary `Code/tests/test_provider_tool_roundtrip.py` copy changes;
- exact validation command is observed and exits zero;
- independent exact validation exits zero;
- no fallback, retry, suspicious success, credential, prompt/source/response/patch/stdout/stderr body;
- focused and adjacent gates pass.

## 阻塞/降级语义

- If current dirty source prevents building an isolated workspace, stop as `typed_blocked` with zero
  provider transport/mutation claim.
- If the mock provider can bypass tool-choice or finalization, record `needs_followup` and fix the shared
  contract before any real provider arm.
- If exact validation cannot run in the disposable workspace, record `needs_followup`; do not mark the
  replay as a pass based on writer evidence alone.
