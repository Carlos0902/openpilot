# Stage 7F-2: Full-session routine Goal compact/current canary

Status: **passed**. This was a new source-bound Provider campaign with the
feature flag enabled, kill switch armed, read-only project environment, and an
experiment-only routine Goal reasoning route. The production default route was
not changed.

| Boundary | Rendered input | Provider input | Output | Total | Quality |
|---|---:|---:|---:|---:|---|
| project-improvement analyzer | 2,858 | 2,893 | 193 | 3,086 | responded |
| Goal Maker (routine) | 2,873 | 2,908 | 338 | 3,246 | responded |
| compact Task Designer | 2,857 | 2,892 | 172 | 3,064 | passed |
| current Task Designer | 2,940 | 2,974 | 195 | 3,169 | passed |

The paired Task Designer evidence is the context result: compact input was
82 provider tokens lower than current (2.76%), and total usage was 105 tokens
lower (3.31%). The compact arm selected one `compaction:*` artifact and no
`session_dialog:*` raw sources; the current arm selected no compaction artifact
and retained raw `session_dialog:*` sources. Both arms selected the same
required session constraint and produced an authorized `calculator.py` task
with the required validation command.

The campaign used 4 Provider/network calls, 12,565 aggregate tokens, and zero
project or memory mutations. The ContextLoader selected 5 of 9 dialog turns,
kept the required constraint, and persisted one compaction binding. Source
snapshot, session-turn hash, session-constraint hash, and checkpoint hashes
match. All four requests resolved the known DeepSeek capability profile with
routine/disabled reasoning; the routine Goal treatment was locked into the
campaign metadata.

## Interpretation

This is the first complete real-session evidence that the compact projection
reaches analyzer → Goal → Task without losing constraints or authorization,
and that it lowers the paired Task prompt under the same completion policy.
The reduction is real but modest for this fixture; it is not yet a claim about
longer histories, other task shapes, or unknown provider profiles. The next
stage should expand only with an explicitly reviewed multi-task canary and
retain current fallback as the kill-switch path.
