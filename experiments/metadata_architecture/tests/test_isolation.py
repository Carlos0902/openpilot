from __future__ import annotations

from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]


def test_experiment_does_not_import_production_metadata() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((EXPERIMENT_ROOT / "src").rglob("*.py"))
    )

    assert "from metadata" not in source
    assert "import metadata" not in source
    assert "Code.src" not in source


def test_production_does_not_import_the_experiment() -> None:
    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPOSITORY_ROOT / "Code" / "src").rglob("*.py"))
    )

    assert "openpilot_metadata_experiment" not in production_source
