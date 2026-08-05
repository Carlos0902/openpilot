from __future__ import annotations

import hashlib

import pytest

from mini_swe_active_iteration.acquisition import (
    build_candidate_stratum_review_decision,
    write_candidate_stratum_review_decision,
)
from mini_swe_active_iteration.core_benefit_screen import (
    CoreBenefitScreenCandidate,
    CoreBenefitScreenAdjudication,
    CoreBenefitScreenMechanismReview,
    audit_v4_screen_candidate,
    build_core_benefit_screen_pool,
    build_stage_a_manifest,
    resolve_screen_candidate,
)
from pathlib import Path


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _candidate(
    index: int,
    *,
    first_reviewer: str = "first-reviewer",
    second_reviewer: str = "second-reviewer",
    first_stratum: str = "measurement_disambiguation",
    second_stratum: str = "measurement_disambiguation",
) -> CoreBenefitScreenCandidate:
    repository = f"repo-{index // 3}"
    return CoreBenefitScreenCandidate(
        instance_id=f"{repository}__issue-{index}",
        repository=repository,
        selection_rank=index + 1,
        nonexecution_receipt_sha256=_digest(f"nonexecution-{index}"),
        first_review_sha256=_digest(f"first-review-{index}"),
        second_review_sha256=_digest(f"second-review-{index}"),
        first_reviewer_identity_sha256=_digest(first_reviewer),
        second_reviewer_identity_sha256=_digest(second_reviewer),
        first_proposed_stratum=first_stratum,
        second_proposed_stratum=second_stratum,
        blind_to_future_arm_outcomes=True,
        agent_arm_outcomes_available=False,
    )


def _eligible(index: int):
    candidate = _candidate(index)
    mechanism = CoreBenefitScreenMechanismReview(
        schema_version="1.0",
        review_id=f"mechanism-{index}",
        instance_id=candidate.instance_id,
        selection_rank=candidate.selection_rank,
        first_review_sha256=candidate.first_review_sha256,
        second_review_sha256=candidate.second_review_sha256,
        adjudication_sha256=None,
        reviewer_identity_sha256=_digest(f"mechanism-reviewer-{index}"),
        diagnostic_measurement=True,
        post_action_validation=False,
        recovery_or_safe_stop=False,
        private_rationale_sha256=_digest(f"mechanism-rationale-{index}"),
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
        mechanism_review_sha256=_digest(f"mechanism-review-{index}"),
    )


def test_pool_rejects_nonindependent_or_unresolved_reviews() -> None:
    same_reviewer = _candidate(
        0,
        first_reviewer="same",
        second_reviewer="same",
    )
    disagreement = _candidate(
        1,
        second_stratum="single_file",
    )

    mechanism = CoreBenefitScreenMechanismReview(
        schema_version="1.0",
        review_id="mechanism",
        instance_id=same_reviewer.instance_id,
        selection_rank=same_reviewer.selection_rank,
        first_review_sha256=same_reviewer.first_review_sha256,
        second_review_sha256=same_reviewer.second_review_sha256,
        adjudication_sha256=None,
        reviewer_identity_sha256=_digest("mechanism"),
        diagnostic_measurement=True,
        post_action_validation=False,
        recovery_or_safe_stop=False,
        private_rationale_sha256=_digest("rationale"),
        blind_to_future_arm_outcomes=True,
        agent_arm_outcomes_available=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )
    with pytest.raises(ValueError, match="independence"):
        resolve_screen_candidate(
            candidate=same_reviewer,
            adjudication=None,
            adjudication_sha256=None,
            mechanism_review=mechanism,
            mechanism_review_sha256=_digest("mechanism-review"),
        )
    with pytest.raises(ValueError, match="adjudication"):
        resolve_screen_candidate(
            candidate=disagreement,
            adjudication=None,
            adjudication_sha256=None,
            mechanism_review=CoreBenefitScreenMechanismReview(
                schema_version="1.0",
                review_id="mechanism-disagreement",
                instance_id=disagreement.instance_id,
                selection_rank=disagreement.selection_rank,
                first_review_sha256=disagreement.first_review_sha256,
                second_review_sha256=disagreement.second_review_sha256,
                adjudication_sha256=None,
                reviewer_identity_sha256=_digest("mechanism-disagreement"),
                diagnostic_measurement=True,
                post_action_validation=False,
                recovery_or_safe_stop=False,
                private_rationale_sha256=_digest("rationale-disagreement"),
                blind_to_future_arm_outcomes=True,
                agent_arm_outcomes_available=False,
                provider_execution_authorized=False,
                production_execution_authorized=False,
            ),
            mechanism_review_sha256=_digest("mechanism-review-disagreement"),
        )


def test_adjudication_and_mechanism_review_resolve_a_disagreement() -> None:
    candidate = _candidate(
        1,
        first_stratum="localization",
        second_stratum="single_file",
    )
    adjudication = CoreBenefitScreenAdjudication(
        schema_version="1.0",
        adjudication_id="adjudication-1",
        instance_id=candidate.instance_id,
        selection_rank=candidate.selection_rank,
        first_review_sha256=candidate.first_review_sha256,
        second_review_sha256=candidate.second_review_sha256,
        adjudicator_identity_sha256=_digest("adjudicator"),
        resolved_primary_stratum="localization",
        private_rationale_sha256=_digest("adjudication-rationale"),
        blind_to_future_arm_outcomes=True,
        agent_arm_outcomes_available=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )
    adjudication_sha256 = _digest("adjudication-1")
    mechanism = CoreBenefitScreenMechanismReview(
        schema_version="1.0",
        review_id="mechanism-1",
        instance_id=candidate.instance_id,
        selection_rank=candidate.selection_rank,
        first_review_sha256=candidate.first_review_sha256,
        second_review_sha256=candidate.second_review_sha256,
        adjudication_sha256=adjudication_sha256,
        reviewer_identity_sha256=_digest("mechanism-reviewer"),
        diagnostic_measurement=False,
        post_action_validation=True,
        recovery_or_safe_stop=False,
        private_rationale_sha256=_digest("mechanism-rationale"),
        blind_to_future_arm_outcomes=True,
        agent_arm_outcomes_available=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )

    eligible = resolve_screen_candidate(
        candidate=candidate,
        adjudication=adjudication,
        adjudication_sha256=adjudication_sha256,
        mechanism_review=mechanism,
        mechanism_review_sha256=_digest("mechanism-1"),
    )

    assert eligible.primary_stratum == "localization"
    assert eligible.adjudication_sha256 == adjudication_sha256
    assert eligible.post_action_validation is True


def test_mechanism_review_may_reuse_the_independent_second_reviewer() -> None:
    candidate = _candidate(2)
    mechanism = CoreBenefitScreenMechanismReview(
        schema_version="1.0",
        review_id="mechanism-second-reviewer",
        instance_id=candidate.instance_id,
        selection_rank=candidate.selection_rank,
        first_review_sha256=candidate.first_review_sha256,
        second_review_sha256=candidate.second_review_sha256,
        adjudication_sha256=None,
        reviewer_identity_sha256=candidate.second_reviewer_identity_sha256,
        diagnostic_measurement=False,
        post_action_validation=False,
        recovery_or_safe_stop=True,
        private_rationale_sha256=_digest("mechanism-rationale-second"),
        blind_to_future_arm_outcomes=True,
        agent_arm_outcomes_available=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )

    eligible = resolve_screen_candidate(
        candidate=candidate,
        adjudication=None,
        adjudication_sha256=None,
        mechanism_review=mechanism,
        mechanism_review_sha256=_digest("mechanism-second"),
    )

    assert eligible.recovery_or_safe_stop is True


def test_pool_and_stage_a_manifest_are_deterministic_and_outcome_free() -> None:
    pool = build_core_benefit_screen_pool(
        screen_protocol_sha256=_digest("screen"),
        acquisition_rules_sha256=_digest("rules"),
        candidates=tuple(_eligible(index) for index in range(12)),
    )

    stage_a = build_stage_a_manifest(
        pool=pool,
        selection_seed=20260801,
        maximum_tasks_per_repository=4,
    )

    assert pool.eligible_candidate_count == 12
    assert pool.provider_execution_authorized is False
    assert pool.task_outcomes_generated is False
    assert stage_a.stage == "A"
    assert len(stage_a.entries) == 12
    assert stage_a.provider_execution_authorized is False
    assert stage_a.task_outcomes_generated is False

    repeated_a = build_stage_a_manifest(
        pool=pool,
        selection_seed=20260801,
        maximum_tasks_per_repository=4,
    )
    assert repeated_a == stage_a


def test_stage_a_manifest_requires_twelve_repository_capped_candidates() -> None:
    pool = build_core_benefit_screen_pool(
        screen_protocol_sha256=_digest("screen"),
        acquisition_rules_sha256=_digest("rules"),
        candidates=tuple(_eligible(index) for index in range(11)),
    )

    with pytest.raises(ValueError, match="12-task repository-capped"):
        build_stage_a_manifest(
            pool=pool,
            selection_seed=20260801,
            maximum_tasks_per_repository=4,
        )


def test_v4_audit_requires_two_distinct_agreeing_reviewers(tmp_path: Path) -> None:
    package_root = Path(__file__).parents[1]
    instance_id = "matplotlib__matplotlib-20826"
    rules_path = package_root / "EXPLORATORY_TASK_ACQUISITION_RULES_V4.json"
    host_preflight_path = package_root / "EXPLORATORY_ACQUISITION_PREFLIGHT_V6.json"
    inventory_path = package_root / "EXPLORATORY_CANDIDATE_INVENTORY_V4.json"
    image_receipt_path = (
        package_root / "exploratory_candidate_image_v1" / f"{instance_id}.json"
    )
    execution_receipt_path = (
        package_root / "exploratory_candidate_execution_v1" / f"{instance_id}.json"
    )
    nonexecution_receipt_path = (
        package_root / "exploratory_candidate_nonexecution_v1" / f"{instance_id}.json"
    )
    first_review_path = (
        package_root
        / "exploratory_candidate_review_v1"
        / f"{instance_id}.reviewer-root-v1.json"
    )
    rationale_path = tmp_path / "reviewer-b-rationale.txt"
    rationale_path.write_text("Independent review retained outside public artifacts.\n")
    second_review = build_candidate_stratum_review_decision(
        decision_id="test-reviewer-b",
        rules_path=rules_path,
        nonexecution_receipt_path=nonexecution_receipt_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        image_receipt_path=image_receipt_path,
        execution_receipt_path=execution_receipt_path,
        instance_id=instance_id,
        reviewer_identity_sha256=_digest("reviewer-b"),
        proposed_stratum="localization",
        private_rationale_path=rationale_path,
    )
    second_review_path = tmp_path / "reviewer-b.json"
    write_candidate_stratum_review_decision(
        decision=second_review,
        output_path=second_review_path,
    )

    candidate = audit_v4_screen_candidate(
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        image_receipt_path=image_receipt_path,
        execution_receipt_path=execution_receipt_path,
        nonexecution_receipt_path=nonexecution_receipt_path,
        first_review_path=first_review_path,
        second_review_path=second_review_path,
    )

    assert candidate.instance_id == instance_id
    assert candidate.repository == "matplotlib"
    assert candidate.first_proposed_stratum == "localization"
