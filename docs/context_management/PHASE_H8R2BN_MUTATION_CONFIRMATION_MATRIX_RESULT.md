# Phase H8-R2BN：Isolated mutation/tool-task confirmation matrix 结果

## 状态

PASS。

Official body-free receipt:

- `experiments/full_architecture_context_observation/runs/phase_h8r2bn_mutation_confirmation_matrix_20260810_v3/aggregate/receipt.json`
- Canonical hash: `sha256:06bc71d3fc740c59a3a36aad7bda9c9387001cca605e282c41da27330ac8e003`

## 本阶段验证的问题

H8-R2BM 只覆盖了一个 isolated calculator mutation fixture。H8-R2BN 将同一
discovered-binding / preflight / simulation / production provider-tool execution chain 扩展为
3 个临时单文件 mutation case：

- `divide_zero_value_error`
- `clamp_bounds`
- `safe_get_default`

每个 case 都运行 raw 与 reusable 两臂，并要求：

- 通过生产 `ToolPlanningTaskExecutor.execute_provider_tool_task(...)` 入口；
- 使用真实 DeepSeek provider transport；
- 只暴露 `file_reader`、`file_patch_writer`、`command_executor`；
- 仅修改临时 fixture 的 `calculator.py`；
- 通过 provider 内 exact validation；
- 通过独立 exact validation；
- 无 fallback、无 suspicious success、无 out-of-scope source change；
- receipt 不保存 prompt/source/summary/response bodies、patch body、stdout/stderr 或
  credential。

## 运行中暴露并修复的原因

v1/v2 没有形成可靠工具回路：请求里虽然包含 `tools`，但多数 arm 没有进入 native
tool calls，raw arm 还出现 `finish_reason=length`。根因不是 Compact 本身，而是 provider
tool request 缺少 typed `tool_choice` surface：

- `LLMRequest` 没有 `tool_choice` 字段；
- request builder 传入的 extra `tool_choice` 被 Pydantic 忽略；
- provider transport 实际只发送 `tools`，模型可以继续输出普通文本。

本阶段修复为：

- `LLMRequest.tool_choice` 支持 provider-neutral `auto` / `none` / `required`；
- cache key、OpenAI-compatible payload、request diagnostics 与 context request builder 都传递
  `tool_choice`；
- `ProviderToolRoundTripRunner` 在等待非 finalization 工具动作时发送
  `tool_choice=required`；
- scoped writer + exact validation 成功后切入 finalization：不暴露 tools，也不发送
  `tool_choice`，避免强制模型继续工具调用。

## Provider 结果摘要

| Case | Raw total | Reusable total | Total delta | Raw prompt | Reusable prompt | Prompt delta |
|---|---:|---:|---:|---:|---:|---:|
| `divide_zero_value_error` | 13,412 | 5,082 | 8,330 | 12,957 | 4,737 | 8,220 |
| `clamp_bounds` | 15,667 | 5,142 | 10,525 | 15,162 | 4,787 | 10,375 |
| `safe_get_default` | 13,554 | 5,027 | 8,527 | 13,169 | 4,699 | 8,470 |
| **Aggregate** | **42,633** | **15,251** | **27,382** | **41,288** | **14,223** | **27,065** |

Aggregate completion tokens also fell from `1,345` to `1,028` (`-317`).

All 6 arms finished with the same round shape:

```text
tool_calls → tool_calls → tool_calls → stop
```

All 6 first provider requests carried:

```text
tool_choice=required
tools=[file_reader, file_patch_writer, command_executor]
```

Side-effect summary:

- provider transport attempted: true
- provider calls: 24
- mock completion calls: 0
- project mutations: 6, each limited to the temporary target source file
- memory mutations: 0
- writer actions: 6
- command actions: 6
- verification runs: 6
- retry count: 0
- fallback count: 0
- `ContextCompactionReuseAdmission.used_in_prompt`: false

## Gates

- BN focused tests:
  `PYTHONPATH=Code/src:. python -m pytest -q experiments/full_architecture_context_observation/test_stage_h8r2bn_mutation_confirmation_matrix.py`
  → **5 passed**
- Adjacent compaction/provider-tool focused gate:
  selected compaction reuse, memory context, execution planning, BK/BL/BM/BN tests
  → **147 passed**
- Stronger provider round-trip regression:
  `Code/tests/test_deepseek_tool_roundtrip.py Code/tests/test_provider_tool_roundtrip.py`
  → **122 passed**
- `compileall` over touched code/tests: passed
- `git diff --check` over touched tracked files: passed
- v3 receipt scan: 1 JSON scanned, 0 secret hits, 0 forbidden body keys

## Claim boundary

Accepted for this stage:

- DeepSeek provider-native tool round-trip can be forced into the intended tool phase with typed
  `tool_choice=required`.
- Finalization must omit tools/tool choice after the scoped mutation and exact validation evidence
  are complete.
- The reusable projection chain produced lower prompt and total tokens across 3 isolated mutation
  fixtures while preserving scoped writer, exact validation, independent validation and
  suspicious-success gates.

Still experimental / not accepted:

- no production prompt-use;
- no default-on Compact;
- no real-project mutation rollout;
- no OpenAI/cross-provider claim;
- no semantic equivalence beyond these deterministic fixture gates;
- no claim that dirty-tree runs or receipt artifacts should be committed;
- no accepted commit until an independent review/acceptance pass consolidates the shared contract
  changes.
