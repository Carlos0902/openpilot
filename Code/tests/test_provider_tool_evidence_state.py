from __future__ import annotations

import pytest

from core.provider_tool_evidence_state import (
    ProviderToolEvidenceState,
    ProviderToolEvidenceStateError,
)
from core.provider_tool_roundtrip_contracts import (
    MAX_PROVIDER_EVIDENCE_PATHS,
    MAX_PROVIDER_ROUND_TRIP_ROUNDS,
    MAX_PROVIDER_TOOL_ATTEMPTS,
    ProviderDeclaredReadWindow,
)


def test_empty_state_projects_bounded_default_coverage() -> None:
    state = ProviderToolEvidenceState(page_read_cap=3)

    coverage = state.coverage()

    assert coverage.completed_read_paths == ()
    assert coverage.page_read_cap == 3
    assert coverage.duplicate_only_rounds == 0
    assert coverage.finalization_requests == 0


def test_state_records_completed_read_projection_and_evidence(tmp_path) -> None:
    state = ProviderToolEvidenceState(project_path=str(tmp_path))

    assert state.record_completed_read(
        "app.py",
        projection="bounded_preview",
    ) is True
    assert state.record_evidence_key("file:app.py:sha256") is True
    assert state.record_evidence_key("file:app.py:sha256") is False

    coverage = state.coverage()
    canonical = str((tmp_path / "app.py").resolve())
    assert coverage.completed_read_paths == (canonical,)
    assert coverage.bounded_projection_paths == (canonical,)
    assert coverage.observed_evidence_keys == ("file:app.py:sha256",)


def test_declared_window_becomes_completed_bounded_evidence(tmp_path) -> None:
    state = ProviderToolEvidenceState(project_path=str(tmp_path))
    window = ProviderDeclaredReadWindow(
        file_path="app.py",
        read_mode="adaptive",
        offset=40,
        max_lines=20,
    )

    assert state.record_declared_window(window) is True
    assert state.record_declared_window(window) is False

    coverage = state.coverage()
    canonical = str((tmp_path / "app.py").resolve())
    assert coverage.completed_read_paths == (canonical,)
    assert coverage.completed_declared_windows[0].file_path == canonical
    assert coverage.bounded_projection_paths == (canonical,)


def test_page_reads_stop_at_cap_and_derive_cap_path(tmp_path) -> None:
    state = ProviderToolEvidenceState(
        project_path=str(tmp_path),
        page_read_cap=2,
    )

    assert state.record_page_read("app.py") == 1
    assert state.record_page_read("app.py") == 2
    with pytest.raises(ProviderToolEvidenceStateError):
        state.record_page_read("app.py")

    coverage = state.coverage()
    canonical = str((tmp_path / "app.py").resolve())
    assert coverage.page_reads_by_path[0].count == 2
    assert coverage.page_cap_paths == (canonical,)


def test_source_path_limit_is_atomic(tmp_path) -> None:
    state = ProviderToolEvidenceState(project_path=str(tmp_path))
    for index in range(MAX_PROVIDER_EVIDENCE_PATHS):
        state.record_completed_read(f"file-{index}.py")

    before = state.coverage()
    with pytest.raises(ProviderToolEvidenceStateError):
        state.record_page_read("overflow.py")

    assert state.coverage() == before


def test_project_root_containment_failure_is_atomic(tmp_path) -> None:
    project = tmp_path / "project"
    state = ProviderToolEvidenceState(project_path=str(project))
    before = state.coverage()

    with pytest.raises(ProviderToolEvidenceStateError):
        state.record_completed_read("../outside.py")

    assert state.coverage() == before


def test_evidence_key_limit_accepts_boundary_and_rejects_overflow() -> None:
    state = ProviderToolEvidenceState()
    for index in range(MAX_PROVIDER_TOOL_ATTEMPTS):
        assert state.record_evidence_key(f"evidence-{index}") is True

    before = state.coverage()
    with pytest.raises(ProviderToolEvidenceStateError):
        state.record_evidence_key("overflow")

    assert state.coverage() == before


def test_round_observations_are_idempotent_and_bounded() -> None:
    state = ProviderToolEvidenceState()

    assert state.record_duplicate_only_round(1) is True
    assert state.record_duplicate_only_round(1) is False
    assert state.record_finalization_request(
        MAX_PROVIDER_ROUND_TRIP_ROUNDS
    ) is True
    assert state.record_finalization_request(
        MAX_PROVIDER_ROUND_TRIP_ROUNDS
    ) is False

    coverage = state.coverage()
    assert coverage.duplicate_only_rounds == 1
    assert coverage.finalization_requests == 1


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ProviderToolEvidenceState(page_read_cap=0),
        lambda: ProviderToolEvidenceState(page_read_cap=True),
        lambda: ProviderToolEvidenceState(project_path=""),
        lambda: ProviderToolEvidenceState(project_path=1),
    ],
)
def test_state_rejects_invalid_constructor_controls(factory) -> None:
    with pytest.raises(ProviderToolEvidenceStateError):
        factory()


@pytest.mark.parametrize(
    "operation",
    [
        lambda state: state.record_completed_read(""),
        lambda state: state.record_completed_read("app.py", projection="full"),
        lambda state: state.record_declared_window(object()),
        lambda state: state.record_page_read(""),
        lambda state: state.record_evidence_key(""),
        lambda state: state.record_evidence_key("x" * 513),
        lambda state: state.record_duplicate_only_round(0),
        lambda state: state.record_finalization_request(True),
    ],
)
def test_state_rejects_invalid_observations(operation) -> None:
    with pytest.raises(ProviderToolEvidenceStateError):
        operation(ProviderToolEvidenceState())


def test_state_does_not_create_or_read_observed_paths(tmp_path) -> None:
    state = ProviderToolEvidenceState(project_path=str(tmp_path))
    state.record_completed_read("missing.py")

    assert not (tmp_path / "missing.py").exists()
