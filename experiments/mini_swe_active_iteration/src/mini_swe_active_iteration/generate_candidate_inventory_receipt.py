"""Generate a non-overwriting receipt for a candidate inventory."""

from __future__ import annotations

import argparse
from pathlib import Path

from .acquisition import (
    build_candidate_inventory_receipt,
    write_candidate_inventory_receipt,
)


def generate_candidate_inventory_receipt(
    *,
    receipt_id: str,
    rules_path: Path,
    preflight_path: Path,
    inventory_path: Path,
    output_path: Path,
) -> Path:
    receipt = build_candidate_inventory_receipt(
        receipt_id=receipt_id,
        rules_path=rules_path,
        preflight_path=preflight_path,
        inventory_path=inventory_path,
    )
    write_candidate_inventory_receipt(
        receipt=receipt,
        output_path=output_path,
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt-id", required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = generate_candidate_inventory_receipt(
        receipt_id=args.receipt_id,
        rules_path=args.rules,
        preflight_path=args.preflight,
        inventory_path=args.inventory,
        output_path=args.output,
    )
    print(output.resolve())


if __name__ == "__main__":
    main()
