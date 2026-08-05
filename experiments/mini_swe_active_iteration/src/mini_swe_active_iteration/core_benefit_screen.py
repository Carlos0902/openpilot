"""Outcome-free artifacts for the mini-SWE core-benefit screening protocol."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from .acquisition import (
    load_candidate_execution_preflight_receipt,
    load_candidate_image_acquisition_receipt,
    load_candidate_nonexecution_evidence_receipt,
    load_candidate_stratum_review_decision,
)


_ALLOWED_STRATA = frozenset(
    {
        "localization",
        "single_file",
        "multi_file_interface",
        "config_cli",
        "test_regression",
        "measurement_disambiguation",
    }
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_sha256(value: str, *, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256")


class CoreBenefitScreenCandidate(_StrictModel):
    """A candidate only after two independently recorded stratum reviews."""

    instance_id: str
    repository: str
    selection_rank: int
    nonexecution_receipt_sha256: str
    first_review_sha256: str
    second_review_sha256: str
    first_reviewer_identity_sha256: str
    second_reviewer_identity_sha256: str
    first_proposed_stratum: str
    second_proposed_stratum: str
    blind_to_future_arm_outcomes: bool
    agent_arm_outcomes_available: bool

    @model_validator(mode="after")
    def validate_candidate(self) -> "CoreBenefitScreenCandidate":
        if (
            not self.instance_id.strip()
            or not self.repository.strip()
            or self.selection_rank <= 0
        ):
            raise ValueError("screen candidate identity is incomplete")
        for field in (
            "nonexecution_receipt_sha256",
            "first_review_sha256",
            "second_review_sha256",
            "first_reviewer_identity_sha256",
            "second_reviewer_identity_sha256",
        ):
            _require_sha256(getattr(self, field), field=field)
        if (
            self.first_proposed_stratum not in _ALLOWED_STRATA
            or self.second_proposed_stratum not in _ALLOWED_STRATA
        ):
            raise ValueError("screen candidate has an unsupported stratum")
        if (
            not self.blind_to_future_arm_outcomes
            or self.agent_arm_outcomes_available
        ):
            raise ValueError("screen candidate review must be outcome-blind")
        return self


class CoreBenefitScreenAdjudication(_StrictModel):
    """Redacted resolution for an otherwise unresolved review pair."""

    schema_version: str
    adjudication_id: str
    instance_id: str
    selection_rank: int
    first_review_sha256: str
    second_review_sha256: str
    adjudicator_identity_sha256: str
    resolved_primary_stratum: str
    private_rationale_sha256: str
    blind_to_future_arm_outcomes: bool
    agent_arm_outcomes_available: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_adjudication(self) -> "CoreBenefitScreenAdjudication":
        if (
            self.schema_version != "1.0"
            or not self.adjudication_id.strip()
            or not self.instance_id.strip()
            or self.selection_rank <= 0
            or self.resolved_primary_stratum not in _ALLOWED_STRATA
            or not self.blind_to_future_arm_outcomes
            or self.agent_arm_outcomes_available
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError("unsupported or outcome-aware screen adjudication")
        for field in (
            "first_review_sha256",
            "second_review_sha256",
            "adjudicator_identity_sha256",
            "private_rationale_sha256",
        ):
            _require_sha256(getattr(self, field), field=field)
        return self


class CoreBenefitScreenMechanismReview(_StrictModel):
    """Outcome-blind confirmation that a candidate belongs to the screen scope."""

    schema_version: str
    review_id: str
    instance_id: str
    selection_rank: int
    first_review_sha256: str
    second_review_sha256: str
    adjudication_sha256: str | None
    reviewer_identity_sha256: str
    diagnostic_measurement: bool
    post_action_validation: bool
    recovery_or_safe_stop: bool
    private_rationale_sha256: str
    blind_to_future_arm_outcomes: bool
    agent_arm_outcomes_available: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_mechanism_review(self) -> "CoreBenefitScreenMechanismReview":
        if (
            self.schema_version != "1.0"
            or not self.review_id.strip()
            or not self.instance_id.strip()
            or self.selection_rank <= 0
            or not (
                self.diagnostic_measurement
                or self.post_action_validation
                or self.recovery_or_safe_stop
            )
            or not self.blind_to_future_arm_outcomes
            or self.agent_arm_outcomes_available
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError("unsupported or outcome-aware mechanism review")
        for field in (
            "first_review_sha256",
            "second_review_sha256",
            "reviewer_identity_sha256",
            "private_rationale_sha256",
        ):
            _require_sha256(getattr(self, field), field=field)
        if self.adjudication_sha256 is not None:
            _require_sha256(self.adjudication_sha256, field="adjudication_sha256")
        return self


class CoreBenefitScreenEligibleCandidate(_StrictModel):
    """A reviewed, resolved and mechanism-eligible screen candidate."""

    instance_id: str
    repository: str
    selection_rank: int
    nonexecution_receipt_sha256: str
    first_review_sha256: str
    second_review_sha256: str
    primary_stratum: str
    adjudication_sha256: str | None
    mechanism_review_sha256: str
    diagnostic_measurement: bool
    post_action_validation: bool
    recovery_or_safe_stop: bool

    @model_validator(mode="after")
    def validate_eligible_candidate(self) -> "CoreBenefitScreenEligibleCandidate":
        if (
            not self.instance_id.strip()
            or not self.repository.strip()
            or self.selection_rank <= 0
            or self.primary_stratum not in _ALLOWED_STRATA
            or not (
                self.diagnostic_measurement
                or self.post_action_validation
                or self.recovery_or_safe_stop
            )
        ):
            raise ValueError("screen candidate is not mechanism eligible")
        for field in (
            "nonexecution_receipt_sha256",
            "first_review_sha256",
            "second_review_sha256",
            "mechanism_review_sha256",
        ):
            _require_sha256(getattr(self, field), field=field)
        if self.adjudication_sha256 is not None:
            _require_sha256(self.adjudication_sha256, field="adjudication_sha256")
        return self


def resolve_screen_candidate(
    *,
    candidate: CoreBenefitScreenCandidate,
    adjudication: CoreBenefitScreenAdjudication | None,
    adjudication_sha256: str | None,
    mechanism_review: CoreBenefitScreenMechanismReview,
    mechanism_review_sha256: str,
) -> CoreBenefitScreenEligibleCandidate:
    """Resolve review disagreement and bind a separate scope-eligibility review."""

    _require_sha256(mechanism_review_sha256, field="mechanism_review_sha256")
    if (
        candidate.first_reviewer_identity_sha256
        == candidate.second_reviewer_identity_sha256
    ):
        raise ValueError("screen candidate review independence drifted")
    agreed = candidate.first_proposed_stratum == candidate.second_proposed_stratum
    if agreed:
        if adjudication is not None or adjudication_sha256 is not None:
            raise ValueError("agreeing review pairs cannot receive an adjudication")
        primary_stratum = candidate.first_proposed_stratum
    else:
        if adjudication is None or adjudication_sha256 is None:
            raise ValueError("review disagreement requires an adjudication")
        _require_sha256(adjudication_sha256, field="adjudication_sha256")
        if (
            adjudication.instance_id != candidate.instance_id
            or adjudication.selection_rank != candidate.selection_rank
            or adjudication.first_review_sha256 != candidate.first_review_sha256
            or adjudication.second_review_sha256 != candidate.second_review_sha256
            or adjudication.adjudicator_identity_sha256
            in {
                candidate.first_reviewer_identity_sha256,
                candidate.second_reviewer_identity_sha256,
            }
        ):
            raise ValueError("screen adjudication binding or independence drifted")
        primary_stratum = adjudication.resolved_primary_stratum
    if (
        mechanism_review.instance_id != candidate.instance_id
        or mechanism_review.selection_rank != candidate.selection_rank
        or mechanism_review.first_review_sha256 != candidate.first_review_sha256
        or mechanism_review.second_review_sha256 != candidate.second_review_sha256
        or mechanism_review.adjudication_sha256 != adjudication_sha256
        or mechanism_review.reviewer_identity_sha256
        == candidate.first_reviewer_identity_sha256
        or (
            adjudication is not None
            and mechanism_review.reviewer_identity_sha256
            == adjudication.adjudicator_identity_sha256
        )
    ):
        raise ValueError("screen mechanism review binding or independence drifted")
    return CoreBenefitScreenEligibleCandidate(
        instance_id=candidate.instance_id,
        repository=candidate.repository,
        selection_rank=candidate.selection_rank,
        nonexecution_receipt_sha256=candidate.nonexecution_receipt_sha256,
        first_review_sha256=candidate.first_review_sha256,
        second_review_sha256=candidate.second_review_sha256,
        primary_stratum=primary_stratum,
        adjudication_sha256=adjudication_sha256,
        mechanism_review_sha256=mechanism_review_sha256,
        diagnostic_measurement=mechanism_review.diagnostic_measurement,
        post_action_validation=mechanism_review.post_action_validation,
        recovery_or_safe_stop=mechanism_review.recovery_or_safe_stop,
    )


class CoreBenefitScreenPoolReceipt(_StrictModel):
    """A redacted, outcome-free pool for the 12-task strong-signal screen."""

    schema_version: str
    screen_protocol_sha256: str
    acquisition_rules_sha256: str
    candidates: tuple[CoreBenefitScreenEligibleCandidate, ...]
    eligible_candidate_count: int
    provider_execution_authorized: bool
    production_execution_authorized: bool
    task_outcomes_generated: bool

    @model_validator(mode="after")
    def validate_pool(self) -> "CoreBenefitScreenPoolReceipt":
        if self.schema_version != "1.0":
            raise ValueError("unsupported core benefit screen pool")
        _require_sha256(self.screen_protocol_sha256, field="screen_protocol_sha256")
        _require_sha256(
            self.acquisition_rules_sha256,
            field="acquisition_rules_sha256",
        )
        if (
            self.eligible_candidate_count != len(self.candidates)
            or not self.candidates
            or self.provider_execution_authorized
            or self.production_execution_authorized
            or self.task_outcomes_generated
        ):
            raise ValueError("screen pool cannot authorize execution or outcomes")
        instance_ids = tuple(candidate.instance_id for candidate in self.candidates)
        ranks = tuple(candidate.selection_rank for candidate in self.candidates)
        if len(set(instance_ids)) != len(instance_ids) or len(set(ranks)) != len(ranks):
            raise ValueError("screen pool candidates must have unique identities")
        if self.candidates != tuple(
            sorted(self.candidates, key=lambda item: (item.selection_rank, item.instance_id))
        ):
            raise ValueError("screen pool candidates must use canonical ordering")
        return self

    def canonical_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()


class CoreBenefitScreenManifestEntry(_StrictModel):
    ordinal: int
    instance_id: str
    repository: str
    selection_rank: int
    primary_stratum: str
    nonexecution_receipt_sha256: str
    first_review_sha256: str
    second_review_sha256: str

    @model_validator(mode="after")
    def validate_entry(self) -> "CoreBenefitScreenManifestEntry":
        if (
            self.ordinal <= 0
            or not self.instance_id.strip()
            or not self.repository.strip()
            or self.selection_rank <= 0
            or self.primary_stratum not in _ALLOWED_STRATA
        ):
            raise ValueError("screen manifest entry is incomplete")
        for field in (
            "nonexecution_receipt_sha256",
            "first_review_sha256",
            "second_review_sha256",
        ):
            _require_sha256(getattr(self, field), field=field)
        return self


class CoreBenefitScreenStageManifest(_StrictModel):
    schema_version: str
    stage: str
    pool_sha256: str
    selection_seed: int
    selection_algorithm: str
    maximum_tasks_per_repository: int
    entries: tuple[CoreBenefitScreenManifestEntry, ...]
    provider_execution_authorized: bool
    production_execution_authorized: bool
    task_outcomes_generated: bool

    @model_validator(mode="after")
    def validate_manifest(self) -> "CoreBenefitScreenStageManifest":
        if self.schema_version != "1.0" or self.stage != "A":
            raise ValueError("unsupported core benefit screen stage")
        _require_sha256(self.pool_sha256, field="pool_sha256")
        if (
            self.selection_seed < 0
            or self.selection_algorithm != "sha256-ranked-screen-v1"
            or self.maximum_tasks_per_repository <= 0
            or len(self.entries) != 12
            or self.provider_execution_authorized
            or self.production_execution_authorized
            or self.task_outcomes_generated
        ):
            raise ValueError("screen manifest cannot authorize execution or outcomes")
        if tuple(entry.ordinal for entry in self.entries) != tuple(range(1, 13)):
            raise ValueError("screen manifest ordinals must be contiguous")
        instance_ids = tuple(entry.instance_id for entry in self.entries)
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("screen manifest task identities must be unique")
        if any(
            count > self.maximum_tasks_per_repository
            for count in Counter(entry.repository for entry in self.entries).values()
        ):
            raise ValueError("screen manifest exceeds repository cap")
        return self


def build_core_benefit_screen_pool(
    *,
    screen_protocol_sha256: str,
    acquisition_rules_sha256: str,
    candidates: tuple[CoreBenefitScreenEligibleCandidate, ...],
) -> CoreBenefitScreenPoolReceipt:
    """Validate resolved, mechanism-eligible candidates without execution authority."""

    _require_sha256(screen_protocol_sha256, field="screen_protocol_sha256")
    _require_sha256(acquisition_rules_sha256, field="acquisition_rules_sha256")
    return CoreBenefitScreenPoolReceipt(
        schema_version="1.0",
        screen_protocol_sha256=screen_protocol_sha256,
        acquisition_rules_sha256=acquisition_rules_sha256,
        candidates=tuple(
            sorted(candidates, key=lambda item: (item.selection_rank, item.instance_id))
        ),
        eligible_candidate_count=len(candidates),
        provider_execution_authorized=False,
        production_execution_authorized=False,
        task_outcomes_generated=False,
    )


def _artifact_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("screen audit artifact must be a regular non-symlink file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_v4_screen_candidate(
    *,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    execution_receipt_path: Path,
    nonexecution_receipt_path: Path,
    first_review_path: Path,
    second_review_path: Path,
) -> CoreBenefitScreenCandidate:
    """Bind one screen candidate to the full V4 receipt and two-review chain.

    This function reads only redacted public receipts and review decisions. It
    deliberately does not read private rationales, patches, tests, or an agent
    trajectory, so screen construction cannot expose hidden evaluator inputs.
    """

    image = load_candidate_image_acquisition_receipt(
        image_receipt_path,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
    )
    execution = load_candidate_execution_preflight_receipt(
        execution_receipt_path,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        image_receipt_path=image_receipt_path,
    )
    nonexecution = load_candidate_nonexecution_evidence_receipt(
        nonexecution_receipt_path,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        image_receipt_path=image_receipt_path,
        execution_receipt_path=execution_receipt_path,
    )
    first = load_candidate_stratum_review_decision(
        first_review_path,
        rules_path=rules_path,
        nonexecution_receipt_path=nonexecution_receipt_path,
    )
    second = load_candidate_stratum_review_decision(
        second_review_path,
        rules_path=rules_path,
        nonexecution_receipt_path=nonexecution_receipt_path,
    )
    instance_ids = {
        image.instance_id,
        execution.instance_id,
        nonexecution.instance_id,
        first.instance_id,
        second.instance_id,
    }
    if len(instance_ids) != 1:
        raise ValueError("screen audit candidate identity drifted")
    ranks = {
        image.selection_rank,
        execution.selection_rank,
        nonexecution.selection_rank,
        first.selection_rank,
        second.selection_rank,
    }
    if len(ranks) != 1:
        raise ValueError("screen audit candidate rank drifted")
    if (
        not execution.execution_preflight_passed
        or nonexecution.previously_exposed
        or not nonexecution.nonexecution_evidence_complete
        or nonexecution.agent_task_outcomes_generated
        or nonexecution.provider_execution_authorized
        or nonexecution.production_execution_authorized
    ):
        raise ValueError("screen audit candidate is not outcome-free and eligible")
    if (
        first.agent_arm_outcomes_available
        or second.agent_arm_outcomes_available
        or not first.blind_to_future_arm_outcomes
        or not second.blind_to_future_arm_outcomes
    ):
        raise ValueError("screen audit reviews are not outcome-blind")
    instance_id = next(iter(instance_ids))
    repository, separator, _ = instance_id.partition("__")
    if not separator or not repository:
        raise ValueError("screen audit candidate has an unsupported instance id")
    return CoreBenefitScreenCandidate(
        instance_id=instance_id,
        repository=repository,
        selection_rank=next(iter(ranks)),
        nonexecution_receipt_sha256=_artifact_sha256(nonexecution_receipt_path),
        first_review_sha256=_artifact_sha256(first_review_path),
        second_review_sha256=_artifact_sha256(second_review_path),
        first_reviewer_identity_sha256=first.reviewer_identity_sha256,
        second_reviewer_identity_sha256=second.reviewer_identity_sha256,
        first_proposed_stratum=first.proposed_stratum,
        second_proposed_stratum=second.proposed_stratum,
        blind_to_future_arm_outcomes=True,
        agent_arm_outcomes_available=False,
    )


def _selection_key(*, selection_seed: int, candidate: CoreBenefitScreenCandidate) -> str:
    return hashlib.sha256(
        f"{selection_seed}:{candidate.instance_id}".encode()
    ).hexdigest()


def _manifest_entry(
    *, ordinal: int,
    candidate: CoreBenefitScreenEligibleCandidate,
) -> CoreBenefitScreenManifestEntry:
    return CoreBenefitScreenManifestEntry(
        ordinal=ordinal,
        instance_id=candidate.instance_id,
        repository=candidate.repository,
        selection_rank=candidate.selection_rank,
        primary_stratum=candidate.primary_stratum,
        nonexecution_receipt_sha256=candidate.nonexecution_receipt_sha256,
        first_review_sha256=candidate.first_review_sha256,
        second_review_sha256=candidate.second_review_sha256,
    )


def build_stage_a_manifest(
    *,
    pool: CoreBenefitScreenPoolReceipt,
    selection_seed: int,
    maximum_tasks_per_repository: int,
) -> CoreBenefitScreenStageManifest:
    """Select the deterministic 12-task strong-signal manifest."""

    if selection_seed < 0 or maximum_tasks_per_repository != 4:
        raise ValueError("screen selection configuration is invalid")
    selected: list[CoreBenefitScreenEligibleCandidate] = []
    repository_counts: Counter[str] = Counter()
    for candidate in sorted(
        pool.candidates,
        key=lambda item: (_selection_key(selection_seed=selection_seed, candidate=item), item.instance_id),
    ):
        if repository_counts[candidate.repository] >= maximum_tasks_per_repository:
            continue
        selected.append(candidate)
        repository_counts[candidate.repository] += 1
        if len(selected) == 12:
            break
    if len(selected) != 12:
        raise ValueError(
            "eligible pool cannot satisfy the 12-task repository-capped screen"
        )

    pool_sha256 = pool.canonical_sha256()
    common = {
        "schema_version": "1.0",
        "pool_sha256": pool_sha256,
        "selection_seed": selection_seed,
        "selection_algorithm": "sha256-ranked-screen-v1",
        "maximum_tasks_per_repository": maximum_tasks_per_repository,
        "provider_execution_authorized": False,
        "production_execution_authorized": False,
        "task_outcomes_generated": False,
    }
    return CoreBenefitScreenStageManifest(
        stage="A",
        entries=tuple(
            _manifest_entry(ordinal=index, candidate=candidate)
            for index, candidate in enumerate(selected, start=1)
        ),
        **common,
    )


def write_core_benefit_screen_artifact(
    *,
    artifact: (
        CoreBenefitScreenAdjudication
        | CoreBenefitScreenMechanismReview
        | CoreBenefitScreenPoolReceipt
        | CoreBenefitScreenStageManifest
    ),
    output_path: Path,
) -> None:
    """Persist a canonical, non-overwriting redacted screen artifact."""

    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(f"refusing to overwrite screen artifact: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            artifact.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
