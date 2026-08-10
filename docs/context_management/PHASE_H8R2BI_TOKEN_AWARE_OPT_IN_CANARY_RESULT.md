# Phase H8-R2BI：Token-aware builder-adjacent opt-in canary 结果

## 判定

**PASS FOR TOKEN-AWARE DEFAULT-OFF OPT-IN CANARY；仍不授权生产 prompt-use、default-on 或真实 billing/token 收益声明。**

本阶段把 H8-R2BH 的 builder-sourced simulation 推进到 token-aware canary：同一组
`MemoryContextBuilder.build()` selected candidates 使用显式 token policy 和显式 offline token counter 分别
运行 raw/reusable assembly，并记录 raw/reusable prompt tokens、token delta、tokenizer metadata 和既有
source/required/recent invariants。

## 实施内容

- 扩展：
  `Code/src/memory/compaction_reuse.py`
  - `simulate_reusable_compaction_prompt_use(..., token_counter=...)`
  - `ReusableCompactionPromptUseSimulation` token fields：
    `raw_final_prompt_tokens`, `reusable_final_prompt_tokens`, `prompt_token_delta`,
    `token_count_method`, `tokenizer_id`, `token_model`
  - token-aware rejection reasons：
    `token_accounting_unavailable`, `no_token_reduction`
- 扩展测试：
  `Code/tests/test_compaction_reuse.py`
- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2bi_token_aware_opt_in_canary.py`
- 新增实验测试：
  `experiments/full_architecture_context_observation/test_stage_h8r2bi_token_aware_opt_in_canary.py`

## Metadata impact

本阶段没有新增或修改 public metadata contract。Token evidence 是 runtime-only simulation result 的派生字段，
不是 durable authority。生产 `MemoryContextBuilder` 默认输出不变，`ContextCompactionReuseAdmission` 仍然
shadow-only，`used_in_prompt=true` 仍非法。

## 结果

正式 receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bi_token_aware_opt_in_canary_20260810_v1/aggregate/receipt.json`

Aggregate hash：

`sha256:ea3d3016dc20ff21a59a55300c87e2b8296117707e606bafc95e8e09114d75cb`

Token accounting：

| Metric | Value |
|---|---:|
| raw final prompt tokens | 234 |
| reusable final prompt tokens | 45 |
| prompt token delta | 189 |

Invariants：

| Invariant | Result |
|---|---|
| builder output shape ready | true |
| builder prompt hash present | true |
| builder context compactions unchanged | true |
| simulation passed | true |
| char delta positive | true |
| token delta positive | true |
| token counts present | true |
| tokenizer metadata present | true |
| simulation replaces builder sources | true |
| simulation keeps required and recent | true |
| simulation is dry run | true |

## Gates

- BI focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py experiments/full_architecture_context_observation/test_stage_h8r2bi_token_aware_opt_in_canary.py`
  → **34 passed**
- BG/BH/BI focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py experiments/full_architecture_context_observation/test_stage_h8r2bg_prompt_use_simulation.py experiments/full_architecture_context_observation/test_stage_h8r2bh_builder_sourced_simulation.py experiments/full_architecture_context_observation/test_stage_h8r2bi_token_aware_opt_in_canary.py`
  → **36 passed**
- Metadata/context/checkpoint + BG/BH/BI focused：
  `Code/tests/test_compaction_reuse.py Code/tests/test_metadata_models.py Code/tests/test_memory_context.py Code/tests/test_memory_context_rolling_integration.py Code/tests/test_compaction_summary_contract.py Code/tests/test_context_assembly.py Code/tests/test_context_projection.py Code/tests/test_runtime_checkpoint.py Code/tests/test_agent_runtime_controller.py experiments/full_architecture_context_observation/test_stage_h8r2bg_prompt_use_simulation.py experiments/full_architecture_context_observation/test_stage_h8r2bh_builder_sourced_simulation.py experiments/full_architecture_context_observation/test_stage_h8r2bi_token_aware_opt_in_canary.py`
  → **298 passed**
- AV/AX/AY/AZ/BA/BB/BC/BD/BE/BF/BG/BH/BI adjacent focused：
  → **29 passed**
- compileall：passed
- `git diff --check`：passed
- Official receipt body/secret scan：passed

## 安全边界

- no production prompt mutation；
- no provider transport；
- no provider usage/billing claim；
- no tokenizer download/install；
- no artifact store body read；
- no persistent fixture body files；
- no project/memory/network/writer/command/verification side effects beyond local receipt；
- no default-on behavior；
- `ContextCompactionReuseAdmission.used_in_prompt` remains false；
- no semantic-equivalence, cross-provider, mutation/tool-task or production rollout claim。

## 限制

- Token counts use a deterministic offline token counter to validate accounting plumbing. They are not provider billing
  tokens and cannot be used as real-cost evidence.
- The reusable binding is still constructed in memory by the harness.
- Semantic quality remains deterministic fact coverage inherited from H8-R2BF.
- Production artifact discovery and production prompt mutation remain unimplemented.

## 下一步

下一阶段应 add a default-off production-adjacent opt-in arm that can consume a discovered/persisted reusable binding
rather than a harness-constructed binding, still requiring preflight + simulation + token accounting before any prompt
projection. Only after that should a real provider read-only paired canary be used for actual token/quality evidence.
