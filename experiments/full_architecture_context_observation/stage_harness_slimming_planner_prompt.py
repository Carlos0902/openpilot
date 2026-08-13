"""Experiment-only planner prompt treatment for Harness Slimming phase 1.

This module is deliberately not imported by production execution.  It renders a
minimal treatment prompt from the same task/context facts as the production
planner while retaining the JSON contract and typed safety boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.task_models import TaskExecutionContext


ALLOWED_NEED_TYPES = (
    "file_read", "project_structure", "web_search", "command_check", "file_write",
    "file_delete", "code_file_create", "directory_generate", "code_unit_generate",
    "code_symbol_modify", "code_patch", "code_generation", "code_execution",
    "readme_generation", "bug_fix", "repair",
)


def build_economical_planner_prompt(
    executor: ToolPlanningTaskExecutor,
    task_description: str,
    goal: str,
    planning_surface: str,
    context: TaskExecutionContext | None = None,
) -> str:
    """Render the phase-1 treatment; no production-default switch is made."""
    history = executor._execution_history_summary(context)
    project_context = executor._project_context_summary(context)
    constraint_state = executor._session_constraints_from_context(context)
    constraint_prompt = (
        __import__("memory.session_constraints", fromlist=["session_constraint_prompt_text"])
        .session_constraint_prompt_text(constraint_state)
        if constraint_state else ""
    )
    read_only_notice = executor._read_only_notice(task_description, goal, context)
    sections = [
        "You are an AI assistant that plans decision_needs for the task.",
        "Do not choose tools; the runtime ToolRouter enforces budget, path, risk, and permission checks.",
        f"Task: {task_description}", f"Overall Goal: {goal}",
    ]
    if constraint_prompt:
        sections.append(constraint_prompt)
    if project_context:
        sections.append(f"Current Project Context:\n{project_context}")
    sections.extend([
        f"Previous Task Results:\n{history}",
        *( [read_only_notice] if read_only_notice else [] ),
        f"Planning Surface:\n{planning_surface}",
        """Output ONLY valid JSON in this format:
{"decision_needs":[{"need_type":"file_read","question":"inspect the relevant file"}]}

Allowed need_type values: """ + ", ".join(ALLOWED_NEED_TYPES) + """.
Use values supported by the planning surface. Optional fields (for example
target_path, operation_kind, target_scope, symbol_name, command, and
attributes) must be omitted when unknown; never emit null, placeholders, or
tool_calls. Ground paths and claims in the task, current evidence, and
Previous Task Results. The runtime, Router, and Guard remain authoritative for
read/write scope, mutation approval, and validation; a plan is not evidence of
execution or success.""",
    ])
    return "\n\n".join(sections) + "\n"


@dataclass(frozen=True)
class PlannerPilotCase:
    case_id: str
    task: str
    goal: str
    kind: str


def pilot_corpus() -> tuple[PlannerPilotCase, ...]:
    """Return the frozen 30-case shape corpus used by the offline pilot."""
    shapes = (
        ("single_file_local_fix", "Fix the failing function in src/app.py", "repair"),
        ("single_file_new_symbol", "Add a helper to src/app.py", "implement"),
        ("new_file", "Create src/cli.py", "create"),
        ("multi_file_scoped", "Update src/a.py and src/b.py", "modify"),
        ("readonly_diagnostic", "Inspect the CLI entrypoint", "inspect"),
        ("validation_only", "Run the exact test command", "validate"),
        ("needs_evidence", "Repair the issue after inspecting files", "repair"),
        ("insufficient_info", "Determine the intended behavior", "clarify"),
        ("ambiguous_scope", "Change the project appropriately", "stop"),
        ("support_relevant", "Use the supplied API notes to implement", "implement"),
    )
    return tuple(
        PlannerPilotCase(f"{name}-{index // len(shapes) + 1}", task, "Complete the requested task", kind)
        for index, (name, task, kind) in enumerate(shapes * 3)
    )


def run_offline_pilot(executor: ToolPlanningTaskExecutor, *, repeats: int = 3) -> dict[str, Any]:
    """Render paired arms and return body-free, deterministic pilot metrics."""
    cases = pilot_corpus()
    rows: list[dict[str, Any]] = []
    for case in cases:
        surface = executor._planning_surface_for_prompt(case.task, case.goal)
        control = executor._build_tool_plan_prompt(case.task, case.goal, surface)
        treatment = build_economical_planner_prompt(executor, case.task, case.goal, surface)
        for arm, prompt in (("control", control), ("treatment", treatment)):
            for repeat in range(repeats):
                rows.append({"case_id": case.case_id, "kind": case.kind, "arm": arm,
                             "repeat": repeat + 1, "prompt_chars": len(prompt),
                             "has_schema": '"decision_needs"' in prompt,
                             "has_boundary": "Router" in prompt and "Guard" in prompt})
    return {"schema": "harness-slimming-planner-pilot-v1", "claim_boundary": "experiment_only",
            "cases": len(cases), "repeats": repeats, "rows": rows,
            "treatment_median_chars": sorted(r["prompt_chars"] for r in rows if r["arm"] == "treatment")[len(rows)//4],
            "control_median_chars": sorted(r["prompt_chars"] for r in rows if r["arm"] == "control")[len(rows)//4]}

