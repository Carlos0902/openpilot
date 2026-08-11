from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.provider_tool_roundtrip_contracts import (
    MAX_PROVIDER_EVIDENCE_PATHS,
    MAX_PROVIDER_ROUND_TRIP_ROUNDS,
    ProviderDeclaredReadWindow,
    ProviderPageReadCount,
    ProviderToolAttempt,
    ProviderToolEvidenceCoverage,
)


def _attempt(**updates) -> ProviderToolAttempt:
    values = {
        "signature": "a" * 64,
        "tool_name": "file_reader",
        "provider_call_id": "provider-1",
        "round_index": 1,
        "success": True,
    }
    values.update(updates)
    return ProviderToolAttempt(**values)


def test_attempt_contract_preserves_success_and_duplicate_failure() -> None:
    success = _attempt()
    duplicate = _attempt(
        provider_call_id="provider-2",
        success=False,
        error_type="ProviderToolDuplicateAttempt",
        duplicate_of="provider-1",
    )

    assert success.error_type is None
    assert duplicate.duplicate_of == "provider-1"
    assert ProviderToolAttempt.model_validate_json(
        duplicate.model_dump_json()
    ) == duplicate


@pytest.mark.parametrize(
    "updates",
    [
        {"success": True, "error_type": "Unexpected"},
        {"success": False},
        {
            "success": False,
            "error_type": "Duplicate",
            "duplicate_of": "provider-1",
        },
        {"signature": "not-a-sha256"},
        {"round_index": 0},
        {"round_index": MAX_PROVIDER_ROUND_TRIP_ROUNDS + 1},
        {"tool_name": " "},
        {"hidden_control": True},
    ],
)
def test_attempt_contract_rejects_contradictory_facts(updates) -> None:
    with pytest.raises(ValidationError):
        _attempt(**updates)


def test_evidence_coverage_serializes_owned_values() -> None:
    coverage = ProviderToolEvidenceCoverage(
        completed_read_paths=("/project/app.py",),
        completed_declared_windows=(
            ProviderDeclaredReadWindow(
                file_path="/project/app.py",
                read_mode="adaptive",
                offset=0,
                max_lines=40,
            ),
        ),
        bounded_projection_paths=("/project/app.py",),
        page_reads_by_path=(
            ProviderPageReadCount(file_path="/project/app.py", count=3),
        ),
        page_cap_paths=("/project/app.py",),
        page_read_cap=3,
        observed_evidence_keys=("file:/project/app.py",),
        duplicate_only_rounds=1,
        finalization_requests=1,
    )

    payload = coverage.model_dump(mode="json")

    assert payload["completed_declared_windows"][0]["max_lines"] == 40
    assert payload["page_reads_by_path"] == [
        {"file_path": "/project/app.py", "count": 3}
    ]
    assert ProviderToolEvidenceCoverage.model_validate(payload) == coverage


@pytest.mark.parametrize(
    "updates",
    [
        {"completed_read_paths": ("app.py", "app.py")},
        {"completed_read_paths": (" ",)},
        {
            "completed_declared_windows": (
                ProviderDeclaredReadWindow(
                    file_path="app.py",
                    read_mode="adaptive",
                    offset=0,
                    max_lines=20,
                ),
            )
            * 2,
        },
        {
            "page_reads_by_path": (
                ProviderPageReadCount(file_path="app.py", count=2),
            ),
            "page_cap_paths": ("missing.py",),
            "page_read_cap": 2,
        },
        {
            "page_reads_by_path": (
                ProviderPageReadCount(file_path="app.py", count=1),
            ),
            "page_cap_paths": ("app.py",),
            "page_read_cap": 2,
        },
    ],
)
def test_evidence_coverage_rejects_inconsistent_sets(updates) -> None:
    with pytest.raises(ValidationError):
        ProviderToolEvidenceCoverage(**updates)


def test_evidence_path_limit_accepts_boundary_and_rejects_overflow() -> None:
    paths = tuple(
        f"/project/file-{index}.py"
        for index in range(MAX_PROVIDER_EVIDENCE_PATHS)
    )

    assert ProviderToolEvidenceCoverage(
        completed_read_paths=paths
    ).completed_read_paths == paths
    with pytest.raises(ValidationError):
        ProviderToolEvidenceCoverage(
            completed_read_paths=paths + ("/project/overflow.py",)
        )
