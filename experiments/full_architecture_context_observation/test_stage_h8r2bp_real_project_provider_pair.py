from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from experiments.full_architecture_context_observation.stage_h8r2bp_real_project_provider_pair import (
    EXACT_VALIDATION_COMMAND,
    TARGET_FILE,
    build_mock_real_project_provider_pair,
    canonical_hash,
    prepare_pair_context_for_test,
    run_real_project_provider_pair,
    validate_real_project_provider_pair_receipt,
)


_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}(?![A-Za-z0-9])")


def test_mock_real_project_provider_pair_passes_both_arms(tmp_path: Path) -> None:
    result = build_mock_real_project_provider_pair(output_root=tmp_path / "bp")

    assert result["status"] == "passed"
    assert result["arm_count"] == 2
    assert result["side_effects"]["provider_transport_attempted"] is False
    assert result["side_effects"]["mock_completion_calls"] == 8
    assert result["invariants"]["both_arms_passed_mutation_gate"] is True
    assert result["invariants"]["both_arms_passed_request_shape"] is True
    assert result["invariants"]["provider_usage_complete"] is True
    assert result["aggregate_usage"]["prompt_token_delta"] > 0
    assert result["aggregate_usage"]["total_token_delta"] > 0

    for arm in result["arms"]:
        assert arm["status"] == "passed"
        assert arm["mutation_gate"]["changed_source_files"] == [TARGET_FILE]
        assert arm["mutation_gate"]["provider_exact_validation_observed"] is True
        assert arm["mutation_gate"]["independent_exact_validation_exit_zero"] is True
        assert arm["request_shape_gate"]["all_tool_phase_requests_required"] is True
        assert arm["request_shape_gate"]["finalization_without_tool_choice"] is True
        assert arm["request_shape_gate"]["finalization_without_tools"] is True

    assert validate_real_project_provider_pair_receipt(result)["status"] == "passed"


def test_real_project_provider_pair_blocks_without_settings(tmp_path: Path) -> None:
    def missing_settings():
        raise ValueError("missing credential")

    result = run_real_project_provider_pair(
        output_root=tmp_path / "bp-blocked",
        source_root=Path.cwd(),
        settings_factory=missing_settings,
        provider_transport=True,
    )

    assert result["status"] == "blocked"
    assert result["setup_reasons"] == ["settings_unavailable:ValueError"]
    assert result["side_effects"]["provider_calls"] == 0
    assert result["side_effects"]["project_mutations"] == 0
    assert result["secret_handling"]["credential_serialized"] is False
    assert validate_real_project_provider_pair_receipt(result)["status"] == "blocked"


def test_real_project_provider_pair_receipt_is_body_and_secret_free(tmp_path: Path) -> None:
    result = build_mock_real_project_provider_pair(output_root=tmp_path / "bp-body")
    encoded = json.dumps(result, ensure_ascii=False)

    for token in (
        "generated_unit",
        "ProviderToolRoundTripRunner.__name__",
        "def test_h8r2bo",
        "stdout",
        "stderr",
        "\"content\"",
        "raw_response",
    ):
        assert token not in encoded
    assert not _SECRET_RE.search(encoded)

    result["arms"][0]["requests"][0]["content"] = "must not persist"
    result["receipt_hash"] = canonical_hash(
        {key: value for key, value in result.items() if key != "receipt_hash"}
    )
    with pytest.raises(ValueError, match="prompt/body"):
        validate_real_project_provider_pair_receipt(result)


def test_real_project_provider_pair_context_preflight_and_simulation(tmp_path: Path) -> None:
    prepared = prepare_pair_context_for_test(tmp_path / "context")

    assert not prepared.setup_reasons
    assert prepared.binding is not None
    assert prepared.preflight and prepared.preflight["status"] == "passed"
    assert prepared.simulation and prepared.simulation["status"] == "passed"
    assert prepared.raw_projection_tokens is not None
    assert prepared.reusable_projection_tokens is not None
    assert prepared.raw_projection_tokens > prepared.reusable_projection_tokens
    assert any(
        getattr(candidate.retention, "value", candidate.retention) == "required"
        for candidate in prepared.raw_candidates
    )
    assert len(prepared.reusable_candidates) > len(prepared.raw_candidates)


def test_real_project_provider_pair_validation_hash_is_stable(tmp_path: Path) -> None:
    result = build_mock_real_project_provider_pair(output_root=tmp_path / "bp-validation")

    assert result["validation_command_sha256"] == canonical_hash(EXACT_VALIDATION_COMMAND)
    assert result["tool_allowlist"] == [
        "file_reader",
        "file_patch_writer",
        "command_executor",
    ]
