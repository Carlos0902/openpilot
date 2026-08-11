# Active Diagnostic Metadata Inventory

## Scope

CRU-5 makes the existing task-owned diagnostic loop explicit without adding a
second runtime controller. It strengthens how `RuntimeStateMetadata` represents
conflicts, risks, diagnostic decisions, and canonical progress, then lets the
existing `AgentRuntimeController` choose among bounded measure, act, verify,
recover, and stop dispositions before `ToolRouter`/Actor execution.

This slice does not alter entry routing, Agent Generator, tool permissions,
mutation scope, checkpoint ownership, or project completion authority.

## Existing owner and producer inventory

- `RuntimeStateMetadata` owns mutable task phase, known facts, unknowns,
  resolved questions, file evidence, edit plans, verification, risk level,
  budget, no-progress count, and completion reason.
- `AgentRuntimeController` owns task-level phase and completion transitions.
- `StateUpdater` absorbs tool results and currently computes no-progress from
  the lengths of five collections.
- `DecisionNeedMetadata` is the bounded question/action request consumed by
  `ToolRouter`; it does not own the Controller's disposition.
- `ToolRouter` maps one admitted need to a capability, and `RuntimeGuard` owns
  permission/risk/scope admission.
- `RuntimeVerifier` owns the smallest post-write verification plan.
- `RuntimeCheckpointMetadata` persists an immutable snapshot of the complete
  runtime state; `RuntimeReportMetadata` is a derived terminal view.
- Experiment-local `ActiveState`/E2/E3 contracts remain non-production and do
  not own OpenPilot runtime facts.

The missing production facts are an explicit unresolved-conflict set,
evidence-linked diagnostic risks, the Controller's typed next-step decision,
and a content-sensitive progress signature. Storing these in `tool_history`,
`attributes`, or free-form completion text would hide control state.

## Metadata impact notes

### Diagnostic conflicts and risks

Fact: unresolved/resolved evidence conflicts and diagnostic risks that affect
the next task-level decision.

Authoritative producer: `AgentRuntimeController`/`StateUpdater`, based on typed
tool, verification, and caller-supplied diagnostic evidence.

Consumers: active diagnostic evaluator, no-progress gate, checkpoint resume,
runtime reporter, and trajectory projection.

Lifecycle: task runtime state, snapshotted by the existing checkpoint.

Control impact: routing, recovery, and completion blocking.

Existing contracts reviewed: `RuntimeStateMetadata.known_facts`, `unknowns`,
`risk_level`, `guard_history`, `DecisionNeedMetadata`, `FailureMetadata`,
`ProjectDiagnosisMetadata.blocking_risks`, and experiment-local `ActiveState`.

Decision: add strict owned nested values to `RuntimeStateMetadata`; do not add a
public `MetadataKind`.

Why no duplicate source of truth is created: known/unknown facts remain in
their existing fields, Guard decisions remain permission evidence, and project
diagnosis remains project-domain state. The new values own only task-runtime
conflict/risk lifecycle and evidence references.

Serialization and migration: bounded tuple/list fields default empty;
historical checkpoints deserialize with no conflict/risk assertion.

Tests: strict construction, illegal resolution/evidence combinations,
round-trip, checkpoint round-trip, evaluator precedence, and reporter residual
risk projection.

Documentation updates: catalog, API, active-iteration plan/index, trajectory
alignment, session resume protocol, and implementation log.

### Active diagnostic decision and progress signature

Fact: one Controller-owned measure/act/verify/recover/stop decision, including
the canonical task-state signature it evaluated and whether evidence changed
since the prior decision.

Authoritative producer: `ActiveDiagnosticEvaluator`, invoked by
`AgentRuntimeController` before capability routing.

Consumers: `AgentRuntimeController`, no-progress/trajectory audit, checkpoint
resume, and experiment adapters.

Lifecycle: task runtime state, bounded history inside the existing checkpoint.

Control impact: routing, budget/no-progress, recovery, and stopping.

Existing contracts reviewed: `AgentPhase`, `DecisionNeedMetadata`,
`ToolDecisionMetadata`, `GuardDecisionMetadata`, `RuntimeBudgetMetadata`,
`IterationDisposition`, root progress signature, and experiment-local
`DecisionKind`.

Decision: add a strict enum and owned nested decision value under
`RuntimeStateMetadata`; reuse the existing `DecisionNeedMetadata` as the action
request and keep `ToolDecisionMetadata` as the downstream capability decision.

Why no duplicate source of truth is created: the diagnostic decision owns
*what kind of next move is justified*; `DecisionNeedMetadata` owns the concrete
question/request, `ToolRouter` owns capability selection, and Guard owns
admission. The canonical signature is derived from authoritative runtime
fields and is not a second fact store.

Serialization and migration: decision history and current signature default
empty/`None`; history is bounded. Historical checkpoints remain readable.

Tests: deterministic decision hierarchy, cost tie-break, evidence-change
explanation, content-sensitive no-progress, bounded history, JSON/checkpoint
round-trip, and permission/verification strength-preservation tests.

Documentation updates: same set as above.

## Implementation order

1. Add contract/serialization tests and the owned values.
2. Add evaluator tests for non-compensatory precedence.
3. Replace length-only no-progress with the canonical signature.
4. Wire streamed/bounded needs through evaluator then existing router/guard.
5. Add experiment-interface fixtures without importing experiment-local state
   into production metadata.
6. Run the five-axis review and full regression before changing canary status.

## Five-axis review evidence

### State and effects

| Trigger / input | State or side effect | Blast radius | Failure / recovery | Evidence |
| --- | --- | --- | --- | --- |
| conflict/risk observation | bounded owned value appended to task runtime state | one task/checkpoint | duplicate ID with changed facts or malformed evidence fails validation | metadata round-trip/invalid-state tests |
| diagnostic evaluation | one typed decision + current canonical signature | one task/checkpoint/report | incompatible required kind produces typed stop; no tool or permission is selected | hierarchy/evidence-change tests |
| failed verification | open high diagnostic risk plus existing replan/recover transition | one task | recover precedes re-verification; fresh pass resolves risk with new tool evidence | updater/evaluator tests |
| streamed needs | selected typed need proceeds to existing Router/Guard | one bounded step | blocking risk, no-progress, or absent compatible need stops before routing | controller routing tests |
| three-arm receipt | development comparison record only | one task/seed experiment | missing arm/active decision/provenance/budget/safety evidence rejects comparison | three-arm contract tests |

### Resource bounds

| Operation | Work per item | Cardinality | Max work per invocation | Yield / backlog policy |
| --- | --- | --- | --- | --- |
| canonical signature | serialize current bounded runtime facts | one task snapshot | existing state bounds; excludes tool/decision history growth | synchronous local hash |
| diagnostic selection | classify/sort supplied needs | caller-bounded need tuple | no Provider/tool call; current streamed path supplies one or a small tuple | no queue; stop when no compatible need |
| diagnostic state | append conflict/risk/decision | 64 conflicts, 64 risks, 128 decisions | hard validation/helper limits | checkpoint preserves exact bounded history |
| three-arm comparison | inspect three receipts | exactly 3 arms | constant work, shared budget object | no Provider execution authorization |

### Boundary behavior

| Input | Zero / empty | Exact boundary | Outside boundary | Blast radius / recovery | Test evidence |
| --- | --- | --- | --- | --- | --- |
| decision candidates | public streamed API rejects empty tuple | one compatible need selects exactly one decision | no compatible need produces stop | no capability/permission expansion | evaluator/controller tests |
| conflict evidence | fewer than 2 refs invalid | 2 refs accepted | duplicate/blank refs invalid | task metadata rejected before checkpoint | metadata tests |
| diagnostic history | historical empty fields migrate | 128 decisions allowed by model | helper rejects the next append | controlled task failure; no unbounded checkpoint | contract tests + model limits |
| experiment arms | none/missing invalid | fixed/model-directed/active exactly once | duplicate, legacy ordinary, or extra arm invalid | comparison rejected, no cost claim | three-arm tests |

Review conclusion: correctness findings about failed-verification precedence and
deserialization invariants were fixed. The change adds no execution authority,
keeps decision and evidence state bounded, preserves existing Router/Guard/
Actor/Verifier ownership, and uses a derived report rather than a second
runtime-state authority.
