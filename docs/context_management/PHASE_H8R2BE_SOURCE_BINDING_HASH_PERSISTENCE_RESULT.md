# Phase H8-R2BE：Source-binding hash persistence 结果

## 判定

**PASS FOR PERSISTED SOURCE-BINDING HASH；仍不授权 prompt use、default-on 或真实收益声明。**

H8-R2BE 把 H8-R2BD 的外部 source-binding hash index 要求推进到了生产 binding lineage：
新生成的 `ContextCompactionBinding` 会携带 body-free `source_binding_hash`。Checkpoint discovery
shadow 现在可以直接从 persisted binding hash admission；外部 hash map 只保留为历史 binding
兼容输入，且与 persisted hash 冲突时 fail closed。

## 实施内容

- 扩展：
  `Code/src/metadata/agent_runtime.py`
  - `ContextCompactionBinding.source_binding_hash`
- 扩展：
  `Code/src/memory/compaction_summary.py`
  - `source_candidate_binding_hash(...)`
- 扩展：
  `Code/src/memory/context_builder.py`
  - 新生成 compaction binding 写入 `source_binding_hash`
- 扩展：
  `Code/src/memory/compaction_reuse.py`
  - `from_binding(...)` 可使用 persisted hash；
  - checkpoint discovery provider 优先使用 persisted hash；
  - external compatibility hash 冲突时 rejected。
- 扩展测试：
  `Code/tests/test_compaction_reuse.py`
  `Code/tests/test_metadata_models.py`
  `Code/tests/test_memory_context.py`
- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2be_source_binding_hash_persistence.py`
- 新增实验测试：
  `experiments/full_architecture_context_observation/test_stage_h8r2be_source_binding_hash_persistence.py`

## Metadata impact

本阶段没有新增 public metadata contract；扩展现有 `ContextCompactionBinding`。

`ContextCompactionRecord.source_fingerprint` 仍然是 summary source-content fingerprint。
`ContextCompactionBinding.source_binding_hash` 是另一个 body-free projection guard，用于 reusable
admission drift checks。二者不是重复事实：前者绑定摘要来源正文，后者绑定 prompt/source projection
视图的身份、顺序、控制字段和 content hash。

历史 binding 缺失该字段时默认 `""`，保持可读；消费者必须把空值视为 missing，除非调用方显式提供
历史兼容 hash。

## 结果

正式 receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2be_source_binding_hash_persistence_20260809_v1/aggregate/receipt.json`

Aggregate hash：

`sha256:69930bcbd8f534655651a7933dac6cbb0e14d3548c8006a22533093e370fa1ec`

Invariants：

| Invariant | Result |
|---|---|
| new binding persisted source-binding hash | true |
| request hash unchanged | true |
| prompt hash unchanged | true |
| selected candidates unchanged | true |
| context compactions preserve persisted hash | true |
| persisted and historical compatibility admissions pass | true |
| conflicting external hash rejects | true |
| historical compatibility alone admits | true |
| all shadow admissions not prompt-used | true |

Admission matrix：

| Case | Status | Rejection reason |
|---|---|---|
| persisted binding hash, no external index | admitted | |
| historical binding + compatible external hash | admitted | |
| persisted binding hash + conflicting external hash | rejected | artifact_contract_invalid |

## Gates

- Source-binding persistence focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py Code/tests/test_metadata_models.py Code/tests/test_memory_context.py Code/tests/test_memory_context_rolling_integration.py experiments/full_architecture_context_observation/test_stage_h8r2be_source_binding_hash_persistence.py`
  → **110 passed**
- Metadata/context/checkpoint focused：
  `Code/tests/test_compaction_reuse.py Code/tests/test_metadata_models.py Code/tests/test_memory_context.py Code/tests/test_memory_context_rolling_integration.py Code/tests/test_compaction_summary_contract.py Code/tests/test_context_assembly.py Code/tests/test_context_projection.py Code/tests/test_runtime_checkpoint.py Code/tests/test_agent_runtime_controller.py`
  → **281 passed**
- AV/AX/AY/AZ/BA/BB/BC/BD/BE adjacent focused：
  → **25 passed**
- compileall：passed
- `git diff --check`：passed
- Official receipt body/secret scan：passed
- trailing whitespace scan：passed

## 安全边界

- no provider transport；
- no prompt/source/summary body persisted；
- no artifact body read；
- no prompt candidate added by reuse admission；
- no source omission governed by reuse admission；
- all admissions remain `used_in_prompt=false`；
- no semantic-equivalence, token-benefit, cross-provider, mutation/tool-task or default-on claim。

## 限制

- Prompt-use behavior remains unimplemented and unauthorized.
- Semantic quality/oracle checks for reusable summaries are still pending.
- This does not prove token benefit in a real tool/mutation task.

## 下一步

H8-R2BF should add a prompt-use preflight/semantic quality gate for persisted reusable compaction artifacts, still
default-off. It should verify that a reusable artifact can only replace the exact governed non-required source set when
required constraints, recent suffix, source binding, artifact integrity and semantic facts all pass.
