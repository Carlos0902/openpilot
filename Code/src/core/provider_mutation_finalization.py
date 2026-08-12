"""Request one no-tool final response after exact mutation validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.llm import LLMMessage, LLMResponse
from core.provider_completion_outcome import provider_completion_outcome
from core.provider_completion_usage_observation import provider_completion_tokens
from core.provider_final_response_transition import (
    ProviderFinalResponseAction,
    ProviderFinalResponseTransition,
    provider_final_response_transition,
)
from core.provider_mutation_validation_round import (
    ProviderMutationValidationRoundResult,
)
from core.provider_round_request_builder import build_provider_round_llm_request
from core.provider_round_request_plan import build_provider_round_request_plan
from metadata import ContextRequestPurpose, RuntimeBudgetMetadata


class ProviderMutationFinalizationError(ValueError):
    """Raised when a validated mutation cannot enter finalization safely."""


@dataclass(frozen=True)
class ProviderMutationFinalizationResult:
    """Bounded final response and its typed response transition."""

    success: bool
    response: LLMResponse | None
    transition: ProviderFinalResponseTransition
    messages: tuple[LLMMessage, ...]
    rounds_used: int
    error_message: str | None = None


class ProviderMutationFinalizationRunner:
    """Run one finalization request with an empty provider tool surface."""

    def __init__(
        self,
        owner: Any,
        task: Any,
        *,
        validation: ProviderMutationValidationRoundResult,
        max_tokens: int | None = None,
        context_max_prompt_tokens: int | None = None,
    ) -> None:
        if not isinstance(validation, ProviderMutationValidationRoundResult):
            raise ProviderMutationFinalizationError(
                "finalization requires a typed validation result"
            )
        if not validation.transition.finalization_pending:
            raise ProviderMutationFinalizationError(
                "finalization requires a pending mutation transition"
            )
        self.owner = owner
        self.runtime = owner.runtime
        self.task = task
        self.validation = validation
        self.max_tokens = max_tokens
        self.context_max_prompt_tokens = context_max_prompt_tokens

    def run(self) -> ProviderMutationFinalizationResult:
        try:
            budget = self._runtime_budget()
            request_limit = budget.tool_event_completion_limit(
                round_index=self.validation.rounds_used + 1,
                calls_remaining=1,
            )
            if request_limit <= 0:
                raise ProviderMutationFinalizationError(
                    "provider finalization completion budget is exhausted"
                )
            messages = [
                *self.validation.messages,
                LLMMessage(
                    role="user",
                    content=(
                        "Exact validation succeeded. Do not call tools. Report the "
                        "completed mutation and validation evidence concisely."
                    ),
                ),
            ]
            plan = build_provider_round_request_plan(
                messages=messages,
                tool_names=["command_executor"],
                finalization_pending=True,
                post_mutation_active=False,
                mutation_tools_exposed=False,
                all_scoped_reads_complete=False,
                prompt_budget_tokens=(
                    self.context_max_prompt_tokens
                    or int(
                        getattr(
                            self.runtime.llm_client.settings,
                            "context_max_prompt_tokens",
                            4096,
                        )
                        or 4096
                    )
                ),
                response_call_count=1,
            )
            request = build_provider_round_llm_request(
                self.runtime.llm_client,
                plan=plan,
                tool_definitions=[],
                purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
                max_tokens=min(
                    request_limit,
                    self.max_tokens if self.max_tokens is not None else request_limit,
                ),
                timeout_seconds=45.0,
                transport_retries=0,
                reasoning_policy=self._reasoning_policy(),
                trace_info={"provider_mutation_finalization": True},
            )
            reserved = request.max_tokens or request_limit
            budget.consume_tool_event_completion(reserved)
            response = self.runtime.llm_client.complete(request)
            if not isinstance(response, LLMResponse):
                raise ProviderMutationFinalizationError(
                    "provider returned a non-LLMResponse value"
                )
            usage = provider_completion_tokens(response)
            if usage is not None:
                if usage > reserved:
                    raise ProviderMutationFinalizationError(
                        "provider completion usage exceeded request limit"
                    )
                budget.reconcile_tool_event_completion(
                    reserved=reserved,
                    actual=usage,
                )
            budget.observe_tool_event_outcome(provider_completion_outcome(response))
            transition = provider_final_response_transition(
                response,
                finalization_pending=True,
            )
            if transition.action is ProviderFinalResponseAction.COMPLETE:
                return ProviderMutationFinalizationResult(
                    success=bool(response.content.strip()),
                    response=response,
                    transition=transition,
                    messages=tuple(messages),
                    rounds_used=self.validation.rounds_used + 1,
                    error_message=None
                    if response.content.strip()
                    else "ProviderToolFinalizationEmpty",
                )
            return ProviderMutationFinalizationResult(
                success=False,
                response=response,
                transition=transition,
                messages=tuple(messages),
                rounds_used=self.validation.rounds_used + 1,
                error_message=(
                    transition.error_code.value
                    if transition.error_code is not None
                    else "ProviderToolFinalizationFailed"
                ),
            )
        except Exception as exc:
            return ProviderMutationFinalizationResult(
                success=False,
                response=None,
                transition=provider_final_response_transition(
                    LLMResponse(
                        content="",
                        model="",
                        provider="",
                        finish_reason="stop",
                    ),
                    finalization_pending=True,
                ),
                messages=tuple(self.validation.messages),
                rounds_used=self.validation.rounds_used + 1,
                error_message=str(exc)[:2000] or type(exc).__name__,
            )

    def _runtime_budget(self) -> RuntimeBudgetMetadata:
        state = getattr(
            getattr(self.runtime, "runtime_controller", None),
            "state",
            None,
        )
        budget = getattr(state, "budget", None)
        if not isinstance(budget, RuntimeBudgetMetadata):
            raise ProviderMutationFinalizationError(
                "finalization requires active runtime budget"
            )
        return budget

    def _reasoning_policy(self) -> Any:
        policy = getattr(self.owner, "_reasoning_policy_for_task", None)
        return policy(self.task) if callable(policy) else None


__all__ = [
    "ProviderMutationFinalizationError",
    "ProviderMutationFinalizationResult",
    "ProviderMutationFinalizationRunner",
]
