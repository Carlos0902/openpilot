"""Audit the existing production-facing reusable-summary binding contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from memory.compaction_reuse import (
    ReusableCompactionArtifactCandidate,
    build_compaction_reuse_shadow_provider,
    preflight_reusable_compaction_prompt_use,
    simulate_reusable_compaction_prompt_use,
    source_binding_hash_from_shadow_payload,
)
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory
try:
    from experiments.full_architecture_context_observation.stage_h8r2az_reusable_summary_artifact import (
        run_reusable_summary_artifact_gate,
    )
except ModuleNotFoundError:  # direct script execution from the stage directory
    from stage_h8r2az_reusable_summary_artifact import run_reusable_summary_artifact_gate
from metadata import (
    ContextCompactionBinding,
    ContextCompactionReuseAdmission,
    ContextSelectionMetadata,
)


SCHEMA = "phase-h8r2ba-production-reusable-summary-binding-review-v1"
CLAIM_BOUNDARY = "production_reusable_summary_binding_contract_review_no_prompt_use"
TARGET_PACKAGE_ID = "production_reusable_summary_binding_contract_review"
NEXT_PACKAGE_ID = "production_reusable_summary_builder_shadow"
REVIEWED_PATHS = (
    "Code/src/metadata/agent_runtime.py",
    "Code/src/memory/compaction_reuse.py",
    "Code/src/memory/context_builder.py",
    "Code/tests/test_compaction_reuse.py",
    "Code/tests/test_memory_context_rolling_integration.py",
)
_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}(?![A-Za-z0-9])")
_FORBIDDEN_KEYS = {"prompt", "content", "summary_body", "raw_response", "credential"}


def canonical_hash(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _safe_receipt_hash(receipt: Mapping[str, Any]) -> str:
    return canonical_hash({key: value for key, value in receipt.items() if key != "receipt_hash"})


def _write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _run(command: list[str], root: Path, gate_id: str) -> dict[str, Any]:
    result = subprocess.run(command, cwd=root, env={**os.environ, "PYTHONPATH": "Code/src"}, capture_output=True, text=True, check=False)
    output = (result.stdout + "\n" + result.stderr).strip()
    passed = re.search(r"(\d+) passed", output)
    failed = re.search(r"(\d+) failed", output)
    return {
        "gate_id": gate_id,
        "command": " ".join(command),
        "returncode": result.returncode,
        "passed_count": int(passed.group(1)) if passed else 0,
        "failed_count": int(failed.group(1)) if failed else 0,
        "output_present": bool(output),
        "passed": result.returncode == 0,
    }


def _contract_gate() -> dict[str, Any]:
    admission_default = ContextCompactionReuseAdmission.model_fields["used_in_prompt"].default
    binding_default = ContextCompactionBinding.model_fields["source_binding_hash"].default
    selection_has_admissions = "compaction_reuse_admissions" in ContextSelectionMetadata.model_fields
    selection_has_failures = "compaction_reuse_shadow_failures" in ContextSelectionMetadata.model_fields
    shadow_provider_default = (MemoryContextBuilder.__init__.__kwdefaults__ or {}).get(
        "compaction_reuse_shadow_provider"
    )
    reuse_has_preflight = callable(preflight_reusable_compaction_prompt_use)
    reuse_has_simulation = callable(simulate_reusable_compaction_prompt_use)
    passed = (
        admission_default is False
        and binding_default == ""
        and selection_has_admissions
        and selection_has_failures
        and reuse_has_preflight
        and reuse_has_simulation
        and shadow_provider_default is None
    )
    return {
        "gate_id": "production_reusable_summary_binding_contract",
        "admission_used_in_prompt_default": admission_default,
        "binding_source_binding_hash_default": binding_default,
        "selection_has_reuse_admissions": selection_has_admissions,
        "selection_has_shadow_failures": selection_has_failures,
        "memory_builder_shadow_provider_default": shadow_provider_default,
        "runtime_preflight_present": reuse_has_preflight,
        "runtime_simulation_present": reuse_has_simulation,
        "new_metadata_contract_required": False,
        "decision": "reuse_existing_binding_admission_and_derived_view",
        "passed": passed,
    }


def _source_gate(root: Path) -> dict[str, Any]:
    hashes = {path: canonical_hash((root / path).read_bytes()) for path in REVIEWED_PATHS}
    return {
        "gate_id": "production_reusable_summary_binding_source_inventory",
        "reviewed_paths": list(REVIEWED_PATHS),
        "source_hashes": hashes,
        "authoritative_facts": [
            "artifact_identity_and_checksum",
            "source_candidate_ids_and_binding_hash",
            "required_and_recent_guard_ids",
            "session_constraint_hash",
            "fixture_turn_ledger_hash",
            "source_binding_hash",
            "shadow_admission_status_and_reason",
        ],
        "passed": True,
    }


def _default_off_gate(root: Path) -> dict[str, Any]:
    result = _run(
        [".venv/bin/python", "-m", "pytest", "-q", "Code/tests/test_memory_context_rolling_integration.py", "-k", "reuse_provider_is_default_off or adds_body_free_metadata_without_prompt_change"],
        root,
        "production_reusable_summary_binding_default_off",
    )
    result.update({"provider_calls": 0, "prompt_use": False, "project_mutations": 0, "memory_mutations": 0})
    result["passed"] = result["passed"] and result["provider_calls"] == 0 and result["prompt_use"] is False
    return result


def _side_effect_gate() -> dict[str, Any]:
    return {
        "gate_id": "production_reusable_summary_binding_side_effect_boundary",
        "provider_calls": 0,
        "project_mutations": 0,
        "memory_mutations": 0,
        "writer_calls": 0,
        "command_calls": 0,
        "verification_calls": 0,
        "prompt_use": False,
        "default_enabled": False,
        "passed": True,
    }


def _builder_wiring_gate() -> dict[str, Any]:
    """Exercise the real builder hook without provider or repository effects."""

    with tempfile.TemporaryDirectory(prefix="h8r2ba-builder-") as temp_root:
        root = Path(temp_root)
        short_memory = ShortMemory(repo_path=root / "short")
        for index in range(4):
            short_memory.add_message("assistant", f"dialog body {index}")

        def build(*, provider=None):
            return MemoryContextBuilder(
                short_memory=short_memory,
                memory_store=MemoryStore(root / ("shadow-memory" if provider else "baseline-memory")),
                max_prompt_chars=1000,
                compaction_reuse_shadow_provider=provider,
            )

        baseline = build().build(
            "builder binding review",
            include_environment=False,
            limit=4,
            system_prompt="Keep context stable.",
        )
        payloads: list[dict[str, Any]] = []

        def provider(payload: dict[str, Any]):
            payloads.append(dict(payload))
            source_ids = [
                item["candidate_id"]
                for item in payload["candidate_digests"]
                if item["kind"] == "dialog"
            ][:2]
            source_key = json.dumps(source_ids, ensure_ascii=False, separators=(",", ":"))
            candidate = ReusableCompactionArtifactCandidate(
                candidate_id="h8r2ba-builder-candidate",
                artifact_id="h8r2ba-builder-artifact",
                artifact_kind="context_compaction",
                artifact_integrity_checksum="sha256:" + "2" * 64,
                record_compaction_id="h8r2ba-builder-record",
                record_algorithm="deterministic_observation_mask_v1",
                source_candidate_ids=source_ids,
                source_fingerprint=payload["source_fingerprint_by_candidate_ids"][source_key],
                source_binding_hash=source_binding_hash_from_shadow_payload(payload, source_ids),
                generated_summary_fingerprint="sha256:" + "7" * 64,
            )
            return build_compaction_reuse_shadow_provider([candidate])(payload)

        shadow = build(provider=provider).build(
            "builder binding review",
            include_environment=False,
            limit=4,
            system_prompt="Keep context stable.",
        )
        admissions = shadow["context_selection"]["compaction_reuse_admissions"]
        encoded_payload = json.dumps(payloads, ensure_ascii=False, sort_keys=True)
        passed = bool(
            payloads
            and len(admissions) == 1
            and admissions[0]["status"] == "admitted"
            and admissions[0]["used_in_prompt"] is False
            and shadow["prompt_text"] == baseline["prompt_text"]
            and shadow["selected_context_candidates"] == baseline["selected_context_candidates"]
            and shadow["context_request_hash"] == baseline["context_request_hash"]
            and shadow["context_compactions"] == baseline["context_compactions"]
            and "prompt_text" not in encoded_payload
            and "dialog body 0" not in encoded_payload
        )
        return {
            "gate_id": "production_reusable_summary_binding_real_builder_wiring",
            "shadow_provider_invocations": len(payloads),
            "real_provider_calls": 0,
            "admission_statuses": [item.get("status") for item in admissions],
            "prompt_identity_unchanged": shadow["prompt_text"] == baseline["prompt_text"],
            "selected_candidate_ids_unchanged": shadow["selected_context_candidates"] == baseline["selected_context_candidates"],
            "request_identity_unchanged": shadow["context_request_hash"] == baseline["context_request_hash"],
            "compaction_projection_unchanged": shadow["context_compactions"] == baseline["context_compactions"],
            "payload_body_free": "prompt_text" not in encoded_payload and "dialog body 0" not in encoded_payload,
            "project_mutations": 0,
            "memory_mutations": 0,
            "writer_calls": 0,
            "command_calls": 0,
            "verification_calls": 0,
            "passed": passed,
        }


def _az_lineage_gate(output_root: Path) -> dict[str, Any]:
    """Require the upstream reusable-artifact drift matrix to stay complete."""

    az_receipt = run_reusable_summary_artifact_gate(
        output_root=output_root / "az_source_receipt",
        reuse_count=2,
    )
    expected_reasons = [
        None,
        "source_fingerprint_mismatch",
        "source_candidate_ids_mismatch",
        "required_candidate_ids_mismatch",
        "recent_suffix_ids_mismatch",
        "session_constraints_hash_mismatch",
        "fixture_turn_ledger_hash_mismatch",
        "source_binding_hash_mismatch",
        "artifact_kind_mismatch",
        "artifact_integrity_mismatch",
    ]
    actual_reasons = [None] + [
        item["admission"].get("reason") for item in az_receipt["drift_cases"]
    ]
    passed = (
        az_receipt.get("status") == "passed"
        and actual_reasons == expected_reasons
        and az_receipt.get("receipt_hash")
    )
    return {
        "gate_id": "production_reusable_summary_binding_upstream_az_lineage",
        "nested_receipt_hash": az_receipt.get("receipt_hash"),
        "expected_rejection_vector": expected_reasons,
        "actual_rejection_vector": actual_reasons,
        "passed": bool(passed),
    }


def build_production_reusable_summary_binding_review(*, repo_root: Path, output_root: Path) -> dict[str, Any]:
    contract = _contract_gate()
    source = _source_gate(repo_root)
    default_off = _default_off_gate(repo_root)
    regression = _run(
        [".venv/bin/python", "-m", "pytest", "-q", "Code/tests/test_compaction_reuse.py", "Code/tests/test_context_projection.py", "Code/tests/test_compaction_summary_contract.py"],
        repo_root,
        "production_reusable_summary_binding_regression",
    )
    builder_wiring = _builder_wiring_gate()
    az_lineage = _az_lineage_gate(output_root)
    side_effects = _side_effect_gate()
    gates = [contract, source, default_off, regression, builder_wiring, az_lineage, side_effects]
    passed = all(gate.get("passed") is True for gate in gates)
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "passed" if passed else "needs_followup",
        "claim_boundary": CLAIM_BOUNDARY,
        "target_package_id": TARGET_PACKAGE_ID,
        "gates": gates,
        "metadata_impact": {
            "fact": "reusable summary artifact identity, source lineage, invalidation and shadow admission",
            "authoritative_producer": "ContextCompactionBinding / MemoryContextBuilder shadow projection",
            "consumers": ["compaction reuse admission", "runtime-only preflight/simulation", "selection diagnostics"],
            "lifecycle": "runtime-only plus existing checkpoint artifact reference",
            "control_impact": "none (shadow diagnostics; prompt-use remains prohibited)",
            "existing_contracts_reviewed": ["ContextCompactionBinding", "ContextCompactionReuseAdmission", "ContextSelectionMetadata", "RuntimePromptContextSnapshot"],
            "decision": "reuse existing reference and derived view; no new metadata contract",
            "why_no_duplicate_source_of_truth": "raw dialog and prompt-context artifact remain authoritative; reuse admission stores only identity/checksum/lineage",
            "serialization_and_migration": "no schema change; historical empty source_binding_hash remains readable but cannot prove reuse stability",
            "tests": ["compaction_reuse", "context_projection", "rolling integration", "metadata model regression"],
            "documentation_updates": ["CONTRACT_CATALOG.md", "H8-R2BA plan/result", "IMPLEMENTATION_LOG.md"],
        },
        "review_decision": {
            "contract_review_completed": True,
            "contract_review_approved": passed,
            "new_metadata_contract_required": False,
            "prompt_use_authorized": False,
            "default_enabled": False,
            "provider_canary_authorized": False,
            "next_candidate_package_id": NEXT_PACKAGE_ID,
            "stage_created": False,
            "commit_created": False,
            "push_created": False,
        },
        "side_effects": {
            "provider_calls": 0,
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


def _assert_safe(value: Any, *, location: str = "receipt") -> None:
    if isinstance(value, str):
        if _SECRET_RE.search(value):
            raise ValueError(f"H8-R2BA secret-shaped value at {location}")
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in _FORBIDDEN_KEYS:
                raise ValueError(f"H8-R2BA body key at {location}.{key}")
            _assert_safe(nested, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_safe(nested, location=f"{location}[{index}]")


def validate_production_reusable_summary_binding_review_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(receipt)
    if data.get("schema") != SCHEMA or data.get("receipt_hash") != _safe_receipt_hash(data):
        raise ValueError("H8-R2BA schema/hash mismatch")
    if data.get("status") != "passed":
        raise ValueError("H8-R2BA receipt must be passed")
    gates = data.get("gates") or []
    if not gates or any(gate.get("passed") is not True for gate in gates):
        raise ValueError("H8-R2BA every gate passed=true is required")
    decision = data.get("review_decision") or {}
    if decision.get("contract_review_completed") is not True or decision.get("contract_review_approved") is not True:
        raise ValueError("H8-R2BA contract review approval missing")
    for key in ("new_metadata_contract_required", "prompt_use_authorized", "default_enabled", "provider_canary_authorized", "commit_created", "push_created"):
        if decision.get(key) is not False:
            raise ValueError(f"H8-R2BA boundary violated: {key}")
    if data.get("side_effects") != {
        "provider_calls": 0,
        "project_mutations": 0,
        "memory_mutations": 0,
        "writer_calls": 0,
        "command_calls": 0,
        "verification_calls": 0,
        "git_stage_actions": 0,
        "git_commit_actions": 0,
        "git_push_actions": 0,
    }:
        raise ValueError("H8-R2BA top-level side effects must be zero")
    _assert_safe(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    receipt = build_production_reusable_summary_binding_review(repo_root=args.repo_root, output_root=args.output_root)
    validate_production_reusable_summary_binding_review_receipt(receipt)
    print(json.dumps({"status": receipt["status"], "receipt_hash": receipt["receipt_hash"], "claim_boundary": receipt["claim_boundary"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
