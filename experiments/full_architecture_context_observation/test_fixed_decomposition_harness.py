from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fixed_decomposition_harness import (
    FIXED_DECOMPOSITION_INJECTION_POINT,
    collect_improvement_cost,
    evaluate_upstream_gate,
    install_fixed_decomposition,
    load_fixed_decomposition_fixture,
)


ROOT = Path(__file__).resolve().parent
PROTOCOL_PATH = ROOT / "PROJECT_IMPROVEMENT_PROVIDER_ARM_PROTOCOL_V1.json"
EXPECTED_FIXTURE_SHA256 = "sha256:62bc25e5240a11c8cc1171a7d578b7fc7595642ecf9136d3d31a092d79443de3"


class _ProductionDecomposerSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def decompose(self, *args, **kwargs):
        raise AssertionError("production decomposition must be the only replaced method")

    def build_task_graph(self, tasks):
        self.calls.append(("build_task_graph", tasks))
        return "production-graph"

    def get_execution_order(self, graph):
        self.calls.append(("get_execution_order", graph))
        return ["production-order"]

    def assemble_results(self, parent, subtasks):
        self.calls.append(("assemble_results", (parent, subtasks)))
        return {"source": "production-assembler"}


def _protocol() -> dict:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def _fixture_contract() -> tuple[Path, str]:
    contract = _protocol()["fixed_decomposition"]
    return ROOT / contract["fixture_path"], contract["fixture_sha256"]


def _llm_event(
    event_type: str,
    call_id: str,
    purpose: str,
    *,
    completion_budget: dict | None = None,
) -> dict:
    payload = {
        "correlation": {"execution_id": call_id},
        "context_selection": {"request_purpose": purpose},
    }
    if completion_budget is not None:
        payload["trace_info"] = {"completion_budget": completion_budget}
    if event_type == "llm_responded":
        payload["response_metadata"] = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            }
        }
    return {"event_type": event_type, "payload": payload}


def test_fixed_fixture_shape_ids_scopes_commands_and_hash_are_stable() -> None:
    fixture_path, expected_hash = _fixture_contract()

    fixture = load_fixed_decomposition_fixture(fixture_path, expected_hash)

    assert expected_hash == EXPECTED_FIXTURE_SHA256
    assert fixture.fixture_id == "calculator-four-stage-decomposition-v1"
    assert fixture.injection_point == FIXED_DECOMPOSITION_INJECTION_POINT
    assert [task.id for task in fixture.tasks] == [
        "inspect-calculator",
        "implement-divide-fix",
        "validate-pytest",
        "validate-compileall",
    ]
    assert [task.kind for task in fixture.tasks] == ["inspect", "implement", "validate", "validate"]
    assert [task.dependencies for task in fixture.tasks] == [
        [],
        ["inspect-calculator"],
        ["implement-divide-fix"],
        ["validate-pytest"],
    ]
    assert fixture.tasks[0].read_files == ["calculator.py", "test_calculator.py"]
    assert fixture.tasks[0].write_files == []
    assert fixture.tasks[1].read_files == ["calculator.py"]
    assert fixture.tasks[1].write_files == ["calculator.py"]
    assert fixture.tasks[2].validation_command == "python -m pytest -q"
    assert fixture.tasks[3].validation_command == "python -m compileall -q calculator.py"


def test_install_only_replaces_decompose_and_delegates_production_graph_order_and_assembly() -> None:
    fixture_path, expected_hash = _fixture_contract()
    production = _ProductionDecomposerSpy()
    autopilot = SimpleNamespace(task_decomposer=production)
    production_graph_method = production.build_task_graph.__func__
    production_order_method = production.get_execution_order.__func__
    production_assembly_method = production.assemble_results.__func__

    descriptor = install_fixed_decomposition(
        autopilot,
        fixture_path=fixture_path,
        expected_sha256=expected_hash,
    )
    decomposer = autopilot.task_decomposer
    decomposition = decomposer.decompose("Fix calculator", context={"project_path": "/tmp/project"})

    assert descriptor["injection_point"] == FIXED_DECOMPOSITION_INJECTION_POINT
    assert descriptor["fixture_sha256"] == expected_hash
    assert descriptor["validation_command_sha256"] == {
        "validate-pytest": "sha256:03b2d5596d232a4210386ff5610b26c7bb59cd1aa14f9531d5121955881b3d13",
        "validate-compileall": "sha256:3f8cff87438a1b32bdd0af6a04064448666768ac8824aa35c3e93aa904583d8d",
    }
    assert decomposer is production
    assert decomposer.build_task_graph.__func__ is production_graph_method
    assert decomposer.get_execution_order.__func__ is production_order_method
    assert decomposer.assemble_results.__func__ is production_assembly_method
    assert decomposer.build_task_graph(decomposition.subtasks) == "production-graph"
    assert decomposer.get_execution_order("graph") == ["production-order"]
    assert decomposer.assemble_results(decomposition.original_task, decomposition.subtasks) == {
        "source": "production-assembler"
    }
    assert [name for name, _ in production.calls] == [
        "build_task_graph",
        "get_execution_order",
        "assemble_results",
    ]

    # The experiment only supplies pending task contracts. Execution results,
    # tool success and completion evidence remain production-owned.
    assert all(task.status.value == "pending" for task in decomposition.subtasks)
    assert all(task.result is None and task.error is None for task in decomposition.subtasks)
    assert not hasattr(decomposer, "execute_task")


def test_upstream_failure_is_censored_and_does_not_enter_improvement_cost() -> None:
    events = [
        _llm_event("llm_requested", "pre-entry", "project_improvement"),
        _llm_event("llm_responded", "pre-entry", "project_improvement"),
        {
            "event_type": "task_completion_rejected",
            "payload": {"error": "exact validation evidence missing"},
        },
    ]

    gate = evaluate_upstream_gate(events)
    cost = collect_improvement_cost(events, gate)

    assert gate == {
        "status": "upstream_invalid",
        "censored": True,
        "improvement_started_sequence": None,
        "failure_reason": "exact validation evidence missing",
    }
    assert cost == {
        "eligible": False,
        "censored": True,
        "cost_conclusion_status": "not_applicable",
        "request_count": 0,
        "provider_totals": None,
        "responded_totals": None,
        "failed_attempt_totals": None,
        "observed_attempt_totals": None,
        "usage_coverage": None,
        "completion_budget_audit": {
            "reservation_count": 0,
            "reservations": [],
            "accounting_scope": (
                "request-time reservation facts only; reconciliation and refunds "
                "are not inferred"
            ),
        },
        "requests": [],
        "target_purpose_coverage": {
            "project_improvement": False,
            "iteration_goal": False,
            "iteration_task_design": False,
            "code_generation": False,
            "code_edit": False,
        },
        "missing_target_purposes": [
            "project_improvement",
            "iteration_goal",
            "iteration_task_design",
            "code_generation",
            "code_edit",
        ],
        "iteration_goal_mode": "missing",
    }


def test_improvement_cost_keeps_observed_cost_but_is_incomplete_when_purposes_are_missing() -> None:
    events = [
        _llm_event("llm_requested", "before", "project_improvement"),
        _llm_event("llm_responded", "before", "project_improvement"),
        {"sequence": 10, "event_type": "pipeline_started", "payload": {}},
        _llm_event("llm_requested", "target", "iteration_goal"),
        _llm_event("llm_responded", "target", "iteration_goal"),
    ]
    events[3]["sequence"] = 11
    events[4]["sequence"] = 12

    gate = evaluate_upstream_gate(events)
    cost = collect_improvement_cost(events, gate)

    assert gate["status"] == "valid"
    assert gate["censored"] is False
    assert gate["improvement_started_sequence"] == 10
    assert cost["eligible"] is False
    assert cost["censored"] is False
    assert cost["cost_conclusion_status"] == "incomplete"
    assert cost["request_count"] == 1
    assert cost["provider_totals"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }
    assert cost["responded_totals"] == cost["provider_totals"]
    assert cost["failed_attempt_totals"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    assert cost["usage_coverage"]["logical_request_usage_fraction"] == 1.0
    assert [request["call_id"] for request in cost["requests"]] == ["target"]
    assert cost["target_purpose_coverage"] == {
        "project_improvement": False,
        "iteration_goal": True,
        "iteration_task_design": False,
        "code_generation": False,
        "code_edit": False,
    }
    assert cost["missing_target_purposes"] == [
        "project_improvement",
        "iteration_task_design",
        "code_generation",
        "code_edit",
    ]
    assert cost["iteration_goal_mode"] == "provider"


def test_improvement_cost_is_complete_only_when_all_target_provider_purposes_are_observed() -> None:
    events = [{"sequence": 10, "event_type": "pipeline_started", "payload": {}}]
    for offset, purpose in enumerate(
        (
            "project_improvement",
            "iteration_goal",
            "iteration_task_design",
            "code_generation",
            "code_edit",
        ),
        start=1,
    ):
        request = _llm_event("llm_requested", purpose, purpose)
        response = _llm_event("llm_responded", purpose, purpose)
        request["sequence"] = 10 + (offset * 2) - 1
        response["sequence"] = 10 + (offset * 2)
        events.extend((request, response))

    gate = evaluate_upstream_gate(events)
    cost = collect_improvement_cost(events, gate)

    assert cost["eligible"] is True
    assert cost["censored"] is False
    assert cost["cost_conclusion_status"] == "complete"
    assert cost["target_purpose_coverage"] == {
        "project_improvement": True,
        "iteration_goal": True,
        "iteration_task_design": True,
        "code_generation": True,
        "code_edit": True,
    }
    assert cost["missing_target_purposes"] == []
    assert cost["iteration_goal_mode"] == "provider"
    assert cost["request_count"] == 5
    assert cost["provider_totals"]["total_tokens"] == 600


def test_deterministic_goal_maker_without_provider_request_remains_incomplete() -> None:
    events = [
        {"sequence": 10, "event_type": "pipeline_started", "payload": {}},
        {
            "sequence": 11,
            "event_type": "pipeline_progress",
            "payload": {
                "input_summary": {"event": "goal_maker"},
                "output_summary": {
                    "iteration": 0,
                    "goals": [{"id": "selected-candidate", "title": "Improve docs"}],
                },
            },
        },
        {
            "sequence": 12,
            **_llm_event("llm_requested", "analysis", "project_improvement"),
        },
        {
            "sequence": 13,
            **_llm_event("llm_responded", "analysis", "project_improvement"),
        },
        {
            "sequence": 14,
            **_llm_event("llm_requested", "design", "iteration_task_design"),
        },
        {
            "sequence": 15,
            **_llm_event("llm_responded", "design", "iteration_task_design"),
        },
    ]

    gate = evaluate_upstream_gate(events)
    cost = collect_improvement_cost(events, gate)

    assert cost["eligible"] is False
    assert cost["censored"] is False
    assert cost["cost_conclusion_status"] == "incomplete"
    assert cost["iteration_goal_mode"] == "deterministic"
    assert cost["missing_target_purposes"] == [
        "iteration_goal",
        "code_generation",
        "code_edit",
    ]
    assert cost["target_purpose_coverage"]["iteration_goal"] is False
    assert cost["request_count"] == 2
    assert cost["provider_totals"]["total_tokens"] == 240


def test_improvement_cost_counts_failed_attempt_usage_and_reservation_facts() -> None:
    events = [
        {"sequence": 10, "event_type": "pipeline_started", "payload": {}},
        {
            "sequence": 11,
            **_llm_event(
                "llm_requested",
                "failed-design",
                "iteration_task_design",
                completion_budget={
                    "purpose": "iteration_task_design",
                    "reservation_id": "enhancement:abc",
                    "reserved_tokens": 1_400,
                    "remaining_tokens": 8_600,
                    "recovery_of": "enhancement:parent",
                },
            ),
        },
        {
            "sequence": 12,
            "event_type": "llm_failed",
            "payload": {
                "correlation": {"execution_id": "failed-design"},
                "details": {
                    "provider_attempt": {
                        "usage": {
                            "prompt_tokens": 900,
                            "completion_tokens": 1_400,
                            "total_tokens": 2_300,
                        },
                        "finish_reason": "length",
                        "response_length": 0,
                    }
                },
            },
        },
    ]

    cost = collect_improvement_cost(events, evaluate_upstream_gate(events))

    assert cost["provider_totals"] == {
        "input_tokens": 900,
        "output_tokens": 1_400,
        "total_tokens": 2_300,
    }
    assert cost["responded_totals"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    assert cost["failed_attempt_totals"] == cost["provider_totals"]
    assert cost["observed_attempt_totals"] == cost["provider_totals"]
    assert cost["usage_coverage"] == {
        "logical_requests": 1,
        "logical_requests_with_observed_usage": 1,
        "logical_request_usage_fraction": 1.0,
    }
    assert cost["requests"] == [
        {
            "call_id": "failed-design",
            "purpose": "iteration_task_design",
            "sequence": 11,
            "status": "failed",
            "input_tokens": 900,
            "output_tokens": 1_400,
            "total_tokens": 2_300,
            "usage_observed": True,
            "finish_reason": "length",
            "response_length": 0,
            "completion_budget": {
                "reservation_id": "enhancement:abc",
                "reserved_tokens": 1_400,
                "remaining_tokens": 8_600,
                "recovery_of": "enhancement:parent",
            },
        }
    ]
    assert cost["completion_budget_audit"] == {
        "reservation_count": 1,
        "reservations": [
            {
                "call_id": "failed-design",
                "purpose": "iteration_task_design",
                "reservation_id": "enhancement:abc",
                "reserved_tokens": 1_400,
                "remaining_tokens": 8_600,
                "recovery_of": "enhancement:parent",
            }
        ],
        "accounting_scope": (
            "request-time reservation facts only; reconciliation and refunds "
            "are not inferred"
        ),
    }


def test_improvement_cost_reports_unknown_usage_without_inventing_zero_cost() -> None:
    events = [
        {"sequence": 10, "event_type": "pipeline_started", "payload": {}},
        {"sequence": 11, **_llm_event("llm_requested", "unknown", "code_generation")},
        {
            "sequence": 12,
            "event_type": "llm_failed",
            "payload": {
                "correlation": {"execution_id": "unknown"},
                "details": {"provider_attempt": {"usage": {}, "finish_reason": None}},
            },
        },
    ]

    cost = collect_improvement_cost(events, evaluate_upstream_gate(events))

    assert cost["requests"][0]["status"] == "failed"
    assert cost["requests"][0]["usage_observed"] is False
    assert cost["usage_coverage"] == {
        "logical_requests": 1,
        "logical_requests_with_observed_usage": 0,
        "logical_request_usage_fraction": 0.0,
    }
