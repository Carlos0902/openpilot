# Stage 7F-1: Interleaved Goal reasoning-complexity result

Status: **routine treatment passed; baseline quality gate stopped**. The
corrected runner ran three interleaved pairs using one source snapshot,
candidate list, JSON schema, and initial completion reservation. It counted
every provider attempt, including bounded recovery attempts, and failed closed
on unknown total usage.

| Pair | Routine | Standard | Initial ceiling |
|---:|---|---|---:|
| 1 | 1 call, 91 output, valid | 2 calls, first `length`, recovery still `length`, invalid | 840 |
| 2 | 1 call, 95 output, valid | 1 call, 677 output, valid | 840 |
| 3 | 1 call, 88 output, valid | 2 calls, first `length`, recovery `stop`, valid | 840 |

The routine arm used requested `disabled` reasoning and resolved to the exact
known DeepSeek capability profile in this run. It produced a valid Goal in one
call for all three pairs. The standard arm used requested
`provider_default`; two pairs required the existing bounded recovery, and one
pair remained invalid after recovery. The corrected result is in
`runs/stage7e_goal_reasoning_canary_v5/result.json`:

- 8 Provider/network calls;
- 19,740 aggregate tokens across all attempts (15,110 input and 4,630 output),
  below the 30,000 experiment cap;
- reasoning aggregate remains `null` because routine attempts do not report
  reasoning usage, rather than treating unknown as zero;
- identical source snapshot, session-turn hash, and session-constraint hash;
- zero project or memory mutations.

## Decision

For this Goal shape, the routine treatment has a 3/3 quality signal and avoids
the standard arm's repeated reasoning cap-hit/recovery behavior. The standard
baseline quality gate did not pass (2/3), so this is not a production-wide A/B
claim and does not justify changing the default route. It is sufficient to
enter the next separately flagged full-session canary with routine Goal
reasoning, because that canary's own analyzer, Goal, Task Designer, constraint
recall, lineage, and mutation gates remain authoritative.

The experiment also confirms that the initial 840 completion reservation is
the treatment value; recovery ceilings and each attempt's usage, finish reason,
and error type are retained separately. The signal is provider-profile scoped:
an unknown/generic capability profile may resolve requested routine disabling
to provider default, so this result must not be generalized across models
without a profile-specific canary.
