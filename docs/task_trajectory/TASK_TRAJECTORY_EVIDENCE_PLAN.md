# TASK_TRAJECTORY_EVIDENCE_PLAN.md

## Purpose

This plan turns the task trajectory evidence layer into a staged implementation
that fits OpenPilot's existing architecture.

The plan follows these constraints:

- reuse existing contracts first;
- keep changes small and localized;
- do not create a repair engine;
- collect both success and failure trajectories;
- keep the evidence layer compatible with future route-training use.

## Phase 0: Document cleanup and naming alignment

Goal:

Align the old test-diagnostics language with the new trajectory-evidence
direction.

Scope:

- keep `THOUGHT_ARCHITECTURE.md` and `Thought.md` unchanged;
- remove `test_diagnostics/` from the active repository so it does not pollute the main codebase;
- convert the old `REAL_TASK_DIAGNOSTICS*` docs into pointers or archive notes;
- make `docs/testing/TEST_DESIGN_GUIDE.md` describe how trajectory evidence supports testing.

Exit criteria:

- new documentation names the trajectory evidence layer explicitly;
- old test-only narrative is no longer the primary story.

## Phase 1: Evidence schema and record model

Goal:

Define the minimal durable record set for one task run.

Scope:

- add / refine models for run, event, evidence, artifact;
- reuse current metadata instead of inventing a new task-spec contract;
- keep raw task input lightweight and external-facing;
- preserve strict schema/version fields.

Recommended record families:

- `RunRecord`
- `EventRecord`
- `EvidenceRecord`
- `ArtifactRecord`

Exit criteria:

- a single run can be represented as a structured trajectory;
- evidence can be serialized without depending on terminal output;
- large payloads can be separated from the main stream.

## Phase 2: Runtime hook integration

Goal:

Attach evidence collection to the real OpenPilot runtime path.

Scope:

- hook task receipt and route selection;
- hook runtime phase transitions and completion;
- hook tool calls, tool success, and tool failure;
- hook verification outcomes where the runtime already knows them;
- avoid making diagnostics influence execution decisions.

Preferred hook points:

- `Code/src/ui/enhanced_cli.py`
- `Code/src/autonomous_iteration/runtime_controller.py`
- `Code/src/core/tool_event_loop.py`
- `Code/src/autonomous_iteration/intelligent_autopilot.py`

Exit criteria:

- a normal `openpilot run --once ...` execution can emit trajectory data;
- evidence collection failures do not fail the main task;
- success and failure runs both produce usable records.

## Phase 3: Persistent trajectory storage

Goal:

Store each task execution as a replayable trajectory.

Scope:

- choose a run directory layout under `Code/data/`;
- write append-only event streams;
- store large artifacts separately;
- create a compact run summary for human review.

Suggested layout:

```text
Code/data/task_trajectory/<run_id>/
  run.json
  events.jsonl
  artifacts/
  summary.json
```

Exit criteria:

- one run can be inspected without reconstructing state from terminal output;
- the stored trajectory is stable enough to replay, summarize, and cluster.

## Phase 4: Stage summaries and issue analysis

Goal:

Turn trajectories into human-readable summaries and issue signals.

Scope:

- summarize by phase, tool, route, and task;
- highlight repeated patterns;
- extract problem signals from trajectory evidence;
- keep first-pass judgments conservative;
- preserve links back to the raw trajectory.

Exit criteria:

- summaries reference the underlying evidence records;
- repeated patterns are visible without reading every raw event;
- no automatic repair is introduced.

## Phase 5: Validation-task generation

Goal:

When a suspected common root appears, generate targeted validation tasks.

Scope:

- derive validation candidates from repeated trajectories;
- run those candidates through the same evidence layer;
- compare the resulting trajectories against the hypothesis;
- keep validation separate from repair.

Exit criteria:

- a hypothesis can be strengthened or weakened by new evidence;
- validation results are stored as trajectory evidence;
- code remains unchanged unless the user opens a separate repair task.

## Phase 6: Dataset export for future routing

Goal:

Convert trajectories into training / evaluation data for future routing and
governance work.

Scope:

- export `(input, route, outcome)` triples;
- export evidence-backed failure clusters;
- export success trajectories as positive examples;
- keep the export format simple and reproducible.

Exit criteria:

- future routing work can reuse the trajectory store;
- training data is derived from real runs, not synthetic assumptions.

## Borrowed design patterns

- OpenHands: durable trajectory + replay + OTEL observability
- OpenCode: durable session context, boundary-driven admission, event stream
- Claude Code: transcript / session persistence and trace-style telemetry
- Langfuse: sessions, traces, observations
- OpenTelemetry: traces, spans, logs, exporters

## Implementation order

1. document cleanup
2. schema / model alignment
3. runtime hooks
4. persistent storage
5. summaries
6. validation tasks
7. route-training export

## Non-goals

- no automatic code repair;
- no over-designed benchmark framework;
- no hard dependency on one model family;
- no terminal output parsing as the primary truth source.
