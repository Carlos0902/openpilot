import json
from pathlib import Path

import pytest

from .provider_readiness import validate_manifest, validate_response_inventory


def _manifest():
    return {
        "schema": "harness-slimming-provider-paired-v1", "manifest_id": "pilot-2026-01",
        "treatment_variable": "planner_prompt_tutorial", "corpus_version": "holdout-v1",
        "evaluator_version": "evaluator.py@frozen", "provider": {"name": "provider-x", "endpoint_profile": "endpoint-v1", "model": "model-y", "capability_profile": "cap-v1"},
        "reasoning_policy": "balanced-v3", "budget": {"profile": "completion-budget-v1"},
        "retry_policy": {"max_attempts": 1, "arm_symmetric": True}, "environment": {"runtime": "python-3.13", "dependency_lock": "lock-v1", "source_snapshot": "snapshot-v1"},
        "workspace": {"isolation": "per_arm_repeat", "mutable_state": "none_shared_between_arms"},
        "pairing": {"repeats_per_task_arm": 3, "response_file_pattern": "<task-id>.<arm>.<repeat>.json", "randomization": "balanced_arm_order"},
        "failure_policy": ["empty_response", "malformed_response", "length_exhausted", "timeout", "provider_error", "tool_admission_failure", "validation_failure", "recovery_exhausted", "stopped", "user_input_required", "unknown_usage_or_finish_reason"],
        "quality_contract": {"primary_metric": "acceptance_passed", "noninferiority_margin": -0.02, "confidence": "one-sided-95-percent"},
        "production_default": False,
    }


def test_manifest_freezes_required_contract_and_rejects_secrets():
    assert validate_manifest(_manifest())["production_default"] is False
    with pytest.raises(ValueError, match="credential"):
        bad = _manifest(); bad["api_key"] = "do-not-store"
        validate_manifest(bad)


def test_manifest_rejects_shared_workspace_or_asymmetric_retry():
    with pytest.raises(ValueError, match="per_arm_repeat"):
        bad = _manifest(); bad["workspace"]["isolation"] = "shared"
        validate_manifest(bad)
    with pytest.raises(ValueError, match="max_attempts"):
        bad = _manifest(); bad["retry_policy"]["arm_symmetric"] = False
        validate_manifest(bad)


def test_inventory_reports_missing_and_rejects_unexpected(tmp_path: Path):
    (tmp_path / "t1.control.1.json").write_text("{}", encoding="utf-8")
    missing = validate_response_inventory(tmp_path, {"t1"}, repeats=3)
    assert "t1.treatment.1.json" in missing
    (tmp_path / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected"):
        validate_response_inventory(tmp_path, {"t1"}, repeats=3)
