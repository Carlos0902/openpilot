"""Command line interface for OpenPilot."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

from rich.console import Console

from core.config import EmbeddingSettings, LLMSettings


DEFAULT_OPENPILOT_LOG = Path(__file__).resolve().parents[2] / "logs" / "openpilot.jsonl"


def build_parser() -> argparse.ArgumentParser:
    """Build the modern OpenPilot CLI parser."""
    parser = argparse.ArgumentParser(prog="openpilot", description="OpenPilot AI Agent System")
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser("config", help="Configuration commands")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_subparsers.add_parser("check", help="Check LLM configuration")

    _add_run_parser(subparsers, "run", "Run the modern interactive OpenPilot CLI")
    _add_run_parser(subparsers, "openpilot", "Backward-compatible alias for run")
    return parser


def _add_run_parser(subparsers, name: str, help_text: str) -> None:
    run_parser = subparsers.add_parser(name, help=help_text)
    run_parser.add_argument("--log-file", default=str(DEFAULT_OPENPILOT_LOG), help="JSONL log file")
    run_parser.add_argument("--constraint", action="append", default=[], help="Reserved for compatibility")
    run_parser.add_argument("--once", help="Run one goal and exit")
    run_parser.add_argument(
        "--checkpointing",
        action="store_true",
        help="Persist durable runtime checkpoints for this run",
    )
    run_parser.add_argument("--project-path", help="Explicit project root used for checkpoint identity")
    run_parser.add_argument("--resume-run-id", help="Existing trajectory run ID to resume")
    run_parser.add_argument("--resume-checkpoint-id", help="Explicit checkpoint ID to resume")
    run_parser.add_argument("--ignore-memory", action="store_true", help="Reserved for compatibility")
    run_parser.add_argument(
        "--improvement-iterations",
        type=int,
        choices=range(0, 6),
        default=None,
        metavar="0-5",
        help="Project improvement iterations for autopilot outputs; 0 disables iterative improvement",
    )


def main(argv: Sequence[str] | None = None, llm_client: Any | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.command == "config" and args.config_command == "check":
        return _config_check(console)

    if args.command in {"run", "openpilot"}:
        return _run_openpilot(args, console, llm_client)

    parser.error("Unknown command")
    return 2


def _run_openpilot(args, console: Console, llm_client: Any | None) -> int:
    """Run OpenPilot with enhanced UI."""
    from ui.enhanced_cli import run_enhanced_cli

    return run_enhanced_cli(args, console, llm_client)


def _config_check(console: Console) -> int:
    settings = LLMSettings()
    embedding_settings = EmbeddingSettings()
    rows = [
        ("provider", settings.provider),
        ("base_url", "set" if settings.base_url.strip() else "missing"),
        ("model", settings.model),
        ("timeout_seconds", str(settings.timeout_seconds)),
        ("temperature", str(settings.temperature)),
        ("api_key", "set" if settings.api_key and settings.api_key.strip() else "missing"),
        ("embedding_provider", embedding_settings.provider),
        ("embedding_base_url", "set" if embedding_settings.base_url and embedding_settings.base_url.strip() else "missing"),
        ("embedding_model", embedding_settings.model),
        ("embedding_timeout_seconds", str(embedding_settings.timeout_seconds)),
        ("embedding_api_key", "set" if embedding_settings.api_key and embedding_settings.api_key.strip() else "missing"),
    ]

    console.print("OpenPilot LLM Configuration")
    for field, value in rows:
        console.print(f"{field}: {value}")

    missing = settings.missing_fields()
    embedding_missing = embedding_settings.missing_fields()
    if missing:
        console.print(
            f"Missing LLM configuration: {', '.join(missing)}. "
            "Real LLM calls will fail."
        )
    if embedding_missing:
        console.print(
            f"Missing embedding configuration: {', '.join(embedding_missing)}. "
            "Real embedding calls will fail."
        )
    if missing or embedding_missing:
        return 0
    console.print("Configuration is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
