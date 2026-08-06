# Phase 10 Plan: Context Governance Enhancements

## Status

Stage 0–5 implementation and offline validation are complete. No real-provider
canary or global default change is enabled by this plan.

## Objective

Extend the existing context boundary with three independently testable
capabilities:

1. LLM-assisted segmented compaction using bounded rolling summaries;
2. a narrower, explicit boundary for promoting conversation statements into
   session-scoped required constraints;
3. provider-neutral reasoning intent resolution backed by versioned capability
   profiles.

The work must preserve the current source-of-truth rules:

- raw dialog, task contracts, verification plans, project state, and runtime
  permissions remain authoritative at their existing owners;
- summaries and prompt projections are derived views only;
- a compact replacement and the source decisions it replaces are selected and
  verified atomically;
- unknown provider capability, invalid summary output, unknown usage, or
  incomplete evidence fails closed;
- Compact, constraint extraction, and reasoning changes remain independently
  switchable and independently attributable in experiments.

## Current completion boundary

Stages 0–5 are implemented and green in offline validation. Production
defaults remain deterministic/current Compact, generic provider-default
reasoning, and explicit-confirmation session constraints. A future
real-provider canary is intentionally separate from this implementation goal
and must first add complete attempt telemetry, semantic summary scoring, Agent
Generator ingress policy, and API/acceptance verification evidence.

## Stage order

### Stage 0 — Inventory, external comparison, and contract freeze

Before behavior changes, inspect the existing compaction, session constraint,
and reasoning contracts, their producers/consumers, persistence boundaries,
tests, and current experiments. Compare the proposed behavior with the official
OpenAI Agents SDK, Anthropic Claude Code, Anthropic thinking, Gemini thinking,
and LangGraph patterns. Record the current gaps and freeze the acceptance
boundaries in this plan and the implementation log.

Exit gate:

- no unresolved source-of-truth or ownership ambiguity;
- closest existing contracts and reuse/extension decisions are recorded;
- each later stage has explicit behavior, tests, and rollback criteria;
- no production feature flag is changed.

### Stage 1 — LLM-assisted compaction contract and offline quality corpus

Define the smallest extension of the existing compaction record/binding and
selection evidence needed for a generated rolling summary. The summary is
allowed to cover only selected, non-required old observations. Required
constraints, current task facts, security fields, verification commands, and
the recent dialog suffix remain outside the summary authority.

The summary contract must specify:

- source candidate IDs, source fingerprint, and algorithm/schema version;
- eligible source kinds and excluded source kinds;
- a narrow structured summary shape (goal delta, verified facts, decisions,
  open issues, evidence IDs, and next action);
- a hard summary output ceiling and a dynamic remaining-budget calculation;
- provider attempt usage, finish reason, and validation outcome;
- quality checks and deterministic source-view fallback.

Build a deterministic/fake-provider corpus before real Provider traffic. Cover
short, medium, and long histories; strongly related, partially related, and
irrelevant old observations; and the Goal, Task Designer, Code Generation, and
Bugfix purposes.

Exit gate:

- summary output is bounded before and after generation;
- required constraints and execution facts are never summary-authored;
- invalid, overlong, or unverifiable summaries fall back to the source view;
- source lineage and replay hashes are stable;
- the quality corpus passes with zero provider/network/mutation side effects.

### Stage 2 — Rolling summary implementation and shadow integration

Implement a feature-flagged LLM summary compactor on top of the existing
segmented/artifact compaction path. Use incremental summaries (previous
validated summary plus newly eligible old segments) rather than re-summarizing
the entire session every time. The final model-facing projection remains:

```text
required typed state
+ validated compact artifact(s)
+ recent verbatim suffix
+ current task/instruction/schema
```

The generated summary must be a preferred derived candidate, never a new
authority. If it cannot be selected atomically, the assembler must restore the
governed source view. Summary calls have their own purpose, reservation, and
telemetry; they cannot silently consume the main task completion budget.

Exit gate:

- offline replay and checkpoint recovery pass;
- the compactor is no-op when there is no eligible segment or no budget gain;
- compact/current projections remain identical in required state and
  permissions;
- the feature flag is off by default and Current remains a kill-switch fallback.

### Stage 3 — Session constraint extraction boundary

Extend only the existing session-scoped proposal/reducer path. Do not create a
memory record or a second task/verification authority. Define the promotion
allowlist, confirmation semantics, conflict/supersession rules, bounded active
state, and session/run lifetime.

Only explicit, stable, scoped user directives or already typed task-authority
facts may become proposals. LLM extraction may improve recall only as an
untrusted proposal; it cannot activate a constraint. Vague prose, assistant
output, summaries, and compact artifacts remain evidence, not authority.

Exit gate:

- proposal, confirmation, rejection, revocation, and supersession transitions
  are deterministic;
- every active entry is source/hash linked and session scoped;
- active state has bounded count/size and cannot grow with ordinary history;
- cross-session, assistant-origin, and ambiguous-scope attempts fail closed;
- production multi-turn ingress and checkpoint/replay tests pass.

### Stage 4 — Provider-neutral reasoning profiles

Keep business modules selecting a typed reasoning intent or decision complexity.
Resolve that intent through a versioned provider/model capability profile and a
provider-specific transport adapter. Do not infer capabilities from model-name
substrings or from observed output. Unsupported explicit controls must follow a
typed fallback/rejection policy and record the requested, resolved, transport,
and observed values separately.

Support the first provider profiles required by real deployments, while keeping
the generic intent layer provider-neutral. Completion budget, context budget,
and reasoning budget remain separate accounting dimensions.

Exit gate:

- profile selection is explicit or configured, never guessed from free text;
- supported and unsupported controls have contract tests;
- OpenAI/Anthropic/Gemini-compatible mappings and a no-control fallback are
  covered without coupling business modules to provider payloads;
- reasoning experiments can run without changing Compact or completion policy.

### Stage 5 — Independent paired canary and total acceptance

Run separate paired experiments for summary compaction, constraint extraction,
and reasoning. Use one immutable source envelope per pair, interleave Current
and treatment arms, keep feature flags and kill switches armed, and account for
every Provider attempt including failures and recovery attempts.

Measure input, output, reasoning, total tokens, call count, latency,
constraint recall, task/schema quality, verification success, provenance,
mutation drift, and fallback/stop reasons. Expand only the purpose and task
families whose gates pass; do not switch the global default based on one
provider or one fixture.

## Metadata impact notes

### Compaction summary

```text
Fact: validated rolling summary of selected old non-required observations
Authoritative producer: compaction pipeline after provider response validation
Consumers: ContextAssembler, derived projection bridge, replay/trajectory audit
Lifecycle: artifact plus request-scoped derived view
Control impact: budget, recovery, evidence; never permission or task authority
Existing contracts reviewed: ContextCandidate, ContextCompactionRecord,
  ContextCompactionBinding, DurableArtifactReference, RuntimePromptContextSnapshot
Decision: extend existing compaction record/binding and derived view; no new
  MetadataKind unless Stage 1 proves an independent lifecycle is required
Why no duplicate source of truth is created: raw candidates/artifacts remain
  authoritative and the summary stores source IDs and fingerprints
Serialization and migration: preserve historical deterministic algorithms;
  add versioned optional fields with historical-read defaults
Tests: strict schema, budget, atomic replacement, invalid-output fallback,
  source mutation, checkpoint/replay, quality corpus
Documentation updates: context plan, metadata catalog/API if public fields
  change, implementation log, experiment protocol and result evidence
```

### Session constraints

```text
Fact: explicit user-confirmed session constraint proposal and active ledger entry
Authoritative producer: SessionIngress/reducer from user-origin source turns
Consumers: runtime guards, ContextAssembler required projection, checkpoint/replay
Lifecycle: runtime session state plus checkpoint snapshot
Control impact: routing, permission, verification, and context retention
Existing contracts reviewed: SessionConstraintState/Entry/Proposal/Value,
  SessionIngressState, RuntimeStateMetadata, TaskGraphNodeMetadata,
  VerificationPlanMetadata
Decision: extend existing SessionConstraintState and reducer; no memory record
  and no duplicate task/verification authority
Why no duplicate source of truth is created: entries remain source-linked and
  narrow; task and verification contracts retain execution ownership
Serialization and migration: bounded optional nested fields; old checkpoints
  remain readable; invalid session/project/cursor combinations fail closed
Tests: extraction allowlist, confirmation, conflict, revoke, quota, scope,
  cross-session rejection, prompt projection, checkpoint/replay, runtime gates
Documentation updates: phase plan, catalog/API if fields change, implementation log
```

### Reasoning profile

```text
Fact: resolved provider transport for a typed reasoning intent
Authoritative producer: core/reasoning resolver plus configured capability profile
Consumers: LLM request builder, provider transport, diagnostics/replay
Lifecycle: request metadata and attempt evidence
Control impact: routing and budget accounting; never file permissions
Existing contracts reviewed: ReasoningPolicy, ResolvedReasoningPolicy,
  ReasoningCapabilityProfileId, LLMRequestMetadata, LLMResponseMetadata
Decision: extend existing reasoning policy/profile contracts and adapters
Why no duplicate source of truth is created: intent remains business-neutral,
  profile owns provider mapping, and observed usage remains provider evidence
Serialization and migration: versioned profiles; unknown profiles fail closed or
  follow an explicit provider-default policy
Tests: profile selection, supported/unsupported controls, transport rendering,
  requested/resolved/observed telemetry, replay hash, provider-neutral A/B
Documentation updates: API/catalog if fields change, reasoning plan, log, tests
```

## Global rollback and non-goals

- No global Compact default switch in this phase.
- No automatic promotion of summaries or assistant text to execution authority.
- No generalized memory graph, embedding store, or cross-session constraint memory.
- No simultaneous change to Compact, reasoning, and completion policy in one
  causal experiment.
- Any failed quality, permission, provenance, usage, or mutation gate disables
  the treatment arm and preserves the Current path.

## Stage 0 audit findings

The Stage 0 read-only audit is complete. The following findings are binding
inputs to the later stages.

### Compaction

- The production path already performs typed selection, deterministic dialog
  extraction/observation masking, artifact binding, source fingerprints, recent
  suffix preservation, and atomic source fallback.
- `ContextCompactionRecord` and `ContextCompactionBinding` are the closest
  existing contracts. A new MetadataKind is not justified at this point.
- `Code/src/memory/context_compressor.py` is a legacy LLM summarizer with no
  production caller. It uses approximate character/token limits, free-form
  output, whole-conversation input, and catch-all fallback. It must not be
  connected to production directly.
- The current offline segmented result is synthetic and character-based; the
  first full-session real canary showed only a small Task Designer input
  reduction. These are mechanism signals, not general quality evidence.
- The context README stage table is behind the implementation log, and the
  repository harness has known frozen snapshot/fixture mismatches. Baseline
  refresh is required before a new canary.

### Session constraints

- The existing state, required projection, runtime gates, checkpoint/replay
  binding, and user-only proposal path are sound foundations.
- The interactive CLI does not currently route `/confirm`, `/reject`, and
  `/revoke` through the constraint handler in all cases.
- The `agent_generator` route can bypass the ingress/context path and therefore
  needs either the same ingress projection or an explicit exclusion/admission
  contract.
- API compatibility and goal acceptance are currently represented in the
  typed state/prompt but are not complete runtime verification gates.
- Confirmation/revocation evidence, stale proposal supersession, and bounded
  pending/tombstone/required-state quotas need explicit contracts.

### Reasoning

- The existing typed policy/resolver/profile/telemetry foundation is useful,
  but auto-selection still infers capabilities from model/endpoint prefixes,
  which conflicts with the project convention that capability must not be
  inferred from model-name substrings.
- Only OpenAI-compatible and DeepSeek transport rendering exists today;
  Anthropic/Gemini native support must not be claimed until an actual adapter
  and snapshot contract exist.
- Profile versions, token-budget mappings, transport resolution, and normalized
  observed reasoning usage need stricter typed evidence.
- STANDARD and COMPLEX routing remains intentionally conservative and must stay
  frozen while compaction work is evaluated.

## Stage 0 evidence and decision

The audit did not change runtime behavior or invoke a Provider. Stage 1 may
start only after the baseline documentation/snapshot mismatch is recorded and
the Stage 1 compaction quality plan is written as a separate reviewed change.
