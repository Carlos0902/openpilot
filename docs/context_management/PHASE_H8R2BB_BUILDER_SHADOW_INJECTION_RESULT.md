# Phase H8-R2BB：Default-off builder shadow injection 结果

## 判定

**PASS FOR DEFAULT-OFF BUILDER SHADOW INJECTION；仍不授权 prompt use 或 default-on。**

`MemoryContextBuilder` 现在可以通过显式注入的 `compaction_reuse_shadow_provider`，在真实
`build()` 流程里追加 `ContextSelectionMetadata.compaction_reuse_admissions`。该 hook 默认关闭，
接收 body-free snapshot，只能追加 shadow metadata，不能改变 prompt、candidate selection、
context compactions、artifact bindings 或 request hash。

## 实施内容

- 更新 `MemoryContextBuilder`：
  - 新增可选 `compaction_reuse_shadow_provider`；
  - 新增 `_apply_compaction_reuse_shadow(...)`；
  - provider 只收到 candidate digests、prompt hash、request hash、session hashes 等 body-free facts；
  - provider 结果被校验为 `ContextCompactionReuseAdmission`；
  - 非 strict 失败 fail closed，strict 失败抛 `ContextSourceError("context_compaction_reuse")`。
- 新增 builder integration tests：
  `Code/tests/test_memory_context_rolling_integration.py`
- 新增 experiment harness：
  `experiments/full_architecture_context_observation/stage_h8r2bb_builder_shadow_injection.py`
- 新增 experiment test：
  `experiments/full_architecture_context_observation/test_stage_h8r2bb_builder_shadow_injection.py`

## Metadata impact

本阶段复用 H8-R2BA 的 `ContextCompactionReuseAdmission`，没有新增 metadata contract。
`MemoryContextBuilder` 只是成为该 nested value 的一个显式注入生产者。该生产者不拥有 raw dialog、
summary record、artifact binding 或 prompt authority；它只拥有本次 selection pass 的 shadow
admission evidence。

## 结果

正式 receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2bb_builder_shadow_injection_20260809_v1/aggregate/receipt.json`

Aggregate hash：

`sha256:24b2c1e4a63ec6f79c9a80af2f25718349f8effb3a9cbb880d816cc0adcf1ab7`

Invariants：

| Invariant | Result |
|---|---|
| request hash unchanged | true |
| prompt hash unchanged | true |
| selected candidates unchanged | true |
| context compactions unchanged | true |
| shadow payload body-free | true |
| all shadow admissions `used_in_prompt=false` | true |

Admissions：

- `admitted`, `used_in_prompt=false`
- `rejected`, `source_fingerprint_mismatch`, `used_in_prompt=false`

## Gates

- Builder integration focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_memory_context_rolling_integration.py`
  → **13 passed**
- Metadata + builder + BB focused：
  → **65 passed**
- Metadata/context/rolling focused：
  `Code/tests/test_metadata_models.py Code/tests/test_memory_context_rolling_integration.py Code/tests/test_rolling_summary_factory.py Code/tests/test_rolling_compaction.py Code/tests/test_compaction_summary_contract.py Code/tests/test_context_assembly.py Code/tests/test_context_projection.py`
  → **153 passed**
- AV/AX/AY/AZ/BA/BB adjacent focused：
  → **22 passed**
- compileall：passed
- body/secret-free receipt scan：passed
- `git diff --check`：passed
- trailing whitespace scan：no hits

## 安全边界

- no provider transport；
- no summary/prompt/source body passed to the shadow provider；
- no prompt body persisted in receipt；
- no project/memory/network/writer/command/verification side effects beyond local fixture files；
- no context compaction binding created；
- no reused summary candidate added；
- no request hash or prompt hash change。

## 限制

- The hook is injectable but not connected to a real artifact store/source invalidation service by default.
- Prompt-use behavior remains unimplemented and unauthorized.
- No semantic equivalence, cross-provider, mutation/tool-task, or long-session real-task benefit claim.

## 下一步

H8-R2BC：connect the builder shadow hook to a default-off artifact source adapter that can evaluate real
body-free `ContextCompactionBinding`/artifact references from checkpoint storage, still with `used_in_prompt=false`.
After that, a separate canary can decide whether prompt-use admission is safe.
