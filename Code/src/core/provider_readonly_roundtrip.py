"""Execute a bounded provider-native read-only conversation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.llm import LLMMessage, LLMResponse, LLMToolDefinition
from core.provider_completion_outcome import provider_completion_outcome
from core.provider_completion_usage_observation import provider_completion_tokens
from core.provider_execution_dispatch import dispatch_provider_execution_batch
from core.provider_round_request_builder import build_provider_round_llm_request
from core.provider_round_request_plan import build_provider_round_request_plan
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
from metadata import ContextRequestPurpose, RuntimeBudgetMetadata


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
        max_rounds: int = 3,
        max_tokens: int | None = None,
        context_max_prompt_tokens: int | None = None,
        allow_mutations: bool = False,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
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
        self.max_rounds = max_rounds
        self.max_tokens = max_tokens
        self.context_max_prompt_tokens = context_max_prompt_tokens
        self._attempt_ledger = ProviderToolAttemptLedger()

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
                    finalization_pending=False,
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
                    tool_definitions=self.tools,
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
                if not response.tool_calls:
                    return ProviderReadonlyRoundTripResult(
                        success=bool(response.content.strip()),
                        final_response=response,
                        messages=tuple(current_messages),
                        tool_loop_results=tuple(loop_results),
                        rounds_used=round_index,
                        error_message=None if response.content.strip() else "ProviderToolEmptyResponse",
                        attempts=self._attempt_ledger.attempts,
                    )

                partition = partition_provider_tool_calls(
                    response.tool_calls,
                    ledger=self._attempt_ledger,
                    round_index=round_index,
                    project_path=None,
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
            except Exception as exc:
                return ProviderReadonlyRoundTripResult(
                    success=False,
                    final_response=last_response,
                    messages=tuple(current_messages),
                    tool_loop_results=tuple(loop_results),
                    rounds_used=round_index,
                    error_message=str(exc)[:2000] or type(exc).__name__,
                    attempts=self._attempt_ledger.attempts,
                )

        return ProviderReadonlyRoundTripResult(
            success=False,
            final_response=last_response,
            messages=tuple(current_messages),
            tool_loop_results=tuple(loop_results),
            rounds_used=self.max_rounds,
            error_message="ProviderToolRoundLimitExceeded",
            attempts=self._attempt_ledger.attempts,
        )

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
