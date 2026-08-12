from pathlib import Path

import tomllib

from utils import __version__


def test_release_version_is_development_snapshot() -> None:
    assert __version__ == "0.1.0.dev11"


def test_source_version_matches_project_metadata() -> None:
    code_root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((code_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert __version__ == project["project"]["version"]
