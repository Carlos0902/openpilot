"""Deterministic quality evaluator for projection recordings."""
from __future__ import annotations
from typing import Any
from .renderer import OPTIONAL, REQUIRED

def evaluate(task: dict[str, Any], projection: Any, *, arm: str) -> dict[str, Any]:
    row = {"task_id": task["id"], "stratum": task["stratum"], "arm": arm,
           "active_constraint_recall": False, "required_fact_recall": 0.0,
           "relevant_evidence_recall": False, "evidence_id_legal": False,
           "duplicate_projection": False, "fallback_correct": False,
           "ready": False, "context_assembly_failure": False, "input_chars": None,
           "reason": ""}
    if not isinstance(projection, dict):
        row.update(context_assembly_failure=True, reason="malformed_projection")
        return row
    ids = projection.get("selected_fact_ids")
    facts = projection.get("facts")
    if not isinstance(ids, list) or not isinstance(facts, dict):
        row.update(context_assembly_failure=True, reason="missing_projection_fields")
        return row
    row["input_chars"] = sum(len(str(value)) for value in facts.values())
    row["evidence_id_legal"] = all(item in REQUIRED + OPTIONAL for item in ids) and len(ids) == len(set(ids)) and set(ids) == set(facts)
    required_present = set(REQUIRED) & set(ids)
    row["required_fact_recall"] = len(required_present) / len(REQUIRED)
    row["active_constraint_recall"] = set(REQUIRED).issubset(required_present)
    relevant = set(task.get("relevant_evidence") or [])
    row["relevant_evidence_recall"] = relevant.issubset(set(ids))
    row["duplicate_projection"] = bool(projection.get("aggregate_copy"))
    fallback_expected = arm == "treatment" and task.get("artifact_status") != "valid"
    row["fallback_correct"] = bool(projection.get("fallback_used")) == fallback_expected and (not fallback_expected or projection.get("mode") == "current_source_view")
    row["ready"] = bool(projection.get("ready")) and not fallback_expected
    row["context_assembly_failure"] = not row["evidence_id_legal"] or not row["active_constraint_recall"] or not row["fallback_correct"]
    if row["context_assembly_failure"]: row["reason"] = "projection_gate_failed"
    return row
