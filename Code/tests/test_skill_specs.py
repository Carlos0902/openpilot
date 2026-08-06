from __future__ import annotations

from pathlib import Path

from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from autonomous_iteration.skill_specs import (
    SkillCapabilityCardProvider,
    default_skill_search_roots,
    discover_skill_files,
    load_skill_specs,
    parse_skill_file,
)
from autonomous_iteration.task_models import Task, TaskExecutionContext


class DummyLLM:
    def complete(self, request):  # pragma: no cover - not exercised here
        raise AssertionError(f"LLM should not be called during skill-spec tests: {request}")


SKILL_MARKDOWN = """---
name: Test Development
description: Investigate repeated test failures before fixing code.
trigger_terms:
  - test development
  - repeated failures
allowed_tools:
  - file_reader
  - multi_file_reader
need_types:
  - project_structure
  - file_read
  - command_check
exposure: deferred
---

# When To Use
- when broad test failures need clustering
- when the likely root cause spans multiple modules

# When Not To Use
- when a direct one-line edit is already known

# Procedure
1. gather cross-module evidence
2. run targeted validation commands
3. summarize likely root causes before any fix
"""


def _write_skill(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "skills" / "test-development"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(SKILL_MARKDOWN, encoding="utf-8")
    return skill_file


def test_parse_skill_file_extracts_frontmatter_and_sections(tmp_path) -> None:
    skill_file = _write_skill(tmp_path)

    spec = parse_skill_file(skill_file)

    assert spec is not None
    assert spec.skill_id == "test_development"
    assert spec.name == "Test Development"
    assert spec.description == "Investigate repeated test failures before fixing code."
    assert spec.trigger_terms == ("test development", "repeated failures")
    assert spec.allowed_tools == ("file_reader", "multi_file_reader")
    assert spec.need_types == ("project_structure", "file_read", "command_check")
    assert spec.when_to_use[0] == "when broad test failures need clustering"
    assert spec.procedure_summary[0] == "gather cross-module evidence"


def test_load_skill_specs_discovers_skill_files_and_builds_cards(tmp_path) -> None:
    skill_file = _write_skill(tmp_path)

    discovered = discover_skill_files([tmp_path / "skills"])
    specs = load_skill_specs([tmp_path / "skills"])
    provider = SkillCapabilityCardProvider(specs)
    cards = provider.planning_cards()

    assert discovered == [skill_file]
    assert len(specs) == 1
    assert len(cards) == 1
    assert cards[0].card_id == "skill_test_development"
    assert cards[0].title == "Test Development"
    assert cards[0].summary.startswith("Investigate repeated test failures")
    assert cards[0].backing_refs == ("skill:test_development",)


def test_intelligent_autopilot_loads_skill_specs_into_planning_surface(tmp_path) -> None:
    _write_skill(tmp_path)
    autopilot = IntelligentAutopilot(DummyLLM(), log_file=tmp_path / "autopilot.jsonl", skill_roots=[tmp_path / "skills"])
    task = Task(id="task", description="Use test development workflow to analyze repeated failures")
    context = TaskExecutionContext(task=task, parent_context={"goal": "cluster failures"}, shared_state={}, execution_history=[])

    planning_surface = autopilot.tool_planning_task_executor._planning_surface_for_prompt(
        task.description,
        "cluster failures",
        context=context,
    )

    assert autopilot.skill_specs
    assert autopilot.planning_surface_providers
    assert "Test Development" in planning_surface
    assert "Allowed tools: file_reader, multi_file_reader." in planning_surface


def test_default_skill_search_roots_points_to_conventional_locations(tmp_path) -> None:
    roots = default_skill_search_roots(tmp_path / "Code")

    assert roots[0] == tmp_path / "Code" / "skills"
    assert roots[1] == tmp_path / "skills"
    assert roots[2] == tmp_path / "Code" / ".openpilot" / "skills"
    assert roots[3] == tmp_path / ".openpilot" / "skills"
