"""Stage 5B-3b pre-transport gate for the paired canary.

This module composes the Stage 5B-2 projection measurements, the Stage 5B-3a
full execute/runtime/checkpoint admission probe, and the cross-arm Stage 12
campaign ledger.  It reserves worst-case request budgets but deliberately does
not reconcile usage or transport a Provider request.  A later real canary can
reuse this gate only after the explicit transport switch is separately
reviewed.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from stage12_session_compact_canary_admission import (
    CanaryCampaignLedger,
    DEFAULT_PROTOCOL_PATH,
    ProjectionFeatureFlag,
    ProjectionPolicy,
    RuntimeKillSwitch,
    SessionCompactCanaryAdmission,
    UsageRoute,
    run_no_provider_preflight,
)
from stage13_session_compact_pipeline_offline import run_offline_pipeline_probe
from stage14_full_entry_admission import run_full_entry_admission


class CanaryGateStop(RuntimeError):
    """The pre-transport canary gate stopped fail closed."""


def run_pretransport_gate(
    *,
    feature_flag: ProjectionFeatureFlag = ProjectionFeatureFlag.CANARY_ENABLED,
    kill_switch: RuntimeKillSwitch = RuntimeKillSwitch.ARMED,
    completion_reserve_tokens: int = 512,
    framing_reserve_tokens: int = 32,
) -> dict[str, Any]:
    """Reserve both paired arms without exposing a Provider transport."""

    stop_reasons: list[str] = []
    if feature_flag != ProjectionFeatureFlag.CANARY_ENABLED:
        stop_reasons.append("projection_feature_flag_disabled")
    if kill_switch != RuntimeKillSwitch.ARMED:
        stop_reasons.append("runtime_kill_switch_engaged")
    if stop_reasons:
        return {
            "status": "stopped",
            "provider_execution_admitted": False,
            "transport_attempted": False,
            "provider_calls": 0,
            "network_calls": 0,
            "project_mutations": 0,
            "stop_reasons": stop_reasons,
        }

    contract = SessionCompactCanaryAdmission.model_validate_json(
        DEFAULT_PROTOCOL_PATH.read_text(encoding="utf-8")
    )
    if contract.projection_feature_flag != feature_flag:
        raise CanaryGateStop("runtime feature flag does not match locked protocol")
    if contract.runtime_kill_switch != kill_switch:
        raise CanaryGateStop("runtime kill switch does not match locked protocol")
    admission = run_no_provider_preflight()
    if admission.get("status") != "passed" or admission.get("controls_admitted") is not True:
        raise CanaryGateStop("locked protocol controls are not admitted")
    if (
        admission.get("projection_feature_flag") != feature_flag.value
        or admission.get("runtime_kill_switch") != kill_switch.value
    ):
        raise CanaryGateStop("preflight controls do not match the requested protocol controls")
    full_entry = run_full_entry_admission(
        feature_flag=feature_flag,
        kill_switch=kill_switch,
    )
    if full_entry.get("status") != "passed":
        raise CanaryGateStop("full execute/runtime admission did not pass")
    projection = run_offline_pipeline_probe(
        feature_flag=feature_flag,
        kill_switch=kill_switch,
    )
    if projection.get("status") != "passed":
        raise CanaryGateStop("paired projection admission did not pass")

    ledger = CanaryCampaignLedger(
        accounting=contract.usage_accounting,
        hard_caps=contract.hard_caps,
    )
    reservation_rows: list[dict[str, Any]] = []
    for arm_name, arm_payload in projection["arms"].items():
        arm = ProjectionPolicy(arm_name)
        route = UsageRoute.PRIMARY if arm == ProjectionPolicy.COMPACT else UsageRoute.FALLBACK
        for ordinal, request in enumerate(arm_payload["requests"], start=1):
            rendered_tokens = int(request["rendered_input_tokens"])
            reserved_tokens = rendered_tokens + int(completion_reserve_tokens) + int(framing_reserve_tokens)
            execution_id = f"stage5b3b:{arm.value}:{ordinal}"
            ledger = ledger.reserve(
                execution_id=execution_id,
                arm=arm,
                route=route,
                reserved_tokens=reserved_tokens,
            )
            reservation_rows.append(
                {
                    "execution_id": execution_id,
                    "arm": arm.value,
                    "route": route.value,
                    "purpose": request["purpose"],
                    "rendered_input_tokens": rendered_tokens,
                    "reserved_tokens": reserved_tokens,
                    "provider_usage_observed": False,
                }
            )

    return {
        "status": "passed",
        "claim_boundary": "pretransport_full_entry_and_campaign_budget",
        "admission": admission,
        "full_entry": {
            "production_entry_exercised": full_entry["production_entry_exercised"],
            "session_constraints_hash": full_entry["session_constraints_hash"],
            "checkpoint_constraints_hash": full_entry["checkpoint_constraints_hash"],
            "session_turn_source_hash": full_entry["session_turn_source_hash"],
            "checkpoint_turn_source_hash": full_entry["checkpoint_turn_source_hash"],
        },
        "projection": {
            "source_snapshot_hash": projection["source_snapshot_hash"],
            "constraint_recall": {
                arm: payload["constraint_recall"]
                for arm, payload in projection["arms"].items()
            },
            "dialog_recall": {
                arm: payload["dialog_recall"]
                for arm, payload in projection["arms"].items()
            },
            "session_turn_source_hashes": sorted(
                {
                    request["session_turn_source_hash"]
                    for request in projection["requests"]
                }
            ),
        },
        "ledger": {
            "reservations": reservation_rows,
            "reservation_count": len(ledger.reservations),
            "observation_count": len(ledger.observations),
            "hard_caps": ledger.hard_caps.model_dump(mode="json"),
            "unknown_usage_action": ledger.accounting.unknown_usage_action,
        },
        "provider_execution_admitted": False,
        "transport_attempted": False,
        "provider_calls": 0,
        "network_calls": 0,
        "project_mutations": 0,
        "stop_reasons": ["provider_transport_disabled_in_stage5b3b"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flag-off", action="store_true")
    parser.add_argument("--kill-switch", action="store_true")
    args = parser.parse_args()
    result = run_pretransport_gate(
        feature_flag=(
            ProjectionFeatureFlag.DISABLED
            if args.flag_off
            else ProjectionFeatureFlag.CANARY_ENABLED
        ),
        kill_switch=(
            RuntimeKillSwitch.ENGAGED
            if args.kill_switch
            else RuntimeKillSwitch.ARMED
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
