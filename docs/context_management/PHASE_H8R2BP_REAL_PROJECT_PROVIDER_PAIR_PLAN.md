# Phase H8-R2BP：Real-project provider raw/Compact mutation pair 计划

## 背景

H8-R2BO 已经在 source-isolated replay 中证明：真实项目形状的 mutation task 可以通过生产
`ToolPlanningTaskExecutor.execute_provider_tool_task(...)` entry 维持 BN 修复后的
`tool_choice=required` / no-tools finalization 合同，并通过 scoped writer、exact validation、
independent validation 和 suspicious-success gates。

BO 仍是 deterministic mock provider。下一步需要一个最小真实 provider paired arm，确认同一
real-project task shape 在 DeepSeek provider 下不会退化成：

- 普通文本回答而不是 tool calls；
- writer/validation/finalization 顺序错误；
- out-of-scope write；
- suspicious success；
- Compact 投影破坏 required constraints 或工具回路。

## 目标

1. 新增 H8-R2BP runner 和 focused tests。
2. 使用 BO 同一个真实项目任务形状：
   - read files:
     - `Code/tests/test_provider_tool_roundtrip.py`
     - `Code/src/core/provider_tool_roundtrip.py`
     - `Code/src/tools/file_reader.py`
   - write files:
     - `Code/tests/test_provider_tool_roundtrip.py`
   - exact validation:
     `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_provider_tool_roundtrip.py`
3. 每个 arm 都在独立 temporary source-isolated workspace 中运行，当前 checkout 不被修改。
4. 跑一个 raw/Compact pair：
   - raw arm：使用 raw historical candidates；
   - Compact arm：使用 discovered binding + preflight + simulation 后的 reusable candidates；
   - 其余 provider/model/tool schema/reasoning/budget/task/scope/validation 完全一致。
5. 每个 arm 必须通过：
   - native tool calls；
   - `tool_choice=required` tool phase；
   - no-tools/no-tool-choice finalization；
   - scoped writer；
   - exact provider validation；
   - independent exact validation；
   - no fallback/retry/suspicious success；
   - body-free receipt。
6. 若 provider credential 缺失，记录 typed-blocked，零 transport，零 mutation claim。

## 非目标

- 不修改当前 checkout。
- 不提交。
- 不开启 production prompt-use。
- 不默认启用 Compact。
- 不做 OpenAI/cross-provider claim。
- 不把一个小 pair 外推为全局收益或调用次数收益。
- 不保存 prompt/source/response/patch/stdout/stderr/credential bodies。
- 不把 run artifacts 纳入 accepted commit。

## Metadata impact note

Fact:
A DeepSeek credentialed provider can or cannot complete the BO real-project task shape under raw and
Compact projections while preserving typed tool-choice, permission and validation contracts.

Authoritative producers:
`MemoryContextBuilder` produces raw/compact candidate sets and compaction binding. Reusable preflight
and simulation produce harness-explicit reusable candidates. `ToolPlanningTaskExecutor` /
`ProviderToolRoundTripRunner` produce tool execution evidence, request diagnostics, budget diagnostics,
mutation gates and finalization outcome. The BP harness produces only body-free aggregate evidence.

Consumers:
The next decision about expanding to a real-project mutation matrix or stopping to repair provider
tool/task routing.

Lifecycle:
Experiment-only. Each arm uses a temporary disposable workspace. Receipt stores hashes, usage,
finish reasons, tool names, request shapes, source-scope booleans and validation booleans only.

Control impact:
No new metadata field is planned. Existing `LLMRequest.tool_choice`, mutation opt-in,
`real_mutation` budget, initial-context projection, reusable compaction preflight/simulation and
provider-tool admission are reused. Any required production contract change must be separately
diagnosed and tested.

Existing contracts reviewed:
`LLMRequest`, request builder, `ProviderToolRoundTripRunner`, `ToolPlanningTaskExecutor`,
`Task.read_files`, `Task.write_files`, `Task.validation_command`,
`ProviderToolExecutionBudgetProfile.REAL_MUTATION`, `ContextCandidate`,
`ContextCompactionBinding`, reusable preflight/simulation, `file_reader`,
`file_patch_writer`, and `command_executor`.

Decision:
Add an experiment harness/tests only. Do not change production runtime unless the real arm reveals a
contract failure.

Why no duplicate source of truth is created:
The task spec owns only this experiment's frozen scope. Source files and exact pytest remain
authoritative inside each disposable workspace. Receipt records body-free proof and hashes.

Tests:
Mock pair pass, missing credential typed block, finalization/tool-choice request-shape checks,
receipt body-free scan and scope/validation gates.

Documentation updates:
H8-R2BP result doc, evidence index, completion audit and implementation log.

## 实施计划

1. Add `stage_h8r2bp_real_project_provider_pair.py`.
2. Add `test_stage_h8r2bp_real_project_provider_pair.py`.
3. Reuse BO workspace/task constants and BN/BM reusable projection helpers where practical.
4. Run mock focused tests.
5. Run adjacent provider-tool/tool-choice/BN/BM/BO gates.
6. If credentialed settings are available, run one real DeepSeek raw/Compact pair.
7. Store body-free aggregate receipt and update docs.

## 通过标准

- If credentialed:
  - status `passed`;
  - 2/2 arms pass mutation gate;
  - every tool-phase request has `tool_choice=required`;
  - every finalization request has no tools and no `tool_choice`;
  - exact validation and independent exact validation pass in both arms;
  - only temporary `Code/tests/test_provider_tool_roundtrip.py` changes;
  - provider usage/finish evidence complete;
  - reusable prompt/total tokens are lower than raw, or if not lower the stage records a safety pass
    without benefit claim.
- If credentials are missing:
  - status `blocked`;
  - provider calls 0;
  - project mutations 0;
  - no benefit claim.

## Stop rules

Stop on:

- missing provider credential;
- unsupported provider/tool response shape;
- no native tool call where one is required;
- finalization tool call;
- out-of-scope write;
- exact validation not observed;
- independent validation failure;
- fallback/retry that changes the requested action;
- unknown usage;
- suspicious success;
- any receipt body/secret hit.
