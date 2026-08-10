# Phase H8-R2BK：Real-provider read-only paired canary 计划

## 背景

H8-R2BJ 已经证明 `MemoryContextBuilder` 真实产生的 persisted
`ContextCompactionBinding` 可以经 checkpoint discovery 进入
default-off preflight + token-aware simulation 链路。但此前仍停留在离线
token accounting，没有真实 provider usage、finish reason 或答案质量信号。

本阶段进入一个最小真实 provider canary：同一份 builder/discovery/preflight
链路产生 raw arm 与 reusable arm，只做只读问答，不调用工具、不写文件、不把
reusable summary 接入生产 builder 默认路径。

## 目标

1. 新增 H8-R2BK paired canary harness。
2. 使用同一份 deterministic short-memory fixture 生成：
   - high-budget raw builder context；
   - low-budget compact builder context 与 persisted binding。
3. 通过 checkpoint discovery admit persisted binding。
4. 先运行 preflight + token-aware simulation；只有通过后才构造 provider arms。
5. 对同一只读问题调用真实 provider 两次：
   - raw arm：完整 raw builder prompt；
   - reusable arm：显式 opt-in projection prompt。
6. Receipt 记录 body-free provider telemetry 与质量布尔量：
   - prompt hash/char/token evidence；
   - provider usage/finish reason/response hash；
   - required facts 覆盖；
   - side-effect counters；
   - secret serialization guard。

## 非目标

- 不启用 production prompt-use。
- 不修改 `MemoryContextBuilder` 默认行为。
- 不把 `ContextCompactionReuseAdmission.used_in_prompt` 改为 true。
- 不调用工具、不读写项目文件、不执行命令。
- 不声明 mutation/tool-task 收益。
- 不保存 prompt body、source body、summary body、response body 或 provider key。
- 不新增 public metadata contract。

## Metadata impact note

Fact:
一个 discovered persisted binding 可以在 default-off canary 中生成 provider-facing
reusable projection，并与 raw projection 做真实 provider usage/quality 对比。

Authoritative producer:
`MemoryContextBuilder` 仍是 raw candidates 与 compact binding 的生产者；
checkpoint discovery 仍只生产 shadow admission；BK harness 只生产实验 receipt。

Consumers:
H8-R2BK receipt 与后续 read-only quality / net-benefit gates。

Lifecycle:
Experiment-only。No durable production lifecycle change。

Control impact:
Provider call is explicitly read-only and canary-scoped. No tools, no writer,
no command, no mutation authority.

Existing contracts reviewed:
`ContextCompactionBinding`, `ContextCompactionReuseAdmission`,
`preflight_reusable_compaction_prompt_use(...)`,
`simulate_reusable_compaction_prompt_use(...)`,
`ContextAssembler`, `LLMRequest`, `LLMResponse`, `ReasoningPolicy`。

Decision:
Add experiment harness and focused tests only. Reuse existing public contracts.
Use explicit in-harness opt-in prompt construction after preflight/simulation
passes; do not alter builder production prompt assembly.

Why no duplicate source of truth is created:
Receipt stores only hashes, IDs, sizes, usage, finish reason and boolean quality
facts. Prompt/source/summary/response bodies remain transient and are discarded.

Tests:
Mock-provider paired canary; body-free receipt scan; failure/blocked envelope for
missing credentials or rejected readiness.

Documentation updates:
H8-R2BK result doc after execution, evidence index / audit / implementation log
if the phase passes.

## 实施计划

1. Add `stage_h8r2bk_real_provider_read_only_paired_canary.py`.
2. Build BK deterministic fixture with stable required facts:
   scoped path, forbidden README change, API preservation, pytest command.
3. Reuse BJ discovery/preflight/simulation chain against the fixture.
4. Construct raw and reusable prompts in memory and hash them.
5. Add a minimal paired provider runner with injectable client for tests.
6. Persist body-free receipt with status:
   `passed`, `needs_followup`, or `blocked`.
7. Add focused tests:
   body-free receipt, mock usage delta, quality facts, no provider on failed
   preflight/readiness.
8. Run focused gates, then run one real DeepSeek canary if `DEEPSEEK_API_KEY`
   is present.

## 通过标准

- Discovery admission is admitted and remains `used_in_prompt=false`.
- Preflight and simulation pass before provider calls.
- Raw/reusable provider calls both finish with observed usage and finish reason.
- Reusable arm has lower provider prompt token usage than raw arm.
- Both arms cover required deterministic facts.
- Side effects show provider calls only; no tool/writer/command/project/memory
  mutation.
- Receipt body/secret scan passes.

## 阻塞/降级语义

- Missing credential: write typed blocked/readiness receipt, not a failed quality
  claim.
- Provider/network failure: record safe error type only; do not serialize error
  text.
- Rejected preflight/simulation: no provider call; write needs-followup receipt.
- Quality miss: provider canary does not pass even if token usage decreases.
