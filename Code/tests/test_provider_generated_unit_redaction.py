from __future__ import annotations

import hashlib
import json

import pytest

from core.provider_generated_unit_redaction import (
    ProviderGeneratedUnitRedactionError,
    redact_provider_generated_units,
)
from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_TOOL_ATTEMPTS
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import (
    ProviderCodeArtifactReference,
    ToolCallMetadata,
    ToolErrorMetadata,
    ToolEventMetadata,
    ToolInputMetadata,
    ToolLoopMetadata,
)
from metadata.artifacts import MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS


_GENERATED = "def generated():\n    return True\n"
_DIGEST = hashlib.sha256(_GENERATED.encode("utf-8")).hexdigest()


def _input(generated_unit: str | None = _GENERATED) -> ToolInputMetadata:
    return ToolInputMetadata(
        tool_name="file_patch_writer",
        file_path="snake_game.py",
        generated_unit=generated_unit,
        artifact_ref=ProviderCodeArtifactReference(
            kind="code_artifact",
            source_id="project-generator",
            provider_call_id="provider-generator",
            sha256=_DIGEST,
            bytes=len(_GENERATED.encode("utf-8")),
            chars=len(_GENERATED),
            language="python",
        ),
        runtime_handles={"_existing": "preserved"},
    )


def _call(call_id: str, input_metadata: ToolInputMetadata) -> ToolCallMetadata:
    return ToolCallMetadata(
        session_id="session",
        task_id="task",
        step_id="step",
        call_id=call_id,
        provider_call_id=f"provider-{call_id}",
        tool_name="file_patch_writer",
        input_metadata=input_metadata,
    )


def _error(call_id: str, input_metadata: ToolInputMetadata) -> ToolErrorMetadata:
    return ToolErrorMetadata(
        session_id="session",
        task_id="task",
        step_id="step",
        call_id=call_id,
        provider_call_id=f"provider-{call_id}",
        tool_name="file_patch_writer",
        error_type="TestError",
        error_message="test failure",
        input_metadata=input_metadata,
    )


def _event(
    call_id: str,
    input_metadata: ToolInputMetadata,
    *,
    tool_call: ToolCallMetadata | None = None,
    tool_error: ToolErrorMetadata | None = None,
) -> ToolEventMetadata:
    return ToolEventMetadata(
        session_id="session",
        task_id="task",
        step_id="step",
        call_id=call_id,
        tool_name="file_patch_writer",
        event_type="completed",
        status="completed",
        input_metadata=input_metadata,
        tool_call=tool_call,
        tool_error=tool_error,
    )


def _loop_result(
    *,
    tool_results: list[dict] | None = None,
    events: list[ToolEventMetadata] | None = None,
    invocations: list[ToolCallMetadata] | None = None,
    errors: list[ToolErrorMetadata] | None = None,
) -> ToolEventLoopRunResult:
    return ToolEventLoopRunResult(
        success=True,
        tool_results=tool_results or [],
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session",
            task_id="task",
            status="completed",
            success=True,
            events=events or [],
            tool_invocations=invocations or [],
            recoverable_errors=errors or [],
        ),
    )


def test_redacts_result_map_and_retains_bounded_diagnostics() -> None:
    loop_result = _loop_result(
        tool_results=[
            {
                "tool": "file_patch_writer",
                "success": True,
                "input_metadata": {
                    "file_path": "snake_game.py",
                    "generated_unit": _GENERATED,
                    "artifact_ref": {"sha256": _DIGEST},
                    "unrelated": "preserved",
                },
            }
        ]
    )

    redacted = redact_provider_generated_units(loop_result)

    assert redacted is loop_result
    input_metadata = loop_result.tool_results[0]["input_metadata"]
    assert input_metadata == {
        "file_path": "snake_game.py",
        "generated_unit": None,
        "generated_unit_chars": len(_GENERATED),
        "generated_unit_sha256": _DIGEST,
        "artifact_ref": {"sha256": _DIGEST},
        "unrelated": "preserved",
    }


def test_redacts_all_typed_event_projections_without_losing_lineage() -> None:
    invocation = _call("invocation", _input())
    error = _error("error", _input())
    nested_call = _call("nested-call", _input())
    nested_error = _error("nested-error", _input())
    event = _event(
        "event",
        _input(),
        tool_call=nested_call,
        tool_error=nested_error,
    )
    loop_result = _loop_result(
        events=[event],
        invocations=[invocation],
        errors=[error],
    )

    redact_provider_generated_units(loop_result)

    redacted_inputs = [
        loop_result.loop_metadata.events[0].input_metadata,
        loop_result.loop_metadata.events[0].tool_call.input_metadata,
        loop_result.loop_metadata.events[0].tool_error.input_metadata,
        loop_result.loop_metadata.tool_invocations[0].input_metadata,
        loop_result.loop_metadata.recoverable_errors[0].input_metadata,
    ]
    for item in redacted_inputs:
        assert item is not None
        assert item.generated_unit is None
        assert item.artifact_ref is not None
        assert item.artifact_ref.source_id == "project-generator"
        assert item.runtime_handles == {
            "_existing": "preserved",
            "_generated_unit_chars": len(_GENERATED),
            "_generated_unit_sha256": _DIGEST,
        }

    retained = json.dumps(
        {
            "tool_results": loop_result.tool_results,
            "loop_metadata": loop_result.loop_metadata.model_dump(mode="json"),
        },
        sort_keys=True,
    )
    assert _GENERATED not in retained


def test_redaction_is_idempotent_and_clean_results_are_unchanged() -> None:
    clean_input = _input(None)
    clean_event = _event("clean", clean_input)
    loop_result = _loop_result(
        tool_results=[{"success": True, "input_metadata": {"file_path": "a.py"}}],
        events=[clean_event],
    )
    before = loop_result.loop_metadata.model_dump(mode="python")

    first = redact_provider_generated_units(loop_result)
    second = redact_provider_generated_units(loop_result)

    assert first is second is loop_result
    assert loop_result.tool_results == [
        {"success": True, "input_metadata": {"file_path": "a.py"}}
    ]
    assert loop_result.loop_metadata.model_dump(mode="python") == before


@pytest.mark.parametrize(
    "generated_unit",
    [
        "x" * MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS,
        "🙂" * MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS,
    ],
)
def test_accepts_exact_generated_unit_character_boundary(
    generated_unit: str,
) -> None:
    loop_result = _loop_result(
        tool_results=[{"input_metadata": {"generated_unit": generated_unit}}]
    )

    redact_provider_generated_units(loop_result)

    input_metadata = loop_result.tool_results[0]["input_metadata"]
    assert input_metadata["generated_unit"] is None
    assert input_metadata["generated_unit_chars"] == len(generated_unit)
    assert input_metadata["generated_unit_sha256"] == hashlib.sha256(
        generated_unit.encode("utf-8")
    ).hexdigest()


def test_accepts_exact_collection_boundary() -> None:
    loop_result = _loop_result(
        tool_results=[{"success": True} for _ in range(MAX_PROVIDER_TOOL_ATTEMPTS)]
    )

    assert redact_provider_generated_units(loop_result) is loop_result


@pytest.mark.parametrize(
    "bad_value",
    [7, b"generated", {"body": "generated"}],
)
def test_rejects_malformed_generated_unit_atomically(bad_value: object) -> None:
    loop_result = _loop_result(
        tool_results=[
            {"input_metadata": {"generated_unit": _GENERATED}},
            {"input_metadata": {"generated_unit": bad_value}},
        ],
        events=[_event("event", _input())],
    )

    with pytest.raises(
        ProviderGeneratedUnitRedactionError,
        match="generated_unit must be a string",
    ):
        redact_provider_generated_units(loop_result)

    assert loop_result.tool_results[0]["input_metadata"]["generated_unit"] == _GENERATED
    assert loop_result.loop_metadata.events[0].input_metadata.generated_unit == _GENERATED


def test_rejects_generated_unit_overflow_atomically() -> None:
    oversized = "x" * (MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS + 1)
    loop_result = _loop_result(
        tool_results=[
            {"input_metadata": {"generated_unit": _GENERATED}},
            {"input_metadata": {"generated_unit": oversized}},
        ]
    )

    with pytest.raises(
        ProviderGeneratedUnitRedactionError,
        match="generated_unit exceeds the provider code artifact limit",
    ):
        redact_provider_generated_units(loop_result)

    assert loop_result.tool_results[0]["input_metadata"]["generated_unit"] == _GENERATED


def test_rejects_collection_overflow_without_mutation() -> None:
    loop_result = _loop_result(
        tool_results=[
            {"input_metadata": {"generated_unit": _GENERATED}},
            *({"success": True} for _ in range(MAX_PROVIDER_TOOL_ATTEMPTS)),
        ]
    )

    with pytest.raises(
        ProviderGeneratedUnitRedactionError,
        match="tool_results exceeds the provider tool attempt limit",
    ):
        redact_provider_generated_units(loop_result)

    assert loop_result.tool_results[0]["input_metadata"]["generated_unit"] == _GENERATED


def test_rejects_unbounded_collection_without_mutation() -> None:
    loop_result = _loop_result(
        tool_results=[{"input_metadata": {"generated_unit": _GENERATED}}]
    )
    loop_result.tool_results = (item for item in loop_result.tool_results)

    with pytest.raises(
        ProviderGeneratedUnitRedactionError,
        match="tool_results must be a bounded list",
    ):
        redact_provider_generated_units(loop_result)
