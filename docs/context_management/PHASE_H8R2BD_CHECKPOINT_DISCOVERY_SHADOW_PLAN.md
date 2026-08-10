# Phase H8-R2BD：Checkpoint discovery shadow 计划

## 背景

H8-R2BC 已经提供了 default-off memory-layer adapter：显式传入 body-free reusable
compaction candidate 后，`MemoryContextBuilder` 可以追加
`ContextSelectionMetadata.compaction_reuse_admissions`，且不改变 prompt、request hash、
selected candidates 或 compaction bindings。

下一步需要让这个 shadow provider 能从 checkpoint prompt-context snapshot 中发现已有
`ContextCompactionBinding`。但当前 `ContextCompactionBinding` 只保存 source candidate IDs 和
source fingerprint，没有保存当时每个 source 的 body-free digest 绑定 hash。若 discovery 在当前
payload 上重新计算 hash 并把它当作“旧 hash”，就会掩盖 source drift。

因此本阶段采用 fail-closed 设计：checkpoint discovery 可以读取 snapshot 中的 binding 身份和
artifact checksum，但必须由调用方提供 body-free `source_binding_hash` index。缺失或不匹配时只产生
rejected shadow admission。

## 目标

1. 新增 memory-layer checkpoint discovery helper/provider，从 checkpoint prompt snapshot 的
   `compaction_bindings` 生成 reusable compaction shadow admissions。
2. Discovery provider 只读取 binding 的 identity/hash/guard 字段，不读取 artifact body，不保留
   summary body。
3. `source_binding_hash` 必须来自外部 body-free index；缺失时 fail closed。
4. Provider 仍通过 `MemoryContextBuilder.compaction_reuse_shadow_provider` default-off 注入。
5. 所有 admission 仍保持 `used_in_prompt=false`，不创建 prompt candidate，不创建
   `ContextCompactionBinding`，不改变 prompt/request identity。

## 非目标

- 不启用 prompt use。
- 不改 checkpoint metadata schema。
- 不自动从 artifact store 读取 compact summary body。
- 不声明真实任务收益。
- 不引入 provider transport、网络或项目写入。

## Metadata impact note

Fact:
Checkpoint prompt-context snapshots can be inspected for reusable compaction artifact identities and evaluated as
shadow admissions when a body-free source binding hash index is supplied.

Authoritative producer:
Memory-layer checkpoint discovery helper explicitly injected into `MemoryContextBuilder.compaction_reuse_shadow_provider`.

Consumers:
`MemoryContextBuilder` selection metadata, H8-R2BD experiment receipt, future prompt-use/recovery design review.

Lifecycle:
Runtime-only helper output / selection evidence. Existing checkpoint snapshots remain the source of binding identity.

Control impact:
None in this phase. Output remains shadow-only and `used_in_prompt=false`.

Existing contracts reviewed:
`RuntimePromptContextSnapshot`, `ContextCompactionBinding`, `ContextCompactionRecord`,
`DurableArtifactReference`, `ContextCompactionReuseAdmission`, `ContextSelectionMetadata`,
`MemoryContextBuilder` shadow hook, `CONTRACT_CATALOG.md`, `API.md`,
`docs/metadata/DEVELOPMENT_CONVENTIONS.md`.

Decision:
Reuse existing metadata and add a memory-layer helper. Do not extend checkpoint schema in this phase.

Why no duplicate source of truth is created:
The helper derives a transient body-free candidate view from an existing checkpoint-owned binding plus an external
source-binding hash index. It does not persist a second compaction record, source snapshot, artifact body, or prompt
projection.

Serialization and migration:
No metadata migration. Historical checkpoints without a source-binding hash index remain readable but are rejected for
reuse shadow admission.

Tests:
Admit matching discovered binding, reject missing source-binding hash, reject stale source-binding hash, reject artifact
checksum drift, verify builder integration leaves prompt/request/selection unchanged except admissions, and scan receipt
for prompt/source/summary bodies and secrets.

Documentation updates:
H8-R2BD result doc, evidence index, completion audit, implementation log, API/catalog clarification if needed.

## 实施计划

1. 扩展 `Code/src/memory/compaction_reuse.py`：
   - `build_checkpoint_compaction_reuse_shadow_provider(...)`
   - 缺失 `source_binding_hash` 时构造 rejected admission；
   - 支持可选 expected artifact checksum map。
2. 扩展 `Code/tests/test_compaction_reuse.py`：
   - matching checkpoint binding admitted；
   - missing hash rejected as `artifact_contract_invalid`；
   - stale hash rejected as `source_binding_hash_mismatch`；
   - artifact checksum drift rejected；
   - serialization excludes summary body。
3. 新增 H8-R2BD experiment harness：
   - baseline build；
   - capture body-free shadow payload；
   - construct checkpoint prompt snapshot with compaction binding；
   - inject discovery provider；
   - verify prompt/request/selected candidates/context compactions unchanged；
   - write body-free receipt。
4. Run gates and update evidence docs.

## 通过标准

- Missing source-binding index fails closed, not admitted.
- Stale source-binding hash fails closed.
- Artifact integrity mismatch fails closed.
- Builder integration has unchanged request hash, prompt hash, selected candidate digests and context compaction count.
- All admissions keep `used_in_prompt=false`.
- Receipt contains no prompt text, source body, summary body, raw provider output, or secrets.
