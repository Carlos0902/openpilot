# TASK_TRAJECTORY_EVENT_ALIGNMENT.md

## Purpose

This document defines the **current event-to-metadata alignment** for the task
trajectory evidence layer.

Its goal is to prevent drift:

- do not invent a second business schema beside `Code/src/metadata/`;
- allow a small trajectory storage shell where necessary;
- make explicit which events are already metadata-native;
- make explicit which events are still temporary bridge events.

This document describes the **current implementation target**, not an abstract
future ideal.

---

## Alignment rules

### Rule 1: trajectory shell is allowed

The following storage-oriented records may remain local to the trajectory
system:

- `RunRecord`
- `EventRecord`
- `ArtifactRecord`
- `RunSummaryRecord`

These are persistence shells, not business facts.

### Rule 2: business facts should come from existing metadata first

If a runtime fact already has a metadata contract, the trajectory event payload
 should use that metadata directly.

Primary examples:

- `TaskRouteMetadata`
- `RuntimeStateMetadata`
- `ToolCallMetadata`
- `ToolExecutionEnvelopeMetadata`
- `ToolErrorMetadata`
- `FailureMetadata`
- `LLMRequestMetadata`
- `LLMResponseMetadata`

### Rule 3: `LogEventMetadata` is a bridge, not a dumping ground

`LogEventMetadata` is acceptable when:

- the runtime fact is real and useful;
- there is no existing dedicated metadata contract for it;
- inventing a new metadata type right now would be speculative.

Typical bridge cases:

- task accepted at entry;
- task card became available;
- task finished summary.

### Rule 4: correlation should follow project contracts

Whenever possible, payload metadata should carry:

- `correlation.task_id`
- `correlation.session_id`
- `correlation.step_id`
- `correlation.execution_id`

The trajectory recorder may backfill missing correlation values, but should not
replace the project's own correlation model.

### Rule 5: do not add new metadata types just to mirror event names

An event name and a metadata type are not the same thing.

Do **not** create types like:

- `TaskReceivedMetadata`
- `ToolSucceededMetadata`
- `RouteSelectedEventMetadata`

unless there is a real semantic gap that existing metadata cannot express.

### Runtime recovery events

`checkpoint_created` uses `RuntimeCheckpointMetadata` because the payload is
the durable recovery contract itself. `resume_preflight_completed` uses
`RuntimeResumeDecisionMetadata` because the decision controls whether runtime
execution may continue. `checkpoint_write_failed` remains a `LogEventMetadata`
bridge: it records an infrastructure failure but must not become the recovery
source of truth.

The checkpoint must be durably stored before `checkpoint_created` is emitted.
Missing trajectory evidence cannot make an invalid checkpoint valid, and a
trajectory recorder failure cannot change a resume preflight decision.
Across a process boundary, the recorder must first open the checkpoint's
explicit existing `run_id` and validate its root task/session. Resume preflight,
checkpoint, phase, verification, and task-finished events then continue the
existing sequence; task/session correlation must never create a replacement
run when the authoritative run ID is already available.
`decomposition_recorded` and `subtask_result_applied` checkpoints carry the
owned session cursor. LLM/read recovery artifacts remain checksum-addressed
checkpoint references rather than trajectory payload truth. Verification
progress is represented by the checkpoint's ordered plan cursor. A resume from
an older immutable generation records `resume_source_checkpoint_id`; trajectory
sequence and new checkpoint generations still append after the current run tip.

### Project environment evidence

Read-only preflight and side-effecting setup/resync reuse
`EnvironmentSyncMetadata`; no event-specific environment contract is created.
Tool envelopes record operation, readiness, project identity, environment ID,
permission outcome, and effective interpreter. Large install output belongs in
an artifact reference, not inline trajectory data. A setup failure is an
execution-gate failure, not a debug warning. Validation evidence preserves both
the requested command and the effective bound command/environment ID. Trajectory
events remain audit evidence; readiness authority stays with the current typed
preflight observation and checkpoint comparison.

### LLM reasoning and failed-attempt evidence

LLM request evidence records both requested and resolved reasoning policy,
including capability-profile ID/version and resolution outcome. The v2 request
identity binds provider, model, sanitized normalized endpoint (with non-default
port), profile, and effective reasoning semantics. Provider-specific payload
keys do not become trajectory authority. Failed provider attempts retain safe
usage, finish reason, and a partial-response artifact reference when present;
missing reasoning usage stays unknown rather than being rewritten to zero.

An optional project-improvement failure records its pre-iteration snapshot,
explicit restored files, rollback outcome, and rollback error. Registry-backed
fast calls and module-owned improvement tools now bridge their existing typed
call/envelope to exactly one durable `tool_called` and one terminal
`tool_succeeded` or `tool_failed` event per logical invocation. Internal retries
remain in the envelope retry history. A random invocation suffix distinguishes
repeated task/step calls; tool context records the explicit execution route and
runtime phase. Diagnostics hook failure is best effort and cannot block or
repeat the business action. Fast file, README, and bounded bug-fix calls now use
the same mutation-target classification for task scope, EditGuard, checkpoint
prepare/observe/apply, and edit-budget accounting. Their pending verification
preserves the exact task command. A no-diff tool success is observed as a
failure before terminal evidence is emitted. Environment setup and arbitrary
external command effects remain outside this file-mutation guarantee.

---

## Current alignment map

## 1. `task_received`

### Capture point

- `Code/src/autonomous_iteration/intelligent_autopilot.py`

### Current payload

- `LogEventMetadata`

### Status

- **Bridge / acceptable**

### Why

The project currently has no dedicated metadata contract for “raw task entered
the runtime”. This event is still important because it anchors:

- user input;
- task source;
- initial session correlation.

### Required payload fields

- `input_summary.task_id`
- `input_summary.source`
- `input_summary.raw_input`
- `input_summary.session_id`
- `correlation.task_id`
- `correlation.session_id`

### Future direction

Keep on `LogEventMetadata` unless multiple modules start depending on a richer
task-entry contract.

---

## 2. `task_card_ready`

### Capture point

- `Code/src/autonomous_iteration/runtime_controller.py`

### Current payload

- `LogEventMetadata`
- embedded `task_card` snapshot in `output_summary`

### Status

- **Bridge / acceptable**

### Why

The project already has `TaskCard`, but it is not currently a metadata contract
under `Code/src/metadata/`. The trajectory layer should therefore record the
fact conservatively instead of inventing a parallel metadata family.

### Required payload fields

- `output_summary.task_id`
- `output_summary.task_card`
- `output_summary.session_id`
- `output_summary.finalization_id`
- `correlation.task_id`
- `correlation.session_id`

Finalization records `task_finished:<finalization_id>` as
`EventRecord.idempotency_key`. The recorder resolves the key while holding the
per-run event lock and reapplies the run projection when the event already
exists, so a replacement process repairs an interrupted projection without
appending a second completion event.

### Future direction

Only formalize this further if:

- `TaskCard` becomes a metadata-native contract; or
- downstream consumers need task-card semantics independent of log events.

---

## 3. `route_selected`

### Capture point

- `Code/src/ui/enhanced_cli.py`

### Current payload

- `TaskRouteMetadata`

### Status

- **Metadata-native / preferred**
- current trajectory integration is focused on the `autonomous_iteration` path

### Why

This is a direct business fact already modeled by the project. The trajectory
layer should not wrap route decisions in ad-hoc dicts.

### Required payload fields

- `route`
- `confidence`
- `reason`
- `correlation.task_id`

### Future direction

Keep as-is.
If the `agent_generator` path later joins the main trajectory backbone, reuse
the same payload contract instead of inventing a second route schema.

---

## 3.1 `decision_need_blocked`

### Capture point

- `Code/src/autonomous_iteration/agents/tool_planning_executor.py`
- `Code/src/runtime_diagnostics/hooks.py`

### Current payload

- `GuardDecisionMetadata`

### Required payload fields

- `approved=false`
- `reason`
- `attributes.need_type`
- `attributes.tool_name`
- `attributes.required`
- `correlation.task_id`
- `correlation.session_id`

### Semantics

This is an execution fact, not a debug-only message. A required need rejected
by the Guard cannot disappear as an empty tool selection or be masked by a
successful read from the same plan. Internal subtask identity stays in payload
annotations while trajectory correlation remains rooted at the run task.

---

## 3.2 `task_completion_rejected`

### Capture point

- `Code/src/autonomous_iteration/agents/tool_planning_executor.py`

### Current payload

- `LogEventMetadata`

### Required payload fields

- `error` describing the missing evidence
- `output_summary.planned_write_files`
- `output_summary.observed_modified_files`
- `output_summary.validation_command`
- root task/session correlation

### Semantics

This event explains why successful individual tool calls were insufficient for
task completion. Planned file targets are retained for comparison but must not
be projected into observed `changed_files`.

---

## 4. `runtime_phase_changed`

### Capture point

- `Code/src/autonomous_iteration/runtime_controller.py`

### Current payload

- preferred: `RuntimeStateMetadata`
- fallback: `LogEventMetadata`

### Status

- **Metadata-first with bridge fallback**

### Why

The real business fact is the runtime state snapshot, not merely a string
transition. When `RuntimeStateMetadata` is available, it should be the payload.
The log-event fallback only exists to keep hooks safe and narrow.

### Required payload fields

Preferred:

- `RuntimeStateMetadata.phase`
- `RuntimeStateMetadata.verification_status`
- `RuntimeStateMetadata.completion_reason`
- `RuntimeStateMetadata.execution_mode`
- `RuntimeStateMetadata.execution_mode_source`
- `RuntimeStateMetadata.execution_mode_reason`
- `RuntimeStateMetadata.guard_history`
- `correlation.task_id`
- `correlation.session_id`

Fallback:

- `output_summary.previous_phase`
- `output_summary.phase`
- `output_summary.verification_status`
- `output_summary.completion_reason`

### Future direction

Reduce fallback usage instead of inventing a new phase-event metadata type.

---

## 5. `verification_state_changed`

### Capture point

- `Code/src/autonomous_iteration/runtime_controller.py`

### Current payload

- preferred: `RuntimeStateMetadata`
- fallback: `LogEventMetadata`

### Status

- **Metadata-first with bridge fallback**

### Why

Verification status is already part of runtime state. Reusing
`RuntimeStateMetadata` keeps the evidence aligned with the real state machine.

### Required payload fields

Preferred:

- `RuntimeStateMetadata.verification_status`
- `RuntimeStateMetadata.phase`
- `RuntimeStateMetadata.completion_reason`
- `correlation.task_id`
- `correlation.session_id`

Fallback:

- `output_summary.previous_status`
- `output_summary.verification_status`
- `output_summary.phase`
- `output_summary.reason`

### Future direction

Keep state-first. Do not split this into a separate verification-event contract
unless the runtime itself grows one.

---

## 6. `tool_called`

### Capture point

- `Code/src/core/tool_event_loop.py`

### Current payload

- `ToolCallMetadata`

### Status

- **Metadata-native / preferred**

### Why

This is already a first-class runtime contract. It carries:

- tool identity;
- input metadata;
- step and call correlation;
- tool context.

### Required payload fields

- `task_id`
- `session_id`
- `step_id`
- `call_id`
- `tool_name`
- `input_metadata`
- `correlation.task_id`
- `correlation.session_id`
- `correlation.step_id`
- `correlation.execution_id`

### Future direction

Keep as-is.

---

## 7. `tool_succeeded`

### Capture point

- `Code/src/core/tool_event_loop.py`

### Current payload

- `ToolExecutionEnvelopeMetadata`

### Status

- **Metadata-native / preferred**

### Why

This payload already expresses the real business fact:

- success status;
- tool input;
- tool output;
- failure slot if present;
- retry / duration / attempt data.

### Required payload fields

- `tool_name`
- `step_id`
- `status`
- `success`
- `input_metadata`
- `output_metadata`
- `call_id`
- `tool_context`
- `correlation.task_id`
- `correlation.session_id`
- `correlation.step_id`
- `correlation.execution_id`

### Future direction

Keep as-is. Large output should move to `ArtifactRecord` when needed, not to a
new event schema.

---

## 8. `tool_failed`

### Capture point

- `Code/src/core/tool_event_loop.py`

### Current payload

- `ToolErrorMetadata`

### Status

- **Metadata-native / preferred**

### Why

The runtime already distinguishes recoverable and terminal tool errors. The
trajectory layer should preserve that typed fact directly.

### Required payload fields

- `task_id`
- `session_id`
- `step_id`
- `call_id`
- `tool_name`
- `error_type`
- `error_message`
- `recoverable`
- `failure`
- `correlation.task_id`
- `correlation.session_id`
- `correlation.step_id`
- `correlation.execution_id`

### Future direction

Keep as-is.

---

## 9. `task_finished`

### Capture point

- `Code/src/autonomous_iteration/runtime_controller.py`

### Current payload

- `LogEventMetadata`

### Status

- **Bridge / acceptable**

### Why

There is no dedicated “task completion event” metadata contract today.
However, the event is still necessary to anchor:

- final success flag;
- completion reason;
- final phase;
- final verification state.

### Required payload fields

- `success`
- `output_summary.task_id`
- `output_summary.summary.phase`
- `output_summary.summary.verification_status`
- `output_summary.summary.completion_reason`
- `output_summary.summary.modified_files`
- `output_summary.session_id`
- `correlation.task_id`
- `correlation.session_id`

### Future direction

Keep on `LogEventMetadata` unless the project introduces a stronger final-run
result envelope for cross-module use.

---

## 9.1 Enhancement completion accounting

`llm_requested.trace_info.completion_budget` records purpose, reservation ID,
reserved tokens, remaining stage tokens, and `recovery_of` when applicable.
`llm_responded` and `llm_failed` retain provider usage and finish reason under
the same logical call evidence. The authoritative apply-once reservation and
reconciliation ledgers live in checkpointed `RuntimeBudgetMetadata`.

There is currently no dedicated terminal reconciliation trajectory event.
Consequently, trajectory records can align a reservation with its provider
attempt but cannot independently reconstruct refunds, unknown-usage holds, or
the final used/reserved aggregates without the matching runtime checkpoint.
Do not claim trajectory-only accounting completeness until a typed terminal
projection is added.

---

## 10. `llm_requested`

### Capture point

- wrapped runtime LLM client under `Code/src/runtime_diagnostics/llm_proxy.py`

### Current payload

- `LLMRequestMetadata`
- request body retained as artifact

### Status

- **Metadata-native with artifact companion**

### Why

The project already has a runtime metadata contract for LLM requests, but the
contract is intentionally small. The trajectory layer therefore:

- keeps the business fact in `LLMRequestMetadata`;
- keeps the full serialized request in an artifact.

This preserves contract reuse without losing real evidence.

### Required payload fields

- `task`
- `purpose`
- `trace_info`
- `correlation.task_id`
- `correlation.session_id`
- `correlation.execution_id`

### Future direction

Keep the payload metadata-first. If more request detail is needed, prefer
artifact expansion over adding ad-hoc event fields.

---

## 11. `llm_responded`

### Capture point

- wrapped runtime LLM client under `Code/src/runtime_diagnostics/llm_proxy.py`

### Current payload

- `LLMResponseMetadata`
- response text / parsed JSON retained as artifacts where available

### Status

- **Metadata-native with artifact companion**

### Why

`LLMResponseMetadata` already captures the auditable response envelope:

- model
- provider
- token usage
- finish reason
- provider details

The response body itself is better stored as artifact evidence than forced into
the event payload.

### Required payload fields

- `model`
- `provider`
- `usage`
- `finish_reason`
- `provider_details`
- `correlation.task_id`
- `correlation.session_id`
- `correlation.execution_id`

### Future direction

Keep as-is. Large or structured response bodies should continue to live in
artifacts.

---

## 12. `llm_failed`

### Capture point

- wrapped runtime LLM client under `Code/src/runtime_diagnostics/llm_proxy.py`

### Current payload

- `FailureMetadata`
- provider-attempt usage, finish reason, and response length in `details` when
  the provider returned them
- partial failed response body retained as an `llm_failed_response` artifact
  when available

### Status

- **Metadata-native / preferred**

### Why

LLM transport and response failures are already representable by the project's
general failure contract. The trajectory layer should reuse that instead of
inventing an LLM-specific error wrapper.

### Required payload fields

- `error_type`
- `error_message`
- `recoverable`
- `retry_recommended`
- `details`
- `details.provider_attempt.usage` when reported
- `details.provider_attempt.finish_reason` when reported
- `correlation.task_id`
- `correlation.session_id`
- `correlation.execution_id`

### Future direction

Keep the failure envelope narrow. Provider attempt facts belong in its scoped
details and potentially large partial bodies belong in artifacts; neither should
be copied into controller state.

---

## 13. `pipeline_progress` / `context_loader`

### Capture point

- `Code/src/autonomous_iteration/project_improvement_runtime.py`
- context payload produced by `Code/src/memory/context_builder.py`

### Current payload

- outer `LogEventMetadata`
- nested `output_summary.context.context_selection` as
  `ContextSelectionMetadata`

### Status

- **Hybrid event with metadata-native context decision**

### Why

The progress event covers several pipeline stages and should not create a
parallel metadata type for each stage. Context selection is different: it
controls model-facing input, crosses module boundaries, and must explain budget
loss. The nested typed record therefore carries:

- maximum, original, and final prompt characters;
- authoritative per-candidate decisions plus compatible per-section aggregation;
- the contiguous dialog suffix boundary and timestamps;
- typed governance/compaction links where present;
- the current deterministic `retention_priority_order_v1` strategy (historical
  `priority_then_recency_v1` payloads remain readable).

### Required payload fields

- `kind=context_selection`
- `max_prompt_chars`
- `original_prompt_chars`
- `final_prompt_chars`
- `truncated`
- `section_decisions`
- `dialog_messages_total`
- `dialog_messages_selected`
- `dialog_start_index`

### Durable recovery alignment

Keep the display decision nested in the existing progress event. Runtime
checkpointing separately stores the selected payload as a `prompt_context`
artifact and owns a `RuntimePromptContextSnapshot` at `context_assembled`.
Resume requires matching request/Prompt hashes and artifact checksum; omitted
candidate messages remain owned by their source stores and are not duplicated.

---

## Events not yet promoted into the trajectory layer

The codebase also contains many logger-oriented structured events such as:

- `module_completed`
- `module_failed`
- `function_completed`
- `agent_completed`
- internal status transitions inside other subsystems

These should **not** automatically enter the main task trajectory stream yet.

Reason:

- they are useful logs, but not all are first-order task trajectory facts;
- importing them wholesale would create noise and schema drift;
- the current trajectory layer should stay focused on the top-level task path.

If later needed, they should enter through one of two clear paths:

1. map to an existing metadata contract already used across modules; or
2. stay as external logs and be linked by artifact/reference, not copied inline.

---

## Optional future expansions that still fit project direction

These are reasonable later additions **without changing the current principle**:

- selected `RuntimeReportMetadata` snapshots as terminal evidence
- artifact-backed stdout/stderr or large tool outputs

These are better additions than inventing new event-specific wrapper schemas.

---

## Practical decision checklist

Before adding a new trajectory event payload, ask:

1. Is there already a metadata type in `Code/src/metadata/` for this fact?
2. If yes, can I store that metadata directly?
3. If no, is `LogEventMetadata` enough as a bridge?
4. If not, is the missing concept truly reused across modules?
5. If the answer is still yes, only then consider a new metadata contract.

Default answer:

- reuse existing metadata;
- otherwise use `LogEventMetadata`;
- avoid speculative type creation.
