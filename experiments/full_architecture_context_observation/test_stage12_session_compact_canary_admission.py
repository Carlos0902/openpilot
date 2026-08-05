from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from pydantic import ValidationError

from stage12_session_compact_canary_admission import (
    DEFAULT_PROTOCOL_PATH,
    AdmissionError,
    CanaryHardCaps,
    CanaryCampaignLedger,
    CanaryUsageLedger,
    ProjectionFeatureFlag,
    ProjectionPolicy,
    RuntimeKillSwitch,
    SessionCompactCanaryAdmission,
    UsageAccountingContract,
    UsageObservation,
    UsageRoute,
    admission_stop_reasons,
    run_no_provider_preflight,
    summarize_usage,
)


def _contract() -> SessionCompactCanaryAdmission:
    return SessionCompactCanaryAdmission.model_validate_json(
        DEFAULT_PROTOCOL_PATH.read_text(encoding="utf-8")
    )


def test_no_provider_preflight_passes_with_hash_locked_reversible_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )

    result = run_no_provider_preflight()

    assert result["status"] == "passed"
    assert result["controls_admitted"] is True
    assert result["provider_execution_admitted"] is False
    assert result["provider_calls"] == 0
    assert result["network_calls"] == 0
    assert result["project_mutations"] == 0
    assert result["protocol_sha256"].startswith("sha256:")
    assert result["fixture_sha256"] == _contract().hash_locks.fixture_sha256
    assert result["primary_usage_account"] != result["fallback_usage_account"]
    assert result["environment_id"]
    assert Path(result["environment_executable"]).is_file()
    assert result["hard_caps"]["per_arm_tokens"] <= result["hard_caps"]["campaign_tokens"]


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        ({"projection_feature_flag": ProjectionFeatureFlag.DISABLED}, "projection_feature_flag_disabled"),
        ({"runtime_kill_switch": RuntimeKillSwitch.ENGAGED}, "runtime_kill_switch_engaged"),
    ],
)
def test_runtime_controls_fail_closed(update: dict[str, object], reason: str) -> None:
    admission = _contract().model_copy(update=update)

    assert admission_stop_reasons(admission) == [reason]


def test_environment_receipt_fails_closed_when_not_ready_or_not_authorized() -> None:
    admission = _contract()
    not_ready = admission.model_copy(
        update={"environment_receipt": admission.environment_receipt.model_copy(update={"readiness": "not_ready"})}
    )
    unauthorized = admission.model_copy(
        update={
            "environment_receipt": admission.environment_receipt.model_copy(
                update={"read_only_preflight_authorized": False}
            )
        }
    )

    assert admission_stop_reasons(not_ready) == ["environment_not_ready"]
    assert admission_stop_reasons(unauthorized) == ["environment_preflight_not_authorized"]


def test_campaign_ledger_enforces_cross_arm_calls_tokens_and_wall_clock() -> None:
    admission = _contract()
    caps = CanaryHardCaps(
        per_arm_tokens=100,
        campaign_tokens=150,
        per_arm_calls=2,
        campaign_calls=3,
        per_arm_wall_clock_seconds=5,
        campaign_wall_clock_seconds=8,
    )
    ledger = CanaryCampaignLedger(
        accounting=admission.usage_accounting,
        hard_caps=caps,
    )
    ledger = ledger.reserve(
        execution_id="compact-1",
        arm=ProjectionPolicy.COMPACT,
        route=UsageRoute.PRIMARY,
        reserved_tokens=80,
    )
    ledger = ledger.reconcile(
        UsageObservation(
            execution_id="compact-1",
            route=UsageRoute.PRIMARY,
            account_id=admission.usage_accounting.primary_account_id,
            input_tokens=40,
            output_tokens=40,
            total_tokens=80,
        ),
        arm=ProjectionPolicy.COMPACT,
    )
    ledger = ledger.reserve(
        execution_id="current-1",
        arm=ProjectionPolicy.CURRENT,
        route=UsageRoute.FALLBACK,
        reserved_tokens=60,
    )
    with pytest.raises(AdmissionError, match="campaign token"):
        ledger.reserve(
            execution_id="compact-2",
            arm=ProjectionPolicy.COMPACT,
            route=UsageRoute.PRIMARY,
            reserved_tokens=20,
        )
    ledger = ledger.reserve(
        execution_id="compact-2",
        arm=ProjectionPolicy.COMPACT,
        route=UsageRoute.PRIMARY,
        reserved_tokens=10,
    )
    with pytest.raises(AdmissionError, match="campaign call"):
        ledger.reserve(
            execution_id="current-2",
            arm=ProjectionPolicy.CURRENT,
            route=UsageRoute.FALLBACK,
            reserved_tokens=1,
        )
    ledger.enforce_wall_clock(
        arm=ProjectionPolicy.COMPACT,
        arm_elapsed_seconds=4,
        campaign_elapsed_seconds=7,
    )
    with pytest.raises(AdmissionError, match="campaign wall"):
        ledger.enforce_wall_clock(
            arm=ProjectionPolicy.CURRENT,
            arm_elapsed_seconds=1,
            campaign_elapsed_seconds=9,
        )


def test_campaign_ledger_unknown_usage_and_arm_drift_fail_closed() -> None:
    admission = _contract()
    ledger = CanaryCampaignLedger(
        accounting=admission.usage_accounting,
        hard_caps=admission.hard_caps,
    ).reserve(
        execution_id="compact-unknown",
        arm=ProjectionPolicy.COMPACT,
        route=UsageRoute.PRIMARY,
        reserved_tokens=100,
    )
    with pytest.raises(AdmissionError, match="unknown usage"):
        ledger.reconcile(None, arm=ProjectionPolicy.COMPACT, execution_id="compact-unknown")
    with pytest.raises(AdmissionError, match="arm"):
        ledger.reconcile(
            UsageObservation(
                execution_id="compact-unknown",
                route=UsageRoute.PRIMARY,
                account_id=admission.usage_accounting.primary_account_id,
                input_tokens=50,
                output_tokens=50,
                total_tokens=100,
            ),
            arm=ProjectionPolicy.CURRENT,
        )


def test_contract_requires_compact_primary_and_current_fallback() -> None:
    raw = json.loads(DEFAULT_PROTOCOL_PATH.read_text(encoding="utf-8"))
    raw["fallback_projection"] = "compact"

    with pytest.raises(ValidationError, match="fallback must remain current"):
        SessionCompactCanaryAdmission.model_validate(raw)


def test_usage_accounts_must_be_independent() -> None:
    with pytest.raises(ValidationError, match="accounts must differ"):
        UsageAccountingContract(
            primary_account_id="shared",
            fallback_account_id="shared",
        )


def test_hard_caps_require_each_arm_to_fit_the_campaign() -> None:
    with pytest.raises(ValidationError, match="per-arm token cap"):
        CanaryHardCaps(
            per_arm_tokens=101,
            campaign_tokens=100,
            per_arm_calls=1,
            campaign_calls=2,
            per_arm_wall_clock_seconds=1,
            campaign_wall_clock_seconds=2,
        )

    with pytest.raises(ValidationError, match="per-arm call cap"):
        CanaryHardCaps(
            per_arm_tokens=10,
            campaign_tokens=100,
            per_arm_calls=3,
            campaign_calls=2,
            per_arm_wall_clock_seconds=1,
            campaign_wall_clock_seconds=2,
        )

    with pytest.raises(ValidationError, match="per-arm wall-clock cap"):
        CanaryHardCaps(
            per_arm_tokens=10,
            campaign_tokens=100,
            per_arm_calls=1,
            campaign_calls=2,
            per_arm_wall_clock_seconds=3,
            campaign_wall_clock_seconds=2,
        )


def test_primary_and_current_fallback_usage_are_summarized_separately() -> None:
    accounting = _contract().usage_accounting
    totals = summarize_usage(
        [
            UsageObservation(
                execution_id="primary-1",
                route="primary",
                account_id=accounting.primary_account_id,
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
            ),
            UsageObservation(
                execution_id="fallback-1",
                route="fallback",
                account_id=accounting.fallback_account_id,
                input_tokens=160,
                output_tokens=30,
                total_tokens=190,
            ),
        ],
        accounting,
    )

    assert totals["primary"] == {
        "requests": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }
    assert totals["fallback"] == {
        "requests": 1,
        "input_tokens": 160,
        "output_tokens": 30,
        "total_tokens": 190,
    }


def test_usage_cannot_cross_accounts_or_replay_execution_ids() -> None:
    accounting = _contract().usage_accounting
    wrong = UsageObservation(
        execution_id="request-1",
        route="fallback",
        account_id=accounting.primary_account_id,
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
    )
    with pytest.raises(AdmissionError, match="wrong account"):
        summarize_usage([wrong], accounting)
    valid = wrong.model_copy(update={"route": "primary"})
    with pytest.raises(AdmissionError, match="execution IDs must be unique"):
        summarize_usage([valid, valid], accounting)


def test_reservation_ledger_rejects_caps_before_transport_and_unknown_usage() -> None:
    admission = _contract()
    caps = CanaryHardCaps(
        per_arm_tokens=60,
        campaign_tokens=100,
        per_arm_calls=2,
        campaign_calls=4,
        per_arm_wall_clock_seconds=10,
        campaign_wall_clock_seconds=20,
    )
    ledger = CanaryUsageLedger(accounting=admission.usage_accounting, hard_caps=caps)

    with pytest.raises(AdmissionError, match="per-arm token hard cap"):
        ledger.reserve(execution_id="too-large", route=UsageRoute.PRIMARY, reserved_tokens=61)
    assert ledger.reservations == []

    ledger = ledger.reserve(execution_id="primary-1", route=UsageRoute.PRIMARY, reserved_tokens=60)
    with pytest.raises(AdmissionError, match="campaign token hard cap"):
        ledger.reserve(execution_id="fallback-1", route=UsageRoute.FALLBACK, reserved_tokens=50)
    with pytest.raises(AdmissionError, match="unknown usage stops"):
        ledger.reconcile(None, execution_id="primary-1")
    with pytest.raises(AdmissionError, match="wall-clock hard cap"):
        ledger.enforce_wall_clock(10.1)

    one_call_ledger = CanaryUsageLedger(
        accounting=admission.usage_accounting,
        hard_caps=CanaryHardCaps(
            per_arm_tokens=100,
            campaign_tokens=500,
            per_arm_calls=1,
            campaign_calls=2,
            per_arm_wall_clock_seconds=10,
            campaign_wall_clock_seconds=20,
        ),
    ).reserve(execution_id="only-call", route=UsageRoute.PRIMARY, reserved_tokens=10)
    with pytest.raises(AdmissionError, match="provider call hard cap"):
        one_call_ledger.reserve(
            execution_id="blocked-call",
            route=UsageRoute.FALLBACK,
            reserved_tokens=10,
        )


def test_reservation_ledger_reconciles_primary_and_fallback_without_pooling() -> None:
    admission = _contract()
    ledger = CanaryUsageLedger(
        accounting=admission.usage_accounting,
        hard_caps=admission.hard_caps,
    )
    ledger = ledger.reserve(execution_id="primary-1", route=UsageRoute.PRIMARY, reserved_tokens=100)
    ledger = ledger.reconcile(
        UsageObservation(
            execution_id="primary-1",
            route=UsageRoute.PRIMARY,
            account_id=admission.usage_accounting.primary_account_id,
            input_tokens=60,
            output_tokens=10,
            total_tokens=70,
        ),
        execution_id="primary-1",
    )
    ledger = ledger.reserve(execution_id="fallback-1", route=UsageRoute.FALLBACK, reserved_tokens=120)
    ledger = ledger.reconcile(
        UsageObservation(
            execution_id="fallback-1",
            route=UsageRoute.FALLBACK,
            account_id=admission.usage_accounting.fallback_account_id,
            input_tokens=80,
            output_tokens=20,
            total_tokens=100,
        ),
        execution_id="fallback-1",
    )

    totals = summarize_usage(ledger.observations, admission.usage_accounting)
    assert totals["primary"]["total_tokens"] == 70
    assert totals["fallback"]["total_tokens"] == 100


def test_preflight_rejects_fixture_hash_drift(tmp_path: Path) -> None:
    raw = json.loads(DEFAULT_PROTOCOL_PATH.read_text(encoding="utf-8"))
    source_root = DEFAULT_PROTOCOL_PATH.parent
    fixture = tmp_path / raw["hash_locks"]["fixture_path"]
    fixture.parent.mkdir(parents=True)
    fixture.write_text("{}", encoding="utf-8")
    stage9 = tmp_path / raw["hash_locks"]["stage9_result_path"]
    stage9.write_bytes((source_root / raw["hash_locks"]["stage9_result_path"]).read_bytes())
    protocol = tmp_path / DEFAULT_PROTOCOL_PATH.name
    protocol.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(AdmissionError, match="fixture hash mismatch"):
        run_no_provider_preflight(protocol)
