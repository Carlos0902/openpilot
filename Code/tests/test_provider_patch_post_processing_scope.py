from __future__ import annotations

from pathlib import Path

from memory.project_index import ProjectIndexManager
from metadata import ToolInputMetadata
from tools.file_patch_writer import file_patch_writer_executor


def _patch(
    target: Path,
    *,
    post_processing_scope: object = None,
):
    values = {
        "file_path": str(target),
        "operation_kind": "add_symbol",
        "generated_unit": "def added():\n    return 2",
    }
    if post_processing_scope is not None:
        values["_post_processing_write_scope"] = post_processing_scope
    return file_patch_writer_executor(
        ToolInputMetadata.from_mapping("file_patch_writer", values)
    )


def _targets(target: Path) -> tuple[Path, Path]:
    manager = ProjectIndexManager.for_path(target)
    return (
        manager.index_file_for(target).resolve(),
        (target.parent / ProjectIndexManager.SKETCH_NAME).resolve(),
    )


def test_provider_scope_with_only_primary_target_skips_derived_writes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def existing():\n    return 1\n", encoding="utf-8")
    index_file, sketch_file = _targets(target)

    result = _patch(
        target,
        post_processing_scope=[str(target.resolve())],
    )

    assert "def added():" in target.read_text(encoding="utf-8")
    assert not index_file.exists()
    assert not sketch_file.exists()
    assert result.result.attributes["index_update"] == {
        "skipped": True,
        "reason": "post_processing_targets_outside_declared_write_scope",
    }


def test_provider_scope_with_partial_derived_authority_skips_all_derived_writes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def existing():\n    return 1\n", encoding="utf-8")
    index_file, sketch_file = _targets(target)

    result = _patch(
        target,
        post_processing_scope=[str(index_file)],
    )

    assert not index_file.exists()
    assert not sketch_file.exists()
    assert result.result.attributes["index_update"]["skipped"] is True


def test_provider_scope_refreshes_only_when_every_derived_target_is_authorized(
    tmp_path: Path,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def existing():\n    return 1\n", encoding="utf-8")
    index_file, sketch_file = _targets(target)

    result = _patch(
        target,
        post_processing_scope=[str(index_file), str(sketch_file)],
    )

    assert index_file.exists()
    assert sketch_file.exists()
    assert result.result.attributes["index_update"]["index_file"] == str(
        index_file
    )


def test_unscoped_local_patch_preserves_existing_index_refresh_behavior(
    tmp_path: Path,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def existing():\n    return 1\n", encoding="utf-8")
    index_file, sketch_file = _targets(target)

    result = _patch(target)

    assert index_file.exists()
    assert sketch_file.exists()
    assert result.result.attributes["index_update"]["index_file"] == str(
        index_file
    )
