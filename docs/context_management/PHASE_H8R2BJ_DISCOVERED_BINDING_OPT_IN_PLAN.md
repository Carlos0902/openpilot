# Phase H8-R2BJ：Discovered persisted binding opt-in canary 计划

## 背景

H8-R2BI 已经证明 builder-sourced candidates 可以进入 token-aware preflight + simulation 链路，但 reusable
binding 仍由 harness 临时构造。进入真实收益实验前，需要证明 opt-in arm 可以消费已经由
`MemoryContextBuilder` 真实 compact/sink 产生、并通过 checkpoint discovery 发现的
`ContextCompactionBinding`。

## 目标

1. 新增 discovered/persisted binding opt-in canary。
2. Canary 使用同一份 `ShortMemory` 产生两条 builder 输出：
   - high-budget raw builder：提供完整 `selected_context_candidates`；
   - low-budget compact builder：通过真实 compaction sink 产生 `context_compactions` binding。
3. 从 compact builder 的 prompt-context snapshot 通过
   `build_checkpoint_compaction_reuse_shadow_provider(...)` 发现并 admit persisted binding。
4. 使用 discovered admission + persisted binding + raw builder candidates 运行：
   - H8-R2BF preflight；
   - H8-R2BI token-aware simulation。
5. Receipt 记录 body-free source/binding/admission/token evidence。

## 非目标

- 不把 reusable summary 接入生产 `MemoryContextBuilder` prompt。
- 不启用 default-on。
- 不调用 provider。
- 不读取 artifact store body。
- 不声明 provider billing/token benefit。
- 不修改 public metadata contract。

## Metadata impact note

Fact:
A `ContextCompactionBinding` produced by `MemoryContextBuilder` and rediscovered from checkpoint lineage can feed the
default-off opt-in preflight/simulation/token-accounting path.

Authoritative producer:
`MemoryContextBuilder` compaction sink produces the binding; checkpoint discovery produces shadow admission.

Consumers:
H8-R2BJ receipt and future production-adjacent opt-in prompt-use design.

Lifecycle:
Experiment-only evidence. No new durable metadata lifecycle in this phase.

Control impact:
None in production. The canary only observes builder outputs and runs an external opt-in simulation.

Existing contracts reviewed:
`ContextCompactionBinding.source_binding_hash`, `RuntimePromptContextSnapshot.compaction_bindings`,
`build_checkpoint_compaction_reuse_shadow_provider(...)`,
`preflight_reusable_compaction_prompt_use(...)`,
`simulate_reusable_compaction_prompt_use(...)`, `ContextAssembler` atomic compaction governance.

Decision:
Add experiment harness and tests only. Reuse existing discovery/admission/preflight/simulation helpers; do not add a
builder flag or production prompt mutation path.

Why no duplicate source of truth is created:
The compact builder binding remains the source of reusable artifact identity. The raw builder candidates remain source
evidence for opt-in simulation. The receipt stores hashes, IDs, sizes and token counts only.

Tests:
Canary pass case; body-free receipt scan; adjacent reuse-chain regression.

Documentation updates:
H8-R2BJ result doc, evidence index, completion audit, implementation log.

## 实施计划

1. Add `stage_h8r2bj_discovered_binding_opt_in.py`.
2. Seed deterministic short memory.
3. Build high-budget raw context and validate selected candidates.
4. Build low-budget compact context with a real compaction sink and validate persisted `source_binding_hash`.
5. Build checkpoint snapshot from compact context.
6. Run checkpoint discovery provider against raw candidate digests to obtain admitted shadow evidence.
7. Run preflight + token-aware simulation using the discovered admission and persisted binding.
8. Write body-free receipt and focused test.

## 通过标准

- Compact builder produces exactly one persisted binding with `source_binding_hash`.
- Discovery admission is `admitted` and `used_in_prompt=false`.
- Preflight and simulation pass.
- Source replacement, required/recent retention, char delta and token delta are positive/true.
- Raw/compact builder production prompts remain unmodified by the opt-in simulation.
- Receipt contains no prompt/source/summary body or secrets.
