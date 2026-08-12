"""Compose one immutable provider round request plan from bounded policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.llm import LLMMessage
from core.provider_historical_tool_compaction import (
    ProviderHistoricalToolCompactionError,
    compact_provider_historical_tool_messages,
)
from core.provider_round_budget_policy import (
    ProviderRoundBudget,
    ProviderRoundBudgetError,
    provider_round_budget,
)
from core.provider_tool_surface_policy import (
    ProviderToolSurface,
    ProviderToolSurfaceError,
    provider_tool_surface,
)


class ProviderRoundRequestPlanError(ValueError):
    """Raised when a provider round request plan cannot be composed safely."""


@dataclass(frozen=True)
class ProviderRoundRequestPlan:
    """Immutable request inputs consumed by a later provider round runner."""

    messages: tuple[LLMMessage, ...]
    tool_names: tuple[str, ...]
    tool_choice: str | None
    max_calls: int
    tool_result_char_budget: int


def build_provider_round_request_plan(
    *,
    messages: Any,
    tool_names: Any,
    finalization_pending: bool,
    post_mutation_active: bool,
    mutation_tools_exposed: bool,
    all_scoped_reads_complete: bool,
    prompt_budget_tokens: int,
    response_call_count: int,
    remaining_prompt_tokens: int | None = None,
) -> ProviderRoundRequestPlan:
    """Compose existing bounded context, surface, and budget policies."""

    try:
        compacted = compact_provider_historical_tool_messages(messages)
        surface: ProviderToolSurface = provider_tool_surface(
            tool_names=tool_names,
            finalization_pending=finalization_pending,
            post_mutation_active=post_mutation_active,
            mutation_tools_exposed=mutation_tools_exposed,
            all_scoped_reads_complete=all_scoped_reads_complete,
        )
        budget: ProviderRoundBudget = provider_round_budget(
            prompt_budget_tokens=prompt_budget_tokens,
            response_call_count=response_call_count,
            remaining_prompt_tokens=remaining_prompt_tokens,
        )
    except (
        ProviderHistoricalToolCompactionError,
        ProviderToolSurfaceError,
        ProviderRoundBudgetError,
    ) as exc:
        raise ProviderRoundRequestPlanError(str(exc)) from exc
    return ProviderRoundRequestPlan(
        messages=tuple(message.model_copy(deep=True) for message in compacted),
        tool_names=surface.tool_names,
        tool_choice=surface.tool_choice,
        max_calls=budget.max_calls,
        tool_result_char_budget=budget.tool_result_char_budget,
    )


__all__ = [
    "ProviderRoundRequestPlan",
    "ProviderRoundRequestPlanError",
    "build_provider_round_request_plan",
]
