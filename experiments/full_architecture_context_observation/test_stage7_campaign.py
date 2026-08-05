from __future__ import annotations

import json
from pathlib import Path

import pytest

import stage7_campaign
from stage7_campaign import (
    _canonical_validation_command,
    _usage,
    analyze_campaign_records,
    arm_stop_reasons,
    build_run_record,
    build_run_command,
    build_schedule,
    evaluate_spend_limits,
    load_campaign_protocol,
    validate_campaign_protocol,
)
from run_observation import configure_enhancement_budget_arm, experiment_memory_scope
from metadata import ContextRequestPurpose, RuntimeBudgetMetadata


def _record(pair: int, arm: str, total: int) -> dict:
    enhancement_total = total - 1_000
    return {
        "pair": pair,
        "arm": arm,
        "code_snapshot_sha256": "sha256:fixed",
        "provider_identity": {
            "provider": "openai-compatible",
            "model": "deepseek-v4-flash",
            "endpoint": "https://api.deepseek.com",
            "tool_event_reasoning_mode": "disabled",
            "reasoning_profile": "deepseek-chat-known:v1",
        },
        "enhancement_budget_arm": {
            "budget_mode": (
                "static_purpose_ceiling"
                if arm == "static"
                else "production_dynamic_policy"
            )
        },
        "memory_baseline_matches": True,
        "effective_arm_policy_matches": True,
        "quality_gate": {
            "passed": True,
            "signature": "sha256:equal-quality",
            "checks": {"core_success": True},
        },
        "usage": {
            "core": {"input_tokens": 600, "output_tokens": 400, "total_tokens": 1_000},
            "enhancement": {
                "input_tokens": enhancement_total // 2,
                "output_tokens": enhancement_total - enhancement_total // 2,
                "total_tokens": enhancement_total,
            },
            "lifecycle": {
                "input_tokens": 600 + enhancement_total // 2,
                "output_tokens": 400 + enhancement_total - enhancement_total // 2,
                "total_tokens": total,
            },
        },
        "enhancement": {
            "iteration_goal_mode": "deterministic",
            "usage_coverage": {
                "logical_requests": 5,
                "logical_requests_with_observed_usage": 5,
                "logical_request_usage_fraction": 1.0,
            },
            "target_purpose_coverage": {
                "project_improvement": True,
                "iteration_goal": True,
                "iteration_task_design": True,
                "code_generation": True,
                "code_edit": True,
            },
            "purpose_totals": {
                purpose: {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200}
                for purpose in (
                    "project_improvement",
                    "iteration_goal",
                    "iteration_task_design",
                    "code_generation",
                    "code_edit",
                )
            },
            "failed_attempt_totals": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "completion_budget_audit": {
                "reservation_count": 5,
                "recovery_count": 1,
                "reservations": [],
                "accounting_scope": "request-time facts only",
            },
        },
    }


def test_protocol_freezes_three_pairs_with_alternating_arm_order() -> None:
    protocol = load_campaign_protocol()

    validate_campaign_protocol(protocol)

    assert build_schedule(protocol) == [
        {"ordinal": 1, "pair": 1, "position": 1, "arm": "static"},
        {"ordinal": 2, "pair": 1, "position": 2, "arm": "dynamic"},
        {"ordinal": 3, "pair": 2, "position": 1, "arm": "dynamic"},
        {"ordinal": 4, "pair": 2, "position": 2, "arm": "static"},
        {"ordinal": 5, "pair": 3, "position": 1, "arm": "static"},
        {"ordinal": 6, "pair": 3, "position": 2, "arm": "dynamic"},
    ]
    assert protocol["analysis"]["enhancement_purposes"] == [
        "project_improvement",
        "iteration_goal",
        "iteration_task_design",
        "code_generation",
        "code_edit",
    ]
    assert protocol["cache_enabled"] is False
    assert protocol["token_limits"] == {
        "per_arm_hard": 45_000,
        "per_pair_warning": 75_000,
        "per_pair_hard": 90_000,
        "campaign_warning": 225_000,
        "campaign_hard": 270_000,
    }
    assert protocol["execution"]["transport_retries"] == 0
    assert protocol["analysis"]["required_provider_purposes"] == [
        "project_improvement",
        "iteration_task_design",
    ]
    assert protocol["analysis"]["mutation_purpose_any_of"] == [
        "code_generation",
        "code_edit",
    ]
    assert protocol["common_interventions"]["memory_baseline"]["strategy"] == (
        "isolated_empty"
    )


def test_stage7_usage_requires_both_components_and_derives_missing_total() -> None:
    complete, complete_observed = _usage(
        {"prompt_tokens": 7, "completion_tokens": 3}
    )
    assert complete_observed is True
    assert complete == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}

    for partial in (
        {"prompt_tokens": 7, "total_tokens": 10},
        {"completion_tokens": 3, "total_tokens": 10},
        {"total_tokens": 10},
    ):
        _, observed = _usage(partial)
        assert observed is False


def test_run_record_excludes_partial_usage_and_marks_failed_attempt_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "arm"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"guard_observation": {}}), encoding="utf-8"
    )
    events = [
        {
            "sequence": 1,
            "event_type": "llm_requested",
            "payload": {
                "correlation": {"execution_id": "partial-success"},
                "context_selection": {"request_purpose": "controller"},
            },
        },
        {
            "sequence": 2,
            "event_type": "llm_responded",
            "payload": {
                "correlation": {"execution_id": "partial-success"},
                "usage": {"prompt_tokens": 7, "total_tokens": 9},
            },
        },
        {
            "sequence": 3,
            "event_type": "llm_requested",
            "payload": {
                "correlation": {"execution_id": "partial-failure"},
                "context_selection": {
                    "request_purpose": "project_improvement"
                },
            },
        },
        {
            "sequence": 4,
            "event_type": "llm_failed",
            "payload": {
                "correlation": {"execution_id": "partial-failure"},
                "details": {
                    "provider_attempt": {
                        "usage": {"completion_tokens": 2, "total_tokens": 7}
                    }
                },
            },
        },
    ]
    monkeypatch.setattr(stage7_campaign, "_load_events", lambda _path: events)
    monkeypatch.setattr(
        stage7_campaign,
        "evaluate_upstream_gate",
        lambda _events: {"improvement_started_sequence": 2},
    )
    monkeypatch.setattr(
        stage7_campaign,
        "collect_improvement_cost",
        lambda _events, _gate: {
            "completion_budget_audit": {"reservations": []},
            "usage_coverage": {},
            "target_purpose_coverage": {},
            "failed_attempt_totals": {},
        },
    )
    monkeypatch.setattr(
        stage7_campaign,
        "evaluate_campaign_quality",
        lambda _manifest, _events, _protocol: {"passed": True},
    )

    record = build_run_record(
        run_dir,
        schedule_item={"pair": 1, "arm": "dynamic"},
        code_snapshot="sha256:test",
        protocol=load_campaign_protocol(),
    )

    assert record["overall_usage_coverage"] == 0.0
    assert record["unknown_failed_usage_count"] == 1
    assert record["usage"]["lifecycle"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def test_historical_v1_protocol_remains_readable_without_code_edit_rewrite() -> None:
    protocol = load_campaign_protocol(
        Path(__file__).with_name("STAGE7_COMPLETION_BUDGET_CAMPAIGN_V1.json")
    )

    validate_campaign_protocol(protocol)

    assert protocol["campaign_id"] == "stage7-enhancement-completion-budget-ab-v1"
    assert protocol["analysis"]["enhancement_purposes"] == [
        "project_improvement",
        "iteration_goal",
        "iteration_task_design",
        "code_generation",
    ]


def test_static_arm_uses_fixed_purpose_caps_and_dynamic_keeps_production_policy() -> None:
    class Agent:
        def __init__(self, runtime_budget: RuntimeBudgetMetadata) -> None:
            self.runtime_budget = runtime_budget

    class Autopilot:
        def __init__(self) -> None:
            self._local_enhancement_runtime_budget = RuntimeBudgetMetadata()
            self.iterative_improvement = Agent(self._local_enhancement_runtime_budget)
            self.runtime_controller = type(
                "Controller",
                (),
                {"state": type("State", (), {"budget": RuntimeBudgetMetadata()})()},
            )()

        def _enhancement_runtime_budget(self) -> RuntimeBudgetMetadata:
            return self.runtime_controller.state.budget

    autopilot = Autopilot()

    static = configure_enhancement_budget_arm(autopilot, "static")

    policy = autopilot._local_enhancement_runtime_budget.enhancement_completion_policy
    assert static["budget_mode"] == "static_purpose_ceiling"
    assert policy.recovery_step == 0
    assert policy.purpose_limits[ContextRequestPurpose.PROJECT_IMPROVEMENT].floor == 1500
    assert policy.purpose_limits[ContextRequestPurpose.PROJECT_IMPROVEMENT].ceiling == 1500
    assert policy.purpose_limits[ContextRequestPurpose.CODE_EDIT].floor == 1600
    assert policy.purpose_limits[ContextRequestPurpose.CODE_EDIT].ceiling == 1600
    assert autopilot.iterative_improvement.runtime_budget is autopilot._local_enhancement_runtime_budget
    assert autopilot._enhancement_runtime_budget() is autopilot._local_enhancement_runtime_budget
    assert autopilot.iterative_improvement._goal_from_candidate({}, {}, object()) is None
    assert (
        static["common_iteration_goal_intervention"]["injection_point"]
        == "AutonomousIterationAgent._goal_from_candidate"
    )

    dynamic_autopilot = Autopilot()
    dynamic = configure_enhancement_budget_arm(dynamic_autopilot, "dynamic")
    assert dynamic["budget_mode"] == "production_dynamic_policy"
    assert (
        dynamic_autopilot._local_enhancement_runtime_budget.enhancement_completion_policy
        == RuntimeBudgetMetadata().enhancement_completion_policy
    )
    assert (
        dynamic_autopilot._enhancement_runtime_budget()
        is dynamic_autopilot._local_enhancement_runtime_budget
    )

    fixed_goal_autopilot = Autopilot()
    fixed = configure_enhancement_budget_arm(
        fixed_goal_autopilot,
        "dynamic",
        iteration_goal_mode="fixed_divide_docstring",
    )
    goal = fixed_goal_autopilot.iterative_improvement._goal_from_candidate(
        {}, {}, object()
    )
    assert goal.id == "campaign-divide-docstring"
    assert goal.title == "Document divide's denominator-zero contract."
    assert fixed["iteration_goal_intervention"]["strategy"] == (
        "fixed_divide_docstring"
    )


def test_v4_run_command_passes_fixed_goal_and_isolated_memory_strategy() -> None:
    command = build_run_command(Path("/tmp/stage7-v4"), "dynamic", load_campaign_protocol())

    mode_index = command.index("--iteration-goal-mode")
    assert command[mode_index + 1] == "fixed_divide_docstring"
    memory_index = command.index("--memory-mode")
    assert command[memory_index + 1] == "isolated_empty"


def test_isolated_memory_scope_uses_an_empty_arm_local_store(tmp_path) -> None:
    import autonomous_iteration.intelligent_autopilot as autopilot_module

    with experiment_memory_scope("isolated_empty", tmp_path) as descriptor:
        store = autopilot_module.MemoryStore()

    assert descriptor == {
        "strategy": "isolated_empty",
        "data_dir": str((tmp_path / "isolated_memory").resolve()),
    }
    assert store.data_dir == (tmp_path / "isolated_memory").resolve()
    assert list(store.data_dir.glob("*.jsonl")) == []


def test_validation_commands_accept_project_venv_interpreter_prefix() -> None:
    assert (
        _canonical_validation_command(
            "/private/tmp/project/.venv/bin/python -m pytest -q"
        )
        == "python -m pytest -q"
    )
    assert (
        _canonical_validation_command(
            "/private/tmp/project/.venv/bin/python -m compileall -q calculator.py"
        )
        == "python -m compileall -q calculator.py"
    )


def test_campaign_analysis_requires_three_equal_quality_complete_coverage_pairs() -> None:
    protocol = load_campaign_protocol()
    records = []
    for pair in range(1, 4):
        records.extend((_record(pair, "static", 10_000), _record(pair, "dynamic", 7_000)))

    result = analyze_campaign_records(records, protocol)

    assert result["eligible"] is True
    assert result["quality_matched_pair_count"] == 3
    assert result["lifecycle_total_tokens"]["static"] == 30_000
    assert result["lifecycle_total_tokens"]["dynamic"] == 21_000
    assert result["window_total_tokens"] == {
        "core": {"static": 3_000, "dynamic": 3_000},
        "enhancement": {"static": 27_000, "dynamic": 18_000},
    }
    assert result["enhancement_purpose_total_tokens"]["code_generation"] == {
        "static": 600,
        "dynamic": 600,
    }
    assert result["enhancement_purpose_total_tokens"]["code_edit"] == {
        "static": 600,
        "dynamic": 600,
    }
    assert result["completion_budget_audit"] == {
        "static": {"reservations": 15, "recoveries": 3},
        "dynamic": {"reservations": 15, "recoveries": 3},
        "refunds_inferred": False,
    }
    assert result["lifecycle_total_token_change_fraction"] == -0.3
    assert result["claim_boundary"] == "mechanism_campaign_not_distribution_wide_causality"


def test_campaign_analysis_rejects_quality_mismatch_or_incomplete_usage() -> None:
    protocol = load_campaign_protocol()
    records = []
    for pair in range(1, 4):
        records.extend((_record(pair, "static", 10_000), _record(pair, "dynamic", 7_000)))
    records[3]["quality_gate"]["signature"] = "sha256:different"
    records[5]["enhancement"]["usage_coverage"]["logical_request_usage_fraction"] = 0.75

    result = analyze_campaign_records(records, protocol)

    assert result["eligible"] is False
    assert result["quality_matched_pair_count"] == 1
    assert result["excluded_pairs"] == {
        "2": ["quality_signature_mismatch"],
        "3": ["incomplete_usage_coverage"],
    }


def test_preflight_stop_reasons_and_spend_limits_are_fail_fast() -> None:
    protocol = load_campaign_protocol()
    record = _record(1, "dynamic", 46_000)
    record["overall_usage_coverage"] = 0.8
    record["unknown_failed_usage_count"] = 1
    record["transport_retry_count"] = 1
    record["effective_arm_policy_matches"] = False
    record["enhancement"]["target_purpose_coverage"]["code_generation"] = False

    assert arm_stop_reasons(record, protocol) == [
        "per_arm_token_hard_limit_exceeded",
        "incomplete_usage_coverage",
        "unknown_failed_usage",
        "transport_retry_observed",
        "effective_arm_policy_mismatch",
    ]

    record["enhancement"]["target_purpose_coverage"]["code_edit"] = False
    assert "incomplete_mutation_purpose_coverage" in arm_stop_reasons(
        record, protocol
    )

    record["memory_baseline_matches"] = False
    assert "memory_baseline_mismatch" in arm_stop_reasons(record, protocol)

    records = [_record(1, "static", 40_000), _record(1, "dynamic", 36_000)]
    spend = evaluate_spend_limits(records, protocol)
    assert spend["pair_totals"] == {"1": 76_000}
    assert spend["warnings"] == ["pair_1_warning_threshold_reached"]
    assert spend["hard_failures"] == []
