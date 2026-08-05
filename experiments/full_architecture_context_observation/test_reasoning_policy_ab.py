from __future__ import annotations

import json
from pathlib import Path

from reasoning_policy_ab import (
    apply_task_contract_filter,
    evaluate_decision_response,
    load_protocol,
    load_request,
    summarize_arm,
)


ROOT = Path(__file__).resolve().parent


def test_protocol_artifacts_are_hash_locked() -> None:
    protocol = load_protocol(ROOT / "REASONING_POLICY_AB_PROTOCOL_V1.json")

    assert len(protocol["samples"]) == 5
    assert protocol["budget_arms"] == {
        "economical_1200": {"policy": {"mode": "disabled"}, "max_tokens": 1200},
        "economical_800": {"policy": {"mode": "disabled"}, "max_tokens": 800},
    }
    for sample in protocol["samples"]:
        request = load_request(ROOT, sample)
        assert request.trace_info.get("context_purpose") == "tool_event_decision"


def test_evaluator_rejects_read_only_mutation_and_wrong_validation_substitute() -> None:
    inspect_sample = {
        "allowed_need_types": ["file_read"],
        "required_target_basenames": ["calculator.py"],
        "forbidden_target_basenames": [],
        "required_commands": [],
        "allowed_commands": [],
    }
    validation_sample = {
        "allowed_need_types": ["command_check"],
        "required_target_basenames": [],
        "forbidden_target_basenames": [],
        "required_commands": ["python -m pytest -q"],
        "allowed_commands": ["python -m pytest -q"],
    }

    mutation = evaluate_decision_response(
        inspect_sample,
        {"decision_needs": [{"need_type": "file_write", "target_path": "/p/calculator.py"}]},
    )
    substitute = evaluate_decision_response(
        validation_sample,
        {"decision_needs": [{"need_type": "command_check", "command": "python -m compileall -q calculator.py"}]},
    )

    assert mutation["quality_pass"] is False
    assert mutation["critical_violation"] is True
    assert substitute["quality_pass"] is False
    assert "missing_required_command" in substitute["issues"]
    assert "unexpected_command" in substitute["issues"]


def test_evaluator_accepts_exact_requested_action() -> None:
    sample = {
        "allowed_need_types": ["command_check"],
        "required_target_basenames": [],
        "forbidden_target_basenames": [],
        "required_commands": ["python -m pytest -q"],
        "allowed_commands": ["python -m pytest -q"],
    }

    result = evaluate_decision_response(
        sample,
        {"decision_needs": [{"need_type": "command_check", "command": "python -m pytest -q"}]},
    )

    assert result["quality_pass"] is True
    assert result["critical_violation"] is False


def test_task_contract_filter_removes_future_validation_before_quality_judgment() -> None:
    sample = {
        "contract_kind": "validate",
        "required_commands": ["python -m pytest -q"],
    }
    parsed = {
        "decision_needs": [
            {"need_type": "command_check", "command": "python -m pytest -q"},
            {"need_type": "command_check", "command": "python -m compileall -q calculator.py"},
        ]
    }

    filtered = apply_task_contract_filter(sample, parsed)

    assert filtered == {
        "decision_needs": [
            {"need_type": "command_check", "command": "python -m pytest -q"}
        ]
    }


def test_arm_summary_does_not_claim_cost_win_before_quality_gate() -> None:
    summary = summarize_arm(
        "economical",
        [
            {
                "quality": {"quality_pass": False, "critical_violation": True},
                "usage": {"completion_tokens": 10, "reasoning_tokens": 1, "total_tokens": 20},
                "duration_ms": 5,
            }
        ],
    )

    assert summary["quality_gate_passed"] is False
    assert summary["cost_eligible"] is False
