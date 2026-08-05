# Stage 5B-3a/3b: Full-entry pre-transport gate

## Decision boundary

This is still a zero-provider gate. It enters through
`IntelligentAutopilot.execute()` and the real `AgentRuntimeController.run`,
persists a temporary runtime checkpoint, and composes the Stage 5B-2 current /
compact rendered-input pair. It reserves the worst-case per-request budget in a
cross-arm campaign ledger, but intentionally does not transport or reconcile a
Provider response.

## Evidence

- Stage 5B-3a: **4 passed**. `execute → runtime controller → checkpoint` was
  exercised with one real `SessionIngress.open_turn → confirm_proposal` state.
  The checkpoint's raw turn and active constraint state matched the ingress
  snapshot by canonical hash. Provider/network/project mutations were all `0`.
- Stage 5B-3b: **17 passed** when combined with the Stage 12 contract tests.
  The campaign ledger enforces shared Token and call caps across compact primary
  and current fallback arms, separate route/account identities, per-arm and
  campaign wall-clock limits, duplicate IDs, overrun, arm drift, and unknown
  usage fail-closed behavior.
- The composed pre-transport run reserved **4** requests (Goal Maker and Task
  Designer in each arm), recorded **0** observations, and kept
  `provider_execution_admitted=false`, `transport_attempted=false`.
- The Stage 7B-3b full ContextLoader chain emits a typed lineage receipt for
  both arms. The current and compact receipts must match on the ContextLoader
  source snapshot, raw-ingress turn digest, active-constraint digest,
  `project_environment_mode=read_only`, and the typed write-scope digest. A
  compact assembly failure carries the same fields into its current fallback
  receipt; it cannot fall back by merely incrementing a counter.
- `run_no_provider_preflight()` echoes the locked feature flag and kill-switch
  values. Stage 15 checks those returned values, along with
  `status=passed`/`controls_admitted=true`, against the requested protocol
  controls before reserving any campaign request.

## Remaining boundary

The gate does not claim Provider quality, observed usage, reasoning-token
behavior, or full raw-dialog ContextLoader coverage. The next stage is one
explicitly approved real Provider pair under this gate; any missing usage,
finish-reason ambiguity, verification failure, mutation drift, or hard-cap
crossing must stop the campaign and keep the current fallback separately
accounted.
