# Phase H8-R2BD：Checkpoint discovery shadow 结果

## 判定

**PASS FOR DEFAULT-OFF CHECKPOINT DISCOVERY SHADOW；仍不授权 prompt use、default-on 或自动复用。**

本次复核先发现旧 v1 receipt 不能在当前 checkout 重放：harness 为 checkpoint binding
重新构造了 ID-only `source_fingerprint`，而当前 builder 的 authoritative source index
是 content-aware，导致 matching admission 被错误拒绝。现已让 fixture bindings 使用
captured builder source fingerprint；生产校验没有放宽。

新增 memory-layer checkpoint discovery helper 能从 checkpoint prompt-context snapshot 的
`compaction_bindings` 发现 body-free reusable compaction artifact identity，并通过
`MemoryContextBuilder.compaction_reuse_shadow_provider` 产生
`ContextCompactionReuseAdmission`。因为当前 `ContextCompactionBinding` 不保存旧 source
binding hash，本阶段要求调用方提供外部 body-free `source_binding_hash` index；缺失或漂移时
fail closed。

## 实施内容

- 扩展：
  `Code/src/memory/compaction_reuse.py`
  - `build_checkpoint_compaction_reuse_shadow_provider(...)`
  - missing source-binding hash 的 typed rejected admission
- 扩展测试：
  `Code/tests/test_compaction_reuse.py`
- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2bd_checkpoint_discovery_shadow.py`
- 新增实验测试：
  `experiments/full_architecture_context_observation/test_stage_h8r2bd_checkpoint_discovery_shadow.py`

## Metadata impact

本阶段没有新增或修改 metadata contract。它复用 H8-R2BA 的
`ContextCompactionReuseAdmission` 和 H8-R2BB 的 builder shadow hook。

`build_checkpoint_compaction_reuse_shadow_provider(...)` 是 memory-layer runtime helper。它不会
把 checkpoint binding 复制成新的 durable authority，也不会读取 artifact body。当前 checkpoint
binding 缺少旧 source binding hash，因此 helper 不会在当前 prompt payload 上现算一个 hash 并把
它假装成旧事实；缺失 index 时返回 `artifact_contract_invalid`。

## 结果

修复后的正式 receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bd_checkpoint_discovery_shadow_20260810_v2/aggregate/receipt.json`

Aggregate canonical hash：

`sha256:97fc3093e55a81e8b23af90fba0f8e9d09ca8c56867a87b3cbae163645dff503`

Receipt file SHA-256：

`sha256:2ad9eed58bbebb062aba84b88c0659c120a4b78e28bafe41a11ee2c2c9b50f30`

Invariants：

| Invariant | Result |
|---|---|
| request hash unchanged | true |
| prompt hash unchanged | true |
| selected candidates unchanged | true |
| context compactions unchanged | true |
| admission matrix expected | true |
| all shadow admissions not prompt-used | true |

Admission matrix：

| Case | Status | Rejection reason |
|---|---|---|
| matching checkpoint binding + supplied source-binding hash | admitted | |
| checkpoint binding without source-binding hash index | rejected | artifact_contract_invalid |
| stale source-binding hash | rejected | source_binding_hash_mismatch |
| artifact checksum drift | rejected | artifact_integrity_mismatch |

## Gates

- Helper + builder + H8-R2BD focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py Code/tests/test_memory_context_rolling_integration.py experiments/full_architecture_context_observation/test_stage_h8r2bd_checkpoint_discovery_shadow.py`
  → **54 passed**
- Metadata/context/reuse focused：
  `Code/tests/test_compaction_reuse.py Code/tests/test_metadata_models.py Code/tests/test_memory_context_rolling_integration.py Code/tests/test_rolling_summary_factory.py Code/tests/test_rolling_compaction.py Code/tests/test_compaction_summary_contract.py Code/tests/test_context_assembly.py Code/tests/test_context_projection.py`
  → **195 passed**
- AV/AX/AY/AZ/BA/BB/BC/BD adjacent focused：
  → **24 passed**
- compileall：passed
- `git diff --check`：passed
- Official receipt body/secret scan：passed
- trailing whitespace scan：passed

修复后的正向测试额外断言 source fingerprint 不是 ID-only hash，避免旧 fixture 漂移再次
伪装成通过。fresh receipt canonical hash 与文件 SHA 均独立复核。

## 安全边界

- no provider transport；
- no prompt/source/summary body persisted；
- no artifact body read；
- no project/memory/network/writer/command/verification side effects beyond local fixture files；
- no prompt candidate added；
- no context compaction binding created；
- no request hash, prompt hash, selected candidate, or context compaction change；
- historical checkpoints without a source-binding-hash index are rejected, not admitted。

## 限制

- `ContextCompactionBinding` still lacks a persisted source-binding hash. This phase therefore
  proves safe discovery only when an external body-free hash index is supplied.
- Prompt-use behavior remains unimplemented and unauthorized.
- No semantic equivalence, cross-provider, mutation/tool-task, long-session, token-benefit or
  default-on claim.

## 下一步

下一阶段应 decide/implement one of two paths before prompt-use canary:

1. persist a source-binding hash in the compact binding/snapshot lineage through a reviewed metadata
   change; or
2. keep checkpoint discovery shadow-only and run semantic quality/oracle checks on reusable artifacts
   before any prompt-use transition.
