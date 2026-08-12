"""Derive the provider-visible tool surface for one conversation phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_COMMAND_TOOL = "command_executor"
_READ_TOOL = "file_reader"


class ProviderToolSurfaceError(ValueError):
    """Raised when provider tool-surface facts are malformed."""


@dataclass(frozen=True)
class ProviderToolSurface:
    """Immutable provider-visible names and tool-choice mode."""

    tool_names: tuple[str, ...]
    tool_choice: str | None


def provider_tool_surface(
    *,
    tool_names: Any,
    finalization_pending: bool,
    post_mutation_active: bool,
    mutation_tools_exposed: bool,
    all_scoped_reads_complete: bool,
) -> ProviderToolSurface:
    """Return the only tool surface legal for the current typed phase facts."""

    names = _validated_names(tool_names)
    for label, value in (
        ("finalization_pending", finalization_pending),
        ("post_mutation_active", post_mutation_active),
        ("mutation_tools_exposed", mutation_tools_exposed),
        ("all_scoped_reads_complete", all_scoped_reads_complete),
    ):
        if type(value) is not bool:
            raise ProviderToolSurfaceError(f"{label} must be a literal boolean")

    if finalization_pending:
        visible = ()
    elif post_mutation_active:
        visible = tuple(name for name in names if name == _COMMAND_TOOL)
    elif mutation_tools_exposed and all_scoped_reads_complete:
        visible = tuple(
            name for name in names if name not in {_READ_TOOL, _COMMAND_TOOL}
        )
    else:
        visible = names
    return ProviderToolSurface(
        tool_names=visible,
        tool_choice="required" if visible else None,
    )


def _validated_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ProviderToolSurfaceError("tool_names must be a bounded list or tuple")
    if len(value) > 64:
        raise ProviderToolSurfaceError("tool_names exceed the provider surface limit")
    names: list[str] = []
    for index, name in enumerate(value):
        if not isinstance(name, str) or not name.strip():
            raise ProviderToolSurfaceError(
                f"tool_names[{index}] must be a non-empty string"
            )
        normalized = name.strip()
        if len(normalized) > 128:
            raise ProviderToolSurfaceError(
                f"tool_names[{index}] exceeds the name limit"
            )
        if normalized in names:
            raise ProviderToolSurfaceError("tool_names must be unique")
        names.append(normalized)
    return tuple(names)


__all__ = [
    "ProviderToolSurface",
    "ProviderToolSurfaceError",
    "provider_tool_surface",
]
