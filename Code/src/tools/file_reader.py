"""File Reader Tool - Read contents from a local file."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from memory.project_path_resolver import ensure_resolved_path
from metadata import ToolContractMetadata, ToolInputMetadata, ToolResultMetadata, metadata_tool_result

from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
    ToolFailureMode,
)

FILE_TYPE_RULES: dict[str, dict[str, Any]] = {
    "code": {
        "extensions": {
            ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".cpp", ".c", ".h",
            ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala",
            ".sh", ".bash", ".zsh", ".fish",
        },
        "read_full": True,
        "max_lines": None,
        "max_size_mb": 5.0,
    },
    "database": {
        "extensions": {".sql", ".db", ".sqlite", ".sqlite3"},
        "read_full": False,
        "max_lines": 50,
        "max_size_mb": 1.0,
    },
    "config": {
        "extensions": {
            ".json", ".yaml", ".yml", ".toml", ".ini", ".conf", ".cfg",
            ".xml", ".env", ".properties",
        },
        "read_full": True,
        "max_lines": None,
        "max_size_mb": 1.0,
    },
    "log": {
        "extensions": {".log", ".out", ".err"},
        "read_full": False,
        "max_lines": 100,
        "max_size_mb": 2.0,
    },
    "data": {
        "extensions": {".csv", ".tsv", ".dat", ".txt"},
        "read_full": False,
        "max_lines": 20,
        "max_size_mb": 1.0,
    },
    "binary": {
        "extensions": {
            ".exe", ".dll", ".so", ".dylib", ".bin",
            ".zip", ".tar", ".gz", ".bz2", ".7z",
            ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico",
            ".mp3", ".mp4", ".avi", ".mov", ".wav",
            ".pdf", ".doc", ".docx", ".xls", ".xlsx",
        },
        "read_full": False,
        "max_lines": 0,
        "max_size_mb": 0.0,
    },
}

READ_MODES = {"full", "adaptive", "sample", "tail"}


FILE_READER_DEFINITION = ToolDefinition(
    name="file_reader",
    display_name="File Reader",
    description="Read contents from a local file",
    version="1.0.0",
    capabilities=[ToolCapability.FILE_READ],
    permission_level=PermissionLevel.LOW,
    contract_metadata=ToolContractMetadata(
        tool_name='file_reader',
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=['file_path'],
        input_defaults={'encoding': 'utf-8', 'max_size_mb': 10, 'read_mode': 'full', 'max_lines': None, 'offset': 0},
    ),
    timeout_seconds=30,
    max_retries=2,
    failure_modes=[
        ToolFailureMode(
            error_type="file_not_found",
            description="File does not exist",
            recovery_strategy="Check file path and try again"
        ),
        ToolFailureMode(
            error_type="permission_denied",
            description="No permission to read file",
            recovery_strategy="Check file permissions"
        ),
        ToolFailureMode(
            error_type="file_too_large",
            description="File exceeds maximum size",
            recovery_strategy="Increase max_size_mb or read file in chunks"
        ),
        ToolFailureMode(
            error_type="encoding_error",
            description="Cannot decode file with specified encoding",
            recovery_strategy="Try different encoding (e.g., latin-1, gbk)"
        )
    ],
    tags=["file", "read", "local", "io"],
    audit_required=True
)


@metadata_tool_result('file_reader')
def file_reader_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    params = input_metadata.to_params()
    """
    Execute file reader tool.

    Args:
        params: Tool parameters (file_path, encoding, max_size_mb, read_mode, max_lines, offset)

    Returns:
        Dictionary with content, size_bytes, encoding, file_type, line counts, and read attributes

    Raises:
        FileNotFoundError: If file doesn't exist
        PermissionError: If no read permission
        ValueError: If file too large or encoding error
    """
    project_path = params.get("project_path")
    file_path = (
        ensure_resolved_path(
            params["file_path"],
            project_path,
            operation="read",
            intent_kind="existing_file",
        )
        if project_path
        else Path(params["file_path"])
    )
    encoding = params.get("encoding", "utf-8")
    max_size_mb = params.get("max_size_mb", 10)
    read_mode = str(params.get("read_mode", "full") or "full").lower()
    max_lines = params.get("max_lines")
    offset = int(params.get("offset", 0) or 0)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Not a file: {file_path}")
    if read_mode not in READ_MODES:
        raise ValueError(f"Invalid read_mode: {read_mode}. Use full, adaptive, sample, or tail.")

    size_bytes = file_path.stat().st_size
    file_type = _detect_file_type(file_path)
    strategy = _read_strategy(file_type)
    effective_max_size_mb = max_size_mb
    if read_mode == "adaptive" and strategy.get("max_size_mb") is not None:
        effective_max_size_mb = min(float(max_size_mb), float(strategy["max_size_mb"]))
    max_size_bytes = max_size_mb * 1024 * 1024
    if effective_max_size_mb is not None:
        max_size_bytes = float(effective_max_size_mb) * 1024 * 1024
    if size_bytes > max_size_bytes:
        raise ValueError(
            f"File too large: {size_bytes} bytes "
            f"(max: {max_size_bytes} bytes)"
        )

    if file_type == "binary":
        return _binary_result(file_path, size_bytes)

    if read_mode == "adaptive":
        read_full = bool(strategy.get("read_full", False))
        if read_full:
            return _read_text_file(file_path, encoding, size_bytes, file_type)
        limit = _coerce_line_limit(max_lines, strategy.get("max_lines") or 100)
        return _read_text_file(
            file_path,
            encoding,
            size_bytes,
            file_type,
            max_lines=limit,
            offset=offset,
            attributes={"read_mode": "adaptive"},
        )
    if read_mode == "sample":
        return _read_text_file(
            file_path,
            encoding,
            size_bytes,
            file_type,
            max_lines=_coerce_line_limit(max_lines, 10),
            offset=offset,
            attributes={"read_mode": "sample"},
        )
    if read_mode == "tail":
        return _read_text_tail(
            file_path,
            encoding,
            size_bytes,
            file_type,
            max_lines=_coerce_line_limit(max_lines, 100),
        )

    return _read_text_file(file_path, encoding, size_bytes, file_type)


def _detect_file_type(file_path: Path) -> str:
    extension = file_path.suffix.lower()
    for file_type, rules in FILE_TYPE_RULES.items():
        if extension in rules["extensions"]:
            return file_type

    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type:
        if mime_type.startswith("text/"):
            return "text"
        if mime_type.startswith("application/"):
            return "binary"
    return "unknown"


def _read_strategy(file_type: str) -> dict[str, Any]:
    return FILE_TYPE_RULES.get(
        file_type,
        {"read_full": False, "max_lines": 100, "max_size_mb": 1.0},
    )


def _coerce_line_limit(value: Any, default: int) -> int:
    if value is None:
        return default
    limit = int(value)
    if limit < 0:
        raise ValueError("max_lines must be greater than or equal to 0")
    return limit


def _read_text_file(
    file_path: Path,
    encoding: str,
    size_bytes: int,
    file_type: str,
    *,
    max_lines: int | None = None,
    offset: int = 0,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if offset < 0:
        raise ValueError("offset must be greater than or equal to 0")

    try:
        lines, used_encoding, fallback = _read_lines_with_fallback(file_path, encoding)
    except PermissionError as e:
        raise PermissionError(f"No permission to read file: {file_path}") from e
    except UnicodeDecodeError as e:
        raise ValueError(
            f"Cannot decode file with encoding '{encoding}': {e}"
        ) from e

    total_lines = len(lines)
    selected_lines = lines[offset:] if offset else lines
    truncated = False
    if max_lines is not None and len(selected_lines) > max_lines:
        selected_lines = selected_lines[:max_lines]
        truncated = True
    if offset and offset < total_lines:
        truncated = True
    content = "".join(selected_lines)
    result_attributes = dict(attributes or {})
    if fallback:
        result_attributes["encoding_fallback"] = True

    return {
        "content": content,
        "size_bytes": size_bytes,
        "encoding": used_encoding,
        "file_type": file_type,
        "lines_read": len(selected_lines),
        "total_lines": total_lines,
        "truncated": truncated,
        "attributes": result_attributes,
    }


def _read_text_tail(
    file_path: Path,
    encoding: str,
    size_bytes: int,
    file_type: str,
    *,
    max_lines: int,
) -> dict[str, Any]:
    try:
        lines, used_encoding, fallback = _read_lines_with_fallback(file_path, encoding)
    except PermissionError as e:
        raise PermissionError(f"No permission to read file: {file_path}") from e
    except UnicodeDecodeError:
        return {
            "content": "[Cannot decode file - possibly binary]",
            "size_bytes": size_bytes,
            "encoding": "unknown",
            "file_type": "binary",
            "lines_read": 0,
            "total_lines": 0,
            "truncated": False,
            "attributes": {"read_mode": "tail"},
        }

    total_lines = len(lines)
    selected_lines = lines[-max_lines:] if max_lines and total_lines > max_lines else lines
    attributes = {"read_mode": "tail"}
    if fallback:
        attributes["encoding_fallback"] = True
    return {
        "content": "".join(selected_lines),
        "size_bytes": size_bytes,
        "encoding": used_encoding,
        "file_type": file_type,
        "lines_read": len(selected_lines),
        "total_lines": total_lines,
        "truncated": total_lines > len(selected_lines),
        "attributes": attributes,
    }


def _read_lines_with_fallback(file_path: Path, encoding: str) -> tuple[list[str], str, bool]:
    encodings = [encoding, "latin-1", "cp1252", "iso-8859-1"]
    tried = set()
    first_error: UnicodeDecodeError | None = None
    for candidate in encodings:
        if candidate in tried:
            continue
        tried.add(candidate)
        try:
            return file_path.read_text(encoding=candidate).splitlines(keepends=True), candidate, candidate != encoding
        except UnicodeDecodeError as e:
            if first_error is None:
                first_error = e
            continue
    if first_error is not None:
        raise first_error
    return [], encoding, False


def _binary_result(file_path: Path, size_bytes: int) -> dict[str, Any]:
    return {
        "content": "[Binary file - content not displayed]",
        "size_bytes": size_bytes,
        "encoding": "binary",
        "file_type": "binary",
        "lines_read": 0,
        "total_lines": 0,
        "truncated": False,
        "attributes": {"mime_type": mimetypes.guess_type(str(file_path))[0]},
    }
