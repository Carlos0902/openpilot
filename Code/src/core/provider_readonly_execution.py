"""Run admitted read-only provider calls through the shared tool lifecycle."""

from __future__ import annotations

from typing import Any

from core.provider_tool_admission import (
    ProviderToolAdmission,
    provider_tool_error,
)
from core.provider_tool_execution_batch import (
    validated_readonly_provider_execution_batch,
)
from metadata import (
    FailureMetadata,
    ResultStatus,
    ToolCallMetadata,
    ToolContextMetadata,
    ToolErrorMetadata,
    ToolExecutionEnvelopeMetadata,
    ToolResultMetadata,
)
from tools.mutation_descriptor import FILE_MUTATION_TOOLS


def execute_readonly_provider_admissions(
    runner: Any,
    task: Any,
    admissions: list[ProviderToolAdmission] | tuple[ProviderToolAdmission, ...],
    *,
    round_index: int = 1,
):
    """Execute one preflighted read-only provider batch in response order."""

    task_id = str(getattr(task, "id", "unknown"))
    session_id = runner.owner._session_id()
    batch = validated_readonly_provider_execution_batch(
        admissions,
        task_id=task_id,
        session_id=session_id,
        round_index=round_index,
    )
    return execute_prepared_provider_admissions(
        runner,
        task,
        batch,
        round_index=round_index,
        mutation_mode=False,
    )


def execute_prepared_provider_admissions(
    runner: Any,
    task: Any,
    admissions: tuple[ProviderToolAdmission, ...],
    *,
    round_index: int,
    mutation_mode: bool,
):
    """Execute one already-preflighted provider batch in response order."""

    task_id = str(getattr(task, "id", "unknown"))
    session_id = runner.owner._session_id()
    batch = tuple(admissions)
    runner._provider_executed = True
    if not batch:
        return runner._finish(
            task_id,
            session_id,
            True,
            round_index,
            None,
            None,
            None,
        )

    last_output: ToolResultMetadata | None = None
    request_projection = [
        {
            "tool_name": (
                item.selection.tool_name
                if item.selection is not None
                else item.tool_call.tool_name
            ),
            "input_metadata": (
                item.selection.input_metadata.to_params()
                if item.selection is not None
                else item.tool_call.input_metadata.to_params()
            ),
        }
        for item in batch
    ]
    for index, admission in enumerate(batch):
        tool_call, tool_context = _prepare_tool_call(runner, admission)
        input_metadata = tool_call.input_metadata
        runner.tool_contexts.append(tool_context)
        runner.tool_invocations.append(tool_call)
        runner._append_event(
            task_id,
            tool_call,
            "pending",
            "pending",
            input_metadata=input_metadata,
            tool_context=tool_context,
            round_index=round_index,
        )

        if admission.status != "admitted" or admission.selection is None:
            tool_error = admission.tool_error or provider_tool_error(
                tool_call,
                error_type="ProviderToolBlocked",
                error_message="Provider tool call was blocked before execution.",
                recoverable=True,
                suggested_recovery=(
                    "Retry with a tool call that satisfies the project contract "
                    "and policy."
                ),
            )
            return _finish_error(
                runner,
                task_id=task_id,
                session_id=session_id,
                round_index=round_index,
                last_output=last_output,
                tool_call=tool_call,
                tool_error=tool_error,
            )

        selection = admission.selection.model_copy(
            update={"input_metadata": input_metadata}
        )
        protocol_error = runner._validate_and_normalize_call(tool_call)
        if protocol_error is not None:
            return _finish_error(
                runner,
                task_id=task_id,
                session_id=session_id,
                round_index=round_index,
                last_output=last_output,
                tool_call=tool_call,
                tool_error=protocol_error,
            )

        runner._append_event(
            task_id,
            tool_call,
            "running",
            "running",
            input_metadata=input_metadata,
            tool_context=tool_context,
            round_index=round_index,
        )
        if mutation_mode and selection.tool_name in FILE_MUTATION_TOOLS:
            guard_error = runner._guard_project_state_change_if_needed(
                task,
                tool_call,
                selection,
            )
            if guard_error is not None:
                return _finish_error(
                    runner,
                    task_id=task_id,
                    session_id=session_id,
                    round_index=round_index,
                    last_output=last_output,
                    tool_call=tool_call,
                    tool_error=guard_error,
                )
        input_payload = input_metadata.to_params()
        _show_tool_running(
            runner,
            task,
            selection.tool_name,
            input_payload,
            index,
            len(batch),
        )
        _owner_hook(
            runner,
            "_log_tool_start",
            task,
            selection.tool_name,
            input_payload,
        )

        diagnostics = getattr(runner.runtime, "runtime_diagnostics_hooks", None)
        if diagnostics:
            diagnostics.on_tool_started(tool_call=tool_call)
        controller = getattr(runner.runtime, "runtime_controller", None)
        set_pending_verification = getattr(
            controller,
            "set_pending_verification",
            None,
        )
        if (
            mutation_mode
            and selection.tool_name in FILE_MUTATION_TOOLS
            and callable(set_pending_verification)
        ):
            set_pending_verification(
                runner._pending_verification_plan(
                    request_projection,
                    index,
                    selection,
                )
            )
        replay = getattr(controller, "replay_tool_result", None)
        exec_result = replay(tool_call, selection) if callable(replay) else None
        prepare = getattr(controller, "prepare_tool_call", None)
        if (
            exec_result is None
            and callable(prepare)
            and not prepare(tool_call, selection)
        ):
            return _finish_error(
                runner,
                task_id=task_id,
                session_id=session_id,
                round_index=round_index,
                last_output=last_output,
                tool_call=tool_call,
                tool_error=provider_tool_error(
                    tool_call,
                    error_type="CheckpointPrepareFailed",
                    error_message=(
                        "Tool was not executed because its prepared checkpoint "
                        "was not durable."
                    ),
                    recoverable=True,
                    suggested_recovery=(
                        "Restore checkpoint storage before retrying the tool."
                    ),
                ),
            )
        if exec_result is None:
            exec_result = runner.runtime.tool_executor.execute_single(
                selection,
                context=None,
            )

        observe = getattr(controller, "observe_tool_result", None)
        if (
            not hasattr(exec_result, "recovery_already_applied")
            and callable(observe)
            and not observe(tool_call, selection, exec_result)
        ):
            return _finish_error(
                runner,
                task_id=task_id,
                session_id=session_id,
                round_index=round_index,
                last_output=last_output,
                tool_call=tool_call,
                tool_error=provider_tool_error(
                    tool_call,
                    error_type="CheckpointObservationFailed",
                    error_message=(
                        "Tool returned, but its result could not be durably "
                        "recorded before state application."
                    ),
                    recoverable=True,
                    suggested_recovery=(
                        "Reconcile the indeterminate result before continuing."
                    ),
                ),
            )
        if not bool(getattr(exec_result, "recovery_already_applied", False)):
            runner._update_runtime_state(selection, exec_result)

        _owner_hook(
            runner,
            "_show_tool_result",
            selection.tool_name,
            exec_result,
        )
        output_metadata = getattr(exec_result, "output_metadata", None)
        _owner_hook(
            runner,
            "_log_tool_complete",
            task,
            selection.tool_name,
            exec_result,
            _summarize_output(runner, output_metadata),
        )
        if bool(getattr(exec_result, "success", False)):
            last_output = output_metadata
            runner._append_event(
                task_id,
                tool_call,
                "completed",
                "completed",
                input_metadata=input_metadata,
                output_metadata=output_metadata,
                tool_context=tool_context,
                round_index=round_index,
            )
            runner._append_tool_result(
                tool_call,
                input_metadata,
                True,
                None,
                output_metadata,
            )
            if diagnostics:
                diagnostics.on_tool_completed(
                    tool_execution=ToolExecutionEnvelopeMetadata(
                        tool_name=selection.tool_name,
                        step_id=tool_call.step_id,
                        status=ResultStatus.SUCCESS,
                        success=True,
                        input_metadata=input_metadata,
                        output_metadata=output_metadata,
                        duration_seconds=0.0,
                        timeout_override=selection.timeout_override,
                        attempts_used=1,
                        call_id=tool_call.call_id,
                        tool_context=tool_context,
                    ),
                    task_id=task_id,
                    session_id=session_id,
                )
            if mutation_mode and selection.tool_name in FILE_MUTATION_TOOLS:
                verification_error = runner._verify_state_change_if_needed(
                    task=task,
                    task_id=task_id,
                    session_id=session_id,
                    source_selection=selection,
                    round_index=round_index,
                    last_output=last_output,
                    defer_provider_validation=True,
                )
                if verification_error is not None:
                    return runner._finish(
                        task_id,
                        session_id,
                        False,
                        round_index,
                        last_output,
                        verification_error,
                        verification_error.error_message,
                    )
            continue

        failure = _execution_failure(runner, exec_result, tool_call)
        return _finish_error(
            runner,
            task_id=task_id,
            session_id=session_id,
            round_index=round_index,
            last_output=last_output,
            tool_call=tool_call,
            tool_error=ToolErrorMetadata(
                session_id=session_id,
                task_id=task_id,
                step_id=tool_call.step_id,
                call_id=tool_call.call_id,
                provider_call_id=tool_call.provider_call_id,
                tool_name=selection.tool_name,
                error_type=failure.error_type,
                error_message=failure.error_message,
                recoverable=bool(failure.recoverable),
                suggested_recovery=runner._suggest_recovery(
                    selection.tool_name,
                    failure.error_message,
                ),
                failure=failure,
                input_metadata=input_metadata,
                tool_context=tool_context,
                provider_executed=True,
                round_index=round_index,
                event_index=runner._next_event_index(),
            ),
        )

    return runner._finish(
        task_id,
        session_id,
        True,
        round_index,
        last_output,
        None,
        None,
    )


def _prepare_tool_call(
    runner: Any,
    admission: ProviderToolAdmission,
) -> tuple[ToolCallMetadata, ToolContextMetadata]:
    tool_call = admission.tool_call
    input_metadata = tool_call.input_metadata
    apply_project_context = getattr(
        runner.runtime,
        "_apply_project_command_context",
        None,
    )
    if callable(apply_project_context):
        input_metadata = apply_project_context(
            tool_call.tool_name,
            input_metadata,
        )
    tool_context = runner.event_emitter.build_context(
        task_id=tool_call.task_id,
        session_id=tool_call.session_id,
        step_id=tool_call.step_id,
        call_id=tool_call.call_id,
        tool_name=tool_call.tool_name,
        input_metadata=input_metadata,
    )
    return (
        tool_call.model_copy(
            update={
                "input_metadata": input_metadata,
                "tool_context": tool_context,
                "provider_executed": True,
                "event_index": runner._next_event_index(),
            }
        ),
        tool_context,
    )


def _providerize_tool_error(
    tool_error: ToolErrorMetadata,
    tool_call: ToolCallMetadata,
) -> ToolErrorMetadata:
    provider_call_id = tool_call.provider_call_id or tool_error.provider_call_id
    if not provider_call_id:
        return tool_error
    failure = tool_error.failure
    if failure is not None:
        failure = failure.model_copy(
            update={
                "details": {
                    **(failure.details or {}),
                    "call_id": tool_call.call_id,
                    "provider_call_id": provider_call_id,
                }
            }
        )
    return tool_error.model_copy(
        update={
            "provider_call_id": provider_call_id,
            "provider_executed": True,
            "tool_context": tool_call.tool_context,
            "input_metadata": tool_call.input_metadata,
            "failure": failure,
        }
    )


def _finish_error(
    runner: Any,
    *,
    task_id: str,
    session_id: str,
    round_index: int,
    last_output: ToolResultMetadata | None,
    tool_call: ToolCallMetadata,
    tool_error: ToolErrorMetadata,
):
    tool_error = _providerize_tool_error(tool_error, tool_call)
    runner._record_tool_error(task_id, tool_call, tool_error, round_index)
    runner._append_tool_result(
        tool_call,
        tool_call.input_metadata,
        False,
        tool_error.error_message,
    )
    final_error = tool_error.failure or FailureMetadata(
        error_type=tool_error.error_type,
        error_message=tool_error.error_message,
        recoverable=tool_error.recoverable,
        retry_recommended=tool_error.recoverable,
    )
    return runner._finish(
        task_id,
        session_id,
        False,
        round_index,
        last_output,
        final_error,
        tool_error.error_message,
    )


def _execution_failure(
    runner: Any,
    exec_result: Any,
    tool_call: ToolCallMetadata,
) -> FailureMetadata:
    failure = getattr(exec_result, "error", None) or FailureMetadata(
        error_type="ToolExecutionFailed",
        error_message=f"{tool_call.tool_name} failed",
    )
    if not isinstance(failure, FailureMetadata):
        failure = FailureMetadata(
            error_type=str(
                getattr(failure, "error_type", "") or type(failure).__name__
            ),
            error_message=str(getattr(failure, "error_message", failure)),
            recoverable=bool(getattr(failure, "recoverable", False)),
            retry_recommended=bool(
                getattr(failure, "retry_recommended", False)
            ),
        )
    recovery = runner._suggest_recovery(
        tool_call.tool_name,
        failure.error_message,
    )
    failure = runner._enrich_execution_failure(
        failure,
        tool_call=tool_call,
        input_metadata=tool_call.input_metadata,
        suggested_recovery=recovery,
    )
    return failure.model_copy(
        update={
            "details": {
                **(failure.details or {}),
                "provider_call_id": tool_call.provider_call_id,
            }
        }
    )


def _show_tool_running(
    runner: Any,
    task: Any,
    tool_name: str,
    input_payload: dict[str, Any],
    index: int,
    total: int,
) -> None:
    hook = getattr(runner.owner, "_show_tool_running", None)
    if callable(hook):
        hook(
            task,
            tool_name,
            input_payload,
            "provider-native tool call",
            index,
            total,
        )


def _summarize_output(runner: Any, output_metadata: Any) -> Any:
    hook = getattr(runner.owner, "_summarize_metadata_output", None)
    return hook(output_metadata) if callable(hook) else output_metadata


def _owner_hook(runner: Any, name: str, *args: Any) -> None:
    hook = getattr(runner.owner, name, None)
    if callable(hook):
        hook(*args)


__all__ = [
    "execute_prepared_provider_admissions",
    "execute_readonly_provider_admissions",
]
