"""Frozen-source full-architecture A/B campaign for Task Designer projections."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

import autonomous_iteration.agents.iteration_agent as iteration_agent_module
from autonomous_iteration.enhancement_completion_budget import (
    EnhancementCompletionBudgetCoordinator,
)
from autonomous_iteration.models import ImprovementGoal, ProjectStateSnapshot
from core.config import LLMSettings
from metadata import (
    ContextRequestPurpose,
    EnhancementCompletionComplexity,
    EnhancementCompletionDecisionValue,
    EnhancementCompletionRequest,
    EnhancementCompletionRequirement,
    RuntimeBudgetMetadata,
)
from run_observation import _load_events, provider_runtime_identity
from stage7_campaign import (
    build_run_record as build_stage7_run_record,
    code_snapshot_sha256,
    evaluate_spend_limits,
)


HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "STAGE8_TASK_DESIGNER_CONTEXT_CAMPAIGN_V1.json"
RUNNER_PATH = HERE / "run_observation.py"
ARMS = {"current", "compact"}


class FrozenSourceError(RuntimeError):
    """A frozen experiment source is missing, changed, or structurally invalid."""


@dataclass(frozen=True)
class FrozenTaskDesignerSource:
    project_state: ProjectStateSnapshot
    improvement_report: dict[str, Any]
    goal: ImprovementGoal
    completed_iteration: int
    source_fingerprint: str
    source_event_sequences: dict[str, int]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _project_relative_source(value: Any, project_root: str) -> Any:
    roots = {project_root.rstrip("/")}
    try:
        roots.add(str(Path(project_root).resolve()).rstrip("/"))
    except OSError:
        pass
    for root in list(roots):
        if root.startswith("/private/"):
            roots.add(root[len("/private") :])
        elif root.startswith("/var/"):
            roots.add("/private" + root)
    replacements = sorted((root for root in roots if root), key=len, reverse=True)

    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, str):
            result = item
            for root in replacements:
                result = result.replace(root, ".")
            return result
        return item

    return normalize(value)


def load_campaign_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_campaign_protocol(protocol: Mapping[str, Any]) -> None:
    if protocol.get("campaign_id") != "stage8-task-designer-context-projection-ab-v1":
        raise ValueError("unexpected Stage 8 campaign id")
    if protocol.get("status") != "refrozen_after_diagnostic_pair":
        raise ValueError("Stage 8 must identify the diagnostic-pair refreeze")
    if int(protocol.get("pairs") or 0) != 3:
        raise ValueError("Stage 8 requires exactly three pairs")
    expected_orders = [["current", "compact"], ["compact", "current"], ["current", "compact"]]
    if protocol.get("pair_arm_order") != expected_orders:
        raise ValueError("Stage 8 pair order is not the frozen crossed order")
    arms = protocol.get("arms") or {}
    if set(arms) != ARMS or any(
        arms[arm] != {"projection_policy": arm} for arm in ARMS
    ):
        raise ValueError("projection policy must be the only arm-specific setting")
    common = protocol.get("common_interventions") or {}
    expected_common = {
        "completion_policy": "production_dynamic_policy",
        "fixed_iteration_goal": "fixed_divide_docstring",
        "memory_baseline": "isolated_empty",
        "task_designer_source": "frozen_verified_v4_payloads",
    }
    if common != expected_common:
        raise ValueError("Stage 8 common interventions changed")
    call = protocol.get("task_designer_call_contract") or {}
    if call != {
        "request_count": 1,
        "reservation_derivation": "production_enhancement_completion_budget",
        "reservation_must_match_derived_value": True,
        "prompt_bonus_threshold": 3000,
        "complexity": "routine",
        "remaining_value": "high",
        "remaining_calls": 3,
        "reasoning_mode": "disabled",
        "transport_retries": 0,
        "required_omitted_count": 0,
        "required_partial_count": 0,
    }:
        raise ValueError("Task Designer call contract changed")
    if protocol.get("cache_enabled") is not False:
        raise ValueError("Stage 8 cache must be disabled")
    if int((protocol.get("execution") or {}).get("transport_retries", -1)) != 0:
        raise ValueError("Stage 8 transport retries must be zero")
    diagnostic = protocol.get("diagnostic_only_runs") or []
    if (
        len(diagnostic) != 1
        or diagnostic[0].get("path")
        != "runs/stage8_campaign_20260804T084822Z"
        or int(diagnostic[0].get("completed_pairs") or 0) != 1
        or len(diagnostic[0].get("reasons") or []) != 2
    ):
        raise ValueError("Stage 8 diagnostic-only first pair contract changed")


def build_schedule(protocol: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_campaign_protocol(protocol)
    schedule = []
    ordinal = 0
    for pair, order in enumerate(protocol["pair_arm_order"], start=1):
        for position, arm in enumerate(order, start=1):
            ordinal += 1
            schedule.append(
                {"ordinal": ordinal, "pair": pair, "position": position, "arm": arm}
            )
    return schedule


def load_frozen_task_designer_source(
    protocol: Mapping[str, Any],
) -> FrozenTaskDesignerSource:
    source = protocol["frozen_task_designer_source"]
    events_path = HERE / str(source["events_path"])
    try:
        event_bytes = events_path.read_bytes()
    except OSError as exc:
        raise FrozenSourceError(f"frozen events unavailable: {events_path}") from exc
    actual_file_hash = _sha256(event_bytes)
    if actual_file_hash != source["events_sha256"]:
        raise FrozenSourceError(
            f"events file hash mismatch: expected {source['events_sha256']}, got {actual_file_hash}"
        )
    events = {
        int(event["sequence"]): event
        for line in event_bytes.decode("utf-8").splitlines()
        if line.strip()
        for event in [json.loads(line)]
    }
    extracted: dict[str, Any] = {}
    sequences: dict[str, int] = {}
    for name in ("project_state", "improvement_report"):
        contract = source[name]
        sequence = int(contract["sequence"])
        event = events.get(sequence)
        if event is None or event.get("event_type") != "pipeline_progress":
            raise FrozenSourceError(f"{name} source event mismatch")
        payload = event.get("payload") or {}
        if (payload.get("input_summary") or {}).get("event") != contract["pipeline_event"]:
            raise FrozenSourceError(f"{name} pipeline event mismatch")
        output = payload.get("output_summary") or {}
        if contract["output_key"] not in output:
            raise FrozenSourceError(f"{name} output key missing")
        value = output[contract["output_key"]]
        actual_payload_hash = _sha256(_canonical_bytes(value))
        if actual_payload_hash != contract["payload_sha256"]:
            raise FrozenSourceError(
                f"{name} payload hash mismatch: expected {contract['payload_sha256']}, "
                f"got {actual_payload_hash}"
            )
        extracted[name] = value
        sequences[name] = sequence
    raw_state = extracted["project_state"]
    project_root = str(raw_state.get("project_path") or "")
    normalized_state = _project_relative_source(raw_state, project_root)
    normalized_report = _project_relative_source(
        extracted["improvement_report"], project_root
    )
    goal = ImprovementGoal.model_validate(source["goal"])
    completed_iteration = int(source["completed_iteration"])
    fingerprint_payload = {
        "normalization": source["normalization"],
        "project_state": normalized_state,
        "improvement_report": normalized_report,
        "goal": goal.model_dump(mode="json"),
        "completed_iteration": completed_iteration,
    }
    source_fingerprint = _sha256(_canonical_bytes(fingerprint_payload))
    if source_fingerprint != source["normalized_source_fingerprint"]:
        raise FrozenSourceError(
            "normalized source fingerprint mismatch: "
            f"expected {source['normalized_source_fingerprint']}, got {source_fingerprint}"
        )
    return FrozenTaskDesignerSource(
        project_state=ProjectStateSnapshot.model_validate(normalized_state),
        improvement_report=dict(normalized_report),
        goal=goal,
        completed_iteration=completed_iteration,
        source_fingerprint=source_fingerprint,
        source_event_sequences=sequences,
    )


@contextmanager
def task_designer_context_scope(
    arm: str,
    frozen: FrozenTaskDesignerSource,
    *,
    agent: Any | None = None,
):
    """Inject identical verified facts and vary only production projection policy."""

    if arm not in ARMS:
        raise ValueError(f"unsupported Task Designer context arm: {arm}")
    production_builder = iteration_agent_module.build_iteration_task_design_candidates
    observed_complexities: list[str] = []
    observed_request_policies: list[dict[str, Any]] = []

    def build_from_frozen_source(**runtime_kwargs: Any):
        live_state = runtime_kwargs.get("project_state")
        safe_targets = list(
            getattr(live_state, "safe_target_files", None)
            or getattr(live_state, "written_files", None)
            or []
        )
        observed_complexities.append("complex" if len(safe_targets) > 1 else "routine")
        return production_builder(
            project_state=frozen.project_state,
            goal=frozen.goal,
            improvement_report=frozen.improvement_report,
            completed_iteration=frozen.completed_iteration,
            projection_policy=arm,
        )

    descriptor = {
        "projection_policy": arm,
        "source_fingerprint": frozen.source_fingerprint,
        "source_event_sequences": frozen.source_event_sequences,
        "injection_point": (
            "autonomous_iteration.agents.iteration_agent."
            "build_iteration_task_design_candidates"
        ),
        "production_builder": (
            "autonomous_iteration.project_improvement_context."
            "build_iteration_task_design_candidates"
        ),
        "observed_complexities": observed_complexities,
        "observed_request_policies": observed_request_policies,
    }
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                iteration_agent_module,
                "build_iteration_task_design_candidates",
                new=build_from_frozen_source,
            )
        )
        if agent is not None:
            complete_candidates = agent._complete_json_candidates

            def observe_request_policy(candidates: list[Any], **kwargs: Any):
                purpose = kwargs.get("purpose")
                if purpose == ContextRequestPurpose.ITERATION_TASK_DESIGN:
                    complexity = kwargs.get("complexity")
                    remaining_value = kwargs.get("remaining_value")
                    observed_request_policies.append(
                        {
                            "complexity": str(
                                getattr(complexity, "value", complexity) or ""
                            ),
                            "remaining_value": str(
                                getattr(remaining_value, "value", remaining_value)
                                or ""
                            ),
                            "remaining_calls": int(kwargs.get("remaining_calls") or 0),
                        }
                    )
                return complete_candidates(candidates, **kwargs)

            stack.enter_context(
                patch.object(
                    agent,
                    "_complete_json_candidates",
                    new=observe_request_policy,
                )
            )
        yield descriptor


def _task_designer_observation(events: list[dict[str, Any]]) -> dict[str, Any]:
    requests = []
    outcomes: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event.get("payload") or {}
        execution_id = str((payload.get("correlation") or {}).get("execution_id") or "")
        if event.get("event_type") == "llm_requested":
            selection = payload.get("context_selection") or {}
            if selection.get("request_purpose") == "iteration_task_design":
                requests.append((execution_id, payload))
        elif event.get("event_type") == "llm_responded" and execution_id:
            metadata = payload.get("response_metadata") or payload
            outcomes[execution_id] = metadata.get("usage") or {}
        elif event.get("event_type") == "llm_failed" and execution_id:
            failure = payload.get("failure") or payload
            outcomes[execution_id] = (
                ((failure.get("details") or {}).get("provider_attempt") or {}).get("usage")
                or {}
            )
    if len(requests) != 1:
        return {"request_count": len(requests), "usage_observed": False}
    execution_id, payload = requests[0]
    selection = payload.get("context_selection") or {}
    diagnostics = (payload.get("trace_info") or {}).get("diagnostics") or {}
    reservation = (payload.get("trace_info") or {}).get("completion_budget") or {}
    usage = outcomes.get(execution_id) or {}
    input_value = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_value = usage.get("output_tokens", usage.get("completion_tokens"))
    decisions = selection.get("candidate_decisions") or []
    return {
        "request_count": 1,
        "provider_input_tokens": int(input_value or 0),
        "provider_output_tokens": int(output_value or 0),
        "final_prompt_tokens": int(selection.get("final_prompt_tokens") or 0),
        "reserved_completion_tokens": int(reservation.get("reserved_tokens") or 0),
        "remaining_tokens_after_reservation": int(
            reservation.get("remaining_tokens") or 0
        ),
        "reasoning_mode": str((payload.get("reasoning_policy") or {}).get("mode") or ""),
        "transport_retries": int(diagnostics.get("transport_retries") or 0),
        "omitted_required_candidate_ids": list(
            selection.get("omitted_required_candidate_ids") or []
        ),
        "required_partial_count": sum(
            decision.get("retention") == "required"
            and decision.get("action") == "partially_kept"
            for decision in decisions
        ),
        "usage_observed": input_value is not None and output_value is not None,
    }


def build_run_record(
    run_dir: Path,
    *,
    schedule_item: dict[str, Any],
    code_snapshot: str,
    protocol: dict[str, Any],
) -> dict[str, Any]:
    stage7_protocol = {
        **protocol,
        "common_interventions": {
            "memory_baseline": {"strategy": "isolated_empty"},
        },
        "analysis": {
            **protocol["analysis"],
            "enhancement_purposes": [
                "project_improvement",
                "iteration_goal",
                "iteration_task_design",
                "code_generation",
                "code_edit",
            ],
        },
    }
    record = build_stage7_run_record(
        run_dir,
        schedule_item={**schedule_item, "arm": "dynamic"},
        code_snapshot=code_snapshot,
        protocol=stage7_protocol,
    )
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    intervention = manifest.get("task_designer_context_intervention") or {}
    events = _load_events(run_dir / "diagnostics")
    task_designer = _task_designer_observation(events)
    observed_complexities = list(intervention.get("observed_complexities") or [])
    observed_policies = list(intervention.get("observed_request_policies") or [])
    observed_policy = observed_policies[0] if len(observed_policies) == 1 else {}
    task_designer["complexity"] = str(
        observed_policy.get("complexity")
        or (observed_complexities[0] if len(observed_complexities) == 1 else "")
    )
    task_designer["remaining_value"] = str(
        observed_policy.get("remaining_value") or ""
    )
    task_designer["remaining_calls"] = int(
        observed_policy.get("remaining_calls") or 0
    )
    designed_tasks: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "pipeline_progress":
            continue
        payload = event.get("payload") or {}
        if (payload.get("input_summary") or {}).get("event") != "task_designer":
            continue
        designed_tasks.extend((payload.get("output_summary") or {}).get("tasks") or [])
    project_dir = Path(str(manifest.get("project_dir") or ""))
    final_file_hashes = {
        basename: _sha256((project_dir / basename).read_bytes())
        for basename in protocol["quality_gates"]["required_modified_basenames"]
        if (project_dir / basename).is_file()
    }
    task_targets = [
        str(path)
        for task in designed_tasks
        for path in task.get("target_files") or []
    ]
    target_basenames = sorted({Path(path).name for path in task_targets})
    authorized_target = (project_dir / "calculator.py").resolve(strict=False)
    authorized_targets = bool(task_targets) and all(
        Path(path).expanduser().resolve(strict=False) == authorized_target
        for path in task_targets
    )
    evidence_ids = sorted(
        {
            str(evidence_id)
            for task in designed_tasks
            for evidence_id in task.get("evidence_ids") or []
        }
    )
    evidence_roles = sorted(
        {
            role
            for evidence_id in evidence_ids
            for role in [
                (
                    "goal"
                    if evidence_id == "iteration_task_design:goal"
                    else "safety"
                    if evidence_id == "iteration_task_design:safety"
                    else "validation"
                    if evidence_id == "iteration_task_design:validation"
                    else "project_file"
                    if evidence_id.startswith("iteration_task_design:project_file:")
                    else ""
                )
            ]
            if role
        }
    )
    expected_goal = protocol["frozen_task_designer_source"]["goal"]
    task_criteria = [
        list(task.get("acceptance_criteria") or []) for task in designed_tasks
    ]
    contract_passed = bool(
        len(designed_tasks) == 1
        and {str(task.get("goal_id") or "") for task in designed_tasks}
        == {expected_goal["id"]}
        and target_basenames == ["calculator.py"]
        and authorized_targets
        and task_criteria == [expected_goal["acceptance_criteria"]]
        and set(evidence_roles) >= {"goal", "safety", "validation", "project_file"}
        and record["quality_gate"]["checks"].get("required_commands") is True
        and record["quality_gate"]["checks"].get("mutation_scope") is True
    )
    equivalence_payload = {
        "stage7_quality_signature": record["quality_gate"]["signature"],
        "task_count": len(designed_tasks),
        "goal_ids": sorted(
            {str(task.get("goal_id") or "") for task in designed_tasks}
        ),
        "task_target_basenames": target_basenames,
        "acceptance_criteria": task_criteria[0] if len(task_criteria) == 1 else [],
        "evidence_roles": evidence_roles,
        "authorized_targets": authorized_targets,
    }
    return {
        **record,
        **schedule_item,
        "projection_policy": intervention.get("projection_policy"),
        "source_fingerprint": intervention.get("source_fingerprint"),
        "task_designer_context_intervention": intervention,
        "task_designer": task_designer,
        "quality_equivalence": {
            "signature": _sha256(_canonical_bytes(equivalence_payload)),
            "task_designer_contract_passed": contract_passed,
            **equivalence_payload,
            "evidence_ids": evidence_ids,
            "final_modified_file_hashes_descriptive": final_file_hashes,
        },
        "effective_completion_policy": manifest.get(
            "effective_enhancement_completion_policy"
        ),
    }


def derive_expected_task_designer_reservation(
    record: Mapping[str, Any], protocol: Mapping[str, Any]
) -> int | None:
    observed = record.get("task_designer") or {}
    policy_payload = record.get("effective_completion_policy")
    if not isinstance(policy_payload, Mapping):
        policy_payload = RuntimeBudgetMetadata().enhancement_completion_policy.model_dump(
            mode="python"
        )
    try:
        remaining_after = int(observed["remaining_tokens_after_reservation"])
        reserved = int(observed["reserved_completion_tokens"])
        remaining_before = remaining_after + reserved
        policy = RuntimeBudgetMetadata.model_validate(
            {"enhancement_completion_policy": policy_payload}
        ).enhancement_completion_policy
        if remaining_before > policy.total_tokens:
            return None
        # The production coordinator derives from the remaining pool, not from
        # historical ledger shape. Give an otherwise identical policy a pool
        # equal to the observed pre-reservation remainder so the coordinator is
        # the formula oracle without fabricating reconciliations.
        budget = RuntimeBudgetMetadata(
            enhancement_completion_policy=policy.model_copy(
                update={"total_tokens": remaining_before}
            ),
        )
        request = EnhancementCompletionRequest(
            logical_key="stage8:reservation-derivation",
            purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            complexity=EnhancementCompletionComplexity(str(observed["complexity"])),
            prompt_tokens=int(observed["final_prompt_tokens"]),
            remaining_calls=int(observed["remaining_calls"]),
            remaining_value=EnhancementCompletionDecisionValue(
                str(observed["remaining_value"])
            ),
            requirement=EnhancementCompletionRequirement.OPTIONAL,
        )
        derived = EnhancementCompletionBudgetCoordinator(budget).reserve(request)
    except (KeyError, TypeError, ValueError):
        return None
    return derived.max_tokens if derived is not None else None


def _call_contract_reasons(record: Mapping[str, Any], protocol: Mapping[str, Any]) -> list[str]:
    observed = record.get("task_designer") or {}
    expected = protocol["task_designer_call_contract"]
    reasons = []
    if observed.get("usage_observed") is not True:
        reasons.append("task_designer_usage_missing")
    if observed.get("omitted_required_candidate_ids") or int(
        observed.get("required_partial_count") or 0
    ):
        reasons.append("task_designer_required_context_failure")
    contract_values = {
        "request_count": observed.get("request_count"),
        "complexity": observed.get("complexity"),
        "remaining_value": observed.get("remaining_value"),
        "remaining_calls": observed.get("remaining_calls"),
        "reasoning_mode": observed.get("reasoning_mode"),
        "transport_retries": observed.get("transport_retries"),
        "required_omitted_count": len(observed.get("omitted_required_candidate_ids") or []),
        "required_partial_count": int(observed.get("required_partial_count") or 0),
    }
    expected_values = {
        key: expected[key]
        for key in contract_values
    }
    derived = derive_expected_task_designer_reservation(record, protocol)
    if (
        contract_values != expected_values
        or derived is None
        or int(observed.get("reserved_completion_tokens") or 0) != derived
    ):
        reasons.append("task_designer_call_contract_mismatch")
    return reasons


def _common_arm_reasons(record: Mapping[str, Any]) -> list[str]:
    reasons = []
    if record.get("overall_usage_coverage") != 1.0:
        reasons.append("incomplete_usage_coverage")
    if int(record.get("unknown_failed_usage_count") or 0):
        reasons.append("unknown_failed_usage")
    if int(record.get("transport_retry_count") or 0):
        reasons.append("transport_retry_observed")
    if record.get("effective_arm_policy_matches") is not True:
        reasons.append("effective_arm_policy_mismatch")
    if not isinstance(record.get("effective_completion_policy"), Mapping):
        reasons.append("effective_completion_policy_missing")
    if record.get("memory_baseline_matches") is not True:
        reasons.append("memory_baseline_mismatch")
    return reasons


def _task_quality_reasons(
    record: Mapping[str, Any], protocol: Mapping[str, Any]
) -> list[str]:
    quality = record.get("quality_equivalence") or {}
    stage7_checks = (record.get("quality_gate") or {}).get("checks") or {}
    expected_goal = protocol["frozen_task_designer_source"]["goal"]
    passed = bool(
        quality.get("task_designer_contract_passed") is True
        and int(quality.get("task_count") or 0) == 1
        and quality.get("goal_ids") == [expected_goal["id"]]
        and quality.get("task_target_basenames") == ["calculator.py"]
        and quality.get("acceptance_criteria")
        == expected_goal["acceptance_criteria"]
        and set(quality.get("evidence_roles") or [])
        >= {"goal", "safety", "validation", "project_file"}
        and quality.get("authorized_targets") is True
        and stage7_checks.get("required_commands") is True
        and stage7_checks.get("mutation_scope") is True
    )
    return [] if passed else ["task_designer_quality_contract_failed"]


def arm_stop_reasons(record: Mapping[str, Any], protocol: Mapping[str, Any]) -> list[str]:
    reasons = []
    if not (record.get("quality_gate") or {}).get("passed"):
        reasons.append("quality_gate_failed")
    reasons.extend(_task_quality_reasons(record, protocol))
    if record.get("provider_identity") != protocol["provider_identity"]:
        reasons.append("provider_identity_mismatch")
    if record.get("projection_policy") != record.get("arm"):
        reasons.append("projection_policy_mismatch")
    if record.get("source_fingerprint") != protocol["frozen_task_designer_source"][
        "normalized_source_fingerprint"
    ]:
        reasons.append("source_fingerprint_mismatch")
    if int((record.get("usage") or {}).get("lifecycle", {}).get("total_tokens") or 0) > int(
        protocol["token_limits"]["per_arm_hard"]
    ):
        reasons.append("per_arm_token_hard_limit_exceeded")
    reasons.extend(_common_arm_reasons(record))
    reasons.extend(_call_contract_reasons(record, protocol))
    return list(dict.fromkeys(reasons))


def analyze_campaign_records(
    records: list[dict[str, Any]], protocol: dict[str, Any]
) -> dict[str, Any]:
    validate_campaign_protocol(protocol)
    grouped: dict[int, dict[str, dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(int(record["pair"]), {})[record["arm"]] = record
    included: list[int] = []
    excluded: dict[str, list[str]] = {}
    campaign_snapshot = records[0].get("code_snapshot_sha256") if records else None
    for pair in range(1, int(protocol["pairs"]) + 1):
        arms = grouped.get(pair, {})
        reasons: list[str] = []
        if set(arms) != ARMS:
            reasons.append("missing_arm")
        else:
            current, compact = arms["current"], arms["compact"]
            if not current["quality_gate"]["passed"] or not compact["quality_gate"]["passed"]:
                reasons.append("quality_gate_failed")
            elif _task_quality_reasons(
                current, protocol
            ) or _task_quality_reasons(compact, protocol):
                reasons.append("task_designer_quality_contract_failed")
            elif current["quality_equivalence"]["signature"] != compact["quality_equivalence"]["signature"]:
                reasons.append("quality_signature_mismatch")
            expected_source = protocol["frozen_task_designer_source"][
                "normalized_source_fingerprint"
            ]
            if (
                current.get("source_fingerprint") != expected_source
                or compact.get("source_fingerprint") != expected_source
            ):
                reasons.append("source_fingerprint_mismatch")
            for arm, record in arms.items():
                if record.get("projection_policy") != arm:
                    reasons.append("projection_policy_mismatch")
                if record.get("code_snapshot_sha256") != campaign_snapshot:
                    reasons.append("code_snapshot_mismatch")
                if record.get("provider_identity") != protocol["provider_identity"]:
                    reasons.append("provider_identity_mismatch")
                effective_policy = record.get("effective_completion_policy")
                if (
                    not isinstance(effective_policy, Mapping)
                    or effective_policy
                    != arms["current"].get("effective_completion_policy")
                ):
                    reasons.append("effective_completion_policy_mismatch")
                reasons.extend(_common_arm_reasons(record))
                reasons.extend(_call_contract_reasons(record, protocol))
        if reasons:
            excluded[str(pair)] = list(dict.fromkeys(reasons))
        else:
            included.append(pair)
    input_totals = {arm: 0 for arm in ARMS}
    prompt_totals = {arm: 0 for arm in ARMS}
    output_totals = {arm: 0 for arm in ARMS}
    enhancement_totals = {arm: 0 for arm in ARMS}
    lifecycle_totals = {arm: 0 for arm in ARMS}
    pair_reductions = []
    for pair in included:
        arms = grouped[pair]
        for arm in ARMS:
            task = arms[arm]["task_designer"]
            input_totals[arm] += int(task["provider_input_tokens"])
            prompt_totals[arm] += int(task["final_prompt_tokens"])
            output_totals[arm] += int(task["provider_output_tokens"])
            enhancement_totals[arm] += int(arms[arm]["usage"]["enhancement"]["total_tokens"])
            lifecycle_totals[arm] += int(arms[arm]["usage"]["lifecycle"]["total_tokens"])
        current_input = int(arms["current"]["task_designer"]["provider_input_tokens"])
        compact_input = int(arms["compact"]["task_designer"]["provider_input_tokens"])
        pair_reductions.append(
            (current_input - compact_input) / current_input if current_input else 0.0
        )
    input_change = (
        (input_totals["compact"] - input_totals["current"]) / input_totals["current"]
        if input_totals["current"]
        else None
    )
    prompt_change = (
        (prompt_totals["compact"] - prompt_totals["current"]) / prompt_totals["current"]
        if prompt_totals["current"]
        else None
    )
    median_reduction = statistics.median(pair_reductions) if pair_reductions else None
    minimum_pairs = int(protocol["analysis"]["minimum_quality_matched_pairs"])
    eligible = len(included) >= minimum_pairs
    thresholds_passed = bool(
        eligible
        and pair_reductions
        and all(value > 0 for value in pair_reductions)
        and median_reduction is not None
        and median_reduction
        >= float(protocol["analysis"]["minimum_median_input_reduction_fraction"])
        and pair_reductions[0]
        >= float(protocol["analysis"]["minimum_first_compact_input_reduction_fraction"])
    )
    return {
        "campaign_id": protocol["campaign_id"],
        "eligible": eligible,
        "quality_matched_pair_count": len(included),
        "included_pairs": included,
        "excluded_pairs": excluded,
        "primary_metrics": {
            "provider_input_tokens": {
                **input_totals,
                "change_fraction": input_change,
            },
            "final_prompt_tokens": {
                **prompt_totals,
                "change_fraction": prompt_change,
            },
            "pair_input_reduction_fractions": pair_reductions,
            "median_pair_input_reduction_fraction": median_reduction,
        },
        "secondary_metrics_noncausal": {
            "provider_output_tokens": output_totals,
            "enhancement_total_tokens": enhancement_totals,
            "lifecycle_total_tokens": lifecycle_totals,
        },
        "success_thresholds_passed": thresholds_passed,
        "claim_boundary": "fixed_source_full_architecture_mechanism_only",
    }


def build_run_command(
    output_dir: Path, arm: str, protocol: Mapping[str, Any]
) -> list[str]:
    if arm not in ARMS:
        raise ValueError(f"unsupported Stage 8 arm: {arm}")
    return [
        sys.executable,
        str(RUNNER_PATH),
        "--fixed-decomposition",
        "--improvement-requirement",
        "optional",
        "--enhancement-budget-arm",
        "dynamic",
        "--iteration-goal-mode",
        "fixed_divide_docstring",
        "--memory-mode",
        "isolated_empty",
        "--task-designer-context-arm",
        arm,
        "--task-designer-source-protocol",
        str(PROTOCOL_PATH),
        "--max-provider-tokens",
        "45000",
        "--output-dir",
        str(output_dir),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    parser.add_argument("--output-dir", type=Path, default=HERE / "runs" / "stage8_campaign")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    protocol = load_campaign_protocol(args.protocol)
    schedule = build_schedule(protocol)
    frozen = load_frozen_task_designer_source(protocol)
    snapshot = code_snapshot_sha256(protocol)
    identity = provider_runtime_identity(LLMSettings())
    plan = {
        "campaign_id": protocol["campaign_id"],
        "execute": args.execute,
        "code_snapshot_sha256": snapshot,
        "provider_identity": identity,
        "provider_identity_matches": identity == protocol["provider_identity"],
        "source_fingerprint": frozen.source_fingerprint,
        "common_interventions": protocol["common_interventions"],
        "schedule": schedule,
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if identity != protocol["provider_identity"]:
        raise SystemExit("configured provider identity does not match frozen campaign")
    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    records = []
    for item in schedule:
        if code_snapshot_sha256(protocol) != snapshot:
            raise RuntimeError("campaign code snapshot changed before arm execution")
        run_dir = output_root / f"{item['ordinal']:02d}_p{item['pair']}_{item['arm']}"
        completed = subprocess.run(
            build_run_command(run_dir, item["arm"], protocol),
            check=False,
            timeout=int(protocol["execution"]["per_arm_wall_clock_seconds"]),
        )
        if code_snapshot_sha256(protocol) != snapshot:
            raise RuntimeError("campaign code snapshot changed during arm execution")
        if not (run_dir / "manifest.json").is_file():
            raise RuntimeError(f"campaign arm produced no manifest: {completed.returncode}")
        record = build_run_record(
            run_dir,
            schedule_item=item,
            code_snapshot=snapshot,
            protocol=protocol,
        )
        records.append(record)
        (run_dir / "campaign_record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        stop_reasons = arm_stop_reasons(record, protocol)
        spend = evaluate_spend_limits(records, protocol)
        stop_reasons.extend(spend["hard_failures"])
        if stop_reasons:
            raise RuntimeError(
                "campaign stopped after arm: " + ", ".join(dict.fromkeys(stop_reasons))
            )
    analysis = analyze_campaign_records(records, protocol)
    analysis["spend_limits"] = evaluate_spend_limits(records, protocol)
    (output_root / "campaign_analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(analysis, ensure_ascii=False, indent=2))
    return 0 if analysis["eligible"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
