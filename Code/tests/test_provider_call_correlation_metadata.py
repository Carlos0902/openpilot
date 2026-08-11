from __future__ import annotations

import pytest
from pydantic import ValidationError

from metadata import ToolCallMetadata, ToolErrorMetadata, ToolInputMetadata


def _tool_call(**updates) -> ToolCallMetadata:
    values = {
        "session_id": "session",
        "task_id": "task",
        "step_id": "step_1",
        "call_id": "task:r1:c1",
        "provider_call_id": "provider-call-1",
        "tool_name": "file_reader",
        "input_metadata": ToolInputMetadata(
            tool_name="file_reader",
            file_path="README.md",
        ),
    }
    values.update(updates)
    return ToolCallMetadata(**values)


def test_tool_call_preserves_separate_provider_correlation_identity() -> None:
    call = _tool_call()

    restored = ToolCallMetadata.model_validate(call.to_json_dict())

    assert restored.call_id == "task:r1:c1"
    assert restored.provider_call_id == "provider-call-1"


def test_tool_error_preserves_provider_correlation_identity() -> None:
    call = _tool_call()
    error = ToolErrorMetadata(
        session_id=call.session_id,
        task_id=call.task_id,
        step_id=call.step_id,
        call_id=call.call_id,
        provider_call_id=call.provider_call_id,
        tool_name=call.tool_name,
        error_type="InvalidToolArguments",
        error_message="arguments must be an object",
        input_metadata=call.input_metadata,
    )

    restored = ToolErrorMetadata.model_validate(error.to_json_dict())

    assert restored.call_id == call.call_id
    assert restored.provider_call_id == call.provider_call_id


@pytest.mark.parametrize("model", [ToolCallMetadata, ToolErrorMetadata])
def test_provider_call_identity_rejects_empty_string(model) -> None:
    payload = _tool_call().to_json_dict()
    payload["provider_call_id"] = ""
    if model is ToolErrorMetadata:
        payload.update(error_type="ProviderFailure", error_message="failed")
        payload.pop("status", None)
        payload.pop("reason", None)

    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_historical_tool_call_without_provider_identity_still_loads() -> None:
    payload = _tool_call().to_json_dict()
    payload.pop("provider_call_id")

    restored = ToolCallMetadata.model_validate(payload)

    assert restored.provider_call_id is None
