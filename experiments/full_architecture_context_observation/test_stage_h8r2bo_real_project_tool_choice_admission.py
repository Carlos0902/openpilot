from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from experiments.full_architecture_context_observation.stage_h8r2bo_real_project_tool_choice_admission import (
    EXACT_VALIDATION_COMMAND,
    TARGET_FILE,
    ReplayOptions,
    canonical_hash,
    run_real_project_tool_choice_admission_replay,
    validate_real_project_tool_choice_admission_receipt,
)


_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}(?![A-Za-z0-9])")


def test_real_project_tool_choice_replay_passes_scope_and_request_shape(
    tmp_path: Path,
) -> None:
    result = run_real_project_tool_choice_admission_replay(
        output_root=tmp_path / "bo",
        source_root=Path.cwd(),
    )

    assert result["status"] == "passed"
    assert result["side_effects"]["provider_transport_attempted"] is False
    assert result["side_effects"]["mock_completion_calls"] == 4
    assert result["mutation_gate"]["changed_source_files"] == [TARGET_FILE]
    assert result["mutation_gate"]["provider_exact_validation_observed"] is True
    assert result["mutation_gate"]["provider_validation_exit_zero"] is True
    assert result["mutation_gate"]["independent_exact_validation_exit_zero"] is True
    assert result["mutation_gate"]["suspicious_success"] is False
    assert result["request_shape_gate"]["all_tool_phase_requests_required"] is True
    assert result["request_shape_gate"]["finalization_without_tool_choice"] is True
    assert result["request_shape_gate"]["finalization_without_tools"] is True

    requests = result["requests"]
    assert [request["tool_choice"] for request in requests[:3]] == [
        "required",
        "required",
        "required",
    ]
    assert requests[-1]["tool_choice"] is None
    assert requests[-1]["tool_names"] == []
    assert validate_real_project_tool_choice_admission_receipt(result)["status"] == "passed"


def test_real_project_tool_choice_replay_rejects_finalization_tool_call(
    tmp_path: Path,
) -> None:
    result = run_real_project_tool_choice_admission_replay(
        output_root=tmp_path / "bo-finalization-tool",
        source_root=Path.cwd(),
        options=ReplayOptions(finalization_tool_call=True),
    )

    assert result["status"] == "needs_followup"
    assert result["request_shape_gate"]["finalization_without_tools"] is True
    assert result["execution"]["task_status"] != "completed"
    assert result["execution"]["task_error_message"] == "ProviderToolFinalizationToolCall"
    assert validate_real_project_tool_choice_admission_receipt(result)["status"] == "needs_followup"


def test_real_project_tool_choice_replay_receipt_is_body_and_secret_free(
    tmp_path: Path,
) -> None:
    result = run_real_project_tool_choice_admission_replay(
        output_root=tmp_path / "bo-body",
        source_root=Path.cwd(),
    )
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

    result["requests"][0]["content"] = "must not persist"
    result["receipt_hash"] = canonical_hash(
        {key: value for key, value in result.items() if key != "receipt_hash"}
    )
    with pytest.raises(ValueError, match="prompt/body"):
        validate_real_project_tool_choice_admission_receipt(result)


def test_real_project_tool_choice_replay_validation_hash_is_stable(
    tmp_path: Path,
) -> None:
    result = run_real_project_tool_choice_admission_replay(
        output_root=tmp_path / "bo-validation",
        source_root=Path.cwd(),
    )

    assert result["validation_command_sha256"] == canonical_hash(EXACT_VALIDATION_COMMAND)
    assert result["tool_allowlist"] == [
        "file_reader",
        "file_patch_writer",
        "command_executor",
    ]
