"""File Writer Tool - Write contents to a local file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from memory.project_path_resolver import ensure_resolved_path
from metadata import ToolContractMetadata, ToolInputMetadata, ToolResultMetadata, metadata_tool_result

from core.python_requirements import invalid_requirement_lines, is_requirements_file
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
    ToolFailureMode,
)
from tools.file_indexing import refresh_after_file_change


FILE_WRITER_DEFINITION = ToolDefinition(
    name="file_writer",
    display_name="File Writer",
    description="Write contents to a local file",
    version="1.0.0",
    capabilities=[ToolCapability.FILE_WRITE],
    permission_level=PermissionLevel.MEDIUM,
    contract_metadata=ToolContractMetadata(
        tool_name='file_writer',
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=['file_path', 'content'],
        input_defaults={'encoding': 'utf-8', 'create_dirs': True, 'overwrite': True, 'operation_kind': 'create_file'},
    ),
    timeout_seconds=30,
    max_retries=2,
    failure_modes=[
        ToolFailureMode(
            error_type="permission_denied",
            description="No permission to write file",
            recovery_strategy="Check file/directory permissions"
        ),
        ToolFailureMode(
            error_type="file_exists",
            description="File exists and overwrite=False",
            recovery_strategy="Set overwrite=True or choose different path"
        ),
        ToolFailureMode(
            error_type="disk_full",
            description="Not enough disk space",
            recovery_strategy="Free up disk space or write to different location"
        )
    ],
    tags=["file", "write", "local", "io"],
    audit_required=True
)


@metadata_tool_result('file_writer')
def file_writer_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    params = input_metadata.to_params()
    """
    Execute file writer tool.

    Args:
        params: Tool parameters (file_path, content, encoding, create_dirs, overwrite)

    Returns:
        Dictionary with file_path, bytes_written, created

    Raises:
        PermissionError: If no write permission
        FileExistsError: If file exists and overwrite=False
        OSError: If disk full or other OS error
    """
    project_path = params.get("project_path")
    requested_operation = str(params.get("operation_kind") or "create_file").lower()
    intent_kind = "planned_new_file" if requested_operation in {"create_file", "file_create", "directory_generate"} else "existing_file"
    file_path = (
        ensure_resolved_path(
            params["file_path"],
            project_path,
            operation="write",
            intent_kind=intent_kind,
        )
        if project_path
        else Path(params["file_path"])
    )
    content = params["content"]
    encoding = params.get("encoding", "utf-8")
    create_dirs = params.get("create_dirs", True)
    overwrite = params.get("overwrite", True)
    operation_kind = requested_operation

    if is_requirements_file(file_path):
        invalid_lines = invalid_requirement_lines(str(content))
        if invalid_lines:
            preview = ", ".join(invalid_lines[:3])
            raise ValueError(
                "file_writer rejected malformed requirements content. "
                "A requirements file may contain dependency specifiers, pip directives, comments, and blank lines only. "
                f"Invalid line(s): {preview}"
            )

    # Check if file exists
    file_existed = file_path.exists()
    if file_existed and not overwrite:
        raise FileExistsError(
            f"File exists and overwrite=False: {file_path}"
        )
    if file_existed and operation_kind not in {"file_replace", "full_file_replace", "replace_file"}:
        raise FileExistsError(
            "file_writer refuses to overwrite an existing file without "
            f"operation_kind=file_replace: {file_path}"
        )

    # Create parent directories if needed
    if create_dirs and not file_path.parent.exists():
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise PermissionError(
                f"No permission to create directory: {file_path.parent}"
            ) from e

    # Write file
    try:
        file_path.write_text(content, encoding=encoding)
        bytes_written = file_path.stat().st_size
    except PermissionError as e:
        raise PermissionError(
            f"No permission to write file: {file_path}"
        ) from e
    except OSError as e:
        # Could be disk full or other OS error
        raise OSError(f"Failed to write file: {e}") from e

    index_update: dict[str, Any] = {}
    warnings: list[str] = []
    try:
        index_update = refresh_after_file_change(file_path)
    except Exception as exc:
        warnings.append(f"File index refresh failed: {exc}")

    return {
        "file_path": str(file_path.absolute()),
        "bytes_written": bytes_written,
        "created": not file_existed,
        "operation_kind": operation_kind,
        "index_update": index_update,
        "warnings": warnings,
    }
