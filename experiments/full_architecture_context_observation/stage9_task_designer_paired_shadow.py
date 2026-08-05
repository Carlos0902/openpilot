"""Stage 9 V2 same-source paired Task Designer experiment.

The module is experiment-only.  It patches one live Task Designer boundary so
current and compact requests are assembled from one value snapshot, transports
production first, reconciles its normal product reservation, then transports a
no-downstream shadow request.  Importing or preflighting this module never calls
a provider.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest.mock import patch

import autonomous_iteration.agents.iteration_agent as iteration_agent_module
from metadata import ContextRequestPurpose
from memory.context_assembly.request_builder import build_context_candidate_request
from stage9_task_designer_provider_sentinel import (
    _candidate_contract,
    _sha256,
    capture_enhancement_start_snapshot,
    expected_runtime_artifact_manifest,
    overlay_scenario_sources,
    runtime_contract_hash,
    scenario_overlay_fingerprint,
)
from stage9_task_designer_scenario_gate import (
    ARMS,
    POSITIVE_SCENARIO_IDS,
    ScenarioFixture,
    build_scenario_fixtures,
    load_offline_report,
    offline_report_snapshot,
    run_offline_sentinel,
)


HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "STAGE9_TASK_DESIGNER_PAIRED_SHADOW_V2.json"
RUNNER_PATH = HERE / "run_observation.py"


class PairedShadowError(RuntimeError):
    """The frozen paired-shadow contract is invalid."""


class PairedShadowStopped(RuntimeError):
    """A fail-closed paired-shadow gate stopped downstream work."""


@dataclass(frozen=True)
class RequestPair:
    runtime_contract_hash: str
    production_policy: str
    shadow_policy: str
    production_request: Any
    shadow_request: Any


@dataclass(frozen=True)
class PairExecutionResult:
    production_response: Any
    shadow_response: Any


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_reanalysis_resume_prefix(
    path: Path,
    *,
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the one corrected, hash-pinned V2 prefix without provider work."""

    try:
        reference = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PairedShadowError(f"resume prefix reference is invalid: {exc}") from exc
    if reference.get("campaign_id") != protocol.get("campaign_id"):
        raise PairedShadowError("resume prefix campaign mismatch")
    original_run = (HERE / str(reference.get("original_run") or "")).resolve()
    try:
        original_run.relative_to(HERE)
    except ValueError as exc:
        raise PairedShadowError("resume prefix run escapes experiment root") from exc
    state_path = original_run.parent / "campaign_state.json"
    record_path = original_run / "campaign_record.json"
    manifest_path = original_run / "manifest.json"
    expected_hashes = reference.get("immutable_source_hashes") or {}
    actual_hashes = {
        "campaign_state_sha256": _sha256_file(state_path),
        "campaign_record_sha256": _sha256_file(record_path),
        "manifest_sha256": _sha256_file(manifest_path),
    }
    if actual_hashes != expected_hashes:
        raise PairedShadowError("resume prefix immutable source hash mismatch")
    raw_record = json.loads(record_path.read_text(encoding="utf-8"))
    raw_state = json.loads(state_path.read_text(encoding="utf-8"))
    if (raw_state.get("records") or []) != [raw_record]:
        raise PairedShadowError("resume prefix state/record mismatch")
    expected_item = build_schedule(protocol)[0]
    schedule_fields = (
        "ordinal",
        "scenario_pair",
        "scenario_id",
        "production_policy",
        "shadow_policy",
        "request_order",
    )
    if any(
        raw_record.get(field) != expected_item.get(field)
        or (
            field in reference
            and reference.get(field) != expected_item.get(field)
        )
        for field in schedule_fields
    ):
        raise PairedShadowError("resume prefix is not a contiguous frozen schedule prefix")
    corrected = deepcopy(raw_record)
    paired_roles = (corrected.get("paired_task_designer") or {}).get("roles") or {}
    production = paired_roles.get("production") or {}
    shadow = paired_roles.get("shadow") or {}
    correction = reference.get("observer_correction") or {}
    if (
        int(production.get("max_completion_tokens") or 0) != 1_000
        or shadow.get("max_completion_tokens") != 0
        or int(correction.get("production_max_completion_tokens") or 0) != 1_000
        or int(correction.get("shadow_max_completion_tokens") or 0) != 1_000
    ):
        raise PairedShadowError("resume prefix observer correction preimage mismatch")
    shadow["max_completion_tokens"] = 1_000
    reasons = arm_stop_reasons(corrected, protocol)
    if reasons or reasons != list(reference.get("recomputed_arm_stop_reasons") or []):
        raise PairedShadowError(f"resume seed gate failed: {reasons}")
    lifecycle = _record_lifecycle_tokens(corrected)
    if lifecycle != int((reference.get("usage") or {}).get("lifecycle_total_tokens") or 0):
        raise PairedShadowError("resume seed lifecycle mismatch")
    accounting = reference.get("resume_accounting") or {}
    failed_tokens = int(accounting.get("failed_first_arm_tokens") or 0)
    expected_opening = (
        int(protocol["historical_stage9_spend"]["observed_complete_tokens"])
        + failed_tokens
        + lifecycle
    )
    if (
        failed_tokens != 12_790
        or int(accounting.get("successful_seed_tokens") or 0) != lifecycle
        or int(accounting.get("lifetime_opening_tokens") or 0) != expected_opening
        or int(accounting.get("strongly_related_scenario_prior_tokens") or 0)
        != failed_tokens + lifecycle
        or accounting.get(
            "seed_is_in_historical_and_must_not_be_recounted_as_new_observed"
        )
        is not True
    ):
        raise PairedShadowError("resume prefix accounting mismatch")
    return {
        "records": [corrected],
        "seed_lifecycle_tokens": lifecycle,
        "seed_scenario_tokens": {str(corrected["scenario_id"]): lifecycle},
        "next_ordinal": 2,
        "required_additional_historical_tokens": failed_tokens,
        "reference_path": str(path.resolve()),
        "reference_sha256": _sha256_file(path),
    }


def validate_protocol(protocol: Mapping[str, Any]) -> None:
    if protocol.get("campaign_id") != "stage9-task-designer-paired-shadow-v2":
        raise PairedShadowError("unexpected paired-shadow campaign id")
    schedule = list(protocol.get("schedule") or [])
    expected = [
        (1, 1, "strongly_related_diagnosis", "current", "compact"),
        (2, 1, "strongly_related_diagnosis", "compact", "current"),
        (3, 2, "partial_shared_criterion", "compact", "current"),
        (4, 2, "partial_shared_criterion", "current", "compact"),
        (5, 3, "relevant_iteration_memory", "current", "compact"),
        (6, 3, "relevant_iteration_memory", "compact", "current"),
    ]
    observed = [
        (
            int(item.get("ordinal") or 0),
            int(item.get("scenario_pair") or 0),
            str(item.get("scenario_id") or ""),
            str(item.get("production_policy") or ""),
            str(item.get("shadow_policy") or ""),
        )
        for item in schedule
    ]
    if observed != expected or any(
        item.get("request_order") != ["production", "shadow"]
        for item in schedule
    ):
        raise PairedShadowError("six-run paired schedule changed")
    source_contract = protocol.get("paired_source_contract") or {}
    if (
        int(source_contract.get("builder_entry_count") or 0) != 1
        or source_contract.get("projection_policies") != ["current", "compact"]
        or source_contract.get("shared_snapshot_required_for_both_requests")
        is not True
    ):
        raise PairedShadowError("same-source builder contract changed")
    execution = protocol.get("execution") or {}
    if execution.get("production_first") is not True or execution.get(
        "shadow_second"
    ) is not True:
        raise PairedShadowError("production-first transport contract changed")
    if any(
        int(execution.get(key, -1)) != 0
        for key in ("transport_retries", "json_repair_attempts", "length_recoveries")
    ):
        raise PairedShadowError("retry or recovery contract changed")
    if protocol.get("cache_enabled") is not False:
        raise PairedShadowError("provider cache must be disabled")
    limits = protocol.get("token_limits") or {}
    if (
        int(limits.get("per_run_hard") or 0) != 30_000
        or int(limits.get("per_scenario_v2_hard") or 0) != 60_000
        or int(limits.get("stage9_lifetime_hard") or 0) != 180_000
        or int(limits.get("stage9_lifetime_opening_spend") or 0) != 51_391
        or int(limits.get("stage9_lifetime_opening_remaining") or 0) != 128_609
    ):
        raise PairedShadowError("paired-shadow token limits changed")
    promotion = protocol.get("promotion_gate") or {}
    if promotion.get("production_default_before_pass") is not False:
        raise PairedShadowError("compact default must remain disabled before pass")
    fixtures = build_scenario_fixtures()
    if tuple((protocol.get("scenarios") or {}).keys()) != POSITIVE_SCENARIO_IDS:
        raise PairedShadowError("provider scenario membership changed")
    contracts = protocol.get("scenario_candidate_contracts") or {}
    for scenario_id in POSITIVE_SCENARIO_IDS:
        fixture = fixtures[scenario_id]
        frozen = protocol["scenarios"][scenario_id]
        if frozen.get("frozen_source_fingerprint") != scenario_overlay_fingerprint(
            fixture
        ):
            raise PairedShadowError(f"source fingerprint mismatch for {scenario_id}")
        if frozen.get("frozen_goal_hash") != fixture.goal_hash:
            raise PairedShadowError(f"goal hash mismatch for {scenario_id}")
        if set((contracts.get(scenario_id) or {}).keys()) != set(ARMS):
            raise PairedShadowError(f"candidate contract missing for {scenario_id}")
    for item in (protocol.get("source_gates") or {}).values():
        path = (HERE / str(item.get("path") or "")).resolve()
        try:
            path.relative_to(HERE)
        except ValueError as exc:
            raise PairedShadowError("source gate escapes experiment root") from exc
        if not path.is_file() or _sha256_file(path) != item.get("sha256"):
            raise PairedShadowError("source gate hash mismatch")
    _validate_historical_spend(protocol)


def _validate_historical_spend(protocol: Mapping[str, Any]) -> None:
    """Rebuild the Stage 9 opening balance from immutable V1 evidence."""

    historical = protocol.get("historical_stage9_spend") or {}
    evidence = list(historical.get("evidence") or [])
    if len(evidence) != 3:
        raise PairedShadowError("historical Stage 9 spend evidence inventory changed")
    payloads: list[dict[str, Any]] = []
    for item in evidence:
        path = (HERE / str(item.get("path") or "")).resolve()
        try:
            path.relative_to(HERE)
        except ValueError as exc:
            raise PairedShadowError(
                "historical Stage 9 spend evidence escapes experiment root"
            ) from exc
        if not path.is_file() or _sha256_file(path) != item.get("sha256"):
            raise PairedShadowError("historical Stage 9 spend evidence hash mismatch")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PairedShadowError(
                "historical Stage 9 spend evidence is unreadable"
            ) from exc
        if not isinstance(payload, dict):
            raise PairedShadowError("historical Stage 9 spend evidence is invalid")
        payloads.append(payload)

    state, *records = payloads
    record_tokens = sum(
        int((((record.get("usage") or {}).get("lifecycle") or {}).get("total_tokens")) or 0)
        for record in records
    )
    state_records = list(state.get("records") or [])
    state_record_tokens = sum(
        int((((record.get("usage") or {}).get("lifecycle") or {}).get("total_tokens")) or 0)
        for record in state_records
    )
    spend = state.get("spend") or {}
    observed_total = int(historical.get("observed_complete_tokens") or 0)
    if (
        state.get("status") != "stopped"
        or "runtime_contract_hash_mismatch" not in set(state.get("stop_reasons") or [])
        or len(records) != 2
        or records != state_records
        or record_tokens != 24_851
        or state_record_tokens != record_tokens
        or int(spend.get("formal_campaign_total") or 0) != record_tokens
        or int(spend.get("prior_paid_campaign_total") or 0) != 26_540
        or int(spend.get("cumulative_campaign_total") or 0) != observed_total
        or observed_total != 51_391
    ):
        raise PairedShadowError("historical Stage 9 spend evidence does not reconcile")


def build_schedule(
    protocol: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    frozen = dict(protocol or load_protocol())
    validate_protocol(frozen)
    return [
        {
            **dict(item),
            "transport_order": [
                {"role": "production", "policy": item["production_policy"]},
                {"role": "shadow", "policy": item["shadow_policy"]},
            ],
        }
        for item in frozen["schedule"]
    ]


def assemble_request_pair(
    *,
    runtime_contract: Any,
    production_policy: str,
    hash_runtime_contract: Callable[[Any], str],
    build_request: Callable[[Any, str], Any],
) -> RequestPair:
    if production_policy not in ARMS:
        raise PairedShadowError("unsupported production projection policy")
    shadow_policy = "compact" if production_policy == "current" else "current"
    shared_hash = str(hash_runtime_contract(runtime_contract))
    production = build_request(runtime_contract, production_policy)
    shadow = build_request(runtime_contract, shadow_policy)
    for request in (production, shadow):
        observed = str(getattr(request, "runtime_contract_hash", "") or "")
        if observed and observed != shared_hash:
            raise PairedShadowStopped("request runtime contract hash mismatch")
    return RequestPair(
        runtime_contract_hash=shared_hash,
        production_policy=production_policy,
        shadow_policy=shadow_policy,
        production_request=production,
        shadow_request=shadow,
    )


def _request_reservation(request: Any) -> int:
    try:
        value = int(getattr(request, "reservation_tokens"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PairedShadowStopped("combined request reservation is unavailable") from exc
    if value <= 0:
        raise PairedShadowStopped("combined request reservation must be positive")
    return value


def _complete_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if not isinstance(usage, Mapping):
        raise PairedShadowStopped("provider usage is censored")
    input_value = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_value = usage.get("output_tokens", usage.get("completion_tokens"))
    if input_value is None or output_value is None:
        raise PairedShadowStopped("provider usage is censored")
    try:
        input_tokens = int(input_value)
        output_tokens = int(output_value)
    except (TypeError, ValueError) as exc:
        raise PairedShadowStopped("provider usage is censored") from exc
    if input_tokens < 0 or output_tokens < 0:
        raise PairedShadowStopped("provider usage is censored")
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def execute_request_pair(
    pair: RequestPair,
    *,
    remaining_provider_tokens: int,
    transport: Callable[[Any], Any],
    consume_production: Callable[[Any], None],
    reconcile_production: Callable[[Any], None] | None = None,
) -> PairExecutionResult:
    combined = _request_reservation(pair.production_request) + _request_reservation(
        pair.shadow_request
    )
    if combined > int(remaining_provider_tokens):
        raise PairedShadowStopped(
            "combined request reservation exceeds remaining provider tokens"
        )
    try:
        production = transport(pair.production_request)
    except Exception as exc:
        raise PairedShadowStopped(f"production provider failed: {exc}") from exc
    if reconcile_production is not None:
        reconcile_production(production)
    _complete_usage(production)
    try:
        shadow = transport(pair.shadow_request)
    except Exception as exc:
        raise PairedShadowStopped(f"shadow provider failed: {exc}") from exc
    _complete_usage(shadow)
    consume_production(production)
    return PairExecutionResult(
        production_response=production,
        shadow_response=shadow,
    )


def observe_paired_task_designer(events: list[dict[str, Any]]) -> dict[str, Any]:
    requested: dict[str, dict[str, Any]] = {}
    responses: dict[str, dict[str, Any]] = {}
    request_count = 0
    duplicate_execution_ids: list[str] = []
    duplicate_response_execution_ids: list[str] = []
    failed_execution_ids: list[str] = []
    for event in events:
        payload = event.get("payload") or {}
        execution_id = str((payload.get("correlation") or {}).get("execution_id") or "")
        if not execution_id:
            continue
        if event.get("event_type") == "llm_requested":
            selection = payload.get("context_selection") or {}
            trace = (payload.get("trace_info") or {}).get("paired_shadow") or {}
            if selection.get("request_purpose") != "iteration_task_design" or not trace:
                continue
            request_count += 1
            if execution_id in requested:
                duplicate_execution_ids.append(execution_id)
            completion_budget = (payload.get("trace_info") or {}).get(
                "completion_budget"
            ) or {}
            request_diagnostics = (payload.get("trace_info") or {}).get(
                "diagnostics"
            ) or {}
            reasoning = payload.get("reasoning_policy") or {}
            requested[execution_id] = {
                "execution_id": execution_id,
                "role": str(trace.get("role") or ""),
                "projection_policy": str(trace.get("projection_policy") or ""),
                "transport_ordinal": int(trace.get("transport_ordinal") or 0),
                "runtime_contract_hash": str(trace.get("runtime_contract_hash") or ""),
                "final_prompt_tokens": int(selection.get("final_prompt_tokens") or 0),
                "max_completion_tokens": int(
                    request_diagnostics.get("max_tokens")
                    or completion_budget.get("reserved_tokens")
                    or 0
                ),
                "product_reservation_tokens": int(
                    completion_budget.get("reserved_tokens") or 0
                ),
                "reasoning_mode": str(reasoning.get("mode") or ""),
                "candidate_decisions": list(
                    selection.get("candidate_decisions") or []
                ),
            }
        elif event.get("event_type") == "llm_responded":
            response_metadata = payload
            usage = response_metadata.get("usage") or {}
            if execution_id in responses:
                duplicate_response_execution_ids.append(execution_id)
            responses[execution_id] = {
                "usage": dict(usage),
                "finish_reason": str(response_metadata.get("finish_reason") or ""),
                "content_length": int(
                    (response_metadata.get("provider_details") or {}).get(
                        "content_length"
                    )
                    or 0
                ),
            }
        elif event.get("event_type") == "llm_failed" and execution_id in requested:
            failed_execution_ids.append(execution_id)
    roles: dict[str, dict[str, Any]] = {}
    usage_observed = len(requested) == 2
    for execution_id, observation in requested.items():
        response = responses.get(execution_id) or {}
        usage = response.get("usage")
        try:
            complete = _complete_usage(type("Usage", (), {"usage": usage})())
            role_usage_observed = True
        except PairedShadowStopped:
            usage_observed = False
            complete = {"input_tokens": 0, "output_tokens": 0}
            role_usage_observed = False
        roles[observation["role"]] = {
            **observation,
            "provider_input_tokens": complete["input_tokens"],
            "provider_output_tokens": complete["output_tokens"],
            "provider_total_tokens": complete["input_tokens"] + complete["output_tokens"],
            "usage_observed": role_usage_observed,
            "finish_reason": str(response.get("finish_reason") or ""),
            "response_content_length": int(response.get("content_length") or 0),
        }
    ordered = sorted(roles.values(), key=lambda item: item["transport_ordinal"])
    validation_failures: list[str] = []
    if duplicate_execution_ids:
        validation_failures.append("duplicate_request_execution_id")
    if duplicate_response_execution_ids:
        validation_failures.append("duplicate_response_execution_id")
    if set(roles) != {"production", "shadow"}:
        validation_failures.append("role_membership_invalid")
    if [item.get("role") for item in ordered] != ["production", "shadow"]:
        validation_failures.append("transport_role_order_invalid")
    if {int(item.get("transport_ordinal") or 0) for item in ordered} != {1, 2}:
        validation_failures.append("transport_ordinal_membership_invalid")
    runtime_hashes = {
        str(item.get("runtime_contract_hash") or "") for item in ordered
    }
    if len(runtime_hashes) != 1 or "" in runtime_hashes:
        validation_failures.append("runtime_contract_hash_mismatch")
    if {str(item.get("projection_policy") or "") for item in ordered} != set(ARMS):
        validation_failures.append("projection_policy_membership_invalid")
    return {
        "request_count": request_count,
        "usage_observed": (
            usage_observed
            and not duplicate_execution_ids
            and not duplicate_response_execution_ids
            and not failed_execution_ids
            and len(roles) == 2
            and not validation_failures
        ),
        "transport_order": [item.get("role") for item in ordered],
        "execution_ids": [str(item.get("execution_id") or "") for item in ordered],
        "roles": roles,
        "provider_total_tokens": sum(
            int(item.get("provider_total_tokens") or 0) for item in roles.values()
        ),
        "duplicate_execution_ids": sorted(set(duplicate_execution_ids)),
        "failed_execution_ids": sorted(set(failed_execution_ids)),
        "duplicate_response_execution_ids": sorted(
            set(duplicate_response_execution_ids)
        ),
        "validation_failures": list(dict.fromkeys(validation_failures)),
    }


def evaluate_campaign_spend(
    records: list[Mapping[str, Any]],
    *,
    campaign_hard: int,
    historical_tokens: int = 0,
) -> dict[str, Any]:
    for record in records:
        lifecycle = _record_lifecycle_tokens(record)
        held_value = int(
            (record.get("guard_observation") or {}).get(
                "unsettled_reserved_tokens"
            )
            or 0
        )
        if lifecycle < 0 or held_value < 0:
            raise PairedShadowError("negative token accounting is invalid")
    observed = sum(
        int((((record.get("usage") or {}).get("lifecycle") or {}).get("total_tokens")) or 0)
        for record in records
    )
    held = sum(
        int((record.get("guard_observation") or {}).get("unsettled_reserved_tokens") or 0)
        for record in records
        if (record.get("guard_observation") or {}).get("usage_censored") is True
    )
    effective = int(historical_tokens) + observed + held
    return {
        "historical_tokens": int(historical_tokens),
        "observed_lifecycle_tokens": observed,
        "held_unknown_usage_tokens": held,
        "effective_campaign_tokens": effective,
        "remaining_campaign_tokens": int(campaign_hard) - effective,
        "hard_failures": (
            ["campaign_hard_limit_exceeded"] if effective > int(campaign_hard) else []
        ),
    }


def code_snapshot_sha256(protocol: Mapping[str, Any]) -> str:
    """Hash the frozen experiment source roots through the existing Stage 7 rule."""

    del protocol  # V2 freezes its source gates; roots remain the Stage 8 code roots.
    from stage7_campaign import code_snapshot_sha256 as stage7_snapshot
    from stage8_task_designer_context_campaign import load_campaign_protocol

    return stage7_snapshot(load_campaign_protocol())


def _record_lifecycle_tokens(record: Mapping[str, Any]) -> int:
    return int(
        (((record.get("usage") or {}).get("lifecycle") or {}).get("total_tokens"))
        or 0
    )


def remaining_run_hard_cap(
    records: list[Mapping[str, Any]],
    schedule_item: Mapping[str, Any],
    protocol: Mapping[str, Any],
    *,
    additional_historical_tokens: int = 0,
    prior_scenario_tokens: Mapping[str, int] | None = None,
) -> int:
    if isinstance(additional_historical_tokens, bool) or not isinstance(
        additional_historical_tokens, int
    ) or additional_historical_tokens < 0:
        raise PairedShadowError(
            "additional_historical_tokens must be a non-negative integer"
        )
    historical = (
        int(protocol["historical_stage9_spend"]["observed_complete_tokens"])
        + additional_historical_tokens
    )
    observed = sum(_record_lifecycle_tokens(record) for record in records)
    scenario = str(schedule_item["scenario_id"])
    scenario_spend = int((prior_scenario_tokens or {}).get(scenario) or 0) + sum(
        _record_lifecycle_tokens(record)
        for record in records
        if str(record.get("scenario_id") or "") == scenario
    )
    limits = protocol["token_limits"]
    cap = min(
        int(limits["per_run_hard"]),
        int(limits["per_scenario_v2_hard"]) - scenario_spend,
        int(limits["stage9_lifetime_hard"]) - historical - observed,
    )
    if cap <= 0:
        raise PairedShadowStopped("no provider token budget remains for next run")
    return cap


def build_run_command(
    output_dir: Path,
    schedule_item: Mapping[str, Any],
    protocol: Mapping[str, Any],
    *,
    records: list[Mapping[str, Any]],
    protocol_path: Path = PROTOCOL_PATH,
    additional_historical_tokens: int = 0,
    prior_scenario_tokens: Mapping[str, int] | None = None,
) -> list[str]:
    cap = remaining_run_hard_cap(
        records,
        schedule_item,
        protocol,
        additional_historical_tokens=additional_historical_tokens,
        prior_scenario_tokens=prior_scenario_tokens,
    )
    return [
        sys.executable,
        str(RUNNER_PATH),
        "--fixed-decomposition",
        "--improvement-requirement",
        "optional",
        "--enhancement-budget-arm",
        "dynamic",
        "--iteration-goal-mode",
        "provider",
        "--memory-mode",
        "isolated_empty",
        "--task-designer-context-arm",
        str(schedule_item["production_policy"]),
        "--task-designer-source-protocol",
        str(protocol_path.resolve()),
        "--task-designer-scenario-id",
        str(schedule_item["scenario_id"]),
        "--max-provider-tokens",
        str(cap),
        "--default-max-completion-tokens",
        str(protocol["common_interventions"]["default_max_completion_tokens"]),
        "--output-dir",
        str(output_dir),
    ]


def build_integrated_run_record(
    run_dir: Path,
    *,
    schedule_item: dict[str, Any],
    protocol: Mapping[str, Any],
    code_snapshot: str,
) -> dict[str, Any]:
    """Retain the V1 full-architecture gates and add paired trace evidence."""

    import stage9_task_designer_provider_sentinel as v1
    from run_observation import _load_events

    v1_item = {
        **schedule_item,
        "pair": int(schedule_item["scenario_pair"]),
        "position": 1,
        "arm": str(schedule_item["production_policy"]),
    }
    base = v1.build_integrated_run_record(
        run_dir,
        schedule_item=v1_item,
        protocol=protocol,
        code_snapshot=code_snapshot,
    )
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    events = _load_events(run_dir / "diagnostics")
    descriptor = dict(manifest.get("task_designer_context_intervention") or {})
    paired_observation = observe_paired_task_designer(events)
    all_execution_ids: list[str] = []
    observed_event_total = 0
    requested_ids: set[str] = set()
    for event in events:
        payload = event.get("payload") or {}
        execution_id = str(
            (payload.get("correlation") or {}).get("execution_id") or ""
        )
        if event.get("event_type") == "llm_requested" and execution_id:
            if execution_id not in requested_ids:
                all_execution_ids.append(execution_id)
                requested_ids.add(execution_id)
        elif event.get("event_type") == "llm_responded" and execution_id:
            usage = payload.get("usage") or {}
            try:
                observed_event_total += sum(_complete_usage(type("Usage", (), {"usage": usage})()).values())
            except PairedShadowStopped:
                pass
    lifecycle_total = _record_lifecycle_tokens(base)
    paired_ids = list(paired_observation.get("execution_ids") or [])
    usage_reconciliation = {
        "all_request_count": len(all_execution_ids),
        "all_execution_ids": sorted(all_execution_ids),
        "paired_execution_ids_subset": (
            len(paired_ids) == 2 and set(paired_ids).issubset(requested_ids)
        ),
        "observed_event_total_tokens": observed_event_total,
        "lifecycle_total_tokens": lifecycle_total,
        "passed": (
            observed_event_total == lifecycle_total
            and len(paired_ids) == 2
            and set(paired_ids).issubset(requested_ids)
        ),
    }
    outcome_result = (manifest.get("outcome") or {}).get("result") or {}
    session_result = outcome_result.get("session_result") or {}
    controlled_stop = (
        outcome_result.get("error_type") == "PairedShadowStopped"
        or session_result.get("error_type") == "PairedShadowStopped"
    )
    primary_stop_reason: str | None = None
    if controlled_stop:
        failure_reason = ""
        for event in reversed(events):
            if event.get("event_type") not in {"pipeline_failed", "pipeline_finished"}:
                continue
            payload = event.get("payload") or {}
            summary = payload.get("output_summary") or {}
            failure_reason = str(
                payload.get("error") or summary.get("failure_reason") or ""
            ).strip()
            if failure_reason:
                break
        primary_stop_reason = "PairedShadowStopped"
        if failure_reason:
            primary_stop_reason += f": {failure_reason}"
    return {
        **base,
        **schedule_item,
        "code_snapshot": code_snapshot,
        "provider_identity": manifest.get("provider_runtime_identity"),
        "guard_observation": manifest.get("guard_observation"),
        "task_designer_context_intervention": descriptor,
        "paired_task_designer": paired_observation,
        "provider_usage_reconciliation": usage_reconciliation,
        **(
            {"primary_stop_reason": primary_stop_reason}
            if primary_stop_reason is not None
            else {}
        ),
    }


def _shadow_side_effect_proof_valid(intervention: Mapping[str, Any]) -> bool:
    proof = intervention.get("shadow_side_effect_proof")
    if not isinstance(proof, Mapping):
        return False
    pairs = (
        ("project_snapshot_before", "project_snapshot_after"),
        ("memory_hash_before", "memory_hash_after"),
        ("runtime_budget_hash_before", "runtime_budget_hash_after"),
        ("enhancement_budget_hash_before", "enhancement_budget_hash_after"),
    )
    snapshots = [
        proof.get("project_snapshot_before"),
        proof.get("project_snapshot_after"),
    ]
    if any(
        not isinstance(snapshot, Mapping)
        or snapshot.get("truncated") is True
        or bool(snapshot.get("symlink_paths"))
        for snapshot in snapshots
    ):
        return False
    return all(
        before in proof
        and after in proof
        and proof.get(before) == proof.get(after)
        for before, after in pairs
    )


def independent_hard_stop_reasons(
    record: Mapping[str, Any], protocol: Mapping[str, Any]
) -> list[str]:
    """Return gates that remain meaningful for an intentionally incomplete arm."""

    reasons: list[str] = []
    guard = record.get("guard_observation") or {}
    if guard.get("usage_censored") is True:
        reasons.append("guard_usage_censored")
    if guard.get("reservation_admission_failed") is True or int(
        guard.get("blocked_reservation_tokens") or 0
    ):
        reasons.append("guard_reservation_admission_failed")
    if int(guard.get("unsettled_reserved_tokens") or 0):
        reasons.append("guard_unsettled_reservation")
    if int(guard.get("reservation_overrun_tokens") or 0):
        reasons.append("guard_reservation_overrun")
    if int(guard.get("blocked_requests_before_transport") or 0):
        reasons.append("guard_request_blocked")
    if _record_lifecycle_tokens(record) > int(
        protocol["token_limits"]["per_run_hard"]
    ):
        reasons.append("per_run_hard_limit_exceeded")
    return reasons


def arm_stop_reasons(
    record: Mapping[str, Any], protocol: Mapping[str, Any]
) -> list[str]:
    """Recompute the paired contract over a V1-hardened full-run record."""

    reasons: list[str] = []
    schedule_fields = (
        "ordinal",
        "scenario_pair",
        "scenario_id",
        "production_policy",
        "shadow_policy",
        "request_order",
    )
    matching_schedule_items = [
        item
        for item in (protocol.get("schedule") or [])
        if int(item.get("ordinal") or 0) == int(record.get("ordinal") or 0)
    ]
    if (
        len(matching_schedule_items) != 1
        or any(
            record.get(field) != matching_schedule_items[0].get(field)
            for field in schedule_fields
        )
    ):
        reasons.append("frozen_schedule_item_mismatch")
    paired = record.get("paired_task_designer") or {}
    roles = paired.get("roles") or {}
    production = roles.get("production") or {}
    shadow = roles.get("shadow") or {}
    if int(paired.get("request_count") or 0) != 2:
        reasons.append("paired_request_membership_invalid")
    if set(roles) != {"production", "shadow"}:
        reasons.append("paired_role_membership_invalid")
    expected_production = str(record.get("production_policy") or "")
    expected_shadow = str(record.get("shadow_policy") or "")
    if (
        production.get("projection_policy") != expected_production
        or shadow.get("projection_policy") != expected_shadow
        or expected_production == expected_shadow
    ):
        reasons.append("paired_projection_policy_mismatch")
    runtime_hashes = {
        str(production.get("runtime_contract_hash") or ""),
        str(shadow.get("runtime_contract_hash") or ""),
        str(
            (record.get("task_designer_context_intervention") or {}).get(
                "runtime_contract_hash"
            )
            or ""
        ),
    }
    if len(runtime_hashes) != 1 or "" in runtime_hashes:
        reasons.append("paired_runtime_contract_mismatch")
    completion_limits = {
        int(production.get("max_completion_tokens") or 0),
        int(shadow.get("max_completion_tokens") or 0),
    }
    if len(completion_limits) != 1 or 0 in completion_limits:
        reasons.append("paired_max_completion_mismatch")
    reasoning_modes = {
        str(production.get("reasoning_mode") or ""),
        str(shadow.get("reasoning_mode") or ""),
    }
    if len(reasoning_modes) != 1 or reasoning_modes != {
        str(protocol["task_designer_request_contract"]["reasoning_mode"])
    }:
        reasons.append("paired_reasoning_policy_mismatch")
    paired_execution_ids = list(paired.get("execution_ids") or [])
    reconciliation = record.get("provider_usage_reconciliation") or {}
    all_execution_ids = set(reconciliation.get("all_execution_ids") or [])
    if (
        len(paired_execution_ids) != 2
        or len(set(paired_execution_ids)) != 2
        or not set(paired_execution_ids).issubset(all_execution_ids)
        or reconciliation.get("paired_execution_ids_subset") is not True
    ):
        reasons.append("paired_execution_membership_invalid")
    if (
        paired.get("usage_observed") is not True
        or production.get("usage_observed") is not True
        or shadow.get("usage_observed") is not True
        or paired.get("duplicate_execution_ids")
        or paired.get("failed_execution_ids")
    ):
        reasons.append("paired_usage_incomplete")
    if any(
        int(role.get("response_content_length") or 0) <= 0
        or str(role.get("finish_reason") or "").lower()
        in {"length", "max_tokens"}
        for role in (production, shadow)
    ):
        reasons.append("paired_response_incomplete")
    paired_total = int(paired.get("provider_total_tokens") or 0)
    if paired_total <= 0 or paired_total > _record_lifecycle_tokens(record):
        reasons.append("paired_usage_lifecycle_mismatch")
    lifecycle_total = _record_lifecycle_tokens(record)
    if (
        reconciliation.get("passed") is not True
        or int(reconciliation.get("observed_event_total_tokens") or 0)
        != lifecycle_total
        or int(reconciliation.get("lifecycle_total_tokens") or 0)
        != lifecycle_total
    ):
        reasons.append("provider_usage_lifecycle_mismatch")
    intervention = record.get("task_designer_context_intervention") or {}
    scenario_id = str(record.get("scenario_id") or "")
    frozen_scenario = (protocol.get("scenarios") or {}).get(scenario_id) or {}
    if (
        intervention.get("scenario_id") != scenario_id
        or intervention.get("production_policy") != expected_production
        or intervention.get("shadow_policy") != expected_shadow
        or intervention.get("source_fingerprint")
        != frozen_scenario.get("frozen_source_fingerprint")
        or intervention.get("goal_hash") != frozen_scenario.get("frozen_goal_hash")
        or int(intervention.get("builder_entry_count") or 0) != 1
        or not str(intervention.get("shared_snapshot_hash") or "")
    ):
        reasons.append("paired_source_descriptor_invalid")
    expected_contracts = (protocol.get("scenario_candidate_contracts") or {}).get(
        scenario_id
    )
    canonical_candidate_payloads = intervention.get(
        "canonical_candidate_payloads"
    ) or {}
    if (
        not isinstance(expected_contracts, Mapping)
        or intervention.get("candidate_contracts") != expected_contracts
        or set((intervention.get("candidate_fingerprints") or {})) != set(ARMS)
        or any(
            not str(value or "")
            for value in (intervention.get("candidate_fingerprints") or {}).values()
        )
    ):
        reasons.append("paired_candidate_contract_invalid")
    if set(canonical_candidate_payloads) != set(ARMS) or any(
        not isinstance(canonical_candidate_payloads.get(policy), list)
        for policy in ARMS
    ):
        reasons.append("paired_candidate_payload_contract_invalid")
    else:
        recomputed_fingerprints = {
            policy: _sha256(canonical_candidate_payloads[policy])
            for policy in ARMS
        }
        if intervention.get("candidate_fingerprints") != recomputed_fingerprints:
            reasons.append("paired_candidate_fingerprint_mismatch")
        for policy in ARMS:
            contract = (expected_contracts or {}).get(policy) or {}
            content = "\n".join(
                str(payload.get("content") or "")
                for payload in canonical_candidate_payloads[policy]
                if isinstance(payload, Mapping)
            )
            if any(
                marker not in content
                for marker in (contract.get("required_present") or [])
            ) or any(
                marker in content
                for marker in (contract.get("required_absent") or [])
            ):
                reasons.append("paired_candidate_payload_contract_invalid")
                break
    expected_shared_snapshot_hash = _sha256(
        {
            "runtime_contract_hash": intervention.get("runtime_contract_hash"),
            "source_fingerprint": intervention.get("source_fingerprint"),
            "goal_hash": intervention.get("goal_hash"),
            "completed_iteration": intervention.get("completed_iteration"),
        }
    )
    if intervention.get("shared_snapshot_hash") != expected_shared_snapshot_hash:
        reasons.append("paired_shared_snapshot_hash_mismatch")
    sentinel_ids = intervention.get("sentinel_candidate_ids") or {}
    for role, policy in (("production", expected_production), ("shadow", expected_shadow)):
        expected_ids = set(sentinel_ids.get(policy) or [])
        candidate_payloads = canonical_candidate_payloads.get(policy) or []
        contract = (expected_contracts or {}).get(policy) or {}
        recomputed_sentinel_ids = {
            str(payload.get("candidate_id") or "")
            for payload in candidate_payloads
            if isinstance(payload, Mapping)
            and any(
                marker in str(payload.get("content") or "")
                for marker in (contract.get("required_present") or [])
            )
        }
        if expected_ids != recomputed_sentinel_ids:
            reasons.append("paired_candidate_sentinel_mismatch")
            break
        decisions = {
            str(decision.get("candidate_id") or ""): str(decision.get("action") or "")
            for decision in (roles.get(role) or {}).get("candidate_decisions") or []
            if isinstance(decision, Mapping)
        }
        if not expected_ids or any(
            decisions.get(candidate_id) != "kept" for candidate_id in expected_ids
        ):
            reasons.append("paired_candidate_sentinel_not_kept")
            break
    if not _shadow_side_effect_proof_valid(intervention):
        reasons.append("shadow_side_effect_proof_invalid")
    if intervention.get("production_output_consumed_by_downstream") is not True:
        reasons.append("production_output_not_consumed")
    production_response_hash = str(
        intervention.get("production_response_hash") or ""
    )
    production_consumed_hash = str(
        intervention.get("production_consumed_payload_hash") or ""
    )
    consumed_hashes = list(
        intervention.get("downstream_consumed_payload_hashes") or []
    )
    if (
        not production_response_hash
        or production_consumed_hash != production_response_hash
        or consumed_hashes != [production_response_hash]
    ):
        reasons.append("production_consumption_evidence_invalid")
    if (
        intervention.get("shadow_output_consumed_by_downstream") is not False
        or intervention.get("shadow_completed") is not True
        or str(intervention.get("shadow_response_hash") or "") in consumed_hashes
    ):
        reasons.append("shadow_output_contract_invalid")
    quality = record.get("quality_gate") or {}
    required_quality_checks = {
        "run_completed",
        "core_success",
        "verification_passed",
        "improvement_succeeded",
        "improvement_count",
        "mutation_scope",
        "required_commands",
        "unchanged_file",
        "fixed_decomposition",
    }
    quality_checks = quality.get("checks") or {}
    if quality.get("passed") is not True or any(
        quality_checks.get(check) is not True for check in required_quality_checks
    ):
        reasons.append("quality_gate_failed")
    if not str(quality.get("signature") or ""):
        reasons.append("quality_gate_signature_invalid")
    if record.get("provider_identity") != protocol["provider_identity"]:
        reasons.append("provider_identity_mismatch")
    if "overall_usage_coverage" not in record:
        reasons.append("usage_coverage_evidence_missing")
    elif float(record.get("overall_usage_coverage") or 0.0) != 1.0:
        reasons.append("incomplete_usage_coverage")
    if "unknown_failed_usage_count" not in record:
        reasons.append("unknown_failed_usage_evidence_missing")
    elif int(record.get("unknown_failed_usage_count") or 0):
        reasons.append("unknown_failed_usage")
    if "transport_retry_count" not in record:
        reasons.append("transport_retry_evidence_missing")
    elif int(record.get("transport_retry_count") or 0):
        reasons.append("transport_retry_observed")
    if record.get("mutation_classification_failures"):
        reasons.append("runtime_owned_mutation_invalid")
    producer = record.get("producer_validation")
    if not isinstance(producer, Mapping):
        reasons.append("producer_validation_missing")
    elif producer.get("passed") is not True:
        reasons.append("producer_validation_failed")
    mutation_keys = {
        "observed_project_mutations",
        "observed_enhancement_mutations",
        "project_root",
        "calculator_path",
    }
    if any(key not in record for key in mutation_keys):
        reasons.append("v1_mutation_evidence_missing")
    else:
        import stage9_task_designer_provider_sentinel as v1

        project_root = str(record.get("project_root") or "")
        calculator_path = str(
            Path(str(record.get("calculator_path") or "")).resolve(strict=False)
        )
        observed_mutations = record.get("observed_enhancement_mutations") or {}
        start_snapshot = intervention.get("enhancement_start_project_snapshot")
        expected_artifacts = (
            expected_runtime_artifact_manifest(start_snapshot)
            if isinstance(start_snapshot, Mapping)
            else []
        )
        recomputed = v1.classify_enhancement_mutations(
            project_root,
            observed_mutations,
            expected_artifacts=expected_artifacts,
        )
        recorded_user = record.get("user_owned_mutations") or {}
        recorded_user_paths = {
            key: list(recorded_user.get(key) or [])
            for key in (
                "added_paths",
                "deleted_paths",
                "modified_paths",
                "all_changed_paths",
            )
        }
        if (
            recomputed.get("failures")
            or list(
                (recomputed.get("user_owned_mutations") or {}).get(
                    "all_changed_paths"
                )
                or []
            )
            != [calculator_path]
            or recorded_user_paths != recomputed.get("user_owned_mutations")
        ):
            reasons.append("v1_mutation_scope_invalid")
        if v1.validate_modified_paths(
            project_root,
            list(observed_mutations.get("all_changed_paths") or []),
        ):
            reasons.append("v1_mutation_scope_invalid")
        if v1.validate_divide_docstring(calculator_path):
            reasons.append("v1_docstring_quality_failed")
    guard = record.get("guard_observation") or {}
    if guard.get("usage_censored") is True:
        reasons.append("guard_usage_censored")
    if guard.get("reservation_admission_failed") is True or int(
        guard.get("blocked_reservation_tokens") or 0
    ):
        reasons.append("guard_reservation_admission_failed")
    guard_calls = int(guard.get("logical_complete_calls_seen") or 0)
    effective_limits = list(guard.get("effective_max_completion_tokens") or [])
    if guard_calls < 2 or len(effective_limits) != guard_calls:
        reasons.append("guard_effective_completion_count_mismatch")
    if int(guard.get("unsettled_reserved_tokens") or 0):
        reasons.append("guard_unsettled_reservation")
    if int(guard.get("reservation_overrun_tokens") or 0):
        reasons.append("guard_reservation_overrun")
    if int(guard.get("blocked_requests_before_transport") or 0):
        reasons.append("guard_request_blocked")
    total = _record_lifecycle_tokens(record)
    if total > int(protocol["token_limits"]["per_run_hard"]):
        reasons.append("per_run_hard_limit_exceeded")
    return list(dict.fromkeys(reasons))


def _canonical_hash(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return _sha256(value)


def _memory_tree_hash(agent: Any) -> str:
    store = getattr(agent, "memory_store", None)
    root = Path(getattr(store, "data_dir", "")) if store is not None else None
    if root is None or not str(root) or not root.exists():
        return _sha256([])
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        entries.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return _sha256(entries)


def _response_payload(response: Any) -> dict[str, Any] | None:
    parsed = getattr(response, "parsed_json", None)
    if isinstance(parsed, dict):
        return parsed
    try:
        value = json.loads(str(getattr(response, "content", "") or ""))
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _validate_response_envelope(response: Any, *, role: str) -> dict[str, Any]:
    """Reject empty or truncated responses before any paired output is used."""

    _complete_usage(response)
    content = str(getattr(response, "content", "") or "")
    if not content.strip():
        raise PairedShadowStopped(f"{role} response is empty")
    finish_reason = str(getattr(response, "finish_reason", "") or "").lower()
    if finish_reason in {"length", "max_tokens"}:
        raise PairedShadowStopped(
            f"{role} response finish_reason indicates truncation"
        )
    payload = _response_payload(response)
    if payload is None:
        raise PairedShadowStopped(f"{role} response is not a JSON object")
    return payload


def _shadow_task_valid(
    agent: Any,
    response: Any,
    request: Any,
    *,
    state: Any,
    goal: Any,
    report: dict[str, Any],
    completed_iteration: int,
    coerce: bool = True,
    coerce_fn: Callable[..., Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    retained_ids: set[str] | None = None,
) -> bool:
    details = diagnostics if diagnostics is not None else {}
    payload = _response_payload(response)
    if payload is None or set(payload) - {"task"}:
        details["reason"] = "response_schema_violation"
        return False
    raw_task = payload.get("task")
    if not isinstance(raw_task, dict):
        details["reason"] = "response_schema_violation"
        return False
    if set(raw_task) - {
        "id",
        "goal_id",
        "description",
        "target_files",
        "acceptance_criteria",
        "risk_notes",
        "evidence_ids",
    }:
        details["reason"] = "response_schema_violation"
        return False
    project_root = Path(str(state.project_path)).expanduser().resolve(strict=False)
    safe_targets = {
        str(
            (
                Path(path).expanduser()
                if Path(path).expanduser().is_absolute()
                else project_root / path
            ).resolve(strict=False)
        )
        for path in state.safe_target_files
    }
    requested_targets = raw_task.get("target_files")
    if not isinstance(requested_targets, list) or not requested_targets:
        details["reason"] = "target_scope_violation"
        return False
    if any(
        str(
            (
                Path(str(path)).expanduser()
                if Path(str(path)).expanduser().is_absolute()
                else project_root / str(path)
            ).resolve(strict=False)
        )
        not in safe_targets
        for path in requested_targets
    ):
        details["reason"] = "target_scope_violation"
        return False
    decisions = getattr(getattr(request, "context_selection", None), "candidate_decisions", [])
    retained = set(retained_ids) if retained_ids is not None else {
        decision.candidate_id
        for decision in decisions
        if str(decision.action) not in {"omitted", "ContextCandidateAction.OMITTED"}
    }
    project_root = Path(str(state.project_path)).expanduser().resolve(strict=False)
    safe_targets = {
        str(
            (
                Path(path).expanduser()
                if Path(path).expanduser().is_absolute()
                else project_root / path
            ).resolve(strict=False)
        )
        for path in state.safe_target_files
    }
    raw_targets = raw_task.get("target_files")
    if not isinstance(raw_targets, list) or not raw_targets:
        details["reason"] = "target_scope_violation"
        return False
    requested_targets = []
    for item in raw_targets:
        if not isinstance(item, str) or not item.strip():
            details["reason"] = "target_scope_violation"
            return False
        path = Path(item).expanduser()
        requested_targets.append(
            str((path if path.is_absolute() else project_root / path).resolve(strict=False))
        )
    if any(target not in safe_targets for target in requested_targets):
        details["reason"] = "target_scope_violation"
        return False
    raw_evidence_ids = raw_task.get("evidence_ids")
    if not isinstance(raw_evidence_ids, list):
        details["reason"] = "response_schema_violation"
        return False
    if any(not isinstance(item, str) for item in raw_evidence_ids):
        details["reason"] = "response_schema_violation"
        return False
    normalized_evidence_ids = [item.strip() for item in raw_evidence_ids if item.strip()]
    accepted_evidence_ids = [
        item for item in normalized_evidence_ids if item in retained
    ]
    details.update(
        raw_evidence_ids=raw_evidence_ids,
        accepted_evidence_ids=accepted_evidence_ids,
        rejected_evidence_ids=[
            item for item in normalized_evidence_ids if item not in retained
        ],
    )
    if not coerce:
        return True
    coerced = (coerce_fn or agent._coerce_task)(
        raw_task,
        goal,
        state,
        completed_iteration,
        report,
        retained,
    )
    if coerced is None:
        details["reason"] = "production_coercion_failed"
        return False
    details["accepted_evidence_ids"] = list(
        getattr(coerced, "evidence_ids", accepted_evidence_ids) or []
    )
    details["rejected_evidence_ids"] = [
        item
        for item in normalized_evidence_ids
        if item not in set(details["accepted_evidence_ids"])
    ]
    return True


def _guard_combined_admission(client: Any, production: Any, shadow: Any) -> dict[str, int]:
    if not all(hasattr(client, name) for name in ("_prepare_request", "_request_reservation")):
        raise PairedShadowStopped("combined request reservation requires guarded client")
    prepared_production, _ = client._prepare_request(production)
    prepared_shadow, _ = client._prepare_request(shadow)
    production_tokens = int(client._request_reservation(prepared_production))
    shadow_tokens = int(client._request_reservation(prepared_shadow))
    settled_or_held = int(client.total_tokens) + int(client.unsettled_reserved_tokens)
    if int(client.calls) + 2 > int(client.max_calls):
        raise PairedShadowStopped("combined request reservation exceeds provider call limit")
    if settled_or_held + production_tokens + shadow_tokens > int(client.max_total_tokens):
        raise PairedShadowStopped("combined request reservation exceeds remaining provider tokens")
    return {
        "settled_or_held_tokens": settled_or_held,
        "production_reservation_tokens": production_tokens,
        "shadow_reservation_tokens": shadow_tokens,
        "combined_reservation_tokens": production_tokens + shadow_tokens,
    }


@contextmanager
def paired_task_designer_scenario_scope(
    production_policy: str,
    fixture: ScenarioFixture,
    *,
    agent: Any,
    protocol: Mapping[str, Any] | None = None,
):
    """Install one same-source production/shadow Task Designer boundary."""

    frozen = dict(protocol or load_protocol())
    validate_protocol(frozen)
    if production_policy not in ARMS:
        raise PairedShadowError("unsupported production projection policy")
    if fixture.scenario_id not in POSITIVE_SCENARIO_IDS:
        raise PairedShadowError("paired shadow accepts positive scenarios only")
    shadow_policy = "compact" if production_policy == "current" else "current"
    production_builder = iteration_agent_module.build_iteration_task_design_candidates
    original_complete_candidates = agent._complete_json_candidates
    client = agent.llm_client
    original_transport = client.complete
    descriptor: dict[str, Any] = {
        "scenario_id": fixture.scenario_id,
        "production_policy": production_policy,
        "shadow_policy": shadow_policy,
        "source_fingerprint": scenario_overlay_fingerprint(fixture),
        "goal_hash": fixture.goal_hash,
        "builder_entry_count": 0,
        "production_output_consumed_by_downstream": False,
        "shadow_output_consumed_by_downstream": False,
        "shadow_completed": False,
    }
    paired: dict[str, Any] = {}
    original_coerce_task = agent._coerce_task
    shadow_coercion_active = False

    def tracked_coerce_task(*args: Any, **kwargs: Any):
        result = original_coerce_task(*args, **kwargs)
        if result is not None and not shadow_coercion_active:
            raw_task = args[0] if args else kwargs.get("raw_task")
            consumed_hash = _sha256({"task": raw_task})
            descriptor.setdefault("downstream_consumed_payload_hashes", []).append(
                consumed_hash
            )
            if consumed_hash == descriptor.get("production_response_hash"):
                descriptor["production_output_consumed_by_downstream"] = True
                descriptor["production_consumed_payload_hash"] = consumed_hash
            if consumed_hash == descriptor.get("shadow_response_hash"):
                descriptor["shadow_output_consumed_by_downstream"] = True
        return result

    def build_both(**runtime_kwargs: Any):
        if descriptor["builder_entry_count"]:
            raise PairedShadowStopped("Task Designer builder entry count is not exactly one")
        descriptor["builder_entry_count"] = 1
        live_state = runtime_kwargs["project_state"].model_copy(deep=True)
        live_goal = runtime_kwargs["goal"].model_copy(deep=True)
        live_report = deepcopy(runtime_kwargs["improvement_report"])
        completed = int(runtime_kwargs["completed_iteration"])
        if _sha256(live_goal.model_dump(mode="json")) != fixture.goal_hash:
            raise PairedShadowStopped("runtime goal hash mismatch")
        snapshot = capture_enhancement_start_snapshot(live_state.project_path)
        if snapshot.get("truncated") or snapshot.get("symlink_paths"):
            raise PairedShadowStopped("enhancement start snapshot is untrustworthy")
        state, report = overlay_scenario_sources(live_state, live_report, fixture)
        session_constraints = runtime_kwargs.get("session_constraints")
        if session_constraints is not None:
            constraint_hash = getattr(session_constraints, "canonical_hash", None)
            if not constraint_hash:
                raise PairedShadowStopped(
                    "session constraint projection must be a validated typed state"
                )
            descriptor["session_constraints_hash"] = constraint_hash
        frozen_projection_source_hash = _sha256(
            {
                "state": state.model_dump(mode="json"),
                "goal": live_goal.model_dump(mode="json"),
                "report": report,
                "completed_iteration": completed,
            }
        )
        shared_runtime_hash = runtime_contract_hash(live_state, live_goal, live_report)
        shared_source_hash = _sha256(
            {
                "runtime_contract_hash": shared_runtime_hash,
                "source_fingerprint": descriptor["source_fingerprint"],
                "goal_hash": descriptor["goal_hash"],
                "completed_iteration": completed,
            }
        )
        candidates: dict[str, list[Any]] = {}
        for policy in (production_policy, shadow_policy):
            builder_kwargs = {
                "project_state": state,
                "goal": live_goal,
                "improvement_report": report,
                "completed_iteration": completed,
                "projection_policy": policy,
            }
            if session_constraints is not None:
                builder_kwargs["session_constraints"] = session_constraints
            built = production_builder(**builder_kwargs)
            if _sha256(
                {
                    "state": state.model_dump(mode="json"),
                    "goal": live_goal.model_dump(mode="json"),
                    "report": report,
                    "completed_iteration": completed,
                }
            ) != frozen_projection_source_hash:
                raise PairedShadowStopped(
                    "frozen Task Designer source changed during projection building"
                )
            contract, passed = _candidate_contract(
                fixture.scenario_id, policy, built, frozen
            )
            if not passed:
                raise PairedShadowStopped(f"{policy} candidate sentinel contract failed")
            candidates[policy] = built
            descriptor.setdefault("candidate_contracts", {})[policy] = contract
            canonical_payloads = [
                candidate.model_dump(mode="json") for candidate in built
            ]
            descriptor.setdefault("canonical_candidate_payloads", {})[
                policy
            ] = canonical_payloads
            descriptor.setdefault("candidate_fingerprints", {})[policy] = _sha256(
                canonical_payloads
            )
            descriptor.setdefault("sentinel_candidate_ids", {})[policy] = sorted(
                {
                    str(candidate.candidate_id)
                    for candidate in built
                    if any(
                        sentinel in str(candidate.content)
                        for sentinel in contract["required_present"]
                    )
                }
            )
        paired.update(
            state=state,
            goal=live_goal,
            report=report,
            completed_iteration=completed,
            candidates=candidates,
            runtime_contract_hash=shared_runtime_hash,
            shared_snapshot_hash=shared_source_hash,
        )
        descriptor.update(
            runtime_contract_hash=shared_runtime_hash,
            shared_snapshot_hash=shared_source_hash,
            completed_iteration=completed,
            enhancement_start_project_snapshot=snapshot,
            enhancement_start_snapshot_fingerprint=_sha256(snapshot),
            enhancement_start_snapshot_capture_count=1,
            enhancement_start_snapshot_observation_count=1,
            enhancement_start_snapshot_consistent=True,
            expected_runtime_artifact_manifest=expected_runtime_artifact_manifest(snapshot),
        )
        return candidates[production_policy]

    def complete_paired(candidates: list[Any], *, purpose: Any, **kwargs: Any):
        nonlocal shadow_coercion_active
        if purpose != ContextRequestPurpose.ITERATION_TASK_DESIGN:
            return original_complete_candidates(candidates, purpose=purpose, **kwargs)
        if not paired or candidates is not paired["candidates"][production_policy]:
            raise PairedShadowStopped("production candidates do not match paired snapshot")
        captured: dict[str, Any] = {}

        def production_transport(request: Any, **transport_kwargs: Any):
            if captured:
                raise PairedShadowStopped("production Task Designer transported more than once")
            shadow_base = build_context_candidate_request(
                client,
                candidates=paired["candidates"][shadow_policy],
                purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
                response_format=request.response_format,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout_seconds=request.timeout_seconds,
                transport_retries=0,
                reasoning_policy=request.reasoning_policy,
            )
            shadow_request = shadow_base.model_copy(
                update={
                    "trace_info": {
                        **shadow_base.trace_info,
                        "paired_shadow": {
                            "role": "shadow",
                            "projection_policy": shadow_policy,
                            "transport_ordinal": 2,
                            "runtime_contract_hash": paired["runtime_contract_hash"],
                        },
                    }
                }
            )
            production_request = request.model_copy(
                update={
                    "trace_info": {
                        **request.trace_info,
                        "paired_shadow": {
                            "role": "production",
                            "projection_policy": production_policy,
                            "transport_ordinal": 1,
                            "runtime_contract_hash": paired["runtime_contract_hash"],
                        },
                    }
                }
            )
            try:
                admission = _guard_combined_admission(
                    client, production_request, shadow_request
                )
            except PairedShadowStopped as exc:
                # The product reservation already exists at this boundary, but
                # no provider transport occurred.  Give its normal reconciliation
                # path authoritative zero usage so it can refund the reservation.
                exc.usage = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }
                captured["production_error"] = f"{type(exc).__name__}: {exc}"
                raise
            captured.update(
                production_request=production_request,
                shadow_request=shadow_request,
                combined_admission=admission,
            )
            descriptor["combined_admission"] = admission
            try:
                response = original_transport(production_request, **transport_kwargs)
            except Exception as exc:
                captured["production_error"] = f"{type(exc).__name__}: {exc}"
                stopped = PairedShadowStopped(f"production provider failed: {exc}")
                for attribute in ("usage", "finish_reason", "response_text"):
                    if hasattr(exc, attribute):
                        setattr(stopped, attribute, getattr(exc, attribute))
                raise stopped from exc
            captured["production_response"] = response
            return response

        with patch.object(client, "complete", new=production_transport):
            production_result = original_complete_candidates(
                candidates, purpose=purpose, **kwargs
            )
        if captured.get("production_error"):
            raise PairedShadowStopped(captured["production_error"])
        production_response = captured.get("production_response")
        if production_response is None:
            raise PairedShadowStopped("production response was not captured")
        _validate_response_envelope(production_response, role="production")
        descriptor["production_response_hash"] = _sha256(
            _response_payload(production_response)
        )
        descriptor["production_response_payload"] = _response_payload(
            production_response
        )
        production_diagnostics: dict[str, Any] = {}
        production_retained_ids = (
            set(production_result[1])
            if isinstance(production_result, tuple)
            and len(production_result) >= 2
            and isinstance(production_result[1], (set, frozenset))
            else None
        )
        if not _shadow_task_valid(
            agent,
            production_response,
            captured["production_request"],
            state=paired["state"],
            goal=paired["goal"],
            report=paired["report"],
            completed_iteration=paired["completed_iteration"],
            coerce=False,
            diagnostics=production_diagnostics,
            retained_ids=production_retained_ids,
        ):
            descriptor["production_rejection"] = dict(production_diagnostics)
            if production_diagnostics.get("reason") == "target_scope_violation":
                raise PairedShadowStopped(
                    "production response target authority violation; "
                    "not an authorized task"
                )
            raise PairedShadowStopped(
                "production response did not satisfy task schema or coercion"
            )
        descriptor["production_task_provenance"] = {
            key: list(production_diagnostics.get(key) or [])
            for key in (
                "raw_evidence_ids",
                "accepted_evidence_ids",
                "rejected_evidence_ids",
            )
        }

        project_before = capture_enhancement_start_snapshot(paired["state"].project_path)
        if project_before.get("truncated") or project_before.get("symlink_paths"):
            raise PairedShadowStopped("shadow before snapshot is untrustworthy")
        memory_before = _memory_tree_hash(agent)
        runtime_budget_before = _canonical_hash(agent.runtime_budget)
        enhancement_budget_before = _canonical_hash(agent.enhancement_budget.budget)
        shadow_response = None
        shadow_error: Exception | None = None
        try:
            shadow_response = original_transport(
                captured["shadow_request"], max_retries=1, use_cache=False
            )
        except Exception as exc:
            descriptor["shadow_error"] = f"{type(exc).__name__}: {exc}"
            shadow_error = exc
        project_after = capture_enhancement_start_snapshot(paired["state"].project_path)
        if project_after.get("truncated") or project_after.get("symlink_paths"):
            raise PairedShadowStopped("shadow after snapshot is untrustworthy")
        memory_after = _memory_tree_hash(agent)
        runtime_budget_after = _canonical_hash(agent.runtime_budget)
        enhancement_budget_after = _canonical_hash(agent.enhancement_budget.budget)
        descriptor["shadow_side_effect_proof"] = {
            "project_snapshot_before": project_before,
            "project_snapshot_after": project_after,
            "memory_hash_before": memory_before,
            "memory_hash_after": memory_after,
            "runtime_budget_hash_before": runtime_budget_before,
            "runtime_budget_hash_after": runtime_budget_after,
            "enhancement_budget_hash_before": enhancement_budget_before,
            "enhancement_budget_hash_after": enhancement_budget_after,
        }
        if (
            project_before != project_after
            or memory_before != memory_after
            or runtime_budget_before != runtime_budget_after
            or enhancement_budget_before != enhancement_budget_after
        ):
            raise PairedShadowStopped("shadow changed project, memory, or product budget state")
        if shadow_error is not None:
            raise PairedShadowStopped(f"shadow provider failed: {shadow_error}") from shadow_error
        if shadow_response is None:
            raise PairedShadowStopped("shadow response was not captured")
        _validate_response_envelope(shadow_response, role="shadow")
        descriptor["shadow_response_payload"] = _response_payload(shadow_response)
        shadow_diagnostics: dict[str, Any] = {}
        shadow_coercion_active = True
        try:
            if not _shadow_task_valid(
                agent,
                shadow_response,
                captured["shadow_request"],
                state=paired["state"],
                goal=paired["goal"],
                report=paired["report"],
                completed_iteration=paired["completed_iteration"],
                coerce_fn=original_coerce_task,
                diagnostics=shadow_diagnostics,
            ):
                descriptor["shadow_rejection"] = dict(shadow_diagnostics)
                if shadow_diagnostics.get("reason") == "target_scope_violation":
                    raise PairedShadowStopped(
                        "shadow response target authority violation; "
                        "not an authorized task"
                    )
                raise PairedShadowStopped(
                    "shadow response did not satisfy task schema or coercion"
                )
        finally:
            shadow_coercion_active = False
        descriptor["shadow_task_provenance"] = {
            key: list(shadow_diagnostics.get(key) or [])
            for key in (
                "raw_evidence_ids",
                "accepted_evidence_ids",
                "rejected_evidence_ids",
            )
        }
        descriptor["shadow_completed"] = True
        descriptor["shadow_response_hash"] = _sha256(
            _response_payload(shadow_response)
        )
        return production_result

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                iteration_agent_module,
                "build_iteration_task_design_candidates",
                new=build_both,
            )
        )
        stack.enter_context(
            patch.object(agent, "_complete_json_candidates", new=complete_paired)
        )
        stack.enter_context(patch.object(agent, "_coerce_task", new=tracked_coerce_task))
        stack.enter_context(
            patch.object(
                agent,
                "_goal_from_candidate",
                new=lambda selected_candidate, report, evaluation: fixture.goal,
            )
        )
        yield descriptor


def preflight(protocol: Mapping[str, Any] | None = None) -> dict[str, Any]:
    frozen = dict(protocol or load_protocol())
    validate_protocol(frozen)
    runtime_offline = offline_report_snapshot(run_offline_sentinel())
    if runtime_offline != load_offline_report() or runtime_offline.get("passed") is not True:
        raise PairedShadowError("offline Stage 9 gate is not frozen and passing")
    from core.config import LLMSettings
    from run_observation import provider_runtime_identity

    identity = provider_runtime_identity(LLMSettings())
    if identity != frozen["provider_identity"]:
        raise PairedShadowError("configured provider identity does not match")
    historical = int(frozen["historical_stage9_spend"]["observed_complete_tokens"])
    return {
        "campaign_id": frozen["campaign_id"],
        "provider_calls": 0,
        "offline_gate_passed": True,
        "provider_identity": identity,
        "schedule": build_schedule(frozen),
        "historical_stage9_tokens": historical,
        "remaining_stage9_tokens": int(frozen["token_limits"]["stage9_lifetime_hard"]) - historical,
        "execute_requires_explicit_flag": frozen["execute_requires_explicit_flag"],
    }


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def execute_campaign(
    *,
    output_dir: Path,
    run_arm: Callable[[dict[str, Any], Path, Mapping[str, Any]], dict[str, Any]] | None = None,
    protocol: Mapping[str, Any] | None = None,
    protocol_path: Path = PROTOCOL_PATH,
    max_runs: int | None = None,
    additional_historical_tokens: int = 0,
    resume_reference_path: Path | None = None,
) -> dict[str, Any]:
    """Execute through the hardened subprocess adapter or an injected test arm."""

    frozen = dict(protocol or load_protocol())
    schedule = build_schedule(frozen)
    if isinstance(additional_historical_tokens, bool) or not isinstance(
        additional_historical_tokens, int
    ) or additional_historical_tokens < 0:
        raise PairedShadowError(
            "additional_historical_tokens must be a non-negative integer"
        )
    resume_prefix = (
        load_reanalysis_resume_prefix(resume_reference_path, protocol=frozen)
        if resume_reference_path is not None
        else {
            "records": [],
            "seed_lifecycle_tokens": 0,
            "seed_scenario_tokens": {},
            "next_ordinal": 1,
        }
    )
    seed_records = [deepcopy(record) for record in resume_prefix["records"]]
    if seed_records and additional_historical_tokens != int(
        resume_prefix["required_additional_historical_tokens"]
    ):
        raise PairedShadowError(
            "resume prefix historical accounting does not match the failed pilot"
        )
    remaining_schedule = schedule[len(seed_records) :]
    if max_runs is not None and (
        max_runs <= 0 or max_runs > len(remaining_schedule)
    ):
        raise PairedShadowError(
            f"max_runs must be between 1 and {len(remaining_schedule)}"
        )
    selected_schedule = (
        remaining_schedule[:max_runs]
        if max_runs is not None
        else remaining_schedule
    )
    actual_protocol_path = protocol_path.resolve()
    if load_protocol(actual_protocol_path) != frozen:
        raise PairedShadowError(
            "parent protocol object and subprocess protocol path differ"
        )
    preflight(frozen)
    output_dir.mkdir(parents=True, exist_ok=False)
    new_records: list[dict[str, Any]] = []
    observed_paired_execution_ids: set[str] = {
        str(execution_id)
        for record in seed_records
        for execution_id in (
            (record.get("paired_task_designer") or {}).get("execution_ids") or []
        )
    }
    state_path = output_dir / "campaign_state.json"
    historical = (
        int(frozen["historical_stage9_spend"]["observed_complete_tokens"])
        + additional_historical_tokens
        + int(resume_prefix["seed_lifecycle_tokens"])
    )
    hard = int(frozen["token_limits"]["stage9_lifetime_hard"])
    source_snapshot = code_snapshot_sha256(frozen)

    def spend_snapshot() -> dict[str, Any]:
        try:
            return evaluate_campaign_spend(
                new_records, campaign_hard=hard, historical_tokens=historical
            )
        except PairedShadowError as exc:
            return {
                "historical_tokens": historical,
                "observed_lifecycle_tokens": None,
                "held_unknown_usage_tokens": None,
                "effective_campaign_tokens": hard,
                "remaining_campaign_tokens": 0,
                "hard_failures": ["token_accounting_invalid"],
                "accounting_error": "negative_token_accounting",
                "accounting_error_detail": f"{type(exc).__name__}: {exc}",
            }

    def persist(status: str, reasons: list[str]) -> None:
        _write_json_atomic(
            state_path,
            {
                "campaign_id": frozen["campaign_id"],
                "status": status,
                "records": [*seed_records, *new_records],
                "spend": spend_snapshot(),
                "stop_reasons": reasons,
            },
        )

    persist("running", [])
    try:
        prior_scenario_tokens: dict[str, int] = {
            str(key): int(value)
            for key, value in resume_prefix["seed_scenario_tokens"].items()
        }
        if seed_records and additional_historical_tokens:
            seed_scenario = str(seed_records[0]["scenario_id"])
            prior_scenario_tokens[seed_scenario] = (
                prior_scenario_tokens.get(seed_scenario, 0)
                + additional_historical_tokens
            )
        scenario_tokens = dict(prior_scenario_tokens)
        for item in selected_schedule:
            if code_snapshot_sha256(frozen) != source_snapshot:
                raise PairedShadowStopped("campaign source snapshot changed before run")
            run_dir = output_dir / (
                f"{item['ordinal']:02d}_p{item['scenario_pair']}_"
                f"{item['scenario_id']}_{item['production_policy']}_production"
            )
            if run_arm is None:
                command = build_run_command(
                    run_dir,
                    item,
                    frozen,
                    records=new_records,
                    protocol_path=protocol_path,
                    additional_historical_tokens=(
                        additional_historical_tokens
                        + int(resume_prefix["seed_lifecycle_tokens"])
                    ),
                    prior_scenario_tokens=prior_scenario_tokens,
                )
                completed = subprocess.run(
                    command,
                    check=False,
                    timeout=int(frozen["execution"]["per_run_wall_clock_seconds"]),
                )
                if code_snapshot_sha256(frozen) != source_snapshot:
                    raise PairedShadowStopped("campaign source snapshot changed during run")
                if completed.returncode != 0:
                    raise PairedShadowStopped(
                        "paired-shadow subprocess failed: "
                        f"returncode={completed.returncode}"
                    )
                if not (run_dir / "manifest.json").is_file():
                    raise PairedShadowStopped(
                        "paired-shadow run produced no manifest: "
                        f"returncode={completed.returncode}"
                    )
                record = build_integrated_run_record(
                    run_dir,
                    schedule_item=item,
                    protocol=frozen,
                    code_snapshot=source_snapshot,
                )
            else:
                record = run_arm(item, run_dir, frozen)
                if code_snapshot_sha256(frozen) != source_snapshot:
                    raise PairedShadowStopped("campaign source snapshot changed during run")
            new_records.append(record)
            run_dir.mkdir(parents=True, exist_ok=True)
            # Record-first persistence: no quality, paired, mutation, or spend
            # gate is evaluated until the complete run record is durable.
            _write_json_atomic(run_dir / "campaign_record.json", record)
            if code_snapshot_sha256(frozen) != source_snapshot:
                reasons = [
                    *(
                        [str(record["primary_stop_reason"])]
                        if record.get("primary_stop_reason")
                        else []
                    ),
                    "campaign source snapshot changed after record build",
                ]
                persist("stopped", reasons)
                raise PairedShadowStopped(reasons[-1])
            total = _record_lifecycle_tokens(record)
            scenario = str(item["scenario_id"])
            scenario_tokens[scenario] = scenario_tokens.get(scenario, 0) + total
            primary_stop_reason = str(record.get("primary_stop_reason") or "")
            reasons = (
                [
                    primary_stop_reason,
                    *independent_hard_stop_reasons(record, frozen),
                ]
                if primary_stop_reason
                else arm_stop_reasons(record, frozen)
            )
            if any(
                record.get(field) != item.get(field)
                for field in (
                    "ordinal",
                    "scenario_pair",
                    "scenario_id",
                    "production_policy",
                    "shadow_policy",
                    "request_order",
                )
            ):
                reasons.append("campaign_schedule_item_mismatch")
            paired_execution_ids = set(
                (record.get("paired_task_designer") or {}).get("execution_ids")
                or []
            )
            if paired_execution_ids & observed_paired_execution_ids:
                reasons.append("paired_execution_id_replay")
            observed_paired_execution_ids.update(paired_execution_ids)
            reasons.extend(list(record.get("stop_reasons") or []))
            if scenario_tokens[scenario] > int(
                frozen["token_limits"]["per_scenario_v2_hard"]
            ):
                reasons.append("per_scenario_hard_limit_exceeded")
            try:
                reasons.extend(
                    evaluate_campaign_spend(
                        new_records,
                        campaign_hard=hard,
                        historical_tokens=historical,
                    )["hard_failures"]
                )
            except PairedShadowError:
                reasons.append("accounting_error")
            persist("stopped" if reasons else "running", list(dict.fromkeys(reasons)))
            if reasons:
                raise PairedShadowStopped(", ".join(dict.fromkeys(reasons)))
    except Exception as exc:
        current = json.loads(state_path.read_text(encoding="utf-8"))
        if current.get("status") != "stopped":
            persist("stopped", [f"{type(exc).__name__}: {exc}"])
        raise
    if code_snapshot_sha256(frozen) != source_snapshot:
        persist("stopped", ["campaign source snapshot changed before completion"])
        raise PairedShadowStopped("campaign source snapshot changed before completion")
    all_records = [*seed_records, *new_records]
    fully_completed = (
        len(all_records) == len(schedule)
        and len(selected_schedule) == len(remaining_schedule)
    )
    persist("completed" if fully_completed else "sentinel_completed", [])
    return {
        "campaign_id": frozen["campaign_id"],
        "eligible": fully_completed,
        "opening_historical_tokens": historical,
        "opening_remaining_tokens": hard - historical,
        "records": all_records,
        "spend": evaluate_campaign_spend(
            new_records, campaign_hard=hard, historical_tokens=historical
        ),
    }
