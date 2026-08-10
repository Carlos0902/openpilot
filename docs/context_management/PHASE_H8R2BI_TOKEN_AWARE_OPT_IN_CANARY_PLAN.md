# Phase H8-R2BI：Token-aware builder-adjacent opt-in canary 计划

## 背景

H8-R2BH 已经证明 `MemoryContextBuilder.build()` 的真实 `selected_context_candidates` 可以进入
H8-R2BF preflight + H8-R2BG simulation 链路，且不改变 builder prompt 或 context compactions。但 BH
仍只记录 character-level delta。进入真实收益实验前，需要把同一条 default-off 链路推进到 token-level
accounting：raw assembly 和 reusable assembly 必须通过同一个 token counter、同一个 typed policy 计数，
并输出 body-free token delta。

## 目标

1. 扩展 runtime-only simulation helper，使其在显式提供 token counter 且 policy 启用 token budget 时记录：
   - raw/reusable final prompt tokens；
   - prompt token delta；
   - tokenizer id、model、token count method。
2. 新增 builder-adjacent opt-in canary harness：
   - 仍先正常运行 `MemoryContextBuilder.build()`；
   - 从 builder-selected candidates 构造 in-memory reusable binding；
   - 显式 opt-in token policy；
   - 运行 preflight + token-aware simulation；
   - 写 body-free receipt。
3. 验证 token delta 与 char delta 同向为正，同时 required/recent/source governance 不变。
4. 不改 builder 默认 production prompt，不启用 default-on。

## 非目标

- 不调用 provider。
- 不安装或下载 tokenizer。
- 不声称真实 billing token 或 provider request usage。
- 不读取 artifact store body。
- 不接 production artifact discovery。
- 不修改 public metadata contract。

## Metadata impact note

Fact:
Reusable prompt-use simulation can carry token-accounting evidence when an explicit token counter and token budget are
provided.

Authoritative producer:
Memory-layer runtime simulation helper invoked by an explicit default-off canary.

Consumers:
H8-R2BI receipt, future real-provider canary design.

Lifecycle:
Runtime-only / experiment evidence. No durable metadata lifecycle in this phase.

Control impact:
None in production. Token evidence informs future canary admission but does not change prompt selection.

Existing contracts reviewed:
`ContextAssembler` token mode, `ContextSelectionMetadata` token fields, `ProviderTokenCounter` protocol,
`ReusableCompactionPromptUseSimulation`, `MemoryContextBuilder.build()` selected candidate projection.

Decision:
Extend the existing runtime-only simulation model/helper with optional token fields. This is not a public metadata
contract change because the helper is not exported as authoritative runtime metadata.

Why no duplicate source of truth is created:
Token counts are derived from the same raw/reusable assembly results and token counter. The builder prompt remains the
authoritative build output.

Tests:
Unit test for token-aware simulation; experiment test for builder-adjacent opt-in token receipt and body-free scan.

Documentation updates:
H8-R2BI result doc, evidence index, completion audit, implementation log, and API/catalog boundary note if needed.

## 实施计划

1. Add optional token fields to `ReusableCompactionPromptUseSimulation`.
2. Add `token_counter` parameter to `simulate_reusable_compaction_prompt_use(...)`; pass it into `ContextAssembler`.
3. Validate passed token-aware simulations when token counts are present:
   - raw/reusable prompt token counts are present；
   - token delta is positive；
   - tokenizer metadata is present。
4. Add tests in `Code/tests/test_compaction_reuse.py`.
5. Add `stage_h8r2bi_token_aware_opt_in_canary.py` and focused test.
6. Run focused, metadata/context, adjacent, compile/diff/body scans.

## 通过标准

- H8-R2BI canary status is `passed`.
- Character and token deltas are both positive.
- Required/recent/source replacement invariants still pass.
- Builder output remains unmodified.
- Receipt is body/secret-free.
