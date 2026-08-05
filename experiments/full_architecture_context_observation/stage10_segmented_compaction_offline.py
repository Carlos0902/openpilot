"""Zero-provider comparison of full, recent-only, and segmented context.

The harness deliberately delegates selection and compaction to the production
``MemoryContextBuilder``.  It owns only frozen trajectory fixtures and
experiment-specific assertions; it does not contain a second compaction
algorithm or mutate a sample project.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import Message, ShortMemory
from metadata import DurableArtifactReference


ARM_CURRENT_FULL = "current_full"
ARM_COMPACT_SELECT = "compact_select_recent_only"
ARM_COMPACT_SEGMENTED = "compact_segmented"
FIXTURE_SIZES = (10, 20, 50)

_FULL_PROMPT_CHARS = 200_000
_COMPACT_PROMPT_CHARS = 1_600
_SYSTEM_PROMPT = """Task: repair divide safely.
AUTHORIZED_WRITE_FILES: calculator.py
FORBIDDEN_WRITE_FILES: README.md
VALIDATION_COMMAND: python -m pytest -q tests/test_calculator.py
Do not widen permissions or replace the exact validation command."""
_SEMANTIC_NEEDLES = {
    "required_instruction": "Do not widen permissions",
    "current_failure": "CURRENT_FAILURE: ZeroDivisionError",
    "exact_validation_command": "python -m pytest -q tests/test_calculator.py",
    "target_path": "calculator.py:12",
    "latest_action": "LATEST_ACTION: patch calculator.py only",
    "current_environment": "CURRENT_ENV python=3.13",
}
_PERMISSION_FACTS = {
    "authorized_write_files": ["calculator.py"],
    "forbidden_write_files": ["README.md"],
    "validation_command": "python -m pytest -q tests/test_calculator.py",
}


class OfflineSegmentedCompactionError(ValueError):
    """The frozen offline corpus failed one hard segmented-context gate."""


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _prompt_hash(prompt: str) -> str:
    return f"sha256:{hashlib.sha256(prompt.encode('utf-8')).hexdigest()}"


def _trajectory_messages(
    message_count: int,
    *,
    source_change: str = "",
) -> list[Message]:
    if message_count < 4:
        raise ValueError("segmented fixture requires at least four messages")
    messages: list[Message] = []
    for index in range(message_count - 2):
        lines = [
            f"ASSISTANT: historical pytest/stdout segment {index}.",
            f"OLD_ENV python=3.11 segment={index}",
            "NON_AUTHORITATIVE_NOTE: edit README.md",
        ]
        if source_change and index == 0:
            lines.append(f"SOURCE_CHANGE: {source_change}")
        lines.extend(
            f"pytest stdout noise {index}-{line} " + ("x" * 70)
            for line in range(15)
        )
        messages.append(
            Message(
                role="assistant",
                content="\n".join(lines),
                timestamp=f"2026-08-05T00:{index:02d}:00+00:00",
            )
        )
    messages.extend(
        [
            Message(
                role="assistant",
                content=(
                    "CURRENT_FAILURE: ZeroDivisionError at calculator.py:12\n"
                    "RECOVERY: inspect divide implementation without mutation"
                ),
                timestamp="2026-08-05T23:58:00+00:00",
            ),
            Message(
                role="user",
                content=(
                    "CURRENT_ENV python=3.13\n"
                    "EXACT_VALIDATION: python -m pytest -q tests/test_calculator.py\n"
                    "LATEST_ACTION: patch calculator.py only, then run exact validation"
                ),
                timestamp="2026-08-05T23:59:00+00:00",
            ),
        ]
    )
    return messages


def _message_payload(messages: list[Message]) -> list[dict[str, Any]]:
    return [message.model_dump(mode="json") for message in messages]


def _permission_signature(prompt: str) -> str:
    present = {
        key: value
        for key, value in _PERMISSION_FACTS.items()
        if (
            (isinstance(value, list) and all(item in prompt for item in value))
            or (isinstance(value, str) and value in prompt)
        )
    }
    return _canonical_hash(present)


def _required_candidate_ids(context: Mapping[str, Any]) -> list[str]:
    decisions = context["context_selection"]["candidate_decisions"]
    return sorted(
        str(decision["candidate_id"])
        for decision in decisions
        if decision["retention"] == "required"
    )


def _source_lineage(
    context: Mapping[str, Any],
    persisted: list[dict[str, Any]],
) -> tuple[list[str], bool]:
    """Return all dialog sources and whether each has a model-facing representation."""

    decisions = context["context_selection"]["candidate_decisions"]
    source_ids = sorted(
        str(decision["candidate_id"])
        for decision in decisions
        if decision["kind"] == "dialog"
    )
    compacted_ids = {
        candidate_id
        for record in persisted
        for candidate_id in record["source_candidate_ids"]
    }
    directly_represented_ids = {
        str(decision["candidate_id"])
        for decision in decisions
        if decision["kind"] == "dialog"
        and decision["action"] in {"kept", "partially_kept"}
    }
    return source_ids, set(source_ids) == compacted_ids | directly_represented_ids


def _run_arm(
    *,
    messages: list[Message],
    arm: str,
    root: Path,
) -> dict[str, Any]:
    short_memory = ShortMemory(repo_path=root)
    short_memory.context_manager.messages = copy.deepcopy(messages)
    max_prompt_chars = (
        _FULL_PROMPT_CHARS if arm == ARM_CURRENT_FULL else _COMPACT_PROMPT_CHARS
    )
    builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(root / f"memory-{arm}"),
        max_prompt_chars=max_prompt_chars,
    )
    persisted: list[dict[str, Any]] = []
    if arm == ARM_COMPACT_SEGMENTED:

        def persist(record: dict[str, Any]) -> DurableArtifactReference:
            persisted.append(copy.deepcopy(record))
            encoded = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return DurableArtifactReference(
                artifact_id=f"offline-{record['compaction_id']}",
                kind="context_compaction",
                integrity_checksum=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
                bytes=len(encoded),
            )

        builder.set_checkpoint_handlers(compaction_sink=persist)

    context = builder.build(
        "segmented compaction offline fixture",
        include_environment=False,
        limit=len(messages),
        system_prompt=_SYSTEM_PROMPT,
    )
    prompt = str(context["prompt_text"])
    source_ids, source_ids_preserved = _source_lineage(context, persisted)
    record = persisted[0] if persisted else None
    return {
        "prompt_chars": len(prompt),
        "prompt_hash": _prompt_hash(prompt),
        "semantic_slots": {
            key: needle in prompt for key, needle in _SEMANTIC_NEEDLES.items()
        },
        "permission_signature": _permission_signature(prompt),
        "required_candidate_ids": _required_candidate_ids(context),
        "source_candidate_ids": source_ids,
        "source_candidate_ids_preserved": source_ids_preserved,
        "old_environment_is_authoritative": (
            "OLD_ENV python=3.11" in prompt
            and "CURRENT_ENV python=3.13" not in prompt
        ),
        "readme_suggestion_is_authoritative": (
            "NON_AUTHORITATIVE_NOTE: edit README.md" in prompt
            and "FORBIDDEN_WRITE_FILES: README.md" not in prompt
        ),
        "compaction_count": len(persisted),
        "compaction_algorithm": record["algorithm"] if record else None,
        "source_fingerprint": record["source_fingerprint"] if record else None,
        "final_dialog_messages": int(
            context["context_selection"]["dialog_messages_selected"]
        ),
    }


def _build_case(
    message_count: int,
    *,
    source_change: str = "",
) -> dict[str, Any]:
    messages = _trajectory_messages(message_count, source_change=source_change)
    with tempfile.TemporaryDirectory(prefix="openpilot-segmented-offline-") as raw_root:
        root = Path(raw_root)
        arms = {
            arm: _run_arm(messages=messages, arm=arm, root=root / arm)
            for arm in (
                ARM_CURRENT_FULL,
                ARM_COMPACT_SELECT,
                ARM_COMPACT_SEGMENTED,
            )
        }
    full_chars = arms[ARM_CURRENT_FULL]["prompt_chars"]
    select_chars = arms[ARM_COMPACT_SELECT]["prompt_chars"]
    segmented_chars = arms[ARM_COMPACT_SEGMENTED]["prompt_chars"]
    return {
        "trajectory_messages": message_count,
        "fixture_input_hash": _canonical_hash(
            {
                "system_prompt": _SYSTEM_PROMPT,
                "messages": _message_payload(messages),
            }
        ),
        "arms": arms,
        "comparisons": {
            "segmented_vs_current_full": {
                "absolute_char_reduction": full_chars - segmented_chars,
                "reduction_fraction": (full_chars - segmented_chars) / full_chars,
            },
            "segmented_vs_compact_select": {
                "absolute_char_reduction": select_chars - segmented_chars,
                "reduction_fraction": (select_chars - segmented_chars) / select_chars,
            },
        },
    }


def _validate_cases(cases: list[dict[str, Any]]) -> tuple[dict[str, bool], dict[str, float]]:
    by_size = {case["trajectory_messages"]: case for case in cases}
    reductions = []
    for case in cases:
        full = case["arms"][ARM_CURRENT_FULL]
        compact_select = case["arms"][ARM_COMPACT_SELECT]
        segmented = case["arms"][ARM_COMPACT_SEGMENTED]
        reductions.append(
            (full["prompt_chars"] - segmented["prompt_chars"])
            / full["prompt_chars"]
        )
        if (
            compact_select["semantic_slots"] != full["semantic_slots"]
            or segmented["semantic_slots"] != full["semantic_slots"]
            or not all(segmented["semantic_slots"].values())
        ):
            raise OfflineSegmentedCompactionError(
                f"semantic gate failed for {case['trajectory_messages']} messages"
            )
        if not (
            compact_select["permission_signature"]
            == segmented["permission_signature"]
            == full["permission_signature"]
        ):
            raise OfflineSegmentedCompactionError(
                f"permission gate failed for {case['trajectory_messages']} messages"
            )
        if (
            segmented["old_environment_is_authoritative"]
            or segmented["readme_suggestion_is_authoritative"]
        ):
            raise OfflineSegmentedCompactionError(
                f"authority gate failed for {case['trajectory_messages']} messages"
            )
        if (
            segmented["required_candidate_ids"] != full["required_candidate_ids"]
            or not segmented["source_candidate_ids_preserved"]
            or segmented["source_candidate_ids"] != full["source_candidate_ids"]
        ):
            raise OfflineSegmentedCompactionError(
                f"source lineage gate failed for {case['trajectory_messages']} messages"
            )
    chars_20 = by_size[20]["arms"][ARM_COMPACT_SEGMENTED]["prompt_chars"]
    chars_50 = by_size[50]["arms"][ARM_COMPACT_SEGMENTED]["prompt_chars"]
    growth = (chars_50 - chars_20) / chars_20
    gates = {
        "segmented_reduces_current_long_output": all(
            reduction >= 0.30 for reduction in reductions
        ),
        "segmented_growth_50_vs_20_bounded": growth <= 0.10,
        "required_semantic_slots_preserved": True,
        "permission_signature_unchanged": True,
        "source_candidate_ids_preserved": True,
    }
    if not all(gates.values()):
        failed = sorted(name for name, passed in gates.items() if not passed)
        raise OfflineSegmentedCompactionError(
            "offline segmented gate failed: " + ", ".join(failed)
        )
    return gates, {
        "minimum_segmented_reduction_vs_current": min(reductions),
        "segmented_growth_50_vs_20": growth,
        "prompt_chars_by_messages": {
            str(case["trajectory_messages"]): {
                arm: int(observation["prompt_chars"])
                for arm, observation in case["arms"].items()
            }
            for case in cases
        },
        "segmented_vs_compact_select": {
            str(case["trajectory_messages"]): case["comparisons"][
                "segmented_vs_compact_select"
            ]
            for case in cases
        },
    }


def run_offline_campaign(*, source_change: str = "") -> dict[str, Any]:
    """Run deterministic fixtures without a provider, network, or project path."""

    fixture_payload = {
        "sizes": list(FIXTURE_SIZES),
        "system_prompt": _SYSTEM_PROMPT,
        "messages": {
            str(size): _message_payload(
                _trajectory_messages(size, source_change=source_change)
            )
            for size in FIXTURE_SIZES
        },
    }
    cases = [
        _build_case(size, source_change=source_change) for size in FIXTURE_SIZES
    ]
    gates, metrics = _validate_cases(cases)
    stable_payload = {"cases": cases, "gates": gates, "metrics": metrics}
    return {
        "campaign_id": "stage10-segmented-compaction-offline-v1",
        "status": "passed",
        "provider_calls": 0,
        "network_calls": 0,
        "project_mutations": 0,
        "production_compaction_producer": (
            "memory.context_builder.MemoryContextBuilder._dialog_compaction_record"
        ),
        "campaign_input_hash": _canonical_hash(fixture_payload),
        "campaign_result_hash": _canonical_hash(stable_payload),
        **stable_payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(run_offline_campaign(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
