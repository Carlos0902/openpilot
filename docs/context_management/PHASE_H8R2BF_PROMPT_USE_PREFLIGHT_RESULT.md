# Phase H8-R2BF：Reusable compact prompt-use preflight 结果

## 判定

**PASS FOR DEFAULT-OFF PROMPT-USE PREFLIGHT；仍不授权真实 prompt-use、default-on 或真实收益声明。**

本阶段新增 runtime-only prompt-use preflight helper。它可以在不改变真实 prompt 的情况下，验证一个
reusable compaction binding 是否满足未来 prompt-use transition 的硬条件：admission、source binding、
artifact integrity、required/recent retention、semantic fact coverage 和 trial assembly 都必须通过。

## 实施内容

- 扩展：
  `Code/src/memory/compaction_reuse.py`
  - `ReusableCompactionSemanticFact`
  - `ReusableCompactionPromptUsePreflightStatus`
  - `ReusableCompactionPromptUseRejectionReason`
  - `ReusableCompactionPromptUsePreflight`
  - `preflight_reusable_compaction_prompt_use(...)`
- 扩展测试：
  `Code/tests/test_compaction_reuse.py`
- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2bf_prompt_use_preflight.py`
- 新增实验测试：
  `experiments/full_architecture_context_observation/test_stage_h8r2bf_prompt_use_preflight.py`

## Metadata impact

本阶段没有新增或修改 public metadata contract。Preflight result 是 memory-layer runtime helper 输出，
不是新的 durable authority。

`ContextCompactionReuseAdmission` 仍然保持 shadow-only，`used_in_prompt=true` 仍非法。BF 没有把 reusable
summary 放进真实 prompt，只在 trial assembly 中验证如果未来要替换 source candidates，必须满足哪些
条件。

## 结果

正式 receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bf_prompt_use_preflight_20260809_v1/aggregate/receipt.json`

Aggregate hash：

`sha256:4389babece9548e6310ce6b57f3730d9d3077a7a8aa2ea9baf652e8a61c44652`

Invariants：

| Invariant | Result |
|---|---|
| pass case passed | true |
| pass case replaces all sources | true |
| all admissions remain dry-run | true |
| rejected admission fails | true |
| source drift fails | true |
| semantic missing fails | true |
| semantic bad evidence fails | true |
| recent omitted fails | true |
| trial not selected fails | true |

Preflight matrix：

| Case | Status | Expected reason |
|---|---|---|
| admitted binding + semantic facts + required/recent retained | passed | |
| rejected shadow admission | rejected | admission_not_admitted |
| source content drift | rejected | source_binding_hash_mismatch |
| semantic fact missing from summary | rejected | semantic_fact_missing |
| semantic evidence outside compacted source set | rejected | semantic_evidence_mismatch |
| recent suffix would be compacted | rejected | recent_suffix_omitted |
| summary candidate cannot fit trial prompt | rejected | trial_summary_not_selected |

## Gates

- BF focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py experiments/full_architecture_context_observation/test_stage_h8r2bf_prompt_use_preflight.py`
  → **28 passed**
- Metadata/context/checkpoint + BF focused：
  `Code/tests/test_compaction_reuse.py Code/tests/test_metadata_models.py Code/tests/test_memory_context.py Code/tests/test_memory_context_rolling_integration.py Code/tests/test_compaction_summary_contract.py Code/tests/test_context_assembly.py Code/tests/test_context_projection.py Code/tests/test_runtime_checkpoint.py Code/tests/test_agent_runtime_controller.py experiments/full_architecture_context_observation/test_stage_h8r2bf_prompt_use_preflight.py`
  → **290 passed**
- AV/AX/AY/AZ/BA/BB/BC/BD/BE/BF adjacent focused：
  → **26 passed**
- compileall：passed
- `git diff --check`：passed
- Official receipt body/secret scan：passed
- trailing whitespace scan：passed

## 安全边界

- no real prompt mutation；
- no provider transport；
- no artifact body read from store；
- no project/memory/network/writer/command/verification side effects beyond local fixture files；
- no default-on behavior；
- `ContextCompactionReuseAdmission.used_in_prompt` remains false；
- no semantic-equivalence, token-benefit, cross-provider, mutation/tool-task or production rollout claim。

## 限制

- Semantic quality is deterministic fact coverage, not an LLM judge or real-task semantic-equivalence proof.
- The helper validates a future transition but does not yet wire reusable summary into production prompt assembly.
- Token benefit remains unproven for real tasks after generation/reuse accounting.

## 下一步

H8-R2BG should run a default-off prompt-use simulation/canary that actually compares raw selected candidates with a
preflight-passed reusable projection in a controlled assembly path, still without enabling production default-on.
