"""Authoritative classification and targets for file-mutating tool calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any


FILE_MUTATION_TOOLS = {
    "file_writer",
    "file_patch_writer",
    "file_delete_tool",
    "readme_tool",
    "bug_fix_tool",
}


def file_mutation_targets(selection: Any) -> list[str]:
    """Return the explicit filesystem targets owned by a mutating selection."""
    metadata = selection.input_metadata
    if selection.tool_name in {"file_writer", "file_patch_writer", "file_delete_tool"}:
        return [str(metadata.file_path)] if metadata.file_path else []
    if selection.tool_name == "readme_tool" and metadata.project_path:
        return [str(Path(metadata.project_path) / "README.md")]
    if selection.tool_name == "bug_fix_tool":
        return [str(path) for path in metadata.file_paths]
    return []
