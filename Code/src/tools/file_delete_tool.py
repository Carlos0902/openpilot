"""File Delete Tool - Delete one local file through the file mutation protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from memory.project_path_resolver import ensure_resolved_path
from metadata import ToolContractMetadata, ToolInputMetadata, ToolResultMetadata, metadata_tool_result

from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition, ToolFailureMode
from tools.file_indexing import refresh_after_file_delete


FILE_DELETE_TOOL_DEFINITION = ToolDefinition(
    name="file_delete_tool",
    display_name="File Delete Tool",
    description="Delete one local file and refresh its deterministic project index",
    version="1.0.0",
    capabilities=[ToolCapability.FILE_DELETE, ToolCapability.FILE_WRITE],
    permission_level=PermissionLevel.HIGH,
    contract_metadata=ToolContractMetadata(
        tool_name="file_delete_tool",
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=["file_path"],
        input_defaults={"operation_kind": "delete_file"},
    ),
    timeout_seconds=30,
    max_retries=0,
    failure_modes=[
        ToolFailureMode(
            error_type="file_not_found",
            description="Target file does not exist",
            recovery_strategy="Read the project sketch or directory before deleting.",
        ),
        ToolFailureMode(
            error_type="not_a_file",
            description="Target path is not a regular file",
            recovery_strategy="Use a dedicated directory operation instead of file_delete_tool.",
        ),
        ToolFailureMode(
            error_type="permission_denied",
            description="No permission to delete the file",
            recovery_strategy="Check file permissions or ask the user for confirmation.",
        ),
    ],
    tags=["file", "delete", "local", "io", "index"],
    audit_required=True,
)


@metadata_tool_result("file_delete_tool")
def file_delete_tool_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    params = input_metadata.to_params()
    project_path = params.get("project_path")
    file_path = (
        ensure_resolved_path(
            params["file_path"],
            project_path,
            operation="delete",
            intent_kind="existing_file",
        )
        if project_path
        else Path(str(params["file_path"])).expanduser()
    )
    if not file_path.exists():
        raise FileNotFoundError(f"Delete target file not found: {file_path}")
    if not file_path.is_file():
        raise IsADirectoryError(f"file_delete_tool only deletes files, not directories: {file_path}")

    absolute = file_path.absolute()
    bytes_deleted = file_path.stat().st_size
    try:
        file_path.unlink()
    except PermissionError as exc:
        raise PermissionError(f"No permission to delete file: {file_path}") from exc
    except OSError as exc:
        raise OSError(f"Failed to delete file: {exc}") from exc

    index_update: dict[str, Any] = {}
    warnings: list[str] = []
    try:
        index_update = refresh_after_file_delete(file_path)
    except Exception as exc:
        warnings.append(f"File index refresh failed: {exc}")

    return {
        "file_path": str(absolute),
        "bytes_deleted": bytes_deleted,
        "created": False,
        "deleted": True,
        "operation_kind": str(params.get("operation_kind") or "delete_file"),
        "index_update": index_update,
        "warnings": warnings,
    }
