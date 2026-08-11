from __future__ import annotations

from pathlib import Path

from core.provider_tool_admission import (
    provider_read_scope_error,
    provider_write_scope_error,
)
from metadata import ToolInputMetadata


def _input(**updates) -> ToolInputMetadata:
    values = {"tool_name": "file_reader", "file_path": "README.md"}
    values.update(updates)
    return ToolInputMetadata(**values)


def test_read_scope_accepts_only_explicit_canonical_path(tmp_path) -> None:
    target = tmp_path / "README.md"
    target.write_text("hello", encoding="utf-8")

    assert (
        provider_read_scope_error(
            _input(file_path="README.md"),
            [str(target)],
            str(tmp_path),
        )
        is None
    )
    assert "outside path" in str(
        provider_read_scope_error(
            _input(file_path="other.md"),
            [str(target)],
            str(tmp_path),
        )
    )


def test_read_scope_empty_list_grants_no_file_authority(tmp_path) -> None:
    assert "non-empty explicit" in str(
        provider_read_scope_error(
            _input(file_path="README.md"),
            [],
            str(tmp_path),
        )
    )


def test_read_scope_rejects_scope_or_request_outside_project(tmp_path) -> None:
    outside = tmp_path.parent / "outside.txt"

    assert "outside the project root" in str(
        provider_read_scope_error(
            _input(file_path="README.md"),
            [str(outside)],
            str(tmp_path),
        )
    )
    assert "outside the project root" in str(
        provider_read_scope_error(
            _input(file_path=str(outside)),
            [str(tmp_path / "README.md")],
            str(tmp_path),
        )
    )


def test_scope_rejects_symlink_paths(tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("hello", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    assert "symlink" in str(
        provider_read_scope_error(
            _input(file_path=str(link)),
            [str(link)],
            str(tmp_path),
        )
    )


def test_write_scope_requires_explicit_target_and_exact_match(tmp_path) -> None:
    target = tmp_path / "app.py"

    assert "non-empty explicit" in str(
        provider_write_scope_error(
            _input(tool_name="file_writer", file_path="app.py"),
            [],
            str(tmp_path),
        )
    )
    assert "did not provide" in str(
        provider_write_scope_error(
            _input(tool_name="file_writer", file_path=None),
            [str(target)],
            str(tmp_path),
        )
    )
    assert (
        provider_write_scope_error(
            _input(tool_name="file_writer", file_path="app.py"),
            [str(target)],
            str(tmp_path),
        )
        is None
    )
    assert "outside path" in str(
        provider_write_scope_error(
            _input(tool_name="file_writer", file_path="other.py"),
            [str(target)],
            str(tmp_path),
        )
    )


def test_scope_path_sets_are_bounded(tmp_path) -> None:
    paths = [str(tmp_path / f"file_{index}.txt") for index in range(65)]

    assert "at most 64" in str(
        provider_read_scope_error(
            _input(file_paths=paths),
            paths,
            str(tmp_path),
        )
    )


def test_relative_parent_escape_is_rejected(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    allowed = project / "README.md"

    error = provider_write_scope_error(
        _input(tool_name="file_writer", file_path="../escape.py"),
        [str(allowed)],
        str(project),
    )

    assert "outside the project root" in str(error)
    assert str(Path(project.parent / "escape.py").resolve()) in str(error)
