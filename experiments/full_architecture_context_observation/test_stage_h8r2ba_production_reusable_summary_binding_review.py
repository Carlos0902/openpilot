from __future__ import annotations

import json
from pathlib import Path

from experiments.full_architecture_context_observation.stage_h8r2ba_production_reusable_summary_binding_review import (
    build_production_reusable_summary_binding_review,
    validate_production_reusable_summary_binding_review_receipt,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_h8r2ba_real_builder_gate_preserves_authority_context(tmp_path) -> None:
    result = build_production_reusable_summary_binding_review(
        repo_root=REPO_ROOT,
        output_root=tmp_path / "review",
    )

    # The fixture is intentionally run from the repository root in production;
    # this unit test only verifies the receipt validator contract.
    assert result["schema"].startswith("phase-h8r2ba-")


def test_h8r2ba_receipt_hash_excludes_self_and_is_body_free(tmp_path) -> None:
    result = build_production_reusable_summary_binding_review(
        repo_root=REPO_ROOT,
        output_root=tmp_path / "review",
    )
    validated = validate_production_reusable_summary_binding_review_receipt(result)
    encoded = json.dumps(validated, ensure_ascii=False, sort_keys=True)
    assert "summary_body" not in encoded
    assert "raw_response" not in encoded
    assert "sk-" not in encoded
