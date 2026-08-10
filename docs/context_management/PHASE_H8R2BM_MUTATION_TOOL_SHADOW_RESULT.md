# Phase H8-R2BM：Mutation/tool-task shadow gate 结果

## 判定

**PASS FOR ISOLATED DEEPSEEK MUTATION/TOOL-TASK SHADOW；仍不授权 production prompt-use、default-on、真实项目 mutation rollout 或 cross-provider claim。**

本阶段把 H8-R2BL 的 read-only reusable projection 证据推进到一个安全隔离的工具任务：

1. 临时 calculator fixture；
2. raw/reusable 两臂都走生产入口 `ToolPlanningTaskExecutor.execute_provider_tool_task(...)`；
3. 工具 allowlist 仅为 `file_reader`、`file_patch_writer`、`command_executor`；
4. 写范围仅 `calculator.py`；
5. 精确验证命令为 `python -m pytest -q tests/test_calculator.py`；
6. receipt 不保存 prompt/source/summary/response body、stdout/stderr、patch body 或凭证。

## 实施内容

- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2bm_mutation_tool_shadow.py`
- 新增 focused tests：
  `experiments/full_architecture_context_observation/test_stage_h8r2bm_mutation_tool_shadow.py`
- 新增真实结果 receipt：
  `experiments/full_architecture_context_observation/runs/phase_h8r2bm_mutation_tool_shadow_20260810_v1/aggregate/receipt.json`

## Metadata impact

本阶段没有新增 public metadata contract。它复用已有：

- `ContextCandidate`
- `ContextCompactionBinding`
- checkpoint discovery shadow admission
- reusable prompt-use preflight/simulation
- provider-tool execution metadata

`ContextCompactionReuseAdmission.used_in_prompt` 仍为 `false`。Reusable projection 只在 BM harness 的显式 opt-in candidates 中使用，没有改变 `MemoryContextBuilder` 默认 prompt-use 权限。

## 结果

Official receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bm_mutation_tool_shadow_20260810_v1/aggregate/receipt.json`

Receipt hash：

`sha256:f3b2719a95ca6164156440d92c793e5ce60a6d79542cecce00ca711e5c00dba8`

Aggregate provider usage：

| Metric | Raw | Reusable | Delta |
|---|---:|---:|---:|
| prompt tokens | 17,433 | 7,127 | 10,306 |
| completion tokens | 503 | 524 | -21 |
| total tokens | 17,936 | 7,651 | 10,285 |

Per-arm execution：

| Arm | Status | Tool attempts | Finish reasons | Mutation gate |
|---|---|---|---|---|
| raw | passed | file_reader → file_patch_writer → command_executor | tool_calls, tool_calls, tool_calls, stop | passed |
| reusable | passed | file_reader → file_patch_writer → command_executor | tool_calls, tool_calls, tool_calls, stop | passed |

Mutation gates：

| Gate | Raw | Reusable |
|---|---|---|
| target changed | true | true |
| only scoped source file changed | true | true |
| writer observed | true | true |
| provider exact validation observed | true | true |
| provider validation exit zero | true | true |
| independent exact validation exit zero | true | true |
| suspicious success | false | false |

Side effects：

| Side effect | Value |
|---|---:|
| provider calls | 8 |
| writer actions | 2 |
| command actions | 2 |
| independent verification runs | 2 |
| memory mutations | 0 |
| fallback count | 0 |
| retry count | 0 |

## Gates

- BM focused：
  `PYTHONPATH=Code/src:. python -m pytest -q experiments/full_architecture_context_observation/test_stage_h8r2bm_mutation_tool_shadow.py`
  → **4 passed**
- Adjacent compaction/provider-tool focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py Code/tests/test_memory_context.py::test_memory_context_segmented_compaction_preserves_signals_and_masks_long_observations Code/tests/test_memory_context.py::test_memory_context_segmented_compaction_preserves_structured_markers Code/tests/test_execution_tool_planning_executor.py experiments/full_architecture_context_observation/test_stage_h8r2bk_real_provider_read_only_paired_canary.py experiments/full_architecture_context_observation/test_stage_h8r2bl_read_only_confirmation_matrix.py experiments/full_architecture_context_observation/test_stage_h8r2bm_mutation_tool_shadow.py`
  → **140 passed**
- BM real DeepSeek shadow：passed
- compileall for BM files：passed
- `git diff --check` for BM touched files：passed
- BM receipt body/secret scan：passed

## 安全边界

- mutation 只发生在临时 calculator fixture；
- 不修改仓库源码；
- 不保存 patch body、stdout/stderr、prompt/source/summary/response body；
- provider key 只在进程中使用，不序列化；
- reusable projection 仍是 harness-level explicit opt-in；
- `used_in_prompt=false`；
- 不授权 production builder prompt mutation；
- 不授权 default-on；
- 不声明 OpenAI/cross-provider；
- 不声明真实项目复杂 mutation 已经通过。

## 限制

- 只覆盖一个小型 calculator mutation fixture。
- 虽然真实使用了 production provider-tool entry，但不是本仓库真实任务。
- 质量判定是 scoped writer + exact pytest + 独立 pytest，不是复杂语义等价评审。
- Reusable completion tokens 比 raw 多 21；总 token 仍明显下降，但这说明后续还要继续看 completion inflation。
- OpenAI/cross-provider 仍未验证。

## 下一步

可以进入更接近真实收益的下一阶段，但仍要分层：

1. 扩大 isolated mutation fixture matrix，覆盖多文件/跨符号/失败恢复；
2. 或进入真实项目 mutation shadow，但必须保留 scoped selection、exact validation 和 suspicious-success gate；
3. OpenAI lane 仍需真实 credential 后单独跑 provider-neutral canary。
