from pathlib import Path

from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from experiments.full_architecture_context_observation.stage_harness_slimming_planner_prompt import (
    build_economical_planner_prompt,
    pilot_corpus,
    run_offline_pilot,
)

from test_execution_tool_planning_executor import FakeRuntime


def test_economical_prompt_keeps_contract_and_hard_boundaries(tmp_path: Path) -> None:
    executor = ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))
    prompt = build_economical_planner_prompt(executor, "Create app.py", "build app", "Need Catalog")
    assert '"decision_needs"' in prompt
    assert "Router" in prompt and "Guard" in prompt
    assert "read/write scope" in prompt
    assert "fixed" not in prompt.lower()
    assert "subtask_0.md" not in prompt
    assert "For new code files" not in prompt


def test_pilot_corpus_has_30_cases_and_repeats_are_recorded(tmp_path: Path) -> None:
    executor = ToolPlanningTaskExecutor(FakeRuntime(tmp_path, {"decision_needs": []}))
    result = run_offline_pilot(executor)
    assert len(pilot_corpus()) == 30
    assert result["cases"] == 30
    assert result["repeats"] == 3
    assert len(result["rows"]) == 180
    assert all(row["has_schema"] for row in result["rows"])
    assert all(row["has_boundary"] for row in result["rows"] if row["arm"] == "treatment")
    assert result["treatment_median_chars"] < result["control_median_chars"]
