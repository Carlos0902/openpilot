"""Generate human-review summaries from persisted runtime diagnostics."""

from __future__ import annotations

import argparse
from pathlib import Path

from runtime_diagnostics.recorder import DEFAULT_DATA_DIR, DiagnosticRecorder
from runtime_diagnostics.summarizer import summarize_records, write_summary_json, write_summary_markdown


def generate_stage_summary(
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    output_dir: str | Path | None = None,
    limit: int = 0,
) -> tuple[Path, Path]:
    recorder = DiagnosticRecorder(data_dir)
    records = recorder.load_recent_records(limit=limit)
    summary = summarize_records(records)
    target_dir = Path(output_dir) if output_dir else Path(data_dir) / "summaries"
    md_path = write_summary_markdown(summary, target_dir / "latest.md")
    json_path = write_summary_json(summary, target_dir / "latest.json")
    return md_path, json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m runtime_diagnostics.report")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--limit", type=int, default=0, help="0 means all records")
    args = parser.parse_args(argv)

    md_path, json_path = generate_stage_summary(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        limit=args.limit,
    )
    print(f"markdown_summary={md_path}")
    print(f"json_summary={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
