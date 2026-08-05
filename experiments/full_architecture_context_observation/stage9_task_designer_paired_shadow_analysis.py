"""Read-only analysis for a completed Stage 9 V2 paired-shadow campaign.

The analyzer consumes durable campaign state only.  It never executes an arm,
calls an LLM provider, or changes the production projection policy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from stage9_task_designer_paired_shadow import build_schedule, load_protocol, validate_protocol


RECOMMENDATION = "requires_separate_review"
ROLES = ("production", "shadow")
POLICIES = ("current", "compact")
SCHEDULE_FIELDS = (
    "ordinal",
    "scenario_pair",
    "scenario_id",
    "production_policy",
    "shadow_policy",
    "request_order",
)
QUALITY_CHECKS = frozenset(
    {
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
)


class PairedShadowAnalysisError(ValueError):
    """A durable campaign cannot support the frozen offline analysis."""


def _reject(reason: str, detail: str | None = None) -> None:
    message = reason if not detail else f"{reason}: {detail}"
    raise PairedShadowAnalysisError(message)


def _positive_integer(value: Any, *, detail: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _reject("input_usage_invalid", detail)
    return value


def _validate_quality(record: Mapping[str, Any], *, ordinal: int) -> str:
    gate = record.get("quality_gate")
    if not isinstance(gate, Mapping) or gate.get("passed") is not True:
        _reject("quality_gate_invalid", f"ordinal={ordinal}")
    signature = gate.get("signature")
    checks = gate.get("checks")
    if (
        not isinstance(signature, str)
        or not signature.strip()
        or not isinstance(checks, Mapping)
        or set(checks) != QUALITY_CHECKS
        or any(checks.get(check) is not True for check in QUALITY_CHECKS)
    ):
        _reject("quality_gate_invalid", f"ordinal={ordinal}")
    return signature


def analyze_completed_campaign(
    campaign: Mapping[str, Any],
    *,
    protocol: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and summarize one durable six-arm V2 campaign.

    Input-token observations are attributed to the projection policy on each
    role, not to the run's production policy.  The result is descriptive only:
    production adoption always remains a separately reviewed decision.
    """

    frozen = dict(protocol or load_protocol())
    validate_protocol(frozen)
    if campaign.get("campaign_id") != frozen.get("campaign_id"):
        _reject("campaign_id_mismatch")
    if campaign.get("status") != "completed":
        _reject("campaign_not_completed")
    if campaign.get("stop_reasons"):
        _reject("campaign_has_stop_reasons")
    spend = campaign.get("spend")
    if not isinstance(spend, Mapping) or spend.get("hard_failures"):
        _reject("campaign_has_hard_failures")

    records = campaign.get("records")
    schedule = build_schedule(frozen)
    if not isinstance(records, list) or len(records) != len(schedule):
        _reject("record_count_invalid")

    production_policy_counts = {policy: 0 for policy in POLICIES}
    observation_counts = {policy: 0 for policy in POLICIES}
    input_totals = {policy: 0 for policy in POLICIES}
    signatures: list[dict[str, Any]] = []
    paired_reductions: list[dict[str, Any]] = []

    for expected, record in zip(schedule, records, strict=True):
        ordinal = int(expected["ordinal"])
        if not isinstance(record, Mapping) or any(
            record.get(field) != expected.get(field) for field in SCHEDULE_FIELDS
        ):
            _reject("frozen_schedule_mismatch", f"ordinal={ordinal}")
        if record.get("primary_stop_reason") or record.get("stop_reasons"):
            _reject("arm_has_stop_reasons", f"ordinal={ordinal}")

        production_policy = str(record["production_policy"])
        if production_policy not in production_policy_counts:
            _reject("production_policy_invalid", f"ordinal={ordinal}")
        production_policy_counts[production_policy] += 1

        signature = _validate_quality(record, ordinal=ordinal)
        signatures.append(
            {
                "ordinal": ordinal,
                "scenario_id": str(record["scenario_id"]),
                "production_policy": production_policy,
                "signature": signature,
            }
        )

        paired = record.get("paired_task_designer")
        roles = paired.get("roles") if isinstance(paired, Mapping) else None
        if not isinstance(roles, Mapping) or set(roles) != set(ROLES):
            _reject("role_membership_invalid", f"ordinal={ordinal}")
        per_policy: dict[str, int] = {}
        for role in ROLES:
            observation = roles[role]
            if not isinstance(observation, Mapping) or observation.get("role") != role:
                _reject("role_membership_invalid", f"ordinal={ordinal}, role={role}")
            expected_policy = str(record[f"{role}_policy"])
            policy = observation.get("projection_policy")
            if policy != expected_policy or policy not in observation_counts:
                _reject("role_policy_mismatch", f"ordinal={ordinal}, role={role}")
            tokens = _positive_integer(
                observation.get("provider_input_tokens"),
                detail=f"ordinal={ordinal}, role={role}",
            )
            observation_counts[policy] += 1
            input_totals[policy] += tokens
            per_policy[policy] = tokens
        if set(per_policy) != set(POLICIES):
            _reject("role_policy_mismatch", f"ordinal={ordinal}")
        current = per_policy["current"]
        compact = per_policy["compact"]
        paired_reductions.append(
            {
                "ordinal": ordinal,
                "scenario_id": str(record["scenario_id"]),
                "current": current,
                "compact": compact,
                "absolute_reduction": current - compact,
                "reduction_fraction": (current - compact) / current,
            }
        )

    expected_production_counts = {"current": 3, "compact": 3}
    if production_policy_counts != expected_production_counts:
        _reject("production_policy_balance_invalid")
    expected_observation_counts = {"current": 6, "compact": 6}
    if observation_counts != expected_observation_counts:
        _reject("policy_observation_count_invalid")

    current_total = input_totals["current"]
    compact_total = input_totals["compact"]
    absolute_reduction = current_total - compact_total
    return {
        "campaign_id": frozen["campaign_id"],
        "eligible": True,
        "recommendation": RECOMMENDATION,
        "production_policy_counts": production_policy_counts,
        "observation_counts": observation_counts,
        "primary_metrics": {
            "provider_input_tokens": {
                **input_totals,
                "absolute_reduction": absolute_reduction,
                "reduction_fraction": absolute_reduction / current_total,
            },
            "paired_reductions": paired_reductions,
        },
        "quality": {
            "all_passed": True,
            "signatures": signatures,
        },
        "claim_boundary": "same_source_paired_task_designer_input_only",
    }


def analyze_completed_campaign_file(
    path: Path,
    *,
    protocol: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read and analyze a campaign-state artifact without writing any file."""

    try:
        campaign = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PairedShadowAnalysisError(f"campaign_state_invalid: {exc}") from exc
    if not isinstance(campaign, Mapping):
        _reject("campaign_state_invalid")
    return analyze_completed_campaign(campaign, protocol=protocol)
