"""Deterministic evaluator for recorded planner responses."""
from __future__ import annotations
import json
from typing import Any

ALLOWED_NEEDS = {"file_read","project_structure","web_search","command_check","file_write","file_delete","code_file_create","directory_generate","code_unit_generate","code_symbol_modify","code_patch","code_generation","code_execution","readme_generation","bug_fix","repair"}

def evaluate(task: dict[str, Any], response: Any) -> dict[str, Any]:
    result = {"task_id": task["id"], "stratum": task["stratum"], "plan_legal": False, "required_need": False, "malformed": False, "reason": ""}
    if not isinstance(response, dict) or not isinstance(response.get("decision_needs"), list):
        result.update(malformed=True, reason="missing decision_needs array")
        return result
    needs = response["decision_needs"]
    bad = [n for n in needs if not isinstance(n, dict) or n.get("need_type") not in ALLOWED_NEEDS]
    result["plan_legal"] = not bad
    result["required_need"] = bool(needs) or task["stratum"] in {"needs_user_input","ambiguous_scope","high_risk_stop","provider_malformed","provider_error"}
    if bad: result["reason"] = "unknown need_type or malformed need"
    return result

def load_json(path):
    with open(path, encoding="utf-8") as f: return json.load(f)
