# Phase H8-R2BH：Builder-sourced reusable prompt-use simulation 结果

## 判定

**PASS FOR BUILDER-SOURCED DEFAULT-OFF SIMULATION；仍不授权生产 prompt-use、default-on 或真实收益声明。**

本阶段把 H8-R2BG 的 simulation 输入从手写 fixture 推进到 `MemoryContextBuilder.build()` 的真实
`selected_context_candidates`。Builder 先正常构造 prompt；实验 harness 随后从已选择候选中提取
non-required dialog sources、required instruction 和 recent suffix，构造 in-memory reusable binding，
再运行 H8-R2BF preflight 和 H8-R2BG simulation。

## 实施内容

- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2bh_builder_sourced_simulation.py`
- 新增实验测试：
  `experiments/full_architecture_context_observation/test_stage_h8r2bh_builder_sourced_simulation.py`
- 新增计划：
  `docs/context_management/PHASE_H8R2BH_BUILDER_SOURCED_SIMULATION_PLAN.md`

## Metadata impact

本阶段没有新增或修改 public metadata contract，也没有修改 `MemoryContextBuilder` 默认行为。

Simulation result 仍是实验派生证据：它只消费 builder 已经输出的 typed candidates，并记录 hash、字符数、
candidate IDs 和 typed status。Builder 的 prompt text、selected candidates、context compactions 和 request
identity 不被 simulation 改写。

## 结果

正式 receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bh_builder_sourced_simulation_20260810_v1/aggregate/receipt.json`

Aggregate hash：

`sha256:cef18b6c863117329809bbbfab49b58246355eb3830ee5e9d42b7bf3e45d88b7`

Invariants：

| Invariant | Result |
|---|---|
| builder output shape ready | true |
| builder prompt hash present | true |
| builder context compactions unchanged | true |
| simulation passed | true |
| simulation reduces prompt chars | true |
| simulation replaces builder sources | true |
| simulation keeps required and recent | true |
| simulation is dry run | true |

## Gates

- BG/BH focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py experiments/full_architecture_context_observation/test_stage_h8r2bg_prompt_use_simulation.py experiments/full_architecture_context_observation/test_stage_h8r2bh_builder_sourced_simulation.py`
  → **34 passed**
- Metadata/context/checkpoint + BG/BH focused：
  `Code/tests/test_compaction_reuse.py Code/tests/test_metadata_models.py Code/tests/test_memory_context.py Code/tests/test_memory_context_rolling_integration.py Code/tests/test_compaction_summary_contract.py Code/tests/test_context_assembly.py Code/tests/test_context_projection.py Code/tests/test_runtime_checkpoint.py Code/tests/test_agent_runtime_controller.py experiments/full_architecture_context_observation/test_stage_h8r2bg_prompt_use_simulation.py experiments/full_architecture_context_observation/test_stage_h8r2bh_builder_sourced_simulation.py`
  → **296 passed**
- AV/AX/AY/AZ/BA/BB/BC/BD/BE/BF/BG/BH adjacent focused：
  → **28 passed**
- compileall：passed
- `git diff --check`：passed
- Official receipt body/secret scan：passed

## 安全边界

- no production prompt mutation；
- no provider transport；
- no artifact store body read；
- no persistent fixture body files；
- no project/memory/network/writer/command/verification side effects beyond local receipt；
- no default-on behavior；
- `ContextCompactionReuseAdmission.used_in_prompt` remains false；
- no semantic-equivalence, token-benefit, cross-provider, mutation/tool-task or production rollout claim。

## 限制

- This is still an offline canary, not a real provider/tool-task run.
- Prompt benefit is measured in characters from builder-sourced candidates, not provider tokens or billing cost.
- The reusable binding is constructed in memory by the harness; production artifact-store discovery/prompt mutation remains
  unimplemented.
- Semantic quality is deterministic fact coverage inherited from H8-R2BF.

## 下一步

下一阶段可以进入更接近生产的 default-off canary：把 reusable projection 作为显式 opt-in assembly arm 挂到
builder-adjacent路径，继续要求 preflight + simulation 双门禁、body-free receipt、source drift fail-closed、
required/recent 保留，并开始记录 token-level accounting；但仍应保持 default-off，直到真实任务质量和回滚语义
通过。
