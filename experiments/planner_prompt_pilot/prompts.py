"""Frozen prompt arms for planner-prompt pilot (not production prompts).

The treatment renderer accepts already-projected facts so the experiment can
compare the prompt text without importing or changing the production executor.
"""

from __future__ import annotations

CONTROL_PROMPT = """You are an AI assistant that plans decision_needs for a task.
The runtime validates permissions, paths, budgets, tools, and evidence. Preserve
the task, goal, constraints, and observed paths; do not invent paths or
intermediate plan files. Prefer evidence before mutation and use this fixed
sequence: inspect first, then choose a typed mutation, then validate. For new
files use create/generate then write; for existing edits read then patch; for
deletion gather evidence first. Include README delivery after code work when
needed. Return only JSON with a decision_needs array.
"""

TREATMENT_PROMPT = """Plan the decision_needs required to satisfy the task and goal.
Return only JSON: {\"decision_needs\": [{\"need_type\": ..., \"question\": ...}]}.
Use only facts present in the task/context and omit unavailable optional fields.
The runtime, not you, enforces permissions, paths, budgets, tools, and evidence.
"""

ARMS = {"control": CONTROL_PROMPT, "treatment": TREATMENT_PROMPT}


def _render_facts(
    *,
    task: str,
    goal: str,
    planning_surface: str,
    history: str,
    constraints: str,
    project_context: str,
    read_only_notice: str,
    instructions: str,
) -> str:
    sections = [instructions, f"Task: {task}", f"Overall Goal: {goal}"]
    for label, value in (
        ("Active Session Constraints", constraints),
        ("Current Project Context", project_context),
        ("Previous Task Results", history),
        ("Read-only task mode", read_only_notice),
        ("Planning Surface", planning_surface),
    ):
        if value:
            sections.append(f"{label}:\n{value}")
    return "\n\n".join(sections)


def render_control_prompt(
    *,
    task: str,
    goal: str,
    planning_surface: str = "",
    history: str = "No previous task results.",
    constraints: str = "",
    project_context: str = "",
    read_only_notice: str = "",
) -> str:
    """Render the frozen control arm from model-facing facts."""

    base = _render_facts(
        task=task,
        goal=goal,
        planning_surface=planning_surface,
        history=history,
        constraints=constraints,
        project_context=project_context,
        read_only_notice=read_only_notice,
        instructions=(
            "You are an AI assistant that plans decision_needs for tasks. Do not "
            "choose tools; the runtime ToolRouter maps needs to concrete tools "
            "under budget, path, risk, and permission checks. Use the fixed "
            "sequence: inspect first, then choose a typed mutation, then validate."
        ),
    )
    return base + """

Output ONLY valid JSON in this format:
{"decision_needs":[{"need_type":"code_file_create","question":"create the main project file","target_path":"/absolute/path/to/file.py","operation_kind":"create_file","attributes":{"language":"python"}}]}

Allowed need_type values: file_read, project_structure, web_search, command_check,
file_write, file_delete, code_file_create, directory_generate, code_unit_generate,
code_symbol_modify, code_patch, code_generation, code_execution, readme_generation,
bug_fix, repair. Optional fields may include target_path, operation_kind,
target_scope, symbol_name, symbol_type, insertion_hint, patch_mode, candidate_paths,
query, command, risk_level, and attributes. Omit unknown or unavailable optional
fields. Do not emit null, placeholders, or tool_calls.

Important:
- Use latest_change and evidence_paths in Previous Task Results; never invent plan files.
- Never invent or read intermediate files such as subtask_0.md, subtask_1.md,
  requirements.md, or plan.md unless previous results or the user explicitly mention them.
- When a project root is present, prefer paths observed in previous results or
  directory evidence; do not invent nested directories or filenames.
- Prefer evidence before mutation: inspect files/directories first, then mutate
  with concrete target paths.
- For new code files, use code_file_create or directory_generate, then file_write
  with operation_kind create_file.
- For existing-file additions, read first, then use code_unit_generate and file_write
  with add_symbol semantics.
- For existing-file edits, read first, then use code_symbol_modify or code_patch and
  file_write with modify_symbol semantics.
- For deletion, gather evidence first, then use file_delete with delete_file semantics.
- Code generation supports only executable code languages; never plan language text.
- After completed code/project delivery, emit readme_generation when a README is needed.
- For command validation, use only typed dry_run, interactive, or automatic modes;
  never plan source/activate/cd/export wrappers.
"""


def render_treatment_prompt(
    *,
    task: str,
    goal: str,
    planning_surface: str = "",
    history: str = "No previous task results.",
    constraints: str = "",
    project_context: str = "",
    read_only_notice: str = "",
) -> str:
    """Render the economical arm from frozen, model-facing facts.

    This is deliberately a pure experiment helper. Runtime Router/Guard and
    evidence owners remain outside the prompt and are not replaced by it.
    """

    sections = [
        "You are an AI assistant that plans decision_needs for the task.",
        "Do not choose tools; the runtime ToolRouter enforces budget, path, risk, and permission checks.",
        f"Task: {task}",
        f"Overall Goal: {goal}",
    ]
    for label, value in (
        ("Active Session Constraints", constraints),
        ("Current Project Context", project_context),
        ("Previous Task Results", history),
        ("Read-only task mode", read_only_notice),
        ("Planning Surface", planning_surface),
    ):
        if value:
            sections.append(f"{label}:\n{value}")
    sections.append(
        """Output ONLY valid JSON in this format:
{"decision_needs":[{"need_type":"file_read","question":"inspect the relevant file"}]}

Allowed need_type values: file_read, project_structure, web_search, command_check,
file_write, file_delete, code_file_create, directory_generate, code_unit_generate,
code_symbol_modify, code_patch, code_generation, code_execution,
readme_generation, bug_fix, repair.
Use values supported by the planning surface. Optional fields (for example
target_path, operation_kind, target_scope, symbol_name, command, and attributes)
must be omitted when unknown; never emit null, placeholders, or tool_calls.
Ground paths and claims in the task, current evidence, and previous results.
The runtime Router and Guard remain authoritative for scope, mutation approval,
and validation; a plan is not evidence of execution or success."""
    )
    return "\n\n".join(sections) + "\n"


def render_arm_prompt(arm: str, **facts: str) -> str:
    """Render one frozen arm using the same projected facts."""

    renderer = render_control_prompt if arm == "control" else render_treatment_prompt
    if arm not in ARMS:
        raise ValueError(f"unknown planner pilot arm: {arm}")
    return renderer(**facts)

PROMPT_MANIFEST = {
    "manifest": "harness-slimming-planner-prompt-pilot-v1",
    "control": "current-instructional-prompt-proxy-v1",
    "treatment": "economical-prompt-proxy-v1",
    "production_default": False,
    "metadata_changed": False,
    "permissions_changed": False,
    "tool_execution_changed": False,
}
