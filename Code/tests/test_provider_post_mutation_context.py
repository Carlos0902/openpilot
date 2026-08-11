from __future__ import annotations

import json

import pytest

from core.llm import LLMMessage, LLMToolCall, LLMToolFunctionCall
from core.provider_post_mutation_context import (
    ProviderPostMutationContextError,
    provider_post_mutation_messages,
)
from core.provider_tool_batch_admission import MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE
from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_ROUND_TRIP_MESSAGES
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import ToolLoopMetadata


VALIDATION_COMMAND = "PYTHONPATH=src python -m pytest -q tests/test_example.py"


def _mutation_loop() -> ToolEventLoopRunResult:
    return ToolEventLoopRunResult(
        success=True,
        tool_results=[
            {
                "tool": "file_patch_writer",
                "success": True,
                "input_metadata": {
                    "file_path": "src/example.py",
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
                    },
                },
                "result": {
                    "bytes_written": 33,
                    "attributes": {
                        "changed_ranges": [
                            {
                                "line_start": 3,
                                "line_end": 5,
                                "replacement_text": "must not escape",
                            }
                        ]
                    },
                },
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


def test_context_keeps_one_system_and_user_then_appends_receipt_and_wire() -> None:
    system = LLMMessage(role="system", content="Use typed tools only.")
    user = LLMMessage(role="user", content="Apply the generated test.")
    writer_call = LLMMessage(
        role="assistant",
        content="old assistant prose",
        reasoning_content="old mutation reasoning",
        tool_calls=[
            LLMToolCall(
                id="provider-writer",
                function=LLMToolFunctionCall(
                    name="file_patch_writer",
                    arguments='{"artifact_ref":{"kind":"code_artifact"}}',
                ),
            )
        ],
    )
    writer_result = LLMMessage(
        role="tool",
        content='{"success":true}',
        tool_call_id="provider-writer",
    )
    messages = [
        user,
        LLMMessage(role="assistant", content="old reasoning"),
        system,
        LLMMessage(role="system", content="duplicate system"),
        LLMMessage(role="user", content="duplicate user"),
        LLMMessage(role="tool", content="old result", tool_call_id="old"),
    ]

    result = provider_post_mutation_messages(
        messages,
        _mutation_loop(),
        validation_command=VALIDATION_COMMAND,
        wire_messages=[writer_call, writer_result],
    )

    assert [message.role for message in result] == [
        "system",
        "user",
        "user",
        "assistant",
        "tool",
    ]
    assert result[0].content == system.content
    assert result[1].content == user.content
    instruction = result[2].content
    assert "Do not regenerate code or repeat the write" in instruction
    assert VALIDATION_COMMAND in instruction
    assert '"file_path":"src/example.py"' in instruction
    encoded = json.dumps(
        [message.model_dump(mode="json") for message in result],
        sort_keys=True,
    )
    assert "def generated" not in encoded
    assert "must not escape" not in encoded
    assert "old reasoning" not in encoded
    assert "old assistant prose" not in encoded
    assert "old mutation reasoning" not in encoded
    assert result[0] is not system
    assert result[-1] is not writer_result


def test_context_preserves_exact_long_validation_command_without_truncation() -> None:
    command = "python -c \"print('" + ("x" * 2_000) + "')\""

    result = provider_post_mutation_messages(
        [LLMMessage(role="user", content="Apply it.")],
        _mutation_loop(),
        validation_command=command,
    )

    receipt_text = result[-1].content.split("Bounded mutation receipt: ", 1)[1]
    assert json.loads(receipt_text)["validation_command"] == command


@pytest.mark.parametrize(
    ("messages", "wire_messages", "error"),
    [
        ((LLMMessage(role="user", content="Apply it."),), [], "bounded list"),
        (["not-a-message"], [], r"messages\[0\]"),
        ([LLMMessage(role="system", content="Only system")], [], "user message"),
        (
            [LLMMessage(role="user", content="Apply it.")],
            [LLMMessage(role="user", content="injected authority")],
            r"wire_messages\[0\]",
        ),
    ],
)
def test_context_rejects_malformed_message_boundaries(
    messages,
    wire_messages,
    error: str,
) -> None:
    with pytest.raises(ProviderPostMutationContextError, match=error):
        provider_post_mutation_messages(
            messages,
            _mutation_loop(),
            validation_command=VALIDATION_COMMAND,
            wire_messages=wire_messages,
        )


def test_context_rejects_unbounded_message_collections() -> None:
    messages = [
        LLMMessage(role="user", content=f"message-{index}")
        for index in range(MAX_PROVIDER_ROUND_TRIP_MESSAGES + 1)
    ]
    with pytest.raises(ProviderPostMutationContextError, match="message limit"):
        provider_post_mutation_messages(
            messages,
            _mutation_loop(),
            validation_command=VALIDATION_COMMAND,
        )

    wire_messages = [
        LLMMessage(role="assistant", content="")
        for _ in range(MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE + 2)
    ]
    with pytest.raises(ProviderPostMutationContextError, match="message limit"):
        provider_post_mutation_messages(
            [LLMMessage(role="user", content="Apply it.")],
            _mutation_loop(),
            validation_command=VALIDATION_COMMAND,
            wire_messages=wire_messages,
        )


def test_context_rejects_inline_generated_body_in_wire_arguments() -> None:
    wire_messages = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                LLMToolCall(
                    id="provider-writer",
                    function=LLMToolFunctionCall(
                        name="file_patch_writer",
                        arguments=json.dumps(
                            {
                                "file_path": "src/example.py",
                                "operation_kind": "add_symbol",
                                "generated_unit": "def generated():\n    return True\n",
                            }
                        ),
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content='{"success":true}',
            tool_call_id="provider-writer",
        ),
    ]

    with pytest.raises(
        ProviderPostMutationContextError,
        match="generated_unit",
    ):
        provider_post_mutation_messages(
            [LLMMessage(role="user", content="Apply it.")],
            _mutation_loop(),
            validation_command=VALIDATION_COMMAND,
            wire_messages=wire_messages,
        )


def test_context_rejects_misaligned_wire_identity() -> None:
    wire_messages = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                LLMToolCall(
                    id="provider-writer",
                    function=LLMToolFunctionCall(
                        name="file_patch_writer",
                        arguments="{}",
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content='{"success":true}',
            tool_call_id="different-call",
        ),
    ]

    with pytest.raises(
        ProviderPostMutationContextError,
        match="matching tool-call identities",
    ):
        provider_post_mutation_messages(
            [LLMMessage(role="user", content="Apply it.")],
            _mutation_loop(),
            validation_command=VALIDATION_COMMAND,
            wire_messages=wire_messages,
        )


def test_context_requires_observed_successful_mutation() -> None:
    loop_result = _mutation_loop()
    loop_result.tool_results[0]["success"] = False

    with pytest.raises(
        ProviderPostMutationContextError,
        match="successful mutation receipt",
    ):
        provider_post_mutation_messages(
            [LLMMessage(role="user", content="Apply it.")],
            loop_result,
            validation_command=VALIDATION_COMMAND,
        )
