# Phase H8-R2BC：Default-off artifact source adapter 计划

## 背景

H8-R2BB 已经让 `MemoryContextBuilder` 能通过显式注入的
`compaction_reuse_shadow_provider` 在真实 `build()` 流程中追加
`ContextSelectionMetadata.compaction_reuse_admissions`，且不改变 prompt、request hash、
selected candidates 或 compaction bindings。

但这个 provider 还只是测试/实验手写函数。下一步需要一个可复用的 memory-layer adapter，
把 body-free compaction artifact reference/source binding 转成 shadow admission。

## 目标

1. 新增 memory-layer artifact source adapter，用于从 body-free reusable compaction artifact
   candidate 生成 `ContextCompactionReuseAdmission`。
2. Adapter 可注入 `MemoryContextBuilder.compaction_reuse_shadow_provider`。
3. Adapter 输入不需要 summary body；如果从 `ContextCompactionBinding` 构造 candidate，也只保留
   summary fingerprint，不保留 summary text。
4. Adapter fail closed：
   - source IDs missing/order mismatch；
   - source binding hash mismatch；
   - required/recent guard mismatch；
   - session constraint hash mismatch；
   - artifact kind mismatch；
   - artifact integrity mismatch。
5. Adapter 不创建 candidate、不创建 binding、不改变 prompt、不调用 provider。

## 非目标

- 不启用 prompt use。
- 不连接真实 checkpoint store 自动发现。
- 不读取 artifact body。
- 不声明真实任务收益。
- 不新增 metadata contract。

## Metadata impact note

Fact:
Reusable compaction artifact source/reference can be evaluated into a shadow admission.

Authoritative producer:
Memory-layer adapter supplied explicitly to `MemoryContextBuilder.compaction_reuse_shadow_provider`.

Consumers:
`MemoryContextBuilder` selection metadata, experiment evidence, future checkpoint-store shadow canary.

Lifecycle:
Runtime adapter output / selection evidence. No new durable metadata lifecycle.

Control impact:
None in this phase. Output remains shadow-only and `used_in_prompt=false`.

Existing contracts reviewed:
`ContextCompactionReuseAdmission`, `ContextSelectionMetadata`, `ContextCompactionRecord`,
`ContextCompactionBinding`, `DurableArtifactReference`, `MemoryContextBuilder` shadow hook,
`CONTRACT_CATALOG.md`, `API.md`.

Decision:
Create a memory-layer helper, not a new metadata contract. Reuse H8-R2BA metadata.

Why no duplicate source of truth is created:
The adapter candidate is a body-free view/reference over an existing compaction record/artifact. It records only
identity, hashes and guard facts needed to produce a shadow admission. Raw dialog, compact records, artifact refs
and prompt snapshots remain authoritative.

Serialization and migration:
No metadata schema migration. Adapter candidate is runtime-only and can be serialized for tests/receipts without
summary body.

Tests:
Adapter admit/reject matrix, from-binding body-free conversion, builder integration with unchanged prompt/request
hash, receipt body-free scan.

Documentation updates:
H8-R2BC result doc, evidence index, completion audit, implementation log, API/catalog if production-visible behavior
needs clarification.

## 实施计划

1. 新增 `Code/src/memory/compaction_reuse.py`：
   - `ReusableCompactionArtifactCandidate`
   - `source_binding_hash_from_shadow_payload(...)`
   - `admit_reusable_compaction_candidate(...)`
   - `build_compaction_reuse_shadow_provider(...)`
2. 新增 `Code/tests/test_compaction_reuse.py`：
   - matching candidate admitted；
   - source binding drift rejected；
   - missing source IDs rejected；
   - required/recent/session drift rejected；
   - artifact kind/integrity mismatch rejected；
   - `from_binding` candidate serialization excludes summary body。
3. 新增 H8-R2BC experiment harness：
   - run `MemoryContextBuilder` baseline；
   - build a body-free reusable candidate from a compaction binding；
   - inject adapter；
   - verify prompt/request/selection unchanged except admissions；
   - write body-free receipt。
4. Run gates and update evidence.

## 通过标准

- Adapter matrix fails closed on every drift.
- Builder integration has unchanged request hash, prompt hash, selected candidates and compaction bindings.
- All admissions keep `used_in_prompt=false`.
- Receipts contain no prompt/source/summary body or secrets.
