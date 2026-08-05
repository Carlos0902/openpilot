"""Persist one redacted, outcome-blind candidate stratum review."""

from __future__ import annotations

import argparse
from pathlib import Path

from .acquisition import (
    build_candidate_stratum_review_decision,
    write_candidate_stratum_review_decision,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--nonexecution-receipt", type=Path, required=True)
    parser.add_argument("--host-preflight", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--image-receipt", type=Path, required=True)
    parser.add_argument("--execution-receipt", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--reviewer-identity-sha256", required=True)
    parser.add_argument(
        "--proposed-stratum",
        choices=(
            "measurement_disambiguation",
            "config_cli",
            "multi_file_interface",
            "test_regression",
            "localization",
            "single_file",
        ),
        required=True,
    )
    parser.add_argument("--private-rationale", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    decision = build_candidate_stratum_review_decision(
        decision_id=args.decision_id,
        rules_path=args.rules,
        nonexecution_receipt_path=args.nonexecution_receipt,
        host_preflight_path=args.host_preflight,
        inventory_path=args.inventory,
        image_receipt_path=args.image_receipt,
        execution_receipt_path=args.execution_receipt,
        instance_id=args.instance_id,
        reviewer_identity_sha256=args.reviewer_identity_sha256,
        proposed_stratum=args.proposed_stratum,
        private_rationale_path=args.private_rationale,
    )
    write_candidate_stratum_review_decision(
        decision=decision,
        output_path=args.output,
    )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
