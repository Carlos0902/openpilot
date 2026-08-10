# Phase H8-R2BB：Shadow failure telemetry 与 artifact contract fail-closed 计划

## 背景

H8-R2BA 已证明 source fingerprint 与真实 builder wiring 的安全边界，但审计仍发现两类
问题：

1. `MemoryContextBuilder` 在 non-strict 模式下对 shadow provider 的异常、`None`/空返回、
   malformed admission 静默回退，selection 中没有 typed fallback evidence；
2. checkpoint binding 的 artifact checksum 未在通用 reference 上强校验，构造 runtime
   candidate 时可能抛异常，随后被 builder 静默吞掉；直接构造 admitted metadata 也仍可绕过
   production artifact kind/summary identity 约束。

## 本阶段目标

- 为 shadow provider failure 增加 body-free、typed、selection-owned fallback evidence；
- 保持 strict/non-strict 语义：strict 仍 fail fast，non-strict 保持原 assembly 但可审计；
- 对 malformed checkpoint artifact 生成安全的 typed rejection，而不是静默丢 admission；
- 对 admitted reuse admission 强制 production artifact kind 和 generated summary identity；
- 不改变 prompt、selected candidates、request identity、authority artifact 或 prompt-use；
- 不调用真实 provider，不写 project/memory 数据，不进入 canary。

## metadata impact note

```text
Fact: one shadow-provider failure and its deterministic fallback classification
Authoritative producer: MemoryContextBuilder._apply_compaction_reuse_shadow
Consumers: ContextSelectionMetadata diagnostics, stage receipt, future canary analysis
Lifecycle: runtime-only / derived assembly snapshot
Control impact: none (diagnostic only; strict_sources controls raising separately)
Existing contracts reviewed: ContextSelectionMetadata, ContextCompactionAttempt,
  ContextCompactionReuseAdmission, ContextCompactionBinding, DurableArtifactReference
Decision: extend ContextSelectionMetadata with an owned nested diagnostic value
Why no duplicate source of truth is created: the diagnostic records only failure class and
  exception type; prompt context, artifact identity, and source lineage remain authoritative
  in the existing assembly/binding contracts
Serialization and migration: optional empty list; historical receipts remain readable
Tests: metadata construction/round-trip, invalid combinations, builder exception/empty/malformed
  paths, checkpoint malformed checksum, prompt/request invariants
Documentation updates: CONTRACT_CATALOG.md, API.md, H8-R2BB result, IMPLEMENTATION_LOG.md
```

## 实施顺序

1. 先新增 tracked negative tests，固定 exception / empty / malformed / invalid artifact 期望；
2. 更新 metadata inventory、实现 nested failure value 与 admission validator；
3. 在 builder 和 checkpoint provider 加入最小 fail-closed telemetry；
4. 运行 focused、H8-R2BB stage、compileall、body/secret scan、`git diff --check`，并单独
   记录 full collection 的既有缺失文件 blocker；
5. 独立只读验收后，写 result 与 implementation log；不创建 commit。

## 通过标准

- 四类 shadow failure 均有 typed reason，receipt 不含正文、异常 message 或 credential；
- malformed artifact 不再静默丢失，得到 `artifact_contract_invalid` rejection；
- admitted admission 必须是 `context_compaction` 且有 summary fingerprint；
- non-strict fallback 的 prompt、selected IDs、request hash 不变；strict 仍抛
  `ContextSourceError("context_compaction_reuse", ...)`；
- focused 与 stage gates 全通过，full gate 只如实记录历史 blocker；
- 不把 telemetry acceptance 宣称为 prompt-use、canary 或 accepted commit。

## 非目标

- 不在本阶段持久化 required/recent/session/ledger guard provenance；
- 不实现 artifact body loader 或 checksum re-read；
- 不开启 reusable summary、真实 provider、prompt-use 或 default-on；
- 不修复与本阶段无关的全量测试缺失文件，不整理 dirty tree。
