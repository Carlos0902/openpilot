# Phase H8-R2BL：Read-only provider confirmation matrix 结果

## 判定

**PASS FOR DEEPSEEK READ-ONLY CONFIRMATION MATRIX；仍不授权 production prompt-use、mutation/tool-task、cross-provider rollout 或 default-on。**

本阶段把 H8-R2BK 的单点真实 read-only canary 扩展为 3 个只读 fact-contract 场景。每个场景都走完整链路：

1. raw `MemoryContextBuilder` context；
2. compact `MemoryContextBuilder` context + persisted binding；
3. checkpoint discovery shadow admission；
4. reusable preflight；
5. token-aware simulation；
6. explicit harness opt-in raw/reusable provider projection；
7. DeepSeek JSON-only read-only paired request。

## 关键发现与修复

BL 的首轮真实矩阵没有放行，原因不是 usage，而是 reusable arm 的质量事实缺失：
deterministic compact summary 原先保留行首，长行会变成类似 `ASSISTANT: Decision ...` 的截断片段，导致
`key=value` 结构化事实消失。

本阶段补了一个小的 Compact 策略修复：

- 当 dialog line 含明确 `key=value` marker 时，deterministic compaction 优先投影 marker；
- 仍保留原有自然语言 signal 提取；
- 不改变 metadata contract；
- 不改变 production prompt-use/default-on 状态；
- 新增单测锁定 marker 保留。

## 实施内容

- 新增计划：
  `docs/context_management/PHASE_H8R2BL_READ_ONLY_CONFIRMATION_MATRIX_PLAN.md`
- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2bl_read_only_confirmation_matrix.py`
- 新增实验测试：
  `experiments/full_architecture_context_observation/test_stage_h8r2bl_read_only_confirmation_matrix.py`
- 更新 deterministic compaction marker projection：
  `Code/src/memory/context_builder.py`
- 更新单测：
  `Code/tests/test_memory_context.py`
- 新增正式 v3 receipt：
  `experiments/full_architecture_context_observation/runs/phase_h8r2bl_read_only_confirmation_matrix_20260810_v3/aggregate/receipt.json`

## Metadata impact

本阶段没有新增或修改 public metadata contract。变更是 deterministic compact projection 策略：在既有
`deterministic_observation_mask_v1` 摘要内优先保留短结构化 marker。

`ContextCompactionReuseAdmission.used_in_prompt` 仍为 `false`。Reusable projection 仍只在 BL harness 的显式
opt-in paired canary 中临时构造，没有改变 `MemoryContextBuilder` 默认 prompt-use 权限。

## 结果

Official passing receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bl_read_only_confirmation_matrix_20260810_v3/aggregate/receipt.json`

Aggregate hash：

`sha256:e706653b341de451d3f237fee80c8928b4cf732fb987e343075a1d7520a69766`

Aggregate provider usage：

| Metric | Raw | Reusable | Delta |
|---|---:|---:|---:|
| prompt tokens | 28,623 | 2,054 | 26,569 |
| total tokens | 28,719 | 2,150 | 26,569 |

Per-case provider usage：

| Case | Raw prompt | Reusable prompt | Raw total | Reusable total | Status |
|---|---:|---:|---:|---:|---|
| scope_validation | 6,643 | 1,195 | 6,673 | 1,225 | passed |
| permission_boundary | 8,593 | 417 | 8,630 | 454 | passed |
| completion_evidence_boundary | 13,387 | 442 | 13,416 | 471 | passed |

Invariants：

| Invariant | Result |
|---|---|
| all cases prepared | true |
| all discovery admissions shadow-only | true |
| all preflight gates passed | true |
| all simulations passed | true |
| all provider usage complete | true |
| all reusable prompt tokens reduced | true |
| all quality facts covered | true |
| aggregate prompt tokens reduced | true |
| aggregate total tokens reduced | true |

## Gates

- BL focused：
  `PYTHONPATH=Code/src:. python -m pytest -q experiments/full_architecture_context_observation/test_stage_h8r2bl_read_only_confirmation_matrix.py`
  → **2 passed**
- Marker preservation + compaction reuse + BK/BL focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py Code/tests/test_memory_context.py::test_memory_context_segmented_compaction_preserves_signals_and_masks_long_observations Code/tests/test_memory_context.py::test_memory_context_segmented_compaction_preserves_structured_markers experiments/full_architecture_context_observation/test_stage_h8r2bk_real_provider_read_only_paired_canary.py experiments/full_architecture_context_observation/test_stage_h8r2bl_read_only_confirmation_matrix.py`
  → **39 passed**
- BL real v3 DeepSeek matrix：passed
- compileall for touched code/tests：passed
- `git diff --check` for touched files：passed
- BL receipt/code/doc body/secret scan：passed

## 安全边界

- no production prompt mutation；
- no default-on；
- no tool calls；
- no project/memory mutation；
- no writer or command actions；
- no artifact body read；
- no prompt/source/summary/response body persisted；
- provider key is process-only and not serialized；
- `ContextCompactionReuseAdmission.used_in_prompt=false`；
- result is DeepSeek read-only scoped, not cross-provider。

## 限制

- 只覆盖 3 个 read-only fact-contract prompts，不覆盖 mutation/tool-task。
- 质量判定是结构化事实覆盖，不是通用语义等价评审。
- 这是 DeepSeek lane 的真实 usage，不是 OpenAI 或其他 provider 结果。
- Reusable projection 仍是 harness-level explicit opt-in，不是 production builder default。

## 下一步

可以进入 mutation/tool-task shadow 前的最后一道设计门：

1. 设计 H8-R2BM mutation/tool-task shadow gate；
2. 继承 BL 的 marker-preserving compact strategy；
3. 保持 reusable prompt-use feature-flagged；
4. 先跑 isolated/safe mutation shadow，不直接 default-on。
