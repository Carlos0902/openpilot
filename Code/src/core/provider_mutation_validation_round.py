"""Execute one exact validation round after an admitted provider mutation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from core.llm import LLMMessage, LLMResponse, LLMToolDefinition
from core.provider_execution_dispatch import dispatch_provider_execution_batch
from core.provider_mutation_round_execution import ProviderMutationRoundResult
from core.provider_mutation_transition import (
    ProviderMutationTransition,
    provider_mutation_transition,
)
from core.provider_post_mutation_context import provider_post_mutation_messages
from core.provider_round_request_builder import build_provider_round_llm_request
from core.provider_round_request_plan import build_provider_round_request_plan
from core.provider_tool_batch_admission import admit_provider_tool_calls
from core.provider_tool_call_signature import provider_tool_call_signature
from core.provider_tool_attempt_ledger import ProviderToolAttemptLedger
from core.provider_tool_result_batch import provider_tool_result_batch
from core.provider_tool_roundtrip_contracts import ProviderToolAttempt
from core.provider_tool_wire_exchange import provider_tool_wire_exchange
from core.provider_validation_observation import (
    ProviderValidationObservation,
    provider_validation_observation,
)
from metadata import ContextRequestPurpose, RuntimeBudgetMetadata


class ProviderMutationValidationRoundError(ValueError):
    """Raised when exact post-mutation validation cannot be admitted safely."""


@dataclass(frozen=True)
class ProviderMutationValidationRoundResult:
    """Bounded validation evidence and the next typed mutation transition."""

    observation: ProviderValidationObservation
    transition: ProviderMutationTransition
    loop_result: Any | None
    messages: tuple[LLMMessage, ...]
    attempts: tuple[ProviderToolAttempt, ...]
    rounds_used: int
    error_message: str | None = None


class ProviderMutationValidationRoundRunner:
    """Run exactly one provider validation request after a mutation receipt."""

    def __init__(
        self,
        owner: Any,
        task: Any,
        *,
        mutation: ProviderMutationRoundResult,
        messages: Sequence[LLMMessage],
        tools: Sequence[LLMToolDefinition],
        validation_command: str,
        project_path: str | None = None,
        validation_cwd: str | None = None,
        max_rounds: int = 2,
        max_tokens: int | None = None,
        context_max_prompt_tokens: int | None = None,
        user_confirmed: bool = False,
    ) -> None:
        if not isinstance(mutation, ProviderMutationRoundResult):
            raise ProviderMutationValidationRoundError(
                "validation requires a completed mutation round"
            )
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
            raise ProviderMutationValidationRoundError("messages must be a bounded sequence")
        if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes)):
            raise ProviderMutationValidationRoundError("tools must be a bounded sequence")
        if not any(tool.function.name == "command_executor" for tool in tools):
            raise ProviderMutationValidationRoundError(
                "validation round requires command_executor"
            )
        if not isinstance(validation_command, str) or not validation_command.strip():
            raise ProviderMutationValidationRoundError(
                "validation requires an exact command"
            )
        if type(user_confirmed) is not bool or not user_confirmed:
            raise ProviderMutationValidationRoundError(
                "validation requires explicit confirmation"
            )
        if type(max_rounds) is not int or max_rounds < 1:
            raise ProviderMutationValidationRoundError(
                "max_rounds must be positive"
            )
        self.owner = owner
        self.runtime = owner.runtime
        self.task = task
        self.mutation = mutation
        self.validation_command = validation_command.strip()
        self.project_path = str(project_path or "").strip() or None
        self.validation_cwd = str(validation_cwd or self.project_path or "").strip() or None
        self.max_rounds = max_rounds
        self.max_tokens = max_tokens
        self.context_max_prompt_tokens = context_max_prompt_tokens
        self.user_confirmed = user_confirmed
        self.tools = tuple(tool.model_copy(deep=True) for tool in tools)
        self._messages = provider_post_mutation_messages(
            list(messages),
            mutation.loop_result,
            validation_command=self.validation_command,
            wire_messages=list(mutation.wire_messages),
        )
        self._attempt_ledger = ProviderToolAttemptLedger()

    def run(self) -> ProviderMutationValidationRoundResult:
        last_loop_result = None
        for round_index in range(1, self.max_rounds + 1):
            try:
                budget = self._runtime_budget()
                request_limit = budget.tool_event_completion_limit(
                    round_index=round_index,
                    calls_remaining=self.max_rounds - round_index + 1,
                )
                if request_limit <= 0:
                    raise ProviderMutationValidationRoundError(
                        "provider validation completion budget is exhausted"
                    )
                plan = build_provider_round_request_plan(
                    messages=self._messages,
                    tool_names=["command_executor"],
                    finalization_pending=False,
                    post_mutation_active=True,
                    mutation_tools_exposed=False,
                    all_scoped_reads_complete=False,
                    prompt_budget_tokens=(
                        self.context_max_prompt_tokens
                        or int(getattr(self.runtime.llm_client.settings, "context_max_prompt_tokens", 4096) or 4096)
                    ),
                    response_call_count=self.max_rounds - round_index + 1,
                )
                request = build_provider_round_llm_request(
                    self.runtime.llm_client,
                    plan=plan,
                    tool_definitions=[
                        tool for tool in self.tools
                        if tool.function.name == "command_executor"
                    ],
                    purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
                    max_tokens=min(
                        request_limit,
                        self.max_tokens if self.max_tokens is not None else request_limit,
                    ),
                    timeout_seconds=45.0,
                    transport_retries=0,
                    reasoning_policy=self._reasoning_policy(),
                    trace_info={"provider_mutation_validation_round": round_index},
                )
                reserved = request.max_tokens or request_limit
                budget.consume_tool_event_completion(reserved)
                response = self.runtime.llm_client.complete(request)
                if not isinstance(response, LLMResponse):
                    raise ProviderMutationValidationRoundError(
                        "provider returned a non-LLMResponse value"
                    )
                usage = response.usage.get("completion_tokens") if isinstance(response.usage, dict) else None
                if type(usage) is int:
                    if usage > reserved:
                        raise ProviderMutationValidationRoundError(
                            "provider completion usage exceeded request limit"
                        )
                    budget.reconcile_tool_event_completion(
                        reserved=reserved,
                        actual=usage,
                    )
                admissions = self._admit_validation(response, budget, round_index)
                last_loop_result = dispatch_provider_execution_batch(
                    self._event_runner(),
                    self.task,
                    admissions,
                    round_index=round_index,
                    allow_mutations=False,
                )
                observation = provider_validation_observation(
                    last_loop_result,
                    validation_command=self.validation_command,
                )
                transition = provider_mutation_transition(
                    receipt_available=True,
                    receipt_observed_this_round=False,
                    validation_observation=observation,
                    round_index=round_index + 1,
                    max_rounds=self.max_rounds + 1,
                )
                self._messages.extend(
                    provider_tool_wire_exchange(
                        response,
                        provider_tool_result_batch(
                            response,
                            last_loop_result,
                            char_budget=plan.tool_result_char_budget,
                        ),
                    )
                )
                self._record_attempts(response, last_loop_result, round_index)
                return ProviderMutationValidationRoundResult(
                    observation=observation,
                    transition=transition,
                    loop_result=last_loop_result,
                    messages=tuple(self._messages),
                    attempts=self._attempt_ledger.attempts,
                    rounds_used=round_index,
                )
            except Exception as exc:
                if round_index >= self.max_rounds:
                    return ProviderMutationValidationRoundResult(
                        observation=ProviderValidationObservation.NOT_OBSERVED,
                        transition=provider_mutation_transition(
                            receipt_available=True,
                            receipt_observed_this_round=False,
                            validation_observation=ProviderValidationObservation.NOT_OBSERVED,
                            round_index=round_index,
                            max_rounds=self.max_rounds,
                        ),
                        loop_result=last_loop_result,
                        messages=tuple(self._messages),
                        attempts=self._attempt_ledger.attempts,
                        rounds_used=round_index,
                        error_message=str(exc)[:2000] or type(exc).__name__,
                    )
        raise AssertionError("validation loop exited without a bounded result")

    def _admit_validation(
        self,
        response: LLMResponse,
        budget: RuntimeBudgetMetadata,
        round_index: int,
    ):
        if len(response.tool_calls) != 1:
            raise ProviderMutationValidationRoundError(
                "validation requires exactly one provider tool call"
            )
        call = response.tool_calls[0]
        if call.function.name != "command_executor":
            raise ProviderMutationValidationRoundError(
                "validation round permits command_executor only"
            )
        return admit_provider_tool_calls(
            [call],
            task_id=str(getattr(self.task, "id", "unknown")),
            session_id=self.owner._session_id(),
            round_index=round_index,
            registry=self.runtime.tool_registry,
            budget=budget,
            user_confirmed=self.user_confirmed,
            allow_mutations=False,
            validation_command=self.validation_command,
            validation_cwd=self.validation_cwd,
            validation_commands_used=0,
        )

    def _runtime_budget(self) -> RuntimeBudgetMetadata:
        state = getattr(getattr(self.runtime, "runtime_controller", None), "state", None)
        budget = getattr(state, "budget", None)
        if not isinstance(budget, RuntimeBudgetMetadata):
            raise ProviderMutationValidationRoundError(
                "validation round requires active runtime budget"
            )
        return budget

    def _reasoning_policy(self) -> Any:
        policy = getattr(self.owner, "_reasoning_policy_for_task", None)
        return policy(self.task) if callable(policy) else None

    def _event_runner(self) -> Any:
        from core.tool_event_loop import ToolEventLoopRunner

        return ToolEventLoopRunner(self.owner)

    def _record_attempts(self, response: LLMResponse, loop_result: Any, round_index: int) -> None:
        results = {str(item.get("provider_call_id")): item for item in loop_result.tool_results}
        for call in response.tool_calls:
            item = results.get(call.id)
            if item is None or self._attempt_ledger.provider_call_seen(call.id):
                continue
            success = bool(item.get("success"))
            self._attempt_ledger.record(
                ProviderToolAttempt(
                    signature=provider_tool_call_signature(call, project_path=self.project_path),
                    tool_name=call.function.name,
                    provider_call_id=call.id,
                    round_index=round_index,
                    success=success,
                    error_type=None if success else "ProviderToolExecutionFailed",
                )
            )


__all__ = [
    "ProviderMutationValidationRoundError",
    "ProviderMutationValidationRoundResult",
    "ProviderMutationValidationRoundRunner",
]
