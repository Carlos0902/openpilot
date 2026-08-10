"""Run a real-provider read-only paired canary for reusable context compaction.

This stage is deliberately a canary wrapper, not a production prompt-use
transition.  It uses the discovered/persisted binding path from H8-R2BJ,
performs preflight + simulation, then sends two small read-only provider
requests: one raw projection and one explicit reusable projection.  Receipts
store only hashes, IDs, usage, finish reasons and boolean quality facts.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Protocol

from core.config import LLMSettings
from core.llm import LLMClient, LLMMessage, LLMRequest, LLMResponse
from memory.compaction_reuse import (
    build_checkpoint_compaction_reuse_shadow_provider,
    preflight_reusable_compaction_prompt_use,
    simulate_reusable_compaction_prompt_use,
)
from memory.context_assembly import ContextAssembler
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory
from metadata import (
    ContextAssemblyPolicy,
    ContextAssemblyStatus,
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextCompactionBinding,
    DurableArtifactReference,
    ReasoningCapabilityProfileId,
    ReasoningMode,
    ReasoningPolicy,
    UnsupportedReasoningBehavior,
)

from experiments.full_architecture_context_observation.stage_h8r2aj_real_provider import (
    _response_hash,
)
from experiments.full_architecture_context_observation.stage_h8r2at_provider_summary_shadow import (
    _write_receipt,
)
from experiments.full_architecture_context_observation.stage_h8r2av_multi_window_summary_quality import (
    canonical_hash,
)
from experiments.full_architecture_context_observation.stage_h8r2az_reusable_summary_artifact import (
    ZERO_SIDE_EFFECTS,
)
from experiments.full_architecture_context_observation.stage_h8r2bh_builder_sourced_simulation import (
    _candidate_digest,
    _sha256_text,
)
from experiments.full_architecture_context_observation.stage_h8r2bi_token_aware_opt_in_canary import (
    _WhitespaceTokenCounter,
)
from experiments.full_architecture_context_observation.stage_h8r2bj_discovered_binding_opt_in import (
    _optional_hash,
    _select_required_and_recent,
    _semantic_facts_from_binding,
    _shadow_payload_from_raw_context,
    _snapshot_from_context,
)


SCHEMA = "phase-h8r2bk-real-provider-read-only-paired-canary-v1"
CLAIM_BOUNDARY = (
    "real_provider_read_only_paired_canary_no_production_prompt_mutation"
)
FACT_IDS = (
    "scoped_target",
    "forbidden_target",
    "api_rule",
    "validation_command",
)
FACT_CONTRACT_SHA256 = canonical_hash(
    [
        {
            "fact_id": "scoped_target",
            "response_field": "scoped_target",
            "match": "calculator.py",
        },
        {
            "fact_id": "forbidden_target",
            "response_field": "forbidden_target",
            "match": "README.md",
        },
        {
            "fact_id": "api_rule",
            "response_field": "api_rule",
            "match": "preserve api",
        },
        {
            "fact_id": "validation_command",
            "response_field": "validation_command",
            "match": "python -m pytest -q",
        },
    ]
)


class SupportsComplete(Protocol):
    def complete(self, request: LLMRequest, **kwargs: Any) -> LLMResponse: ...


def _seed_dialog(short_memory: ShortMemory, count: int = 9) -> None:
    for index in range(count):
        short_memory.add_message(
            "assistant",
            (
                f"Decision {index}: scoped target is calculator.py. "
                "Constraint: do not modify README.md. "
                "Requirement: preserve existing API behavior. "
                "Validation command must be python -m pytest -q. "
                + (f"low-value-observation-{index} " * 72)
            ),
        )


def _compaction_sink(record: dict[str, Any]) -> DurableArtifactReference:
    artifact_id = f"h8r2bk-artifact-{record['compaction_id']}"
    return DurableArtifactReference(
        artifact_id=artifact_id,
        kind="context_compaction",
        integrity_checksum=canonical_hash(record),
        bytes=len(json.dumps(record, ensure_ascii=False, sort_keys=True)),
    )


def _build_contexts(tmp_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    short_memory = ShortMemory(repo_path=tmp_root / "short")
    _seed_dialog(short_memory)
    system_prompt = (
        "Read-only canary. Required facts: scoped target calculator.py; "
        "do not modify README.md; preserve existing API behavior; "
        "validation command python -m pytest -q."
    )
    raw_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / "raw-memory"),
        max_prompt_chars=24_000,
    )
    raw_context = raw_builder.build(
        "real-provider read-only paired raw",
        include_environment=False,
        limit=9,
        system_prompt=system_prompt,
    )
    compact_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / "compact-memory"),
        max_prompt_chars=4_500,
    )
    compact_builder.set_checkpoint_handlers(compaction_sink=_compaction_sink)
    compact_context = compact_builder.build(
        "real-provider read-only paired compact",
        include_environment=False,
        limit=9,
        system_prompt=system_prompt,
    )
    return raw_context, compact_context


def _summary_candidate_from_binding(
    binding: ContextCompactionBinding,
    *,
    candidate_id: str,
) -> ContextCandidate:
    return ContextCandidate(
        candidate_id=candidate_id,
        kind=ContextCandidateKind.ARTIFACT,
        source_id=binding.record.source_fingerprint,
        content=binding.record.summary,
        retention=ContextCandidateRetention.PREFERRED,
        priority=99,
        source_order=500,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.DERIVED,
        freshness=ContextCandidateFreshness.CURRENT,
        compacted_candidate_ids=list(binding.record.source_candidate_ids),
    )


def _projection_pair(
    *,
    binding: ContextCompactionBinding,
    candidates: list[ContextCandidate],
    policy: ContextAssemblyPolicy,
    summary_candidate_id: str,
    token_counter: Any,
) -> tuple[Any, Any]:
    assembler = ContextAssembler(
        renderer=lambda _payload: "",
        token_counter=token_counter,
    )
    raw = assembler.assemble_candidates(
        list(candidates),
        policy=policy,
        renderer=MemoryContextBuilder._render_candidates,
    )
    reusable = assembler.assemble_candidates(
        [
            *candidates,
            _summary_candidate_from_binding(
                binding,
                candidate_id=summary_candidate_id,
            ),
        ],
        policy=policy,
        renderer=MemoryContextBuilder._render_candidates,
    )
    return raw, reusable


def _settings_from_env() -> LLMSettings:
    credential = (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENPILOT_LLM_API_KEY")
        or ""
    )
    if not credential.strip():
        raise ValueError("DEEPSEEK_API_KEY or OPENPILOT_LLM_API_KEY is required")
    return LLMSettings(
        _env_file=None,
        provider="openai-compatible",
        base_url=os.environ.get("OPENPILOT_LLM_BASE_URL") or "https://api.deepseek.com",
        api_key=credential,
        model=os.environ.get("OPENPILOT_LLM_MODEL") or "deepseek-v4-flash",
        timeout_seconds=45.0,
        temperature=0.0,
        transport_retries=0,
        retry_initial_delay=0.0,
        retry_max_delay=0.0,
        reasoning_capability_profile=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
    )


def _provider_descriptor(settings: LLMSettings | None) -> dict[str, Any]:
    if settings is None:
        return {
            "configured": False,
            "credential_present": False,
            "credential_serialized": False,
        }
    endpoint = str(settings.base_url or "").rstrip("/")
    return {
        "configured": True,
        "provider_name": settings.provider,
        "endpoint_sha256": _sha256_text(endpoint),
        "model": settings.model,
        "reasoning_profile": (
            settings.reasoning_capability_profile.value
            if settings.reasoning_capability_profile is not None
            else ""
        ),
        "reasoning_mode": "disabled",
        "credential_present": bool(settings.api_key and settings.api_key.strip()),
        "credential_serialized": False,
    }


def _arm_request(*, arm: str, projection: str) -> LLMRequest:
    question = (
        "Using only the context projection below, return JSON with exactly these keys: "
        "scoped_target, forbidden_target, api_rule, validation_command, confidence. "
        "Do not propose edits, do not call tools, and do not add extra keys.\n\n"
        f"Context projection ({arm} arm):\n{projection}"
    )
    return LLMRequest(
        messages=[
            LLMMessage(
                role="system",
                content=(
                    "You are a read-only context-quality canary. "
                    "Return only valid JSON. Do not perform actions."
                ),
            ),
            LLMMessage(role="user", content=question),
        ],
        response_format="json_object",
        temperature=0.0,
        max_tokens=180,
        transport_retries=0,
        reasoning_policy=ReasoningPolicy(
            mode=ReasoningMode.DISABLED,
            unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
        ),
    )


def _flat_json_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_flat_json_text(child) for child in value.values())
    if isinstance(value, list):
        return " ".join(_flat_json_text(child) for child in value)
    return str(value or "")


def _parsed_payload(response: LLMResponse) -> Mapping[str, Any]:
    if isinstance(response.parsed_json, Mapping):
        return response.parsed_json
    try:
        parsed = json.loads(response.content)
    except Exception:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _quality(response: LLMResponse) -> dict[str, Any]:
    payload = _parsed_payload(response)
    text = _flat_json_text(payload).casefold()
    key_set = set(payload)
    facts = {
        "scoped_target": "calculator.py" in text,
        "forbidden_target": "readme.md" in text,
        "api_rule": "api" in text
        and ("preserve" in text or "unchanged" in text or "existing" in text),
        "validation_command": "python" in text and "pytest" in text and "-q" in text,
    }
    return {
        "json_object": bool(payload),
        "expected_keys_present": set(
            [
                "scoped_target",
                "forbidden_target",
                "api_rule",
                "validation_command",
                "confidence",
            ]
        ).issubset(key_set),
        "fact_contract_sha256": FACT_CONTRACT_SHA256,
        "fact_coverage": facts,
        "all_facts_covered": all(facts.values()),
    }


def _usage_complete(usage: Mapping[str, Any]) -> bool:
    return all(
        isinstance(usage.get(key), int)
        and not isinstance(usage.get(key), bool)
        and usage.get(key) >= 0
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    )


def _attempt(response: LLMResponse) -> dict[str, Any]:
    usage = dict(response.usage or {})
    return {
        "status": "passed",
        "provider_name": response.provider,
        "model": response.model,
        "finish_reason": response.finish_reason,
        "usage": usage,
        "usage_complete": _usage_complete(usage),
        "reasoning_tokens": (
            usage.get("completion_tokens_details", {}).get("reasoning_tokens")
            if isinstance(usage.get("completion_tokens_details"), Mapping)
            else None
        ),
        "tool_call_count": len(response.tool_calls or []),
        "response_hash": _response_hash(response),
    }


def _failed_attempt(exc: Exception) -> dict[str, Any]:
    return {
        "status": "failed",
        "error_type": type(exc).__name__,
    }


def _arm_result(
    *,
    arm: str,
    projection: str,
    offline_tokens: int | None,
    client: SupportsComplete,
) -> dict[str, Any]:
    request = _arm_request(arm=arm, projection=projection)
    request_hash = canonical_hash(
        {
            "arm": arm,
            "message_roles": [message.role for message in request.messages],
            "projection_sha256": _sha256_text(projection),
            "response_format": request.response_format,
            "max_tokens": request.max_tokens,
            "reasoning_mode": request.reasoning_policy.mode.value,
        }
    )
    try:
        response = client.complete(request, max_retries=1, use_cache=False)
    except Exception as exc:
        return {
            "arm": arm,
            "projection_sha256": _sha256_text(projection),
            "projection_chars": len(projection),
            "offline_projection_tokens": offline_tokens,
            "request_sha256": request_hash,
            "attempt": _failed_attempt(exc),
            "quality": {
                "json_object": False,
                "expected_keys_present": False,
                "fact_contract_sha256": FACT_CONTRACT_SHA256,
                "fact_coverage": {fact_id: False for fact_id in FACT_IDS},
                "all_facts_covered": False,
            },
        }
    return {
        "arm": arm,
        "projection_sha256": _sha256_text(projection),
        "projection_chars": len(projection),
        "offline_projection_tokens": offline_tokens,
        "request_sha256": request_hash,
        "attempt": _attempt(response),
        "quality": _quality(response),
    }


def _real_side_effects(call_count: int) -> dict[str, Any]:
    return dict(ZERO_SIDE_EFFECTS) | {
        "provider_transport_attempted": call_count > 0,
        "provider_calls": call_count,
        "network_side_effects": call_count,
        "used_in_prompt": False,
        "authority_artifact_persisted": False,
    }


def _safe_receipt_hash(receipt: Mapping[str, Any]) -> str:
    return canonical_hash(
        {key: value for key, value in receipt.items() if key != "receipt_hash"}
    )


def _binding_digest(binding: ContextCompactionBinding | None) -> dict[str, Any] | None:
    if binding is None:
        return None
    return {
        "compaction_id": binding.record.compaction_id,
        "source_candidate_ids": list(binding.record.source_candidate_ids),
        "source_fingerprint": binding.record.source_fingerprint,
        "source_binding_hash": binding.source_binding_hash,
        "artifact_id": binding.artifact.artifact_id,
        "artifact_integrity_checksum": binding.artifact.integrity_checksum,
        "projection_fingerprint": _sha256_text(binding.record.summary),
    }


def _status_from_arms(raw: Mapping[str, Any], reusable: Mapping[str, Any]) -> str:
    raw_attempt = raw.get("attempt") if isinstance(raw.get("attempt"), Mapping) else {}
    reusable_attempt = (
        reusable.get("attempt") if isinstance(reusable.get("attempt"), Mapping) else {}
    )
    raw_usage = raw_attempt.get("usage") if isinstance(raw_attempt.get("usage"), Mapping) else {}
    reusable_usage = (
        reusable_attempt.get("usage")
        if isinstance(reusable_attempt.get("usage"), Mapping)
        else {}
    )
    raw_quality = raw.get("quality") if isinstance(raw.get("quality"), Mapping) else {}
    reusable_quality = (
        reusable.get("quality") if isinstance(reusable.get("quality"), Mapping) else {}
    )
    if raw_attempt.get("status") != "passed" or reusable_attempt.get("status") != "passed":
        return "needs_followup"
    if not raw_attempt.get("finish_reason") or not reusable_attempt.get("finish_reason"):
        return "needs_followup"
    if not raw_attempt.get("usage_complete") or not reusable_attempt.get("usage_complete"):
        return "needs_followup"
    if not raw_quality.get("all_facts_covered") or not reusable_quality.get("all_facts_covered"):
        return "needs_followup"
    if int(reusable_usage.get("prompt_tokens") or 0) >= int(raw_usage.get("prompt_tokens") or 0):
        return "needs_followup"
    return "passed"


def run_real_provider_read_only_paired_canary(
    *,
    output_root: Path,
    client_factory: Callable[[LLMSettings], SupportsComplete] | None = None,
    settings_factory: Callable[[], LLMSettings] | None = None,
) -> dict[str, Any]:
    with TemporaryDirectory(prefix="h8r2bk-real-provider-") as tmp:
        raw_context, compact_context = _build_contexts(Path(tmp))
    raw_candidates = [
        ContextCandidate.model_validate(item)
        for item in raw_context.get("selected_context_candidates") or []
    ]
    compact_bindings = [
        ContextCompactionBinding.model_validate(item)
        for item in compact_context.get("context_compactions") or []
    ]
    binding = compact_bindings[0] if compact_bindings else None
    required_ids: list[str] = []
    recent_ids: list[str] = []
    admissions: list[dict[str, Any]] = []
    preflight_payload: dict[str, Any] | None = None
    simulation_payload: dict[str, Any] | None = None
    raw_projection = None
    reusable_projection = None
    setup_reasons: list[str] = []
    counter = _WhitespaceTokenCounter()
    policy = ContextAssemblyPolicy(
        max_prompt_chars=24_000,
        max_prompt_tokens=2_400,
        reserved_prompt_tokens=160,
    )

    if binding is None:
        setup_reasons.append("compact_builder_missing_binding")
    else:
        required_ids, recent_ids = _select_required_and_recent(
            raw_candidates,
            binding=binding,
        )
        snapshot = _snapshot_from_context(
            compact_context,
            context_id="h8r2bk-compact-context",
        )
        provider = build_checkpoint_compaction_reuse_shadow_provider(
            snapshot,
            required_candidate_ids_by_compaction_id={
                binding.record.compaction_id: required_ids,
            },
            recent_suffix_ids_by_compaction_id={
                binding.record.compaction_id: recent_ids,
            },
            session_constraints_hash=_optional_hash(
                raw_context.get("session_constraints_hash")
            ),
        )
        discovered = provider(
            _shadow_payload_from_raw_context(
                raw_context=raw_context,
                candidates=raw_candidates,
                source_fingerprint=binding.record.source_fingerprint,
                source_candidate_ids=binding.record.source_candidate_ids,
            )
        )
        admissions = [admission.model_dump(mode="json") for admission in discovered]
        admitted = next(
            (
                admission
                for admission in discovered
                if admission.status == "admitted"
                and admission.artifact_id == binding.artifact.artifact_id
            ),
            None,
        )
        if admitted is None:
            setup_reasons.append("discovery_not_admitted")
        else:
            preflight = preflight_reusable_compaction_prompt_use(
                binding=binding,
                candidates=raw_candidates,
                policy=policy,
                renderer=MemoryContextBuilder._render_candidates,
                admission=admitted,
                semantic_facts=_semantic_facts_from_binding(binding),
                required_candidate_ids=required_ids,
                recent_suffix_ids=recent_ids,
                expected_artifact_integrity_checksum=binding.artifact.integrity_checksum,
                preflight_id="h8r2bk-real-provider-preflight",
                token_counter=counter,
            )
            simulation = simulate_reusable_compaction_prompt_use(
                binding=binding,
                candidates=raw_candidates,
                policy=policy,
                renderer=MemoryContextBuilder._render_candidates,
                preflight=preflight,
                simulation_id="real_provider_read_only_paired_projection",
                token_counter=counter,
            )
            preflight_payload = preflight.model_dump(mode="json")
            simulation_payload = simulation.model_dump(mode="json")
            if simulation.status != "passed":
                setup_reasons.append("simulation_not_passed")
            else:
                raw_projection, reusable_projection = _projection_pair(
                    binding=binding,
                    candidates=raw_candidates,
                    policy=policy,
                    summary_candidate_id=simulation.summary_candidate_id,
                    token_counter=counter,
                )
                if (
                    raw_projection.selection.assembly_status
                    != ContextAssemblyStatus.READY
                    or reusable_projection.selection.assembly_status
                    != ContextAssemblyStatus.READY
                ):
                    setup_reasons.append("projection_not_ready")
                if _sha256_text(raw_projection.prompt_text) != simulation.raw_prompt_hash:
                    setup_reasons.append("raw_projection_hash_mismatch")
                if (
                    _sha256_text(reusable_projection.prompt_text)
                    != simulation.reusable_prompt_hash
                ):
                    setup_reasons.append("reusable_projection_hash_mismatch")

    settings: LLMSettings | None = None
    provider_descriptor = _provider_descriptor(None)
    arms: dict[str, Any] = {}
    provider_call_count = 0
    if not setup_reasons:
        try:
            settings = (settings_factory or _settings_from_env)()
            provider_descriptor = _provider_descriptor(settings)
        except Exception as exc:
            setup_reasons.append(f"settings_unavailable:{type(exc).__name__}")

    if not setup_reasons and raw_projection is not None and reusable_projection is not None:
        client = (
            client_factory(settings)
            if client_factory is not None
            else LLMClient(settings=settings, enable_cache=False)
        )
        arms["raw"] = _arm_result(
            arm="raw",
            projection=raw_projection.prompt_text,
            offline_tokens=raw_projection.selection.final_prompt_tokens,
            client=client,
        )
        provider_call_count += 1
        arms["reusable"] = _arm_result(
            arm="reusable",
            projection=reusable_projection.prompt_text,
            offline_tokens=reusable_projection.selection.final_prompt_tokens,
            client=client,
        )
        provider_call_count += 1

    status = (
        "blocked"
        if any(reason.startswith("settings_unavailable") for reason in setup_reasons)
        else (
            _status_from_arms(arms.get("raw", {}), arms.get("reusable", {}))
            if arms
            else "needs_followup"
        )
    )
    if setup_reasons and status != "blocked":
        status = "needs_followup"

    receipt = {
        "schema": SCHEMA,
        "status": status,
        "claim_boundary": CLAIM_BOUNDARY,
        "setup_reasons": setup_reasons,
        "provider_descriptor": provider_descriptor,
        "raw_context_request_hash": raw_context.get("context_request_hash"),
        "compact_context_request_hash": compact_context.get("context_request_hash"),
        "raw_builder_projection_sha256": _sha256_text(
            str(raw_context.get("prompt_text") or "")
        ),
        "compact_builder_projection_sha256": _sha256_text(
            str(compact_context.get("prompt_text") or "")
        ),
        "selected_candidate_ids": [
            candidate.candidate_id for candidate in raw_candidates
        ],
        "candidate_digests": [_candidate_digest(candidate) for candidate in raw_candidates],
        "binding_digest": _binding_digest(binding),
        "required_candidate_ids": required_ids,
        "recent_suffix_ids": recent_ids,
        "admissions": admissions,
        "preflight": preflight_payload,
        "simulation": simulation_payload,
        "arms": arms,
        "invariants": {
            "raw_candidates_available": bool(raw_candidates),
            "compact_builder_produced_one_binding": len(compact_bindings) == 1,
            "discovery_admitted_binding": any(
                admission.get("status") == "admitted" for admission in admissions
            ),
            "discovery_is_shadow_only": all(
                admission.get("used_in_prompt") is False for admission in admissions
            ),
            "preflight_passed": (
                preflight_payload or {}
            ).get("status") == "passed",
            "simulation_passed": (
                simulation_payload or {}
            ).get("status") == "passed",
            "projection_hashes_match_simulation": (
                not setup_reasons
                or (
                    raw_projection is not None
                    and reusable_projection is not None
                    and _sha256_text(raw_projection.prompt_text)
                    == (simulation_payload or {}).get("raw_prompt_hash")
                    and _sha256_text(reusable_projection.prompt_text)
                    == (simulation_payload or {}).get("reusable_prompt_hash")
                )
            ),
            "provider_usage_complete": bool(arms)
            and all(
                ((arm.get("attempt") or {}).get("usage_complete") is True)
                for arm in arms.values()
            ),
            "provider_finish_reason_present": bool(arms)
            and all(
                bool((arm.get("attempt") or {}).get("finish_reason"))
                for arm in arms.values()
            ),
            "provider_prompt_tokens_reduced": bool(arms)
            and int(
                ((arms.get("reusable") or {}).get("attempt") or {})
                .get("usage", {})
                .get("prompt_tokens", 0)
            )
            < int(
                ((arms.get("raw") or {}).get("attempt") or {})
                .get("usage", {})
                .get("prompt_tokens", 0)
            ),
            "quality_facts_covered": bool(arms)
            and all(
                ((arm.get("quality") or {}).get("all_facts_covered") is True)
                for arm in arms.values()
            ),
        },
        "side_effects": _real_side_effects(provider_call_count),
        "secret_handling": {
            "credential_present": bool(
                settings is not None and settings.api_key and settings.api_key.strip()
            ),
            "serialized": False,
        },
    }
    receipt["receipt_hash"] = _safe_receipt_hash(receipt)
    _write_receipt(output_root / "aggregate" / "receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_real_provider_read_only_paired_canary(output_root=args.output_root)
    print(
        json.dumps(
            {
                "status": result["status"],
                "receipt_hash": result["receipt_hash"],
                "invariants": result["invariants"],
                "setup_reasons": result["setup_reasons"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
