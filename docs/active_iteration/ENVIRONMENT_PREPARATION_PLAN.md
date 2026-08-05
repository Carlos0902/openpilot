# Environment Preparation Lifecycle Plan

## Goal

Prepare and bind a project-local execution environment before Python validation,
resynchronize it after dependency-surface changes, and prevent silent fallback to
the host interpreter.

## Metadata impact note

Fact: The requested environment operation, observed readiness, stable environment
identity, and effective interpreter for one project.

Authoritative producer: The read-only environment preflight detector produces
readiness; the setup/sync executor produces a ready environment snapshot.

Consumers: Session environment gate, ready-environment runtime cache, command
rewriter, validation completion evidence, trajectory/UI, and checkpoint/resume
preflight.

Lifecycle: Runtime-derived ready cache, event evidence, and checkpoint identity
reference.

Control impact: Routing, permission, recovery, and completion.

Existing contracts reviewed: `ToolInputMetadata`, `ToolResultMetadata`,
`ToolExecutionEnvelopeMetadata`, `EnvironmentSyncMetadata`,
`RuntimeCheckpointMetadata`, `ProjectFingerprint`, `RuntimeStateMetadata`, and
the project command/validation paths.

Decision: Extend `EnvironmentSyncMetadata` with typed operation/readiness/identity;
extend `ToolInputMetadata` with an explicit environment operation and requested /
effective command evidence; reuse the existing `ProjectFingerprint` interpreter
and environment identity fields. Do not add a `MetadataKind` or copy the active
environment into `RuntimeStateMetadata`.

Why no duplicate source of truth is created: `EnvironmentSyncMetadata` remains
the environment snapshot owner. `_project_environments` is a process-local cache
of ready snapshots only. `ProjectFingerprint` stores only recovery identity.

Serialization and migration: Historical environment payloads deserialize as
`readiness=unknown`; they cannot enter the ready cache until a current read-only
preflight succeeds. Existing checkpoints without environment identity remain
legacy-unbound and must preflight before a Python command is resumed.

Tests: Contract validation/round-trip; zero-side-effect preflight; permission-
controlled setup; ready-cache gate; requested/effective command evidence; no host
fallback; dependency drift resync; standard/enhanced/resume behavior.

Documentation updates: `API.md`, metadata catalog, task-trajectory alignment,
session-resume documentation if recovery behavior changes, and implementation log.

## Phase 1 implementation order

1. Add typed lifecycle fields and failing contract/preflight tests.
2. Implement a filesystem-only preflight that performs no mkdir, subprocess,
   package installation, Git, stack-preset creation, or memory write.
3. Restrict the runtime environment cache to validated `ready` snapshots.
4. Gate Python validation before execution; perform permission-controlled setup
   only when preflight reports `setup_required` or `stale`.
5. Re-run preflight immediately before later validation so dependency changes
   trigger one incremental sync.
6. Preserve requested and effective commands and validate the requested command
   while requiring the bound project interpreter in production execution.
7. Bind the ready environment identity into checkpoint fingerprints and validate
   it on resume.
8. Run deterministic and full regression suites, then perform a fixed-project
   validation using the project-local interpreter.

## Explicit non-goals

- No generalized environment graph or multi-environment scheduler.
- No native container orchestration.
- No automatic setup for inspection-only tasks.
- No provider/reasoning changes in this phase.
