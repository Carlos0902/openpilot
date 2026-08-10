# Phase H8-R2BO：Real-project mutation tool-choice admission replay 结果

## 状态

PASS。

Official body-free receipt:

- `experiments/full_architecture_context_observation/runs/phase_h8r2bo_real_project_tool_choice_admission_20260810_v1/aggregate/receipt.json`
- Canonical hash: `sha256:8464bb2b19ac70460ac9a397683d15d9abcabc9a6d46fcda8d4b2d07cae90a6d`

## 本阶段验证的问题

H8-R2BN 修复了 provider-tool `tool_choice=required` 与 finalization 合同，但仍是在 isolated
fixtures 上。H8-R2BO 将该合同接回真实项目 mutation shadow 的准入层：

- 使用真实项目任务形状；
- 使用生产 `ToolPlanningTaskExecutor.execute_provider_tool_task(...)` entry；
- 使用 temporary source-isolated workspace；
- 修改的是临时 workspace 中的 `Code/tests/test_provider_tool_roundtrip.py`；
- 不发起真实 provider request；
- 不修改当前 checkout。

## Frozen task

- Read files:
  - `Code/tests/test_provider_tool_roundtrip.py`
  - `Code/src/core/provider_tool_roundtrip.py`
  - `Code/src/tools/file_reader.py`
- Write files:
  - `Code/tests/test_provider_tool_roundtrip.py`
- Exact validation command:
  - `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_provider_tool_roundtrip.py`
- Tool allowlist:
  - `file_reader`
  - `file_patch_writer`
  - `command_executor`

The deterministic mock provider added one small sentinel pytest test in the disposable copy, then
ran the exact validation command. Receipt stores only hashes, request shapes, usage counters,
finish reasons, tool names and boolean gates; it stores no prompt/source/response/patch/stdout/
stderr/credential bodies.

## Replay result

Request-shape gate:

- request count: 4
- tool-phase request count: 3
- finalization request count: 1
- all tool-phase requests carried `tool_choice=required`: true
- finalization request exposed no tools: true
- finalization request carried no `tool_choice`: true

Mutation gate:

- target changed: true
- changed source files: `Code/tests/test_provider_tool_roundtrip.py`
- only scoped source file changed: true
- writer observed: true
- provider exact validation observed: true
- provider validation exited zero: true
- independent exact validation exited zero: true
- suspicious success: false

Side effects:

- provider transport attempted: false
- provider calls: 0
- mock completion calls: 4
- network side effects: 0
- project mutations: 1 temporary workspace mutation
- memory mutations: 0
- retry count: 0
- fallback count: 0
- `used_in_prompt`: false

## Gates

- BO focused tests:
  `PYTHONPATH=Code/src:. python -m pytest -q experiments/full_architecture_context_observation/test_stage_h8r2bo_real_project_tool_choice_admission.py`
  → **4 passed**
- Adjacent provider-tool/tool-choice regression:
  `test_stage_h8r2bo_real_project_tool_choice_admission.py`,
  `Code/tests/test_deepseek_tool_roundtrip.py`,
  `Code/tests/test_provider_tool_roundtrip.py`,
  `test_stage_h8r2bn_mutation_confirmation_matrix.py`,
  `test_stage_h8r2bm_mutation_tool_shadow.py`
  → **135 passed**
- `compileall` over BO runner/test: passed
- `git diff --check` over BO files: passed
- Receipt body/secret scan: 0 secret hits, 0 forbidden body keys

## Claim boundary

Accepted for this stage:

- The BN tool-choice/finalization contract holds for a real-project-shaped mutation task in a
  source-isolated replay.
- The production provider-tool entry can enforce scoped writer, exact validation, independent
  validation and suspicious-success gates for that task shape without provider transport.
- A finalization tool call is rejected by the focused test path rather than being accepted as success.

Still experimental / not accepted:

- no real provider behavior or token/cost claim;
- no raw/Compact real-project benefit claim;
- no production prompt-use;
- no default-on Compact;
- no OpenAI/cross-provider claim;
- no accepted commit until independent review consolidates the shared contract and excludes run
  artifacts, secrets and dirty-tree data.
