"""Shared typed tool event loop runtime."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.exceptions import ContextAssemblyBudgetError, InvalidLLMResponseError, LLMProviderError, LLMTimeoutError
from core.llm import LLMMessage, LLMRequest
from core.reasoning import reasoning_policy_for_decision
from core.provider_tool_admission import ProviderToolAdmission, provider_tool_error
from core.validation_command import normalize_command_argv
from memory.context_assembly import build_context_llm_request
from memory.session_constraints import session_constraint_prompt_text
from core.tool_event_emitter import ToolEventEmitter
from metadata import (
    AgentPhase,
    ContextRequestPurpose,
    EditPlanMetadata,
    FailureMetadata,
    ResultStatus,
    ReasoningDecisionComplexity,
    ReasoningPolicy,
    RuntimeBudgetMetadata,
    ToolCallMetadata,
    ToolContextMetadata,
    ToolErrorMetadata,
    ToolEventMetadata,
    ToolExecutionEnvelopeMetadata,
    ToolInputMetadata,
    ToolLoopMetadata,
    ToolResultMetadata,
    VerificationPlanMetadata,
    VerificationCommandSpec,
)
from tools.tool_selection import ToolSelection
from tools.mutation_descriptor import FILE_MUTATION_TOOLS, file_mutation_targets


def _phase_value(phase: AgentPhase | str) -> str:
    return phase.value if isinstance(phase, AgentPhase) else str(phase)


@dataclass
class ToolEventLoopRunResult:
    success: bool
    tool_results: list[dict[str, Any]]
    last_output: ToolResultMetadata | None
    loop_metadata: ToolLoopMetadata
    error_message: str | None = None


class ToolEventLoopRunner:
    """Run LLM-planned tool calls as a recoverable typed event loop."""

    _RECOVERY_OBSERVATION_MASK_THRESHOLD = 2_048
    _RECOVERY_SENSITIVE_KEY = re.compile(
        r"(?:api[_-]?key|token|secret|password|authorization|cookie|credential|env)",
        re.IGNORECASE,
    )
    _RECOVERY_CONTROL_FIELDS = frozenset(
        {
            "cleanup_instruction",
            "command",
            "continuation_iterations",
            "create_dirs",
            "cwd",
            "directory_path",
            "effective_interpreter",
            "encoding",
            "entry_files",
            "env_name",
            "environment_id",
            "environment_operation",
            "file_path",
            "file_paths",
            "files",
            "fix_instruction",
            "follow_redirects",
            "freshness",
            "include_environment",
            "insertion_hint",
            "install",
            "instruction",
            "iteration",
            "language",
            "limit",
            "line_end",
            "line_start",
            "llm_cleanup",
            "max_files",
            "max_iterations",
            "max_lines",
            "max_page_chars",
            "max_pages",
            "max_redirect_candidates",
            "max_redirect_depth",
            "max_redirect_pages",
            "max_results",
            "max_search_attempts",
            "max_size_mb",
            "max_tokens",
            "max_total_chars",
            "memory_query",
            "mode",
            "model",
            "offset",
            "operation_kind",
            "overwrite",
            "patch_mode",
            "pattern",
            "project_path",
            "provider",
            "query",
            "read_mode",
            "readme_path",
            "recursive",
            "requested_command",
            "requires_user_input",
            "risk_level",
            "run_command",
            "safe_search",
            "search_budget_seconds",
            "setup_commands",
            "step_id",
            "symbol_name",
            "symbol_type",
            "system_prompt",
            "target_scope",
            "tbs",
            "test_command",
            "time_range",
            "timeout",
            "tool_name",
            "use_cache",
            "validation_context",
            "warning_check_required",
            "written_files",
        }
    )
    _RECOVERY_OBSERVATION_FIELDS = frozenset(
        {
            "code",
            "content",
            "generated_unit",
            "memory_context",
            "patch",
            "project_summary",
            "prompt_context",
            "replacement_text",
            "stack_preset_update",
            "stderr",
            "stdout",
            "text",
            "validation_result",
            "warning_check_result",
            "warnings",
        }
    )

    def __init__(self, owner: Any, *, max_steps: int = 5, doom_loop_threshold: int = 3) -> None:
        self.owner = owner
        self.runtime = owner.runtime
        self.max_steps = max_steps
        self.doom_loop_threshold = doom_loop_threshold
        self.events: list[ToolEventMetadata] = []
        self.tool_invocations: list[ToolCallMetadata] = []
        self.tool_contexts: list[ToolContextMetadata] = []
        self.recoverable_errors: list[ToolErrorMetadata] = []
        self.tool_results: list[dict[str, Any]] = []
        self.event_emitter = ToolEventEmitter(self.runtime, log_hook=self.owner._log)
        self._seen_signatures: dict[str, int] = {}
        self._local_completion_budget = RuntimeBudgetMetadata()
        self._provider_executed = False
        self._retry_count = 0
        self._fallback_count = 0

    def run_provider_tool_calls(
        self,
        task: Any,
        admissions: list[ProviderToolAdmission] | tuple[ProviderToolAdmission, ...],
        *,
        round_index: int = 1,
    ) -> ToolEventLoopRunResult:
        """Execute admitted provider-native calls through the normal lifecycle.

        Admission and execution are deliberately separate. Callers must first
        run ``admit_provider_tool_calls``; this method never trusts provider
        payloads or bypasses the project-owned checkpoint, permission, budget,
        observation, and state-update hooks. The existing JSON planner loop
        does not call this method unless an integration explicitly opts in.
        """
        if round_index < 1:
            raise ValueError("round_index must be positive")
        task_id = str(getattr(task, "id", "unknown"))
        session_id = self.owner._session_id()
        self._provider_executed = True
        admissions = list(admissions)
        if not admissions:
            return self._finish(task_id, session_id, True, round_index, None, None, None)

        last_output: ToolResultMetadata | None = None
        request_projection = [
            {
                "tool_name": admission.selection.tool_name if admission.selection is not None else admission.tool_call.tool_name,
                "input_metadata": (
                    admission.selection.input_metadata.to_params()
                    if admission.selection is not None
                    else admission.tool_call.input_metadata.to_params()
                ),
            }
            for admission in admissions
        ]

        for index, admission in enumerate(admissions):
            tool_call, tool_context = self._prepare_provider_tool_call(admission)
            input_metadata = tool_call.input_metadata
            self.tool_contexts.append(tool_context)
            self.tool_invocations.append(tool_call)
            self._append_event(
                task_id,
                tool_call,
                "pending",
                "pending",
                input_metadata=input_metadata,
                tool_context=tool_context,
                round_index=round_index,
                provider_executed=True,
            )

            if admission.status != "admitted" or admission.selection is None:
                tool_error = admission.tool_error or provider_tool_error(
                    tool_call,
                    error_type="ProviderToolBlocked",
                    error_message="Provider tool call was blocked before execution.",
                    recoverable=True,
                    suggested_recovery="Retry with a tool call that satisfies the project contract and policy.",
                )
                tool_error = self._providerize_tool_error(tool_error, tool_call)
                self._record_tool_error(task_id, tool_call, tool_error, round_index)
                self._append_tool_result(tool_call, input_metadata, False, tool_error.error_message)
                final_error = tool_error.failure or FailureMetadata(
                    error_type=tool_error.error_type,
                    error_message=tool_error.error_message,
                    recoverable=tool_error.recoverable,
                    retry_recommended=tool_error.recoverable,
                )
                return self._finish(
                    task_id,
                    session_id,
                    False,
                    round_index,
                    last_output,
                    final_error,
                    tool_error.error_message,
                )

            selection = admission.selection
            selection = selection.model_copy(update={"input_metadata": input_metadata})

            protocol_error = self._validate_and_normalize_call(tool_call)
            if protocol_error is not None:
                protocol_error = self._providerize_tool_error(protocol_error, tool_call)
                self._record_tool_error(task_id, tool_call, protocol_error, round_index)
                self._append_tool_result(tool_call, input_metadata, False, protocol_error.error_message)
                final_error = protocol_error.failure
                return self._finish(
                    task_id,
                    session_id,
                    False,
                    round_index,
                    last_output,
                    final_error,
                    protocol_error.error_message,
                )

            self._append_event(
                task_id,
                tool_call,
                "running",
                "running",
                input_metadata=input_metadata,
                tool_context=tool_context,
                round_index=round_index,
                provider_executed=True,
            )
            input_payload = input_metadata.to_params()
            self._owner_show_tool_running(task, selection, input_payload, index, len(admissions))
            controller = getattr(self.runtime, "runtime_controller", None)
            guard_error = self._guard_project_state_change_if_needed(task, tool_call, selection)
            if guard_error is not None:
                guard_error = self._providerize_tool_error(guard_error, tool_call)
                self._record_tool_error(task_id, tool_call, guard_error, round_index)
                self._append_tool_result(tool_call, input_metadata, False, guard_error.error_message)
                return self._finish(
                    task_id,
                    session_id,
                    False,
                    round_index,
                    last_output,
                    guard_error.failure,
                    guard_error.error_message,
                )

            self._owner_log_tool_start(task, selection.tool_name, input_payload)
            diagnostics = getattr(self.runtime, "runtime_diagnostics_hooks", None)
            if diagnostics:
                diagnostics.on_tool_started(tool_call=tool_call)
            set_pending_verification = getattr(controller, "set_pending_verification", None)
            if callable(set_pending_verification) and selection.tool_name in FILE_MUTATION_TOOLS:
                set_pending_verification(self._pending_verification_plan(request_projection, index, selection))

            replay_tool_result = getattr(controller, "replay_tool_result", None)
            exec_result = replay_tool_result(tool_call, selection) if callable(replay_tool_result) else None
            prepare_tool_call = getattr(controller, "prepare_tool_call", None)
            if exec_result is None and callable(prepare_tool_call) and not prepare_tool_call(tool_call, selection):
                checkpoint_error = self._protocol_error(
                    session_id,
                    task_id,
                    tool_call.step_id,
                    tool_call.call_id,
                    selection.tool_name,
                    "CheckpointPrepareFailed",
                    "Mutation was not executed because its prepared checkpoint was not durable.",
                    input_metadata,
                    tool_context,
                    suggested_recovery="Restore checkpoint storage before retrying the mutation.",
                    provider_call_id=tool_call.provider_call_id,
                )
                checkpoint_error = self._providerize_tool_error(checkpoint_error, tool_call)
                self._record_tool_error(task_id, tool_call, checkpoint_error, round_index)
                self._append_tool_result(tool_call, input_metadata, False, checkpoint_error.error_message)
                return self._finish(
                    task_id,
                    session_id,
                    False,
                    round_index,
                    last_output,
                    checkpoint_error.failure,
                    checkpoint_error.error_message,
                )
            if exec_result is None:
                exec_result = self.runtime.tool_executor.execute_single(selection, context=None)
            observe_tool_result = getattr(controller, "observe_tool_result", None)
            if (
                not hasattr(exec_result, "recovery_already_applied")
                and callable(observe_tool_result)
                and not observe_tool_result(tool_call, selection, exec_result)
            ):
                checkpoint_error = self._protocol_error(
                    session_id,
                    task_id,
                    tool_call.step_id,
                    tool_call.call_id,
                    selection.tool_name,
                    "CheckpointObservationFailed",
                    "Tool returned, but its result could not be durably recorded before state application.",
                    input_metadata,
                    tool_context,
                    suggested_recovery="Reconcile the indeterminate side effect before continuing.",
                    provider_call_id=tool_call.provider_call_id,
                )
                checkpoint_error = self._providerize_tool_error(checkpoint_error, tool_call)
                self._record_tool_error(task_id, tool_call, checkpoint_error, round_index)
                self._append_tool_result(tool_call, input_metadata, False, checkpoint_error.error_message)
                return self._finish(
                    task_id,
                    session_id,
                    False,
                    round_index,
                    last_output,
                    checkpoint_error.failure,
                    checkpoint_error.error_message,
                )
            if not bool(getattr(exec_result, "recovery_already_applied", False)):
                self._update_runtime_state(selection, exec_result)

            self._owner_show_tool_result(selection.tool_name, exec_result)
            log_output = self._owner_summarize_output(getattr(exec_result, "output_metadata", None))
            self._owner_log_tool_complete(task, selection.tool_name, exec_result, log_output)
            if bool(getattr(exec_result, "success", False)):
                output_metadata = exec_result.output_metadata
                last_output = output_metadata
                self._append_event(
                    task_id,
                    tool_call,
                    "completed",
                    "completed",
                    input_metadata=input_metadata,
                    output_metadata=output_metadata,
                    tool_context=tool_context,
                    round_index=round_index,
                    provider_executed=True,
                )
                self._append_tool_result(tool_call, input_metadata, True, None, output_metadata)
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
                verification_error = self._verify_state_change_if_needed(
                    task=task,
                    task_id=task_id,
                    session_id=session_id,
                    source_selection=selection,
                    round_index=round_index,
                    last_output=last_output,
                    defer_provider_validation=True,
                )
                if verification_error is not None:
                    return self._finish(
                        task_id,
                        session_id,
                        False,
                        round_index,
                        last_output,
                        verification_error,
                        verification_error.error_message,
                    )
                continue

            failure = exec_result.error or FailureMetadata(
                error_type="ToolExecutionFailed",
                error_message=f"{selection.tool_name} failed",
            )
            if not isinstance(failure, FailureMetadata):
                failure = FailureMetadata(
                    error_type=str(getattr(failure, "error_type", "") or type(failure).__name__),
                    error_message=str(getattr(failure, "error_message", failure)),
                    recoverable=bool(getattr(failure, "recoverable", False)),
                    retry_recommended=bool(getattr(failure, "retry_recommended", False)),
                )
            failure = self._enrich_execution_failure(
                failure,
                tool_call=tool_call,
                input_metadata=input_metadata,
                suggested_recovery=self._suggest_recovery(selection.tool_name, failure.error_message),
            )
            tool_error = ToolErrorMetadata(
                session_id=session_id,
                task_id=task_id,
                step_id=tool_call.step_id,
                call_id=tool_call.call_id,
                provider_call_id=tool_call.provider_call_id,
                tool_name=selection.tool_name,
                error_type=failure.error_type,
                error_message=failure.error_message,
                recoverable=bool(failure.recoverable),
                suggested_recovery=self._suggest_recovery(selection.tool_name, failure.error_message),
                failure=failure,
                input_metadata=input_metadata,
                tool_context=tool_context,
                provider_executed=True,
                round_index=round_index,
                event_index=self._next_event_index(),
            )
            self._record_tool_error(task_id, tool_call, tool_error, round_index)
            self._append_tool_result(tool_call, input_metadata, False, failure.error_message)
            return self._finish(
                task_id,
                session_id,
                False,
                round_index,
                last_output,
                failure,
                failure.error_message,
            )

        return self._finish(task_id, session_id, True, round_index, last_output, None, None)

    def _prepare_provider_tool_call(
        self,
        admission: ProviderToolAdmission,
    ) -> tuple[ToolCallMetadata, ToolContextMetadata]:
        """Attach project-derived context without changing provider identity."""
        tool_call = admission.tool_call
        input_metadata = tool_call.input_metadata
        apply_project_context = getattr(self.runtime, "_apply_project_command_context", None)
        if callable(apply_project_context):
            input_metadata = apply_project_context(tool_call.tool_name, input_metadata)
            tool_call = tool_call.model_copy(update={"input_metadata": input_metadata})
        tool_context = self.event_emitter.build_context(
            task_id=tool_call.task_id,
            session_id=tool_call.session_id,
            step_id=tool_call.step_id,
            call_id=tool_call.call_id,
            tool_name=tool_call.tool_name,
            input_metadata=input_metadata,
        )
        tool_call = tool_call.model_copy(
            update={
                "tool_context": tool_context,
                "provider_executed": True,
                "event_index": self._next_event_index(),
            }
        )
        return tool_call, tool_context

    def _providerize_tool_error(
        self,
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

    def _owner_show_tool_running(
        self,
        task: Any,
        selection: ToolSelection,
        input_payload: dict[str, Any],
        index: int,
        total: int,
    ) -> None:
        hook = getattr(self.owner, "_show_tool_running", None)
        if callable(hook):
            hook(task, selection.tool_name, input_payload, "provider-native tool call", index, total)

    def _owner_log_tool_start(self, task: Any, tool_name: str, input_payload: dict[str, Any]) -> None:
        hook = getattr(self.owner, "_log_tool_start", None)
        if callable(hook):
            hook(task, tool_name, input_payload)

    def _owner_show_tool_result(self, tool_name: str, exec_result: Any) -> None:
        hook = getattr(self.owner, "_show_tool_result", None)
        if callable(hook):
            hook(tool_name, exec_result)

    def _owner_summarize_output(self, output_metadata: Any) -> Any:
        hook = getattr(self.owner, "_summarize_metadata_output", None)
        return hook(output_metadata) if callable(hook) else output_metadata

    def _owner_log_tool_complete(self, task: Any, tool_name: str, exec_result: Any, log_output: Any) -> None:
        hook = getattr(self.owner, "_log_tool_complete", None)
        if callable(hook):
            hook(task, tool_name, exec_result, log_output)

    def run(self, task: Any, initial_prompt: str) -> ToolEventLoopRunResult:
        task_id = str(getattr(task, "id", "unknown"))
        session_id = self.owner._session_id()
        self._retry_count = 0
        self._fallback_count = 0
        prompt = initial_prompt
        last_output: ToolResultMetadata | None = None
        last_code_output: ToolResultMetadata | None = None
        final_error: FailureMetadata | None = None
        rounds_used = 0
        pending_retry_requests: list[dict[str, Any]] | None = None
        direct_retry_counts: dict[str, int] = {}

        for round_index in range(1, self.max_steps + 1):
            rounds_used = round_index
            if pending_retry_requests is not None:
                tool_requests = pending_retry_requests
                pending_retry_requests = None
            else:
                try:
                    budget = self._completion_budget()
                    calls_remaining = self.max_steps - round_index + 1
                    completion_limit = budget.tool_event_completion_limit(
                        round_index=round_index,
                        calls_remaining=calls_remaining,
                    )
                    if completion_limit <= 0:
                        final_error = FailureMetadata(
                            error_type="CompletionBudgetExhausted",
                            error_message="Tool-event completion token budget is exhausted.",
                            recoverable=False,
                        )
                        return self._finish(task_id, session_id, False, rounds_used, last_output, final_error, final_error.error_message)
                    reasoning_complexity = self._reasoning_complexity_for_task(task)
                    request = build_context_llm_request(
                        self.runtime.llm_client,
                        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
                        messages=[LLMMessage(role="user", content=prompt)],
                        response_format="json_object",
                        max_tokens=completion_limit,
                        timeout_seconds=45.0,
                        transport_retries=0,
                        trace_info={
                            "completion_budget": {
                                "purpose": ContextRequestPurpose.TOOL_EVENT_DECISION.value,
                                "round_index": round_index,
                                "calls_remaining": calls_remaining,
                                "static_ceiling": budget.tool_event_completion_ceiling,
                                "dynamic_limit": completion_limit,
                                "total_remaining": budget.tool_event_completion_tokens_remaining,
                            },
                            "reasoning_complexity": reasoning_complexity.value,
                        },
                        reasoning_policy=self._reasoning_policy_for_task(task),
                    )
                    constraint_resolver = getattr(self.owner, "_session_constraints_from_context", None)
                    if callable(constraint_resolver):
                        constraint_state = constraint_resolver(getattr(self.owner, "_active_context", None))
                        constraint_prompt = (
                            session_constraint_prompt_text(constraint_state)
                            if constraint_state is not None
                            else ""
                        )
                        rendered_request = "\n\n".join(
                            str(message.content or "") for message in request.messages
                        )
                        if constraint_prompt and constraint_prompt not in rendered_request:
                            raise ContextAssemblyBudgetError(["session_constraints"])
                    budget.consume_tool_event_completion(completion_limit)
                    llm_response = self._complete_tool_event_request(request)
                    self._reconcile_completion_usage(budget, llm_response, reserved=completion_limit)
                    tool_requests = self.owner._parse_decision_needs(llm_response)
                except (InvalidLLMResponseError, LLMProviderError, LLMTimeoutError) as exc:
                    self._reconcile_completion_failure(budget, exc, reserved=completion_limit)
                    fallback = getattr(self.owner, "_fallback_tool_requests", None)
                    tool_requests = fallback(reason=str(exc)) if callable(fallback) else []
                    if tool_requests:
                        self._fallback_count += 1
                    if not tool_requests:
                        raise
            round_had_recoverable_error = False

            for index, tool_call in enumerate(tool_requests):
                tool_name = str(tool_call.get("tool_name") or "").strip()
                reason_text = str(tool_call.get("reason") or "")
                timeout_override = tool_call.get("timeout_override")
                raw_input = tool_call.get("input_metadata")
                if not isinstance(raw_input, dict):
                    raw_input = {}
                call_id = f"{task_id}:r{round_index}:c{index + 1}"
                step_id = f"step_{round_index}_{index + 1}"
                input_metadata = self._input_from_raw(tool_name, dict(raw_input))
                input_metadata = self.runtime._resolve_chained_metadata(
                    tool_name,
                    input_metadata,
                    last_output,
                    last_code_output,
                )
                apply_project_context = getattr(self.runtime, "_apply_project_command_context", None)
                if callable(apply_project_context):
                    input_metadata = apply_project_context(tool_name, input_metadata)

                tool_context = self.event_emitter.build_context(
                    task_id=task_id,
                    session_id=session_id,
                    step_id=step_id,
                    call_id=call_id,
                    tool_name=tool_name,
                    input_metadata=input_metadata,
                )
                self.tool_contexts.append(tool_context)

                tool_call_metadata = self.event_emitter.create_tool_call(
                    session_id=session_id,
                    task_id=task_id,
                    step_id=step_id,
                    call_id=call_id,
                    tool_name=tool_name,
                    input_metadata=input_metadata,
                    tool_context=tool_context,
                    status="pending",
                    reason=reason_text,
                    round_index=round_index,
                )
                self.tool_invocations.append(tool_call_metadata)
                self._append_event(
                    task_id,
                    tool_call_metadata,
                    "pending",
                    "pending",
                    input_metadata=input_metadata,
                    tool_context=tool_context,
                    round_index=round_index,
                )

                protocol_error = self._validate_and_normalize_call(tool_call_metadata)
                if protocol_error:
                    self._record_tool_error(task_id, tool_call_metadata, protocol_error, round_index)
                    self._append_tool_result(tool_call_metadata, input_metadata, False, protocol_error.error_message)
                    final_error = protocol_error.failure
                    if not protocol_error.recoverable:
                        return self._finish(
                            task_id,
                            session_id,
                            False,
                            rounds_used,
                            last_output,
                            final_error,
                            protocol_error.error_message,
                        )
                    round_had_recoverable_error = True
                    break

                signature = self._call_signature(tool_name, input_metadata)
                self._seen_signatures[signature] = self._seen_signatures.get(signature, 0) + 1
                if self._seen_signatures[signature] >= self.doom_loop_threshold:
                    final_error = FailureMetadata(
                        error_type="ToolDoomLoop",
                        error_message=f"Repeated the same tool call too many times: {tool_name}",
                        recoverable=False,
                        details={"call_signature": signature},
                    )
                    tool_error = ToolErrorMetadata(
                        session_id=session_id,
                        task_id=task_id,
                        step_id=step_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        error_type=final_error.error_type,
                        error_message=final_error.error_message,
                        recoverable=False,
                        failure=final_error,
                        input_metadata=input_metadata,
                        tool_context=tool_context,
                        round_index=round_index,
                        event_index=self._next_event_index(),
                    )
                    self._record_tool_error(task_id, tool_call_metadata, tool_error, round_index)
                    self._append_tool_result(tool_call_metadata, input_metadata, False, final_error.error_message)
                    return self._finish(task_id, session_id, False, rounds_used, last_output, final_error, final_error.error_message)

                input_payload = input_metadata.to_params()
                self._append_event(
                    task_id,
                    tool_call_metadata,
                    "running",
                    "running",
                    input_metadata=input_metadata,
                    tool_context=tool_context,
                    round_index=round_index,
                )
                self.owner._show_tool_running(task, tool_name, input_payload, reason_text, index, len(tool_requests))
                selection = ToolSelection(
                    step_id=step_id,
                    tool_name=tool_name,
                    reason=self.runtime._map_reason_to_enum(reason_text),
                    confidence=0.9,
                    input_metadata=input_metadata,
                    requires_confirmation=False,
                    fallback_tools=[],
                    depends_on=[],
                    timeout_override=timeout_override if isinstance(timeout_override, int) else None,
                )
                guard_error = self._guard_project_state_change_if_needed(task, tool_call_metadata, selection)
                if guard_error:
                    self._record_tool_error(task_id, tool_call_metadata, guard_error, round_index)
                    self._append_tool_result(tool_call_metadata, input_metadata, False, guard_error.error_message)
                    final_error = guard_error.failure
                    return self._finish(task_id, session_id, False, rounds_used, last_output, final_error, guard_error.error_message)

                self.owner._log_tool_start(task, tool_name, input_payload)
                diagnostics = getattr(self.runtime, "runtime_diagnostics_hooks", None)
                if diagnostics:
                    diagnostics.on_tool_started(
                        tool_call=tool_call_metadata,
                    )
                controller = getattr(self.runtime, "runtime_controller", None)
                set_pending_verification = getattr(controller, "set_pending_verification", None)
                if callable(set_pending_verification) and selection.tool_name in {
                    "file_writer",
                    "file_patch_writer",
                    "file_delete_tool",
                }:
                    set_pending_verification(
                        self._pending_verification_plan(tool_requests, index, selection)
                    )
                replay_tool_result = getattr(controller, "replay_tool_result", None)
                exec_result = (
                    replay_tool_result(tool_call_metadata, selection)
                    if callable(replay_tool_result)
                    else None
                )
                prepare_tool_call = getattr(controller, "prepare_tool_call", None)
                if exec_result is None and callable(prepare_tool_call) and not prepare_tool_call(tool_call_metadata, selection):
                    checkpoint_error = self._protocol_error(
                        session_id,
                        task_id,
                        step_id,
                        call_id,
                        tool_name,
                        "CheckpointPrepareFailed",
                        "Mutation was not executed because its prepared checkpoint was not durable.",
                        input_metadata,
                        tool_context,
                        suggested_recovery="Restore checkpoint storage before retrying the mutation.",
                    )
                    self._record_tool_error(task_id, tool_call_metadata, checkpoint_error, round_index)
                    self._append_tool_result(tool_call_metadata, input_metadata, False, checkpoint_error.error_message)
                    return self._finish(
                        task_id,
                        session_id,
                        False,
                        rounds_used,
                        last_output,
                        checkpoint_error.failure,
                        checkpoint_error.error_message,
                    )
                if exec_result is None:
                    exec_result = self.runtime.tool_executor.execute_single(selection, context=None)
                observe_tool_result = getattr(controller, "observe_tool_result", None)
                if (
                    not hasattr(exec_result, "recovery_already_applied")
                    and callable(observe_tool_result)
                    and not observe_tool_result(tool_call_metadata, selection, exec_result)
                ):
                    checkpoint_error = self._protocol_error(
                        session_id,
                        task_id,
                        step_id,
                        call_id,
                        tool_name,
                        "CheckpointObservationFailed",
                        "Tool returned, but its result could not be durably recorded before state application.",
                        input_metadata,
                        tool_context,
                        suggested_recovery="Reconcile the indeterminate side effect before continuing.",
                    )
                    self._record_tool_error(task_id, tool_call_metadata, checkpoint_error, round_index)
                    self._append_tool_result(tool_call_metadata, input_metadata, False, checkpoint_error.error_message)
                    return self._finish(
                        task_id,
                        session_id,
                        False,
                        rounds_used,
                        last_output,
                        checkpoint_error.failure,
                        checkpoint_error.error_message,
                    )
                if not bool(getattr(exec_result, "recovery_already_applied", False)):
                    self._update_runtime_state(selection, exec_result)
                self.owner._show_tool_result(tool_name, exec_result)
                log_output = self.owner._summarize_metadata_output(exec_result.output_metadata)
                self.owner._log_tool_complete(task, tool_name, exec_result, log_output)

                if exec_result.success:
                    output_metadata = exec_result.output_metadata
                    last_output = output_metadata
                    if tool_name in {"code_generator", "code_unit_generator", "code_editor"}:
                        last_code_output = output_metadata
                    self._append_event(
                        task_id,
                        tool_call_metadata,
                        "completed",
                        "completed",
                        input_metadata=input_metadata,
                        output_metadata=output_metadata,
                        tool_context=tool_context,
                        round_index=round_index,
                    )
                    self._append_tool_result(tool_call_metadata, input_metadata, True, None, output_metadata)
                    if diagnostics:
                        tool_execution = ToolExecutionEnvelopeMetadata(
                            tool_name=tool_name,
                            step_id=tool_call_metadata.step_id,
                            status=ResultStatus.SUCCESS,
                            success=True,
                            input_metadata=input_metadata,
                            output_metadata=output_metadata,
                            duration_seconds=0.0,
                            timeout_override=timeout_override if isinstance(timeout_override, int) else None,
                            attempts_used=1,
                            call_id=tool_call_metadata.call_id,
                            tool_context=tool_context,
                        )
                        diagnostics.on_tool_completed(
                            tool_execution=tool_execution,
                            task_id=task_id,
                            session_id=session_id,
                        )
                    verification_error = self._verify_state_change_if_needed(
                        task=task,
                        task_id=task_id,
                        session_id=session_id,
                        source_selection=selection,
                        round_index=round_index,
                        last_output=last_output,
                    )
                    if verification_error:
                        final_error = verification_error
                        return self._finish(task_id, session_id, False, rounds_used, last_output, final_error, final_error.error_message)
                    continue

                failure = exec_result.error or FailureMetadata(
                    error_type="ToolExecutionFailed",
                    error_message=f"{tool_name} failed",
                )
                if not isinstance(failure, FailureMetadata):
                    failure = FailureMetadata(
                        error_type=str(getattr(failure, "error_type", "") or type(failure).__name__),
                        error_message=str(getattr(failure, "error_message", failure)),
                        recoverable=bool(getattr(failure, "recoverable", False))
                        or self._is_recoverable_error(tool_name, str(getattr(failure, "error_message", failure))),
                        retry_recommended=bool(getattr(failure, "retry_recommended", False)),
                    )
                failure = self._enrich_execution_failure(
                    failure,
                    tool_call=tool_call_metadata,
                    input_metadata=input_metadata,
                    suggested_recovery=self._suggest_recovery(tool_name, failure.error_message),
                )
                recoverable = bool(failure.recoverable) or self._is_recoverable_error(
                    tool_name,
                    f"{failure.error_type}: {failure.error_message}",
                )
                tool_error = ToolErrorMetadata(
                    session_id=session_id,
                    task_id=task_id,
                    step_id=step_id,
                    call_id=call_id,
                    tool_name=tool_name,
                    error_type=failure.error_type,
                    error_message=failure.error_message,
                    recoverable=recoverable,
                    suggested_recovery=self._suggest_recovery(tool_name, failure.error_message),
                    failure=failure,
                    input_metadata=input_metadata,
                    tool_context=tool_context,
                    round_index=round_index,
                    event_index=self._next_event_index(),
                )
                self._record_tool_error(task_id, tool_call_metadata, tool_error, round_index)
                self._append_tool_result(tool_call_metadata, input_metadata, False, failure.error_message)
                final_error = failure
                if not recoverable:
                    return self._finish(task_id, session_id, False, rounds_used, last_output, final_error, failure.error_message)
                if self._is_transient_error(f"{failure.error_type}: {failure.error_message}"):
                    retry_count = direct_retry_counts.get(signature, 0)
                    if retry_count < 1:
                        direct_retry_counts[signature] = retry_count + 1
                        self._retry_count += 1
                        pending_retry_requests = [
                            dict(request)
                            for request in tool_requests[index:]
                            if isinstance(request, dict)
                        ]
                        self.owner._log(
                            "tool_loop_direct_retry_scheduled",
                            output_summary={
                                "task_id": task_id,
                                "call_id": call_id,
                                "tool": tool_name,
                                "retry_count": retry_count + 1,
                                "remaining_tool_requests": len(pending_retry_requests),
                            },
                            success=None,
                            level="WARNING",
                        )
                    elif tool_name == "code_generator" and not self._uses_local_code_fallback(input_metadata):
                        pending_retry_requests = self._local_code_fallback_requests(tool_requests, index)
                        if pending_retry_requests:
                            self._fallback_count += 1
                        self.owner._log(
                            "tool_loop_local_fallback_scheduled",
                            output_summary={
                                "task_id": task_id,
                                "call_id": call_id,
                                "tool": tool_name,
                                "remaining_tool_requests": len(pending_retry_requests),
                                "fallback_mode": "deterministic_local_scaffold",
                            },
                            success=None,
                            level="WARNING",
                        )
                round_had_recoverable_error = True
                break

            if not round_had_recoverable_error:
                return self._finish(task_id, session_id, True, rounds_used, last_output, None, None)
            if pending_retry_requests is None:
                prompt = self._build_recovery_prompt(initial_prompt, self.recoverable_errors[-3:])

        final_error = FailureMetadata(
            error_type="ToolLoopExceeded",
            error_message=self._tool_loop_exceeded_message(),
            recoverable=False,
            details=self._last_recoverable_error_details(),
        )
        return self._finish(task_id, session_id, False, rounds_used, last_output, final_error, final_error.error_message)

    def _completion_budget(self) -> RuntimeBudgetMetadata:
        controller = getattr(self.runtime, "runtime_controller", None)
        state = getattr(controller, "state", None)
        budget = getattr(state, "budget", None)
        return budget if isinstance(budget, RuntimeBudgetMetadata) else self._local_completion_budget

    def _complete_tool_event_request(self, request: LLMRequest) -> Any:
        complete = self.runtime.llm_client.complete
        parameters = inspect.signature(complete).parameters
        accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        kwargs = {"max_retries": 1} if accepts_kwargs or "max_retries" in parameters else {}
        return complete(request, **kwargs)

    def _reasoning_policy_for_task(self, task: Any) -> ReasoningPolicy:
        policy_for_task = getattr(self.owner, "_reasoning_policy_for_task", None)
        if callable(policy_for_task):
            return policy_for_task(task)
        return reasoning_policy_for_decision(
            getattr(self.runtime.llm_client, "settings", None),
            ReasoningDecisionComplexity.STANDARD,
        )

    def _reasoning_complexity_for_task(self, task: Any) -> ReasoningDecisionComplexity:
        complexity_for_task = getattr(self.owner, "_reasoning_complexity_for_task", None)
        if callable(complexity_for_task):
            complexity = complexity_for_task(task)
            if isinstance(complexity, ReasoningDecisionComplexity):
                return complexity
        return ReasoningDecisionComplexity.STANDARD

    def _reconcile_completion_usage(self, budget: RuntimeBudgetMetadata, response: Any, *, reserved: int) -> None:
        provider_details = getattr(response, "provider_details", None)
        if isinstance(provider_details, dict) and provider_details.get("recovery_replay"):
            budget.reconcile_tool_event_completion(reserved=reserved, actual=0)
            return
        usage = getattr(response, "usage", None)
        if not isinstance(usage, dict):
            return
        tokens = usage.get("completion_tokens")
        if tokens is None:
            tokens = usage.get("output_tokens")
        if tokens is not None:
            budget.reconcile_tool_event_completion(reserved=reserved, actual=int(tokens))

    def _reconcile_completion_failure(
        self,
        budget: RuntimeBudgetMetadata,
        error: Exception,
        *,
        reserved: int,
    ) -> None:
        usage = getattr(error, "usage", None)
        tokens = None
        if isinstance(usage, dict):
            tokens = usage.get("completion_tokens")
            if tokens is None:
                tokens = usage.get("output_tokens")
        if tokens is not None:
            budget.reconcile_tool_event_completion(reserved=reserved, actual=int(tokens))
        elif isinstance(error, InvalidLLMResponseError) and not str(getattr(error, "response_text", "") or ""):
            budget.reconcile_tool_event_completion(reserved=reserved, actual=0)
        if str(getattr(error, "finish_reason", "") or "").lower() in {"length", "max_tokens"}:
            budget.grant_tool_event_completion_recovery(budget.tool_event_completion_recovery_step)

    def _build_tool_context(
        self,
        *,
        task_id: str,
        session_id: str,
        step_id: str,
        call_id: str,
        tool_name: str,
        input_metadata: ToolInputMetadata,
    ) -> ToolContextMetadata:
        return self.event_emitter.build_context(
            task_id=task_id,
            session_id=session_id,
            step_id=step_id,
            call_id=call_id,
            tool_name=tool_name,
            input_metadata=input_metadata,
        )

    def _environment_for_input(self, input_metadata: ToolInputMetadata) -> dict[str, Any]:
        environment_for_tool_input = getattr(self.runtime, "_environment_for_tool_input", None)
        if callable(environment_for_tool_input):
            environment = environment_for_tool_input(input_metadata)
            if isinstance(environment, dict):
                return environment
        environments = getattr(self.runtime, "_project_environments", {}) or {}
        if len(environments) == 1:
            return next(iter(environments.values()))
        return {}

    def _project_from_path(self, raw: str) -> str:
        if not raw:
            return ""
        environments = getattr(self.runtime, "_project_environments", {}) or {}
        try:
            path = Path(str(raw)).expanduser().resolve()
        except OSError:
            path = Path(str(raw)).expanduser()
        for project_path in environments:
            try:
                project = Path(str(project_path)).expanduser().resolve()
            except OSError:
                project = Path(str(project_path)).expanduser()
            if path == project or project in path.parents:
                return str(project_path)
        return ""

    def _permission_required(self, tool_name: str, input_metadata: ToolInputMetadata) -> bool:
        if input_metadata.requires_user_input:
            return True
        registry = getattr(self.runtime, "tool_registry", None)
        definition = registry.get(tool_name) if registry and hasattr(registry, "get") else None
        permission_level = str(getattr(definition, "permission_level", "") or "").lower()
        return permission_level in {"medium", "high", "forbidden"}

    def _validate_and_normalize_call(self, tool_call: ToolCallMetadata) -> ToolErrorMetadata | None:
        tool_name = tool_call.tool_name
        input_metadata = tool_call.input_metadata
        session_id = tool_call.session_id
        task_id = tool_call.task_id
        step_id = tool_call.step_id
        call_id = tool_call.call_id

        if not tool_name:
            return self._protocol_error(session_id, task_id, step_id, call_id, tool_name, "UnknownTool", "Tool name is required", input_metadata, tool_call.tool_context)

        registry = getattr(self.runtime, "tool_registry", None)
        get_executor = getattr(registry, "get_executor", None)
        if callable(get_executor) and get_executor(tool_name) is None:
            return self._protocol_error(
                session_id,
                task_id,
                step_id,
                call_id,
                tool_name,
                "UnknownTool",
                f"Unknown tool: {tool_name}",
                input_metadata,
                tool_call.tool_context,
                suggested_recovery="Choose one of the registered tools from the available tool list.",
            )

        missing_fields = [field for field in self._required_fields(tool_name) if not getattr(input_metadata, field, None)]
        if missing_fields:
            return self._protocol_error(
                session_id,
                task_id,
                step_id,
                call_id,
                tool_name,
                "MissingRequiredInput",
                f"Missing required input field(s) for {tool_name}: {', '.join(missing_fields)}",
                input_metadata,
                tool_call.tool_context,
                suggested_recovery=f"Retry with input_metadata containing: {', '.join(missing_fields)}.",
            )

        missing_any_of = self._missing_required_any_of(tool_name, input_metadata)
        if missing_any_of:
            readable_group = " or ".join(missing_any_of)
            return self._protocol_error(
                session_id,
                task_id,
                step_id,
                call_id,
                tool_name,
                "MissingRequiredInputGroup",
                f"Missing required input for {tool_name}: provide {readable_group}",
                input_metadata,
                tool_call.tool_context,
                suggested_recovery=f"Retry with input_metadata containing one of: {readable_group}.",
                details={"required_any_of": [missing_any_of]},
            )

        if tool_name == "code_generator":
            language = (input_metadata.language or "python").lower()
            if language not in {"python", "shell", "bash"}:
                return self._protocol_error(
                    session_id,
                    task_id,
                    step_id,
                    call_id,
                    tool_name,
                    "UnsupportedLanguage",
                    f"Unsupported language: {input_metadata.language}. Use python, shell, or bash.",
                    input_metadata,
                    tool_call.tool_context,
                    suggested_recovery=(
                        "If the task asks for design, outline, or documentation, use README/document writing "
                        "or return planning metadata instead of code_generator(language=text)."
                    ),
                )

        if tool_name == "command_executor":
            mode = (input_metadata.mode or "automatic").lower()
            aliases = {"execute": "automatic", "run": "automatic", "exec": "automatic"}
            if mode in aliases:
                input_metadata.mode = aliases[mode]
            elif mode not in {"dry_run", "interactive", "automatic"}:
                return self._protocol_error(
                    session_id,
                    task_id,
                    step_id,
                    call_id,
                    tool_name,
                    "UnsupportedCommandMode",
                    f"unsupported command execution mode: {input_metadata.mode}",
                    input_metadata,
                    tool_call.tool_context,
                    suggested_recovery="Use command_executor mode dry_run, interactive, or automatic.",
                )
        if tool_name == "file_reader" and input_metadata.file_path:
            file_path = Path(str(input_metadata.file_path)).expanduser()
            if not file_path.exists() and self._looks_like_invented_intermediate_file(file_path):
                recovery = "Use the original goal or shared execution history instead of reading invented intermediate files."
                return self._protocol_error(
                    session_id,
                    task_id,
                    step_id,
                    call_id,
                    tool_name,
                    "InventedIntermediateFile",
                    f"File not found: {input_metadata.file_path}",
                    input_metadata,
                    tool_call.tool_context,
                    suggested_recovery=recovery,
                    details={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "step_id": step_id,
                        "file_path": str(input_metadata.file_path),
                        "suggested_recovery": recovery,
                    },
                )
            try:
                is_directory = file_path.is_dir()
            except OSError:
                is_directory = False
            if is_directory:
                return self._protocol_error(
                    session_id,
                    task_id,
                    step_id,
                    call_id,
                    tool_name,
                    "FileReaderDirectoryPath",
                    f"file_reader expected a file path but received a directory: {input_metadata.file_path}",
                    input_metadata,
                    tool_call.tool_context,
                    suggested_recovery="Use multi_file_reader with directory_path to inspect directories.",
                    details={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "step_id": step_id,
                        "received_path": str(input_metadata.file_path),
                        "suggested_tool": "multi_file_reader",
                        "suggested_input": {"directory_path": str(input_metadata.file_path), "pattern": "*"},
                    },
                )
        if tool_name == "file_writer" and input_metadata.content:
            placeholder_reason = self._generated_placeholder_reason(str(input_metadata.content))
            if placeholder_reason:
                return self._protocol_error(
                    session_id,
                    task_id,
                    step_id,
                    call_id,
                    tool_name,
                    "GeneratedPlaceholderContent",
                    f"file_writer content still contains generated placeholder text: {placeholder_reason}",
                    input_metadata,
                    tool_call.tool_context,
                    suggested_recovery=(
                        "Regenerate real implementation content, or let chained metadata fill file_writer.content "
                        "from code_generator output before writing."
                    ),
                    details={
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "step_id": step_id,
                        "file_path": str(input_metadata.file_path or ""),
                        "placeholder_reason": placeholder_reason,
                    },
                )
        return None

    def _guard_project_state_change_if_needed(self, task: Any, tool_call: ToolCallMetadata, selection: ToolSelection) -> ToolErrorMetadata | None:
        controller = getattr(self.runtime, "runtime_controller", None)
        state = getattr(controller, "state", None)
        contract_error = self._provider_task_contract_error(task, tool_call, selection)
        if contract_error is not None:
            return contract_error
        session_constraint_check = getattr(controller, "session_constraint_violation", None)
        if state is not None and callable(session_constraint_check):
            command_is_mutation = bool(file_mutation_targets(selection)) or (
                selection.tool_name == "command_executor"
                and bool(
                    getattr(controller, "_command_may_modify_project", lambda _command: False)(
                        str(selection.input_metadata.command or "")
                    )
                )
            )
            violation = session_constraint_check(
                selection,
                task_validation_command=str(getattr(task, "validation_command", "") or ""),
            )
            if violation is not None:
                constraint_state = getattr(state, "session_constraints", None)
                state_hash = (
                    str(getattr(constraint_state, "canonical_hash", ""))
                    if constraint_state is not None
                    else ""
                )
                return self._protocol_error(
                    tool_call.session_id,
                    tool_call.task_id,
                    tool_call.step_id,
                    tool_call.call_id,
                    selection.tool_name,
                    "SessionConstraintViolation",
                    f"Active session constraint denied this tool call: {violation.value}",
                    selection.input_metadata,
                    tool_call.tool_context,
                    details={
                        "violation_code": violation.value,
                        "constraint_state_hash": state_hash,
                        "command_is_mutation": command_is_mutation,
                    },
                )
        if not self._requires_edit_guard(selection):
            return None
        guard = getattr(controller, "edit_guard", None)
        file_selector = getattr(controller, "file_selector", None)
        if state is None or guard is None:
            return None

        params = selection.input_metadata.to_params()
        target_files = self._edit_target_files(selection)
        if not target_files:
            return self._protocol_error(
                tool_call.session_id,
                tool_call.task_id,
                tool_call.step_id,
                tool_call.call_id,
                selection.tool_name,
                "MissingEditTarget",
                f"{selection.tool_name} requires an affected path before the state change can be approved",
                selection.input_metadata,
                tool_call.tool_context,
            )

        edit_plan = self._matching_edit_plan(state, target_files)
        if edit_plan is None:
            evidence = tool_call.reason or f"Tool plan for task: {getattr(task, 'description', '')}"
            for file_path in target_files:
                if file_path not in getattr(state, "candidate_files", {}):
                    state.add_candidate_file(file_path, evidence)
            if file_selector is not None:
                selected = file_selector.select(
                    state,
                    target_files,
                    {file_path: list(state.candidate_files.get(file_path) or [evidence]) for file_path in target_files},
                )
            else:
                selected = []
                for file_path in target_files:
                    state.select_file(file_path, evidence)
                    selected.append(file_path)
            if set(selected) != set(target_files):
                return self._protocol_error(
                    tool_call.session_id,
                    tool_call.task_id,
                    tool_call.step_id,
                    tool_call.call_id,
                    selection.tool_name,
                    "FileSelectionMissingEvidence",
                    "Edit targets must be selected by FileSelector with evidence before approval",
                    selection.input_metadata,
                    tool_call.tool_context,
                    suggested_recovery="Provide evidence-backed candidate files before requesting the edit.",
                    details={"target_files": target_files, "selected_files": selected},
                )
            verification = self._verification_steps_for_state_change(selection, target_files)
            edit_plan = EditPlanMetadata(
                subgoal=str(getattr(task, "description", "") or f"Run {selection.tool_name}"),
                target_files=target_files,
                evidence=[evidence],
                allowed_changes=[self._allowed_change_description(selection, target_files)],
                forbidden_changes=["Do not modify files outside the approved edit plan."],
                risk_level=str(selection.input_metadata.risk_level or "medium"),
                verification=verification,
                attributes={"budget_kind": self._write_budget_kind(selection)},
            )
            state.planned_edits.append(edit_plan)

        decision = guard.approve(state, edit_plan)
        state.record_tool_event(
            {
                "tool_name": selection.tool_name,
                "step_id": selection.step_id,
                "event_type": "edit_guard",
                "approved": decision.approved,
                "reason": decision.reason,
                "target_files": list(edit_plan.target_files),
            }
        )
        if decision.approved:
            return None
        return self._protocol_error(
            tool_call.session_id,
            tool_call.task_id,
            tool_call.step_id,
            tool_call.call_id,
            selection.tool_name,
            "EditGuardRejected",
            decision.reason,
            selection.input_metadata,
            tool_call.tool_context,
            suggested_recovery="Create a scoped EditPlanMetadata with selected target files, evidence, allowed changes, and verification.",
            details=decision.to_json_dict(),
        )

    def _provider_task_contract_error(
        self,
        task: Any,
        tool_call: ToolCallMetadata,
        selection: ToolSelection,
    ) -> ToolErrorMetadata | None:
        """Recheck provider task scope after admission and context binding.

        Admission is the first boundary; this second check protects against a
        transformed input (environment binding, relative-path resolution, or a
        synthesized selection) crossing the task contract before execution.
        Tasks without explicit scopes retain the legacy planner behavior.
        """

        task_reads = [str(path) for path in getattr(task, "read_files", []) or [] if str(path).strip()]
        task_writes = [str(path) for path in getattr(task, "write_files", []) or [] if str(path).strip()]
        project_path = str(
            getattr(selection.input_metadata, "project_path", None)
            or getattr(tool_call.tool_context, "project_path", None)
            or ""
        ).strip()
        if not project_path and (task_writes or task_reads):
            first_write = Path((task_writes or task_reads)[0]).expanduser()
            if first_write.is_absolute():
                project_path = str(first_write.resolve(strict=False).parent)

        def canonical(raw: Any) -> str:
            path = Path(str(raw or "")).expanduser()
            if not path.is_absolute() and project_path:
                path = Path(project_path) / path
            return str(path.resolve(strict=False))

        def scoped_paths(fields: tuple[str, ...]) -> list[str]:
            params = selection.input_metadata.to_params()
            values: list[str] = []
            for field in fields:
                value = params.get(field)
                if value:
                    values.append(canonical(value))
            values.extend(canonical(value) for value in params.get("file_paths") or [] if value)
            return values

        if task_reads and selection.tool_name == "file_reader":
            allowed = {canonical(path) for path in task_reads}
            requested = scoped_paths(("file_path", "directory_path"))
            if requested and not set(requested).issubset(allowed):
                return self._protocol_error(
                    tool_call.session_id,
                    tool_call.task_id,
                    tool_call.step_id,
                    tool_call.call_id,
                    selection.tool_name,
                    "ProviderTaskReadScopeViolation",
                    "Provider file read crossed the task read_files contract.",
                    selection.input_metadata,
                    tool_call.tool_context,
                    suggested_recovery="Use only a path from Task.read_files.",
                    details={"requested": requested, "allowed": sorted(allowed)},
                )

        if task_writes and selection.tool_name in FILE_MUTATION_TOOLS:
            allowed = {canonical(path) for path in task_writes}
            requested = [canonical(path) for path in file_mutation_targets(selection)]
            if requested and not set(requested).issubset(allowed):
                return self._protocol_error(
                    tool_call.session_id,
                    tool_call.task_id,
                    tool_call.step_id,
                    tool_call.call_id,
                    selection.tool_name,
                    "ProviderTaskWriteScopeViolation",
                    "Provider file mutation crossed the task write_files contract.",
                    selection.input_metadata,
                    tool_call.tool_context,
                    suggested_recovery="Use only a path from Task.write_files.",
                    details={"requested": requested, "allowed": sorted(allowed)},
                )

        expected_command = str(getattr(task, "validation_command", "") or "").strip()
        if expected_command and selection.tool_name == "command_executor":
            controller = getattr(self.runtime, "runtime_controller", None)
            state = getattr(controller, "state", None)
            task_requires_write_order = bool(getattr(task, "write_files", []) or [])
            if task_requires_write_order and state is not None:
                phase = _phase_value(getattr(state, "phase", ""))
                verification_status = str(getattr(state, "verification_status", "") or "")
                if phase != AgentPhase.VERIFY.value and verification_status != "required":
                    return self._protocol_error(
                        tool_call.session_id,
                        tool_call.task_id,
                        tool_call.step_id,
                        tool_call.call_id,
                        selection.tool_name,
                        "ProviderValidationOrderViolation",
                        "Validation is admitted only after the requested mutation enters verification.",
                        selection.input_metadata,
                        tool_call.tool_context,
                        suggested_recovery="Complete the scoped mutation first, then run validation once.",
                        details={"phase": phase, "verification_status": verification_status},
                    )
            requested_command = str(
                selection.input_metadata.requested_command
                or selection.input_metadata.command
                or ""
            ).strip()
            actual_argv = normalize_command_argv(requested_command)
            expected_argv = normalize_command_argv(expected_command)
            if actual_argv != expected_argv:
                return self._protocol_error(
                    tool_call.session_id,
                    tool_call.task_id,
                    tool_call.step_id,
                    tool_call.call_id,
                    selection.tool_name,
                    "ProviderValidationCommandViolation",
                    "Provider command crossed the task validation_command contract.",
                    selection.input_metadata,
                    tool_call.tool_context,
                    suggested_recovery="Run the exact Task.validation_command once.",
                    details={"requested_command": requested_command, "expected_command": expected_command},
                )
            mode = str(selection.input_metadata.mode or "").strip().lower()
            if mode and mode not in {"automatic", "execute", "run", "exec"}:
                return self._protocol_error(
                    tool_call.session_id,
                    tool_call.task_id,
                    tool_call.step_id,
                    tool_call.call_id,
                    selection.tool_name,
                    "ProviderValidationModeViolation",
                    "Provider validation command must use automatic execution mode.",
                    selection.input_metadata,
                    tool_call.tool_context,
                    suggested_recovery="Use automatic mode for the exact validation command.",
                )
            requested_cwd = str(selection.input_metadata.cwd or "").strip()
            if project_path and requested_cwd and canonical(requested_cwd) != canonical(project_path):
                return self._protocol_error(
                    tool_call.session_id,
                    tool_call.task_id,
                    tool_call.step_id,
                    tool_call.call_id,
                    selection.tool_name,
                    "ProviderValidationCwdViolation",
                    "Provider validation command cwd crossed the project boundary.",
                    selection.input_metadata,
                    tool_call.tool_context,
                    suggested_recovery="Run validation from the bound project root.",
                    details={"requested_cwd": requested_cwd, "project_path": project_path},
                )
        return None

    def _requires_edit_guard(self, selection: ToolSelection) -> bool:
        if selection.tool_name in FILE_MUTATION_TOOLS:
            return True
        if selection.tool_name != "command_executor":
            return False
        input_metadata = selection.input_metadata
        mode = str(input_metadata.mode or "").lower()
        if mode == "dry_run":
            return False
        controller = getattr(self.runtime, "runtime_controller", None)
        state = getattr(controller, "state", None)
        if state is not None and (
            _phase_value(getattr(state, "phase", "")) == AgentPhase.VERIFY.value
            or getattr(state, "verification_status", "") == "required"
        ):
            return False
        return self._command_may_modify_project(str(input_metadata.command or ""))

    def _command_may_modify_project(self, command: str) -> bool:
        lowered = command.lower()
        mutation_markers = (
            " rm ",
            " rm -",
            " mv ",
            " cp ",
            " mkdir ",
            " touch ",
            " chmod ",
            " chown ",
            " sed -i",
            " tee ",
            " >",
            ">>",
            " pip install",
            " uv add",
            " poetry add",
            " npm install",
            " pnpm add",
            " yarn add",
            " cargo add",
            " go get",
        )
        padded = f" {lowered} "
        return any(marker in padded for marker in mutation_markers)

    def _edit_target_files(self, selection: ToolSelection) -> list[str]:
        params = selection.input_metadata.to_params()
        if selection.tool_name in FILE_MUTATION_TOOLS:
            return file_mutation_targets(selection)
        if selection.tool_name == "command_executor":
            explicit = params.get("file_paths") or params.get("files") or []
            if explicit:
                return [str(path) for path in explicit]
            cwd = params.get("cwd") or self._project_from_path(str(params.get("command") or "")) or "."
            return [str(cwd)]
        return []

    def _verification_steps_for_state_change(self, selection: ToolSelection, target_files: list[str]) -> list[str]:
        controller = getattr(self.runtime, "runtime_controller", None)
        state = getattr(controller, "state", None)
        verifier = getattr(controller, "verifier", None)
        if state is not None and verifier is not None:
            plan = verifier.plan(state, self._runtime_context())
            if plan.commands:
                return list(plan.commands)
            if plan.fallback_checks:
                return list(plan.fallback_checks)
        if selection.tool_name in FILE_MUTATION_TOOLS:
            return ["Run the runtime-selected verification after the file change."]
        return [f"Verify command side effects for: {', '.join(target_files)}"]

    def _pending_verification_plan(
        self,
        tool_requests: list[dict[str, Any]],
        current_index: int,
        selection: ToolSelection,
    ) -> VerificationPlanMetadata | None:
        if selection.tool_name not in {"file_writer", "file_patch_writer", "file_delete_tool"}:
            return None
        commands: list[str] = []
        specs: list[VerificationCommandSpec] = []
        for request in tool_requests[current_index + 1 :]:
            if not isinstance(request, dict) or request.get("tool_name") != "command_executor":
                continue
            raw_input = request.get("input_metadata") or {}
            if isinstance(raw_input, ToolInputMetadata):
                params = raw_input.to_params()
            elif isinstance(raw_input, dict):
                params = dict(raw_input)
            else:
                continue
            command = str(params.get("command") or "").strip()
            if not command:
                continue
            commands.append(command)
            specs.append(
                VerificationCommandSpec(
                    command=command,
                    cwd=str(params.get("cwd")) if params.get("cwd") else None,
                    mode=str(params.get("mode") or "automatic"),
                    timeout=int(params["timeout"]) if params.get("timeout") else None,
                )
            )
        if not commands:
            return None
        return VerificationPlanMetadata(
            reason="Persisted required validation following a file mutation.",
            commands=commands,
            command_specs=specs,
            target_files=self._edit_target_files(selection),
        )

    def _allowed_change_description(self, selection: ToolSelection, target_files: list[str]) -> str:
        if selection.tool_name == "file_writer":
            return f"Write requested content to {', '.join(target_files)}"
        if selection.tool_name == "file_patch_writer":
            operation = selection.input_metadata.operation_kind or selection.input_metadata.patch_mode or "local patch"
            return f"Apply {operation} local edit to {', '.join(target_files)}"
        if selection.tool_name == "file_delete_tool":
            return f"Delete requested file {', '.join(target_files)}"
        command = str(selection.input_metadata.command or "")
        return f"Run approved command with bounded side effects: {command[:160]}"

    def _write_budget_kind(self, selection: ToolSelection) -> str:
        operation_kind = str(selection.input_metadata.operation_kind or "").lower()
        if selection.tool_name == "file_writer" and operation_kind in {
            "create_file",
            "file_create",
            "directory_generate",
        }:
            return "file_create"
        return "file_edit"

    def _matching_edit_plan(self, state: Any, target_files: list[str]) -> EditPlanMetadata | None:
        targets = set(target_files)
        for edit_plan in getattr(state, "planned_edits", []) or []:
            if targets.issubset(set(getattr(edit_plan, "target_files", []))):
                return edit_plan
        return None

    def _verify_state_change_if_needed(
        self,
        *,
        task: Any,
        task_id: str,
        session_id: str,
        source_selection: ToolSelection,
        round_index: int,
        last_output: ToolResultMetadata | None,
        defer_provider_validation: bool = False,
    ) -> FailureMetadata | None:
        if source_selection.tool_name not in {"file_writer", "file_patch_writer", "file_delete_tool", "command_executor"}:
            return None
        controller = getattr(self.runtime, "runtime_controller", None)
        state = getattr(controller, "state", None)
        verifier = getattr(controller, "verifier", None)
        if state is None or verifier is None:
            return None
        # Provider-native mutation tasks carry their exact validation command
        # as the next provider tool call.  Do not run RuntimeVerifier's
        # generic fallback (for example ``<entrypoint> --help``) between the
        # write and that provider call; doing so both substitutes the command
        # and moves the runtime past the VERIFY boundary.  The provider's
        # typed command is admitted and executed in the next round instead.
        if defer_provider_validation and str(getattr(task, "validation_command", "") or "").strip():
            return None
        if getattr(state, "verification_status", "") != "required":
            return None
        if state.budget.verification_attempts_used >= state.budget.max_verification_attempts:
            state.block("verification budget exhausted")
            return FailureMetadata(
                error_type="VerificationBudgetExceeded",
                error_message="Project state change requires verification, but verification budget is exhausted.",
                recoverable=False,
            )

        plan = verifier.plan(state, self._runtime_context())
        if not plan.commands:
            state.block("verification plan did not provide a command")
            return FailureMetadata(
                error_type="MissingVerificationCommand",
                error_message="Project state change requires verification, but no verification command was available.",
                recoverable=False,
            )

        command = plan.commands[0]
        step_id = f"{source_selection.step_id}_verify"
        call_id = f"{task_id}:r{round_index}:verify"
        input_metadata = ToolInputMetadata.from_mapping(
            "command_executor",
            {
                "command": command,
                "mode": "automatic",
                "cwd": self._runtime_context().get("cwd"),
                "test_command": command,
                "timeout": plan.attributes.get("timeout"),
            },
        )
        apply_project_context = getattr(self.runtime, "_apply_project_command_context", None)
        if callable(apply_project_context):
            input_metadata = apply_project_context("command_executor", input_metadata)

        tool_context = self._build_tool_context(
            task_id=task_id,
            session_id=session_id,
            step_id=step_id,
            call_id=call_id,
            tool_name="command_executor",
            input_metadata=input_metadata,
        )
        tool_call = self.event_emitter.create_tool_call(
            session_id=session_id,
            task_id=task_id,
            step_id=step_id,
            call_id=call_id,
            tool_name="command_executor",
            input_metadata=input_metadata,
            tool_context=tool_context,
            status="pending",
            reason="verify write operation",
            round_index=round_index,
        )
        self.tool_contexts.append(tool_context)
        self.tool_invocations.append(tool_call)
        self._append_event(task_id, tool_call, "pending", "pending", input_metadata=input_metadata, tool_context=tool_context, round_index=round_index)
        self._append_event(task_id, tool_call, "running", "running", input_metadata=input_metadata, tool_context=tool_context, round_index=round_index)

        selection = ToolSelection(
            step_id=step_id,
            tool_name="command_executor",
            reason=self.runtime._map_reason_to_enum("verify write operation"),
            confidence=1.0,
            input_metadata=input_metadata,
            requires_confirmation=False,
            fallback_tools=[],
            depends_on=[source_selection.step_id],
            timeout_override=None,
        )
        self.owner._show_tool_running(task, "command_executor", input_metadata.to_params(), "verify write operation", 0, 1)
        self.owner._log_tool_start(task, "command_executor", input_metadata.to_params())
        exec_result = self.runtime.tool_executor.execute_single(selection, context=None)
        self._update_runtime_state(selection, exec_result)
        self.owner._show_tool_result("command_executor", exec_result)
        log_output = self.owner._summarize_metadata_output(exec_result.output_metadata)
        self.owner._log_tool_complete(task, "command_executor", exec_result, log_output)

        if exec_result.success:
            self._append_event(
                task_id,
                tool_call,
                "completed",
                "completed",
                input_metadata=input_metadata,
                output_metadata=exec_result.output_metadata,
                tool_context=tool_context,
                round_index=round_index,
            )
            self._append_tool_result(tool_call, input_metadata, True, None, exec_result.output_metadata)
            return None

        failure = exec_result.error or FailureMetadata(
            error_type="VerificationFailed",
            error_message="Write verification failed.",
            recoverable=False,
        )
        if not isinstance(failure, FailureMetadata):
            failure = FailureMetadata(
                error_type=str(getattr(failure, "error_type", "") or type(failure).__name__),
                error_message=str(getattr(failure, "error_message", failure)),
                recoverable=False,
            )
        failure = failure.model_copy(
            update={
                "details": {
                    **(failure.details or {}),
                    "tool_name": "command_executor",
                    "call_id": call_id,
                    "step_id": step_id,
                    "command": command,
                    "input_summary": self._input_summary(input_metadata),
                }
            }
        )
        self._append_event(
            task_id,
            tool_call,
            "error",
            "error",
            input_metadata=input_metadata,
            tool_context=tool_context,
            failure=failure,
            round_index=round_index,
        )
        self._append_tool_result(tool_call, input_metadata, False, failure.error_message)
        return failure

    def _runtime_context(self) -> dict[str, Any]:
        environments = getattr(self.runtime, "_project_environments", {}) or {}
        if len(environments) == 1:
            environment = next(iter(environments.values()))
            return {
                "project_path": environment.get("project_path"),
                "cwd": environment.get("command_cwd"),
                "python_command": environment.get("python_command"),
                "run_command": environment.get("run_command"),
                "test_command": environment.get("test_command"),
            }
        return {}

    def _update_runtime_state(self, selection: ToolSelection, exec_result: Any) -> None:
        controller = getattr(self.runtime, "runtime_controller", None)
        state = getattr(controller, "state", None)
        updater = getattr(controller, "state_updater", None)
        if state is None or updater is None:
            return
        try:
            updater.apply_tool_result(state, selection, exec_result)
        except Exception as exc:
            self.owner._log(
                "runtime_state_update_failed",
                output_summary={"tool": selection.tool_name, "step_id": selection.step_id},
                success=False,
                error=str(exc),
            )

    def _input_from_raw(self, tool_name: str, raw_input: dict[str, Any]) -> ToolInputMetadata:
        try:
            return ToolInputMetadata.from_mapping(tool_name, raw_input)
        except Exception:
            runtime_handles = {key: value for key, value in raw_input.items() if str(key).startswith("_")}
            attributes = {str(key): value for key, value in raw_input.items() if not str(key).startswith("_")}
            return ToolInputMetadata(tool_name=tool_name, attributes=attributes, runtime_handles=runtime_handles)

    def _required_fields(self, tool_name: str) -> list[str]:
        registry = getattr(self.runtime, "tool_registry", None)
        definition = registry.get(tool_name) if registry and hasattr(registry, "get") else None
        contract = getattr(definition, "contract_metadata", None)
        required = getattr(contract, "required_input_fields", None)
        if required:
            return list(required)
        fallback = {
            "code_generator": ["task_description"],
            "file_writer": ["file_path", "content"],
            "file_delete_tool": ["file_path"],
            "readme_tool": ["project_path"],
            "command_executor": ["command"],
        }
        return fallback.get(tool_name, [])

    def _missing_required_any_of(self, tool_name: str, input_metadata: ToolInputMetadata) -> list[str]:
        registry = getattr(self.runtime, "tool_registry", None)
        definition = registry.get(tool_name) if registry and hasattr(registry, "get") else None
        contract = getattr(definition, "contract_metadata", None)
        required_any_of = getattr(contract, "required_any_of", None)
        if not required_any_of and tool_name == "multi_file_reader":
            required_any_of = [["file_paths"], ["directory_path"]]
        if not required_any_of:
            return []
        params = input_metadata.to_params()
        for field_group in required_any_of:
            if all(params.get(field) not in (None, "", [], {}) for field in field_group):
                return []
        return [str(field) for field_group in required_any_of for field in field_group]

    def _protocol_error(
        self,
        session_id: str,
        task_id: str,
        step_id: str,
        call_id: str,
        tool_name: str,
        error_type: str,
        message: str,
        input_metadata: ToolInputMetadata,
        tool_context: ToolContextMetadata | None,
        *,
        suggested_recovery: str = "",
        details: dict[str, Any] | None = None,
        provider_call_id: str | None = None,
    ) -> ToolErrorMetadata:
        suggested = suggested_recovery or self._suggest_recovery(tool_name, message)
        enriched_details = {
            "tool_name": tool_name or "unknown",
            "call_id": call_id,
            "step_id": step_id,
            "input_summary": self._input_summary(input_metadata),
        }
        enriched_details.update(details or {})
        if provider_call_id:
            enriched_details["provider_call_id"] = provider_call_id
        failure = FailureMetadata(
            error_type=error_type,
            error_message=message,
            recoverable=True,
            retry_recommended=True,
            recovery_strategy=suggested,
            details=enriched_details,
        )
        return ToolErrorMetadata(
            session_id=session_id,
            task_id=task_id,
            step_id=step_id,
            call_id=call_id,
            provider_call_id=provider_call_id,
            tool_name=tool_name or "unknown",
            error_type=error_type,
            error_message=message,
            recoverable=True,
            suggested_recovery=suggested,
            failure=failure,
            input_metadata=input_metadata,
            tool_context=tool_context,
            event_index=self._next_event_index(),
        )

    def _record_tool_error(
        self,
        task_id: str,
        tool_call: ToolCallMetadata,
        tool_error: ToolErrorMetadata,
        round_index: int,
    ) -> None:
        if tool_error.recoverable:
            self.recoverable_errors.append(tool_error)
        self.owner._log(
            "tool_loop_recoverable_error" if tool_error.recoverable else "tool_loop_terminal_error",
            output_summary={
                "task_id": task_id,
                "call_id": tool_call.call_id,
                "provider_call_id": tool_call.provider_call_id,
                "tool": tool_call.tool_name,
                "error_type": tool_error.error_type,
                "recoverable": tool_error.recoverable,
                "suggested_recovery": tool_error.suggested_recovery,
            },
            success=False,
            error=tool_error.error_message,
            level="WARNING" if tool_error.recoverable else "ERROR",
        )
        self._append_event(
            task_id,
            tool_call,
            "error",
            "error",
            input_metadata=tool_error.input_metadata,
            tool_context=tool_error.tool_context,
            failure=tool_error.failure,
            tool_error=tool_error,
            recoverable=tool_error.recoverable,
            round_index=round_index,
        )
        diagnostics = getattr(self.runtime, "runtime_diagnostics_hooks", None)
        if diagnostics:
            diagnostics.on_tool_failed(tool_error)

    def _append_event(
        self,
        task_id: str,
        tool_call: ToolCallMetadata,
        event_type: str,
        status: str,
        *,
        input_metadata: ToolInputMetadata | None = None,
        output_metadata: ToolResultMetadata | None = None,
        tool_context: ToolContextMetadata | None = None,
        failure: FailureMetadata | None = None,
        tool_error: ToolErrorMetadata | None = None,
        recoverable: bool = True,
        round_index: int = 1,
        provider_executed: bool | None = None,
    ) -> None:
        if provider_executed is None:
            provider_executed = bool(tool_call.provider_executed)
        event = self.event_emitter.emit(
            task_id=task_id,
            tool_call=tool_call,
            event_type=event_type,
            status=status,
            input_metadata=input_metadata,
            output_metadata=output_metadata,
            tool_context=tool_context,
            tool_error=tool_error,
            failure=failure,
            recoverable=recoverable,
            provider_executed=provider_executed,
            round_index=round_index,
        )
        self.events.append(event)

    def _emit_ui_tool_event(self, event: ToolEventMetadata) -> None:
        self.event_emitter.emit_ui(event)

    def _append_tool_result(
        self,
        tool_call: ToolCallMetadata,
        input_metadata: ToolInputMetadata,
        success: bool,
        error: str | None,
        output_metadata: ToolResultMetadata | None = None,
    ) -> None:
        output_result = output_metadata.result if output_metadata else None
        self.tool_results.append(
            {
                "call_id": tool_call.call_id,
                "provider_call_id": tool_call.provider_call_id,
                "provider_executed": bool(tool_call.provider_executed),
                "step_id": tool_call.step_id,
                "tool": tool_call.tool_name,
                "input_metadata": input_metadata.to_json_dict(),
                "tool_context": tool_call.tool_context.to_json_dict() if tool_call.tool_context else None,
                "result": output_result,
                "success": success,
                "error": error,
                "suggested_recovery": self._suggest_recovery(tool_call.tool_name, error or "") if error else None,
            }
        )

    def _finish(
        self,
        task_id: str,
        session_id: str,
        success: bool,
        rounds_used: int,
        last_output: ToolResultMetadata | None,
        final_error: FailureMetadata | None,
        error_message: str | None,
    ) -> ToolEventLoopRunResult:
        loop_metadata = ToolLoopMetadata(
            session_id=session_id,
            task_id=task_id,
            status="completed" if success else "failed",
            success=success,
            rounds_used=rounds_used,
            max_rounds=self.max_steps,
            events=self.events,
            tool_invocations=self.tool_invocations,
            recoverable_errors=self.recoverable_errors,
            tool_contexts=self.tool_contexts,
            retry_count=self._retry_count,
            fallback_count=self._fallback_count,
            provider_executed=self._provider_executed,
            final_output=last_output,
            final_error=final_error,
        )
        self.owner._log(
            "tool_event_loop_completed",
            output_summary={
                "task_id": task_id,
                "success": success,
                "rounds_used": rounds_used,
                "events": len(self.events),
                "recoverable_errors": len(self.recoverable_errors),
            },
            success=success,
            error=error_message,
            level="INFO" if success else "ERROR",
        )
        return ToolEventLoopRunResult(
            success=success,
            tool_results=self.tool_results,
            last_output=last_output,
            loop_metadata=loop_metadata,
            error_message=error_message,
        )

    def _build_recovery_prompt(self, initial_prompt: str, errors: list[ToolErrorMetadata]) -> str:
        errors_payload = [
            {
                "tool_name": error.tool_name,
                "call_id": error.call_id,
                "provider_call_id": error.provider_call_id,
                "error_type": error.error_type,
                "error_message": error.error_message,
                "suggested_recovery": error.suggested_recovery,
                "input_metadata": self._recovery_input_projection(error.input_metadata),
                "tool_contract": self._contract_summary(error.tool_name),
            }
            for error in errors
        ]
        return (
            f"{initial_prompt}\n\n"
            "The previous tool call attempt produced recoverable tool errors. "
            "Revise the decision_needs JSON and try again. Focus on the unresolved information need only; "
            "do not repeat needs that already produced useful state, and do not repeat the same invalid need/input.\n"
            f"Recoverable errors:\n{json.dumps(errors_payload, ensure_ascii=False, indent=2)}"
        )

    @classmethod
    def _recovery_input_projection(
        cls,
        input_metadata: ToolInputMetadata | None,
    ) -> dict[str, Any] | None:
        """Return a bounded model-facing view without changing authoritative tool input."""
        if input_metadata is None:
            return None
        source = input_metadata.to_json_dict()
        allowed_fields = cls._RECOVERY_CONTROL_FIELDS | cls._RECOVERY_OBSERVATION_FIELDS
        projected = {
            field_name: cls._redact_recovery_value(source[field_name])
            for field_name in allowed_fields
            if field_name in source and source[field_name] is not None
        }
        for field_name in cls._RECOVERY_OBSERVATION_FIELDS:
            if field_name not in projected or projected[field_name] is None:
                continue
            serialized = cls._canonical_observation(projected[field_name])
            if len(serialized) <= cls._RECOVERY_OBSERVATION_MASK_THRESHOLD:
                continue
            projected[field_name] = {
                "observation_masked": True,
                "chars": len(serialized),
                "sha256": f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}",
            }
        return projected

    @classmethod
    def _redact_recovery_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = str(raw_key)
                redacted[key] = (
                    {"redacted": True}
                    if cls._RECOVERY_SENSITIVE_KEY.search(key)
                    else cls._redact_recovery_value(item)
                )
            return redacted
        if isinstance(value, list):
            return [cls._redact_recovery_value(item) for item in value]
        return value

    @staticmethod
    def _canonical_observation(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _call_signature(self, tool_name: str, input_metadata: ToolInputMetadata) -> str:
        stable_input = input_metadata.model_dump(
            mode="json",
            exclude={
                "kind",
                "schema_version",
                "source",
                "correlation",
                "created_at",
                "annotations",
                "runtime_handles",
                "tool_name",
            },
        )
        return json.dumps({"tool": tool_name, "input": stable_input}, sort_keys=True, ensure_ascii=False)

    def _uses_local_code_fallback(self, input_metadata: ToolInputMetadata) -> bool:
        prompt_context = input_metadata.prompt_context
        return bool(
            isinstance(prompt_context, dict)
            and prompt_context.get("local_fallback_after_provider_failure")
        )

    def _local_code_fallback_requests(
        self,
        tool_requests: list[dict[str, Any]],
        failed_index: int,
    ) -> list[dict[str, Any]]:
        pending = [
            dict(request)
            for request in tool_requests[failed_index:]
            if isinstance(request, dict)
        ]
        if not pending:
            return []
        first = pending[0]
        raw_input = first.get("input_metadata")
        input_metadata = dict(raw_input) if isinstance(raw_input, dict) else {}
        prompt_context = input_metadata.get("prompt_context")
        prompt_context = dict(prompt_context) if isinstance(prompt_context, dict) else {}
        prompt_context["local_fallback_after_provider_failure"] = True
        input_metadata["prompt_context"] = prompt_context
        first["input_metadata"] = input_metadata
        first["reason"] = (
            f"{str(first.get('reason') or 'Generate code')}. "
            "Use deterministic local fallback after repeated transient provider failure."
        )
        return pending

    def _looks_like_invented_intermediate_file(self, file_path: Path) -> bool:
        name = file_path.name.lower()
        if re.fullmatch(r"subtask_\d+\.md", name):
            return True
        return name in {"requirements.md", "plan.md"} and any(part.lower() == "results" for part in file_path.parts)

    def _is_recoverable_error(self, tool_name: str, message: str) -> bool:
        lowered = message.lower()
        recoverable_tokens = (
            "unknown tool",
            "unsupported language",
            "unsupported command execution mode",
            "missing required",
            "missing field",
            "requires",
            "required metadata",
            "provide ",
            "invalid input",
            "invalidinput",
            "validation",
            "not a file",
            "file not found",
            "expected a file path",
            "received a directory",
            "generated placeholder",
            "placeholder text",
            "malformed requirements content",
            "dependency specifiers",
            "file exists",
            "refuses to overwrite",
            "operation_kind=file_replace",
            "timeout",
            "timed out",
            "read operation timed out",
            "connection error",
            "network error",
            "incomplete chunked read",
            "peer closed connection",
        )
        return any(token in lowered for token in recoverable_tokens)

    def _is_transient_error(self, message: str) -> bool:
        lowered = message.lower()
        return any(
            token in lowered
            for token in (
                "timeout",
                "timed out",
                "connection error",
                "network error",
                "incomplete chunked read",
                "peer closed connection",
            )
        )

    def _suggest_recovery(self, tool_name: str, message: str) -> str:
        lowered = message.lower()
        if self._is_transient_error(lowered):
            if tool_name == "code_generator":
                return "Retry code generation with a bounded request. If the provider remains unavailable, simplify the requested artifact and preserve the same target file."
            return "Retry the operation with a bounded request. The failure appears transient rather than a tool-contract violation."
        if tool_name == "code_generator" and ("text" in lowered or "unsupported language" in lowered):
            return "Use code_generator only for python/shell/bash code. For prose/design tasks, write Markdown/text with a document/file tool or return planning metadata."
        if tool_name == "command_executor":
            return "Use command_executor mode automatic, interactive, or dry_run. Aliases execute/run/exec are normalized to automatic."
        if tool_name == "multi_file_reader":
            return "Provide file_paths as a list of files, or directory_path with an optional pattern/max_files."
        if tool_name == "file_reader" and ("not a file" in lowered or "directory" in lowered):
            return "Use multi_file_reader with directory_path to inspect directories, or provide a concrete file path to file_reader."
        if tool_name == "file_reader" and "file not found" in lowered:
            if "subtask_" in lowered or "requirements.md" in lowered or "plan.md" in lowered:
                return "Use the original goal or shared execution history instead of reading invented intermediate files."
            return "Check that file_path exists before calling file_reader, or use project_structure/multi_file_reader to discover files first."
        if tool_name == "file_writer" and ("placeholder" in lowered or "generated" in lowered):
            return "Regenerate real content or route code_generator output into file_writer.content before writing."
        if tool_name == "file_writer" and ("malformed requirements" in lowered or "dependency specifiers" in lowered):
            return (
                "Do not write generated source code to requirements files. "
                "Use a .py/.js/etc. source file for code, or write only dependency specifiers to requirements.txt."
            )
        if "unknown tool" in lowered:
            return "Select a registered tool from the available tools list."
        if "missing required" in lowered or "requires" in lowered:
            return "Provide all required input_metadata fields for the selected tool."
        return "Revise the tool call and arguments according to the tool contract."

    def _generated_placeholder_reason(self, content: str) -> str | None:
        placeholder_patterns = (
            (r"will be replaced", "will be replaced marker"),
            (r"\bREPLACE_ME\b", "REPLACE_ME token"),
            (r"\bTO_BE_FILLED\b", "TO_BE_FILLED token"),
            (r"FILLED_BY_CODEGENERATION", "filled-by-codegeneration marker"),
            (r"\bTODO_GENERATED\b", "TODO_GENERATED token"),
            (r"\bCODE_GENERATOR_OUTPUT\b", "CODE_GENERATOR_OUTPUT token"),
            (r"\{\{\s*[^}]*output[^}]*\}\}", "template output placeholder"),
            (r"待填充|后填充|输出填充|由.*填充", "Chinese fill-later marker"),
            (r"前一步输出", "previous-step-output marker"),
            (r"code_generation生成", "code_generation fill marker"),
        )
        for pattern, reason in placeholder_patterns:
            if re.search(pattern, content, flags=re.IGNORECASE):
                return reason
        standalone = content.strip().strip("#/*- ").strip()
        if re.fullmatch(r"PLACEHOLDER", standalone, flags=re.IGNORECASE):
            return "standalone PLACEHOLDER token"
        if standalone == "占位":
            return "standalone Chinese placeholder marker"
        return None

    def _enrich_execution_failure(
        self,
        failure: FailureMetadata,
        *,
        tool_call: ToolCallMetadata,
        input_metadata: ToolInputMetadata,
        suggested_recovery: str,
    ) -> FailureMetadata:
        details = dict(getattr(failure, "details", {}) or {})
        details.update(
            {
                "tool_name": tool_call.tool_name,
                "call_id": tool_call.call_id,
                "step_id": tool_call.step_id,
                "error_type": failure.error_type,
                "error_message": failure.error_message,
                "input_summary": self._input_summary(input_metadata),
                "suggested_recovery": suggested_recovery,
            }
        )
        if tool_call.provider_call_id:
            details["provider_call_id"] = tool_call.provider_call_id
        message = failure.error_message
        error_type = failure.error_type
        if tool_call.tool_name == "file_reader" and "not a file" in message.lower():
            error_type = "FileReaderDirectoryPath"
            message = f"file_reader expected a file path but received a directory: {input_metadata.file_path}"
            details["received_path"] = str(input_metadata.file_path)
            details["suggested_tool"] = "multi_file_reader"
            details["suggested_input"] = {"directory_path": str(input_metadata.file_path), "pattern": "*"}
        return failure.model_copy(
            update={
                "error_type": error_type,
                "error_message": message,
                "recoverable": bool(failure.recoverable) or self._is_recoverable_error(tool_call.tool_name, message),
                "retry_recommended": bool(failure.retry_recommended)
                or self._is_recoverable_error(tool_call.tool_name, message),
                "recovery_strategy": failure.recovery_strategy or suggested_recovery,
                "details": details,
            }
        )

    def _input_summary(self, input_metadata: ToolInputMetadata) -> dict[str, Any]:
        params = input_metadata.to_params()
        summary: dict[str, Any] = {}
        for key in (
            "file_path",
            "directory_path",
            "file_paths",
            "command",
            "cwd",
            "read_mode",
            "pattern",
            "mode",
            "project_path",
        ):
            value = params.get(key)
            if value not in (None, "", [], {}):
                summary[key] = value
        if params.get("content"):
            summary["content_length"] = len(str(params["content"]))
        if params.get("code"):
            summary["code_length"] = len(str(params["code"]))
        return summary

    def _contract_summary(self, tool_name: str) -> dict[str, Any]:
        registry = getattr(self.runtime, "tool_registry", None)
        definition = registry.get(tool_name) if registry and hasattr(registry, "get") else None
        contract = getattr(definition, "contract_metadata", None)
        if not contract:
            return {}
        return {
            "required_input_fields": list(getattr(contract, "required_input_fields", []) or []),
            "required_any_of": list(getattr(contract, "required_any_of", []) or []),
            "input_defaults": dict(getattr(contract, "input_defaults", {}) or {}),
        }

    def _last_recoverable_error_details(self) -> dict[str, Any]:
        if not self.recoverable_errors:
            return {}
        error = self.recoverable_errors[-1]
        return {
            "tool_name": error.tool_name,
            "call_id": error.call_id,
            "provider_call_id": error.provider_call_id,
            "error_type": error.error_type,
            "error_message": error.error_message,
            "suggested_recovery": error.suggested_recovery,
            "input_metadata": error.input_metadata.to_json_dict() if error.input_metadata else None,
            "tool_contract": self._contract_summary(error.tool_name),
        }

    def _tool_loop_exceeded_message(self) -> str:
        base = f"Tool event loop exceeded {self.max_steps} correction step(s)"
        if not self.recoverable_errors:
            return base
        error = self.recoverable_errors[-1]
        recovery = f"; suggested recovery: {error.suggested_recovery}" if error.suggested_recovery else ""
        return (
            f"{base}. Last unresolved tool error: {error.tool_name} ({error.call_id}) "
            f"{error.error_type}: {error.error_message}{recovery}"
        )

    def _next_event_index(self) -> int:
        return self.event_emitter.next_event_index()
