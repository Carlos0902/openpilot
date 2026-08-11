from __future__ import annotations

import copy
import json

import pytest

from core.provider_mutation_receipt import (
    MAX_PROVIDER_MUTATION_RECEIPT_CHANGED_RANGES,
    MAX_PROVIDER_MUTATION_RECEIPT_COMMAND_CHARS,
    MAX_PROVIDER_MUTATION_RECEIPT_PATH_CHARS,
    ProviderMutationReceiptError,
    provider_mutation_receipt,
)
from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_TOOL_ATTEMPTS
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import ToolLoopMetadata


def _loop_result(*tool_results: dict) -> ToolEventLoopRunResult:
    return ToolEventLoopRunResult(
        success=True,
        tool_results=list(tool_results),
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session",
            task_id="task",
            status="completed",
            success=True,
        ),
    )


def _mutation_result(**overrides) -> dict:
    item = {
        "tool": "file_patch_writer",
        "success": True,
        "input_metadata": {
            "file_path": "Code/src/example.py",
            "operation_kind": "replace_symbol",
            "generated_unit": "def generated():\n    return True\n",
            "artifact_ref": {
                "kind": "code_artifact",
                "source_id": "source-1",
                "provider_call_id": "provider-1",
                "sha256": "a" * 64,
                "bytes": 33,
                "chars": 33,
                "language": "python",
                "untrusted_extra": "must not escape",
            },
        },
        "result": {
            "bytes_written": 33,
            "attributes": {
                "changed_ranges": [
                    {
                        "line_start": index + 1,
                        "line_end": index + 2,
                        "replacement_text": "must not escape",
                    }
                    for index in range(12)
                ]
            },
        },
    }
    item.update(overrides)
    return item


def test_receipt_projects_first_successful_mutation_without_generated_body() -> None:
    failed = _mutation_result(success=False)
    first = _mutation_result()
    second = _mutation_result(
        input_metadata={
            "file_path": "Code/src/second.py",
            "operation_kind": "replace_symbol",
        }
    )
    original = copy.deepcopy(first)

    receipt = provider_mutation_receipt(
        _loop_result(failed, first, second),
        validation_command="PYTHONPATH=src python -m pytest -q tests/test_example.py",
    )

    assert receipt == {
        "status": "mutation_applied",
        "tool": "file_patch_writer",
        "file_path": "Code/src/example.py",
        "operation_kind": "replace_symbol",
        "artifact_ref": {
            "kind": "code_artifact",
            "source_id": "source-1",
            "provider_call_id": "provider-1",
            "sha256": "a" * 64,
            "bytes": 33,
            "chars": 33,
            "language": "python",
        },
        "bytes_written": 33,
        "changed_ranges": [
            {"line_start": index + 1, "line_end": index + 2}
            for index in range(MAX_PROVIDER_MUTATION_RECEIPT_CHANGED_RANGES)
        ],
        "validation_command": (
            "PYTHONPATH=src python -m pytest -q tests/test_example.py"
        ),
    }
    assert first == original
    encoded = json.dumps(receipt, sort_keys=True)
    assert "generated_unit" not in encoded
    assert "must not escape" not in encoded
    assert "Code/src/second.py" not in encoded


def test_receipt_ignores_non_mutations_and_unsuccessful_mutations() -> None:
    receipt = provider_mutation_receipt(
        _loop_result(
            {"tool": "file_reader", "success": True},
            _mutation_result(success=False),
        ),
        validation_command="python -m pytest -q",
    )

    assert receipt is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "file_path",
            "x" * (MAX_PROVIDER_MUTATION_RECEIPT_PATH_CHARS + 1),
            "file_path",
        ),
        ("operation_kind", "x" * 65, "operation_kind"),
    ],
)
def test_receipt_rejects_unbounded_mutation_identity(
    field: str,
    value: str,
    message: str,
) -> None:
    item = _mutation_result()
    item["input_metadata"][field] = value

    with pytest.raises(ProviderMutationReceiptError, match=message):
        provider_mutation_receipt(
            _loop_result(item),
            validation_command="python -m pytest -q",
        )


def test_receipt_preserves_exact_bounded_validation_command() -> None:
    command = "x" * MAX_PROVIDER_MUTATION_RECEIPT_COMMAND_CHARS

    receipt = provider_mutation_receipt(
        _loop_result(_mutation_result()),
        validation_command=command,
    )

    assert receipt is not None
    assert receipt["validation_command"] == command


@pytest.mark.parametrize("validation_command", ["", "   ", None, 7])
def test_receipt_rejects_missing_or_non_string_validation_command(
    validation_command,
) -> None:
    with pytest.raises(
        ProviderMutationReceiptError,
        match="validation_command",
    ):
        provider_mutation_receipt(
            _loop_result(_mutation_result()),
            validation_command=validation_command,
        )


def test_receipt_rejects_unbounded_validation_command() -> None:
    with pytest.raises(
        ProviderMutationReceiptError,
        match="validation_command",
    ):
        provider_mutation_receipt(
            _loop_result(_mutation_result()),
            validation_command=(
                "x" * (MAX_PROVIDER_MUTATION_RECEIPT_COMMAND_CHARS + 1)
            ),
        )


@pytest.mark.parametrize(
    "changed_ranges",
    [
        ({"line_start": 1, "line_end": 2},),
        [{"line_start": True, "line_end": 2}],
        [{"line_start": 2, "line_end": 1}],
        [{"line_start": 0, "line_end": 1}],
        [{"line_start": 1}],
    ],
)
def test_receipt_rejects_malformed_changed_ranges(changed_ranges) -> None:
    item = _mutation_result()
    item["result"]["attributes"]["changed_ranges"] = changed_ranges

    with pytest.raises(
        ProviderMutationReceiptError,
        match="changed_ranges",
    ):
        provider_mutation_receipt(
            _loop_result(item),
            validation_command="python -m pytest -q",
        )


def test_receipt_rejects_malformed_artifact_reference() -> None:
    item = _mutation_result()
    item["input_metadata"]["artifact_ref"]["sha256"] = "not-a-digest"

    with pytest.raises(
        ProviderMutationReceiptError,
        match="artifact_ref",
    ):
        provider_mutation_receipt(
            _loop_result(item),
            validation_command="python -m pytest -q",
        )


def test_receipt_rejects_unbounded_or_malformed_result_collections() -> None:
    loop_result = _loop_result()
    loop_result.tool_results = (
        _mutation_result() for _ in range(MAX_PROVIDER_TOOL_ATTEMPTS + 1)
    )
    with pytest.raises(
        ProviderMutationReceiptError,
        match="bounded list",
    ):
        provider_mutation_receipt(
            loop_result,
            validation_command="python -m pytest -q",
        )

    loop_result.tool_results = [
        {"tool": "file_reader", "success": True}
        for _ in range(MAX_PROVIDER_TOOL_ATTEMPTS + 1)
    ]
    with pytest.raises(
        ProviderMutationReceiptError,
        match="attempt limit",
    ):
        provider_mutation_receipt(
            loop_result,
            validation_command="python -m pytest -q",
        )

    loop_result.tool_results = ["not-a-result"]
    with pytest.raises(
        ProviderMutationReceiptError,
        match=r"tool_results\[0\]",
    ):
        provider_mutation_receipt(
            loop_result,
            validation_command="python -m pytest -q",
        )
