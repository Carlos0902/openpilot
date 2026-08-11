from __future__ import annotations

import json

import pytest

from core.llm import LLMResponse, LLMToolCall, LLMToolFunctionCall
from core.provider_code_artifact_ledger import (
    ProviderCodeArtifactLedger,
    ProviderCodeArtifactLedgerError,
    ProviderCodeArtifactReference,
)
from core.provider_tool_result_batch import (
    ProviderToolResultBatchError,
    provider_tool_result_batch,
)
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import CodeArtifactMetadata, ToolLoopMetadata


def _response(call_id: str = "provider-generator") -> LLMResponse:
    return LLMResponse(
        content="",
        reasoning_content="Generate the requested implementation unit.",
        tool_calls=[
            LLMToolCall(
                id=call_id,
                function=LLMToolFunctionCall(
                    name="code_unit_generator",
                    arguments='{"description":"generate code"}',
                ),
            )
        ],
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )


def _loop_result(result) -> ToolEventLoopRunResult:
    return ToolEventLoopRunResult(
        success=True,
        tool_results=[
            {
                "provider_call_id": "provider-generator",
                "call_id": "project-generator",
                "tool": "code_unit_generator",
                "success": True,
                "result": result,
            }
        ],
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session",
            task_id="task",
            status="completed",
            success=True,
        ),
    )


def test_code_artifact_requires_runtime_ledger() -> None:
    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(),
            _loop_result(
                CodeArtifactMetadata(
                    code="def generated():\n    return True\n",
                    language="python",
                )
            ),
        )


def test_code_artifact_is_registered_and_payload_reference_resolves() -> None:
    code = "def generated():\n    return True\n"
    ledger = ProviderCodeArtifactLedger()

    results = provider_tool_result_batch(
        _response(),
        _loop_result(CodeArtifactMetadata(code=code, language="python")),
        code_artifact_ledger=ledger,
    )

    payload = json.loads(results[0].content)
    reference = ProviderCodeArtifactReference.model_validate(
        payload["artifact_ref"]
    )
    assert ledger.resolve(reference) == code
    assert payload["result"]["kind"] == "code_artifact"
    assert "artifact_ref" in payload["result"]["artifact_handoff"]
    assert code not in results[0].content


def test_code_artifact_file_path_is_not_promoted_into_authority_reference() -> None:
    code = "print('scoped')"
    ledger = ProviderCodeArtifactLedger()

    results = provider_tool_result_batch(
        _response(),
        _loop_result(
            {
                "kind": "code_artifact",
                "code": code,
                "language": "python",
                "file_path": "README.md",
            }
        ),
        code_artifact_ledger=ledger,
    )

    payload = json.loads(results[0].content)
    assert payload["result"]["file_path"] == "README.md"
    assert "file_path" not in payload["artifact_ref"]
    assert ledger.resolve(payload["artifact_ref"]) == code


def test_registration_is_idempotent_for_reprojected_result() -> None:
    code = "print('same')"
    ledger = ProviderCodeArtifactLedger(max_artifacts=1, max_total_chars=100)
    response = _response()
    loop_result = _loop_result(
        CodeArtifactMetadata(code=code, language="python")
    )

    first = provider_tool_result_batch(
        response,
        loop_result,
        code_artifact_ledger=ledger,
    )
    second = provider_tool_result_batch(
        response,
        loop_result,
        code_artifact_ledger=ledger,
    )

    assert second == first
    assert ledger.artifact_count == 1
    assert ledger.total_chars == len(code)


def test_prepare_derives_reference_without_registering_body() -> None:
    ledger = ProviderCodeArtifactLedger()

    reference = ledger.prepare(
        CodeArtifactMetadata(code="print('prepared')", language="python"),
        source_id="project-prepared",
        provider_call_id="provider-prepared",
    )

    assert reference.kind == "code_artifact"
    assert ledger.artifact_count == 0
    assert ledger.total_chars == 0
    with pytest.raises(ProviderCodeArtifactLedgerError):
        ledger.resolve(reference)


def test_registration_failure_does_not_emit_unresolvable_result() -> None:
    ledger = ProviderCodeArtifactLedger(max_artifacts=1, max_total_chars=4)

    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(),
            _loop_result(CodeArtifactMetadata(code="five!", language="python")),
            code_artifact_ledger=ledger,
        )

    assert ledger.artifact_count == 0
    assert ledger.total_chars == 0


def test_projection_reference_mismatch_fails_before_registration(
    monkeypatch,
) -> None:
    ledger = ProviderCodeArtifactLedger()

    def mismatched_projection(*_args, **_kwargs):
        return (
            {
                "kind": "code_artifact",
                "preview": "print('safe')",
                "artifact_handoff": "Pass artifact_ref unchanged.",
            },
            {
                "kind": "code_artifact",
                "source_id": "project-generator",
                "provider_call_id": "provider-generator",
                "sha256": "0" * 64,
                "bytes": 13,
                "chars": 13,
                "language": "python",
            },
        )

    monkeypatch.setattr(
        "core.provider_tool_result_batch.project_provider_tool_result",
        mismatched_projection,
    )

    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(),
            _loop_result(
                CodeArtifactMetadata(
                    code="print('safe')",
                    language="python",
                )
            ),
            code_artifact_ledger=ledger,
        )

    assert ledger.artifact_count == 0
    assert ledger.total_chars == 0


def test_multi_artifact_registration_failure_is_atomic() -> None:
    response = LLMResponse(
        content="",
        tool_calls=[
            LLMToolCall(
                id=f"provider-generator-{index}",
                function=LLMToolFunctionCall(
                    name="code_unit_generator",
                    arguments="{}",
                ),
            )
            for index in range(2)
        ],
        model="test-model",
        provider="test-provider",
        finish_reason="tool_calls",
    )
    loop_result = ToolEventLoopRunResult(
        success=True,
        tool_results=[
            {
                "provider_call_id": f"provider-generator-{index}",
                "call_id": f"project-generator-{index}",
                "tool": "code_unit_generator",
                "success": True,
                "result": CodeArtifactMetadata(
                    code=code,
                    language="python",
                ),
            }
            for index, code in enumerate(("abc", "defg"))
        ],
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session",
            task_id="task",
            status="completed",
            success=True,
        ),
    )
    ledger = ProviderCodeArtifactLedger(max_artifacts=2, max_total_chars=6)

    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            response,
            loop_result,
            code_artifact_ledger=ledger,
        )

    assert ledger.artifact_count == 0
    assert ledger.total_chars == 0


def test_registration_rejects_conflicting_code_mapping() -> None:
    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            _response(),
            _loop_result(
                {
                    "kind": "code_artifact",
                    "code": "print('code')",
                    "content": "print('different')",
                    "language": "python",
                }
            ),
            code_artifact_ledger=ProviderCodeArtifactLedger(),
        )


def test_invalid_ledger_is_rejected_even_for_empty_batch() -> None:
    empty_response = LLMResponse(
        content="done",
        model="test-model",
        provider="test-provider",
        tool_calls=[],
    )
    empty_loop = ToolEventLoopRunResult(
        success=True,
        tool_results=[],
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session",
            task_id="task",
            status="completed",
            success=True,
        ),
    )

    with pytest.raises(ProviderToolResultBatchError):
        provider_tool_result_batch(
            empty_response,
            empty_loop,
            code_artifact_ledger=object(),
        )


def test_non_code_result_does_not_require_artifact_ledger() -> None:
    result = {
        "kind": "file_artifact",
        "content": "README evidence",
        "file_path": "README.md",
        "lines_read": 1,
        "total_lines": 1,
        "truncated": False,
    }

    batch = provider_tool_result_batch(
        _response(),
        _loop_result(result),
    )

    assert json.loads(batch[0].content)["result"]["kind"] == "file_artifact"


def test_registration_does_not_mutate_result_mapping() -> None:
    result = {
        "kind": "code_artifact",
        "code": "print('safe')",
        "language": "python",
    }
    original = dict(result)

    provider_tool_result_batch(
        _response(),
        _loop_result(result),
        code_artifact_ledger=ProviderCodeArtifactLedger(),
    )

    assert result == original
