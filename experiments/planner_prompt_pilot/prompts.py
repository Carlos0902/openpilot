"""Frozen prompt arms for planner-prompt pilot (not production prompts)."""

CONTROL_PROMPT = """You are an AI assistant that plans decision_needs for a task.
Return only JSON with a decision_needs array. The runtime validates permissions,
paths, budgets, tools, and evidence. Preserve the task, goal, constraints, and
observed paths; do not invent paths or intermediate plan files.
"""

TREATMENT_PROMPT = """Plan the decision_needs required to satisfy the task and goal.
Return only JSON: {\"decision_needs\": [{\"need_type\": ..., \"question\": ...}]}.
Use only facts present in the task/context and omit unavailable optional fields.
The runtime, not you, enforces permissions, paths, budgets, tools, and evidence.
"""

ARMS = {"control": CONTROL_PROMPT, "treatment": TREATMENT_PROMPT}
