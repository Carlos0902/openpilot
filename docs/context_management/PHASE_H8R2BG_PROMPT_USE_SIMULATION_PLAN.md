# Phase H8-R2BG：Reusable compact prompt-use simulation 计划

## 背景

H8-R2BF 已经建立 prompt-use 前置门禁：reusable binding 必须通过 admission、source binding、
artifact integrity、required/recent retention、semantic fact coverage 和 trial assembly 检查。但 BF
仍只是“能不能安全进入”的 dry-run，没有量化 reusable projection 进入 prompt 后相对 raw candidates
的上下文收益。

下一步需要在 default-off/offline 条件下模拟 raw assembly 与 reusable assembly 的差异，开始回答：
进入 prompt 后是否真的缩短上下文，同时 required constraints、recent suffix 和 semantic fact coverage
是否仍被保留。

## 目标

1. 新增 runtime-only prompt-use simulation helper。
2. Simulation 必须要求 H8-R2BF preflight passed。
3. 对同一组 typed candidates 运行两条 assembly：
   - raw assembly：不加入 reusable summary candidate；
   - reusable assembly：加入 summary candidate，并通过 `compacted_candidate_ids` governed replacement
     替换 source candidates。
4. 输出 body-free result：
   - raw/reusable prompt hashes；
   - raw/reusable prompt chars；
   - char delta；
   - raw/reusable selected candidate IDs；
   - replaced source IDs；
   - required/recent retained status；
   - semantic fact IDs。
5. 不改变真实 `MemoryContextBuilder` 输出，不启用 production default-on。

## 非目标

- 不把 reusable summary 接入生产 `MemoryContextBuilder` 默认路径。
- 不调用 provider 或 LLM semantic judge。
- 不读取 artifact store body。
- 不声明真实任务收益或跨 provider 行为。
- 不修改 public metadata contract。

## Metadata impact note

Fact:
A preflight-passed reusable compaction projection can be simulated against the raw candidate assembly to measure
body-free prompt-size delta and retention invariants.

Authoritative producer:
Memory-layer runtime simulation helper invoked explicitly by experiments or future default-off canary code.

Consumers:
H8-R2BG experiment receipt, future reusable prompt-use canary design, audit docs.

Lifecycle:
Runtime-only simulation result / experiment evidence. No durable metadata lifecycle in this phase.

Control impact:
None in production. It informs future prompt-use admission but does not change current prompt selection.

Existing contracts reviewed:
`ReusableCompactionPromptUsePreflight`, `ContextCompactionBinding`, `ContextCandidate`,
`ContextAssemblyPolicy`, `ContextAssembler`, `ContextSelectionMetadata`, `CONTRACT_CATALOG.md`, `API.md`,
`docs/metadata/DEVELOPMENT_CONVENTIONS.md`.

Decision:
Add memory-layer strict runtime helper/models, not a public metadata contract. Keep existing metadata unchanged until a
real prompt-use transition is separately reviewed.

Why no duplicate source of truth is created:
Simulation output is derived from current candidates, one binding and one passed preflight. It records hashes, IDs and
sizes only, does not persist source text or summary text, and does not replace the authoritative context assembly.

Serialization and migration:
No metadata migration. Simulation result is serializable for receipts and excludes prompt/source/summary bodies.

Tests:
Pass case with positive char reduction and retained required/recent IDs; reject non-passed preflight; reject reusable
assembly not ready or source replacement mismatch; body-free receipt scan.

Documentation updates:
H8-R2BG result doc, evidence index, completion audit, implementation log; API/catalog only if contract text needs
clarification.

## 实施计划

1. Extend `Code/src/memory/compaction_reuse.py` with:
   - `ReusableCompactionPromptUseSimulationStatus`
   - `ReusableCompactionPromptUseSimulationRejectionReason`
   - `ReusableCompactionPromptUseSimulation`
   - `simulate_reusable_compaction_prompt_use(...)`
2. Extend `Code/tests/test_compaction_reuse.py` with simulation pass/fail tests.
3. Add H8-R2BG experiment harness:
   - build a deterministic long-source fixture;
   - run preflight;
   - run simulation;
   - write body-free receipt with char delta and invariants.
4. Run focused gates and update evidence docs.

## 通过标准

- Simulation accepts only a passed preflight.
- Reusable assembly keeps required and recent candidates.
- Reusable assembly governed-replaces exactly the compacted source IDs.
- Reusable prompt chars are lower than raw prompt chars in the canary fixture.
- Receipt contains no prompt/source/summary body or secrets.
