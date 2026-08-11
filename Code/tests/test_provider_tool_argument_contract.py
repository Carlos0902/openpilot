from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_tool_admission import (
    decode_provider_tool_arguments,
    provider_tool_contract_error,
)
from metadata import ToolContractMetadata, ToolInputMetadata


def _call(arguments: str) -> LLMToolCall:
    return LLMToolCall(
        id="provider-call-1",
        function=LLMToolFunctionCall(
            name="file_patch_writer",
            arguments=arguments,
        ),
    )


@pytest.mark.parametrize(
    ("arguments", "decoded", "error"),
    [
        ("", {}, None),
        ('{"file_path":"app.py"}', {"file_path": "app.py"}, None),
        ("[]", {}, "must be a JSON object"),
        ("{bad", {}, "not valid JSON"),
    ],
)
def test_provider_arguments_decode_only_json_objects(arguments, decoded, error) -> None:
    actual, actual_error = decode_provider_tool_arguments(_call(arguments))

    assert actual == decoded
    if error is None:
        assert actual_error is None
    else:
        assert error in str(actual_error)


def test_provider_arguments_reject_oversized_json_before_parsing() -> None:
    decoded, error = decode_provider_tool_arguments(_call("{" + "x" * 200_000))

    assert decoded == {}
    assert "exceed 200000 characters" in str(error)


def _definition() -> SimpleNamespace:
    return SimpleNamespace(
        contract_metadata=ToolContractMetadata(
            tool_name="file_patch_writer",
            input_metadata_type="ToolInputMetadata",
            output_metadata_type="ToolResultMetadata",
            required_input_fields=["file_path"],
            required_any_of=[["replacement_text"], ["patch"]],
            conditional_requirements=[
                {
                    "when": {"operation_kind": "modify_symbol"},
                    "required": ["symbol_name"],
                }
            ],
        )
    )


def _input(**updates) -> ToolInputMetadata:
    values = {
        "tool_name": "file_patch_writer",
        "file_path": "app.py",
        "replacement_text": "return 1",
    }
    values.update(updates)
    return ToolInputMetadata(**values)


def test_contract_validation_accepts_required_and_alternative_fields() -> None:
    assert provider_tool_contract_error(_definition(), _input()) is None
    assert (
        provider_tool_contract_error(
            _definition(),
            _input(replacement_text=None, patch={"replacement": "return 2"}),
        )
        is None
    )


@pytest.mark.parametrize(
    ("input_metadata", "message"),
    [
        (_input(file_path=None), "Missing required input field"),
        (_input(replacement_text=None), "provide replacement_text or patch"),
        (
            _input(operation_kind="MODIFY_SYMBOL", symbol_name=None),
            "when operation_kind=modify_symbol",
        ),
    ],
)
def test_contract_validation_reports_exact_missing_boundary(input_metadata, message) -> None:
    assert message in str(provider_tool_contract_error(_definition(), input_metadata))


def test_missing_contract_does_not_invent_requirements() -> None:
    assert provider_tool_contract_error(SimpleNamespace(contract_metadata=None), _input()) is None


def test_contract_validation_rejects_unbounded_declared_fields() -> None:
    definition = _definition()
    definition.contract_metadata.required_input_fields = [
        f"field_{index}" for index in range(65)
    ]

    assert "at most 64" in str(provider_tool_contract_error(definition, _input()))
