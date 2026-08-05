# Stage 11: Session Constraint State Offline Result

Status: **passed** (zero provider, zero network, zero project mutation)

The replay uses the production `MemoryContextBuilder` and freezes the same
10/20/50-message trajectory into three arms:

- `full_truth`: large prompt budget, no state projection;
- `compact_without_state`: segmented compact at 2,200 characters;
- `compact_with_state`: the same compact policy plus one required,
  non-truncatable `SessionConstraintState` candidate.

## Observed result

| Messages | Full truth | Compact without state | Compact with state |
| ---: | ---: | ---: | ---: |
| 10 | 16,387 chars | 2,200 chars | 2,200 chars |
| 20 | 39,304 chars | 2,200 chars | 2,200 chars |
| 50 | 108,064 chars | 2,200 chars | 2,200 chars |

The without-state arm omitted the early exact validation command and write
scope in all three sizes. The with-state arm retained the typed allowed file,
forbidden file, exact validation command, current failure, and one required
constraint candidate in all three sizes. Active constraint recall was 100%;
assistant-origin authority acceptance was 0%.

Assistant-only noise kept the canonical state hash stable while changing the
rendered prompt hash. A revised user constraint changed the state hash. The
fixture does not claim provider token savings or real-task quality; it proves
the compact boundary and state projection behavior only.

Reproduce with:

```bash
PYTHONPATH=Code/src:experiments/full_architecture_context_observation \
  python experiments/full_architecture_context_observation/stage11_session_constraint_offline.py
```
