from __future__ import annotations

from memory.project_inventory import collect_project_files


def test_inventory_skips_ignored_directories_and_respects_file_bound(tmp_path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    for index in range(5):
        (tmp_path / f"file_{index}.py").write_text("x = 1\n", encoding="utf-8")

    inventory = collect_project_files(tmp_path, suffixes={".py"}, max_files=3)

    assert len(inventory.files) == 3
    assert all(".venv" not in path.parts for path in inventory.files)
    assert inventory.truncated is True


def test_inventory_enforces_depth_and_entry_bounds(tmp_path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "deep.py").write_text("x = 1\n", encoding="utf-8")

    inventory = collect_project_files(tmp_path, suffixes={".py"}, max_depth=1, max_entries=32)

    assert inventory.files == ()
    assert inventory.truncated is True


def test_inventory_rejects_invalid_bounds(tmp_path) -> None:
    try:
        collect_project_files(tmp_path, max_files=0)
    except ValueError as exc:
        assert "bounds" in str(exc)
    else:
        raise AssertionError("expected invalid inventory bounds to fail")
