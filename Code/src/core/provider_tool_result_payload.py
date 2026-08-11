"""Deterministic bounded JSON projection for provider tool results."""

from __future__ import annotations

import json
import math
from typing import Any

MIN_PROVIDER_TOOL_RESULT_CHARS = 640
MAX_PROVIDER_TOOL_RESULT_CHARS = 1_600
MAX_PROVIDER_TOOL_RESULT_INPUT_CHARS = 200_000
MAX_PROVIDER_TOOL_RESULT_ITEMS = 1_024
MAX_PROVIDER_TOOL_RESULT_DEPTH = 16


class ProviderToolResultPayloadError(ValueError):
    """Raised when a tool-result payload cannot be projected safely."""


def fit_provider_tool_result_payload(
    payload: dict[str, Any],
    *,
    limit: int = MAX_PROVIDER_TOOL_RESULT_CHARS,
) -> str:
    """Return valid JSON within the approved provider result character limit."""

    if not isinstance(payload, dict):
        raise ProviderToolResultPayloadError("payload must be a dictionary")
    if type(payload.get("success")) is not bool:
        raise ProviderToolResultPayloadError(
            "payload success must be a literal boolean"
        )
    if not isinstance(payload.get("tool"), str) or not str(
        payload.get("tool")
    ).strip():
        raise ProviderToolResultPayloadError(
            "payload tool must be a non-empty string"
        )
    _validate_payload_shape(payload)
    if (
        type(limit) is not int
        or limit < MIN_PROVIDER_TOOL_RESULT_CHARS
        or limit > MAX_PROVIDER_TOOL_RESULT_CHARS
    ):
        raise ProviderToolResultPayloadError(
            "limit must be an integer within the provider result bounds"
        )
    content = _encode(payload)
    if len(content) <= limit:
        return content
    candidate = json.loads(content)

    _bound_diagnostic_fields(candidate)
    result = candidate.get("result")
    declared_window_complete = bool(
        isinstance(result, dict)
        and result.get("evidence_status") == "complete"
        and result.get("projection_status") == "bounded_window"
    )
    marker = (
        "display_truncated"
        if declared_window_complete
        else "projection_compacted"
    )
    candidate[marker] = True

    if isinstance(result, dict) and (
        "preview" in result or "content" in result
    ):
        text_key = "preview" if "preview" in result else "content"
        preview = str(result.get(text_key) or "")
        if text_key == "content":
            result.pop("content", None)
            text_key = "preview"
        result[text_key] = _largest_fitting_prefix(
            candidate,
            result=result,
            text_key=text_key,
            text=preview,
            limit=limit,
        )
        content = _encode(candidate)
        if len(content) <= limit:
            return content

    minimal = {
        "success": bool(candidate.get("success")),
        "tool": _bounded_text(candidate.get("tool"), 128),
        "error_type": _bounded_text(candidate.get("error_type"), 128),
        "error": _bounded_text(candidate.get("error"), 320),
        "suggested_recovery": _bounded_text(
            candidate.get("suggested_recovery"),
            320,
        ),
        "artifact_ref": _compact_artifact_ref(candidate.get("artifact_ref")),
        marker: True,
    }
    minimal = {
        key: value
        for key, value in minimal.items()
        if value is not None
    }
    content = _encode(minimal)
    if len(content) <= limit:
        return content

    final = {
        "success": bool(candidate.get("success")),
        "tool": _bounded_text(candidate.get("tool"), 128),
        "error_type": _bounded_text(candidate.get("error_type"), 128),
        marker: True,
    }
    final = {key: value for key, value in final.items() if value is not None}
    content = _encode(final)
    if len(content) > limit:
        raise ProviderToolResultPayloadError(
            "minimal provider tool result exceeds the approved limit"
        )
    return content


def _largest_fitting_prefix(
    candidate: dict[str, Any],
    *,
    result: dict[str, Any],
    text_key: str,
    text: str,
    limit: int,
) -> str:
    low = 0
    high = len(text)
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        result[text_key] = text[:midpoint]
        if len(_encode(candidate)) <= limit:
            best = text[:midpoint]
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _bound_diagnostic_fields(payload: dict[str, Any]) -> None:
    for field, limit in (
        ("tool", 128),
        ("error_type", 128),
        ("error", 320),
        ("suggested_recovery", 320),
    ):
        if field in payload:
            payload[field] = _bounded_text(payload[field], limit)


def _compact_artifact_ref(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = {
        key: value[key]
        for key in (
            "kind",
            "source_id",
            "provider_call_id",
            "sha256",
            "chars",
            "file_path",
            "language",
        )
        if key in value
    }
    return compact or None


def _bounded_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    suffix = "...[truncated]"
    return text[: max(0, limit - len(suffix))] + suffix


def _encode(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProviderToolResultPayloadError(
            "payload must contain bounded JSON-compatible values"
        ) from exc


def _validate_payload_shape(value: Any) -> None:
    item_count = [0]
    string_chars = [0]

    def visit(item: Any, *, depth: int) -> None:
        if depth > MAX_PROVIDER_TOOL_RESULT_DEPTH:
            raise ProviderToolResultPayloadError(
                "payload exceeds the provider result depth limit"
            )
        if isinstance(item, dict):
            _consume_count(item_count, len(item))
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ProviderToolResultPayloadError(
                        "payload dictionary keys must be strings"
                    )
                _consume_chars(string_chars, len(key))
                visit(child, depth=depth + 1)
            return
        if isinstance(item, (list, tuple)):
            _consume_count(item_count, len(item))
            for child in item:
                visit(child, depth=depth + 1)
            return
        if isinstance(item, str):
            _consume_chars(string_chars, len(item))
            return
        if isinstance(item, float) and not math.isfinite(item):
            raise ProviderToolResultPayloadError(
                "payload floats must be finite"
            )
        if item is None or isinstance(item, (bool, int, float)):
            return
        raise ProviderToolResultPayloadError(
            "payload must contain JSON-compatible values"
        )

    visit(value, depth=0)


def _consume_count(counter: list[int], amount: int) -> None:
    counter[0] += amount
    if counter[0] > MAX_PROVIDER_TOOL_RESULT_ITEMS:
        raise ProviderToolResultPayloadError(
            "payload exceeds the provider result item limit"
        )


def _consume_chars(counter: list[int], amount: int) -> None:
    counter[0] += amount
    if counter[0] > MAX_PROVIDER_TOOL_RESULT_INPUT_CHARS:
        raise ProviderToolResultPayloadError(
            "payload exceeds the provider result input character limit"
        )


__all__ = [
    "MAX_PROVIDER_TOOL_RESULT_CHARS",
    "MAX_PROVIDER_TOOL_RESULT_DEPTH",
    "MAX_PROVIDER_TOOL_RESULT_INPUT_CHARS",
    "MAX_PROVIDER_TOOL_RESULT_ITEMS",
    "MIN_PROVIDER_TOOL_RESULT_CHARS",
    "ProviderToolResultPayloadError",
    "fit_provider_tool_result_payload",
]
