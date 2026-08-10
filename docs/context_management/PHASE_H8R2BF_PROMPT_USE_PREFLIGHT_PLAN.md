# Phase H8-R2BF：Reusable compact prompt-use preflight 计划

## 背景

H8-R2BE 已经让新生成的 `ContextCompactionBinding` 持久化 body-free
`source_binding_hash`，checkpoint discovery shadow 不再必须依赖外部 index。但 reusable artifact 仍然
只能产生 `used_in_prompt=false` 的 shadow admission。

真正让 reusable summary 替换 source candidates 之前，还需要一个前置门禁：即使 source hash、artifact
hash 都通过，也必须证明 summary candidate 能完整进入 trial prompt、required constraints 和 recent
suffix 不被挤掉，并且 summary 覆盖明确的语义事实。

## 目标

1. 新增 runtime-only prompt-use preflight helper，输入 current candidates、reusable binding、policy、
   renderer 和 semantic facts。
2. Preflight 必须检查：
   - reusable admission 已 admitted；
   - current source candidate body-free hash 与 binding hash 匹配；
   - artifact kind/checksum 匹配；
   - required candidate IDs 在 trial 中保留；
   - recent suffix IDs 在 trial 中保留；
   - semantic facts 的 evidence IDs 来自 compacted sources；
   - semantic fact text 出现在 summary projection 中；
   - trial assembly 中 reusable summary candidate 完整 kept；
   - governed source candidates 由 summary candidate compacted，而不是普通 budget omission。
3. 输出 body-free typed result：只记录 status、reason codes、IDs、hashes 和 trial evidence，不写 prompt/source/summary body。
4. 保持 `ContextCompactionReuseAdmission.used_in_prompt=false`；本阶段不改变真实 prompt，不启用 default-on。

## 非目标

- 不让 reusable artifact 进入生产 prompt。
- 不改 `ContextCompactionReuseAdmission` 的 shadow-only contract。
- 不新增 public metadata contract。
- 不调用 provider 或 LLM semantic judge。
- 不声明真实任务 token benefit 或语义等价。

## Metadata impact note

Fact:
A reusable compaction artifact can be preflighted for a future prompt-use transition with typed source, guard, trial
assembly, and semantic-fact evidence.

Authoritative producer:
Memory-layer runtime helper invoked explicitly by experiments or future default-off canary code.

Consumers:
H8-R2BF experiment receipt, future reusable prompt-use canary design, audit docs.

Lifecycle:
Runtime-only preflight result / experiment evidence. No durable metadata lifecycle in this phase.

Control impact:
Future prompt-use admission, but in this phase it controls only a dry-run status and never changes prompt selection.

Existing contracts reviewed:
`ContextCompactionBinding`, `ContextCompactionRecord`, `ContextCompactionReuseAdmission`,
`ContextCandidate`, `ContextAssemblyPolicy`, `ContextAssemblyResult`, `ContextSelectionMetadata`,
`ContextAssembler`, `CONTRACT_CATALOG.md`, `API.md`, `docs/metadata/DEVELOPMENT_CONVENTIONS.md`.

Decision:
Add memory-layer strict runtime helper/models, not a public metadata contract. Keep existing metadata unchanged until a
real prompt-use transition is separately reviewed.

Why no duplicate source of truth is created:
The preflight result is derived from current candidates, one binding, one existing shadow admission and explicit
semantic facts. It does not persist source text, alter binding authority, or create an alternative context selection.

Serialization and migration:
No metadata migration. Preflight result is serializable for receipts and excludes prompt/source/summary bodies.

Tests:
Pass case, non-admitted admission, stale source hash, missing semantic fact, invalid semantic evidence, required omitted,
recent omitted, summary candidate not selected, governed source mismatch, body-free receipt scan.

Documentation updates:
H8-R2BF result doc, evidence index, completion audit, implementation log; API/catalog only if contract text needs
clarification.

## 实施计划

1. Extend `Code/src/memory/compaction_reuse.py` with:
   - `ReusableCompactionSemanticFact`
   - `ReusableCompactionPromptUsePreflightStatus`
   - `ReusableCompactionPromptUseRejectionReason`
   - `ReusableCompactionPromptUsePreflight`
   - `preflight_reusable_compaction_prompt_use(...)`
2. Extend `Code/tests/test_compaction_reuse.py` with preflight pass/fail matrix.
3. Add H8-R2BF experiment harness:
   - create current typed candidates;
   - create binding with persisted source-binding hash;
   - create admitted shadow evidence;
   - run preflight matrix;
   - write body-free receipt.
4. Run focused gates and update evidence docs.

## 通过标准

- Passing case proves summary candidate can replace exactly its governed sources in trial.
- Every unsafe case fails closed with typed reason.
- `ContextCompactionReuseAdmission.used_in_prompt` remains false.
- No prompt/source/summary body or secrets are serialized in receipts.
- No provider/network/mutation/writer/command side effects.
