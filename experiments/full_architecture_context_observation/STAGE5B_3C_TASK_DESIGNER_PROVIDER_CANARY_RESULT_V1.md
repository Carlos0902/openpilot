# Stage 5B-3c: Task Designer Provider canary result

## Claim boundary

This is one bounded Provider pair at the production Task Designer
candidate/request boundary, with a separate full-entry admission check before
transport. It is not a claim about a complete end-to-end project-improvement
run: no downstream task executor, file writer, validation command, or raw-turn
ContextLoader hydration was allowed to run. The full-entry probe did exercise
`IntelligentAutopilot.execute()` → `AgentRuntimeController.run()` and verified
the checkpoint constraint hash before the Provider pair.

## Valid paired sample

The valid sample is the persistent campaign-state `v4` run. Both arms used the
same source snapshot and the same confirmed SessionIngress constraints. The
compact arm was the primary route; current was the separately accounted
fallback/control route.

| Metric | Compact primary | Current control/fallback | Difference (compact-current) |
| --- | ---: | ---: | ---: |
| Rendered input tokens | 1,258 | 1,719 | **-461 (-26.8%)** |
| Provider input tokens | 1,361 | 1,822 | **-461 (-25.3%)** |
| Provider output tokens | 1,866 | 2,069 | **-203 (-9.8%)** |
| Reasoning tokens | 1,674 | 1,856 | **-182 (-9.8%)** |
| Provider total tokens | 3,227 | 3,891 | **-664 (-17.1%)** |
| Provider calls | 1 | 1 | 0 |
| Finish reason | `stop` | `stop` | both valid |
| Constraint / task quality | pass | pass | both target `calculator.py` |

The three active constraints were recalled in both request assemblies. Both
responses stayed inside the active write scope and did not trigger a project
mutation. The persistent ledger contains two reservations and two observed
reconciliations; all campaign/per-arm Token, call, and wall-clock caps remained
below their limits. Provider/network calls were **2** and project mutations
were **0**.

## Diagnostic attempts and stop evidence

Earlier attempts are deliberately excluded from the paired result because they
were runner diagnostics, not a valid current/compact pair:

- `max_retries=0` was initially mistaken for “one attempt, no retry”; in this
  code it executes zero attempts. The runner was corrected to `max_retries=1`
  while keeping `transport_retries=0`.
- A 512-token completion reserve produced an empty `finish_reason=length`
  response under Provider-default reasoning. The runner was aligned to the
  existing typed Task Designer ceiling of 2,200 without changing reasoning
  policy.
- Subsequent attempts exposed real Provider truncation and schema failures:
  observed usage was retained, unknown usage and reservation overrun stopped
  before the other arm, and known-usage JSON failure was routed only through a
  separately accounted current fallback. No attempt was silently regenerated.

These attempts consumed Provider calls but are not aggregated into the valid
paired metrics above. They are evidence that failure telemetry and fallback
semantics are active, not evidence of compact quality.

## Remaining limitations

- The Provider result is one task family and one source snapshot; it is a
  mechanism signal, not a generalized quality conclusion.
- `SessionIngressState` is authoritative for the runtime/checkpoint probe, but
  raw `SessionTurn` values are still not hydrated into the production
  ContextLoader/ShortMemory path for a full conversation claim.
- Reasoning policy was intentionally held at Provider-default. Any explicit
  non-thinking/routing change must be a separate, paired experiment; it must
  not be mixed into this compact result.
- More tasks and providers are required before promotion. Current remains the
  safe fallback, and any unknown usage, quality failure, verification failure,
  mutation drift, or cap crossing must stop the canary.
