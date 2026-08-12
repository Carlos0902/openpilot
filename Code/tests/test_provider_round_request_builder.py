from __future__ import annotations

import pytest

from core.llm import LLMMessage, LLMToolCall, LLMToolDefinition, LLMToolFunction, LLMToolFunctionCall
from core.provider_round_request_builder import (
    ProviderRoundRequestBuilderError,
    build_provider_round_llm_request,
)
from core.provider_round_request_plan import build_provider_round_request_plan
from metadata import ContextRequestPurpose


class _Settings:
    context_max_prompt_tokens = 4096
    context_reserved_prompt_tokens = 0


class _Client:
    settings = _Settings()


def _plan(*, finalization_pending: bool = False):
    return build_provider_round_request_plan(
        messages=[LLMMessage(role="user", content="inspect")],
        tool_names=[] if finalization_pending else ["file_reader"],
        finalization_pending=finalization_pending,
        post_mutation_active=False,
        mutation_tools_exposed=False,
        all_scoped_reads_complete=finalization_pending,
        prompt_budget_tokens=4096,
        response_call_count=1,
    )


def test_builder_preserves_plan_and_adds_typed_budget_trace() -> None:
    plan = _plan()
    tool = LLMToolDefinition(function=LLMToolFunction(name="file_reader"))
    request = build_provider_round_llm_request(
        _Client(),
        plan=plan,
        tool_definitions=[tool],
        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
        max_tokens=128,
    )
    assert [message.model_dump(mode="json") for message in request.messages] == [
        message.model_dump(mode="json") for message in plan.messages
    ]
    assert [tool.function.name for tool in request.tools] == ["file_reader"]
    assert request.tool_choice == "required"
    assert request.trace_info["provider_round_max_calls"] == plan.max_calls


def test_builder_finalization_has_empty_tool_surface() -> None:
    plan = _plan(finalization_pending=True)
    request = build_provider_round_llm_request(
        _Client(),
        plan=plan,
        tool_definitions=[],
        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
    )
    assert request.tools == []
    assert request.tool_choice is None


def test_builder_rejects_mismatched_tool_definitions() -> None:
    plan = _plan()
    with pytest.raises(ProviderRoundRequestBuilderError):
        build_provider_round_llm_request(
            _Client(),
            plan=plan,
            tool_definitions=[],
            purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
        )


def test_builder_assembles_tool_continuation_and_restores_wire_snapshot() -> None:
    messages = [
        LLMMessage(role="user", content="Read README"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[],
        ),
    ]
    messages[1] = messages[1].model_copy(
        update={
            "tool_calls": [
                LLMToolCall(
                    id="call-1",
                    function=LLMToolFunctionCall(
                        name="file_reader",
                        arguments='{"file_path":"README.md"}',
                    ),
                )
            ]
        }
    )
    messages.append(
        LLMMessage(
            role="tool",
            content='{"success":true}',
            tool_call_id="call-1",
        )
    )
    plan = build_provider_round_request_plan(
        messages=messages,
        tool_names=["file_reader"],
        finalization_pending=False,
        post_mutation_active=False,
        mutation_tools_exposed=False,
        all_scoped_reads_complete=False,
        prompt_budget_tokens=4096,
        response_call_count=1,
    )
    tool = LLMToolDefinition(function=LLMToolFunction(name="file_reader"))
    request = build_provider_round_llm_request(
        _Client(),
        plan=plan,
        tool_definitions=[tool],
        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
    )
    assert [message.model_dump(mode="json") for message in request.messages] == [
        message.model_dump(mode="json") for message in plan.messages
    ]
    assert request.messages[-1].role == "tool"
