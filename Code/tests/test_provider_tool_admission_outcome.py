from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.provider_tool_admission import ProviderToolAdmission, provider_tool_error
from metadata import ToolCallMetadata, ToolInputMetadata
from tools.tool_selection import SelectionReason, ToolSelection


def _call() -> ToolCallMetadata:
    return ToolCallMetadata(
        session_id="session",
        task_id="task",
        step_id="step_1_1",
        call_id="task:r1:c1",
        provider_call_id="provider-call-1",
        tool_name="file_reader",
        input_metadata=ToolInputMetadata(
            tool_name="file_reader",
            file_path="README.md",
        ),
        round_index=1,
    )


def _selection(call: ToolCallMetadata) -> ToolSelection:
    return ToolSelection(
        step_id=call.step_id,
        tool_name=call.tool_name,
        reason=SelectionReason.CAPABILITY_MATCH,
        confidence=1.0,
        input_metadata=call.input_metadata,
    )


def test_admitted_outcome_requires_selection_and_preserves_both_identities() -> None:
    call = _call()

    admission = ProviderToolAdmission(
        status="admitted",
        provider_call_id="provider-call-1",
        project_call_id="task:r1:c1",
        tool_call=call,
        selection=_selection(call),
    )

    assert admission.tool_error is None
    assert admission.provider_call_id == call.provider_call_id
    assert admission.project_call_id == call.call_id


def test_blocked_outcome_uses_existing_typed_error_lineage() -> None:
    call = _call()
    error = provider_tool_error(
        call,
        error_type="InvalidToolArguments",
        error_message="arguments must be a JSON object",
        recoverable=True,
        suggested_recovery="Return one JSON object.",
    )

    admission = ProviderToolAdmission(
        status="blocked",
        provider_call_id="provider-call-1",
        project_call_id="task:r1:c1",
        tool_call=call,
        tool_error=error,
    )

    assert admission.selection is None
    assert error.provider_call_id == call.provider_call_id
    assert error.failure is not None
    assert error.failure.retry_recommended is True
    assert error.failure.details == {
        "tool_name": "file_reader",
        "call_id": "task:r1:c1",
        "provider_call_id": "provider-call-1",
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "admitted", "selection": None},
        {"status": "blocked", "tool_error": None},
        {
            "status": "admitted",
            "tool_error": provider_tool_error(
                _call(),
                error_type="Failure",
                error_message="failed",
                recoverable=False,
            ),
        },
    ],
)
def test_admission_rejects_contradictory_status_payloads(updates) -> None:
    call = _call()
    payload = {
        "status": "admitted",
        "provider_call_id": "provider-call-1",
        "project_call_id": "task:r1:c1",
        "tool_call": call,
        "selection": _selection(call),
    }
    payload.update(updates)

    with pytest.raises(ValidationError):
        ProviderToolAdmission(**payload)


@pytest.mark.parametrize(
    ("provider_call_id", "project_call_id"),
    [("wrong", "task:r1:c1"), ("provider-call-1", "wrong")],
)
def test_admission_rejects_identity_drift(provider_call_id, project_call_id) -> None:
    call = _call()

    with pytest.raises(ValidationError):
        ProviderToolAdmission(
            status="admitted",
            provider_call_id=provider_call_id,
            project_call_id=project_call_id,
            tool_call=call,
            selection=_selection(call),
        )
