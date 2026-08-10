# Phase H8-R2BB：Default-off builder shadow injection 计划

## 背景

H8-R2AZ 证明 reusable summary artifact 的实验层 admission/invalidation 成立。H8-R2BA
把 admission 结果映射到生产 metadata surface：
`ContextSelectionMetadata.compaction_reuse_admissions`，并强制 shadow-only。

剩余缺口是：真实 `MemoryContextBuilder` 还不会在一次普通 context build 中调用这个 shadow
admission surface。本阶段补这个缺口，但仍不让 reused summary 进入 prompt。

## 目标

1. 为 `MemoryContextBuilder` 增加 default-off reusable compaction shadow hook。
2. hook 只返回 `ContextCompactionReuseAdmission` body-free values，并附加到
   `assembly.selection.compaction_reuse_admissions`。
3. hook 不能修改 candidates、sources、prompt text、context compactions、artifact bindings 或 request hash。
4. hook 抛错时默认 fail closed：不改变 prompt；strict mode 下转成 `ContextSourceError`。
5. 历史行为默认不变；未配置 hook 时输出仍为空列表。

## 非目标

- 不读取/持久化 summary 正文。
- 不把 reusable artifact 作为 `ContextCandidate`。
- 不创建 `ContextCompactionBinding`。
- 不调用 provider。
- 不改变 deterministic/LLM rolling summary 生成路径。
- 不声明真实任务质量或 token 收益。

## Metadata impact note

Fact:
One context selection pass may contain shadow-only reusable compaction admission values.

Authoritative producer:
`MemoryContextBuilder` via an explicitly injected reusable-compaction shadow provider.

Consumers:
Context selection diagnostics, checkpoint/replay audit, H8-R2BB experiment, future canary gates.

Lifecycle:
Runtime selection evidence / checkpoint-compatible nested value.

Control impact:
None in this phase; `used_in_prompt=true` remains invalid.

Existing contracts reviewed:
`ContextSelectionMetadata`, `ContextCompactionReuseAdmission`, `ContextCompactionAttempt`,
`ContextCompactionRecord`, `ContextCompactionBinding`, `DurableArtifactReference`,
context builder, context projection, runtime checkpoint tests, `CONTRACT_CATALOG.md`.

Decision:
Reuse the H8-R2BA nested value and extend `MemoryContextBuilder` with an injected default-off
provider. Do not add a new metadata contract.

Why no duplicate source of truth is created:
The hook records only admission evidence. Raw dialog, memory/project/file candidates, deterministic
compaction records, and prompt-context artifacts remain authoritative. The hook cannot govern
source omissions or bind artifacts.

Serialization and migration:
No schema migration beyond H8-R2BA; `compaction_reuse_admissions` already defaults to `[]`.

Tests:
Builder default-off, admitted/rejected shadow injection, hook failure fallback/strict failure,
request hash/prompt/body-free invariants.

Documentation updates:
H8-R2BB result doc, evidence index, completion audit, implementation log.

## 实施计划

1. Extend `MemoryContextBuilder.__init__` with an optional
   `compaction_reuse_shadow_provider`.
2. Provider signature should receive body-safe current context facts:
   - candidates after normal compaction path；
   - assembly；
   - session constraints hash；
   - session turn source hash；
   - request hash。
3. Add a private `_apply_compaction_reuse_shadow(...)` helper that validates returned values as
   `ContextCompactionReuseAdmission` and appends them to selection metadata.
4. Tests:
   - no provider → `compaction_reuse_admissions == []`；
   - provider returns admitted + rejected → values appear, prompt/context candidates/bindings unchanged；
   - provider exception non-strict → no prompt change, empty admissions；
   - provider exception strict → `ContextSourceError("context_compaction_reuse")`；
   - duplicate/invalid admissions fail closed by metadata validation.
5. Run focused gates and update evidence.

## 通过标准

- Existing context/rolling tests remain green.
- New builder shadow tests pass.
- BA/AZ adjacent experiment tests remain green.
- Receipts/docs stay body-free and secret-free.
- The result explicitly states that prompt use is still not authorized.
