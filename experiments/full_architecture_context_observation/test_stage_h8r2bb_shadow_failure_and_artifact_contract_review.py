from __future__ import annotations

from pathlib import Path

from experiments.full_architecture_context_observation.stage_h8r2bb_shadow_failure_and_artifact_contract_review import (
    build_h8r2bb_review,
    validate_h8r2bb_receipt,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_h8r2bb_review_passes_real_builder_and_artifact_gates(tmp_path) -> None:
    receipt = build_h8r2bb_review(repo_root=REPO_ROOT, output_root=tmp_path / "bb")
    assert receipt["status"] == "passed"
    validate_h8r2bb_receipt(receipt)
    assert receipt["gates"][0]["strict_context_source_error"] is True
    assert receipt["gates"][1]["safe_checksum_substitution"] is True


def test_h8r2bb_receipt_validator_rejects_boundary_drift(tmp_path) -> None:
    receipt = build_h8r2bb_review(repo_root=REPO_ROOT, output_root=tmp_path / "bb")
    tampered = {
        **receipt,
        "review_decision": {
            **receipt["review_decision"],
            "prompt_use_authorized": True,
        },
    }
    # The receipt hash is intentionally stale after tampering.
    try:
        validate_h8r2bb_receipt(tampered)
    except ValueError:
        pass
    else:
        raise AssertionError("tampered H8-R2BB receipt must fail closed")
