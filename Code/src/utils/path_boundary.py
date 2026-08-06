"""Project-root path boundary helpers.

These helpers keep local file tools grounded in the runtime project root when
that root is known. They intentionally do not enforce a boundary when no
project_path is provided, preserving legacy standalone tool behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


HALLUCINATED_PROJECT_ROOTS = (
    "/workspace/openpilot",
    "/workspace/project",
    "/openpilot",
)


class PathBoundaryError(ValueError):
    """Raised when a path escapes the declared project boundary."""


def resolve_project_path(raw_project_path: str | Path) -> Path:
    """Return an absolute, expanded project path."""
    return Path(raw_project_path).expanduser().resolve()


def is_within_project(path: str | Path, project_path: str | Path) -> bool:
    """Return whether path is inside project_path or equal to it."""
    resolved = Path(path).expanduser().resolve()
    root = resolve_project_path(project_path)
    try:
        resolved.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_within_project(raw_path: str | Path, project_path: str | Path, *, allow_root_alias: bool = True) -> Path:
    """Resolve a path under project_path, rejecting escapes.

    Relative paths are interpreted relative to project_path. Absolute paths are
    allowed only when already inside project_path. Common hallucinated project
    roots such as /workspace/openpilot are treated as aliases for project_path
    when allow_root_alias is true.
    """
    root = resolve_project_path(project_path)
    text = str(raw_path or "").strip()
    if not text:
        raise PathBoundaryError("Path is empty and cannot be resolved within project boundary")

    aliased = _replace_hallucinated_root(text, root) if allow_root_alias else None
    candidate = Path(aliased if aliased is not None else text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    if not _is_relative_to(resolved, root):
        raise PathBoundaryError(f"Path outside project boundary: {resolved} not under {root}")
    return resolved


def normalize_paths_within_project(raw_paths: Iterable[str | Path], project_path: str | Path) -> list[str]:
    """Resolve multiple paths within project_path and return strings."""
    return [str(resolve_within_project(raw_path, project_path)) for raw_path in raw_paths]


def project_path_from_mapping(values: dict) -> str:
    """Extract a project_path from a loose mapping."""
    raw = values.get("project_path") or values.get("cwd")
    return str(raw or "")


def _replace_hallucinated_root(path_text: str, project_root: Path) -> str | None:
    normalized = path_text.rstrip("/") or path_text
    for raw_root in HALLUCINATED_PROJECT_ROOTS:
        root = raw_root.rstrip("/")
        if normalized == root:
            return str(project_root)
        if normalized.startswith(root + "/"):
            suffix = normalized[len(root) + 1 :]
            return str(project_root / suffix)
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
