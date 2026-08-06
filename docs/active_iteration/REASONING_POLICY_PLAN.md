# Provider-Neutral Reasoning Policy and Experiment Plan

## Goal

Control model reasoning as a typed, provider-capability-resolved request policy,
without coupling Controller decisions to DeepSeek-specific fields. Bind the
effective policy to provider transport, cache, trajectory, and recovery identity,
then use fixed trajectories to decide whether dynamic Controller routing is safe
and worthwhile.

## Why this phase exists

The observed Controller failures are not explained by prompt size alone. Routine
decisions consumed most of a 2,000-token completion allowance as hidden reasoning,
returned empty or truncated visible JSON, and then entered fallbacks that changed
the task or validation command. A completion ceiling cannot distinguish useful
deliberation from reasoning that crowds out the required narrow schema. The
system therefore needs a separate reasoning policy and measured escalation rule;
it must not infer reasoning behavior from a model name inside business modules.

## Metadata impact note

Authoritative facts:

- the caller's requested reasoning intent;
- the provider/model capability profile selected for the request;
- the effective policy and whether it was exact, mapped, clamped, omitted, or
  unsupported;
- the versioned semantic request identity used by cache and recovery.

Existing contracts reviewed: `LLMRequest`, `LLMRequestMetadata`,
`LLMResponseMetadata`, `RuntimeBudgetMetadata`, `PendingLLMRequest`,
`LLMReplayEntry`, `ContextRequestPurpose`, `DifficultyAssessmentMetadata`, and
`LLMSettings`.

Decision:

- Add strict owned values, not a new `MetadataKind`: `ReasoningMode`,
  `ReasoningEffort`, `UnsupportedReasoningBehavior`, `ReasoningResolution`,
  `ReasoningPolicy`, and `ResolvedReasoningPolicy`.
- `LLMRequest.reasoning_policy` owns caller intent and defaults to
  `provider_default`, preserving current provider payload behavior.
- `LLMRequestMetadata` records requested and resolved policy as audit evidence.
  `LLMResponse.usage` remains the sole provider token-usage authority.
- `RuntimeBudgetMetadata` remains the sole completion-budget owner. Reasoning
  policy does not create a second token budget; provider-specific thinking
  budgets, when supported, are a field of the requested/effective policy.
- `PendingLLMRequest` and `LLMReplayEntry` gain a versioned hash identity. Old
  values migrate to `legacy_unbound_v1`; new values are `provider_bound_v2`.
- Do not reuse `DifficultyAssessmentMetadata`: its free-text-oriented problem
  resolution semantics are not an LLM transport routing authority.

Producer/consumer lifecycle:

- Business call owner produces requested policy.
- `core/reasoning.py` selects a versioned capability profile and resolves it.
- `LLMClient` renders only the resolved policy into provider transport fields.
- Cache, trajectory proxy, and checkpoint replay consume the same normalized
  provider/model/profile/effective-policy identity.
- Diagnostics persist policy resolution; they do not control later routing.

No duplicate source of truth is introduced: profiles are versioned capability
configuration, requested policy is caller intent, resolved policy is a derived
request observation, usage is still provider response evidence, and completion
budget remains runtime-owned.

## Architecture

1. `metadata/runtime.py`: strict policy and resolution values plus request-event
   fields; checkpoint-owned hash version stays in `metadata/agent_runtime.py`.
2. `core/reasoning.py`: pure profile registry, resolver, normalized semantic
   identity, and OpenAI-compatible renderer. No Controller or tool logic.
3. `core/config.py`: optional explicit capability-profile ID. Automatic
   selection is allowed only for recognized official endpoint hosts and bounded
   model families; unknown combinations use the generic profile.
4. `core/llm.py`: resolve before cache lookup and transport; default policy adds
   no provider payload field. Explicit unsupported policy follows its typed
   reject/clamp/provider-default behavior.
5. `runtime_diagnostics/llm_proxy.py`: resolve before request event and hash;
   record requested/effective/profile/version and use provider-bound v2 hash.
6. Runtime checkpoint recovery: legacy unbound LLM pending/replay evidence may
   not silently claim exact replay. It fails closed or requires a new linked run;
   non-LLM legacy checkpoints remain readable.

## Initial capability profiles

- `generic-openai-compatible:v1`: only `provider_default`; explicit controls are
  rejected or deliberately omitted according to typed policy.
- `openai-chat-known:v1`: only documented official OpenAI model families and
  supported effort values; renderer uses the documented Chat Completions field.
- `deepseek-chat-known:v1`: official DeepSeek endpoint/model combinations;
  thinking on/off and supported strength are mapped explicitly. A low/medium
  request may never be reported as exact if the provider only exposes a coarser
  control.

Anthropic-native and Gemini-native transports are out of scope because this
repository currently implements only OpenAI-compatible Chat Completions. They
may be represented as typed unsupported future profiles, but must not be claimed
as working adapters. An OpenAI-compatible Gemini profile is added only if the
official compatibility endpoint and exact fields are verified and tested.

## Test-driven implementation order

### Phase 2A: contracts and resolver, default behavior unchanged

1. Add failing serialization, validation, enum, legacy migration, and extra-
   forbid tests.
2. Add failing profile-selection and exact/map/clamp/reject tests.
3. Implement strict policy models and pure resolver.
4. Prove a default request produces the same provider payload as before.

Exit gate: unknown providers do not guess support; every explicit resolution is
auditable; existing callers need no edits and provider-default payload snapshots
are unchanged.

### Phase 2B: transport, cache, trajectory, and recovery binding

1. Add failing OpenAI/DeepSeek renderer snapshots and unsupported-policy tests.
2. Add failing cache tests proving provider, model, transport, profile version,
   and effective policy cannot cross-hit.
3. Add failing trajectory/hash tests proving request evidence and provider-bound
   identity use the same resolved policy.
4. Add failing legacy/new checkpoint tests; then implement versioned replay
   identity and fail-closed migration.
5. Preserve actual failed-attempt usage, finish reason, and partial response.

Exit gate: changing provider/model/effective policy/profile version always
changes cache and recovery identity; no legacy unbound LLM replay is advertised
as exact.

### Phase 2C: fixed-trajectory provider experiment

Reuse the hash-locked `20260803T200118Z` Controller requests:

1. read-only inspection: must choose read and must not mutate;
2. modify divide: must return a complete narrow edit plan;
3. run pytest: must preserve the exact validation command and completion
   evidence;
4. one added multi-branch/conflicting-evidence sample where deeper reasoning is
   expected to help.

Freeze provider/model/endpoint fingerprint, profile/version, prompt and schema
hashes, temperature, completion ceiling, cache=false, repetitions, and judge
version.

Arms are sequential to preserve attribution:

- A: `provider_default`, completion ceiling 2,000;
- B: routine economical/disabled policy, same ceiling;
- C: only if B passes quality, lower completion ceiling 1,200 then 800;
- D: complex positive sample, economical versus adaptive/high.

Non-compensating safety/quality gates are evaluated before cost: schema/JSON
complete, exact action and command, read-only mutation zero, permission boundary
violations zero, suspicious success zero, and no incorrect fallback substitution.
Any critical violation rejects an arm. Only after quality passes compare input,
visible output, total output, reasoning tokens, reasoning share, fallback rate,
latency, and tokens per valid decision. A small fixed sample is mechanism
screening, not a population-level benefit claim.

### Phase 2D: Controller routing only after experimental evidence

First integrate only the tool-event decision owner:

- explicit single read, exact validation, or one known action -> routine policy;
- evidence conflict, multiple writable targets, recovery choice, or ambiguous
  permission boundary -> deliberative/high policy;
- `max` remains explicit long-range planning only;
- truncated JSON first retries once with a narrower schema and economical policy,
  not a full-plan regeneration or automatic reasoning escalation;
- fallback inherits root execution mode, task kind, requested validation command,
  and completion evidence requirements.

If decision complexity becomes a routing control value, add a strict owned enum
and reason codes; never parse it from prompt prose or `trace_info`.

### Phase 2E: full-architecture A/B

Run matched baseline/treatment strata for calculator repair, clean success, and a
longer recovery task. Compare verified task success, false success/permission
violations, exact validation, fallback rate, total and Controller tokens, calls,
and latency. Only this stage can support a system-level Token/quality claim.

## Documentation and acceptance

Update `API.md`, metadata catalog, `.env.example` only if a profile override is
added, runtime-recovery docs, task-trajectory alignment, testing guide, and
implementation log. Final acceptance requires offline full regression,
provider experiment artifacts with complete usage coverage, no safety
regression, and explicit remaining limitations.

## Primary references

- OpenAI reasoning controls: <https://developers.openai.com/api/docs/guides/latest-model>
- Anthropic effort semantics (architecture comparison only):
  <https://platform.claude.com/docs/en/build-with-claude/effort>
- Gemini thinking semantics (architecture comparison only):
  <https://ai.google.dev/gemini-api/docs/generate-content/thinking>
- DeepSeek thinking mode: <https://api-docs.deepseek.com/guides/thinking_mode>

## Phase 2E result

Implemented the provider-neutral contracts, versioned resolver, transport
renderers, provider-bound recovery identity, typed routine routing, and
purpose-aware fail-closed plan filtering. The fixed screens selected `disabled`
only for typed routine decisions, retained `provider_default` for ambiguous and
multi-write work, rejected the 800-token ceiling, and found no evidence for
automatic `high` routing.

The final single paired full-architecture pilot preserved exact `.venv` pytest
and compileall verification in both arms. Routine-disabled reduced tool-event
output 85.08%, tool-event total tokens 40.94%, and tool-event duration 68.64%; it
removed the baseline pair's one reasoning-driven length failure. Full observed
lifecycle tokens fell 27.88%. This is mechanism evidence from one pair, not a
distributional causal estimate. Late-run input amplification and optional
improvement output remain separate context-management work. Full details and
hashes are in `experiments/full_architecture_context_observation/REASONING_POLICY_AB_RESULT_V1.md`.
