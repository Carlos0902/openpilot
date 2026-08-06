# TASK_TRAJECTORY_EVIDENCE.md

## Purpose

This document defines OpenPilot's **task trajectory evidence layer**.

The goal is not only to find bugs. The goal is to continuously capture the full
execution trail of real tasks so the project can later:

- understand how the agent actually behaves;
- diagnose failures and recoveries;
- compare successful and unsuccessful trajectories;
- derive future routing / governance / verification data;
- support careful, human-reviewed system improvement.

This layer is evidence-first, not repair-first.

## Core idea

One task run should produce one durable trajectory that includes:

- the raw task input;
- task interpretation and route selection;
- runtime state transitions;
- tool calls, results, and failures;
- verification attempts and outcomes;
- final completion reason;
- selected artifacts and bounded previews;
- enough context to replay and analyze the run later.

Both successful and failed runs matter.

## What this replaces

Earlier work focused on "test diagnostics" and "problem discovery" only.
That direction is still useful, but it is now a narrower use of the larger
trajectory evidence layer.

The evidence layer is broader:

- it covers real tasks, not only tests;
- it records success, failure, and recovery;
- it supports later clustering and hypothesis generation;
- it can feed future task-routing data.

## Existing OpenPilot building blocks to reuse

Do not invent a new top-level task contract if existing structures already fit.

Reuse:

- `TaskCard` for interpreted task shape;
- `TaskRouteMetadata` for route decisions;
- `RuntimeStateMetadata` for runtime phase / unknowns / assumptions / tool history;
- `ToolExecutionEnvelopeMetadata` for tool-level evidence;
- `FailureMetadata` and `LogEventMetadata` for failure and log facts;
- `ProblemSignalMetadata` and `ProblemJudgmentMetadata` for downstream issue analysis.

The evidence layer should sit on top of these contracts, not replace them.

## Recommended evidence model

### 1. Run record
One durable record per task run.

Suggested fields:

- `run_id`
- `task_id`
- `source`
- `raw_input`
- `goal`
- `started_at`
- `finished_at`
- `final_status`
- `completion_reason`
- `route`
- `session_id`

### 2. Event stream
Append-only chronological events.

Examples:

- `task_received`
- `route_selected`
- `runtime_phase_changed`
- `tool_called`
- `tool_succeeded`
- `tool_failed`
- `verification_required`
- `verification_passed`
- `verification_failed`
- `task_finished`

### 3. Evidence items
Structured evidence attached to events.

Examples:

- tool input summary
- tool output summary
- failure metadata
- phase transitions
- selected files
- modified files
- assumptions / unknowns
- verification outcomes
- llm response previews

### 4. Artifacts
Large payloads should stay out of the main event stream.

Examples:

- full stdout/stderr
- full diffs
- full tool outputs
- full transcripts
- large JSON responses

## Storage suggestion

Keep the current implementation home under:

```text
Code/src/runtime_diagnostics/
```

That package can act as the first concrete implementation of the evidence
layer without forcing an immediate rename.

Suggested on-disk layout:

```text
Code/data/task_trajectory/<run_id>/
  run.json
  events.jsonl
  artifacts/
  summary.json
```

## Integration points

Evidence should be collected from the normal runtime path, not from a separate
test-only loop.

High-value hooks:

- `ui.enhanced_cli`: task receipt and route selection;
- `autonomous_iteration.runtime_controller`: phase transitions, completion;
- `core.tool_event_loop`: tool call / success / failure;
- `core.llm` via wrapped runtime client: key LLM request / response / failure evidence;
- `runtime reporter / verifier`: final status and residual risk.

## Design constraints

- Default to observation, not repair.
- Do not make evidence collection change task outcomes.
- Do not depend on TUI output as the truth source.
- Record successful and failed runs alike.
- Keep raw evidence structured.
- Keep large payloads separate from the main event stream.

## Relationship to later analysis

The evidence layer is the input to later layers:

1. stage summaries;
2. issue clustering;
3. human review;
4. validation task design;
5. route-training dataset extraction.

It is not itself the place where final root cause certainty is claimed.

## External patterns to borrow

- OpenHands trajectory API and replayable event trail:  
  [Get Trajectory](https://docs.openhands.dev/api-reference/get-trajectory)
- OpenHands observability / OTEL tracing:  
  [Observability & Tracing](https://docs.openhands.dev/sdk/guides/observability)
- OpenHands trajectory visualization:  
  [Trajectory Visualizer](https://github.com/OpenHands/trajectory-visualizer)
- OpenCode durable session / context boundary design:  
  local research repo at `/Users/abab/Desktop/AI-Work-Control-Plane/research_repos/opencode/CONTEXT.md`
- Langfuse sessions / traces:  
  [Sessions](https://langfuse.com/docs/observability/features/sessions)
  and [Overview](https://langfuse.com/docs/observability/overview)
- OpenTelemetry traces / logs:  
  [Traces](https://opentelemetry.io/docs/concepts/signals/traces/)
  and [Logs](https://opentelemetry.io/docs/specs/otel/logs/)

## Current project stance

OpenPilot should keep its existing strict metadata boundaries and use the
trajectory evidence layer as the durable observational backbone.

The layer is meant to make the agent more observable and more trainable over
time, not to automate code repair.
