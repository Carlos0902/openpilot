from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from metadata import (
    EnvironmentOperation,
    EnvironmentReadiness,
    EnvironmentSyncMetadata,
    ProjectStackPresetMetadata,
)


def _interactive_environment(project: Path) -> EnvironmentSyncMetadata:
    return EnvironmentSyncMetadata(
        operation=EnvironmentOperation.SETUP,
        readiness=EnvironmentReadiness.READY,
        environment_id="env:test",
        project_path=str(project),
        python_executable="/usr/bin/python3",
        command_cwd=str(project),
        run_command=".venv/bin/python snake_game.py",
        stack_preset=ProjectStackPresetMetadata(
            project_path=str(project),
            delivery_surface="interactive_runtime",
        ),
    )


def test_project_delivery_environment_reuses_ready_typed_environment(tmp_path) -> None:
    environment = _interactive_environment(tmp_path)
    autopilot = object.__new__(IntelligentAutopilot)
    autopilot._collect_written_files = lambda _results: [str(tmp_path / "snake_game.py")]
    autopilot._infer_project_path_from_files = lambda _goal, _files: tmp_path
    autopilot._project_environment_context = lambda _path: environment.to_json_dict()

    selected = autopilot.project_delivery_environment(
        {"success": True, "goal": "build a game", "results": []}
    )

    assert selected == environment


def test_project_delivery_environment_unwraps_checkpointed_session_result(tmp_path) -> None:
    environment = _interactive_environment(tmp_path)
    target = tmp_path / "snake_game.py"
    autopilot = object.__new__(IntelligentAutopilot)
    autopilot._collect_written_files = lambda results: [str(target)] if results else []
    autopilot._infer_project_path_from_files = lambda _goal, _files: tmp_path
    autopilot._project_environment_context = lambda _path: environment.to_json_dict()

    selected = autopilot.project_delivery_environment(
        {
            "success": True,
            "session_result": {
                "success": True,
                "goal": "build a game",
                "results": [object()],
            },
        }
    )

    assert selected == environment


def test_project_delivery_environment_rejects_nested_result_without_explicit_success(tmp_path) -> None:
    autopilot = object.__new__(IntelligentAutopilot)

    selected = autopilot.project_delivery_environment(
        {
            "success": True,
            "session_result": {"goal": "build a game", "results": [object()]},
        }
    )

    assert selected is None


def test_interactive_launch_passes_typed_user_confirmation_as_runtime_only_handle(tmp_path) -> None:
    environment = _interactive_environment(tmp_path)
    autopilot = object.__new__(IntelligentAutopilot)
    captured = {}

    def execute_fast_tool(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(success=True)

    autopilot._execute_fast_tool = execute_fast_tool

    result = autopilot.launch_interactive_application(environment, user_confirmed=True)

    metadata = captured["input_metadata"]
    execution_context = metadata.runtime_handles["_tool_execution_context"]
    assert result.success is True
    assert metadata.mode == "interactive"
    assert "user_confirmed" not in metadata.to_json_dict()
    assert execution_context.user_confirmed is True
    assert execution_context.input_metadata.runtime_handles == {}


def test_interactive_launch_rejects_truthy_non_boolean_confirmation(tmp_path) -> None:
    autopilot = object.__new__(IntelligentAutopilot)

    with pytest.raises(PermissionError, match="explicit user confirmation"):
        autopilot.launch_interactive_application(_interactive_environment(tmp_path), user_confirmed="yes")
