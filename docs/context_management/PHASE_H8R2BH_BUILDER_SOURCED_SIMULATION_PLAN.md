# Phase H8-R2BH：Builder-sourced reusable prompt-use simulation 计划

## 背景

H8-R2BG 已经证明 runtime-only simulation helper 可以在手写 typed candidate fixture 上比较 raw
assembly 与 reusable projection，并要求 source candidates 被 exact governed replacement。但它还没有
证明 `MemoryContextBuilder` 真实构造出的 `selected_context_candidates` 形态也能进入同一条 preflight +
simulation 链路。

下一阶段需要把 simulation 的输入来源从手写 fixture 推进到 builder 输出，同时仍保持 default-off：
builder 先正常 `build()`，随后实验 harness 从 builder 已选择的 typed candidates 中选取可压缩的非 required
dialog sources，构造一个 in-memory reusable binding，运行 H8-R2BF preflight 和 H8-R2BG simulation。

## 目标

1. 新增 builder-sourced offline canary harness。
2. Canary 必须使用 `MemoryContextBuilder.build()` 的真实 `selected_context_candidates` 作为 simulation
   输入。
3. Canary 必须证明：
   - builder 原始 prompt hash 不变；
   - selected candidate IDs 来自 builder 输出；
   - reusable projection exact governed-replaces 指定 non-required dialog sources；
   - required system/instruction candidate 和 recent suffix 仍保留；
   - reusable assembly 相比 raw builder-sourced assembly 有正 prompt-char delta；
   - receipt 不包含 prompt/source/summary 正文或 secrets。
4. 不改 `MemoryContextBuilder` 默认输出，不启用 production prompt-use。

## 非目标

- 不接 artifact store discovery。
- 不读取 durable artifact body。
- 不调用 provider 或 LLM semantic judge。
- 不修改 public metadata contract。
- 不声明真实任务 token/billing/net benefit。
- 不让 reusable summary 进入生产 prompt。

## Metadata impact note

Fact:
The existing builder-selected typed candidates can feed the reusable prompt-use preflight/simulation pipeline without
changing the builder prompt output.

Authoritative producer:
Experiment harness that consumes `MemoryContextBuilder.build()` output.

Consumers:
H8-R2BH receipt, future default-off production canary design.

Lifecycle:
Experiment-only derived evidence. No durable metadata lifecycle in this phase.

Control impact:
None in production. The harness observes builder output and runs an external simulation only.

Existing contracts reviewed:
`MemoryContextBuilder.build()`, `selected_context_candidates`, `ContextCandidate`,
`ContextCompactionBinding`, `ContextAssemblyPolicy`, `preflight_reusable_compaction_prompt_use(...)`,
`simulate_reusable_compaction_prompt_use(...)`, `ContextAssembler` atomic compaction governance.

Decision:
Add experiment harness and tests only. Do not add a builder flag or production prompt-use path in this phase.

Why no duplicate source of truth is created:
The builder prompt remains authoritative for the build result. The simulation result stores only hashes, sizes and IDs
derived from the builder output.

Tests:
Pass case from builder-selected candidates; reject if builder lacks required/recent/source shape; body-free receipt scan.

Documentation updates:
H8-R2BH result doc, evidence index, completion audit, implementation log.

## 实施计划

1. Add `stage_h8r2bh_builder_sourced_simulation.py`.
2. Build a deterministic short-memory conversation through `MemoryContextBuilder`.
3. Parse `selected_context_candidates` back into `ContextCandidate` values.
4. Construct a body-free source-binding hash and in-memory `ContextCompactionBinding` over selected non-required dialog
   source candidates.
5. Run BF preflight and BG simulation using the builder renderer.
6. Write a body-free receipt with builder prompt hash, raw/reusable prompt hashes, char delta, selected/replaced IDs and
   side-effect counters.
7. Add focused experiment test and run adjacent gates.

## 通过标准

- Builder output is ready and unchanged by the canary.
- Simulation status is `passed`.
- Source replacements equal the selected source IDs.
- Required and recent IDs are retained.
- Prompt-char delta is positive.
- Receipt contains no prompt/source/summary body and no secrets.
