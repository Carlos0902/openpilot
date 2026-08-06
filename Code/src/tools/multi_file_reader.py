"""Multi File Reader Tool - Read and combine contents from multiple local files."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from memory.project_index import ProjectIndexManager
from memory.project_path_resolver import ensure_resolved_path
from metadata import ToolContractMetadata, ToolInputMetadata, ToolResultMetadata, metadata_tool_result

from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
    ToolFailureMode,
)


MULTI_FILE_READER_DEFINITION = ToolDefinition(
    name="multi_file_reader",
    display_name="Multi File Reader",
    description="Read and combine contents from multiple local files",
    version="1.0.0",
    capabilities=[ToolCapability.FILE_READ],
    permission_level=PermissionLevel.LOW,
    contract_metadata=ToolContractMetadata(
        tool_name='multi_file_reader',
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=[],
        required_any_of=[["file_paths"], ["directory_path"]],
        input_defaults={'pattern': '*完成报告*.md', 'encoding': 'utf-8', 'max_total_chars': 50000},
    ),
    timeout_seconds=60,
    max_retries=2,
    failure_modes=[
        ToolFailureMode(
            error_type="file_not_found",
            description="One or more files do not exist",
            recovery_strategy="Check file paths and retry"
        ),
        ToolFailureMode(
            error_type="encoding_error",
            description="Cannot decode a file with specified encoding",
            recovery_strategy="Try a different encoding"
        ),
    ],
    tags=["file", "read", "batch", "local", "io"],
    audit_required=True,
)


@metadata_tool_result('multi_file_reader')
def multi_file_reader_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    params = input_metadata.to_params()
    """Read multiple files and combine them into one text payload."""
    project_path = params.get("project_path")
    file_paths = params.get("file_paths") or params.get("files")
    sketch_files: list[str] = []
    if not file_paths:
        directory_path_value = params.get("directory_path")
        if not directory_path_value:
            raise ValueError("multi_file_reader requires file_paths or directory_path")
        directory_path = (
            ensure_resolved_path(
                directory_path_value,
                project_path,
                operation="read",
                intent_kind="existing_directory",
            )
            if project_path
            else Path(directory_path_value)
        )
        if not directory_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory_path}")
        if not directory_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory_path}")

        sketch_files.append(_ensure_directory_sketch(directory_path))
        pattern = params.get("pattern", "*完成报告*.md")
        recursive = params.get("recursive", False)
        max_files = params.get("max_files", 100)
        iterator = directory_path.rglob(pattern) if recursive else directory_path.glob(pattern)
        file_paths = sorted(str(path) for path in iterator if path.is_file())[:max_files]
    else:
        if isinstance(file_paths, str):
            file_paths = [file_paths]
        if project_path:
            file_paths = [
                str(
                    ensure_resolved_path(
                        file_path,
                        project_path,
                        operation="read",
                        intent_kind="existing_file",
                    )
                )
                for file_path in file_paths
            ]
        sketch_files.extend(_ensure_parent_sketches(file_paths))

    encoding = params.get("encoding", "utf-8")
    max_total_chars = params.get("max_total_chars", 50000)
    combined_parts: list[str] = []
    read_files: list[str] = []
    skipped_files: list[dict[str, Any]] = []
    total_chars = 0
    truncated = False

    for file_path_value in file_paths:
        file_path = Path(file_path_value)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if not file_path.is_file():
            continue

        content_result = _read_text_safely(file_path, str(encoding))
        if content_result["skipped"]:
            skipped_files.append(
                {
                    "path": str(file_path),
                    "reason": content_result["reason"],
                    "size_bytes": file_path.stat().st_size,
                    "mime_type": content_result.get("mime_type") or "",
                }
            )
            continue
        content = str(content_result["content"])
        header = f"\n\n# Source: {file_path}\n\n"
        remaining = max_total_chars - total_chars
        if remaining <= 0:
            truncated = True
            break

        chunk = header + content
        if len(chunk) > remaining:
            chunk = chunk[:remaining]
            truncated = True

        combined_parts.append(chunk)
        read_files.append(str(file_path))
        total_chars += len(chunk)

        if truncated:
            break

    return {
        "content": "".join(combined_parts).strip(),
        "files": read_files,
        "skipped_files": skipped_files,
        "count": len(read_files),
        "truncated": truncated,
        "encoding": encoding,
        "sketch_files": list(dict.fromkeys(sketch_files)),
        "sketch_refreshed": bool(sketch_files),
    }


def _ensure_directory_sketch(directory_path: Path) -> str:
    """Refresh sketch.json before reading any directory, including empty folders."""
    manager = ProjectIndexManager.for_path(directory_path)
    sketch = manager.update_directory_sketch(directory_path)
    return str(Path(sketch.directory) / ProjectIndexManager.SKETCH_NAME)


def _ensure_parent_sketches(file_paths: Any) -> list[str]:
    sketch_files: list[str] = []
    parents: list[Path] = []
    for raw_path in file_paths or []:
        path = Path(raw_path).expanduser()
        parent = path.parent
        if parent not in parents and parent.exists() and parent.is_dir():
            parents.append(parent)
    for parent in parents:
        sketch_files.append(_ensure_directory_sketch(parent))
    return sketch_files


def _read_text_safely(file_path: Path, encoding: str) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(str(file_path))[0] or ""
    if _looks_binary(file_path, mime_type):
        return {
            "skipped": True,
            "reason": "binary_file",
            "content": "",
            "mime_type": mime_type,
        }
    encodings = [encoding, "utf-8-sig", "latin-1", "cp1252", "iso-8859-1"]
    tried: set[str] = set()
    last_error = ""
    for candidate in encodings:
        if candidate in tried:
            continue
        tried.add(candidate)
        try:
            content = file_path.read_text(encoding=candidate)
            return {
                "skipped": False,
                "reason": "",
                "content": content,
                "encoding": candidate,
                "mime_type": mime_type,
            }
        except UnicodeDecodeError as exc:
            last_error = str(exc)
            continue
    return {
        "skipped": True,
        "reason": f"encoding_error:{last_error}",
        "content": "",
        "mime_type": mime_type,
    }


def _looks_binary(file_path: Path, mime_type: str) -> bool:
    if mime_type and not mime_type.startswith("text/") and mime_type not in {
        "application/json",
        "application/javascript",
        "application/xml",
        "application/x-sh",
    }:
        return True
    try:
        sample = file_path.read_bytes()[:4096]
    except OSError:
        return False
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    control_bytes = sum(1 for byte in sample if byte < 32 and byte not in {9, 10, 12, 13})
    return control_bytes / len(sample) > 0.30
