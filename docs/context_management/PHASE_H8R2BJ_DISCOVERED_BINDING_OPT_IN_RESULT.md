# Phase H8-R2BJ：Discovered persisted binding opt-in canary 结果

## 判定

**PASS FOR DISCOVERED/PERSISTED BINDING OPT-IN CANARY；仍不授权生产 prompt-use、default-on 或真实 provider 收益声明。**

本阶段把 H8-R2BI 的 token-aware opt-in canary 从 harness-constructed binding 推进到
`MemoryContextBuilder` 真实产生的 persisted `ContextCompactionBinding`。Canary 使用同一份 short memory：

- high-budget raw builder 提供完整 typed source candidates；
- low-budget compact builder 通过真实 compaction sink 产生 persisted binding；
- checkpoint discovery 从 compact context snapshot 发现 binding 并生成 shadow admission；
- discovered admission + persisted binding + raw builder candidates 进入 preflight + token-aware simulation。

## 实施内容

- 新增计划：
  `docs/context_management/PHASE_H8R2BJ_DISCOVERED_BINDING_OPT_IN_PLAN.md`
- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2bj_discovered_binding_opt_in.py`
- 新增实验测试：
  `experiments/full_architecture_context_observation/test_stage_h8r2bj_discovered_binding_opt_in.py`

## Metadata impact

本阶段没有新增或修改 public metadata contract。它复用：

- `ContextCompactionBinding.source_binding_hash`；
- `RuntimePromptContextSnapshot.compaction_bindings`；
- `ContextCompactionReuseAdmission` shadow evidence；
- H8-R2BF preflight；
- H8-R2BI token-aware simulation。

Production builder prompt 仍不被 opt-in simulation 改写，`ContextCompactionReuseAdmission.used_in_prompt` 仍为
`false`。

## 结果

正式 receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bj_discovered_binding_opt_in_20260810_v1/aggregate/receipt.json`

Aggregate hash：

`sha256:966b6693355dbf3943f2c8c24db70e75cd9d860515bc9e03c8ae27b685bf590e`

Token accounting：

| Metric | Value |
|---|---:|
| raw final prompt tokens | 789 |
| reusable final prompt tokens | 239 |
| prompt token delta | 550 |

Invariants：

| Invariant | Result |
|---|---|
| raw builder candidates available | true |
| compact builder produced one binding | true |
| persisted binding has source hash | true |
| discovery admitted binding | true |
| discovery is shadow only | true |
| simulation passed | true |
| char delta positive | true |
| token delta positive | true |
| simulation replaces discovered sources | true |
| simulation keeps required and recent | true |
| simulation is dry run | true |

## Gates

- BJ focused：
  `PYTHONPATH=Code/src:. python -m pytest -q experiments/full_architecture_context_observation/test_stage_h8r2bj_discovered_binding_opt_in.py`
  → **1 passed**
- Compaction reuse + BI/BJ focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py experiments/full_architecture_context_observation/test_stage_h8r2bi_token_aware_opt_in_canary.py experiments/full_architecture_context_observation/test_stage_h8r2bj_discovered_binding_opt_in.py`
  → **35 passed**
- BG/BH/BI/BJ focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py experiments/full_architecture_context_observation/test_stage_h8r2bg_prompt_use_simulation.py experiments/full_architecture_context_observation/test_stage_h8r2bh_builder_sourced_simulation.py experiments/full_architecture_context_observation/test_stage_h8r2bi_token_aware_opt_in_canary.py experiments/full_architecture_context_observation/test_stage_h8r2bj_discovered_binding_opt_in.py`
  → **37 passed**
- Metadata/context/checkpoint + BG/BH/BI/BJ focused：
  → **299 passed**
- AV/AX/AY/AZ/BA/BB/BC/BD/BE/BF/BG/BH/BI/BJ adjacent focused：
  → **30 passed**
- compileall：passed
- `git diff --check`：passed
- Official receipt body/secret scan：passed

## 安全边界

- no production prompt mutation；
- no default-on；
- no provider transport；
- no provider usage/billing claim；
- no artifact store body read；
- no persistent fixture body files；
- no project/memory/network/writer/command/verification side effects beyond local receipt；
- checkpoint discovery remains shadow-only；
- `ContextCompactionReuseAdmission.used_in_prompt` remains false；
- no semantic-equivalence, real-task quality, mutation/tool-task, cross-provider or production rollout claim。

## 限制

- Token counts use deterministic offline accounting, not provider billing usage.
- The reusable summary still enters only simulation, not production prompt assembly.
- Semantic facts are deterministic summary-coverage checks, not an LLM semantic-equivalence judge.
- This does not yet prove real provider quality or net benefit.

## 下一步

下一阶段可以 run a real-provider read-only paired canary using the discovered/persisted binding path:

1. raw arm：normal builder prompt；
2. reusable arm：default-off opt-in projection guarded by discovery + preflight + simulation + token accounting；
3. compare provider usage, answer quality/grounding, and failure rollback；
4. keep mutation/write/tool-task out of scope until read-only quality passes.
