# Stage 7E: Goal reasoning-complexity canary result

Status: **stopped by the quality gate**. The experiment isolated reasoning
routing from context and completion reservation using one source snapshot and
one candidate list. It did not change the production default.

| Arm | Reasoning mode | Initial max tokens | Input | Output | Reasoning | Finish | JSON/goal |
|---|---|---:|---:|---:|---:|---|---|
| routine | `disabled` | 840 | 1,834 | 97 | unknown | `stop` | valid/valid |
| standard | `provider_default` | 840 | 1,912 | 1,140 | 1,140 | `length` | invalid/invalid |

The standard arm made one bounded length recovery (the initial reservation
remained 840; the receipt does not redefine the treatment ceiling from the
recovery). The routine arm produced a valid Goal in one call. Both arms used
the same rendered input size (1,805 local tokens), and the source snapshot hash
was identical. Total calls were three, all usage was observed, and project and
memory mutations were zero.

## Decision

For a single bounded Goal decision, the routine route is a promising treatment:
it avoided the provider-default reasoning cap-hit in this sample while
preserving the exact completion reservation. This is a mechanism sample, not a
statistical quality claim. Keep the production Goal default unchanged until at
least three interleaved pairs (and another task shape or provider) confirm the
valid-goal rate and downstream acceptance. If a future pair fails the routine
quality gate, revert the route for that task shape to provider default without
changing compact policy.
