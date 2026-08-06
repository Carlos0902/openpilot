# TASK_TRAJECTORY_EVIDENCE_ARCHITECTURE.md

## 1. Purpose

This document defines the architecture of OpenPilot's **task trajectory
evidence system**.

Its purpose is to make every real task execution observable as a durable,
structured trajectory.

The system should support:

- engineering diagnosis;
- human review of failures and suspicious successes;
- trajectory replay and comparison;
- later clustering / validation workflows;
- future routing / governance / strategy training data.

It must **not** automatically repair product code.

---

## 2. Architectural position

The task trajectory evidence system is **not** a replacement for:

- `autonomous_iteration`
- `runtime_controller`
- `metadata`
- `memory`
- `logger`

Instead, it is a **cross-cutting observation layer** attached to the normal
runtime path.

It should:

- consume existing runtime facts;
- normalize them into durable trajectory records;
- persist them safely;
- expose them to later analysis modules.

Current event-to-metadata mapping guidance lives in:

- `docs/task_trajectory/TASK_TRAJECTORY_EVENT_ALIGNMENT.md`
- `docs/task_trajectory/TASK_TRAJECTORY_ID_STRATIFICATION.md`

In short:

```text
OpenPilot runtime executes tasks
    ↓
Trajectory evidence layer observes execution
    ↓
Durable run/event/artifact records are stored
    ↓
Diagnostics / clustering / human review consume those records
```

---

## 3. Design principles

### 3.1 Evidence-first

Collect evidence before claiming root cause.

### 3.2 Runtime-native

Observe the real OpenPilot task path, not a separate synthetic loop.

### 3.3 Success and failure symmetry

Successful runs are evidence too.

### 3.4 Contract reuse first

Prefer existing metadata contracts before inventing new ones.

### 3.5 No behavior coupling

Evidence collection must not change runtime decisions.

### 3.6 Bounded storage

Large payloads must be stored as artifacts, not inline in every event.

### 3.7 Layer separation

Trajectory collection, issue analysis, validation design, and repair must stay
separate.

---

## 4. System layers

## 4.1 Capture layer

Responsibility:

- observe runtime events from existing execution points.

Sources:

- CLI / task entry
- route selection
- runtime phase updates
- tool loop events
- verification
- runtime completion

Outputs:

- normalized internal event payloads.

## 4.2 Normalization layer

Responsibility:

- convert diverse runtime facts into stable evidence records.

Inputs:

- raw task text
- route metadata
- runtime state metadata
- tool envelopes
- failure metadata
- log events

Outputs:

- `RunRecord`
- `EventRecord`
- `ArtifactRecord`

## 4.3 Persistence layer

Responsibility:

- durably write trajectories to disk.

Requirements:

- append-safe
- restart-safe
- partial-run-safe
- no reliance on TUI output parsing

## 4.4 Analysis bridge layer

Responsibility:

- expose evidence to later diagnostics and summarization.

This is where current `runtime_diagnostics` logic can evolve instead of being
replaced abruptly.

## 4.5 Downstream consumers

Examples:

- repeated-pattern summaries
- problem clustering
- suspicious-success review
- validation task design
- route-training export

---

## 5. Core data model

The architecture should converge on four durable record families.

## 5.1 RunRecord

One record for one task run.

Suggested fields:

- `run_id`
- `task_id`
- `session_id`
- `source`
- `raw_input`
- `goal`
- `route`
- `started_at`
- `finished_at`
- `final_status`
- `completion_reason`
- `success`

## 5.2 EventRecord

Chronological event stream.

Suggested fields:

- `event_id`
- `run_id`
- `sequence`
- `event_type`
- `phase`
- `created_at`
- `summary`
- `correlation`
- `evidence_refs`
- `artifact_refs`

Typical event types:

- `task_received`
- `task_card_ready`
- `route_selected`
- `runtime_phase_changed`
- `tool_called`
- `tool_succeeded`
- `tool_failed`
- `verification_started`
- `verification_finished`
- `task_finished`

## 5.3 EvidenceRecord

Structured fact payload attached to the run or event stream.

Suggested contents:

- route metadata
- runtime state snapshots
- tool input / output summaries
- failure metadata
- verification metadata
- residual risks

Note:

In the first implementation phase, `EvidenceRecord` can remain implicit inside
`EventRecord.payload` if a separate file adds unnecessary complexity.

## 5.4 ArtifactRecord

Large or lossy-separation payload.

Suggested contents:

- full stdout/stderr
- full tool output
- large JSON
- diff patch
- transcript excerpt

Suggested fields:

- `artifact_id`
- `run_id`
- `kind`
- `path`
- `content_type`
- `bytes`
- `created_at`
- `source_event_id`

---

## 6. Storage architecture

Suggested layout:

```text
Code/data/task_trajectory/<run_id>/
  run.json
  events.jsonl
  artifacts/
  summary.json
```

Meaning:

- `run.json`: stable run header
- `events.jsonl`: append-only event trail
- `artifacts/`: larger payloads
- `summary.json`: derived quick-look summary

Why this shape:

- easy to inspect locally;
- easy to diff;
- easy to export later;
- resilient to partial interruption.

---

## 7. Integration with existing project modules

## 7.1 `ui/`

Use only for:

- task entry capture
- route visibility

Do not treat terminal rendering as the source of truth.

## 7.2 `autonomous_iteration/`

This is the primary runtime evidence source.

Capture:

- task received
- state transitions
- completion
- blocked / recover / summarize phase changes

## 7.3 `core/`

Capture:

- tool event loop events
- LLM request/response envelopes where useful
- logger events where they already exist

## 7.4 `metadata/`

Remain the source of structured contracts.

Do not move business logic into `metadata/`.

## 7.5 `memory/`

Treat memory-derived context as evidence input when useful, but do not let the
trajectory layer become a memory subsystem.

---

## 8. Suggested package evolution

Current package:

```text
Code/src/runtime_diagnostics/
```

Recommended near-term strategy:

- keep this package path for now;
- evolve it from "problem diagnostics" into "trajectory evidence + analysis bridge";
- avoid a premature rename that creates more churn than value.

Suggested internal split:

```text
runtime_diagnostics/
├── models.py          # run/event/artifact records
├── collector.py       # capture + normalization helpers
├── recorder.py        # persistence
├── hooks.py           # runtime hook entry points
├── summarizer.py      # summary views over trajectories
├── report.py          # human-readable summaries
├── judge.py           # conservative problem judgment (downstream)
├── runner.py          # task-pool execution helper
└── task_pool.py       # raw task pool loading
```

Longer-term optional split:

```text
runtime_evidence/
runtime_analysis/
```

But that should happen only if the current package becomes too mixed.

---

## 9. Relationship to diagnostics

Diagnostics becomes a downstream interpretation layer.

Meaning:

- evidence layer asks: **what happened?**
- diagnostics asks: **what might be wrong?**

This distinction is important:

- evidence should preserve facts;
- diagnostics may generate hypotheses;
- repair remains out of scope.

---

## 10. Relationship to future routing / training

The system should eventually export:

- task input
- route decision
- key trajectory signals
- success/failure outcome

This makes the evidence layer a future data backbone for:

- route learning
- tool selection policy
- verification strategy learning
- risk / governance tuning

The architecture should therefore preserve:

- stable IDs
- chronological order
- route and outcome linkage

---

## 11. Safety and performance constraints

### Safety

- no code modification from evidence collection;
- no behavior override of the runtime;
- no silent swallowing of real runtime failures.

### Performance

- avoid writing huge payloads inline;
- use bounded previews in event records;
- store oversized content in artifacts;
- allow future collection levels if needed.

---

## 12. Initial implementation scope

The first practical version should do only this:

1. capture task start / route / finish
2. capture tool success / failure
3. capture runtime phase snapshots at important boundaries
4. persist one run directory per execution
5. generate a lightweight summary

Do not start with:

- distributed tracing backend integration
- full transcript reconstruction
- automatic validation-task generation
- training export pipelines

Those come later, after the core record model is stable.

---

## 13. Summary

The task trajectory evidence system should become OpenPilot's durable execution
observation backbone.

It should be:

- runtime-native
- metadata-aligned
- evidence-first
- analysis-friendly
- future-trainable

And it should remain clearly separate from automatic repair.
