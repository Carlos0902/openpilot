# Phase H8-R2BG：Reusable compact prompt-use simulation 结果

## 判定

**PASS FOR DEFAULT-OFF PROMPT-USE SIMULATION；仍不授权生产 prompt-use、default-on 或真实收益声明。**

本阶段在 H8-R2BF preflight 之后新增 runtime-only simulation：对同一组 typed candidates 分别运行
raw assembly 和 reusable assembly。Reusable assembly 只在内存里加入一个
`compacted_candidate_ids` summary candidate，并验证它确实 governed-replace 了原 source candidates。
结果只记录 hash、字符数、candidate IDs 和 typed rejection reasons，不记录 prompt/source/summary 正文。

## 实施内容

- 扩展：
  `Code/src/memory/compaction_reuse.py`
  - `ReusableCompactionPromptUseSimulationStatus`
  - `ReusableCompactionPromptUseSimulationRejectionReason`
  - `ReusableCompactionPromptUseSimulation`
  - `simulate_reusable_compaction_prompt_use(...)`
- 扩展测试：
  `Code/tests/test_compaction_reuse.py`
- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2bg_prompt_use_simulation.py`
- 新增实验测试：
  `experiments/full_architecture_context_observation/test_stage_h8r2bg_prompt_use_simulation.py`

## Metadata impact

本阶段没有新增或修改 public metadata contract。Simulation result 是 memory-layer runtime helper 输出，
用于实验 receipt 和下一阶段 canary 设计，不是 durable authority。

`ContextCompactionReuseAdmission.used_in_prompt` 仍保持 `false`；H8-R2BG 没有把 reusable summary 接入
真实 `MemoryContextBuilder` 默认 prompt，也没有创建新的 compaction binding 或 artifact。

## 结果

正式 receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bg_prompt_use_simulation_20260809_v1/aggregate/receipt.json`

Aggregate hash：

`sha256:8fa0e644e8356dacba34c2f2b0ab4d2c73fecabebdb083c5a8b00baeeba06c31`

Invariants：

| Invariant | Result |
|---|---|
| pass case passed | true |
| pass case reduces prompt chars | true |
| pass case replaces all sources | true |
| pass case keeps required and recent | true |
| all simulations dry run | true |
| preflight rejected fails | true |
| source drift fails | true |
| summary fallback fails | true |
| no benefit fails | true |

Simulation matrix：

| Case | Status | Expected signal |
|---|---|---|
| passed preflight + exact governed replacement | passed | positive prompt char delta |
| rejected preflight | rejected | preflight_not_passed |
| source drift after preflight | rejected | source_binding_hash_mismatch |
| summary cannot govern-replace sources | rejected | source_replacement_mismatch |
| safe but longer summary | rejected | no_prompt_reduction |

## Gates

- BG focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py experiments/full_architecture_context_observation/test_stage_h8r2bg_prompt_use_simulation.py`
  → **33 passed**
- Metadata/context/checkpoint + BG focused：
  `Code/tests/test_compaction_reuse.py Code/tests/test_metadata_models.py Code/tests/test_memory_context.py Code/tests/test_memory_context_rolling_integration.py Code/tests/test_compaction_summary_contract.py Code/tests/test_context_assembly.py Code/tests/test_context_projection.py Code/tests/test_runtime_checkpoint.py Code/tests/test_agent_runtime_controller.py experiments/full_architecture_context_observation/test_stage_h8r2bg_prompt_use_simulation.py`
  → **295 passed**
- AV/AX/AY/AZ/BA/BB/BC/BD/BE/BF/BG adjacent focused：
  → **27 passed**
- compileall：passed
- `git diff --check`：passed
- Official receipt body/secret scan：passed
- trailing whitespace scan：passed

## 安全边界

- no production prompt mutation；
- no provider transport；
- no artifact store body read；
- no project/memory/network/writer/command/verification side effects beyond local fixture receipt；
- no default-on behavior；
- `ContextCompactionReuseAdmission.used_in_prompt` remains false；
- rejected preflight short-circuits without prompt hashes；
- summary fallback is not treated as success: source candidates must be omitted with
  `reason="compacted"` and `governed_by_candidate_id=<summary candidate id>`；
- no semantic-equivalence, token-benefit, cross-provider, mutation/tool-task or production rollout claim。

## 限制

- Prompt benefit is measured in fixture prompt characters, not provider tokens or billing cost.
- Semantic quality remains deterministic fact coverage inherited from H8-R2BF, not LLM-judged equivalence.
- The reusable summary still does not enter production prompts by default.
- This does not prove real-task quality, mutation safety, cross-provider behavior, or net benefit after summary
  generation cost.

## 下一步

下一阶段应在 default-off 条件下设计更接近真实 prompt-use 的 canary：仍不 default-on，但可以让通过
preflight 和 simulation 的 reusable projection 进入受控 canary assembly，并继续验证 required/recent、
source lineage、semantic facts、token accounting 和 failure rollback。
