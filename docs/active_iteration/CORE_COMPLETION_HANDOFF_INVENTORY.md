# Core Completion Handoff Inventory

> Status: implementation gate for the core-owned completion handoff
>
> Scope: `codex/user-error-tuning`; this document does not authorize a second
> `CoreCompletionPackageView` builder or a post-core business state machine.

## 1. Boundary and current evidence

The post-core baseline at `codex/post-core-enhancement-plan` commit `64d2757`
contains PKG0 semantic freeze only. PKG1 safety semantics and PKG2's unique
`CoreCompletionPackageView` builder are not implemented. The core branch must
therefore expose a typed, fail-closed source-readiness view and must not infer
that a missing integration consumer is successful admission.

Existing source owners remain:

- `RuntimeStateMetadata`: mutable core task state, completion, verification,
  risk, authority, budget, and observed modified files;
- `SessionExecutionCursor`: exact planned tasks and bounded task-result
  reduction;
- `RuntimeCheckpointMetadata`: durable boundary, side-effect state, project
  fingerprint, verification and finalization evidence;
- `RuntimeFinalizationCursor` and `RuntimeReportMetadata`: report artifact,
  state hash, final event and derived report;
- `SessionConstraintState`: source-linked goal acceptance and authority;
- `ProjectImprovementPolicy`: whether post-core is disabled, optional, or
  required.

No current contract jointly validates these owners for post-core handoff.
Current result dictionaries can also derive `core_success=True` from an empty
task collection and can fall back from missing `core_success` to generic
`success`. Both are unsafe at this boundary.

## 2. Duplication review

Reviewed:

- `docs/metadata/CONTRACT_CATALOG.md` and
  `docs/metadata/DEVELOPMENT_CONVENTIONS.md`;
- `RuntimeStateMetadata`, `RuntimeCheckpointMetadata`,
  `RuntimeFinalizationCursor`, `RuntimeReportMetadata`,
  `SessionExecutionCursor`, `SessionConstraintState`,
  `VerificationPlanMetadata`, `ProjectFingerprint`,
  `DurableArtifactReference`, `TaskResultMetadata`, and iteration outcomes;
- public exports, checkpoint/report stores, result assembly, resume,
  diagnostics, and project-improvement consumers;
- the post-core Phase 0 inventory's PKG1/PKG2 ownership and serialization
  gates.

Decision:

- do not add a `MetadataKind`;
- add strict frozen derived values for the core source-readiness decision and
  bounded source references;
- build the view statelessly from the existing owners;
- never persist the view into `RuntimeStateMetadata` or treat it as a package;
- expose it only after normal core result assembly/finalization, so the future
  integration branch can consume it and build the one package;
- fail closed when any required source is missing, stale, contradictory, or
  indeterminate.

## 3. Impact notes

### 3.1 Core completion source readiness

```text
Fact: existing core source owners jointly do or do not support a verified,
      durable project-task completion handoff.
Authoritative producer: stateless core handoff evaluator over RuntimeState,
                        SessionExecutionCursor, RuntimeCheckpoint and final
                        RuntimeReport evidence.
Consumers: result composition, integration-branch Core Completion Package
           builder, replay/migration tests and trajectory diagnostics.
Lifecycle: runtime-only derived view; source checkpoints/artifacts remain
           durable owners.
Control impact: routing | recovery | completion.
Existing contracts reviewed: RuntimeStateMetadata, RuntimeCheckpointMetadata,
                             RuntimeFinalizationCursor,
                             RuntimeReportMetadata, SessionExecutionCursor,
                             SessionConstraintState, ProjectFingerprint,
                             DurableArtifactReference, iteration outcomes.
Decision: derived view with strict nested values; no MetadataKind and no
          RuntimeState field.
Why no duplicate source of truth is created: the view contains status,
          reason codes, hashes and bounded IDs/references only; it cannot
          update or replace any source owner, and ready is recomputed.
Serialization and migration: no persisted-schema migration; unknown/missing
          inputs produce ready=false. JSON is strict and bounded.
Tests: complete finalized source; missing/empty/incomplete tasks; response-only;
       failed or stale verification; indeterminate side effect; report/state
       hash conflict; incomplete project/environment identity; blocking risk;
       round-trip and mutation isolation.
Documentation updates: API.md, CONTRACT_CATALOG.md, active-iteration plan/index,
                       trajectory alignment and implementation log.
```

### 3.2 Post-core eligibility decision

```text
Fact: ready core sources may still be ineligible for post-core enhancement.
Authoritative producer: stateless eligibility evaluator consuming the ready
                        source view plus existing policy, execution mode,
                        observed output surface and residual risk facts.
Consumers: integration admission and top-level result composition.
Lifecycle: runtime-only derived view.
Control impact: routing | permission | completion.
Existing contracts reviewed: ProjectImprovementPolicy, RuntimeTaskPurpose,
                             RuntimeExecutionMode, RuntimeStateMetadata,
                             RuntimeReportMetadata and modified-file evidence.
Decision: extend the derived source-readiness value with a separate typed
          eligibility status/reason; do not overload ready or core_success.
Why no duplicate source of truth is created: policy, authority, outputs and
          risks remain owned by their existing contracts; eligibility is a
          deterministic admission decision.
Serialization and migration: no persisted migration; absence of the future
          unique package builder or any required mutation/environment source
          keeps admission closed.
Tests: disabled policy; response-only; empty/no-output/read-only analysis;
       blocking residual risk; ready and eligible mutation artifact; ready but
       integration consumer unavailable.
Documentation updates: API.md, CONTRACT_CATALOG.md, active-iteration plan/index,
                       trajectory alignment and implementation log.
```

### 3.3 Layered result composition

```text
Fact: core success, enhancement stage status and overall success are separate.
Authoritative producer: core executor owns core_success; post-core stage owns
                        improvement status; policy resolver deterministically
                        composes overall_success.
Consumers: CLI, RuntimeState/RuntimeReport projections, diagnostics and callers.
Lifecycle: result projection backed by existing state/report sources.
Control impact: completion.
Existing contracts reviewed: ProjectImprovementRequirement,
                             ProjectImprovementStatus, RuntimeStateMetadata,
                             RuntimeReportMetadata and legacy success fields.
Decision: reuse existing contracts and add one pure composition helper; keep
          legacy success as an overall-success compatibility projection.
Why no duplicate source of truth is created: overall_success is derived only;
          it never rewrites core_success or stage status.
Serialization and migration: historical result dictionaries remain readable;
          missing typed core_success is never upgraded from generic success at
          the handoff boundary. New producers emit all three layers.
Tests: optional failure preserves overall core success; required failure or
       interruption fails overall; disabled/skipped preserves core result;
       core failure cannot be repaired into success by enhancement.
Documentation updates: API.md, active-iteration plan and implementation log.
```

## 4. Fail-closed rules

`ready=true` requires all of the following from the same source identity:

1. project-task purpose and explicit `core_success=True`;
2. a non-empty exact task plan with one completed result per task;
3. `verification_status=passed` or legitimately `not_required`, with no
   pending verification;
4. no prepared, observed, or indeterminate side effect;
5. completed runtime phase and a complete finalization cursor;
6. report artifact, report source hash and current state hash agree;
7. project root and project fingerprint are present; mutation-capable Python
   work requiring environment identity has a ready attached identity;
8. no unresolved question, blocking diagnostic risk/conflict, or report
   residual risk;
9. active goal-acceptance constraints have explicit typed satisfaction or
   lawful waiver evidence. Until such evidence exists, they fail closed.

`post_core_eligible=true` additionally requires an enabled policy, a ready
handoff, a concrete mutation/output surface, mutation-capable root authority,
and no blocking residual risk. The current branch does not turn eligibility
into admission while the unique PKG2 builder is absent.

## 5. Cross-branch integration gate

The migration fixture must prove that the core-derived JSON can be consumed by
the future integration builder without depending on Python object identity,
and that missing/unknown fields fail closed. It must not import uncommitted
post-core code. Once PKG1 and PKG2 are accepted, the integration branch replaces
the unavailable-consumer sentinel with the unique builder and adds replay
against the committed package schema.

## 6. Five-axis review evidence

### State and effects

| Trigger / input | State or side effect | Blast radius | Failure / recovery | Evidence |
| --- | --- | --- | --- | --- |
| final core checkpoint + report | derive handoff/package; no source write | current result only | mismatch returns no package | ready/stale/checksum tests |
| integration flag enabled | suppress legacy pre-finalization improvement and README post-processing | current autonomous project run | stage remains typed skipped; disabling flag restores legacy lane | legacy callback assertion tests |
| required policy + skipped/failed stage | derive `overall_success=false`; preserve `core_success=true` | top-level result only | rerun only through an accepted post-core consumer | composition matrix |
| policy historical read | migrate counts in-memory | one policy snapshot | contradictory old/new fields reject | migration/conflict tests |

### Resource bounds

| Operation | Work per item | Input / batch cardinality | Max work per invocation | Yield / cancel / backlog policy |
| --- | --- | --- | --- | --- |
| task/result validation | O(1) set lookup | 256 tasks/results | O(256) | pure synchronous projection |
| modified-file/source projection | canonical path/hash check | 512 files | O(512) | over-bound fails closed |
| acceptance projection | ID/status check | 64 active decisions | O(64) | over-bound fails closed |
| verification command projection | bounded string validation | 64 commands | O(64), 4096 chars/item | invalid projection returns no package |
| diagnostic ID projection | ID-only copy | 128 decisions | O(128) | contract bound rejects overflow |

### Boundary behavior

| Input | Zero / empty | Exact boundary | Outside boundary | Blast radius / recovery | Test evidence |
| --- | --- | --- | --- | --- | --- |
| task plan/results | unready | 256 accepted | no package | current handoff only | empty/incomplete + contract tests |
| modified files | ready but ineligible when zero/read-only; eligible mutation requires non-empty | 512 accepted | `source_bounds_exceeded` | no enhancement entry | 513-file test |
| acceptance decisions | none valid only when no active requirement | 64 accepted | model rejects/fails closed | core recovery/user action | authority/waiver tests |
| policy counts | disabled `0/0/0`; automatic `0/1/1` | exact configured integer caps | negative, bool, partial or contradictory rejected | policy snapshot only | migration matrix |
| checkpoint/report hash | empty is incomplete | exact lowercase SHA-256 | malformed/mismatch unready | replay from source owners | stale/source tests |

### Verdict

- Correctness: approved after fixing empty-task vacuous success, report/event/file identity, legacy default migration, and source-projection bounds.
- Readability/simplicity: approved; controller changes remain thin calls into two focused pure modules.
- Architecture: approved for the CRU-6 integration boundary; one package builder, no new `MetadataKind`, no package persistence or second core truth.
- Security: approved; boolean/coerced budget counts, non-canonical/out-of-root paths, unauthorized waiver authority and indeterminate effects fail closed.
- Performance: approved; all new scans have explicit cardinality bounds and no Provider/tool/network work.
