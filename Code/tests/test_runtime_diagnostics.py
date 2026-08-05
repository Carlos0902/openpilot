from __future__ import annotations

from types import SimpleNamespace

from core.llm import LLMMessage, LLMRequest, LLMResponse
from core.exceptions import InvalidLLMResponseError
from memory.context_assembly import ContextAssembler, ContextRequestBuilder
from metadata import (
    ContextAssemblyPolicy,
    ContextRequestPurpose,
    ContextSelectionMetadata,
    FailureMetadata,
    GuardDecisionMetadata,
    LogEventMetadata,
    ReasoningEffort,
    ReasoningMode,
    ReasoningPolicy,
    ResultStatus,
    RuntimeResumeDecisionMetadata,
    ToolCallMetadata,
    ToolErrorMetadata,
    ToolExecutionEnvelopeMetadata,
    ToolInputMetadata,
)
from metadata.agent_runtime import AgentPhase, RuntimeStateMetadata

from autonomous_iteration.runtime_controller import AgentRuntimeController, StateUpdater
from core.tool_event_loop import ToolEventLoopRunner
from runtime_diagnostics import (
    DiagnosticRecorder,
    RawTaskInput,
    collect_from_failure,
    collect_from_runtime_state,
    collect_from_tool_error,
    judge_signal,
    render_summary_markdown,
    summarize_records,
    suspicious_success_signal,
    write_summary_markdown,
    TrajectoryLLMClientProxy,
)
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks
from runtime_diagnostics.report import generate_stage_summary
from tools.tool_selection import SelectionReason, ToolSelection


def test_raw_task_input_is_minimal_external_wrapper() -> None:
    raw = RawTaskInput(
        task_id="manual_001",
        source="manual",
        raw_input="请总结这个项目的核心模块",
        tags=["understanding", "readonly"],
    )

    payload = raw.to_task_payload()

    assert payload["goal"] == "请总结这个项目的核心模块"
    assert payload["task_id"] == "manual_001"
    assert payload["tags"] == ["understanding", "readonly"]


def test_collect_from_tool_error_reuses_problem_signal_metadata() -> None:
    error = ToolErrorMetadata(
        session_id="s1",
        task_id="task-1",
        step_id="step-1",
        call_id="call-1",
        tool_name="file_reader",
        error_type="FileNotFoundError",
        error_message="missing file",
        suggested_recovery="check path",
    )

    signal = collect_from_tool_error(error)

    assert signal.category == "environment"
    assert signal.task_id == "task-1"
    assert signal.tool_name == "file_reader"
    assert "missing file" in signal.message


def test_collect_from_failure_and_judge_signal() -> None:
    failure = FailureMetadata(
        error_type="AssertionError",
        error_message="verification failed",
        recoverable=False,
    )

    signal = collect_from_failure(failure, source="verification", task_id="task-2")
    judgment = judge_signal(signal)

    assert signal.category == "tool_execution"
    assert judgment.is_problem is True
    assert judgment.severity == "high"
    assert judgment.recommended_repair_kind == "tool_failure_review"


def test_collect_from_runtime_state_detects_verification_failure() -> None:
    state = RuntimeStateMetadata(
        goal="verify result",
        phase=AgentPhase.VERIFY,
        verification_status="failed",
    )

    signals = collect_from_runtime_state(state)

    assert len(signals) == 1
    assert signals[0].category == "verification"


def test_recorder_and_summarizer_round_trip(tmp_path) -> None:
    recorder = DiagnosticRecorder(tmp_path)
    signal = suspicious_success_signal(
        task_id="task-3",
        evidence=["success=true", "verification_status=not_started"],
    )
    judgment = judge_signal(signal)

    record = recorder.record_judgment(signal, judgment)
    records = recorder.load_recent_records()
    summary = summarize_records(records)

    assert record.task_id == "task-3"
    assert len(records) == 1
    assert summary.total_records == 1
    assert summary.by_category == {"suspicious_success": 1}
    assert summary.by_severity == {"review": 1}


def test_hooks_are_no_throw_and_record_tool_failures(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))
    error = {
        "task_id": "task-4",
        "tool_name": "command_executor",
        "error_type": "ModuleNotFoundError",
        "error_message": "No module named demo",
        "suggested_recovery": "install dependency",
    }

    record_ids = hooks.on_tool_failed(error)
    records = hooks.recorder.load_recent_records()

    assert record_ids
    assert records[0]["signal"]["category"] == "environment"


def test_guard_decision_hook_records_typed_block_event(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))
    decision = GuardDecisionMetadata(
        approved=False,
        reason="runtime is read-only",
        attributes={"need_type": "file_write", "tool_name": "file_writer"},
    )

    hooks.on_guard_decision(task_id="task-guard", session_id="session-guard", decision=decision)

    run = hooks.recorder.load_run("task-guard")
    events = hooks.recorder.load_trajectory_events(run.run_id if run else "")
    assert run is not None
    assert events[-1]["event_type"] == "decision_need_blocked"
    assert events[-1]["payload"]["attributes"]["need_type"] == "file_write"


def test_new_recorder_appends_resume_events_to_explicit_existing_run(tmp_path) -> None:
    data_dir = tmp_path / "diagnostics"
    original_hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(data_dir))
    original_hooks.on_task_received(
        task_id="root-task",
        source="test",
        raw_input="repair project",
        session_id="original-session",
    )
    original_run = original_hooks.recorder.load_run("root-task")
    assert original_run is not None

    replacement_hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(data_dir))
    replacement_hooks.on_resume_preflight_completed(
        RuntimeResumeDecisionMetadata(
            checkpoint_id="checkpoint-1",
            run_id=original_run.run_id,
            root_task_id="root-task",
            session_id="original-session",
            resume_attempt_id="resume-1",
            decision="exact_resume",
            safe_boundary="task_normalized",
            reason="safe to resume",
            next_action="continue",
        )
    )

    run_dirs = [item for item in replacement_hooks.recorder.trajectory_dir.iterdir() if item.is_dir()]
    events = replacement_hooks.recorder.load_trajectory_events(original_run.run_id, limit=0)
    assert [item.name for item in run_dirs] == [original_run.run_id]
    assert [event["event_type"] for event in events] == ["task_received", "resume_preflight_completed"]
    assert events[-1]["run_id"] == original_run.run_id
    assert events[-1]["payload"]["recoverability"] == "recoverable_now"
    assert events[-1]["payload"]["recovery_mode"] == "exact_resume"
    assert events[-1]["payload"]["reason_code"] == "checkpoint_valid"
    assert events[-1]["summary"] == "resume preflight: recoverable_now/exact_resume"


def test_separate_recorders_append_strictly_increasing_event_sequences(tmp_path) -> None:
    data_dir = tmp_path / "diagnostics"
    first = RuntimeDiagnosticsHooks(DiagnosticRecorder(data_dir))
    first.on_task_received(
        task_id="root-task",
        source="test",
        raw_input="inspect",
        session_id="session-1",
    )
    run = first.recorder.load_run("root-task")
    assert run is not None

    second = RuntimeDiagnosticsHooks(DiagnosticRecorder(data_dir))
    second.recorder.attach_existing_run(
        run.run_id,
        expected_task_id="root-task",
        expected_session_id="session-1",
    )
    second.on_log_event(
        task_id="root-task",
        session_id="session-1",
        source_name="test",
        phase="execute",
        event_type="second_writer_event",
        success=True,
    )
    first.on_log_event(
        task_id="root-task",
        session_id="session-1",
        source_name="test",
        phase="execute",
        event_type="first_writer_event",
        success=True,
    )

    events = first.recorder.load_trajectory_events(run.run_id, limit=0)
    assert [event["sequence"] for event in events] == [1, 2, 3]


def test_agent_runtime_controller_records_task_finish_and_suspicious_success(tmp_path) -> None:
    class FakeSessionRunner:
        def run(self, goal, context, mode="standard"):
            return {
                "success": True,
                "task_id": context.get("task_id"),
                "stats": {"tasks_completed": 1, "tasks_failed": 0},
            }

    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))
    runtime = type("Runtime", (), {"tool_registry": None, "runtime_diagnostics_hooks": hooks, "session_id": "session-1"})()
    controller = AgentRuntimeController(runtime, session_executor=FakeSessionRunner())

    result = controller.run("Build app", {"project_path": "/tmp/project", "task_id": "task-5"}, mode="standard")

    assert result["success"] is True
    records = hooks.recorder.load_recent_records()
    run = hooks.recorder.load_run("task-5")
    trajectory_events = hooks.recorder.load_trajectory_events(run.run_id if run else "")
    assert records
    assert records[0]["signal"]["category"] == "suspicious_success"
    assert run is not None
    assert "runtime_phase_changed" in [event["event_type"] for event in trajectory_events]
    assert "task_finished" in [event["event_type"] for event in trajectory_events]


def test_tool_event_loop_records_tool_error_via_runtime_hooks(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))

    class FakeRuntime:
        runtime_diagnostics_hooks = hooks

    class FakeOwner:
        def __init__(self) -> None:
            self.runtime = FakeRuntime()

        def _log(self, *args, **kwargs):
            return None

    runner = ToolEventLoopRunner(FakeOwner())
    input_metadata = ToolInputMetadata(tool_name="file_reader", file_path="missing.txt")
    tool_call = ToolCallMetadata(
        session_id="session-2",
        task_id="task-6",
        step_id="step-1",
        call_id="call-1",
        tool_name="file_reader",
        input_metadata=input_metadata,
    )
    tool_error = ToolErrorMetadata(
        session_id="session-2",
        task_id="task-6",
        step_id="step-1",
        call_id="call-1",
        tool_name="file_reader",
        error_type="FileNotFoundError",
        error_message="missing file",
        recoverable=True,
        input_metadata=input_metadata,
    )

    runner._record_tool_error("task-6", tool_call, tool_error, 1)

    records = hooks.recorder.load_recent_records()
    assert records
    assert records[0]["signal"]["signal_source"] == "tool_error"
    assert records[0]["signal"]["source"] == {
        "source_type": "system",
        "source_name": "openpilot",
    }

from runtime_diagnostics import RuntimeTaskPoolRunner, load_raw_tasks


def test_load_raw_tasks_from_jsonl_and_json(tmp_path) -> None:
    jsonl_path = tmp_path / "tasks.jsonl"
    jsonl_path.write_text(
        '{"task_id":"a","source":"manual","raw_input":"one","attachments":[],"tags":[]}\n'
        '{"task_id":"b","source":"manual","raw_input":"two","attachments":[],"tags":["x"]}\n',
        encoding="utf-8",
    )
    json_path = tmp_path / "task.json"
    json_path.write_text(
        '{"task_id":"c","source":"manual","raw_input":"three","attachments":[],"tags":[]}',
        encoding="utf-8",
    )

    tasks_jsonl = load_raw_tasks(jsonl_path)
    tasks_json = load_raw_tasks(json_path)

    assert [task.task_id for task in tasks_jsonl] == ["a", "b"]
    assert [task.task_id for task in tasks_json] == ["c"]


def test_runtime_task_pool_runner_passes_task_context_and_records_runs(tmp_path) -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def fake_executor(goal: str, context: dict[str, object]) -> dict[str, object]:
        seen.append((goal, context))
        return {
            "success": True,
            "goal": goal,
            "runtime_report": {
                "phase": "summarize",
                "verification_status": "not_required",
                "completion_reason": "ok",
            },
        }

    runner = RuntimeTaskPoolRunner(fake_executor, recorder=DiagnosticRecorder(tmp_path))
    task = RawTaskInput(
        task_id="manual_010",
        source="manual",
        raw_input="请总结核心模块",
        tags=["understanding"],
        context={"project_path": "/tmp/project"},
    )

    results = runner.run_tasks([task])

    assert len(results) == 1
    assert results[0].success is True
    assert seen[0][0] == "请总结核心模块"
    assert seen[0][1]["task_id"] == "manual_010"
    assert seen[0][1]["source"] == "manual"
    assert seen[0][1]["project_path"] == "/tmp/project"
    runs = runner.recorder.runs_file.read_text(encoding="utf-8")
    assert '"event": "task_pool_item_started"' in runs
    assert '"event": "task_pool_item_finished"' in runs


def test_summarizer_highlights_repeated_signals_and_renders_markdown(tmp_path) -> None:
    recorder = DiagnosticRecorder(tmp_path)
    signal_a = suspicious_success_signal(
        task_id="task-a",
        message="Task reported success without enough verification evidence",
        evidence=["verification_status=not_started"],
    )
    signal_b = suspicious_success_signal(
        task_id="task-b",
        message="Task reported success without enough verification evidence",
        evidence=["verification_status=not_started"],
    )
    recorder.record_judgment(signal_a, judge_signal(signal_a))
    recorder.record_judgment(signal_b, judge_signal(signal_b))

    summary = summarize_records(recorder.load_recent_records(limit=0))
    markdown = render_summary_markdown(summary)
    output_path = write_summary_markdown(summary, tmp_path / "summary.md")

    assert summary.suspicious_success_count == 2
    assert summary.repeated_signals
    assert summary.repeated_signals[0].count == 2
    assert "Repeated Signals" in markdown
    assert "task-a" in markdown
    assert output_path.read_text(encoding="utf-8").startswith("# Runtime Diagnostics Summary")


def test_recorder_load_run_events(tmp_path) -> None:
    recorder = DiagnosticRecorder(tmp_path)
    recorder.record_run({"event": "task_pool_item_started", "task_id": "t1"})
    recorder.record_run({"event": "task_pool_item_finished", "task_id": "t1", "success": True})

    events = recorder.load_run_events(limit=0)

    assert [event["event"] for event in events] == ["task_pool_item_started", "task_pool_item_finished"]


def test_recorder_creates_run_directory_and_event_stream(tmp_path) -> None:
    recorder = DiagnosticRecorder(tmp_path)

    event = recorder.record_event(
        "task-trajectory-1",
        event_type="task_received",
        source="manual",
        raw_input="请梳理架构",
        session_id="session-trajectory-1",
        payload=LogEventMetadata(
            source_type="system",
            source_name="openpilot",
            phase="entry",
            event_type="task_received",
            input_summary={
                "task_id": "task-trajectory-1",
                "source": "manual",
                "raw_input": "请梳理架构",
                "session_id": "session-trajectory-1",
            },
        ),
    )
    recorder.record_event(
        "session-trajectory-1",
        event_type="task_finished",
        session_id="session-trajectory-1",
        payload=LogEventMetadata(
            source_type="system",
            source_name="openpilot",
            phase="summarize",
            event_type="task_finished",
            success=True,
            output_summary={
                "task_id": "task-trajectory-1",
                "summary": {"completion_reason": "runtime session completed", "phase": "summarize"},
                "session_id": "session-trajectory-1",
            },
        ),
    )

    run = recorder.load_run(event.run_id)
    trajectory_events = recorder.load_trajectory_events(event.run_id)
    summary = recorder.load_run_summary(event.run_id)

    assert run is not None
    assert run.task_id == "task-trajectory-1"
    assert run.session_id == "session-trajectory-1"
    assert run.final_status == "success"
    assert len(trajectory_events) == 2
    assert [item["event_type"] for item in trajectory_events] == ["task_received", "task_finished"]
    assert trajectory_events[0]["payload_kind"] == "log_event"
    assert trajectory_events[1]["payload_kind"] == "log_event"
    assert trajectory_events[0]["payload"]["correlation"]["task_id"] == "task-trajectory-1"
    assert trajectory_events[0]["payload"]["correlation"]["session_id"] == "session-trajectory-1"
    assert summary is not None
    assert summary.final_status == "success"
    assert summary.event_count == 2


def test_hooks_correlate_session_events_into_one_run(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))

    hooks.on_task_received(
        task_id="manual_task_001",
        source="manual",
        raw_input="请总结模块",
        session_id="session-xyz",
    )
    hooks.on_task_card_ready(
        task_id="session-xyz",
        session_id="session-xyz",
        task_card={"goal": "请总结模块", "task_type": "document_summary"},
    )
    hooks.on_task_finished(
        task_id="manual_task_001",
        success=False,
        session_id="session-xyz",
        summary={"completion_reason": "runtime session failed", "phase": "recover"},
    )

    run = hooks.recorder.load_run("session-xyz")
    assert run is not None
    assert run.task_id == "manual_task_001"
    assert run.session_id == "session-xyz"
    assert run.goal == "请总结模块"
    assert run.final_status == "failed"
    events = hooks.recorder.load_trajectory_events(run.run_id)
    assert [event["event_type"] for event in events] == ["task_received", "task_card_ready", "task_finished"]


def test_hooks_record_phase_verification_and_tool_events(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))

    hooks.on_task_received(
        task_id="task-phase-1",
        source="manual",
        raw_input="请检查写文件流程",
        session_id="session-phase-1",
    )
    hooks.on_runtime_phase_changed(
        task_id="task-phase-1",
        session_id="session-phase-1",
        previous_phase="understand_task",
        phase="verify",
        verification_status="required",
        completion_reason="",
    )
    hooks.on_verification_state_changed(
        task_id="task-phase-1",
        session_id="session-phase-1",
        previous_status="not_started",
        verification_status="required",
        phase="verify",
        reason="file modified",
    )
    input_metadata = ToolInputMetadata(tool_name="file_writer", file_path="README.md")
    tool_call = ToolCallMetadata(
        session_id="session-phase-1",
        task_id="subtask-phase-1",
        step_id="step-1",
        call_id="call-1",
        tool_name="file_writer",
        input_metadata=input_metadata,
        round_index=1,
    )
    hooks.on_tool_started(tool_call=tool_call)
    hooks.on_tool_completed(
        task_id="subtask-phase-1",
        session_id="session-phase-1",
        tool_execution=ToolExecutionEnvelopeMetadata(
            tool_name="file_writer",
            step_id="step-1",
            status=ResultStatus.SUCCESS,
            success=True,
            input_metadata=input_metadata,
            call_id="call-1",
        ),
    )

    run = hooks.recorder.load_run("session-phase-1")
    assert run is not None
    events = hooks.recorder.load_trajectory_events(run.run_id)
    summary = hooks.recorder.load_run_summary(run.run_id)
    assert [event["event_type"] for event in events] == [
        "task_received",
        "runtime_phase_changed",
        "verification_state_changed",
        "tool_called",
        "tool_succeeded",
    ]
    assert events[1]["payload_kind"] == "log_event"
    assert events[2]["payload_kind"] == "log_event"
    assert events[3]["payload_kind"] == "tool_call"
    assert events[4]["payload_kind"] == "tool_execution_envelope"
    assert events[1]["payload"]["correlation"]["task_id"] == "task-phase-1"
    assert events[1]["payload"]["correlation"]["session_id"] == "session-phase-1"
    assert events[3]["task_id"] == "task-phase-1"
    assert events[3]["payload"]["correlation"]["task_id"] == "task-phase-1"
    assert events[3]["payload"]["annotations"]["subtask_id"] == "subtask-phase-1"
    assert events[3]["payload"]["correlation"]["execution_id"] == "call-1"
    assert events[4]["payload"]["correlation"]["task_id"] == "task-phase-1"
    assert events[4]["payload"]["annotations"]["subtask_id"] == "subtask-phase-1"
    assert summary is not None
    assert summary.phase_changes == 1
    assert summary.verification_state_changes == 1
    assert summary.tool_called_count == 1
    assert summary.tool_succeeded_count == 1


def test_diagnostic_recorder_reopens_run_id_and_continues_event_sequence_after_restart(tmp_path) -> None:
    first = DiagnosticRecorder(tmp_path)
    run = first.ensure_run("root-restart-task", session_id="restart-session")
    first_event = first.record_event(run.run_id, event_type="checkpoint_created", payload={"generation": 1})

    restarted = DiagnosticRecorder(tmp_path)
    second_event = restarted.record_event(
        run.run_id,
        event_type="resume_preflight_completed",
        payload={"decision": "exact_resume"},
    )

    assert first_event.run_id == run.run_id
    assert second_event.run_id == run.run_id
    assert second_event.sequence == first_event.sequence + 1
    assert len(list((tmp_path / "task_trajectory").glob("*/run.json"))) == 1


def test_hooks_record_generic_log_event_with_correlation(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))

    hooks.on_task_received(
        task_id="root-task-1",
        source="manual",
        raw_input="请梳理真实任务链路",
        session_id="session-root-1",
    )
    hooks.on_log_event(
        task_id="subtask-trajectory-1",
        session_id="session-root-1",
        step_id="phase-1",
        source_type="module",
        source_name="autonomous_iteration.project_improvement_runtime",
        phase="project_improvement_runtime",
        event_type="pipeline_started",
        input_summary={"goal": "请梳理真实任务链路"},
        annotations={"module": "project_improvement_runtime"},
    )

    run = hooks.recorder.load_run("session-root-1")
    assert run is not None
    events = hooks.recorder.load_trajectory_events(run.run_id)
    pipeline_event = next(event for event in events if event["event_type"] == "pipeline_started")
    assert pipeline_event["payload_kind"] == "log_event"
    assert pipeline_event["payload"]["correlation"]["task_id"] == "root-task-1"
    assert pipeline_event["payload"]["annotations"]["subtask_id"] == "subtask-trajectory-1"
    assert pipeline_event["payload"]["annotations"]["module"] == "project_improvement_runtime"


def test_state_updater_emits_phase_and_verification_callbacks_without_direct_diagnostics_coupling() -> None:
    emitted: list[tuple[str, str, str, str]] = []

    def sink(state: RuntimeStateMetadata, kind: str, previous_value: str) -> None:
        emitted.append((kind, previous_value, state.phase.value if hasattr(state.phase, "value") else str(state.phase), state.verification_status))

    updater = StateUpdater(state_event_sink=sink)
    state = RuntimeStateMetadata(goal="write readme", phase=AgentPhase.EXECUTE, verification_status="not_started")
    selection = ToolSelection(
        step_id="step-1",
        tool_name="file_writer",
        reason=SelectionReason.CAPABILITY_MATCH,
        input_metadata=ToolInputMetadata(tool_name="file_writer", file_path="README.md"),
    )
    execution_result = type(
        "ExecResult",
        (),
        {
            "success": True,
            "output_metadata": type(
                "Output",
                (),
                {"result": type("Result", (), {"file_path": "README.md"})()},
            )(),
            "error": None,
        },
    )()

    updater.apply_tool_result(state, selection, execution_result)

    assert emitted == [
        ("phase", "execute", "verify", "required"),
        ("verification", "not_started", "verify", "required"),
    ]


def test_recorder_writes_artifact_and_updates_summary(tmp_path) -> None:
    recorder = DiagnosticRecorder(tmp_path)
    event = recorder.record_event(
        "task-artifact-1",
        event_type="task_received",
        source="manual",
        raw_input="请输出日志",
        session_id="session-artifact-1",
        payload=LogEventMetadata(
            source_type="system",
            source_name="openpilot",
            phase="entry",
            event_type="task_received",
            input_summary={
                "task_id": "task-artifact-1",
                "source": "manual",
                "raw_input": "请输出日志",
                "session_id": "session-artifact-1",
            },
        ),
    )

    artifact = recorder.record_artifact(
        "session-artifact-1",
        kind="stdout",
        content="hello world",
        filename="stdout.txt",
        source_event_id=event.event_id,
    )
    summary = recorder.load_run_summary("session-artifact-1")

    assert artifact.path.endswith("stdout.txt")
    assert summary is not None
    assert summary.artifact_count == 1


def test_llm_proxy_records_request_response_events_and_artifacts(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))

    class FakeLLMClient:
        settings = SimpleNamespace(model="demo-model", provider="demo-provider")

        def complete(self, request, max_retries=3, use_cache=True, stream_callback=None):
            return LLMResponse(
                content='{"task_type":"document_summary"}',
                parsed_json={"task_type": "document_summary"},
                model="demo-model",
                provider="demo-provider",
                usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                finish_reason="stop",
                provider_details={"transport_retry_history": []},
            )

    proxy = TrajectoryLLMClientProxy(
        FakeLLMClient(),
        hooks=hooks,
        task_id_getter=lambda: "task-llm-1",
        session_id_getter=lambda: "session-llm-1",
        phase_getter=lambda: "understand_task",
        goal_getter=lambda: "请总结项目结构",
    )

    response = proxy.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="请总结项目结构")],
            response_format="json_object",
            trace_info={"semantic_task": "goal"},
            context_selection=ContextSelectionMetadata(
                max_prompt_chars=1000,
                original_prompt_chars=8,
                final_prompt_chars=8,
            ),
        )
    )

    assert response.model == "demo-model"
    run = hooks.recorder.load_run("session-llm-1")
    assert run is not None
    events = hooks.recorder.load_trajectory_events(run.run_id)
    summary = hooks.recorder.load_run_summary(run.run_id)
    assert [event["event_type"] for event in events] == ["llm_requested", "llm_responded"]
    assert events[0]["payload_kind"] == "llm_request"
    assert events[1]["payload_kind"] == "llm_response"
    assert events[0]["payload"]["correlation"]["task_id"] == "task-llm-1"
    assert events[0]["payload"]["context_selection"]["max_prompt_chars"] == 1000
    diagnostics = events[0]["payload"]["trace_info"]["diagnostics"]
    assert diagnostics["message_count"] == 1
    assert diagnostics["prompt_chars"] == len("请总结项目结构")
    assert diagnostics["response_format"] == "json_object"
    assert diagnostics["model"] == "demo-model"
    assert diagnostics["provider"] == "demo-provider"
    request_hash = events[0]["payload"]["trace_info"]["request_hash"]
    assert request_hash.startswith("v2:sha256:")
    response_details = events[1]["payload"]["provider_details"]
    assert response_details["request_hash"] == request_hash
    assert response_details["attempt_id"] == events[1]["payload"]["correlation"]["execution_id"]
    assert events[1]["payload"]["correlation"]["execution_id"].startswith("llm_")
    assert summary is not None
    assert summary.artifact_count >= 2
    artifacts = hooks.recorder._load_jsonl(hooks.recorder._artifacts_index_file(run.run_id))
    assert {artifact["kind"] for artifact in artifacts} >= {"llm_request", "llm_response_text", "llm_response_json"}


def test_llm_replay_request_hash_excludes_non_provider_context_selection_evidence() -> None:
    plain = LLMRequest(messages=[LLMMessage(role="user", content="same")])
    assembled = LLMRequest(
        messages=[LLMMessage(role="user", content="same")],
        context_selection=ContextSelectionMetadata(
            max_prompt_chars=100,
            original_prompt_chars=4,
            final_prompt_chars=4,
        ),
    )

    assert TrajectoryLLMClientProxy._request_hash(plain) == TrajectoryLLMClientProxy._request_hash(assembled)


def test_llm_replay_hash_binds_provider_model_and_resolved_reasoning() -> None:
    request = LLMRequest(messages=[LLMMessage(role="user", content="same")])
    openai_settings = SimpleNamespace(
        provider="openai",
        base_url="https://api.openai.com/v1?secret=omitted",
        model="gpt-5.6-terra",
        reasoning_capability_profile=None,
    )
    other_model = SimpleNamespace(
        provider="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-5.6-luna",
        reasoning_capability_profile=None,
    )
    low = request.model_copy(
        update={
            "reasoning_policy": ReasoningPolicy(
                mode=ReasoningMode.ENABLED,
                effort=ReasoningEffort.LOW,
            )
        }
    )

    baseline_hash = TrajectoryLLMClientProxy._request_hash(request, settings=openai_settings)

    assert baseline_hash.startswith("v2:sha256:")
    assert baseline_hash != TrajectoryLLMClientProxy._request_hash(request, settings=other_model)
    assert baseline_hash != TrajectoryLLMClientProxy._request_hash(low, settings=openai_settings)


def test_llm_replay_hash_ignores_completion_budget_audit_trace_but_binds_execution_semantics() -> None:
    settings = SimpleNamespace(
        provider="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-5.6-terra",
        reasoning_capability_profile=None,
    )
    baseline = LLMRequest(
        messages=[LLMMessage(role="user", content="same")],
        max_tokens=800,
        trace_info={
            "context_purpose": "iteration_task_design",
            "completion_budget": {
                "reservation_id": "reservation-a",
                "reserved_tokens": 800,
                "remaining_tokens": 9_000,
            },
        },
    )
    audit_only_change = baseline.model_copy(
        update={
            "trace_info": {
                **baseline.trace_info,
                "completion_budget": {
                    "reservation_id": "reservation-after-resume",
                    "reserved_tokens": 800,
                    "remaining_tokens": 8_200,
                },
            }
        }
    )
    message_change = baseline.model_copy(
        update={"messages": [LLMMessage(role="user", content="different")]}
    )
    max_tokens_change = baseline.model_copy(update={"max_tokens": 801})
    reasoning_change = baseline.model_copy(
        update={
            "reasoning_policy": ReasoningPolicy(
                mode=ReasoningMode.ENABLED,
                effort=ReasoningEffort.LOW,
            )
        }
    )
    other_provider = SimpleNamespace(
        provider="other-provider",
        base_url="https://api.openai.com/v1",
        model="gpt-5.6-terra",
        reasoning_capability_profile=None,
    )

    baseline_hash = TrajectoryLLMClientProxy._request_hash(baseline, settings=settings)

    assert TrajectoryLLMClientProxy._request_hash(audit_only_change, settings=settings) == baseline_hash
    assert TrajectoryLLMClientProxy._request_hash(message_change, settings=settings) != baseline_hash
    assert TrajectoryLLMClientProxy._request_hash(max_tokens_change, settings=settings) != baseline_hash
    assert TrajectoryLLMClientProxy._request_hash(reasoning_change, settings=settings) != baseline_hash
    assert TrajectoryLLMClientProxy._request_hash(baseline, settings=other_provider) != baseline_hash


def test_llm_replay_hash_binds_non_default_endpoint_port() -> None:
    request = LLMRequest(messages=[LLMMessage(role="user", content="same")])
    first = SimpleNamespace(
        provider="openai-compatible",
        base_url="http://localhost:8000/v1",
        model="same-model",
        reasoning_capability_profile=None,
    )
    second = SimpleNamespace(
        provider="openai-compatible",
        base_url="http://localhost:9000/v1",
        model="same-model",
        reasoning_capability_profile=None,
    )

    assert TrajectoryLLMClientProxy._request_hash(
        request, settings=first
    ) != TrajectoryLLMClientProxy._request_hash(request, settings=second)


def test_llm_proxy_records_requested_and_resolved_reasoning_policy(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))

    class FakeLLMClient:
        settings = SimpleNamespace(
            model="gpt-5.6-terra",
            provider="openai",
            base_url="https://api.openai.com/v1",
            reasoning_capability_profile=None,
            timeout_seconds=10,
        )

        def complete(self, request, max_retries=3, use_cache=True, stream_callback=None):
            return LLMResponse(
                content="{}",
                parsed_json={},
                model=self.settings.model,
                provider=self.settings.provider,
            )

    proxy = TrajectoryLLMClientProxy(
        FakeLLMClient(),
        hooks=hooks,
        task_id_getter=lambda: "reasoning-task",
        session_id_getter=lambda: "reasoning-session",
    )

    proxy.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="decide")],
            reasoning_policy=ReasoningPolicy(
                mode=ReasoningMode.ENABLED,
                effort=ReasoningEffort.LOW,
            ),
        )
    )

    run = hooks.recorder.load_run("reasoning-session")
    events = hooks.recorder.load_trajectory_events(run.run_id)
    request_payload = events[0]["payload"]
    assert request_payload["reasoning_policy"]["effort"] == "low"
    assert request_payload["resolved_reasoning_policy"]["effective_effort"] == "low"
    assert request_payload["resolved_reasoning_policy"]["profile_id"] == "openai-chat-known"


def test_llm_proxy_records_failure_event(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))

    class FailingLLMClient:
        settings = SimpleNamespace(model="demo-model", provider="demo-provider")

        def complete(self, request, max_retries=3, use_cache=True, stream_callback=None):
            raise TimeoutError("provider timed out")

    proxy = TrajectoryLLMClientProxy(
        FailingLLMClient(),
        hooks=hooks,
        task_id_getter=lambda: "task-llm-2",
        session_id_getter=lambda: "session-llm-2",
        phase_getter=lambda: "plan",
        goal_getter=lambda: "请分析模块依赖",
    )

    try:
        proxy.complete(
            LLMRequest(
                messages=[LLMMessage(role="user", content="请分析模块依赖")],
                response_format="text",
                trace_info={"purpose": "dependency_analysis"},
            )
        )
    except TimeoutError:
        pass
    else:
        raise AssertionError("Expected TimeoutError")

    run = hooks.recorder.load_run("session-llm-2")
    assert run is not None
    events = hooks.recorder.load_trajectory_events(run.run_id)
    assert [event["event_type"] for event in events] == ["llm_requested", "llm_failed"]
    assert events[1]["payload_kind"] == "failure"
    assert events[1]["payload"]["error_type"] == "TimeoutError"
    failure_diagnostics = events[1]["payload"]["details"]["diagnostics"]
    assert failure_diagnostics["message_count"] == 1
    assert failure_diagnostics["response_format"] == "text"
    assert failure_diagnostics["model"] == "demo-model"


def test_llm_proxy_records_failed_provider_attempt_usage_and_partial_response_artifact(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))

    class TruncatedLLMClient:
        settings = SimpleNamespace(model="demo-model", provider="demo-provider")

        def complete(self, request, max_retries=3, use_cache=True, stream_callback=None):
            raise InvalidLLMResponseError(
                "truncated JSON",
                response_text='{"decision_needs": [',
                usage={"completion_tokens": 77, "completion_tokens_details": {"reasoning_tokens": 70}},
                finish_reason="length",
            )

    proxy = TrajectoryLLMClientProxy(
        TruncatedLLMClient(),
        hooks=hooks,
        task_id_getter=lambda: "task-llm-truncated",
        session_id_getter=lambda: "session-llm-truncated",
    )

    try:
        proxy.complete(LLMRequest(messages=[LLMMessage(role="user", content="plan")], response_format="json_object"))
    except InvalidLLMResponseError:
        pass
    else:
        raise AssertionError("Expected InvalidLLMResponseError")

    run = hooks.recorder.load_run("session-llm-truncated")
    assert run is not None
    events = hooks.recorder.load_trajectory_events(run.run_id)
    attempt = events[-1]["payload"]["details"]["provider_attempt"]
    assert attempt["attempt_id"] == events[-1]["payload"]["correlation"]["execution_id"]
    assert attempt["request_hash"].startswith("v2:sha256:")
    assert attempt["transport_attempted"] is True
    assert attempt["json_repair_attempts"] == 0
    assert attempt["transport_retry_count"] == 0
    assert attempt["error_category"] == "validation"
    assert attempt["finish_reason"] == "length"
    assert attempt["usage"]["completion_tokens"] == 77
    artifacts = hooks.recorder._load_jsonl(hooks.recorder._artifacts_index_file(run.run_id))
    assert "llm_failed_response" in {artifact["kind"] for artifact in artifacts}


def test_generate_stage_summary_writes_markdown_and_json(tmp_path) -> None:
    recorder = DiagnosticRecorder(tmp_path / "data")
    signal = suspicious_success_signal(task_id="task-z", evidence=["verification_status=not_started"])
    recorder.record_judgment(signal, judge_signal(signal))

    md_path, json_path = generate_stage_summary(data_dir=tmp_path / "data", output_dir=tmp_path / "out")

    assert md_path.exists()
    assert json_path.exists()
    assert md_path.read_text(encoding="utf-8").startswith("# Runtime Diagnostics Summary")


def test_recorder_normalizes_subtask_payload_to_root_task(tmp_path) -> None:
    recorder = DiagnosticRecorder(tmp_path)
    recorder.record_event(
        "root-task-1",
        event_type="task_received",
        source="manual",
        raw_input="请检查根任务",
        session_id="session-root-1",
        payload=LogEventMetadata(
            source_type="system",
            source_name="openpilot",
            phase="entry",
            event_type="task_received",
            input_summary={
                "task_id": "root-task-1",
                "source": "manual",
                "raw_input": "请检查根任务",
                "session_id": "session-root-1",
            },
        ),
    )

    recorder.record_event(
        "session-root-1",
        event_type="tool_failed",
        session_id="session-root-1",
        payload={
            "kind": "tool_error",
            "tool_name": "file_reader",
            "error_type": "FileNotFoundError",
            "error_message": "missing file",
            "correlation": {
                "task_id": "subtask-root-1",
                "session_id": "session-root-1",
                "step_id": "step-9",
                "execution_id": "call-9",
            },
        },
    )

    run = recorder.load_run("session-root-1")
    assert run is not None
    events = recorder.load_trajectory_events(run.run_id)
    tool_failed = events[-1]
    assert tool_failed["task_id"] == "root-task-1"
    assert tool_failed["payload"]["correlation"]["task_id"] == "root-task-1"
    assert tool_failed["payload"]["annotations"]["subtask_id"] == "subtask-root-1"
    assert tool_failed["payload"]["annotations"]["parent_task_id"] == "root-task-1"


def test_hooks_task_finished_normalizes_subtask_to_root_task(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))
    hooks.on_task_received(
        task_id="root-finish-1",
        source="manual",
        raw_input="请完成任务",
        session_id="session-finish-1",
    )

    hooks.on_task_finished(
        task_id="subtask-finish-1",
        success=True,
        session_id="session-finish-1",
        summary={"completion_reason": "done", "phase": "summarize"},
    )

    run = hooks.recorder.load_run("session-finish-1")
    assert run is not None
    events = hooks.recorder.load_trajectory_events(run.run_id)
    finished = events[-1]
    assert finished["event_type"] == "task_finished"
    assert finished["task_id"] == "root-finish-1"
    assert finished["payload"]["correlation"]["task_id"] == "root-finish-1"
    assert finished["payload"]["annotations"]["subtask_id"] == "subtask-finish-1"


def test_task_finished_finalization_id_is_idempotent_across_hook_instances(tmp_path) -> None:
    first_hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))
    first_hooks.on_task_received(
        task_id="root-finalization",
        source="test",
        raw_input="finish once",
        session_id="session-finalization",
    )
    first = first_hooks.on_task_finished(
        task_id="root-finalization",
        success=True,
        session_id="session-finalization",
        finalization_id="finalization-1",
        summary={"completion_reason": "done", "phase": "summarize"},
    )
    second_hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))
    second_hooks.recorder.attach_existing_run(
        first_hooks.recorder.load_run("root-finalization").run_id,
        expected_task_id="root-finalization",
        expected_session_id="session-finalization",
    )
    second = second_hooks.on_task_finished(
        task_id="root-finalization",
        success=True,
        session_id="session-finalization",
        finalization_id="finalization-1",
        summary={"completion_reason": "done", "phase": "summarize"},
    )

    events = second_hooks.recorder.load_trajectory_events("root-finalization")
    finished = [event for event in events if event["event_type"] == "task_finished"]
    assert len(finished) == 1
    assert first is not None and second is not None
    assert first.event_id == second.event_id


def test_llm_proxy_replays_durable_observed_response_without_provider_call(tmp_path) -> None:
    hooks = RuntimeDiagnosticsHooks(DiagnosticRecorder(tmp_path))
    provider_calls: list[str] = []
    durable_response = LLMResponse(
        content='{"task_type":"coding"}',
        parsed_json={"task_type": "coding"},
        model="durable-model",
        provider="durable-provider",
    )

    class FakeLLMClient:
        settings = SimpleNamespace(model="demo-model", provider="demo-provider")

        def complete(self, request, max_retries=3, use_cache=True, stream_callback=None):
            provider_calls.append("called")
            return durable_response

    class RecoveryHandler:
        def replay_llm_response(self, task_id, request_ordinal, request_hash):
            return durable_response

        def prepare_llm_request(self, task_id, request_ordinal, request_hash):
            raise AssertionError("durable replay must not prepare a provider request")

        def observe_llm_response(self, task_id, request_ordinal, request_hash, response):
            raise AssertionError("durable replay must not observe a second response")

    proxy = TrajectoryLLMClientProxy(
        FakeLLMClient(),
        hooks=hooks,
        task_id_getter=lambda: "task-replay",
        session_id_getter=lambda: "session-replay",
        recovery_handler=RecoveryHandler(),
    )
    prepared = ContextRequestBuilder(
        ContextAssembler(renderer=lambda _payload: "")
    ).build_messages(
        [LLMMessage(role="user", content="build app")],
        purpose=ContextRequestPurpose.SEMANTIC_GOAL,
        policy=ContextAssemblyPolicy(
            purpose=ContextRequestPurpose.SEMANTIC_GOAL,
            max_prompt_chars=1000,
        ),
        response_format="json_object",
    )

    response = proxy.complete(prepared.require_request())

    assert response == durable_response
    assert provider_calls == []
    assert prepared.assembly.selection.candidate_decisions[0].candidate_id.endswith("message:1")
