"""Safe hooks for attaching runtime diagnostics to OpenPilot runtime paths."""

from __future__ import annotations

import json
from typing import Any

from metadata import (
    FailureMetadata,
    GuardDecisionMetadata,
    LogEventMetadata,
    LLMRequestMetadata,
    LLMResponseMetadata,
    MetadataBase,
    ProblemSignalMetadata,
    RuntimeCheckpointMetadata,
    RuntimeResumeDecisionMetadata,
    RuntimeStateMetadata,
    TaskRouteMetadata,
    ToolCallMetadata,
    ToolErrorMetadata,
    ToolExecutionEnvelopeMetadata,
)
from metadata.base import json_safe

from runtime_diagnostics.collector import collect_from_failure, collect_from_runtime_state, collect_from_tool_error, suspicious_success_signal
from runtime_diagnostics.judge import judge_signal
from runtime_diagnostics.recorder import DiagnosticRecorder


class RuntimeDiagnosticsHooks:
    """No-throw hook facade.

    Diagnostics must never become the reason a user task fails. Hook methods
    therefore catch their own exceptions and return an empty list on diagnostic
    failures.
    """

    def __init__(self, recorder: DiagnosticRecorder | None = None, *, enabled: bool = True):
        self.recorder = recorder or DiagnosticRecorder()
        self.enabled = enabled

    def _root_task_id(self, *, task_id: str = "", session_id: str = "") -> str:
        """Resolve the durable root task id for trajectory correlation.

        Runtime metadata may carry internal subtask ids in its native fields.
        The trajectory layer keeps those native fields intact, but its
        correlation.task_id should point at the root task for the run when the
        run can be resolved from the session/task aliases already known by the
        recorder.
        """
        for key in (session_id, task_id):
            if not key:
                continue
            run = self.recorder.load_run(str(key))
            if run and run.task_id:
                return run.task_id
        return str(task_id or session_id or "")

    def _with_correlation(
        self,
        payload: MetadataBase,
        *,
        task_id: str = "",
        session_id: str = "",
        step_id: str = "",
        call_id: str = "",
    ) -> MetadataBase:
        original_task_id = str(task_id or payload.correlation.task_id or "")
        root_task_id = self._root_task_id(task_id=original_task_id, session_id=session_id) or original_task_id
        correlation = payload.correlation.model_copy(
            update={
                "task_id": root_task_id or payload.correlation.task_id,
                "session_id": session_id or payload.correlation.session_id,
                "step_id": step_id or payload.correlation.step_id,
                "execution_id": call_id or payload.correlation.execution_id,
            }
        )
        annotations = dict(payload.annotations or {})
        if original_task_id and root_task_id and original_task_id != root_task_id:
            annotations.setdefault("subtask_id", original_task_id)
            annotations.setdefault("parent_task_id", root_task_id)
        return payload.model_copy(update={"correlation": correlation, "annotations": annotations})

    def _json_text(self, value: Any) -> str:
        return json.dumps(json_safe(value), ensure_ascii=False, indent=2, sort_keys=True)

    def _record_event(
        self,
        task_key: str,
        *,
        event_type: str,
        payload: Any = None,
        session_id: str = "",
        phase: str = "",
        summary: str = "",
        source: str = "",
        raw_input: str = "",
        goal: str = "",
        route: str = "",
        idempotency_key: str = "",
    ):
        if not self.enabled:
            return None
        try:
            return self.recorder.record_event(
                task_key,
                event_type=event_type,
                payload=payload or {},
                session_id=session_id,
                phase=phase,
                summary=summary,
                source=source,
                raw_input=raw_input,
                goal=goal,
                route=route,
                payload_kind=str(getattr(payload, "kind", "") or ""),
                idempotency_key=idempotency_key,
            )
        except Exception:
            return None

    def record_signal(self, signal: ProblemSignalMetadata) -> list[str]:
        if not self.enabled:
            return []
        try:
            judgment = judge_signal(signal)
            record = self.recorder.record_judgment(signal, judgment)
            return [record.record_id]
        except Exception:
            return []

    def on_task_received(
        self,
        *,
        task_id: str,
        source: str,
        raw_input: str,
        extra: dict[str, Any] | None = None,
        session_id: str = "",
    ) -> None:
        if not self.enabled:
            return
        log_event = LogEventMetadata(
            source_type="system",
            source_name="openpilot",
            phase="entry",
            event_type="task_received",
            input_summary={
                "task_id": task_id,
                "source": source,
                "raw_input": raw_input,
                "extra": extra or {},
                "session_id": session_id,
            },
        )
        log_event = self._with_correlation(log_event, task_id=task_id, session_id=session_id)
        self._record_event(
            task_id,
            event_type="task_received",
            source=source,
            raw_input=raw_input,
            session_id=session_id,
            payload=log_event,
        )

    def on_checkpoint_created(self, checkpoint: RuntimeCheckpointMetadata) -> None:
        if not self.enabled:
            return
        payload = self._with_correlation(
            checkpoint,
            task_id=checkpoint.root_task_id,
            session_id=checkpoint.session_id,
        )
        self._record_event(
            checkpoint.run_id,
            event_type="checkpoint_created",
            session_id=checkpoint.session_id,
            phase=str(checkpoint.runtime_state.phase),
            summary=f"checkpoint {checkpoint.safe_boundary} generation {checkpoint.generation}",
            payload=payload,
        )

    def on_checkpoint_write_failed(
        self,
        *,
        task_id: str,
        session_id: str,
        safe_boundary: str,
        error: str,
    ) -> None:
        self.on_log_event(
            task_id=task_id,
            session_id=session_id,
            source_name="autonomous_iteration.checkpoint_store",
            phase="recover",
            event_type="checkpoint_write_failed",
            success=False,
            output_summary={"safe_boundary": safe_boundary},
            error=error,
        )

    def on_resume_preflight_completed(self, decision: RuntimeResumeDecisionMetadata) -> None:
        if not self.enabled:
            return
        try:
            self.recorder.attach_existing_run(
                decision.run_id,
                expected_task_id=decision.root_task_id,
                expected_session_id=decision.session_id,
            )
        except (OSError, ValueError):
            return
        payload = self._with_correlation(
            decision,
            task_id=decision.root_task_id,
            session_id=decision.session_id,
        )
        self._record_event(
            decision.run_id,
            event_type="resume_preflight_completed",
            session_id=decision.session_id,
            phase="recover",
            summary=f"resume preflight: {decision.recoverability}/{decision.recovery_mode}",
            payload=payload,
        )

    def on_guard_decision(
        self,
        *,
        task_id: str,
        session_id: str,
        decision: GuardDecisionMetadata,
    ) -> None:
        if not self.enabled:
            return
        try:
            payload = self._with_correlation(
                decision,
                task_id=task_id,
                session_id=session_id,
            )
            self._record_event(
                task_id or session_id,
                event_type="decision_need_blocked" if not decision.approved else "decision_need_approved",
                session_id=session_id,
                phase="tool_routing",
                summary=decision.reason,
                payload=payload,
            )
        except Exception:
            return

    def on_log_event(
        self,
        *,
        task_id: str = "",
        session_id: str = "",
        step_id: str = "",
        call_id: str = "",
        source_type: str = "system",
        source_name: str = "openpilot",
        phase: str = "",
        event_type: str,
        success: bool | None = None,
        duration_ms: int | None = None,
        input_summary: Any | None = None,
        output_summary: Any | None = None,
        error: str | None = None,
        trace_info: dict[str, Any] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        payload = LogEventMetadata(
            source_type=source_type,
            source_name=source_name,
            phase=phase,
            event_type=event_type,
            success=success,
            duration_ms=duration_ms,
            input_summary=input_summary,
            output_summary=output_summary,
            error=error,
            trace_info=trace_info or {},
        )
        payload = self._with_correlation(
            payload,
            task_id=task_id,
            session_id=session_id,
            step_id=step_id,
            call_id=call_id,
        )
        if annotations:
            payload = payload.model_copy(update={"annotations": {**payload.annotations, **annotations}})
        self._record_event(
            task_id or session_id,
            event_type=event_type,
            session_id=session_id,
            phase=phase,
            summary=event_type.replace("_", " "),
            source=source_name,
            payload=payload,
        )

    def on_task_card_ready(self, *, task_id: str, task_card: Any, session_id: str = "") -> None:
        if not self.enabled:
            return
        try:
            payload = task_card.model_dump(mode="python") if hasattr(task_card, "model_dump") else dict(task_card)
            log_event = LogEventMetadata(
                source_type="system",
                source_name="openpilot",
                phase="understand_task",
                event_type="task_card_ready",
                output_summary={
                    "task_id": task_id,
                    "task_card": payload,
                    "session_id": session_id or task_id,
                },
            )
            log_event = self._with_correlation(log_event, task_id=task_id, session_id=session_id or task_id)
            self._record_event(
                session_id or task_id,
                event_type="task_card_ready",
                session_id=session_id or task_id,
                goal=str(payload.get("goal") or ""),
                payload=log_event,
            )
        except Exception:
            return

    def on_route_selected(self, *, task_id: str, route: str, confidence: float, reason: str) -> None:
        if not self.enabled:
            return
        route_metadata = self._with_correlation(
            TaskRouteMetadata(route=route, confidence=confidence, reason=reason),
            task_id=task_id,
        )
        self._record_event(
            task_id,
            event_type="route_selected",
            route=route,
            payload=route_metadata,
        )

    def on_llm_requested(
        self,
        *,
        request_metadata: LLMRequestMetadata,
        task_id: str = "",
        session_id: str = "",
        phase: str = "",
        call_id: str = "",
        request_snapshot: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        request_metadata = self._with_correlation(
            request_metadata,
            task_id=task_id,
            session_id=session_id,
            call_id=call_id,
        )
        event = self._record_event(
            task_id or session_id,
            event_type="llm_requested",
            session_id=session_id,
            phase=phase,
            summary=f"llm request: {request_metadata.purpose or request_metadata.task or 'request'}",
            payload=request_metadata,
        )
        if event is None or request_snapshot is None:
            return
        try:
            self.recorder.record_artifact(
                task_id or session_id,
                kind="llm_request",
                content=self._json_text(request_snapshot),
                filename=f"{call_id or event.event_id}_request.json",
                content_type="application/json",
                source_event_id=event.event_id,
            )
        except Exception:
            return

    def on_llm_responded(
        self,
        *,
        response_metadata: LLMResponseMetadata,
        task_id: str = "",
        session_id: str = "",
        phase: str = "",
        call_id: str = "",
        response_content: str = "",
        parsed_json: Any = None,
    ) -> None:
        if not self.enabled:
            return
        response_metadata = self._with_correlation(
            response_metadata,
            task_id=task_id,
            session_id=session_id,
            call_id=call_id,
        )
        event = self._record_event(
            task_id or session_id,
            event_type="llm_responded",
            session_id=session_id,
            phase=phase,
            summary=f"llm response: {response_metadata.model or response_metadata.provider or 'response'}",
            payload=response_metadata,
        )
        if event is None:
            return
        try:
            if response_content:
                self.recorder.record_artifact(
                    task_id or session_id,
                    kind="llm_response_text",
                    content=response_content,
                    filename=f"{call_id or event.event_id}_response.txt",
                    content_type="text/plain",
                    source_event_id=event.event_id,
                )
            if parsed_json is not None:
                self.recorder.record_artifact(
                    task_id or session_id,
                    kind="llm_response_json",
                    content=self._json_text(parsed_json),
                    filename=f"{call_id or event.event_id}_response.json",
                    content_type="application/json",
                    source_event_id=event.event_id,
                )
        except Exception:
            return

    def on_llm_failed(
        self,
        *,
        failure: FailureMetadata,
        task_id: str = "",
        session_id: str = "",
        phase: str = "",
        call_id: str = "",
        failed_response_text: str = "",
    ) -> None:
        if not self.enabled:
            return
        failure = self._with_correlation(
            failure,
            task_id=task_id,
            session_id=session_id,
            call_id=call_id,
        )
        event = self._record_event(
            task_id or session_id,
            event_type="llm_failed",
            session_id=session_id,
            phase=phase,
            summary=f"llm failed: {failure.error_type}",
            payload=failure,
        )
        if failed_response_text and event is not None:
            try:
                self.recorder.record_artifact(
                    task_id or session_id,
                    kind="llm_failed_response",
                    content=failed_response_text,
                    filename=f"{call_id or event.event_id}_failed_response.txt",
                    content_type="text/plain",
                    source_event_id=event.event_id,
                )
            except Exception:
                return

    def on_runtime_phase_changed(
        self,
        *,
        task_id: str,
        session_id: str = "",
        previous_phase: str = "",
        phase: str = "",
        verification_status: str = "",
        completion_reason: str = "",
        state: RuntimeStateMetadata | None = None,
    ) -> None:
        correlated_payload = (
            self._with_correlation(state, task_id=task_id, session_id=session_id)
            if isinstance(state, MetadataBase)
            else self._with_correlation(
                LogEventMetadata(
                    source_type="system",
                    source_name="openpilot",
                    phase=phase,
                    event_type="runtime_phase_changed",
                    output_summary={
                        "task_id": task_id,
                        "session_id": session_id,
                        "previous_phase": previous_phase,
                        "phase": phase,
                        "verification_status": verification_status,
                        "completion_reason": completion_reason,
                    },
                ),
                task_id=task_id,
                session_id=session_id,
            )
        )
        self._record_event(
            task_id or session_id,
            event_type="runtime_phase_changed",
            session_id=session_id,
            phase=phase,
            summary=f"{previous_phase or '<start>'} -> {phase}",
            payload=correlated_payload,
        )

    def on_verification_state_changed(
        self,
        *,
        task_id: str,
        session_id: str = "",
        previous_status: str = "",
        verification_status: str = "",
        phase: str = "",
        reason: str = "",
        state: RuntimeStateMetadata | None = None,
    ) -> None:
        correlated_payload = (
            self._with_correlation(state, task_id=task_id, session_id=session_id)
            if isinstance(state, MetadataBase)
            else self._with_correlation(
                LogEventMetadata(
                    source_type="system",
                    source_name="openpilot",
                    phase=phase,
                    event_type="verification_state_changed",
                    output_summary={
                        "task_id": task_id,
                        "session_id": session_id,
                        "previous_status": previous_status,
                        "verification_status": verification_status,
                        "phase": phase,
                        "reason": reason,
                    },
                ),
                task_id=task_id,
                session_id=session_id,
            )
        )
        self._record_event(
            task_id or session_id,
            event_type="verification_state_changed",
            session_id=session_id,
            phase=phase,
            summary=f"{previous_status or '<none>'} -> {verification_status}",
            payload=correlated_payload,
        )

    def on_tool_started(
        self,
        *,
        tool_call: ToolCallMetadata,
    ) -> None:
        tool_call = self._with_correlation(
            tool_call,
            task_id=tool_call.task_id,
            session_id=tool_call.session_id,
            step_id=tool_call.step_id,
            call_id=tool_call.call_id,
        )
        self._record_event(
            tool_call.task_id or tool_call.session_id,
            event_type="tool_called",
            session_id=tool_call.session_id,
            summary=f"{tool_call.tool_name} called",
            payload=tool_call,
        )

    def on_tool_completed(
        self,
        *,
        tool_execution: ToolExecutionEnvelopeMetadata,
        task_id: str = "",
        session_id: str = "",
    ) -> None:
        tool_context = tool_execution.tool_context
        derived_task_id = task_id or str(getattr(tool_context, "task_id", "") or tool_execution.correlation.task_id or "")
        derived_session_id = session_id or str(getattr(tool_context, "session_id", "") or tool_execution.correlation.session_id or "")
        derived_step_id = str(getattr(tool_context, "step_id", "") or tool_execution.step_id or "")
        derived_call_id = str(getattr(tool_context, "call_id", "") or tool_execution.call_id or "")
        tool_execution = self._with_correlation(
            tool_execution,
            task_id=derived_task_id,
            session_id=derived_session_id,
            step_id=derived_step_id,
            call_id=derived_call_id,
        )
        self._record_event(
            derived_task_id or derived_session_id,
            event_type="tool_succeeded",
            session_id=derived_session_id,
            summary=f"{tool_execution.tool_name} succeeded",
            payload=tool_execution,
        )

    def on_task_finished(
        self,
        *,
        task_id: str,
        success: bool,
        summary: dict[str, Any] | None = None,
        session_id: str = "",
        finalization_id: str = "",
    ):
        if not self.enabled:
            return
        return self._record_event(
            task_id or session_id,
            event_type="task_finished",
            session_id=session_id,
            phase=str((summary or {}).get("phase") or ""),
            payload=self._with_correlation(
                LogEventMetadata(
                    source_type="system",
                    source_name="openpilot",
                    phase=str((summary or {}).get("phase") or ""),
                    event_type="task_finished",
                    success=success,
                    output_summary={
                        "task_id": task_id,
                        "summary": summary or {},
                        "session_id": session_id,
                        "finalization_id": finalization_id,
                    },
                ),
                task_id=task_id,
                session_id=session_id,
            ),
            idempotency_key=(f"task_finished:{finalization_id}" if finalization_id else ""),
        )

    def on_tool_failed(self, error: Any) -> list[str]:
        try:
            if isinstance(error, ToolErrorMetadata):
                error = self._with_correlation(
                    error,
                    task_id=error.task_id,
                    session_id=error.session_id,
                    step_id=error.step_id,
                    call_id=error.call_id,
                )
            error_payload = error.to_json_dict() if hasattr(error, "to_json_dict") else dict(error)
            self._record_event(
                str(error_payload.get("task_id") or error_payload.get("session_id") or ""),
                event_type="tool_failed",
                session_id=str(error_payload.get("session_id") or ""),
                summary=f"{error_payload.get('tool_name') or 'tool'} failed",
                payload=error,
            )
            return self.record_signal(collect_from_tool_error(error))
        except Exception:
            return []

    def on_failure(self, failure: Any, *, source: str = "runtime_failure", task_id: str = "", tool_name: str = "") -> list[str]:
        try:
            return self.record_signal(collect_from_failure(failure, source=source, task_id=task_id, tool_name=tool_name))
        except Exception:
            return []

    def on_runtime_state_updated(self, state: Any) -> list[str]:
        record_ids: list[str] = []
        try:
            for signal in collect_from_runtime_state(state):
                record_ids.extend(self.record_signal(signal))
        except Exception:
            return record_ids
        return record_ids

    def on_suspicious_success(self, *, task_id: str = "", message: str = "", evidence: list[str] | None = None) -> list[str]:
        try:
            return self.record_signal(
                suspicious_success_signal(
                    task_id=task_id,
                    message=message or "Task reported success without enough verification evidence",
                    evidence=evidence or [],
                )
            )
        except Exception:
            return []


_default_hooks: RuntimeDiagnosticsHooks | None = None


def get_default_hooks() -> RuntimeDiagnosticsHooks:
    global _default_hooks
    if _default_hooks is None:
        _default_hooks = RuntimeDiagnosticsHooks()
    return _default_hooks
