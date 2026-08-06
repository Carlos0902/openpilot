# Phase 12: Reasoning Strategy Experiment for Rolling Summary

## Status

Completed. The four-call matrix and final offline regression passed. See
`PHASE_12_REASONING_STRATEGY_RESULT.md`; no global reasoning or Compact default
was changed.

## Question

Why did the real-provider rolling-summary calls fail to produce structured
JSON? The previous authoritative run showed `output_tokens=128` and
`reasoning_tokens=128` with `finish_reason=length`, but that is only a signal.
The experiment must distinguish:

1. provider-default reasoning consuming the whole completion ceiling;
2. an explicit provider capability profile with reasoning disabled;
3. a larger completion ceiling with provider-default reasoning; and
4. (diagnostic only) explicit enabled/high reasoning at the same ceiling.

## Frozen factors

- Same source text, source IDs, source fingerprint, schema, temperature,
  purpose, model endpoint, tokenizer, and transport retry policy.
- Same rolling-summary adapter and validation gates.
- No project mutation, task execution, memory write, or Prompt integration.
- Each strategy gets one request only; maximum four real Provider calls.
- Profiles and policies are explicit typed values. No model-name capability
  inference is allowed.

## Strategy matrix

| strategy | capability profile | reasoning intent | summary ceiling | purpose |
|---|---|---|---:|---|
| `default_128` | generic v1 | provider default | 128 | reproduce prior signal |
| `disabled_128` | DeepSeek-known v1 | disabled | 128 | test reasoning exhaustion hypothesis |
| `default_256` | generic v1 | provider default | 256 | test ceiling-only hypothesis |
| `enabled_high_128` | DeepSeek-known v1 | enabled/high | 128 | diagnostic upper-bound control |

The DeepSeek-known profile is an explicit experiment declaration, not an
inference from the model name. If the configured endpoint rejects its
transport, that is recorded as an adapter/profile compatibility failure.

## Attribution rules

- `finish=length` and `reasoning_tokens == output_tokens == ceiling` is
  `reasoning_exhausted_completion`.
- `finish=length` with materially lower reasoning tokens is
  `completion_ceiling_or_schema_truncation`.
- Complete usage + complete finish + valid structured payload is
  `summary_path_available`; only this can enter recorded replay.
- Unknown usage/finish or provider/profile errors remain explicit blockers;
  they are not treated as zero or success.
- A difference in accepted summary or token cost is not attributed to Compact
  unless the reasoning policy and completion ceiling are held fixed.

## Metadata impact note

```text
Fact: one explicit reasoning strategy attempt for rolling-summary shadow
Authoritative producer: typed ReasoningPolicy in source-bound experiment manifest
Consumers: strategy comparison report and next Compact experiment admission
Lifecycle: experiment evidence; never global reasoning/task/permission authority
Existing contracts reused: ReasoningPolicy, ResolvedReasoningPolicy,
  ReasoningCapabilityProfileId, ProviderAttemptReceipt, RollingSummaryAdapter
Decision: extend experiment manifest with nested typed reasoning policy and add
  a narrow strategy runner/classifier; do not add production MetadataKind
Control impact: request transport rendering and experiment attribution only
Serialization: versioned strict JSON; usage/reasoning/finish remain nullable
Tests: policy binding, profile mismatch, request hash, classifier, replay and
  zero-mutation gates
```

## Exit gate

- Offline strategy and attribution tests pass.
- Real run is either four bounded, fully evidenced attempts or a typed
  pre-transport block.
- The report states whether disabling reasoning or increasing the ceiling
  changed completion validity; no production default is changed.
