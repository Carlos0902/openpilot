"""Pure renderer for a frozen context projection experiment.

It intentionally models a projection, not production context assembly.  The
required facts are always source-owned; optional evidence is either selected
by relevance (treatment) or retained in the full current source view
(control).  A bad compact artifact fails closed and returns that source view.
"""
from __future__ import annotations

from typing import Any

REQUIRED = ("safety", "permissions", "task_goal", "acceptance", "write_scope", "validation", "schema")
OPTIONAL = ("diagnosis", "history", "memory", "manifest", "validation_detail", "support_body")

def _facts(task: dict[str, Any]) -> dict[str, str]:
    return {key: f"{key} for {task['id']}" for key in REQUIRED + OPTIONAL}

def render_projection(task: dict[str, Any], arm: str) -> dict[str, Any]:
    if arm not in {"control", "treatment"}:
        raise ValueError("unknown arm")
    facts = _facts(task)
    status = str(task.get("artifact_status", "valid"))
    fallback = arm == "treatment" and status != "valid"
    if arm == "control" or fallback:
        selected = list(REQUIRED + OPTIONAL)
        mode = "current_source_view" if fallback else "full_source_view"
    else:
        relevant = set(task.get("relevant_evidence") or [])
        selected = list(REQUIRED) + [key for key in OPTIONAL if key in relevant]
        mode = "minimal_required_plus_relevant"
    # Aggregate and granular forms are mutually exclusive in this experiment.
    return {
        "task_id": task["id"], "arm": arm, "mode": mode,
        "selected_fact_ids": selected,
        "facts": {key: facts[key] for key in selected},
        "fallback_used": fallback,
        "ready": not fallback,
    }
