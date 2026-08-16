"""Typed, shell-aware comparison for task-owned validation commands."""

from __future__ import annotations

import shlex


def normalize_command_argv(command: str | None) -> tuple[str, ...] | None:
    """Parse command text for comparison without granting shell-wrapper equivalence."""

    try:
        return tuple(shlex.split(str(command or "").strip()))
    except ValueError:
        return None


def validation_commands_match(expected: str | None, actual: str | None) -> bool:
    """Return whether provider text is argv-equivalent to the task-owned fact."""

    if not str(expected or "").strip():
        return False
    expected_argv = normalize_command_argv(expected)
    actual_argv = normalize_command_argv(actual)
    return expected_argv is not None and actual_argv == expected_argv
