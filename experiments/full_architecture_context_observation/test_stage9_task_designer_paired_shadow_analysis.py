from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

import stage9_task_designer_paired_shadow as paired
import stage9_task_designer_paired_shadow_analysis as analysis


QUALITY_CHECKS = (
    "run_completed",
    "core_success",
    "verification_passed",
    "improvement_succeeded",
    "improvement_count",
    "mutation_scope",
    "required_commands",
    "unchanged_file",
    "fixed_decomposition",
)


def _completed_campaign() -> dict:
    records = []
    for item in paired.build_schedule():
        ordinal = int(item["ordinal"])
        role_inputs = {
            "current": 1_000 + ordinal,
            "compact": 600 + ordinal,
        }
        records.append(
            {
                **deepcopy(item),
                "stop_reasons": [],
                "quality_gate": {
                    "passed": True,
                    "signature": f"sha256:quality-{ordinal}",
                    "checks": {check: True for check in QUALITY_CHECKS},
                },
                "paired_task_designer": {
                    "roles": {
                        role: {
                            "role": role,
                            "projection_policy": item[f"{role}_policy"],
                            "provider_input_tokens": role_inputs[
                                item[f"{role}_policy"]
                            ],
                        }
                        for role in ("production", "shadow")
                    }
                },
            }
        )
    return {
        "campaign_id": paired.load_protocol()["campaign_id"],
        "status": "completed",
        "stop_reasons": [],
        "spend": {"hard_failures": []},
        "records": records,
    }


def test_analyze_completed_campaign_aggregates_usage_by_role_projection_policy() -> None:
    result = analysis.analyze_completed_campaign(_completed_campaign())

    assert result["eligible"] is True
    assert result["recommendation"] == "requires_separate_review"
    assert result["production_policy_counts"] == {"current": 3, "compact": 3}
    assert result["observation_counts"] == {"current": 6, "compact": 6}
    assert result["primary_metrics"]["provider_input_tokens"] == {
        "current": 6_021,
        "compact": 3_621,
        "absolute_reduction": 2_400,
        "reduction_fraction": pytest.approx(2_400 / 6_021),
    }
    assert len(result["primary_metrics"]["paired_reductions"]) == 6
    assert result["quality"]["all_passed"] is True
    assert [item["signature"] for item in result["quality"]["signatures"]] == [
        f"sha256:quality-{ordinal}" for ordinal in range(1, 7)
    ]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda state: state.update(status="sentinel_completed"), "campaign_not_completed"),
        (lambda state: state["stop_reasons"].append("stopped"), "campaign_has_stop_reasons"),
        (lambda state: state["spend"]["hard_failures"].append("hard"), "campaign_has_hard_failures"),
        (lambda state: state["records"].pop(), "record_count_invalid"),
        (lambda state: state["records"].append(deepcopy(state["records"][-1])), "record_count_invalid"),
        (lambda state: state["records"].reverse(), "frozen_schedule_mismatch"),
        (lambda state: state["records"][0]["stop_reasons"].append("arm"), "arm_has_stop_reasons"),
    ],
)
def test_analyze_completed_campaign_rejects_incomplete_or_stopped_campaigns(
    mutate, reason: str
) -> None:
    state = _completed_campaign()
    mutate(state)

    with pytest.raises(analysis.PairedShadowAnalysisError, match=reason):
        analysis.analyze_completed_campaign(state)


def test_analyze_completed_campaign_rejects_non_three_three_production_balance() -> None:
    state = _completed_campaign()
    state["records"][1]["production_policy"] = "current"
    state["records"][1]["shadow_policy"] = "compact"
    state["records"][1]["paired_task_designer"]["roles"]["production"][
        "projection_policy"
    ] = "current"
    state["records"][1]["paired_task_designer"]["roles"]["shadow"][
        "projection_policy"
    ] = "compact"

    with pytest.raises(analysis.PairedShadowAnalysisError, match="frozen_schedule_mismatch"):
        analysis.analyze_completed_campaign(state)


def test_analyze_completed_campaign_rejects_role_policy_mismatch() -> None:
    state = _completed_campaign()
    state["records"][0]["paired_task_designer"]["roles"]["shadow"][
        "projection_policy"
    ] = "current"

    with pytest.raises(analysis.PairedShadowAnalysisError, match="role_policy_mismatch"):
        analysis.analyze_completed_campaign(state)


@pytest.mark.parametrize("value", [None, 0, -1, True])
def test_analyze_completed_campaign_requires_positive_integer_input_usage(value) -> None:
    state = _completed_campaign()
    state["records"][0]["paired_task_designer"]["roles"]["production"][
        "provider_input_tokens"
    ] = value

    with pytest.raises(analysis.PairedShadowAnalysisError, match="input_usage_invalid"):
        analysis.analyze_completed_campaign(state)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda gate: gate.update(passed=False),
        lambda gate: gate.update(signature=""),
        lambda gate: gate["checks"].update(core_success=False),
        lambda gate: gate["checks"].pop("core_success"),
    ],
)
def test_analyze_completed_campaign_requires_complete_quality_evidence(mutate) -> None:
    state = _completed_campaign()
    mutate(state["records"][2]["quality_gate"])

    with pytest.raises(analysis.PairedShadowAnalysisError, match="quality_gate_invalid"):
        analysis.analyze_completed_campaign(state)


def test_analyze_completed_campaign_does_not_require_cross_run_signature_equality() -> None:
    state = _completed_campaign()

    result = analysis.analyze_completed_campaign(state)

    assert result["eligible"] is True
    assert len({item["signature"] for item in result["quality"]["signatures"]}) == 6


def test_analyze_completed_campaign_file_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "campaign_state.json"
    path.write_text(json.dumps(_completed_campaign()), encoding="utf-8")
    before = path.read_bytes()

    result = analysis.analyze_completed_campaign_file(path)

    assert result["eligible"] is True
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
