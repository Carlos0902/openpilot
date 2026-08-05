from __future__ import annotations

import hashlib

import pytest

from mini_swe_active_iteration.core_benefit_screen import (
    CoreBenefitScreenCandidate,
    CoreBenefitScreenMechanismReview,
    resolve_screen_candidate,
)
from mini_swe_active_iteration.generate_core_benefit_screen import _eligible_candidates


def _candidate(index: int, *, agreement: bool):
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    stratum = "localization" if agreement else "single_file"
    candidate = CoreBenefitScreenCandidate(
        instance_id=f"repo-{index}__issue-{index}",
        repository=f"repo-{index}",
        selection_rank=index + 1,
        nonexecution_receipt_sha256=digest(f"n-{index}"),
        first_review_sha256=digest(f"f-{index}"),
        second_review_sha256=digest(f"s-{index}"),
        first_reviewer_identity_sha256=digest("first"),
        second_reviewer_identity_sha256=digest("second"),
        first_proposed_stratum="localization",
        second_proposed_stratum=stratum,
        blind_to_future_arm_outcomes=True,
        agent_arm_outcomes_available=False,
    )
    if not agreement:
        return candidate
    mechanism = CoreBenefitScreenMechanismReview(
        schema_version="1.0",
        review_id=f"mechanism-{index}",
        instance_id=candidate.instance_id,
        selection_rank=candidate.selection_rank,
        first_review_sha256=candidate.first_review_sha256,
        second_review_sha256=candidate.second_review_sha256,
        adjudication_sha256=None,
        reviewer_identity_sha256=digest(f"mechanism-{index}"),
        diagnostic_measurement=True,
        post_action_validation=False,
        recovery_or_safe_stop=False,
        private_rationale_sha256=digest(f"rationale-{index}"),
        blind_to_future_arm_outcomes=True,
        agent_arm_outcomes_available=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )
    return resolve_screen_candidate(
        candidate=candidate,
        adjudication=None,
        adjudication_sha256=None,
        mechanism_review=mechanism,
        mechanism_review_sha256=digest(f"mechanism-artifact-{index}"),
    )


def test_only_resolved_mechanism_eligible_candidates_enter_the_pool() -> None:
    candidates = [_candidate(index, agreement=True) for index in range(12)]
    eligible = _eligible_candidates(candidates)

    assert len(eligible) == 12
    assert all(item.diagnostic_measurement for item in eligible)


def test_too_few_resolved_candidates_fail_closed() -> None:
    candidates = [_candidate(index, agreement=True) for index in range(11)]

    with pytest.raises(ValueError, match="resolved and mechanism eligible"):
        _eligible_candidates(candidates)
