# Phase 10 Stage 4 Plan: Provider-Neutral Reasoning Profiles

## Status

Plan and implementation complete. Real-provider observed-usage canary remains
out of scope until the independent Stage 5 gate passes.

## Objective

Keep business modules provider-neutral while allowing real provider transports
to opt into supported reasoning controls. A typed intent is resolved only by an
explicit, versioned capability profile; model-name substrings and observed
output must never be used as capability evidence.

## Findings carried from Stage 0

- `ReasoningPolicy` and `ResolvedReasoningPolicy` already separate intent from
  transport and bind profile information into request/replay metadata.
- Current selection still auto-detects OpenAI/DeepSeek profiles from endpoint and
  model strings, which conflicts with the project development contract.
- Only OpenAI-compatible renderers exist today; native Anthropic/Gemini support
  must not be claimed until their transports are implemented and tested.
- Unknown usage and finish reasons are already retained in parts of the LLM
  attempt path but need an explicit normalized observed-reasoning field.

## Scope and order

### 4A — Explicit profile registry

1. Make profile ID and version an explicit configuration boundary. Unconfigured
   providers resolve to the generic no-control profile.
2. Replace model-prefix capability inference with registry lookup by typed
   configured profile and transport family. Unknown profiles fail closed.
3. Keep historical profile IDs readable and include profile ID/version in the
   request and replay hash.

### 4B — Transport adapters and accounting

1. Preserve generic `ReasoningPolicy` selection in business modules.
2. Render only controls declared by the selected profile; unsupported explicit
   controls follow the typed `provider_default`, `clamp`, or `reject` policy.
3. Keep completion, context, and reasoning budgets as separate dimensions.
4. Normalize observed reasoning usage as nullable evidence: missing provider
   detail remains `unknown`, never zero.

### 4C — Contract fixtures

Add offline fixtures for generic no-control, explicit OpenAI-compatible,
DeepSeek-compatible, and unsupported/native-provider profiles. Do not add a
fake native Anthropic/Gemini transport; document those as unsupported until a
real adapter exists.

## Metadata impact note

```text
Fact: resolved transport controls for one typed reasoning intent
Authoritative producer: core/reasoning resolver plus configured profile registry
Consumers: LLM request builder, provider transport, diagnostics, replay
Lifecycle: request metadata and provider-attempt evidence
Control impact: routing and budget accounting; never file permissions
Existing contracts reviewed: ReasoningPolicy, ResolvedReasoningPolicy,
  ReasoningCapabilityProfileId, LLMRequestMetadata, LLMResponseMetadata
Decision: extend existing profile/resolution contracts and transport renderer;
  no provider-specific fields in business modules
Source-of-truth rule: configured profile declares support; model output cannot
  upgrade capability
Failure policy: absent/unknown profile and unsupported explicit controls fail
  closed or follow the typed fallback policy; observed usage stays nullable
Migration: legacy default remains generic provider-default; explicit profiles
  are opt-in and versioned
Tests: registry, transport mappings, unsupported controls, hashes, observed
  usage, and independent reasoning A/B fixtures
```

## Exit gate

- No production capability path relies on model-name substring inference.
- Explicit profile/version, requested policy, resolved policy, rendered transport,
  and observed usage are separately inspectable.
- Generic providers remain no-control and safe by default.
- Reasoning tests pass without changing compact, constraint, or completion
  policies.

## Rollback

Unset the explicit reasoning profile and use generic provider-default behavior.
The compact and session-constraint feature flags remain independent.
