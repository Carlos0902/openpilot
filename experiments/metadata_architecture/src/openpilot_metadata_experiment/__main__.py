from __future__ import annotations

import argparse
import json
from pathlib import Path

from .audit import audit_metadata_directory
from .case_studies import run_project_diagnosis_case


def main() -> None:
    parser = argparse.ArgumentParser(description="Run isolated OpenPilot metadata architecture experiments.")
    parser.add_argument("command", choices=("diagnosis-case", "audit"))
    parser.add_argument("--metadata-dir", type=Path)
    args = parser.parse_args()

    if args.command == "diagnosis-case":
        result = run_project_diagnosis_case()
    else:
        if args.metadata_dir is None:
            parser.error("audit requires --metadata-dir")
        result = audit_metadata_directory(args.metadata_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
