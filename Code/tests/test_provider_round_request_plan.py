from __future__ import annotations

import pytest

from core.llm import LLMMessage, LLMToolCall, LLMToolFunctionCall
from core.provider_round_request_plan import (
    ProviderRoundRequestPlanError,
    build_provider_round_request_plan,
)
from core.provider_round_budget_policy import (
    MAX_PROVIDER_RESULT_CHARS,
    MIN_PROVIDER_RESULT_CHARS,
)


def _messages() -> list[LLMMessage]:
    return [
        LLMMessage(role="user", content="Read README"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                LLMToolCall(
                    id="call-1",
                    function=LLMToolFunctionCall(
                        name="file_reader",
                        arguments='{"file_path":"README.md"}',
                    ),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            content='{"success":true}',
            tool_call_id="call-1",
        ),
    ]


def test_plan_combines_surface_budget_and_compacted_messages() -> None:
    plan = build_provider_round_request_plan(
        messages=_messages(),
        tool_names=["file_reader", "command_executor"],
        finalization_pending=False,
        post_mutation_active=False,
        mutation_tools_exposed=False,
        all_scoped_reads_complete=False,
        prompt_budget_tokens=4096,
        response_call_count=1,
        remaining_prompt_tokens=None,
    )

    assert plan.tool_names == ("file_reader", "command_executor")
    assert plan.tool_choice == "required"
    assert plan.max_calls == 1
    assert MIN_PROVIDER_RESULT_CHARS <= plan.tool_result_char_budget <= MAX_PROVIDER_RESULT_CHARS
    assert len(plan.messages) == 3
    assert plan.messages[0].content == "Read README"


def test_plan_finalization_has_no_tools_and_no_required_choice() -> None:
    plan = build_provider_round_request_plan(
        messages=_messages(),
        tool_names=["file_reader", "command_executor"],
        finalization_pending=True,
        post_mutation_active=False,
        mutation_tools_exposed=False,
        all_scoped_reads_complete=True,
        prompt_budget_tokens=4096,
        response_call_count=1,
    )

    assert plan.tool_names == ()
    assert plan.tool_choice is None
    assert plan.max_calls == 1


def test_plan_snapshots_source_messages() -> None:
    messages = _messages()
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

    messages[0].content = "mutated"
    assert plan.messages[0].content == "Read README"
    assert plan.messages[0] is not messages[0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"messages": "not-a-list"},
        {"tool_names": ["file_reader", "file_reader"]},
        {"prompt_budget_tokens": 0},
        {"response_call_count": 0},
        {"finalization_pending": 1},
    ],
)
def test_plan_rejects_invalid_inputs(overrides) -> None:
    values = {
        "messages": _messages(),
        "tool_names": ["file_reader"],
        "finalization_pending": False,
        "post_mutation_active": False,
        "mutation_tools_exposed": False,
        "all_scoped_reads_complete": False,
        "prompt_budget_tokens": 4096,
        "response_call_count": 1,
    }
    values.update(overrides)
    with pytest.raises(ProviderRoundRequestPlanError):
        build_provider_round_request_plan(**values)
