# Phase H8-R2BL：Read-only provider confirmation matrix 计划

## 背景

H8-R2BK 已经完成一个真实 DeepSeek read-only paired canary：同一 discovered
persisted binding 链路下，raw/reusable 两臂都覆盖确定性事实，且真实 provider
prompt/total token 明显下降。但 BK 仍是单点场景。进入 mutation/tool-task shadow
前，需要确认该链路在多个只读事实契约下稳定，而不是被一个 prompt 偶然放大收益。

## 目标

1. 新增 H8-R2BL read-only confirmation matrix。
2. 使用 3 个确定性只读 fact-contract 场景：
   - scope/validation；
   - permission/tool-boundary；
   - completion/evidence-boundary。
3. 每个场景都走完整链路：
   - raw `MemoryContextBuilder` context；
   - compact `MemoryContextBuilder` context + persisted binding；
   - checkpoint discovery admission；
   - reusable preflight；
   - token-aware simulation；
   - explicit harness opt-in raw/reusable provider projections。
4. 真实 provider 每个场景两臂，总计 6 次只读 JSON 请求。
5. Receipt 只记录 body-free telemetry：
   - prompt/projection hashes；
   - provider usage/finish reason/response hash；
   - fact coverage booleans；
   - side-effect counters；
   - credential serialization guard。

## 非目标

- 不启用 production prompt-use。
- 不修改 `MemoryContextBuilder` 默认输出。
- 不把 `ContextCompactionReuseAdmission.used_in_prompt` 改为 true。
- 不调用工具、不写文件、不运行命令。
- 不声明 mutation/tool-task 收益。
- 不保存 prompt/source/summary/response 正文或 provider key。
- 不新增 public metadata contract。

## Metadata impact note

Fact:
Discovered persisted binding + default-off reusable projection can preserve
read-only fact coverage across a small scenario matrix while reducing real
provider prompt tokens.

Authoritative producer:
`MemoryContextBuilder` remains the producer of raw candidates and compact
bindings; checkpoint discovery remains a shadow-admission producer; BL harness
only owns experiment receipt evidence.

Consumers:
H8-R2BL receipt and the next mutation/tool-task shadow decision.

Lifecycle:
Experiment-only. No durable production lifecycle change.

Control impact:
Provider calls are read-only, JSON-only, no-tool requests. No project, memory,
writer or command authority is granted.

Existing contracts reviewed:
`ContextCompactionBinding`, `ContextCompactionReuseAdmission`,
`ContextAssemblyPolicy`, `ContextAssembler`, reusable preflight/simulation,
`LLMRequest`, `LLMResponse`, `ReasoningPolicy`。

Decision:
Add experiment harness and focused tests only. Reuse BK/BJ helpers where
possible. Do not change metadata contracts or builder production prompt
assembly.

Why no duplicate source of truth is created:
The fact contracts are experiment-local oracles. Receipt stores only fact IDs,
contract hashes and boolean coverage, not source facts or response bodies.

Tests:
Mock-provider matrix pass, body-free receipt scan, missing-settings blocked
envelope.

Documentation updates:
H8-R2BL result doc, evidence index, completion audit, implementation log.

## 实施计划

1. Add `stage_h8r2bl_read_only_confirmation_matrix.py`.
2. Define three deterministic matrix cases with compactable dialog facts and
   low-value history.
3. For each case, build raw/compact contexts and run discovery + preflight +
   simulation.
4. If every setup gate passes and credentials are present, run 6 read-only
   provider calls.
5. Aggregate per-case token deltas and quality booleans.
6. Add focused tests with an injectable mock client.
7. Run focused tests and one real DeepSeek matrix if the current environment has
   credentials.
8. Record result and safety boundaries.

## 通过标准

- All 3 cases produce exactly one compact binding.
- Discovery admits each binding and remains shadow-only.
- Preflight and simulation pass for every case.
- Each raw/reusable provider arm has complete usage and finish reason.
- Reusable prompt tokens are lower than raw prompt tokens in every case.
- Required deterministic fact coverage passes in both arms for every case.
- Side effects are limited to provider calls.
- Receipt body/secret scan passes.

## 阻塞/降级语义

- Missing credential: typed blocked receipt, zero provider calls.
- Setup/preflight/simulation failure: needs-followup receipt, zero provider
  calls.
- Provider/network failure: safe error type only, no error text/body.
- Any quality miss or non-reduced prompt tokens: matrix does not pass.
