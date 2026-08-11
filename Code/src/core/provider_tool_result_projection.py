"""Bounded artifact projection for provider-visible tool results."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from core.provider_tool_result_payload import (
    MAX_PROVIDER_TOOL_RESULT_ITEMS,
)
from metadata import metadata_summary
from metadata.artifacts import (
    MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS,
    MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_ID_CHARS,
)

MAX_PROVIDER_RESULT_PREVIEW_CHARS = 480
MAX_PROVIDER_RESULT_SCALAR_CHARS = 320
MAX_PROVIDER_RESULT_ARTIFACT_CHARS = (
    MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS
)
MAX_PROVIDER_RESULT_FILES = MAX_PROVIDER_TOOL_RESULT_ITEMS
MAX_PROVIDER_RESULT_ID_CHARS = MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_ID_CHARS


class ProviderToolResultProjectionError(ValueError):
    """Raised when a tool result cannot be projected within fixed bounds."""


def project_provider_tool_result(
    result: Any,
    *,
    source_id: str,
    provider_call_id: str,
    declared_window_complete: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Project one tool result without exposing its complete artifact body."""

    source_id = _validated_id(source_id, field="source_id")
    provider_call_id = _validated_id(
        provider_call_id,
        field="provider_call_id",
    )
    if type(declared_window_complete) is not bool:
        raise ProviderToolResultProjectionError(
            "declared_window_complete must be a literal boolean"
        )

    summary = _result_summary(result)
    if not isinstance(summary, Mapping):
        return {
            "value": _bounded_text(summary, MAX_PROVIDER_RESULT_SCALAR_CHARS)
        }, None

    raw_text = _first_text(summary)
    if raw_text is not None and len(raw_text) > MAX_PROVIDER_RESULT_ARTIFACT_CHARS:
        raise ProviderToolResultProjectionError(
            "tool result artifact exceeds the projection input limit"
        )

    kind = _normalized_kind(summary.get("kind"))
    artifact_ref = _artifact_reference(
        summary,
        raw_text=raw_text,
        kind=kind,
        source_id=source_id,
        provider_call_id=provider_call_id,
    )
    projected = _project_scalar_fields(summary, kind=kind)

    if kind == "file_artifact":
        complete = _is_complete_file_result(summary)
        projected["evidence_status"] = (
            "complete" if complete or declared_window_complete else "partial"
        )
        if declared_window_complete:
            if raw_text:
                projected["preview"] = _bounded_text(
                    raw_text,
                    MAX_PROVIDER_RESULT_PREVIEW_CHARS,
                )
            projected["projection_status"] = "bounded_window"
        elif raw_text and complete and len(raw_text) <= MAX_PROVIDER_RESULT_PREVIEW_CHARS:
            projected["content"] = raw_text
            projected["projection_status"] = "inline"
        else:
            if raw_text:
                projected["preview"] = _bounded_text(
                    raw_text,
                    MAX_PROVIDER_RESULT_PREVIEW_CHARS,
                )
            projected["projection_status"] = (
                "bounded_preview" if raw_text else "inline"
            )
    elif raw_text:
        projected["preview"] = _bounded_text(
            raw_text,
            MAX_PROVIDER_RESULT_PREVIEW_CHARS,
        )
        if kind == "code_artifact":
            projected["artifact_handoff"] = (
                "Pass artifact_ref unchanged to file_patch_writer.artifact_ref; "
                "do not copy the bounded preview into generated_unit."
            )
    return projected, artifact_ref


def _result_summary(result: Any) -> Any:
    if isinstance(result, Mapping):
        return dict(result)
    return metadata_summary(result)


def _first_text(summary: Mapping[str, Any]) -> str | None:
    for field in (
        "content",
        "code",
        "stdout",
        "stderr",
        "text",
        "research_summary",
    ):
        value = summary.get(field)
        if isinstance(value, str):
            return value
    return None


def _artifact_reference(
    summary: Mapping[str, Any],
    *,
    raw_text: str | None,
    kind: str,
    source_id: str,
    provider_call_id: str,
) -> dict[str, Any] | None:
    if raw_text is None:
        return None
    encoded = raw_text.encode("utf-8")
    reference: dict[str, Any] = {
        "kind": kind or "tool_result_artifact",
        "source_id": source_id,
        "provider_call_id": provider_call_id,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "chars": len(raw_text),
    }
    file_path = summary.get("file_path")
    if file_path not in (None, ""):
        reference["file_path"] = _bounded_text(file_path, 4_096)
    if kind == "code_artifact":
        reference["language"] = _bounded_text(
            summary.get("language") or "python",
            64,
        )
    return reference


def _project_scalar_fields(
    summary: Mapping[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in (
        "file_path",
        "size_bytes",
        "bytes_written",
        "file_type",
        "lines_read",
        "total_lines",
        "truncated",
        "title",
        "content_type",
        "exit_code",
        "success",
        "count",
        "provider",
    ):
        value = summary.get(key)
        if value not in (None, "", [], {}):
            projected[key] = _bounded_scalar(value)
    files = summary.get("files")
    if files not in (None, [], ()):
        projected["files"] = _bounded_files(files)
    read_window = summary.get("read_window")
    if read_window not in (None, {}):
        projected["read_window"] = _bounded_read_window(read_window)
    if kind:
        projected = {"kind": kind, **projected}
    return projected


def _bounded_files(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProviderToolResultProjectionError(
            "tool result files must be a bounded sequence"
        )
    if len(value) > MAX_PROVIDER_RESULT_FILES:
        raise ProviderToolResultProjectionError(
            "tool result files exceed the projection item limit"
        )
    total_chars = 0
    bounded: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ProviderToolResultProjectionError(
                "tool result files must contain only strings"
            )
        total_chars += len(item)
        if total_chars > MAX_PROVIDER_RESULT_ARTIFACT_CHARS:
            raise ProviderToolResultProjectionError(
                "tool result files exceed the projection character limit"
            )
        bounded.append(_bounded_text(item, 4_096))
    return bounded


def _bounded_read_window(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderToolResultProjectionError(
            "tool result read_window must be a mapping"
        )
    if len(value) > 16:
        raise ProviderToolResultProjectionError(
            "tool result read_window exceeds the projection item limit"
        )
    bounded: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise ProviderToolResultProjectionError(
                "tool result read_window keys must be bounded strings"
            )
        bounded[key] = _bounded_scalar(item)
    return bounded


def _bounded_scalar(value: Any) -> str | int | float | bool:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str):
        return _bounded_text(value, 4_096)
    if isinstance(value, float) and not math.isfinite(value):
        raise ProviderToolResultProjectionError(
            "tool result projection floats must be finite"
        )
    if type(value) in (bool, int, float):
        return value
    raise ProviderToolResultProjectionError(
        "tool result projection fields must contain scalar values"
    )


def _is_complete_file_result(summary: Mapping[str, Any]) -> bool:
    if bool(summary.get("truncated")):
        return False
    total_lines = summary.get("total_lines")
    lines_read = summary.get("lines_read")
    if total_lines is not None and lines_read is not None:
        try:
            return int(lines_read) >= int(total_lines)
        except (TypeError, ValueError) as exc:
            raise ProviderToolResultProjectionError(
                "file result line counts must be integers"
            ) from exc
    return True


def _normalized_kind(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    if value in (None, ""):
        return ""
    return _bounded_text(value, 128)


def _validated_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderToolResultProjectionError(
            f"{field} must be a non-empty string"
        )
    if len(value) > MAX_PROVIDER_RESULT_ID_CHARS:
        raise ProviderToolResultProjectionError(
            f"{field} exceeds the projection identity limit"
        )
    return value


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    suffix = "...[truncated]"
    return text[: max(0, limit - len(suffix))] + suffix


__all__ = [
    "MAX_PROVIDER_RESULT_ARTIFACT_CHARS",
    "MAX_PROVIDER_RESULT_FILES",
    "MAX_PROVIDER_RESULT_ID_CHARS",
    "MAX_PROVIDER_RESULT_PREVIEW_CHARS",
    "ProviderToolResultProjectionError",
    "project_provider_tool_result",
]
