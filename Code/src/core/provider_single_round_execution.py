"""Execute one preflighted provider round and compose bounded wire evidence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.llm import LLMMessage, LLMToolResult
from core.provider_execution_dispatch import dispatch_provider_execution_batch
from core.provider_single_round_preflight import ProviderSingleRoundInputs
from core.provider_tool_result_batch import provider_tool_result_batch
from core.provider_tool_wire_exchange import provider_tool_wire_exchange
from core.tool_event_loop import ToolEventLoopRunResult


class ProviderSingleRoundStage(str, Enum):
    """Failure boundary for one preflighted provider tool round."""

    PREFLIGHT = "preflight"
    EXECUTION = "execution"
    RESULT_PROJECTION = "result_projection"
    WIRE_COMPOSITION = "wire_composition"


class ProviderSingleRoundExecutionError(ValueError):
    """Stage-typed error with retained completed evidence when available."""

    def __init__(
        self,
        stage: ProviderSingleRoundStage,
        message: str,
        *,
        loop_result: ToolEventLoopRunResult | None = None,
        tool_results: tuple[LLMToolResult, ...] | None = None,
    ) -> None:
        self.stage = stage
        self.loop_result = loop_result
        self.tool_results = tool_results
        super().__init__(message)


@dataclass(frozen=True)
class ProviderExecutedToolRound:
    """Complete bounded artifacts from one provider tool-call response."""

    loop_result: ToolEventLoopRunResult
    tool_results: tuple[LLMToolResult, ...]
    wire_messages: tuple[LLMMessage, ...]


def execute_preflighted_provider_tool_round(
    runner: Any,
    task: Any,
    prepared: ProviderSingleRoundInputs,
    *,
    authorized_post_processing_write_scope: Sequence[str] | None = None,
) -> ProviderExecutedToolRound:
    """Dispatch, project, and wire one immutable preflighted round."""

    if not isinstance(prepared, ProviderSingleRoundInputs):
        raise ProviderSingleRoundExecutionError(
            ProviderSingleRoundStage.PREFLIGHT,
            "provider round requires preflighted inputs",
        )
    try:
        loop_result = dispatch_provider_execution_batch(
            runner,
            task,
            list(prepared.admissions),
            round_index=prepared.round_index,
            allow_mutations=prepared.allow_mutations,
            code_artifact_ledger=prepared.code_artifact_ledger,
            authorized_post_processing_write_scope=(
                authorized_post_processing_write_scope
            ),
        )
        if not isinstance(loop_result, ToolEventLoopRunResult):
            raise TypeError("execution bridge must return ToolEventLoopRunResult")
    except Exception as exc:
        raise ProviderSingleRoundExecutionError(
            ProviderSingleRoundStage.EXECUTION,
            "provider round execution failed",
        ) from exc

    try:
        tool_results = provider_tool_result_batch(
            prepared.response,
            loop_result,
            duplicate_blocks=prepared.duplicate_blocks,
            declared_window_complete_ids=(
                prepared.declared_window_complete_ids
            ),
            char_budget=prepared.char_budget,
            code_artifact_ledger=prepared.code_artifact_ledger,
        )
    except Exception as exc:
        raise ProviderSingleRoundExecutionError(
            ProviderSingleRoundStage.RESULT_PROJECTION,
            "provider round result projection failed",
            loop_result=loop_result,
        ) from exc

    try:
        wire_messages = provider_tool_wire_exchange(
            prepared.response,
            tool_results,
        )
    except Exception as exc:
        raise ProviderSingleRoundExecutionError(
            ProviderSingleRoundStage.WIRE_COMPOSITION,
            "provider round wire composition failed",
            loop_result=loop_result,
            tool_results=tool_results,
        ) from exc
    return ProviderExecutedToolRound(
        loop_result=loop_result,
        tool_results=tool_results,
        wire_messages=wire_messages,
    )


__all__ = [
    "ProviderExecutedToolRound",
    "ProviderSingleRoundExecutionError",
    "ProviderSingleRoundStage",
    "execute_preflighted_provider_tool_round",
]
