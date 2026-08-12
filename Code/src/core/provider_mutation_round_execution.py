"""Execute one admitted provider mutation round and project its receipt."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_mutation_receipt import provider_mutation_receipt
from core.provider_single_round_execution import (
    ProviderExecutedToolRound,
    ProviderSingleRoundExecutionError,
)
from core.provider_single_round_execution import execute_preflighted_provider_tool_round
from core.provider_single_round_preflight import validated_provider_single_round_inputs
from core.provider_tool_admission import ProviderToolAdmission
from core.provider_tool_batch_admission import MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE
from core.provider_tool_duplicate_partition import ProviderToolDuplicateBlock
from core.provider_tool_result_payload import (
    MAX_PROVIDER_TOOL_RESULT_CHARS,
)
from core.llm import LLMResponse, LLMToolResult


class ProviderMutationRoundStage(str, Enum):
    """Failure boundary for one admitted mutation round."""

    INPUT = "input"
    EXECUTION = "execution"
    RECEIPT = "receipt"


class ProviderMutationRoundExecutionError(ValueError):
    """Raised when a mutation round cannot produce bounded evidence."""

    def __init__(
        self,
        stage: ProviderMutationRoundStage,
        message: str,
        *,
        executed: ProviderExecutedToolRound | None = None,
    ) -> None:
        self.stage = stage
        self.executed = executed
        super().__init__(message)


@dataclass(frozen=True)
class ProviderMutationRoundResult:
    """Bounded execution, wire, and mutation-receipt evidence."""

    executed: ProviderExecutedToolRound
    receipt: dict[str, Any]

    @property
    def loop_result(self) -> Any:
        return self.executed.loop_result

    @property
    def tool_results(self) -> tuple[LLMToolResult, ...]:
        return self.executed.tool_results

    @property
    def wire_messages(self):
        return self.executed.wire_messages


def execute_provider_mutation_round(
    runner: Any,
    task: Any,
    response: LLMResponse,
    admissions: Sequence[ProviderToolAdmission],
    *,
    round_index: int,
    validation_command: str,
    authorized_post_processing_write_scope: Sequence[str],
    duplicate_blocks: Sequence[ProviderToolDuplicateBlock] = (),
    declared_window_complete_ids: Sequence[str] = (),
    char_budget: int = MAX_PROVIDER_TOOL_RESULT_CHARS,
    code_artifact_ledger: ProviderCodeArtifactLedger | None = None,
) -> ProviderMutationRoundResult:
    """Run one preflighted patch mutation and return a bounded receipt."""

    _validate_entry(
        response=response,
        admissions=admissions,
        round_index=round_index,
        validation_command=validation_command,
        authorized_post_processing_write_scope=(
            authorized_post_processing_write_scope
        ),
        duplicate_blocks=duplicate_blocks,
        declared_window_complete_ids=declared_window_complete_ids,
    )
    try:
        prepared = validated_provider_single_round_inputs(
            response,
            list(admissions),
            round_index=round_index,
            allow_mutations=True,
            duplicate_blocks=list(duplicate_blocks),
            declared_window_complete_ids=list(declared_window_complete_ids),
            char_budget=char_budget,
            code_artifact_ledger=code_artifact_ledger,
        )
    except Exception as exc:
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "provider mutation round input preflight failed",
        ) from exc

    try:
        executed = execute_preflighted_provider_tool_round(
            runner,
            task,
            prepared,
            authorized_post_processing_write_scope=(
                authorized_post_processing_write_scope
            ),
        )
    except ProviderSingleRoundExecutionError as exc:
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.EXECUTION,
            "provider mutation round execution failed",
        ) from exc

    try:
        receipt = provider_mutation_receipt(
            executed.loop_result,
            validation_command=validation_command,
        )
    except Exception as exc:
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.RECEIPT,
            "provider mutation receipt projection failed",
            executed=executed,
        ) from exc
    if receipt is None:
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.RECEIPT,
            "provider mutation round produced no successful mutation receipt",
            executed=executed,
        )
    return ProviderMutationRoundResult(executed=executed, receipt=receipt)


def _validate_entry(
    *,
    response: LLMResponse,
    admissions: Sequence[ProviderToolAdmission],
    round_index: int,
    validation_command: str,
    authorized_post_processing_write_scope: Sequence[str],
    duplicate_blocks: Sequence[ProviderToolDuplicateBlock],
    declared_window_complete_ids: Sequence[str],
) -> None:
    if not isinstance(response, LLMResponse):
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "response must be LLMResponse",
        )
    if not isinstance(admissions, Sequence) or isinstance(admissions, (str, bytes)):
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "admissions must be a bounded sequence",
        )
    if len(admissions) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "admissions exceed the provider call limit",
        )
    if not isinstance(validation_command, str) or not validation_command.strip():
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "mutation round requires an exact validation command",
        )
    if not isinstance(authorized_post_processing_write_scope, Sequence) or isinstance(
        authorized_post_processing_write_scope,
        (str, bytes),
    ):
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "mutation round requires an explicit post-processing write scope",
        )
    if not authorized_post_processing_write_scope:
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "mutation round requires a non-empty post-processing write scope",
        )
    if any(
        not isinstance(path, str) or not path.strip()
        for path in authorized_post_processing_write_scope
    ):
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "post-processing write scope paths must be non-empty strings",
        )
    if any(
        not isinstance(item, ProviderToolAdmission)
        or item.status != "admitted"
        or item.tool_call.tool_name != "file_patch_writer"
        for item in admissions
    ):
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "mutation round requires only admitted file_patch_writer calls",
        )
    if duplicate_blocks or declared_window_complete_ids:
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "mutation round cannot include duplicate or read-window results",
        )
    if type(round_index) is not int or round_index < 1:
        raise ProviderMutationRoundExecutionError(
            ProviderMutationRoundStage.INPUT,
            "round_index must be a positive integer",
        )


__all__ = [
    "ProviderMutationRoundExecutionError",
    "ProviderMutationRoundResult",
    "ProviderMutationRoundStage",
    "execute_provider_mutation_round",
]
