"""Fail-closed readiness checks for the experiment-only provider paired pilot.

This module validates a frozen experiment manifest and response inventory.  It
does not load credentials, call a provider, create workspaces, or alter the
production runtime.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REQUIRED_SECTIONS = {
    "schema", "manifest_id", "treatment_variable", "corpus_version",
    "evaluator_version", "provider", "reasoning_policy", "budget",
    "retry_policy", "environment", "workspace", "pairing", "failure_policy",
    "quality_contract",
}
REQUIRED_FAILURES = {
    "empty_response", "malformed_response", "length_exhausted", "timeout",
    "provider_error", "tool_admission_failure", "validation_failure",
    "recovery_exhausted", "stopped", "user_input_required",
    "unknown_usage_or_finish_reason",
}
SECRET_KEYS = re.compile(r"(?:api[_-]?key|token|secret|password|credential)", re.I)
RESPONSE_NAME = re.compile(r"^(?P<task>[A-Za-z0-9_-]+)\.(?P<arm>control|treatment)\.(?P<repeat>[1-9][0-9]*)\.json$")


def _require_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _walk_for_secrets(value: Any, path: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEYS.search(str(key)):
                raise ValueError(f"credential-bearing field is forbidden: {path}.{key}")
            _walk_for_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_for_secrets(child, f"{path}[{index}]")


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate the preregistered provider-run contract and return it unchanged."""
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    missing = sorted(REQUIRED_SECTIONS - set(manifest))
    if missing:
        raise ValueError(f"manifest missing required sections: {', '.join(missing)}")
    _walk_for_secrets(manifest)
    if manifest.get("production_default") is not False:
        raise ValueError("provider paired pilot must keep production_default=false")
    _require_string(manifest["schema"], "schema")
    _require_string(manifest["manifest_id"], "manifest_id")
    _require_string(manifest["treatment_variable"], "treatment_variable")
    _require_string(manifest["corpus_version"], "corpus_version")
    _require_string(manifest["evaluator_version"], "evaluator_version")
    provider = manifest["provider"]
    for key in ("name", "endpoint_profile", "model", "capability_profile"):
        _require_string(provider.get(key) if isinstance(provider, dict) else None, f"provider.{key}")
    _require_string(manifest["reasoning_policy"], "reasoning_policy")
    budget = manifest["budget"]
    if not isinstance(budget, dict) or not isinstance(budget.get("profile"), str):
        raise ValueError("budget.profile is required")
    retry = manifest["retry_policy"]
    if (
        not isinstance(retry, dict)
        or not isinstance(retry.get("max_attempts"), int)
        or isinstance(retry.get("max_attempts"), bool)
        or retry["max_attempts"] < 1
        or retry.get("arm_symmetric") is not True
    ):
        raise ValueError("retry_policy must freeze positive max_attempts and arm_symmetric=true")
    environment = manifest["environment"]
    for key in ("runtime", "dependency_lock", "source_snapshot"):
        _require_string(environment.get(key) if isinstance(environment, dict) else None, f"environment.{key}")
    workspace = manifest["workspace"]
    if not isinstance(workspace, dict) or workspace.get("isolation") != "per_arm_repeat":
        raise ValueError("workspace.isolation must be per_arm_repeat")
    if workspace.get("mutable_state") != "none_shared_between_arms":
        raise ValueError("workspace.mutable_state must forbid sharing between arms")
    pairing = manifest["pairing"]
    if not isinstance(pairing, dict) or pairing.get("repeats_per_task_arm", 0) < 3:
        raise ValueError("pairing.repeats_per_task_arm must be at least 3")
    if pairing.get("response_file_pattern") != "<task-id>.<arm>.<repeat>.json":
        raise ValueError("response_file_pattern does not match the frozen pairing contract")
    if pairing.get("randomization") != "balanced_arm_order":
        raise ValueError("pairing.randomization must be balanced_arm_order")
    quality = manifest["quality_contract"]
    if not isinstance(quality, dict) or quality.get("primary_metric") != "acceptance_passed":
        raise ValueError("quality_contract.primary_metric must be acceptance_passed")
    if quality.get("noninferiority_margin") != -0.02 or quality.get("confidence") != "one-sided-95-percent":
        raise ValueError("non-inferiority margin/confidence must remain preregistered")
    failures = manifest["failure_policy"]
    if not isinstance(failures, list) or not REQUIRED_FAILURES.issubset(set(failures)):
        raise ValueError("failure_policy must retain every preregistered provider and execution failure")
    return manifest


def validate_response_inventory(directory: Path, task_ids: set[str], *, repeats: int) -> list[str]:
    """Validate names only; missing files are reported, never synthesized."""
    if repeats < 1 or not task_ids:
        raise ValueError("task_ids and repeats are required")
    expected = {
        f"{task}.{arm}.{repeat}.json"
        for task in task_ids for arm in ("control", "treatment")
        for repeat in range(1, repeats + 1)
    }
    actual = {item.name for item in directory.iterdir() if item.is_file()} if directory.exists() else set()
    unexpected = sorted(name for name in actual if name not in expected)
    malformed = sorted(name for name in actual if RESPONSE_NAME.match(name) is None)
    missing = sorted(expected - actual)
    if unexpected or malformed:
        raise ValueError(f"unexpected response files: {unexpected or malformed}")
    return missing
