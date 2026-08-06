from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.runtime_controller import ToolRouter
from metadata import AgentPhase, DecisionNeedMetadata, RuntimeStateMetadata, ToolInputMetadata
from tools.file_reader import file_reader_executor
from tools.multi_file_reader import multi_file_reader_executor


class _MinimalRuntime:
    def __init__(self) -> None:
        self._project_environments = {}



def test_file_reader_allows_in_project_file_as_control(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    target = project_dir / "README.md"
    target.write_text("hello", encoding="utf-8")

    result = file_reader_executor(
        ToolInputMetadata.from_mapping(
            "file_reader",
            {"file_path": str(target), "project_path": str(project_dir)},
        )
    )

    assert result.result.file_path == str(target)
    assert "hello" in result.result.content



def test_file_reader_should_reject_project_external_file_when_project_path_is_provided(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "secrets.txt"
    outside_file.write_text("outside", encoding="utf-8")

    with pytest.raises((ValueError, PermissionError), match="project|boundary|outside"):
        file_reader_executor(
            ToolInputMetadata.from_mapping(
                "file_reader",
                {"file_path": str(outside_file), "project_path": str(project_dir)},
            )
        )



def test_multi_file_reader_should_reject_project_external_directory_when_project_path_is_provided(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "note.md").write_text("# external", encoding="utf-8")

    with pytest.raises((ValueError, PermissionError), match="project|boundary|outside"):
        multi_file_reader_executor(
            ToolInputMetadata.from_mapping(
                "multi_file_reader",
                {
                    "directory_path": str(outside_dir),
                    "project_path": str(project_dir),
                    "pattern": "*.md",
                },
            )
        )



def test_tool_router_should_normalize_or_block_hallucinated_workspace_directory(tmp_path: Path) -> None:
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

    assert selections, "router should not silently drop the need"
    assert selections[0].input_metadata.to_params()["directory_path"] == str(project_dir)
    assert selections[0].input_metadata.to_params()["pattern"] == "sketch.json"



def test_tool_planning_should_prefer_context_project_path_over_hallucinated_absolute_path(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    runtime = _MinimalRuntime()
    executor = ToolPlanningTaskExecutor(runtime)
    executor._active_context = SimpleNamespace(
        parent_context={"project_path": str(project_dir)},
        shared_state={},
    )

    inferred = executor._infer_project_path(
        "Trace the CLI runtime flow for /workspace/openpilot and summarize the chain.",
        "Use the current project context.",
    )

    assert inferred == project_dir
