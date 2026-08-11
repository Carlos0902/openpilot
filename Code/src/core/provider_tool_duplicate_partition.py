"""Partition provider calls against bounded cross-round attempt history."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.llm import LLMToolCall
from core.provider_tool_attempt_ledger import ProviderToolAttemptLedger
from core.provider_tool_batch_admission import (
    MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
)
from core.provider_tool_call_signature import (
    ProviderToolSignatureError,
    provider_tool_call_signature,
)
from core.provider_tool_roundtrip_contracts import (
    MAX_PROVIDER_ROUND_TRIP_ROUNDS,
)


class ProviderToolDuplicatePartitionError(ValueError):
    """Raised when duplicate partitioning cannot remain bounded and atomic."""


class ProviderToolDuplicateBlock(BaseModel):
    """One provider call preblocked by an earlier normalized attempt."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    provider_call_id: str = Field(min_length=1, max_length=256)
    previous_provider_call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=128)
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    round_index: int = Field(ge=1, le=MAX_PROVIDER_ROUND_TRIP_ROUNDS)
    error_type: Literal["ProviderToolDuplicateAttempt"] = (
        "ProviderToolDuplicateAttempt"
    )
    error_message: Literal[
        "The same normalized tool input was already attempted."
    ] = "The same normalized tool input was already attempted."
    suggested_recovery: Literal[
        "Choose a new evidence-backed path or finish with existing evidence."
    ] = (
        "Choose a new evidence-backed path or finish with existing evidence."
    )

    @model_validator(mode="after")
    def _provider_ids_are_distinct(self) -> "ProviderToolDuplicateBlock":
        if self.provider_call_id == self.previous_provider_call_id:
            raise ValueError("duplicate block must reference an earlier call ID")
        return self


class ProviderToolCallPartition(BaseModel):
    """Bounded response partition with no tool execution side effects."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    total_calls: int = Field(
        ge=0,
        le=MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
    )
    new_calls: tuple[LLMToolCall, ...] = Field(
        default=(),
        max_length=MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
    )
    duplicate_blocks: tuple[ProviderToolDuplicateBlock, ...] = Field(
        default=(),
        max_length=MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
    )

    @model_validator(mode="after")
    def _partition_is_complete_and_disjoint(self) -> "ProviderToolCallPartition":
        if self.total_calls != len(self.new_calls) + len(self.duplicate_blocks):
            raise ValueError("partition counts must cover every input call")
        new_ids = [call.id for call in self.new_calls]
        duplicate_ids = [item.provider_call_id for item in self.duplicate_blocks]
        all_ids = [*new_ids, *duplicate_ids]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("partition provider call IDs must be unique")
        return self


def partition_provider_tool_calls(
    calls: Sequence[LLMToolCall],
    *,
    ledger: ProviderToolAttemptLedger,
    round_index: int,
    project_path: str | None = None,
) -> ProviderToolCallPartition:
    """Separate unseen calls from signatures attempted in earlier rounds."""

    if not isinstance(ledger, ProviderToolAttemptLedger):
        raise ProviderToolDuplicatePartitionError(
            "ledger must be ProviderToolAttemptLedger"
        )
    if (
        type(round_index) is not int
        or round_index < 1
        or round_index > MAX_PROVIDER_ROUND_TRIP_ROUNDS
    ):
        raise ProviderToolDuplicatePartitionError(
            "round_index must be a positive integer within the static limit"
        )
    if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
        raise ProviderToolDuplicatePartitionError(
            "calls must be a bounded sequence"
        )
    if len(calls) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        raise ProviderToolDuplicatePartitionError(
            "provider response exceeds the static tool-call limit"
        )
    bounded_calls = tuple(calls)
    if any(not isinstance(call, LLMToolCall) for call in bounded_calls):
        raise ProviderToolDuplicatePartitionError(
            "calls must contain only LLMToolCall values"
        )
    if project_path is not None and (
        not isinstance(project_path, str) or not project_path.strip()
    ):
        raise ProviderToolDuplicatePartitionError(
            "project_path must be a non-empty string when provided"
        )
    provider_ids = [call.id for call in bounded_calls]
    if len(provider_ids) != len(set(provider_ids)):
        raise ProviderToolDuplicatePartitionError(
            "provider call IDs must be unique within one response"
        )
    if any(ledger.provider_call_seen(call_id) for call_id in provider_ids):
        raise ProviderToolDuplicatePartitionError(
            "provider call IDs must be unique across one round trip"
        )

    signatures: list[str] = []
    try:
        for call in bounded_calls:
            signatures.append(
                provider_tool_call_signature(
                    call,
                    project_path=project_path,
                )
            )
    except ProviderToolSignatureError as exc:
        raise ProviderToolDuplicatePartitionError(str(exc)) from exc

    duplicate_count = sum(
        ledger.previous_for_signature(signature) is not None
        for signature in signatures
    )
    if duplicate_count > ledger.remaining:
        raise ProviderToolDuplicatePartitionError(
            "duplicate partition exceeds the remaining attempt ledger capacity"
        )

    new_calls: list[LLMToolCall] = []
    duplicate_blocks: list[ProviderToolDuplicateBlock] = []
    for call, signature in zip(bounded_calls, signatures):
        previous = ledger.previous_for_signature(signature)
        if previous is None:
            new_calls.append(call)
            continue
        ledger.record_duplicate(
            signature=signature,
            tool_name=call.function.name,
            provider_call_id=call.id,
            round_index=round_index,
            error_type="ProviderToolDuplicateAttempt",
        )
        duplicate_blocks.append(
            ProviderToolDuplicateBlock(
                provider_call_id=call.id,
                previous_provider_call_id=previous.provider_call_id,
                tool_name=call.function.name,
                signature=signature,
                round_index=round_index,
            )
        )
    return ProviderToolCallPartition(
        total_calls=len(bounded_calls),
        new_calls=tuple(new_calls),
        duplicate_blocks=tuple(duplicate_blocks),
    )


__all__ = [
    "ProviderToolCallPartition",
    "ProviderToolDuplicateBlock",
    "ProviderToolDuplicatePartitionError",
    "partition_provider_tool_calls",
]
