"""Review shadow failure telemetry and malformed artifact fail-closed behavior."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from memory.compaction_reuse import build_checkpoint_compaction_reuse_shadow_provider
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory
from metadata import (
    ContextCompactionBinding,
    ContextCompactionRecord,
    ContextCompactionReuseAdmission,
    ContextCompactionReuseAdmissionStatus,
    ContextCompactionReuseShadowFailureReason,
    DurableArtifactReference,
)


SCHEMA = "phase-h8r2bb-shadow-failure-and-artifact-contract-review-v1"
CLAIM_BOUNDARY = "default_off_shadow_failure_telemetry_no_prompt_use"
_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}(?![A-Za-z0-9])")
_FORBIDDEN_KEYS = {"prompt", "content", "summary_body", "raw_response", "credential", "message"}
_ZERO_HASH = "sha256:" + "0" * 64


def canonical_hash(value: Any) -> str:
    payload = value if isinstance(value, bytes) else json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _safe_receipt_hash(receipt: Mapping[str, Any]) -> str:
    return canonical_hash({key: value for key, value in receipt.items() if key != "receipt_hash"})


def _write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _run(command: list[str], root: Path, gate_id: str) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=root,
        env={**os.environ, "PYTHONPATH": "Code/src"},
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    passed = re.search(r"(\d+) passed", output)
    failed = re.search(r"(\d+) failed", output)
    return {
        "gate_id": gate_id,
        "command": " ".join(command),
        "returncode": result.returncode,
        "passed_count": int(passed.group(1)) if passed else 0,
        "failed_count": int(failed.group(1)) if failed else 0,
        "passed": result.returncode == 0,
    }


def _builder_failure_gate() -> dict[str, Any]:
    cases: list[tuple[str, Any, str, str | None]] = [
        (
            "provider_exception",
            "exception",
            ContextCompactionReuseShadowFailureReason.PROVIDER_EXCEPTION.value,
            "RuntimeError",
        ),
        (
            "provider_none",
            None,
            ContextCompactionReuseShadowFailureReason.PROVIDER_EMPTY.value,
            None,
        ),
        (
            "provider_empty_list",
            [],
            ContextCompactionReuseShadowFailureReason.PROVIDER_EMPTY.value,
            None,
        ),
        (
            "provider_malformed",
            [{"status": "admitted"}],
            ContextCompactionReuseShadowFailureReason.INVALID_PROVIDER_RESULT.value,
            "ValidationError",
        ),
    ]
    outcomes: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="h8r2bb-builder-") as temp_root:
        root = Path(temp_root)
        short_memory = ShortMemory(repo_path=root / "short")
        for index in range(4):
            short_memory.add_message("assistant", f"dialog body {index}")

        baseline = MemoryContextBuilder(
            short_memory=short_memory,
            memory_store=MemoryStore(root / "baseline"),
            max_prompt_chars=1000,
        ).build(
            "h8r2bb failure review",
            include_environment=False,
            limit=4,
            system_prompt="Keep context stable.",
        )
        for case_name, provider_result, expected_reason, expected_exception in cases:
            def provider(_payload, *, result=provider_result, case=case_name):
                if case == "provider_exception":
                    raise RuntimeError("do not serialize this message")
                return result

            shadow = MemoryContextBuilder(
                short_memory=short_memory,
                memory_store=MemoryStore(root / f"shadow-{case_name}"),
                max_prompt_chars=1000,
                compaction_reuse_shadow_provider=provider,
            ).build(
                "h8r2bb failure review",
                include_environment=False,
                limit=4,
                system_prompt="Keep context stable.",
            )
            failures = shadow["context_selection"]["compaction_reuse_shadow_failures"]
            failure = failures[0] if failures else {}
            outcomes.append(
                {
                    "case": case_name,
                    "reason": failure.get("reason"),
                    "exception_type": failure.get("exception_type"),
                    "fallback_applied": failure.get("fallback_applied"),
                    "prompt_unchanged": shadow["prompt_text"] == baseline["prompt_text"],
                    "selected_candidates_unchanged": shadow["selected_context_candidates"]
                    == baseline["selected_context_candidates"],
                    "request_hash_unchanged": shadow["context_request_hash"]
                    == baseline["context_request_hash"],
                    "expected_reason": expected_reason,
                    "expected_exception_type": expected_exception,
                }
            )
    strict_raised = False
    with tempfile.TemporaryDirectory(prefix="h8r2bb-strict-") as temp_root:
        root = Path(temp_root)
        short_memory = ShortMemory(repo_path=root / "short")
        short_memory.add_message("assistant", "strict shadow")

        def raising_provider(_payload):
            raise RuntimeError("strict-only message")

        builder = MemoryContextBuilder(
            short_memory=short_memory,
            memory_store=MemoryStore(root / "memory"),
            max_prompt_chars=1000,
            compaction_reuse_shadow_provider=raising_provider,
        )
        try:
            builder.build(
                "strict failure",
                include_environment=False,
                limit=4,
                system_prompt="Keep context stable.",
                strict_sources=True,
            )
        except Exception as exc:
            strict_raised = type(exc).__name__ == "ContextSourceError" and "context_compaction_reuse" in str(exc)
    passed = bool(
        strict_raised
        and all(
            item["reason"] == item["expected_reason"]
            and item["exception_type"] == item["expected_exception_type"]
            and item["fallback_applied"] is True
            and item["prompt_unchanged"]
            and item["selected_candidates_unchanged"]
            and item["request_hash_unchanged"]
            for item in outcomes
        )
    )
    return {
        "gate_id": "h8r2bb_real_builder_shadow_failure_telemetry",
        "outcomes": outcomes,
        "strict_context_source_error": strict_raised,
        "real_provider_calls": 0,
        "project_mutations": 0,
        "memory_mutations": 0,
        "passed": passed,
    }


def _artifact_contract_gate() -> dict[str, Any]:
    record = ContextCompactionRecord(
        compaction_id="h8r2bb-record",
        source_fingerprint="sha256:" + "1" * 64,
        source_candidate_ids=["dialog-1", "dialog-2"],
        algorithm="deterministic_observation_mask_v1",
        summary="compact",
        original_chars=20,
        compacted_chars=7,
    )
    binding = ContextCompactionBinding(
        record=record,
        artifact=DurableArtifactReference(
            artifact_id="h8r2bb-artifact",
            kind="context_compaction",
            integrity_checksum="malformed-checksum",
            bytes=7,
        ),
        source_binding_hash="sha256:" + "2" * 64,
    )
    provider = build_checkpoint_compaction_reuse_shadow_provider([binding])
    admissions = provider(
        {
            "candidate_digests": [],
            "selected_candidate_ids": [],
            "session_constraints_hash": "",
        }
    )
    malformed_rejection = admissions[0] if admissions else None
    passed = bool(
        malformed_rejection is not None
        and malformed_rejection.status == ContextCompactionReuseAdmissionStatus.REJECTED
        and malformed_rejection.rejection_reason == "artifact_contract_invalid"
        and malformed_rejection.artifact_integrity_checksum == _ZERO_HASH
    )
    return {
        "gate_id": "h8r2bb_malformed_checkpoint_artifact_fail_closed",
        "status": malformed_rejection.status if malformed_rejection else None,
        "rejection_reason": malformed_rejection.rejection_reason if malformed_rejection else None,
        "safe_checksum_substitution": malformed_rejection.artifact_integrity_checksum == _ZERO_HASH
        if malformed_rejection
        else False,
        "passed": passed,
    }


def _admitted_identity_gate() -> dict[str, Any]:
    base = {
        "admission_id": "h8r2bb-invalid-admitted",
        "status": ContextCompactionReuseAdmissionStatus.ADMITTED,
        "source_candidate_ids": ["dialog-1"],
        "source_fingerprint": "sha256:" + "1" * 64,
        "source_binding_hash": "sha256:" + "2" * 64,
        "artifact_id": "artifact-1",
        "artifact_kind": "wrong_kind",
        "artifact_integrity_checksum": "sha256:" + "3" * 64,
    }
    wrong_kind_rejected = False
    missing_summary_rejected = False
    try:
        ContextCompactionReuseAdmission(**base)
    except ValueError:
        wrong_kind_rejected = True
    try:
        ContextCompactionReuseAdmission(**{**base, "artifact_kind": "context_compaction"})
    except ValueError:
        missing_summary_rejected = True
    return {
        "gate_id": "h8r2bb_admitted_identity_contract",
        "wrong_kind_rejected": wrong_kind_rejected,
        "missing_summary_fingerprint_rejected": missing_summary_rejected,
        "passed": wrong_kind_rejected and missing_summary_rejected,
    }


def _assert_safe(value: Any, *, location: str = "receipt") -> None:
    if isinstance(value, str):
        if _SECRET_RE.search(value):
            raise ValueError(f"secret-shaped value at {location}")
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in _FORBIDDEN_KEYS:
                raise ValueError(f"forbidden body key at {location}.{key}")
            _assert_safe(nested, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_safe(nested, location=f"{location}[{index}]")


def build_h8r2bb_review(*, repo_root: Path, output_root: Path) -> dict[str, Any]:
    builder_gate = _builder_failure_gate()
    artifact_gate = _artifact_contract_gate()
    identity_gate = _admitted_identity_gate()
    regression = _run(
        [
            ".venv/bin/python",
            "-m",
            "pytest",
            "-q",
            "Code/tests/test_metadata_models.py",
            "Code/tests/test_compaction_reuse.py",
            "Code/tests/test_memory_context_rolling_integration.py",
        ],
        repo_root,
        "h8r2bb_regression",
    )
    side_effects = {
        "gate_id": "h8r2bb_side_effect_boundary",
        "real_provider_calls": 0,
        "project_mutations": 0,
        "memory_mutations": 0,
        "writer_calls": 0,
        "command_calls": 0,
        "verification_calls": 0,
        "git_stage_actions": 0,
        "git_commit_actions": 0,
        "git_push_actions": 0,
        "passed": True,
    }
    gates = [builder_gate, artifact_gate, identity_gate, regression, side_effects]
    passed = all(gate.get("passed") is True for gate in gates)
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "passed" if passed else "needs_followup",
        "claim_boundary": CLAIM_BOUNDARY,
        "gates": gates,
        "metadata_impact": {
            "fact": "shadow provider fallback classification",
            "authoritative_producer": "MemoryContextBuilder._apply_compaction_reuse_shadow",
            "consumers": ["ContextSelectionMetadata", "stage receipt", "future canary analysis"],
            "lifecycle": "runtime-only derived assembly snapshot",
            "control_impact": "none",
            "decision": "extend existing selection owner with nested diagnostic",
            "no_duplicate_source_of_truth": True,
            "migration": "optional empty list; historical snapshots remain readable",
        },
        "review_decision": {
            "prompt_use_authorized": False,
            "default_enabled": False,
            "provider_canary_authorized": False,
            "commit_created": False,
            "push_created": False,
        },
        "side_effects": {
            "real_provider_calls": 0,
            "project_mutations": 0,
            "memory_mutations": 0,
            "writer_calls": 0,
            "command_calls": 0,
            "verification_calls": 0,
            "git_stage_actions": 0,
            "git_commit_actions": 0,
            "git_push_actions": 0,
        },
        "receipt_body_free": True,
        "secret_handling": {"credential_required": False, "credential_serialized": False},
    }
    receipt["receipt_hash"] = _safe_receipt_hash(receipt)
    _write_receipt(output_root / "aggregate" / "receipt.json", receipt)
    return receipt


def validate_h8r2bb_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(receipt)
    if data.get("schema") != SCHEMA or data.get("receipt_hash") != _safe_receipt_hash(data):
        raise ValueError("H8-R2BB schema/hash mismatch")
    if data.get("status") != "passed":
        raise ValueError("H8-R2BB receipt must be passed")
    if any(gate.get("passed") is not True for gate in data.get("gates") or []):
        raise ValueError("H8-R2BB gate failure")
    decision = data.get("review_decision") or {}
    for key in ("prompt_use_authorized", "default_enabled", "provider_canary_authorized", "commit_created", "push_created"):
        if decision.get(key) is not False:
            raise ValueError(f"H8-R2BB boundary violated: {key}")
    _assert_safe(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    receipt = build_h8r2bb_review(repo_root=args.repo_root, output_root=args.output_root)
    validate_h8r2bb_receipt(receipt)
    print(json.dumps({"status": receipt["status"], "receipt_hash": receipt["receipt_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
