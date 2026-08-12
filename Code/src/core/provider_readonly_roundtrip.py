"""Execute a bounded provider-native read-only conversation."""

from __future__ import annotations

from collections.abc import Sequence
import json
from pathlib import Path
from typing import Any

from core.llm import LLMMessage, LLMResponse, LLMToolDefinition
from core.provider_completion_outcome import provider_completion_outcome
from core.provider_completion_usage_observation import provider_completion_tokens
from core.provider_execution_dispatch import dispatch_provider_execution_batch
from core.provider_round_request_builder import build_provider_round_llm_request
from core.provider_round_request_plan import build_provider_round_request_plan
from core.provider_final_response_transition import (
    ProviderFinalResponseAction,
    provider_final_response_transition,
)
from core.provider_read_finalization_transition import (
    ProviderReadFinalizationAction,
    provider_read_finalization_transition,
)
from core.provider_no_progress_transition import (
    ProviderNoProgressAction,
    provider_no_progress_transition,
)
from core.provider_tool_evidence_state import ProviderToolEvidenceState
from core.provider_tool_batch_admission import admit_provider_tool_calls
from core.provider_tool_call_signature import provider_tool_call_signature
from core.provider_tool_duplicate_partition import partition_provider_tool_calls
from core.provider_tool_attempt_ledger import ProviderToolAttemptLedger
from core.provider_tool_result_batch import provider_tool_result_batch
from core.provider_tool_roundtrip_contracts import (
    ProviderToolAttempt,
    ProviderToolRoundTripResult,
)
from core.provider_tool_wire_exchange import provider_tool_wire_exchange
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import ContextRequestPurpose, RuntimeBudgetMetadata, metadata_summary


class ProviderReadonlyRoundTripError(ValueError):
    """Raised when a read-only provider round trip cannot proceed safely."""


ProviderReadonlyRoundTripResult = ProviderToolRoundTripResult


class ProviderReadonlyRoundTripRunner:
    """Run read-only provider calls with typed request and result boundaries."""

    def __init__(
        self,
        owner: Any,
        task: Any,
        *,
        tools: Sequence[LLMToolDefinition],
        read_scope: Sequence[str] | None = None,
        project_path: str | None = None,
        max_rounds: int = 3,
        max_tokens: int | None = None,
        context_max_prompt_tokens: int | None = None,
        allow_mutations: bool = False,
        max_no_progress_rounds: int = 2,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        if not isinstance(max_no_progress_rounds, int) or isinstance(max_no_progress_rounds, bool) or max_no_progress_rounds < 1:
            raise ValueError("max_no_progress_rounds must be positive")
        if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes)):
            raise ValueError("tools must be a bounded sequence")
        if any(not isinstance(tool, LLMToolDefinition) for tool in tools):
            raise ValueError("tools must contain LLMToolDefinition values")
        if type(allow_mutations) is not bool:
            raise ValueError("allow_mutations must be a literal boolean")
        if allow_mutations:
            raise ValueError("read-only provider round cannot allow mutations")
        self.owner = owner
        self.runtime = owner.runtime
        self.task = task
        self.tools = tuple(tool.model_copy(deep=True) for tool in tools)
        self.read_scope = tuple(read_scope or ())
        self.project_path = str(project_path or "").strip() or None
        self.max_rounds = max_rounds
        self.max_tokens = max_tokens
        self.context_max_prompt_tokens = context_max_prompt_tokens
        self._attempt_ledger = ProviderToolAttemptLedger()
        self._evidence = ProviderToolEvidenceState(project_path=self.project_path)
        self._bounded_projection_seen = False
        self._last_round_progress = False
        self._finalization_pending = False
        self._finalization_requests = 0
        self._max_no_progress_rounds = max_no_progress_rounds
        self._no_progress_rounds = 0
        self._duplicate_only_rounds = 0

        if not self.tools:
            raise ValueError("read-only provider round requires at least one tool")
        if any(tool.function.name in {"file_patch_writer", "file_writer", "file_delete_tool"} for tool in self.tools):
            raise ValueError("read-only provider round cannot expose mutation tools")

    def run(self, messages: Sequence[LLMMessage]) -> ProviderReadonlyRoundTripResult:
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
            raise ProviderReadonlyRoundTripError("messages must be a bounded sequence")
        current_messages = [message.model_copy(deep=True) for message in messages]
        loop_results: list[ToolEventLoopRunResult] = []
        last_response: LLMResponse | None = None

        for round_index in range(1, self.max_rounds + 1):
            try:
                budget = self._runtime_budget()
                calls_remaining = self.max_rounds - round_index + 1
                prompt_budget = self.context_max_prompt_tokens or int(
                    getattr(self.runtime.llm_client.settings, "context_max_prompt_tokens", 4096)
                    or 4096
                )
                plan = build_provider_round_request_plan(
                    messages=current_messages,
                    tool_names=[tool.function.name for tool in self.tools],
                    finalization_pending=self._finalization_pending,
                    post_mutation_active=False,
                    mutation_tools_exposed=False,
                    all_scoped_reads_complete=False,
                    prompt_budget_tokens=prompt_budget,
                    response_call_count=calls_remaining,
                )
                request_max_tokens = budget.tool_event_completion_limit(
                    round_index=round_index,
                    calls_remaining=calls_remaining,
                )
                if request_max_tokens <= 0:
                    raise ProviderReadonlyRoundTripError(
                        "provider tool completion budget is exhausted"
                    )
                request = build_provider_round_llm_request(
                    self.runtime.llm_client,
                    plan=plan,
                    tool_definitions=() if self._finalization_pending else self.tools,
                    purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
                    max_tokens=min(
                        request_max_tokens,
                        self.max_tokens
                        if self.max_tokens is not None
                        else request_max_tokens,
                    ),
                    timeout_seconds=45.0,
                    transport_retries=0,
                    reasoning_policy=self._reasoning_policy(),
                    trace_info={"provider_round": round_index},
                )
                budget.consume_tool_event_completion(request.max_tokens or request_max_tokens)
                response = self.runtime.llm_client.complete(request)
                if not isinstance(response, LLMResponse):
                    raise ProviderReadonlyRoundTripError("provider returned a non-LLMResponse value")
                last_response = response
                usage = provider_completion_tokens(response)
                if usage is not None and request.max_tokens is not None and usage > request.max_tokens:
                    raise ProviderReadonlyRoundTripError("provider completion usage exceeded request limit")
                outcome = provider_completion_outcome(response)
                self._observe_completion_budget(budget, request.max_tokens, usage, outcome)
                response_transition = provider_final_response_transition(
                    response,
                    finalization_pending=self._finalization_pending,
                )
                if response_transition.action is ProviderFinalResponseAction.COMPLETE:
                    return self._result(
                        success=bool(response.content.strip()),
                        final_response=response,
                        messages=current_messages,
                        tool_loop_results=loop_results,
                        rounds_used=round_index,
                        error_message=None
                        if response.content.strip()
                        else "ProviderToolEmptyResponse",
                    )
                if response_transition.action is ProviderFinalResponseAction.FAIL:
                    error_code = response_transition.error_code.value
                    return self._result(
                        success=False,
                        final_response=response,
                        messages=current_messages,
                        tool_loop_results=loop_results,
                        rounds_used=round_index,
                        error_message=error_code,
                    )

                if self._finalization_pending:
                    raise ProviderReadonlyRoundTripError(
                        "provider finalization transition returned an invalid action"
                    )

                partition = partition_provider_tool_calls(
                    response.tool_calls,
                    ledger=self._attempt_ledger,
                    round_index=round_index,
                    project_path=self.project_path,
                )
                admissions = admit_provider_tool_calls(
                    list(partition.new_calls),
                    task_id=str(getattr(self.task, "id", "unknown")),
                    session_id=self.owner._session_id(),
                    round_index=round_index,
                    registry=self.runtime.tool_registry,
                    budget=budget,
                    user_confirmed=False,
                    allow_mutations=False,
                    read_scope=self.read_scope,
                    project_path=self.project_path,
                )
                loop_result = dispatch_provider_execution_batch(
                    self._event_runner(),
                    self.task,
                    admissions,
                    round_index=round_index,
                    allow_mutations=False,
                )
                loop_results.append(loop_result)
                tool_results = provider_tool_result_batch(
                    response,
                    loop_result,
                    duplicate_blocks=partition.duplicate_blocks,
                    char_budget=plan.tool_result_char_budget,
                )
                current_messages.extend(provider_tool_wire_exchange(response, tool_results))
                self._record_attempts(response, loop_result, round_index)
                self._observe_read_evidence(response, loop_result)
                duplicate_only_covered = (
                    not partition.new_calls
                    and bool(partition.duplicate_blocks)
                    and len(partition.duplicate_blocks) == len(response.tool_calls)
                    and self._all_scoped_reads_complete()
                )
                if duplicate_only_covered:
                    self._duplicate_only_rounds += 1
                    self._evidence.record_duplicate_only_round(round_index)
                no_progress = provider_no_progress_transition(
                    duplicate_only_covered=duplicate_only_covered,
                    mutation_tools_exposed=False,
                    mutation_duplicate_guidance_sent=False,
                    read_only_tool_set=True,
                    finalization_requests=self._finalization_requests,
                    round_made_progress=self._last_round_progress and not duplicate_only_covered,
                    no_progress_rounds=self._no_progress_rounds,
                    max_no_progress_rounds=self._max_no_progress_rounds,
                    duplicate_only_rounds=self._duplicate_only_rounds,
                    round_index=round_index,
                    max_rounds=self.max_rounds,
                )
                self._no_progress_rounds = no_progress.no_progress_rounds
                self._duplicate_only_rounds = no_progress.duplicate_only_rounds
                if no_progress.action is ProviderNoProgressAction.FAIL:
                    return self._result(
                        success=False,
                        final_response=response,
                        messages=current_messages,
                        tool_loop_results=loop_results,
                        rounds_used=round_index,
                        error_message=no_progress.error_code.value,
                    )
                if no_progress.action is ProviderNoProgressAction.REQUEST_FINALIZATION:
                    self._finalization_pending = True
                    self._finalization_requests = no_progress.finalization_requests
                    current_messages.append(LLMMessage(role="user", content=self._finalization_instruction()))
                    continue
                transition = provider_read_finalization_transition(
                    page_cap_ready=self._all_scoped_reads_complete(),
                    all_scoped_reads_complete=self._all_scoped_reads_complete(),
                    read_only_tool_set=True,
                    has_bounded_projection=self._bounded_projection_seen,
                    round_made_progress=self._last_round_progress,
                    finalization_requests=self._finalization_requests,
                    round_index=round_index,
                    max_rounds=self.max_rounds,
                )
                if transition.action is ProviderReadFinalizationAction.FAIL:
                    return self._result(
                        success=False,
                        final_response=response,
                        messages=current_messages,
                        tool_loop_results=loop_results,
                        rounds_used=round_index,
                        error_message=transition.error_code.value,
                    )
                if transition.action is ProviderReadFinalizationAction.REQUEST_FINALIZATION:
                    self._finalization_pending = True
                    self._finalization_requests = transition.finalization_requests
                    current_messages.append(LLMMessage(role="user", content=self._finalization_instruction()))
            except Exception as exc:
                return self._result(
                    success=False,
                    final_response=last_response,
                    messages=current_messages,
                    tool_loop_results=loop_results,
                    rounds_used=round_index,
                    error_message=str(exc)[:2000] or type(exc).__name__,
                )

        return self._result(
            success=False,
            final_response=last_response,
            messages=current_messages,
            tool_loop_results=loop_results,
            rounds_used=self.max_rounds,
            error_message="ProviderToolRoundLimitExceeded",
        )

    def _result(self, **kwargs: Any) -> ProviderReadonlyRoundTripResult:
        return ProviderReadonlyRoundTripResult(
            attempts=self._attempt_ledger.attempts,
            evidence_coverage=self._evidence.coverage(),
            **kwargs,
        )

    def _finalization_instruction(self) -> str:
        return (
            "The explicitly scoped read evidence is complete. Do not call any tool. "
            "Answer the task using only the evidence already returned, and state "
            "when the evidence is insufficient."
        )

    def _all_scoped_reads_complete(self) -> bool:
        expected = {
            self._canonical_path(path)
            for path in self.read_scope
            if str(path or "").strip()
        }
        observed = set(self._evidence.coverage().completed_read_paths)
        return bool(expected) and expected.issubset(observed)

    def _canonical_path(self, raw_path: str) -> str:
        path = Path(str(raw_path).strip()).expanduser()
        if not path.is_absolute() and self.project_path:
            path = Path(self.project_path) / path
        return str(path.resolve(strict=False))

    def _observe_read_evidence(
        self,
        response: LLMResponse,
        loop_result: ToolEventLoopRunResult,
    ) -> None:
        self._last_round_progress = False
        result_by_id = {
            str(item.get("provider_call_id")): item
            for item in loop_result.tool_results
            if item.get("provider_call_id")
        }
        for call in response.tool_calls:
            if call.function.name != "file_reader":
                continue
            item = result_by_id.get(call.id)
            if not item or not item.get("success"):
                continue
            arguments = {}
            try:
                decoded = json.loads(call.function.arguments or "{}")
                arguments = decoded if isinstance(decoded, dict) else {}
            except (TypeError, ValueError, RecursionError):
                arguments = {}
            summary = metadata_summary(item.get("result"))
            summary = summary if isinstance(summary, dict) else {}
            read_path = str(summary.get("file_path") or arguments.get("file_path") or "").strip()
            if not read_path:
                continue
            canonical = self._canonical_path(read_path)
            truncated = bool(summary.get("truncated", False))
            content = summary.get("content") or summary.get("preview") or ""
            if truncated:
                self._bounded_projection_seen = True
                is_new = self._evidence.record_completed_read(
                    canonical,
                    projection="bounded_preview",
                )
            else:
                is_new = self._evidence.record_completed_read(
                    canonical,
                    projection="inline",
                )
            if len(str(content)) > 480:
                self._bounded_projection_seen = True
            self._last_round_progress = self._last_round_progress or is_new

    def _event_runner(self) -> Any:
        from core.tool_event_loop import ToolEventLoopRunner

        return ToolEventLoopRunner(self.owner)

    def _runtime_budget(self) -> RuntimeBudgetMetadata:
        state = getattr(getattr(self.runtime, "runtime_controller", None), "state", None)
        budget = getattr(state, "budget", None)
        if not isinstance(budget, RuntimeBudgetMetadata):
            raise ProviderReadonlyRoundTripError("provider round requires active runtime budget")
        return budget

    def _reasoning_policy(self) -> Any:
        policy = getattr(self.owner, "_reasoning_policy_for_task", None)
        return policy(self.task) if callable(policy) else None

    @staticmethod
    def _observe_completion_budget(
        budget: RuntimeBudgetMetadata,
        requested: int | None,
        actual: int | None,
        outcome: Any,
    ) -> None:
        if requested is not None and actual is not None:
            budget.reconcile_tool_event_completion(reserved=requested, actual=actual)
        from metadata import ToolEventCompletionOutcome

        budget.observe_tool_event_outcome(
            ToolEventCompletionOutcome(outcome.value)
            if hasattr(outcome, "value")
            else outcome
        )

    def _record_attempts(
        self,
        response: LLMResponse,
        loop_result: ToolEventLoopRunResult,
        round_index: int,
    ) -> None:
        results = {str(item.get("provider_call_id")): item for item in loop_result.tool_results}
        errors = {
            str(item.provider_call_id): item
            for item in loop_result.loop_metadata.recoverable_errors
            if item.provider_call_id
        }
        for call in response.tool_calls:
            item = results.get(call.id)
            if item is None:
                continue
            if self._attempt_ledger.provider_call_seen(call.id):
                continue
            self._attempt_ledger.record(
                ProviderToolAttempt(
                    signature=provider_tool_call_signature(call),
                    tool_name=call.function.name,
                    provider_call_id=call.id,
                    round_index=round_index,
                    success=bool(item.get("success")),
                    error_type=(
                        errors[call.id].error_type
                        if call.id in errors
                        else "ProviderToolExecutionFailed"
                    )
                    if not bool(item.get("success"))
                    else None,
                )
            )


__all__ = [
    "ProviderReadonlyRoundTripError",
    "ProviderReadonlyRoundTripResult",
    "ProviderReadonlyRoundTripRunner",
]
