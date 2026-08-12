from __future__ import annotations

import pytest

from core.provider_tool_surface_policy import (
    ProviderToolSurfaceError,
    provider_tool_surface,
)


def test_finalization_hides_all_tools_and_tool_choice() -> None:
    surface = provider_tool_surface(
        tool_names=["file_reader", "file_patch_writer", "command_executor"],
        finalization_pending=True,
        post_mutation_active=False,
        mutation_tools_exposed=True,
        all_scoped_reads_complete=True,
    )

    assert surface.tool_names == ()
    assert surface.tool_choice is None


def test_post_mutation_exposes_only_command_executor() -> None:
    surface = provider_tool_surface(
        tool_names=["file_reader", "file_patch_writer", "command_executor"],
        finalization_pending=False,
        post_mutation_active=True,
        mutation_tools_exposed=True,
        all_scoped_reads_complete=True,
    )

    assert surface.tool_names == ("command_executor",)
    assert surface.tool_choice == "required"


def test_completed_mutation_reads_hide_reader_and_command() -> None:
    surface = provider_tool_surface(
        tool_names=["file_reader", "code_unit_generator", "file_patch_writer", "command_executor"],
        finalization_pending=False,
        post_mutation_active=False,
        mutation_tools_exposed=True,
        all_scoped_reads_complete=True,
    )

    assert surface.tool_names == ("code_unit_generator", "file_patch_writer")
    assert surface.tool_choice == "required"


def test_pre_mutation_keeps_declared_surface() -> None:
    surface = provider_tool_surface(
        tool_names=["file_reader", "code_unit_generator", "file_patch_writer", "command_executor"],
        finalization_pending=False,
        post_mutation_active=False,
        mutation_tools_exposed=True,
        all_scoped_reads_complete=False,
    )

    assert surface.tool_names == (
        "file_reader",
        "code_unit_generator",
        "file_patch_writer",
        "command_executor",
    )
    assert surface.tool_choice == "required"


def test_empty_surface_does_not_require_tool_call() -> None:
    surface = provider_tool_surface(
        tool_names=[],
        finalization_pending=False,
        post_mutation_active=False,
        mutation_tools_exposed=False,
        all_scoped_reads_complete=False,
    )

    assert surface.tool_names == ()
    assert surface.tool_choice is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"tool_names": "file_reader"},
        {"tool_names": ["file_reader", "file_reader"]},
        {"tool_names": [""]},
        {"finalization_pending": 1},
        {"post_mutation_active": "yes"},
        {"mutation_tools_exposed": None},
        {"all_scoped_reads_complete": []},
    ],
)
def test_surface_rejects_invalid_facts(overrides) -> None:
    values = {
        "tool_names": ["file_reader"],
        "finalization_pending": False,
        "post_mutation_active": False,
        "mutation_tools_exposed": False,
        "all_scoped_reads_complete": False,
    }
    values.update(overrides)
    with pytest.raises(ProviderToolSurfaceError):
        provider_tool_surface(**values)
