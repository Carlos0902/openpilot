# Stage 9 Task Designer scenario canary V2

Stage 8 proved the compact projection mechanism only for an unrelated diagnosis.
Stage 9 V2 separates each relevance branch before any paid experiment:

1. `unrelated_diagnosis` is the Stage 8 negative-control regression;
2. `strongly_related_diagnosis` matches the goal by exact candidate identity;
3. `partial_shared_criterion` has different goal/candidate identities but shares
   one complete acceptance criterion;
4. `relevant_iteration_memory` has an unrelated diagnosis and independently
   exercises selection of the latest relevant iteration-result memory.

Fixtures use real `ProjectDiagnosisMetadata` and start from real `MemoryRecord`
instances. Each memory is then passed through the production-equivalent reader
mapping (`memory_type` becomes `type`) and `compact_project_memory_record` before
entering `ProjectStateSnapshot`; the fixture therefore matches the consumer
shape rather than the storage-model shape. The memory fixture preserves project path, selected
candidate ID and title, timestamp, and `autonomous_iteration` tags. It contains
environment noise, an old relevant result, a newer unrelated result, and the
latest relevant result. It does not claim that memory content is a serialized
`TaskResultMetadata` payload.

## Phase A: zero-provider offline sentinel

`stage9_task_designer_scenario_gate.py` runs production current/compact candidate
builders through the real `ContextAssembler` with the deterministic
`stage9-stable-lexical-v1` counter. It records final prompt tokens, every
candidate decision, selected-content fingerprints, and final-content
fingerprints. Required candidates and scenario candidates whose truncation is
`FORBIDDEN` must be kept completely.

The gate fails closed on source, runtime/frozen goal, safe targets, validation
commands/result, product intent, required-candidate equality, sentinel behavior,
or assembly-status drift. Every compact scenario must be smaller than current;
10% is an initial entry threshold, not a production performance claim.

The partial scenario must retain `S9_PARTIAL_DIAGNOSIS` and
`S9_SELECTED_METRIC`, while deleting `S9_UNRELATED_DIMENSION` and
`S9_UNRELATED_METRIC`. The memory scenario must retain only
`S9_LATEST_RELEVANT_ITERATION_RESULT` among its memory sentinels.

The committed evidence is
`STAGE9_TASK_DESIGNER_SCENARIO_CANARY_V2_OFFLINE_RESULT.json`. Tests regenerate
the stable snapshot and require exact equality with that file. This phase always
records zero provider calls.

## Phase B: gated six-arm provider sentinel

Only after the offline gate passes may the separately implemented, explicitly
authorized `stage9_task_designer_provider_sentinel.py` runner execute one
current/compact pair for each positive scenario:
strong diagnosis, partial shared criterion, and relevant iteration memory. That
is six provider arms. The unrelated case remains an offline/Stage 8 regression.
The runner exposes a read-only, zero-provider preflight and an injected
arm-runner interface. The hardened `run_observation.py` adapter is connected;
paid execution remains a separate explicit `--execute` action and has not been
started by the offline gate.

## Phase C: deferred six-arm confirmation

Only after all six provider-sentinel arms pass may a later, separately authorized
campaign run the reverse/crossed six-arm confirmation. Neither paid phase is
implemented by this offline module.

Frozen spend limits are:

- 30,000 Token hard limit per arm;
- 60,000 Token hard limit per pair;
- 180,000 Token hard limit for the six-arm provider sentinel;
- 180,000 Token hard limit for the six-arm confirmation;
- 360,000 Token hard limit across both paid phases.

Run the offline gate with:

```bash
PYTHONPATH=Code/src:experiments/full_architecture_context_observation \
  python experiments/full_architecture_context_observation/stage9_task_designer_scenario_gate.py
```
