from __future__ import annotations

from pathlib import Path

import pytest

from autonomous_iteration.runtime_controller import ToolRouter
from metadata import AgentPhase, DecisionNeedMetadata, PathResolutionMetadata, RuntimeStateMetadata, ToolInputMetadata
from runtime_diagnostics.collector import collect_from_runtime_state
from tools.file_writer import file_writer_executor


def test_tool_router_records_path_resolution_for_hallucinated_project_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="inspect project", phase=AgentPhase.UNDERSTAND_PROJECT)
    need = DecisionNeedMetadata(
        need_type="project_structure",
        question="inspect current project structure",
        target_path="/workspace/openpilot",
        attributes={"project_path": str(project_dir)},
    )

    selections = router.route(state, need)

    assert selections
    assert selections[0].input_metadata.to_params()["directory_path"] == str(project_dir.resolve())
    assert state.path_resolutions
    assert state.path_resolutions[-1].status == "corrected"
    assert state.path_resolutions[-1].correction_rule == "hallucinated_root_alias"


def test_file_writer_rejects_project_external_target_when_project_path_is_provided(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_file = tmp_path / "outside" / "secrets.txt"
    outside_file.parent.mkdir()

    with pytest.raises((ValueError, PermissionError), match="project|boundary|outside"):
        file_writer_executor(
            ToolInputMetadata.from_mapping(
                "file_writer",
                {
                    "file_path": str(outside_file),
                    "project_path": str(project_dir),
                    "content": "secret\n",
                },
            )
        )


def test_collect_from_runtime_state_detects_blocked_path_resolution() -> None:
    state = RuntimeStateMetadata(goal="inspect file", phase=AgentPhase.UNDERSTAND_TASK)
    state.record_path_resolution(
        PathResolutionMetadata(
            project_root="/tmp/project",
            raw_path="/tmp/outside/secrets.txt",
            resolved_path="",
            status="blocked",
            reason="Path outside project boundary",
            confidence=1.0,
            correction_rule="outside_project_boundary",
            inside_project=False,
            exists_verified=False,
        )
    )

    signals = collect_from_runtime_state(state)

    assert any(signal.category == "path_resolution" for signal in signals)


def test_tool_router_blocks_command_executor_when_cwd_escapes_project(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="run tests", phase=AgentPhase.EXECUTE)
    need = DecisionNeedMetadata(
        need_type="command_check",
        question="run project tests",
        command="pytest",
        attributes={"project_path": str(project_dir), "cwd": str(outside_dir)},
    )

    selections = router.route(state, need)

    assert selections == []
    assert state.path_resolutions[-1].status == "blocked"
    assert "outside project boundary" in state.path_resolutions[-1].reason.lower()


def test_tool_router_blocks_command_executor_when_command_path_escapes_project(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret\n", encoding="utf-8")
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="inspect log", phase=AgentPhase.EXECUTE)
    need = DecisionNeedMetadata(
        need_type="command_check",
        question="read external file",
        command=f"cat {outside_file}",
        attributes={"project_path": str(project_dir), "cwd": str(project_dir)},
    )

    selections = router.route(state, need)

    assert selections == []
    assert any(
        resolution.path_source == "command_executor:command"
        and resolution.status == "blocked"
        and "outside project boundary" in resolution.reason.lower()
        for resolution in state.path_resolutions
    )


def test_tool_router_corrects_hallucinated_absolute_command_argument(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    target = project_dir / "src" / "ui" / "cli.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('ok')\n", encoding="utf-8")
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="run cli", phase=AgentPhase.EXECUTE)
    need = DecisionNeedMetadata(
        need_type="command_check",
        question="run project cli",
        command="python /workspace/openpilot/src/ui/cli.py",
        attributes={"project_path": str(project_dir), "cwd": str(project_dir)},
    )

    selections = router.route(state, need)

    assert selections
    assert selections[0].input_metadata.command == f"python {target.resolve()}"
    assert any(
        resolution.path_source == "command_executor:command"
        and resolution.status == "corrected"
        and resolution.correction_rule == "hallucinated_root_alias"
        for resolution in state.path_resolutions
    )


def test_tool_router_preserves_pytest_node_suffix_when_correcting_absolute_command_path(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    target = project_dir / "tests" / "test_cli.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="run pytest node", phase=AgentPhase.EXECUTE)
    need = DecisionNeedMetadata(
        need_type="command_check",
        question="run one pytest node",
        command="pytest /workspace/openpilot/tests/test_cli.py::test_ok",
        attributes={"project_path": str(project_dir), "cwd": str(project_dir)},
    )

    selections = router.route(state, need)

    assert selections
    assert selections[0].input_metadata.command == f"pytest {target.resolve()}::test_ok"


def test_tool_router_allows_external_interpreter_after_env_but_still_corrects_project_data_path(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    target = project_dir / "src" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('ok')\n", encoding="utf-8")
    conda_bin = tmp_path / "opt" / "anaconda3" / "bin"
    conda_bin.mkdir(parents=True)
    interpreter = conda_bin / "python3.13"
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="run project entry", phase=AgentPhase.EXECUTE)
    need = DecisionNeedMetadata(
        need_type="command_check",
        question="run project entry file",
        command=f"/usr/bin/env {interpreter} /workspace/openpilot/src/main.py --help",
        attributes={"project_path": str(project_dir), "cwd": str(project_dir)},
    )

    selections = router.route(state, need)

    assert selections
    assert selections[0].input_metadata.command == f"/usr/bin/env {interpreter.resolve()} {target.resolve()} --help"
    assert any(
        resolution.intent_kind == "command_executable_path"
        and resolution.status == "external_allowed"
        and resolution.raw_path == str(interpreter.resolve())
        for resolution in state.path_resolutions
    )
    assert any(
        resolution.intent_kind == "command_data_path"
        and resolution.status == "corrected"
        and resolution.correction_rule == "hallucinated_root_alias"
        for resolution in state.path_resolutions
    )


def test_tool_router_blocks_external_redirection_path_for_project_command(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    readme = project_dir / "README.md"
    readme.write_text("hello\n", encoding="utf-8")
    outside_log = tmp_path / "outside" / "result.txt"
    outside_log.parent.mkdir()
    router = ToolRouter()
    state = RuntimeStateMetadata(goal="capture output", phase=AgentPhase.EXECUTE)
    need = DecisionNeedMetadata(
        need_type="command_check",
        question="capture command output",
        command=f"cat {readme.resolve()} > {outside_log.resolve()}",
        attributes={"project_path": str(project_dir), "cwd": str(project_dir)},
    )

    selections = router.route(state, need)

    assert selections == []
    assert any(
        resolution.intent_kind == "command_redirection_path"
        and resolution.status == "blocked"
        and "outside project boundary" in resolution.reason.lower()
        for resolution in state.path_resolutions
    )
