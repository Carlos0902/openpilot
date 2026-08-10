# Phase H8-R2BP：Real-project provider raw/Compact mutation pair 结果

## 状态

PASS。

Official body-free real-provider receipt:

- `experiments/full_architecture_context_observation/runs/phase_h8r2bp_real_project_provider_pair_20260810_v2/aggregate/receipt.json`
- Canonical hash: `sha256:da4c0e0db7697fe4b1b3bc3774fd120e0fedfddd4eefca8d3635f9368d582fe0`

Mock preflight receipt:

- `experiments/full_architecture_context_observation/runs/phase_h8r2bp_real_project_provider_pair_mock_20260810_v1/aggregate/receipt.json`
- Canonical hash: `sha256:1ddb3233aff901bcd21f3570c76d96b4f980c44e20b66830117615993068884b`

## 本阶段验证的问题

H8-R2BO 证明真实项目形状的 mutation task 可以在 mock provider 下维持
`tool_choice=required` / no-tools finalization 合同。H8-R2BP 在同一任务形状上运行一个真实
DeepSeek raw/Compact provider pair：

- 每个 arm 都使用独立 temporary source-isolated workspace；
- 当前 checkout 不被修改；
- 生产 provider-tool entry 被使用；
- raw 与 Compact 只改变历史上下文投影；
- task、scope、provider/model、reasoning、tool schema、validation command 完全一致。

## Frozen task

- Read files:
  - `Code/tests/test_provider_tool_roundtrip.py`
  - `Code/src/core/provider_tool_roundtrip.py`
  - `Code/src/tools/file_reader.py`
- Write files:
  - `Code/tests/test_provider_tool_roundtrip.py`
- Exact validation:
  - `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_provider_tool_roundtrip.py`
- Tool allowlist:
  - `file_reader`
  - `file_patch_writer`
  - `command_executor`

## v1 诊断与修复

The first real run was rejected by DeepSeek before any tool execution because provider-default
thinking mode does not support `tool_choice=required`. That run is excluded from evidence and was
moved to local quarantine because its receipt contained raw provider error text.

The root cause was task-complexity routing: the frozen real-project task has three read files, so
the generic implementation-task heuristic did not classify it as routine. For this experiment only,
the BP runner uses a narrowed executor that returns `ReasoningDecisionComplexity.ROUTINE`, preserving
the existing settings-level disabled reasoning lane already proven in BN. No production reasoning
policy was changed.

## v2 result

Aggregate usage:

| Arm | Prompt | Completion | Total |
|---|---:|---:|---:|
| raw | 19,673 | 418 | 20,091 |
| Compact/reusable | 11,127 | 465 | 11,592 |
| delta | 8,546 | -47 | 8,499 |

Observed effect:

- prompt tokens fell by 8,546;
- total tokens fell by 8,499;
- completion tokens increased by 47;
- provider calls stayed 4/4 per arm.

Both arms:

- completed `tool_calls → tool_calls → tool_calls → stop`;
- used disabled reasoning;
- used `tool_choice=required` for all three tool-phase requests;
- omitted tools and `tool_choice` in finalization;
- changed only temporary `Code/tests/test_provider_tool_roundtrip.py`;
- observed scoped writer evidence;
- observed exact provider validation;
- passed independent exact validation;
- had complete provider usage/finish evidence;
- had no fallback, retry or suspicious success.

Side effects:

- provider transport attempted: true
- provider calls: 8
- network side effects: 8
- project mutations: 2 temporary workspace mutations
- memory mutations: 0
- writer actions: 2
- command actions: 2
- verification runs: 2
- retry count: 0
- fallback count: 0
- `used_in_prompt`: false

## Gates

- BP focused:
  `PYTHONPATH=Code/src:. python -m pytest -q experiments/full_architecture_context_observation/test_stage_h8r2bp_real_project_provider_pair.py`
  → **5 passed**
- Adjacent provider-tool/tool-choice regression:
  BP + BO + BN + BM + `Code/tests/test_deepseek_tool_roundtrip.py` +
  `Code/tests/test_provider_tool_roundtrip.py`
  → **140 passed**
- `compileall` over BP runner/test: passed
- `git diff --check` over BP files: passed
- Mock and real receipts body/secret scan: 0 secret hits, 0 forbidden body keys

## Claim boundary

Accepted for this stage:

- A real DeepSeek provider can complete the BO real-project mutation task shape under raw and
  Compact projections when the experiment lane is routed to disabled reasoning.
- The BN `tool_choice=required` and finalization contract holds under real provider transport for
  this task shape.
- Compact/reusable projection reduced prompt and total tokens for this one real-project-shaped
  mutation pair while preserving scope, exact validation and suspicious-success gates.

Still experimental / not accepted:

- no production reasoning policy change;
- no production prompt-use;
- no default-on Compact;
- no OpenAI/cross-provider claim;
- no call-count benefit;
- no larger real-project mutation matrix yet;
- no accepted commit until independent review consolidates the shared contract and excludes run
  artifacts, secrets, quarantine and dirty-tree data.
