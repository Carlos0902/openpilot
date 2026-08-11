from __future__ import annotations

import json

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_tool_admission import MAX_PROVIDER_TOOL_ARGUMENT_CHARS
from core.provider_tool_call_signature import (
    MAX_PROVIDER_SIGNATURE_DEPTH,
    MAX_PROVIDER_SIGNATURE_ITEMS,
    ProviderToolSignatureError,
    provider_tool_call_signature,
)


def _call(arguments, *, name="file_reader", call_id="provider-1"):
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(
            name=name,
            arguments=(
                arguments
                if isinstance(arguments, str)
                else json.dumps(arguments)
            ),
        ),
    )


def test_signature_normalizes_relative_and_absolute_paths(tmp_path) -> None:
    relative = _call({"file_path": "README.md"}, call_id="relative")
    absolute = _call(
        {"file_path": str(tmp_path / "README.md")},
        call_id="absolute",
    )

    assert provider_tool_call_signature(
        relative,
        project_path=str(tmp_path),
    ) == provider_tool_call_signature(
        absolute,
        project_path=str(tmp_path),
    )


def test_signature_is_stable_across_object_and_file_path_order(tmp_path) -> None:
    first = _call(
        {
            "file_paths": ["b.py", "a.py"],
            "options": {"offset": 0, "mode": "adaptive"},
        }
    )
    second = _call(
        {
            "options": {"mode": "adaptive", "offset": 0},
            "file_paths": ["a.py", "b.py"],
        }
    )

    assert provider_tool_call_signature(
        first,
        project_path=str(tmp_path),
    ) == provider_tool_call_signature(
        second,
        project_path=str(tmp_path),
    )


def test_signature_preserves_order_for_non_path_lists() -> None:
    forward = _call({"patterns": ["first", "second"]})
    reverse = _call({"patterns": ["second", "first"]})

    assert provider_tool_call_signature(forward) != provider_tool_call_signature(
        reverse
    )


def test_signature_ignores_provider_call_identity() -> None:
    first = _call({"file_path": "README.md"}, call_id="provider-1")
    second = _call({"file_path": "README.md"}, call_id="provider-2")

    assert provider_tool_call_signature(first) == provider_tool_call_signature(
        second
    )


def test_invalid_argument_payloads_remain_distinct_and_bounded() -> None:
    first = _call('{"file_path":')
    second = _call('{"file_path":"')

    first_signature = provider_tool_call_signature(first)

    assert len(first_signature) == 64
    assert first_signature == provider_tool_call_signature(first)
    assert first_signature != provider_tool_call_signature(second)


def test_overlong_invalid_payload_hashes_only_the_bounded_prefix() -> None:
    prefix = "x" * MAX_PROVIDER_TOOL_ARGUMENT_CHARS
    first = _call(prefix + "a")
    second = _call(prefix + "b")

    assert provider_tool_call_signature(first) == provider_tool_call_signature(
        second
    )


def test_signature_changes_with_project_root(tmp_path) -> None:
    call = _call({"file_path": "README.md"})

    assert provider_tool_call_signature(
        call,
        project_path=str(tmp_path / "one"),
    ) != provider_tool_call_signature(
        call,
        project_path=str(tmp_path / "two"),
    )


def test_signature_rejects_nested_values_past_depth_limit() -> None:
    value = "leaf"
    for _ in range(MAX_PROVIDER_SIGNATURE_DEPTH):
        value = {"nested": value}

    assert len(provider_tool_call_signature(_call(value))) == 64
    with pytest.raises(ProviderToolSignatureError):
        provider_tool_call_signature(_call({"nested": value}))


def test_signature_rejects_values_past_item_limit() -> None:
    exact = _call(
        {
            "items": list(range(MAX_PROVIDER_SIGNATURE_ITEMS - 1)),
        }
    )
    overflow = _call(
        {
            "items": list(range(MAX_PROVIDER_SIGNATURE_ITEMS)),
        }
    )

    assert len(provider_tool_call_signature(exact)) == 64
    with pytest.raises(ProviderToolSignatureError):
        provider_tool_call_signature(overflow)
