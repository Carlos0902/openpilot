"""Preflight static inputs for one provider tool execution round."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.llm import LLMResponse
from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_tool_admission import ProviderToolAdmission
from core.provider_tool_batch_admission import MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE
from core.provider_tool_duplicate_partition import ProviderToolDuplicateBlock
from core.provider_tool_result_payload import (
    MAX_PROVIDER_TOOL_RESULT_CHARS,
    MIN_PROVIDER_TOOL_RESULT_CHARS,
)


class ProviderSingleRoundPreflightError(ValueError):
    """Raised before execution when round inputs are inconsistent or unbounded."""


@dataclass(frozen=True)
class ProviderSingleRoundInputs:
    """Validated immutable inputs for one later provider execution round."""

    response: LLMResponse
    admissions: tuple[ProviderToolAdmission, ...]
    round_index: int
    allow_mutations: bool
    duplicate_blocks: tuple[ProviderToolDuplicateBlock, ...]
    declared_window_complete_ids: tuple[str, ...]
    char_budget: int
    code_artifact_ledger: ProviderCodeArtifactLedger | None


def validated_provider_single_round_inputs(
    response: Any,
    admissions: Any,
    *,
    round_index: int,
    allow_mutations: bool,
    duplicate_blocks: Any = (),
    declared_window_complete_ids: Any = (),
    char_budget: int = MAX_PROVIDER_TOOL_RESULT_CHARS,
    code_artifact_ledger: ProviderCodeArtifactLedger | None = None,
) -> ProviderSingleRoundInputs:
    """Return one immutable identity-consistent round input bundle."""

    if not isinstance(response, LLMResponse):
        _fail("response must be LLMResponse")
    calls = list(response.tool_calls)
    if not calls:
        _fail("provider tool round requires at least one response call")
    if len(calls) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        _fail("provider response exceeds the static call limit")
    call_by_id = {call.id: call for call in calls}
    if len(call_by_id) != len(calls):
        _fail("provider response call IDs must be unique")
    if type(round_index) is not int or round_index < 1:
        _fail("round_index must be a positive integer")
    if type(allow_mutations) is not bool:
        _fail("allow_mutations must be a literal boolean")

    if not isinstance(admissions, list):
        _fail("admissions must be a bounded list")
    if len(admissions) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        _fail("admissions exceed the static call limit")
    if any(not isinstance(item, ProviderToolAdmission) for item in admissions):
        _fail("admissions must contain ProviderToolAdmission values")
    admission_ids = [item.provider_call_id for item in admissions]
    if len(admission_ids) != len(set(admission_ids)):
        _fail("admission provider call IDs must be unique")
    for item in admissions:
        call = call_by_id.get(item.provider_call_id)
        if call is None:
            _fail("admission must reference a response call")
        if call.function.name != item.tool_call.tool_name:
            _fail("admission tool must match its response call")
        if item.tool_call.round_index != round_index:
            _fail("admission must match the current round")

    if not isinstance(duplicate_blocks, (list, tuple)):
        _fail("duplicate_blocks must be a bounded list or tuple")
    if len(duplicate_blocks) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        _fail("duplicate_blocks exceed the static call limit")
    if any(
        not isinstance(item, ProviderToolDuplicateBlock)
        for item in duplicate_blocks
    ):
        _fail("duplicate_blocks must contain ProviderToolDuplicateBlock values")
    block_ids = [item.provider_call_id for item in duplicate_blocks]
    if len(block_ids) != len(set(block_ids)):
        _fail("duplicate block provider call IDs must be unique")
    if set(block_ids).intersection(admission_ids):
        _fail("response calls cannot be admitted and duplicate-blocked")
    for block in duplicate_blocks:
        call = call_by_id.get(block.provider_call_id)
        if call is None:
            _fail("duplicate block must reference a response call")
        if block.round_index != round_index:
            _fail("duplicate block must match the current round")
        if block.tool_name != call.function.name:
            _fail("duplicate block tool must match its response call")

    if not isinstance(declared_window_complete_ids, (list, tuple)):
        _fail("declared_window_complete_ids must be a bounded list or tuple")
    if len(declared_window_complete_ids) > MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE:
        _fail("declared window IDs exceed the static call limit")
    if any(
        not isinstance(call_id, str) or not call_id.strip()
        for call_id in declared_window_complete_ids
    ):
        _fail("declared window IDs must be non-empty strings")
    declared_ids = tuple(declared_window_complete_ids)
    if len(declared_ids) != len(set(declared_ids)):
        _fail("declared window IDs must be unique")
    if not set(declared_ids).issubset(call_by_id):
        _fail("declared window IDs must reference response calls")
    if (
        type(char_budget) is not int
        or char_budget < MIN_PROVIDER_TOOL_RESULT_CHARS
        or char_budget > MAX_PROVIDER_TOOL_RESULT_CHARS
    ):
        _fail("char_budget must be within provider result bounds")
    if code_artifact_ledger is not None and not isinstance(
        code_artifact_ledger,
        ProviderCodeArtifactLedger,
    ):
        _fail("code_artifact_ledger must be ProviderCodeArtifactLedger")
    return ProviderSingleRoundInputs(
        response=response.model_copy(deep=True),
        admissions=tuple(item.model_copy(deep=True) for item in admissions),
        round_index=round_index,
        allow_mutations=allow_mutations,
        duplicate_blocks=tuple(duplicate_blocks),
        declared_window_complete_ids=declared_ids,
        char_budget=char_budget,
        code_artifact_ledger=code_artifact_ledger,
    )


def _fail(message: str) -> None:
    raise ProviderSingleRoundPreflightError(message)


__all__ = [
    "ProviderSingleRoundInputs",
    "ProviderSingleRoundPreflightError",
    "validated_provider_single_round_inputs",
]
