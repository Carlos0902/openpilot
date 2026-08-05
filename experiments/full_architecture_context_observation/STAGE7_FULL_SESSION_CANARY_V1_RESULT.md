# Stage 7: Full-session compact canary result

Status: **stopped by the quality gate**. This was one bounded real-Provider
run under the existing feature flag, kill switch, source lock, and campaign
ledger. It did not enable compact as a production default and it did not run a
task executor.

## Scope and safety

- Claim boundary: `full_session_post_core_context_canary`.
- Provider: `deepseek-v4-flash` through the configured OpenAI-compatible endpoint.
- Provider attempts: **3**; network calls: **3**.
- Project mutations: **0**; memory mutations: **0**.
- The same source snapshot and session-turn hash were attached to all three
  attempts; the ContextLoader compaction and constraint lineage gates passed.
- Campaign state was written under `runs/stage7_full_session_canary_v3/` and is
  source-bound; it must not be resumed with a different temporary source.

## Observed attempts

| Purpose | Input | Output | Reasoning | Finish | Result |
|---|---:|---:|---:|---|---|
| `project_improvement` | 2,902 | 179 | unknown/not reported | `stop` | responded |
| `iteration_goal` | 2,987 | 920 | 920 | `length` | empty/invalid JSON |
| `iteration_goal` recovery | 2,987 | 1,200 | 1,200 | `length` | empty/invalid JSON |

The analyzer response was no longer an assistant-history echo after the
terminal user contract fix. The Goal Maker then consumed the provider-default
reasoning route for `STANDARD` complexity. Both attempts spent the entire
completion allowance on reasoning and returned no usable JSON, so the gate
stopped before Task Designer compact/current requests.

## Interpretation

This run proves that the full ContextLoader → derived projection → analyzer →
Goal path is now exercised with auditable usage and no side effects. It does
not provide a compact-vs-current quality or Token comparison because the
paired Task Designer boundary was never reached. The stop is a reasoning/output
contract signal, not evidence that compact lost the source or constraint
lineage.

The next change must therefore be an independent, provider-neutral reasoning
complexity experiment. It should freeze the context projection, schema,
temperature, and completion ceiling, compare a routine single-goal route with
the current standard/provider-default route, and keep the existing fail-closed
behavior if the routine route harms decision quality. No broader canary should
run until that experiment has a valid JSON/finish-quality result.
