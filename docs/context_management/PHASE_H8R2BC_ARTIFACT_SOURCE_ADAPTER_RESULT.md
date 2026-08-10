# Phase H8-R2BC：Default-off artifact source adapter 结果

## 判定

**PASS FOR DEFAULT-OFF ARTIFACT SOURCE ADAPTER；仍不授权 prompt use 或 default-on。**

本次复核先发现旧 v1 receipt 不能在当前 checkout 重放：harness 为正向
`ContextCompactionBinding` 重新构造了 ID-only `source_fingerprint`，而生产 builder
的 authoritative source index 是 content-aware，导致正向 admission 被错误拒绝。
现已让 fixture binding 使用 captured builder source fingerprint；生产校验没有放宽。

新增 memory-layer adapter 能把 body-free reusable compaction artifact candidate 转成
`ContextCompactionReuseAdmission`，并可直接注入 `MemoryContextBuilder.compaction_reuse_shadow_provider`。
Adapter 不读取 artifact body、不创建 prompt candidate、不创建 binding、不调用 provider。

## 实施内容

- 新增：
  `Code/src/memory/compaction_reuse.py`
  - `ReusableCompactionArtifactCandidate`
  - `source_binding_hash_from_shadow_payload(...)`
  - `admit_reusable_compaction_candidate(...)`
  - `build_compaction_reuse_shadow_provider(...)`
- 新增测试：
  `Code/tests/test_compaction_reuse.py`
- 新增实验：
  `experiments/full_architecture_context_observation/stage_h8r2bc_artifact_source_adapter.py`
- 新增实验测试：
  `experiments/full_architecture_context_observation/test_stage_h8r2bc_artifact_source_adapter.py`

## Metadata impact

本阶段没有新增 metadata contract。Adapter 复用 H8-R2BA 的
`ContextCompactionReuseAdmission`，并作为 H8-R2BB builder shadow hook 的一个默认关闭生产者。
`ReusableCompactionArtifactCandidate` 是 memory-layer runtime helper，不是 durable metadata authority。

从 `ContextCompactionBinding` 构造 candidate 时，adapter 只保留：

- artifact ID/kind/checksum；
- record compaction ID/algorithm；
- source candidate IDs；
- source fingerprint；
- source binding hash；
- guard IDs/hash；
- generated summary fingerprint。

它不会保留 `record.summary`。

## 结果

修复后的正式 receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bc_artifact_source_adapter_20260810_v2/aggregate/receipt.json`

Aggregate canonical hash：

`sha256:1ec7205d4ffe9c88b7681142f700580f0333c512e25e1e9e0cd0f4d62ec977b5`

Receipt file SHA-256：

`sha256:bb582a75da7c6c907691b462a4cb3c380b7924bdc9edb6765d2337c610aedc29`

Invariants：

| Invariant | Result |
|---|---|
| request hash unchanged | true |
| prompt hash unchanged | true |
| selected candidates unchanged | true |
| context compactions unchanged | true |
| admitted shadow not prompt-used | true |
| rejected shadow not prompt-used | true |

Admissions：

- `admitted`, `used_in_prompt=false`
- `rejected`, `artifact_integrity_mismatch`, `used_in_prompt=false`

## Gates

- Adapter + builder + BC focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_compaction_reuse.py Code/tests/test_memory_context_rolling_integration.py experiments/full_architecture_context_observation/test_stage_h8r2bc_artifact_source_adapter.py`
  → **54 passed**
- Metadata/context/adapter focused：
  `Code/tests/test_compaction_reuse.py Code/tests/test_metadata_models.py Code/tests/test_memory_context_rolling_integration.py Code/tests/test_rolling_summary_factory.py Code/tests/test_rolling_compaction.py Code/tests/test_compaction_summary_contract.py Code/tests/test_context_assembly.py Code/tests/test_context_projection.py`
  → **195 passed**
- AV/AX/AY/AZ/BA/BB/BC adjacent focused：
  → **23 passed**
- compileall：passed
- body/secret-free receipt scan：passed
- `git diff --check`：passed
- trailing whitespace scan：no hits

修复后的正向测试额外断言 source fingerprint 不是 ID-only hash，避免旧 fixture 漂移再次
伪装成通过。fresh receipt canonical hash 与文件 SHA 均独立复核。

## 安全边界

- no provider transport；
- no prompt/source/summary body persisted；
- no artifact body read；
- no project/memory/network/writer/command/verification side effects beyond local fixture files；
- no prompt candidate added；
- no context compaction binding created；
- no request hash, prompt hash, selected candidate, or context compaction change。

## 限制

- Adapter candidate discovery is still explicit/injected; it is not wired to automatic checkpoint-store discovery.
- 旧 v1 receipt `sha256:0f2581c2...` 仅作为历史失败证据保留，不再作为当前 checkout 的
  PASS 依据；当前证据必须使用上述 v2 receipt。
- Prompt-use behavior remains unimplemented and unauthorized.
- No semantic equivalence, cross-provider, mutation/tool-task, or long-session real-task benefit claim.

## 下一步

H8-R2BD：checkpoint-store discovery shadow。让 builder shadow provider 从 checkpoint/recovery artifact store
发现 eligible body-free compaction candidates，仍只产生 `used_in_prompt=false` admissions。之后再设计
prompt-use canary。
