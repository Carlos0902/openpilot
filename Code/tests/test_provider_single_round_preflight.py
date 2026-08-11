from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_single_round_preflight import (
    ProviderSingleRoundPreflightError,
    validated_provider_single_round_inputs,
)
from core.provider_tool_admission import ProviderToolAdmission
from core.provider_tool_batch_admission import MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE
from core.provider_tool_duplicate_partition import ProviderToolDuplicateBlock
from metadata import ToolCallMetadata, ToolInputMetadata
from tools.tool_selection import SelectionReason, ToolSelection


def _call(call_id: str, tool_name: str = "file_reader") -> LLMToolCall:
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(
            name=tool_name,
            arguments='{"file_path":"README.md"}',
        ),
    )


def _response(*calls: LLMToolCall) -> LLMResponse:
    return LLMResponse(
        content="",
        reasoning_content="Use the admitted tool evidence.",
        tool_calls=list(calls),
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )


def _admission(
    provider_call_id: str,
    tool_name: str = "file_reader",
) -> ProviderToolAdmission:
    input_metadata = ToolInputMetadata(
        tool_name=tool_name,
        file_path="README.md",
    )
    project_call_id = f"project-{provider_call_id}"
    tool_call = ToolCallMetadata(
        session_id="session",
        task_id="task",
        step_id="step-1",
        call_id=project_call_id,
        provider_call_id=provider_call_id,
        tool_name=tool_name,
        input_metadata=input_metadata,
        round_index=1,
    )
    return ProviderToolAdmission(
        status="admitted",
        provider_call_id=provider_call_id,
        project_call_id=project_call_id,
        tool_call=tool_call,
        selection=ToolSelection(
            step_id="step-1",
            tool_name=tool_name,
            reason=SelectionReason.ONLY_OPTION,
            input_metadata=input_metadata,
        ),
    )


def test_preflight_returns_frozen_identity_consistent_inputs() -> None:
    call = _call("provider-reader")
    admission = _admission(call.id)
    ledger = ProviderCodeArtifactLedger()

    result = validated_provider_single_round_inputs(
        _response(call),
        [admission],
        round_index=1,
        allow_mutations=False,
        code_artifact_ledger=ledger,
    )

    assert result.response.tool_calls == [call]
    assert result.admissions == (admission,)
    assert result.duplicate_blocks == ()
    assert result.declared_window_complete_ids == ()
    assert result.code_artifact_ledger is ledger
    with pytest.raises(FrozenInstanceError):
        result.round_index = 2


def test_preflight_snapshots_mutable_response_and_admissions() -> None:
    call = _call("provider-reader")
    response = _response(call)
    admission = _admission(call.id)

    result = validated_provider_single_round_inputs(
        response,
        [admission],
        round_index=1,
        allow_mutations=False,
    )
    response.tool_calls.clear()
    assert admission.selection is not None
    admission.selection.input_metadata.file_path = "changed.py"

    assert [item.id for item in result.response.tool_calls] == [call.id]
    assert result.admissions[0].selection is not None
    assert result.admissions[0].selection.input_metadata.file_path == "README.md"
    assert result.response is not response
    assert result.admissions[0] is not admission


def test_preflight_rejects_response_admission_mismatch() -> None:
    with pytest.raises(
        ProviderSingleRoundPreflightError,
        match="response call",
    ):
        validated_provider_single_round_inputs(
            _response(_call("provider-response")),
            [_admission("provider-admission")],
            round_index=1,
            allow_mutations=False,
        )


def test_preflight_rejects_invalid_duplicate_before_execution() -> None:
    call = _call("provider-reader")
    block = ProviderToolDuplicateBlock(
        provider_call_id="not-in-response",
        previous_provider_call_id="provider-old",
        tool_name="file_reader",
        signature="a" * 64,
        round_index=1,
    )

    with pytest.raises(
        ProviderSingleRoundPreflightError,
        match="duplicate block",
    ):
        validated_provider_single_round_inputs(
            _response(call),
            [_admission(call.id)],
            round_index=1,
            allow_mutations=False,
            duplicate_blocks=[block],
        )


def test_preflight_rejects_admitted_and_duplicate_same_call() -> None:
    call = _call("provider-reader")
    block = ProviderToolDuplicateBlock(
        provider_call_id=call.id,
        previous_provider_call_id="provider-old",
        tool_name="file_reader",
        signature="a" * 64,
        round_index=1,
    )

    with pytest.raises(
        ProviderSingleRoundPreflightError,
        match="admitted and duplicate-blocked",
    ):
        validated_provider_single_round_inputs(
            _response(call),
            [_admission(call.id)],
            round_index=1,
            allow_mutations=False,
            duplicate_blocks=[block],
        )


@pytest.mark.parametrize(
    ("response", "admissions", "allow_mutations", "error"),
    [
        (_response(), [], False, "at least one"),
        (object(), [], False, "LLMResponse"),
        (
            _response(_call("provider-reader")),
            (_admission("provider-reader"),),
            False,
            "bounded list",
        ),
        (
            _response(_call("provider-reader")),
            [_admission("provider-reader")],
            "yes",
            "literal boolean",
        ),
    ],
)
def test_preflight_rejects_invalid_entry_shapes(
    response,
    admissions,
    allow_mutations,
    error: str,
) -> None:
    with pytest.raises(ProviderSingleRoundPreflightError, match=error):
        validated_provider_single_round_inputs(
            response,
            admissions,
            round_index=1,
            allow_mutations=allow_mutations,
        )


def test_preflight_rejects_duplicate_or_oversized_response_calls() -> None:
    duplicate = _call("provider-duplicate")
    with pytest.raises(ProviderSingleRoundPreflightError, match="unique"):
        validated_provider_single_round_inputs(
            _response(duplicate, duplicate),
            [],
            round_index=1,
            allow_mutations=False,
        )

    calls = [
        _call(f"provider-{index}")
        for index in range(MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE + 1)
    ]
    with pytest.raises(ProviderSingleRoundPreflightError, match="call limit"):
        validated_provider_single_round_inputs(
            _response(*calls),
            [],
            round_index=1,
            allow_mutations=False,
        )


def test_preflight_validates_declared_windows_budget_and_ledger() -> None:
    call = _call("provider-reader")
    response = _response(call)
    admission = _admission(call.id)

    with pytest.raises(ProviderSingleRoundPreflightError, match="declared window"):
        validated_provider_single_round_inputs(
            response,
            [admission],
            round_index=1,
            allow_mutations=False,
            declared_window_complete_ids=["missing-call"],
        )
    with pytest.raises(ProviderSingleRoundPreflightError, match="char_budget"):
        validated_provider_single_round_inputs(
            response,
            [admission],
            round_index=1,
            allow_mutations=False,
            char_budget=1,
        )
    with pytest.raises(ProviderSingleRoundPreflightError, match="artifact_ledger"):
        validated_provider_single_round_inputs(
            response,
            [admission],
            round_index=1,
            allow_mutations=False,
            code_artifact_ledger=object(),
        )
