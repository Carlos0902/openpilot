"""Canonical identity for provider-native tool-call replay detection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from core.llm import LLMToolCall
from core.provider_tool_admission import (
    MAX_PROVIDER_TOOL_ARGUMENT_CHARS,
    decode_provider_tool_arguments,
)

MAX_PROVIDER_SIGNATURE_DEPTH = 16
MAX_PROVIDER_SIGNATURE_ITEMS = 1024

_PATH_FIELDS = {"file_path", "directory_path", "file_paths"}


class ProviderToolSignatureError(ValueError):
    """Raised when one call cannot be canonicalized within static bounds."""


def provider_tool_call_signature(
    call: LLMToolCall,
    *,
    project_path: str | None = None,
) -> str:
    """Return a stable SHA-256 identity without using the provider call ID."""

    raw_arguments = str(call.function.arguments or "").strip()
    try:
        arguments, argument_error = decode_provider_tool_arguments(call)
    except (RecursionError, ValueError):
        arguments = {}
        argument_error = "provider arguments exceeded decoder bounds"

    if argument_error:
        bounded_raw = raw_arguments[:MAX_PROVIDER_TOOL_ARGUMENT_CHARS]
        canonical_arguments: Any = {
            "_invalid_arguments": {
                "sha256": hashlib.sha256(
                    bounded_raw.encode("utf-8")
                ).hexdigest(),
                "chars": len(raw_arguments),
            }
        }
    else:
        item_count = [0]
        canonical_arguments = _canonicalize_provider_arguments(
            arguments,
            project_path=project_path,
            item_count=item_count,
        )

    payload = {
        "tool": call.function.name,
        "arguments": canonical_arguments,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonicalize_provider_arguments(
    value: Any,
    *,
    project_path: str | None,
    item_count: list[int],
    key: str = "",
    depth: int = 0,
) -> Any:
    if depth > MAX_PROVIDER_SIGNATURE_DEPTH:
        raise ProviderToolSignatureError(
            "provider tool arguments exceed the signature depth limit"
        )
    if isinstance(value, dict):
        _consume_items(item_count, len(value))
        return {
            str(name): _canonicalize_provider_arguments(
                child,
                project_path=project_path,
                item_count=item_count,
                key=str(name),
                depth=depth + 1,
            )
            for name, child in sorted(
                value.items(),
                key=lambda item: str(item[0]),
            )
        }
    if isinstance(value, list):
        _consume_items(item_count, len(value))
        items = [
            _canonicalize_provider_arguments(
                child,
                project_path=project_path,
                item_count=item_count,
                key=key,
                depth=depth + 1,
            )
            for child in value
        ]
        if key == "file_paths":
            return sorted(
                items,
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        return items
    if isinstance(value, str) and key in _PATH_FIELDS:
        return _canonical_path(value, project_path)
    return value


def _consume_items(item_count: list[int], amount: int) -> None:
    item_count[0] += amount
    if item_count[0] > MAX_PROVIDER_SIGNATURE_ITEMS:
        raise ProviderToolSignatureError(
            "provider tool arguments exceed the signature item limit"
        )


def _canonical_path(raw_path: str, project_path: str | None) -> str:
    path = Path(raw_path).expanduser()
    if not path.is_absolute() and project_path:
        path = Path(project_path).expanduser() / path
    return str(path.resolve(strict=False))


__all__ = [
    "MAX_PROVIDER_SIGNATURE_DEPTH",
    "MAX_PROVIDER_SIGNATURE_ITEMS",
    "ProviderToolSignatureError",
    "provider_tool_call_signature",
]
