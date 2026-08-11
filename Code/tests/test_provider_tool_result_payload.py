from __future__ import annotations

import json

import pytest

from core.provider_tool_result_payload import (
    MAX_PROVIDER_TOOL_RESULT_CHARS,
    MAX_PROVIDER_TOOL_RESULT_DEPTH,
    MAX_PROVIDER_TOOL_RESULT_INPUT_CHARS,
    MAX_PROVIDER_TOOL_RESULT_ITEMS,
    MIN_PROVIDER_TOOL_RESULT_CHARS,
    ProviderToolResultPayloadError,
    fit_provider_tool_result_payload,
)


def test_small_payload_is_preserved_exactly() -> None:
    payload = {
        "success": True,
        "tool": "file_reader",
        "result": {"file_path": "README.md", "preview": "hello"},
    }

    content = fit_provider_tool_result_payload(payload)

    assert json.loads(content) == payload


def test_large_preview_is_compacted_without_mutating_source() -> None:
    payload = {
        "success": True,
        "tool": "file_reader",
        "result": {
            "file_path": "README.md",
            "content": "x" * 4_000,
            "projection_status": "inline",
        },
    }
    source_content = payload["result"]["content"]

    content = fit_provider_tool_result_payload(payload)
    projected = json.loads(content)

    assert len(content) <= MAX_PROVIDER_TOOL_RESULT_CHARS
    assert projected["projection_compacted"] is True
    assert "content" not in projected["result"]
    assert 0 < len(projected["result"]["preview"]) < len(source_content)
    assert payload["result"]["content"] == source_content


def test_preview_compaction_does_not_invent_absent_diagnostics() -> None:
    payload = {
        "success": True,
        "tool": "file_reader",
        "result": {"preview": "x" * 4_000},
    }

    projected = json.loads(fit_provider_tool_result_payload(payload))

    assert "error_type" not in projected
    assert "error" not in projected
    assert "suggested_recovery" not in projected


def test_complete_declared_window_keeps_semantic_completion() -> None:
    payload = {
        "success": True,
        "tool": "file_reader",
        "result": {
            "preview": "x" * 4_000,
            "evidence_status": "complete",
            "projection_status": "bounded_window",
        },
    }

    projected = json.loads(fit_provider_tool_result_payload(payload))

    assert projected["display_truncated"] is True
    assert "projection_compacted" not in projected
    assert projected["result"]["evidence_status"] == "complete"
    assert projected["result"]["projection_status"] == "bounded_window"


def test_large_non_text_result_falls_back_to_minimal_payload() -> None:
    payload = {
        "success": True,
        "tool": "search_tool",
        "result": {"items": [{"value": "x" * 200} for _ in range(40)]},
        "artifact_ref": {
            "sha256": "a" * 64,
            "file_path": "results.json",
            "content": "secret" * 1_000,
        },
    }

    content = fit_provider_tool_result_payload(payload)
    projected = json.loads(content)

    assert len(content) <= MAX_PROVIDER_TOOL_RESULT_CHARS
    assert projected["projection_compacted"] is True
    assert "result" not in projected
    assert projected["artifact_ref"] == {
        "sha256": "a" * 64,
        "file_path": "results.json",
    }
    assert "secret" not in content


def test_failure_fields_are_preserved_with_bounded_text() -> None:
    payload = {
        "success": False,
        "tool": "command_executor",
        "error_type": "ProviderFailure",
        "error": "e" * 2_000,
        "suggested_recovery": "r" * 2_000,
        "result": {"diagnostic": "x" * 4_000},
    }

    content = fit_provider_tool_result_payload(payload)
    projected = json.loads(content)

    assert len(content) <= MAX_PROVIDER_TOOL_RESULT_CHARS
    assert projected["success"] is False
    assert projected["error_type"] == "ProviderFailure"
    assert len(projected["error"]) <= 320
    assert len(projected["suggested_recovery"]) <= 320


def test_payload_fitter_is_deterministic() -> None:
    payload = {
        "tool": "file_reader",
        "success": True,
        "result": {"preview": "x" * 4_000},
    }

    assert fit_provider_tool_result_payload(
        payload
    ) == fit_provider_tool_result_payload(payload)


def test_exact_minimum_limit_is_accepted() -> None:
    payload = {
        "success": True,
        "tool": "file_reader",
        "result": {"preview": "x" * 4_000},
    }

    content = fit_provider_tool_result_payload(
        payload,
        limit=MIN_PROVIDER_TOOL_RESULT_CHARS,
    )

    assert len(content) <= MIN_PROVIDER_TOOL_RESULT_CHARS
    assert isinstance(json.loads(content), dict)


def test_payload_input_character_limit_accepts_boundary_and_rejects_overflow() -> None:
    fixed_chars = sum(
        len(value)
        for value in ("success", "tool", "file_reader", "result", "preview")
    )
    preview_chars = MAX_PROVIDER_TOOL_RESULT_INPUT_CHARS - fixed_chars
    payload = {
        "success": True,
        "tool": "file_reader",
        "result": {"preview": "x" * preview_chars},
    }

    assert (
        len(fit_provider_tool_result_payload(payload))
        <= MAX_PROVIDER_TOOL_RESULT_CHARS
    )
    payload["result"]["preview"] += "x"
    with pytest.raises(ProviderToolResultPayloadError):
        fit_provider_tool_result_payload(payload)


def test_payload_item_limit_accepts_boundary_and_rejects_overflow() -> None:
    exact = {
        "success": True,
        "tool": "search_tool",
        "result": {
            "items": list(range(MAX_PROVIDER_TOOL_RESULT_ITEMS - 4)),
        },
    }

    assert (
        len(fit_provider_tool_result_payload(exact))
        <= MAX_PROVIDER_TOOL_RESULT_CHARS
    )
    exact["result"]["items"].append(MAX_PROVIDER_TOOL_RESULT_ITEMS)
    with pytest.raises(ProviderToolResultPayloadError):
        fit_provider_tool_result_payload(exact)


def test_payload_depth_limit_accepts_boundary_and_rejects_overflow() -> None:
    value = "leaf"
    for _ in range(MAX_PROVIDER_TOOL_RESULT_DEPTH - 1):
        value = {"nested": value}
    payload = {"success": True, "tool": "tool", "result": value}

    assert (
        len(fit_provider_tool_result_payload(payload))
        <= MAX_PROVIDER_TOOL_RESULT_CHARS
    )
    payload["result"] = {"nested": value}
    with pytest.raises(ProviderToolResultPayloadError):
        fit_provider_tool_result_payload(payload)


@pytest.mark.parametrize(
    "limit",
    [
        MIN_PROVIDER_TOOL_RESULT_CHARS - 1,
        MAX_PROVIDER_TOOL_RESULT_CHARS + 1,
        True,
        800.0,
    ],
)
def test_payload_fitter_rejects_invalid_limits(limit) -> None:
    with pytest.raises(ProviderToolResultPayloadError):
        fit_provider_tool_result_payload({}, limit=limit)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"success": 1, "tool": "file_reader"},
        {"success": True, "tool": ""},
        {
            "success": True,
            "tool": "file_reader",
            "result": {"value": object()},
        },
    ],
)
def test_payload_fitter_rejects_invalid_payloads(payload) -> None:
    with pytest.raises(ProviderToolResultPayloadError):
        fit_provider_tool_result_payload(payload)
