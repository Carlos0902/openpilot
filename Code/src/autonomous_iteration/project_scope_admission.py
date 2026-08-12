"""Bounded project-root admission for launches from large directories."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


class ProjectScopeAdmissionError(RuntimeError):
    """Raised when an existing-project task has an ambiguous broad root."""


class ProjectScopeKind(str):
    USE_REQUESTED_ROOT = "use_requested_root"
    GENERATED_CHILD_PROJECT = "generated_child_project"
    REQUIRE_EXPLICIT_PROJECT = "require_explicit_project"


@dataclass(frozen=True)
class ProjectScopeDecision:
    kind: str
    source_root: Path
    effective_root: Path
    reason: str


_PROJECT_MARKERS = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile")
_CREATION_TERMS = ("创建", "生成", "构建", "制作", "开发", "做", "create", "generate", "build", "make", "develop")


def resolve_project_execution_scope(
    goal: str,
    requested_root: str | Path,
    *,
    home_path: str | Path | None = None,
) -> ProjectScopeDecision:
    source = Path(requested_root).expanduser().resolve(strict=False)
    home = Path(home_path or Path.home()).expanduser().resolve(strict=False)
    if not _looks_like_project_execution(goal):
        return ProjectScopeDecision(ProjectScopeKind.USE_REQUESTED_ROOT, source, source, "non_project_request")
    if _has_project_marker(source):
        return ProjectScopeDecision(ProjectScopeKind.USE_REQUESTED_ROOT, source, source, "existing_project_root")
    broad = source == home or source == Path(source.anchor) or _contains_multiple_projects(source)
    if broad and _is_artifact_creation(goal):
        return ProjectScopeDecision(
            ProjectScopeKind.GENERATED_CHILD_PROJECT,
            source,
            _available_child(source, _project_name(goal)),
            "broad_root_artifact_creation",
        )
    if broad:
        return ProjectScopeDecision(
            ProjectScopeKind.REQUIRE_EXPLICIT_PROJECT,
            source,
            source,
            "broad_root_existing_project_ambiguous",
        )
    return ProjectScopeDecision(ProjectScopeKind.USE_REQUESTED_ROOT, source, source, "safe_requested_root")


def _looks_like_project_execution(goal: str) -> bool:
    lowered = str(goal).casefold()
    return any(term in lowered for term in _CREATION_TERMS + ("修复", "修改", "优化", "重构", "implement", "modify", "fix", "refactor", "project", "file"))


def _is_artifact_creation(goal: str) -> bool:
    lowered = str(goal).casefold()
    return any(term in lowered for term in _CREATION_TERMS)


def _has_project_marker(root: Path) -> bool:
    return root.is_dir() and any((root / marker).exists() for marker in _PROJECT_MARKERS)


def _contains_multiple_projects(root: Path) -> bool:
    if not root.is_dir():
        return False
    found = 0
    try:
        for index, child in enumerate(root.iterdir()):
            if index >= 256:
                return True
            if child.is_dir() and _has_project_marker(child):
                found += 1
                if found >= 2:
                    return True
    except OSError:
        return True
    return False


def _project_name(goal: str) -> str:
    lowered = str(goal).casefold()
    if "贪吃蛇" in lowered or "snake" in lowered:
        return "snake-game"
    if "dashboard" in lowered or "仪表盘" in lowered:
        return "dashboard"
    if "game" in lowered or "游戏" in lowered:
        return "game"
    return "generated-project"


def _available_child(root: Path, base_name: str) -> Path:
    candidate = root / base_name
    if not candidate.exists():
        return candidate
    for suffix in range(2, 101):
        candidate = root / f"{base_name}-{suffix}"
        if not candidate.exists():
            return candidate
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:8]
    return root / f"{base_name}-{digest}"
