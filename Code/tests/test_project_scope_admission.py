from __future__ import annotations

from autonomous_iteration.project_scope_admission import (
    ProjectScopeKind,
    resolve_project_execution_scope,
)


def _repo(path) -> None:
    path.mkdir(parents=True)
    (path / ".git").mkdir()


def test_creation_from_home_uses_generated_child_project(tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    decision = resolve_project_execution_scope("帮我做一个贪吃蛇游戏", home, home_path=home)
    assert decision.kind == ProjectScopeKind.GENERATED_CHILD_PROJECT
    assert decision.effective_root == (home / "snake-game").resolve()


def test_generated_child_name_never_reuses_existing_directory(tmp_path) -> None:
    home = tmp_path / "home"
    (home / "snake-game").mkdir(parents=True)
    decision = resolve_project_execution_scope("帮我做一个贪吃蛇游戏", home, home_path=home)
    assert decision.effective_root == (home / "snake-game-2").resolve()


def test_existing_project_at_home_requires_explicit_scope(tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    decision = resolve_project_execution_scope("修复当前项目里的登录问题", home, home_path=home)
    assert decision.kind == ProjectScopeKind.REQUIRE_EXPLICIT_PROJECT


def test_existing_repository_keeps_requested_root(tmp_path) -> None:
    project = tmp_path / "calculator"
    _repo(project)
    decision = resolve_project_execution_scope("修复 calculator.py", project, home_path=tmp_path)
    assert decision.kind == ProjectScopeKind.USE_REQUESTED_ROOT
    assert decision.effective_root == project.resolve()


def test_multi_project_container_is_treated_as_broad_root(tmp_path) -> None:
    workspace = tmp_path / "Developer"
    _repo(workspace / "one")
    _repo(workspace / "two")
    decision = resolve_project_execution_scope("build a dashboard", workspace, home_path=tmp_path / "home")
    assert decision.kind == ProjectScopeKind.GENERATED_CHILD_PROJECT
    assert decision.effective_root == (workspace / "dashboard").resolve()
