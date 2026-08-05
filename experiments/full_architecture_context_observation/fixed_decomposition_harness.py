"""Narrow fixed-decomposition adapter for the project-improvement provider run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autonomous_iteration.task_models import Task, TaskDecompositionResult


FIXED_DECOMPOSITION_INJECTION_POINT = "IntelligentAutopilot.task_decomposer.decompose"
TARGET_IMPROVEMENT_PURPOSES = (
    "project_improvement",
    "iteration_goal",
    "iteration_task_design",
    "code_generation",
    "code_edit",
)
COMPLETION_BUDGET_ACCOUNTING_SCOPE = (
    "request-time reservation facts only; reconciliation and refunds are not inferred"
)


class FixedTaskContract(BaseModel):
    """One frozen task contract; it contains no execution outcome fields."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    kind: Literal["inspect", "implement", "validate"]
    read_files: list[str] = Field(default_factory=list)
    write_files: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    validation_command: str = ""
    estimated_effort: float = Field(default=1.0, gt=0)


class FixedDecompositionFixture(BaseModel):
    """Strict experiment-owned input used only by TaskDecomposer.decompose."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    fixture_id: str = Field(min_length=1)
    source_run: str = Field(min_length=1)
    injection_point: Literal[FIXED_DECOMPOSITION_INJECTION_POINT]
    original_task_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    tasks: list[FixedTaskContract] = Field(min_length=1)

    @model_validator(mode="after")
    def _dependencies_are_a_prior_task_prefix(self) -> "FixedDecompositionFixture":
        task_ids = [task.id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("fixed decomposition task IDs must be unique")
        prior_ids: set[str] = set()
        for task in self.tasks:
            if not set(task.dependencies).issubset(prior_ids):
                raise ValueError("fixed decomposition dependencies must reference prior tasks")
            prior_ids.add(task.id)
        return self


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def load_fixed_decomposition_fixture(
    fixture_path: str | Path,
    expected_sha256: str,
) -> FixedDecompositionFixture:
    """Load one exact fixture and fail closed if its bytes changed."""
    path = Path(fixture_path).resolve()
    raw = path.read_bytes()
    actual_sha256 = _sha256(raw)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"fixed decomposition fixture hash mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    fixture = FixedDecompositionFixture.model_validate(json.loads(raw.decode("utf-8")))
    if fixture.injection_point != FIXED_DECOMPOSITION_INJECTION_POINT:
        raise ValueError("fixed decomposition fixture targets an unsupported injection point")
    return fixture


class FixedDecompositionProvider:
    """Provide the sole replacement method for a production TaskDecomposer."""

    def __init__(
        self,
        fixture: FixedDecompositionFixture,
    ) -> None:
        self.fixture = fixture

    def decompose(
        self,
        task_description: str,
        context: dict[str, Any] | None = None,
        parent_task_id: str | None = None,
    ) -> TaskDecompositionResult:
        original_task = Task(
            id=self.fixture.original_task_id,
            description=task_description,
            parent_id=parent_task_id,
            attributes={
                "context": dict(context or {}),
                "fixed_decomposition_fixture_id": self.fixture.fixture_id,
            },
        )
        subtasks = [
            Task(
                id=contract.id,
                description=contract.description,
                parent_id=original_task.id,
                kind=contract.kind,
                read_files=list(contract.read_files),
                write_files=list(contract.write_files),
                dependencies=list(contract.dependencies),
                validation_command=contract.validation_command,
                estimated_effort=contract.estimated_effort,
                can_run_parallel=False,
                attributes={"fixed_decomposition_fixture_id": self.fixture.fixture_id},
            )
            for contract in self.fixture.tasks
        ]
        return TaskDecompositionResult(
            original_task=original_task,
            subtasks=subtasks,
            task_graph_summary=(
                f"Fixed experiment decomposition {self.fixture.fixture_id}: "
                + " -> ".join(task.id for task in subtasks)
            ),
            decomposition_rationale=self.fixture.rationale,
            estimated_total_effort=sum(task.estimated_effort or 0.0 for task in subtasks),
        )

def install_fixed_decomposition(
    autopilot: Any,
    *,
    fixture_path: str | Path,
    expected_sha256: str,
) -> dict[str, Any]:
    """Install the narrow adapter and return the manifest evidence."""
    fixture_path = Path(fixture_path).resolve()
    fixture = load_fixed_decomposition_fixture(fixture_path, expected_sha256)
    production_decomposer = autopilot.task_decomposer
    provider = FixedDecompositionProvider(fixture)
    production_decomposer.decompose = provider.decompose
    return {
        "fixture_id": fixture.fixture_id,
        "fixture_path": str(fixture_path),
        "fixture_sha256": expected_sha256,
        "source_run": fixture.source_run,
        "injection_point": fixture.injection_point,
        "replaced_method": "decompose",
        "validation_command_sha256": {
            task.id: _sha256(task.validation_command.encode("utf-8"))
            for task in fixture.tasks
            if task.validation_command
        },
        "delegated_methods": [
            "build_task_graph",
            "get_execution_order",
            "assemble_results",
        ],
    }


def _event_sequence(event: dict[str, Any], index: int) -> int:
    value = event.get("sequence")
    return int(value) if value is not None else index + 1


def evaluate_upstream_gate(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Mark a run valid only once production reaches project improvement."""
    for index, event in enumerate(events):
        if event.get("event_type") == "pipeline_started":
            return {
                "status": "valid",
                "censored": False,
                "improvement_started_sequence": _event_sequence(event, index),
                "failure_reason": None,
            }

    failure_reason = "project improvement pipeline was not reached"
    for event in reversed(events):
        if event.get("event_type") not in {
            "task_completion_rejected",
            "tool_failed",
            "llm_failed",
            "task_finished",
        }:
            continue
        payload = event.get("payload") or {}
        candidate = payload.get("error") or payload.get("error_message")
        if not candidate and event.get("event_type") != "task_finished":
            candidate = (payload.get("output_summary") or {}).get("failure_reason")
        if candidate:
            failure_reason = str(candidate)
            break
    return {
        "status": "upstream_invalid",
        "censored": True,
        "improvement_started_sequence": None,
        "failure_reason": failure_reason,
    }


def _request_purpose(payload: dict[str, Any]) -> str:
    request_metadata = payload.get("request_metadata") or payload
    selection = request_metadata.get("context_selection") or {}
    return str(selection.get("request_purpose") or request_metadata.get("purpose") or "")


def _usage_value(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if usage.get(key) is not None:
            return int(usage[key])
    return 0


def _usage_observed(usage: dict[str, Any]) -> bool:
    return any(
        usage.get(key) is not None
        for key in (
            "input_tokens",
            "prompt_tokens",
            "output_tokens",
            "completion_tokens",
            "total_tokens",
        )
    )


def _normalized_usage(usage: dict[str, Any]) -> dict[str, int]:
    input_tokens = _usage_value(usage, "input_tokens", "prompt_tokens")
    output_tokens = _usage_value(usage, "output_tokens", "completion_tokens")
    total_tokens = _usage_value(usage, "total_tokens") or input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _empty_totals() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _add_totals(target: dict[str, int], usage: dict[str, int]) -> None:
    for key in target:
        target[key] += usage[key]


def _completion_budget_facts(payload: dict[str, Any]) -> dict[str, Any] | None:
    request_metadata = payload.get("request_metadata") or payload
    trace_info = request_metadata.get("trace_info") or {}
    budget = trace_info.get("completion_budget") or {}
    facts = {
        "reservation_id": budget.get("reservation_id"),
        "reserved_tokens": budget.get("reserved_tokens"),
        "remaining_tokens": budget.get("remaining_tokens"),
        "recovery_of": budget.get("recovery_of"),
    }
    return facts if any(value is not None for value in facts.values()) else None


def collect_improvement_cost(
    events: list[dict[str, Any]],
    upstream_gate: dict[str, Any],
) -> dict[str, Any]:
    """Collect target-stage cost only for runs that passed the upstream gate."""
    if upstream_gate.get("status") != "valid":
        return {
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
                "accounting_scope": COMPLETION_BUDGET_ACCOUNTING_SCOPE,
            },
            "requests": [],
            "target_purpose_coverage": {
                purpose: False for purpose in TARGET_IMPROVEMENT_PURPOSES
            },
            "missing_target_purposes": list(TARGET_IMPROVEMENT_PURPOSES),
            "iteration_goal_mode": "missing",
        }

    start_sequence = int(upstream_gate["improvement_started_sequence"])
    requests_by_id: dict[str, dict[str, Any]] = {}
    responses_by_id: dict[str, dict[str, Any]] = {}
    failures_by_id: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events):
        if _event_sequence(event, index) <= start_sequence:
            continue
        payload = event.get("payload") or {}
        correlation = payload.get("correlation") or {}
        call_id = str(payload.get("call_id") or correlation.get("execution_id") or "")
        if not call_id:
            continue
        if event.get("event_type") == "llm_requested":
            purpose = _request_purpose(payload)
            if purpose in TARGET_IMPROVEMENT_PURPOSES:
                requests_by_id[call_id] = {
                    "call_id": call_id,
                    "purpose": purpose,
                    "sequence": _event_sequence(event, index),
                    "completion_budget": _completion_budget_facts(payload),
                }
        elif event.get("event_type") == "llm_responded" and call_id in requests_by_id:
            responses_by_id[call_id] = payload
        elif event.get("event_type") == "llm_failed" and call_id in requests_by_id:
            failures_by_id[call_id] = payload

    requests = []
    responded_totals = _empty_totals()
    failed_attempt_totals = _empty_totals()
    reservations = []
    logical_requests_with_usage = 0
    for call_id, request in requests_by_id.items():
        response = responses_by_id.get(call_id)
        failure = failures_by_id.get(call_id)
        finish_reason = None
        response_length = None
        if response is not None:
            response_metadata = response.get("response_metadata") or response
            raw_usage = response_metadata.get("usage") or {}
            finish_reason = response_metadata.get("finish_reason")
            status = "responded"
        elif failure is not None:
            failure_metadata = failure.get("failure") or failure
            provider_attempt = (failure_metadata.get("details") or {}).get(
                "provider_attempt"
            ) or {}
            raw_usage = provider_attempt.get("usage") or {}
            finish_reason = provider_attempt.get("finish_reason")
            response_length = provider_attempt.get("response_length")
            status = "failed"
        else:
            raw_usage = {}
            status = "unmatched"
        usage_observed = _usage_observed(raw_usage)
        usage = _normalized_usage(raw_usage)
        if usage_observed:
            logical_requests_with_usage += 1
            if status == "responded":
                _add_totals(responded_totals, usage)
            elif status == "failed":
                _add_totals(failed_attempt_totals, usage)
        completion_budget = request.pop("completion_budget")
        requests.append(
            {
                **request,
                "status": status,
                **usage,
                "usage_observed": usage_observed,
                "finish_reason": finish_reason,
                "response_length": response_length,
                "completion_budget": completion_budget,
            }
        )
        if completion_budget is not None:
            reservations.append(
                {
                    "call_id": call_id,
                    "purpose": request["purpose"],
                    **completion_budget,
                }
            )
    observed_attempt_totals = {
        key: responded_totals[key] + failed_attempt_totals[key]
        for key in responded_totals
    }
    observed_purposes = {request["purpose"] for request in requests_by_id.values()}
    target_purpose_coverage = {
        purpose: purpose in observed_purposes
        for purpose in TARGET_IMPROVEMENT_PURPOSES
    }
    missing_target_purposes = [
        purpose
        for purpose in TARGET_IMPROVEMENT_PURPOSES
        if not target_purpose_coverage[purpose]
    ]
    if target_purpose_coverage["iteration_goal"]:
        iteration_goal_mode = "provider"
    elif _has_deterministic_goal_maker_result(events, start_sequence):
        iteration_goal_mode = "deterministic"
    else:
        iteration_goal_mode = "missing"
    complete = not missing_target_purposes
    return {
        "eligible": complete,
        "censored": False,
        "cost_conclusion_status": "complete" if complete else "incomplete",
        "request_count": len(requests),
        "provider_totals": observed_attempt_totals,
        "responded_totals": responded_totals,
        "failed_attempt_totals": failed_attempt_totals,
        "observed_attempt_totals": observed_attempt_totals,
        "usage_coverage": {
            "logical_requests": len(requests),
            "logical_requests_with_observed_usage": logical_requests_with_usage,
            "logical_request_usage_fraction": (
                logical_requests_with_usage / len(requests) if requests else None
            ),
        },
        "completion_budget_audit": {
            "reservation_count": len(reservations),
            "reservations": reservations,
            "accounting_scope": COMPLETION_BUDGET_ACCOUNTING_SCOPE,
        },
        "requests": requests,
        "target_purpose_coverage": target_purpose_coverage,
        "missing_target_purposes": missing_target_purposes,
        "iteration_goal_mode": iteration_goal_mode,
    }


def _has_deterministic_goal_maker_result(
    events: list[dict[str, Any]],
    start_sequence: int,
) -> bool:
    for index, event in enumerate(events):
        if _event_sequence(event, index) <= start_sequence:
            continue
        if event.get("event_type") != "pipeline_progress":
            continue
        payload = event.get("payload") or {}
        input_summary = payload.get("input_summary") or {}
        output_summary = payload.get("output_summary") or {}
        if input_summary.get("event") != "goal_maker":
            continue
        goals = output_summary.get("goals")
        if isinstance(goals, list) and goals:
            return True
    return False
