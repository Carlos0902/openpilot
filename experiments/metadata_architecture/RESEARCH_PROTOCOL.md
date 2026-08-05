# Research Protocol

## Question

What metadata structure lets OpenPilot preserve strict ownership, recovery, auditability, and
bounded model context while avoiding competing copies of the same authoritative fact?

The experiment does not begin with “replace trees with a graph.” Each case must choose among:

- owned nested value;
- immutable snapshot-by-value;
- stable reference;
- typed relationship;
- derived projection.

## Three-layer semantics

### 1. Fact layer

A fact revision has a stable ID, positive revision, typed kind/value pair, authoritative owner,
and typed lifecycle. A stored revision is immutable. Resolving the latest revision must be
explicit; persisted projections and relationships should pin a numeric revision.

### 2. Relationship layer

A relationship owns only the relationship fact. It does not own or copy endpoint values. Both
endpoints must resolve when the relationship is admitted. Initial relation kinds are deliberately
narrow: `selects`, `supports`, `derived_from`, `consumes`, and `snapshot_of`.

Adding a generic `related_to` edge is out of scope because it would weaken semantics and make
control behavior dependent on an untyped graph convention.

### 3. Projection layer

A projection is a recipe for a consumer-specific view. It records purpose and pinned source
references, but is not a second source of truth. Prompt, report, UI, and guard projections may
have different fields. A projection must be reproducible or clearly declare unavailable sources.

## Decision matrix

| Situation | Default representation |
| --- | --- |
| Child has one owner and no independent lifecycle | Strict nested value |
| Parent owns a complete recovery/audit state at a point in time | Snapshot by value |
| Fact crosses boundaries and already has stable identity | Pinned reference |
| The relationship itself is queried, audited, or independently owned | Typed relationship |
| Consumer needs a selected subset or reordered rendering | Derived projection |
| Similar field names but different owner/lifecycle | Keep separate |

## Required evidence per case

Each case records:

1. current producer, consumers, persistence boundary, and lifecycle;
2. whether observed repetition is protocol self-containment, snapshot, projection, compatibility,
   or competing authority;
3. baseline and experimental serialized size;
4. number of authoritative value copies;
5. reconstruction and missing-reference behavior;
6. migration and historical-read implications;
7. prompt/view assembly complexity;
8. whether a smaller change to the current value tree solves the problem.

Field-name counts from the AST audit are discovery signals only. They are never sufficient proof
that contracts duplicate a fact.

## Planned cases

1. Project diagnosis candidate selection — likely reference/projection candidate.
2. Runtime checkpoint and report — expected mixed result: checkpoint snapshot by value, report
   derived from state, selected durable references for external evidence.
3. Tool call/error/event envelopes — expected protocol self-containment, not graph migration.
4. Evidence-to-conclusion lineage — likely typed `supports` relationships if real queries need it.
5. Dependencies and stack preset across project state, diagnosis, and environment sync — determine
   whether they are snapshots of one project revision or genuinely different lifecycle facts.

## Promotion gates

No experimental contract may move into production until one bounded case demonstrates all of:

- at least two real cross-module consumers or three repeated authoritative copies;
- one named authoritative producer and lifecycle;
- no loss of checkpoint self-containment or deterministic historical replay;
- explicit missing/stale-reference behavior;
- lower inconsistency risk or materially simpler projection assembly;
- serialization and migration plan;
- contract, transition, persistence, and end-to-end tests;
- updates required by `docs/metadata/DEVELOPMENT_CONVENTIONS.md`.

Byte reduction alone is not a promotion criterion.

