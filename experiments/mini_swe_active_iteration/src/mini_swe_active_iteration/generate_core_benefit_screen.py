"""Generate an outcome-free pool and 12-task Stage A draft after review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .core_benefit_screen import (
    CoreBenefitScreenAdjudication,
    CoreBenefitScreenEligibleCandidate,
    CoreBenefitScreenMechanismReview,
    audit_v4_screen_candidate,
    build_core_benefit_screen_pool,
    build_stage_a_manifest,
    resolve_screen_candidate,
    write_core_benefit_screen_artifact,
)


def _eligible_candidates(
    candidates: list[CoreBenefitScreenEligibleCandidate],
) -> tuple[CoreBenefitScreenEligibleCandidate, ...]:
    """Require enough resolved, mechanism-eligible candidates for Stage A."""

    if len(candidates) < 12:
        raise ValueError(
            "fewer than 12 candidates are resolved and mechanism eligible"
        )
    return tuple(candidates)


def _review_paths(
    *,
    directory: Path,
    suffix: str,
) -> dict[str, Path]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("screen review directory must be a regular directory")
    paths: dict[str, Path] = {}
    for path in sorted(directory.glob(f"*{suffix}")):
        instance_id = path.name.removesuffix(suffix)
        if not instance_id or instance_id in paths:
            raise ValueError("screen review identities must be unique")
        paths[instance_id] = path
    return paths


def _load_optional_adjudication(path: Path) -> CoreBenefitScreenAdjudication | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("screen adjudication must be a regular non-symlink file")
    return CoreBenefitScreenAdjudication.model_validate(json.loads(path.read_text()))


def _load_mechanism_review(path: Path) -> CoreBenefitScreenMechanismReview:
    if path.is_symlink() or not path.is_file():
        raise ValueError("screen mechanism review must be a regular non-symlink file")
    return CoreBenefitScreenMechanismReview.model_validate(json.loads(path.read_text()))


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("screen artifact must be a regular non-symlink file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_core_benefit_screen(
    *,
    screen_protocol_path: Path,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_directory: Path,
    execution_receipt_directory: Path,
    nonexecution_receipt_directory: Path,
    first_review_directory: Path,
    first_review_suffix: str,
    second_review_directory: Path,
    second_review_suffix: str,
    adjudication_directory: Path,
    mechanism_review_directory: Path,
    selection_seed: int,
    maximum_tasks_per_repository: int,
    pool_output_path: Path,
    stage_a_output_path: Path,
) -> tuple[Path, Path]:
    """Create only redacted drafts; provider execution remains unauthorized."""

    for directory in (
        image_receipt_directory,
        execution_receipt_directory,
        nonexecution_receipt_directory,
    ):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("screen receipt directory must be a regular directory")
    if screen_protocol_path.is_symlink() or not screen_protocol_path.is_file():
        raise ValueError("screen protocol must be a regular non-symlink file")
    first_reviews = _review_paths(
        directory=first_review_directory,
        suffix=first_review_suffix,
    )
    second_reviews = _review_paths(
        directory=second_review_directory,
        suffix=second_review_suffix,
    )
    nonexecution_paths = tuple(sorted(nonexecution_receipt_directory.glob("*.json")))
    if not nonexecution_paths:
        raise ValueError("screen requires nonexecution receipts")

    if adjudication_directory.is_symlink() or not adjudication_directory.is_dir():
        raise ValueError("screen adjudication directory must be a regular directory")
    if (
        mechanism_review_directory.is_symlink()
        or not mechanism_review_directory.is_dir()
    ):
        raise ValueError("screen mechanism review directory must be a regular directory")
    candidates = []
    for nonexecution_path in nonexecution_paths:
        instance_id = nonexecution_path.stem
        try:
            first_review_path = first_reviews[instance_id]
            second_review_path = second_reviews[instance_id]
        except KeyError as error:
            raise ValueError(
                f"screen candidate is missing an independent review: {instance_id}"
            ) from error
        mechanism_path = mechanism_review_directory / f"{instance_id}.json"
        if not mechanism_path.is_file() or mechanism_path.is_symlink():
            continue
        candidate = audit_v4_screen_candidate(
            rules_path=rules_path,
            host_preflight_path=host_preflight_path,
            inventory_path=inventory_path,
            image_receipt_path=image_receipt_directory / f"{instance_id}.json",
            execution_receipt_path=(
                execution_receipt_directory / f"{instance_id}.json"
            ),
            nonexecution_receipt_path=nonexecution_path,
            first_review_path=first_review_path,
            second_review_path=second_review_path,
        )
        adjudication_path = adjudication_directory / f"{instance_id}.json"
        adjudication = _load_optional_adjudication(adjudication_path)
        candidates.append(
            resolve_screen_candidate(
                candidate=candidate,
                adjudication=adjudication,
                adjudication_sha256=(
                    _sha256(adjudication_path) if adjudication is not None else None
                ),
                mechanism_review=_load_mechanism_review(mechanism_path),
                mechanism_review_sha256=_sha256(mechanism_path),
            )
        )

    pool = build_core_benefit_screen_pool(
        screen_protocol_sha256=hashlib.sha256(
            screen_protocol_path.read_bytes()
        ).hexdigest(),
        acquisition_rules_sha256=hashlib.sha256(rules_path.read_bytes()).hexdigest(),
        candidates=_eligible_candidates(candidates),
    )
    stage_a = build_stage_a_manifest(
        pool=pool,
        selection_seed=selection_seed,
        maximum_tasks_per_repository=maximum_tasks_per_repository,
    )
    for output_path in (pool_output_path, stage_a_output_path):
        if output_path.exists() or output_path.is_symlink():
            raise FileExistsError(f"refusing to overwrite screen output: {output_path}")
    write_core_benefit_screen_artifact(
        artifact=pool,
        output_path=pool_output_path,
    )
    write_core_benefit_screen_artifact(
        artifact=stage_a,
        output_path=stage_a_output_path,
    )
    return pool_output_path, stage_a_output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screen-protocol", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--host-preflight", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--image-receipt-directory", type=Path, required=True)
    parser.add_argument("--execution-receipt-directory", type=Path, required=True)
    parser.add_argument("--nonexecution-receipt-directory", type=Path, required=True)
    parser.add_argument("--first-review-directory", type=Path, required=True)
    parser.add_argument("--first-review-suffix", required=True)
    parser.add_argument("--second-review-directory", type=Path, required=True)
    parser.add_argument("--second-review-suffix", required=True)
    parser.add_argument("--adjudication-directory", type=Path, required=True)
    parser.add_argument("--mechanism-review-directory", type=Path, required=True)
    parser.add_argument("--selection-seed", type=int, required=True)
    parser.add_argument("--maximum-tasks-per-repository", type=int, required=True)
    parser.add_argument("--pool-output", type=Path, required=True)
    parser.add_argument("--stage-a-output", type=Path, required=True)
    args = parser.parse_args()
    outputs = generate_core_benefit_screen(
        screen_protocol_path=args.screen_protocol,
        rules_path=args.rules,
        host_preflight_path=args.host_preflight,
        inventory_path=args.inventory,
        image_receipt_directory=args.image_receipt_directory,
        execution_receipt_directory=args.execution_receipt_directory,
        nonexecution_receipt_directory=args.nonexecution_receipt_directory,
        first_review_directory=args.first_review_directory,
        first_review_suffix=args.first_review_suffix,
        second_review_directory=args.second_review_directory,
        second_review_suffix=args.second_review_suffix,
        adjudication_directory=args.adjudication_directory,
        mechanism_review_directory=args.mechanism_review_directory,
        selection_seed=args.selection_seed,
        maximum_tasks_per_repository=args.maximum_tasks_per_repository,
        pool_output_path=args.pool_output,
        stage_a_output_path=args.stage_a_output,
    )
    for output in outputs:
        print(output.resolve())


if __name__ == "__main__":
    main()
