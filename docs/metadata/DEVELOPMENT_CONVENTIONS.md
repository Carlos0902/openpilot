# Metadata-first Development Conventions

## 1. Purpose

OpenPilot uses metadata as its architectural skeleton. Cross-module behavior should be
designed from typed facts, ownership, lifecycle, and state transitions before controller
branches, prompt text, logs, or UI rendering are added.

This document is the project-wide development convention for deciding:

- whether a value needs to become metadata;
- whether to reuse, extend, nest, reference, or create a contract;
- how to prevent duplicate facts and competing sources of truth;
- how to make the smallest change compatible with the current value-nested design.

Related sources have distinct authority:

- `Code/src/metadata/`: executable field and validation truth;
- `docs/metadata/CONTRACT_CATALOG.md`: complete public contract inventory, ownership, and lifecycle;
- this document: mandatory design and change workflow;
- `docs/metadata/VALUE_NESTING_RESEARCH.md`: non-normative research note about the current
  nesting shape and possible future pressure;
- `API.md`: externally relevant protocol behavior.

## 2. Core rule

The required implementation order is:

1. identify the fact and its authoritative owner;
2. inspect the complete existing metadata inventory and real producers/consumers;
3. select reuse, extension, an owned nested value, an existing reference mechanism, or a
   new contract;
4. define typed states and legal combinations;
5. add contract and transition tests;
6. only then implement orchestration, persistence, prompt, supervisor, or UI behavior.

Metadata represents facts and protocol. Business execution remains outside
`Code/src/metadata/`.

## 3. What must be typed

A value must be a typed field, enum, strict nested model, or metadata reference when it
affects any of the following:

- routing or state-machine transitions;
- permissions, approvals, risk, or automation policy;
- budget accounting, retry, recovery, or completion;
- identity, correlation, lineage, ownership, or lifecycle;
- side-effect classification, idempotency, or reconciliation;
- persistence, cross-process resume, or cross-module exchange;
- evidence selection, audit aggregation, or user actions offered by the UI.

Free text is allowed for explanations, diagnostics, provider-specific payloads, and
human instructions only when changing that text cannot change program behavior.

The following are prohibited:

- branching by matching `reason`, `message`, exception text, prompt text, or log text;
- hiding control facts in `attributes`, `annotations`, `details`, `raw_payload`, or
  `trace_info`;
- using empty string, `None`, missing field, and arbitrary text as undocumented states;
- creating a UI or supervisor action by interpreting an explanation string;
- copying an authoritative fact into another model without ownership or derivation rules.

When a formerly diagnostic value starts controlling behavior or gains a second real
consumer, promote it deliberately to a typed contract rather than extending an escape
hatch.

## 4. Current architecture boundary

The implemented architecture is a strict metadata protocol composed mostly by value into
several local trees. The project does not currently have a general three-layer metadata
architecture or a shared cross-tree relationship layer.

Development must preserve that current boundary unless a separately reviewed architecture
change is supported by production evidence, migration design, and end-to-end tests.

Within the current design:

- use a typed field or enum for one stable fact or state;
- use a strict nested `BaseModel` when the value is owned by one public contract and has no
  independent lifecycle;
- use value nesting when the parent owns a complete point-in-time snapshot;
- reuse existing correlation, task, step, execution, run, checkpoint, call, and artifact
  IDs when relationships already cross boundaries;
- use a derived report or prompt/UI view without treating that view as new source truth;
- use an explicit diagnostic escape hatch only for non-control provider/debug data.

Cross-tree references, generalized relationship metadata, or graph-like storage are design
options, not current project conventions. Introduce them only for a concrete repeated
problem and in a small, independently accepted slice. `TaskGraphNodeMetadata` and
`TaskGraphEdgeMetadata` remain task-domain contracts rather than a global metadata layer.

## 5. Reuse and duplication decision

Before adding a model or field, inspect all of the following:

1. `docs/metadata/CONTRACT_CATALOG.md` by family, ownership, and lifecycle;
2. `MetadataKind` in `Code/src/metadata/base.py`;
3. public exports in `Code/src/metadata/__init__.py`;
4. similar field names and nested types under `Code/src/metadata/`;
5. actual producers, consumers, stores, trajectory events, and tests;
6. `docs/metadata/VALUE_NESTING_RESEARCH.md` for observed repeated leaves and tree pressure,
   treating its future directions as hypotheses rather than requirements.

Then choose in this order:

### 5.1 Reuse an existing field or model

Reuse when semantics, authority, lifecycle, optionality, and validation are the same. Add a
new consumer rather than a parallel representation.

### 5.2 Extend an existing model

Extend when the new fact belongs to the same owner and lifecycle and is required by a real
producer/consumer path. Persisted changes require defaults or a historical-read migration.

### 5.3 Add a strict nested value

Add a supporting `BaseModel` without a new `MetadataKind` when it exists only as an owned
part of one public contract and has no independent cross-module lifecycle.

### 5.4 Reuse a reference or derive a view

When the project already has stable identity or artifact references, reuse them instead of
copying. A consumer-specific report, prompt, or UI view normally does not justify a new
authoritative metadata kind. A new general relationship mechanism is an architecture
change and requires separate evidence and review.

### 5.5 Create a new metadata contract

Create one only when all are true:

- the concept has distinct semantics not represented by an existing contract;
- it has a clear authoritative producer;
- it crosses a module or persistence boundary, or has an independent auditable lifecycle;
- at least one concrete consumer is identified;
- nesting it into an existing owner would give that owner the wrong responsibility;
- reuse, extension, reference, and projection were considered and rejected explicitly.

Similar field names alone are not a reason to merge contracts. A one-off prompt bundle or
UI view alone is not a reason to create one.

## 6. Required metadata impact note

Every change that adds or changes metadata must record this compact design note in its plan,
change description, or implementation log:

```text
Fact:
Authoritative producer:
Consumers:
Lifecycle: runtime-only | event evidence | checkpoint | durable project state | artifact
Control impact: none | routing | permission | budget | recovery | completion
Existing contracts reviewed:
Decision: reuse | extend | nested value | existing reference | derived view | new contract
Why no duplicate source of truth is created:
Serialization and migration:
Tests:
Documentation updates:
```

If `Decision` is `new contract`, the note must name the closest existing contracts and state
why each is not semantically or lifecycle-equivalent.

## 7. Implementation and test requirements

For a new public concrete metadata contract:

- add one unique `MetadataKind`;
- inherit `MetadataBase` and lock `kind` to one `Literal`;
- export it through `metadata.__all__`;
- list it in `docs/metadata/CONTRACT_CATALOG.md`;
- define its producer, consumers, lifecycle, defaults, and migration behavior;
- test valid construction, invalid combinations, assignment validation, and JSON round-trip;
- test affected state transitions and persistence boundaries.

For a changed existing contract:

- preserve historical reads or explicitly change schema version;
- stop new producers of deprecated fields before removing consumers;
- reject contradictory old/new fields or define deterministic precedence;
- test that explanation text can change without changing control decisions;
- verify that unknown states fail closed at permission, mutation, and recovery boundaries.

For nesting and references:

- test whether serialization is snapshot-by-value or identity-by-reference;
- prevent recursive/unbounded payload growth;
- preserve source IDs and evidence lineage across projections;
- avoid putting full artifacts into events when an artifact reference is sufficient.

## 8. Documentation and review gates

Reviewers must reject a metadata change when:

- the inventory and closest existing contracts were not checked;
- a control value remains in a free-form container or string;
- producer or consumer ownership is ambiguous;
- a second authoritative copy is introduced;
- a projection is persisted as if it were source truth;
- migration or invalid-state tests are absent;
- the catalog, API, or relevant trajectory documentation is stale.

When contracts change, review and update as applicable:

- `docs/metadata/CONTRACT_CATALOG.md`;
- `docs/metadata/VALUE_NESTING_RESEARCH.md` when a new audit materially changes the
  observed tree shape or repeated leaves;
- `API.md` for protocol-visible behavior;
- task-trajectory alignment and implementation log for event/recovery changes;
- `AGENTS.md` only when the project-wide convention itself changes.

## 9. Application to recovery work

Recovery changes must begin by reviewing the existing runtime family:

- `RuntimeStateMetadata` owns mutable operational state;
- `RuntimeCheckpointMetadata` owns an immutable persisted snapshot;
- `RuntimeResumeDecisionMetadata` owns one preflight assessment;
- `RuntimeReportMetadata` is a derived auditable projection;
- `FailureMetadata`, `VerificationPlanMetadata`, tool-call metadata, budget metadata, and
  correlation IDs should be reused where their semantics already match.

New recovery types are justified only for facts these contracts cannot represent without
mixing ownership or lifecycle. Recovery status, recoverability, automation policy, reason
codes, blockers, fallback actions, and lineage must be evaluated individually under the
reuse decision above. The recovery controller must consume the resulting typed contracts;
it must not create a parallel string state machine.
