"""Run a DeepSeek read-only confirmation matrix for reusable compaction.

H8-R2BK proved one real-provider read-only paired canary.  This stage repeats
the same discovered-binding -> preflight -> simulation -> explicit opt-in
projection chain across three deterministic read-only fact contracts.  It is
still experiment-only and never mutates production builder prompt behavior.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
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
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.short_memory import ShortMemory
from metadata import (
    ContextAssemblyPolicy,
    ContextAssemblyStatus,
    ContextCandidate,
    ContextCompactionBinding,
    DurableArtifactReference,
    ReasoningMode,
    ReasoningPolicy,
    UnsupportedReasoningBehavior,
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
from experiments.full_architecture_context_observation.stage_h8r2bk_real_provider_read_only_paired_canary import (
    _binding_digest,
    _failed_attempt,
    _projection_pair,
    _provider_descriptor,
    _response_hash,
    _safe_receipt_hash,
    _settings_from_env,
    _usage_complete,
)


SCHEMA = "phase-h8r2bl-read-only-confirmation-matrix-v1"
CLAIM_BOUNDARY = (
    "real_provider_read_only_confirmation_matrix_no_production_prompt_mutation"
)


class SupportsComplete(Protocol):
    def complete(self, request: LLMRequest, **kwargs: Any) -> LLMResponse: ...


@dataclass(frozen=True)
class FactSpec:
    fact_id: str
    response_key: str
    marker: str
    required_patterns: tuple[str, ...]


@dataclass(frozen=True)
class MatrixCase:
    case_id: str
    facts: tuple[FactSpec, ...]
    message_count: int = 10
    low_value_repetitions: int = 96
    raw_max_chars: int = 120_000
    compact_max_chars: int = 4_500
    max_prompt_tokens: int = 30_000

    @property
    def contract_hash(self) -> str:
        return canonical_hash(
            [
                {
                    "fact_id": fact.fact_id,
                    "response_key": fact.response_key,
                    "marker_sha256": _sha256_text(fact.marker),
                    "pattern_sha256": [
                        _sha256_text(pattern) for pattern in fact.required_patterns
                    ],
                }
                for fact in self.facts
            ]
        )


MATRIX_CASES: tuple[MatrixCase, ...] = (
    MatrixCase(
        case_id="scope_validation",
        facts=(
            FactSpec(
                fact_id="scoped_target",
                response_key="scoped_target",
                marker="scoped_target=calculator.py",
                required_patterns=("calculator.py",),
            ),
            FactSpec(
                fact_id="forbidden_target",
                response_key="forbidden_target",
                marker="forbidden_target=README.md",
                required_patterns=("readme.md",),
            ),
            FactSpec(
                fact_id="api_rule",
                response_key="api_rule",
                marker="api_rule=preserve_api",
                required_patterns=("preserve", "api"),
            ),
            FactSpec(
                fact_id="validation_command",
                response_key="validation_command",
                marker="validation_command=pytest_q",
                required_patterns=("pytest", "q"),
            ),
        ),
    ),
    MatrixCase(
        case_id="permission_boundary",
        facts=(
            FactSpec(
                fact_id="allowed_tool",
                response_key="allowed_tool",
                marker="allowed_tool=file_reader",
                required_patterns=("file_reader",),
            ),
            FactSpec(
                fact_id="forbidden_tool",
                response_key="forbidden_tool",
                marker="forbidden_tool=file_patch_writer",
                required_patterns=("file_patch_writer",),
            ),
            FactSpec(
                fact_id="execution_mode",
                response_key="execution_mode",
                marker="execution_mode=real_read_only",
                required_patterns=("read", "only"),
            ),
            FactSpec(
                fact_id="mutation_policy",
                response_key="mutation_policy",
                marker="mutation_policy=no_mutation",
                required_patterns=("no", "mutation"),
            ),
        ),
        message_count=11,
        low_value_repetitions=112,
    ),
    MatrixCase(
        case_id="completion_evidence_boundary",
        facts=(
            FactSpec(
                fact_id="evidence_output",
                response_key="evidence",
                marker="evidence=eids_only",
                required_patterns=("eids", "only"),
            ),
            FactSpec(
                fact_id="completion_rule",
                response_key="done_rule",
                marker="done_rule=suspicious_reject",
                required_patterns=("suspicious", "reject"),
            ),
            FactSpec(
                fact_id="source_anchor",
                response_key="source",
                marker="source=dialog_constraint",
                required_patterns=("dialog", "constraint"),
            ),
            FactSpec(
                fact_id="final_state",
                response_key="final",
                marker="final=verify_required",
                required_patterns=("verify", "required"),
            ),
        ),
        message_count=12,
        low_value_repetitions=128,
    ),
)


@dataclass
class PreparedCase:
    case: MatrixCase
    raw_context: dict[str, Any]
    compact_context: dict[str, Any]
    raw_candidates: list[ContextCandidate]
    binding: ContextCompactionBinding | None
    required_ids: list[str]
    recent_ids: list[str]
    admissions: list[dict[str, Any]]
    preflight: dict[str, Any] | None
    simulation: dict[str, Any] | None
    raw_projection_text: str
    reusable_projection_text: str
    raw_projection_tokens: int | None
    reusable_projection_tokens: int | None
    setup_reasons: list[str]


def _case_by_id(case_id: str) -> MatrixCase:
    for case in MATRIX_CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(case_id)


def _seed_dialog(short_memory: ShortMemory, case: MatrixCase) -> None:
    fact_markers = [fact.marker for fact in case.facts]
    compactable_count = max(4, case.message_count - 2)
    for index in range(case.message_count):
        if index >= compactable_count:
            signal = (
                f"Recent status {index}: read-only observation continues; "
                "no replacement facts in this suffix."
            )
        elif index == 0:
            signal = fact_markers[0]
        elif index == 1:
            signal = fact_markers[1]
        elif (index - 2) % 2 == 0:
            signal = fact_markers[2]
        else:
            signal = fact_markers[3]
        repetitions = 8 if index >= compactable_count else case.low_value_repetitions
        short_memory.add_message(
            "assistant",
            (
                f"Decision {index} for {case.case_id}: {signal}. "
                + (f"low-value-{case.case_id}-{index} " * repetitions)
            ),
        )


def _compaction_sink_for(case: MatrixCase) -> Callable[[dict[str, Any]], DurableArtifactReference]:
    def sink(record: dict[str, Any]) -> DurableArtifactReference:
        artifact_id = f"h8r2bl-{case.case_id}-{record['compaction_id']}"
        return DurableArtifactReference(
            artifact_id=artifact_id,
            kind="context_compaction",
            integrity_checksum=canonical_hash(record),
            bytes=len(json.dumps(record, ensure_ascii=False, sort_keys=True)),
        )

    return sink


def _build_contexts(case: MatrixCase, tmp_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    short_memory = ShortMemory(repo_path=tmp_root / f"{case.case_id}-short")
    _seed_dialog(short_memory, case)
    system_prompt = (
        "Read-only confirmation matrix canary. Extract exact marker values from "
        "the context projection only. Do not propose edits, call tools, run "
        "commands, or mutate state."
    )
    raw_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / f"{case.case_id}-raw-memory"),
        max_prompt_chars=case.raw_max_chars,
    )
    raw_context = raw_builder.build(
        f"h8r2bl {case.case_id} raw",
        include_environment=False,
        limit=case.message_count,
        system_prompt=system_prompt,
    )
    compact_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / f"{case.case_id}-compact-memory"),
        max_prompt_chars=case.compact_max_chars,
    )
    compact_builder.set_checkpoint_handlers(compaction_sink=_compaction_sink_for(case))
    compact_context = compact_builder.build(
        f"h8r2bl {case.case_id} compact",
        include_environment=False,
        limit=case.message_count,
        system_prompt=system_prompt,
    )
    return raw_context, compact_context


def _prepare_case(case: MatrixCase, *, tmp_root: Path) -> PreparedCase:
    raw_context, compact_context = _build_contexts(case, tmp_root)
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
    raw_projection_text = ""
    reusable_projection_text = ""
    raw_projection_tokens: int | None = None
    reusable_projection_tokens: int | None = None
    setup_reasons: list[str] = []
    counter = _WhitespaceTokenCounter()
    policy = ContextAssemblyPolicy(
        max_prompt_chars=case.raw_max_chars,
        max_prompt_tokens=case.max_prompt_tokens,
        reserved_prompt_tokens=256,
    )

    if not raw_candidates:
        setup_reasons.append("raw_candidates_missing")
    if len(compact_bindings) != 1 or binding is None:
        setup_reasons.append("compact_builder_missing_single_binding")
    else:
        required_ids, recent_ids = _select_required_and_recent(
            raw_candidates,
            binding=binding,
        )
        snapshot = _snapshot_from_context(
            compact_context,
            context_id=f"h8r2bl-{case.case_id}-compact-context",
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
                preflight_id=f"h8r2bl-{case.case_id}-preflight",
                token_counter=counter,
            )
            simulation = simulate_reusable_compaction_prompt_use(
                binding=binding,
                candidates=raw_candidates,
                policy=policy,
                renderer=MemoryContextBuilder._render_candidates,
                preflight=preflight,
                simulation_id=f"h8r2bl-{case.case_id}-simulation",
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
                raw_projection_text = raw_projection.prompt_text
                reusable_projection_text = reusable_projection.prompt_text
                raw_projection_tokens = raw_projection.selection.final_prompt_tokens
                reusable_projection_tokens = reusable_projection.selection.final_prompt_tokens
                if (
                    raw_projection.selection.assembly_status
                    != ContextAssemblyStatus.READY
                    or reusable_projection.selection.assembly_status
                    != ContextAssemblyStatus.READY
                ):
                    setup_reasons.append("projection_not_ready")
                if _sha256_text(raw_projection_text) != simulation.raw_prompt_hash:
                    setup_reasons.append("raw_projection_hash_mismatch")
                if _sha256_text(reusable_projection_text) != simulation.reusable_prompt_hash:
                    setup_reasons.append("reusable_projection_hash_mismatch")

    return PreparedCase(
        case=case,
        raw_context=raw_context,
        compact_context=compact_context,
        raw_candidates=raw_candidates,
        binding=binding,
        required_ids=required_ids,
        recent_ids=recent_ids,
        admissions=admissions,
        preflight=preflight_payload,
        simulation=simulation_payload,
        raw_projection_text=raw_projection_text,
        reusable_projection_text=reusable_projection_text,
        raw_projection_tokens=raw_projection_tokens,
        reusable_projection_tokens=reusable_projection_tokens,
        setup_reasons=setup_reasons,
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


def _quality(case: MatrixCase, response: LLMResponse) -> dict[str, Any]:
    payload = _parsed_payload(response)
    full_text = _flat_json_text(payload).casefold()
    key_set = set(payload)
    fact_coverage: dict[str, bool] = {}
    for fact in case.facts:
        field_text = str(payload.get(fact.response_key) or "").casefold()
        searchable = field_text or full_text
        fact_coverage[fact.fact_id] = all(
            pattern.casefold() in searchable for pattern in fact.required_patterns
        )
    return {
        "json_object": bool(payload),
        "expected_keys_present": {fact.response_key for fact in case.facts}.issubset(
            key_set
        ),
        "fact_contract_sha256": case.contract_hash,
        "fact_ids": [fact.fact_id for fact in case.facts],
        "fact_coverage": fact_coverage,
        "all_facts_covered": all(fact_coverage.values()),
    }


def _arm_request(*, prepared: PreparedCase, arm: str, projection: str) -> LLMRequest:
    keys = ", ".join(fact.response_key for fact in prepared.case.facts)
    question = (
        "Using only the context projection below, return JSON with exactly these "
        f"keys: {keys}. For each key, copy the exact marker value found in the "
        "context. Do not add keys, do not propose edits, do not call tools, and "
        "do not mutate anything.\n\n"
        f"Context projection ({prepared.case.case_id}/{arm} arm):\n{projection}"
    )
    return LLMRequest(
        messages=[
            LLMMessage(
                role="system",
                content=(
                    "You are a read-only context confirmation canary. "
                    "Return only valid JSON."
                ),
            ),
            LLMMessage(role="user", content=question),
        ],
        response_format="json_object",
        temperature=0.0,
        max_tokens=220,
        transport_retries=0,
        trace_info={
            "stage": "h8r2bl",
            "case_id": prepared.case.case_id,
            "arm": arm,
        },
        reasoning_policy=ReasoningPolicy(
            mode=ReasoningMode.DISABLED,
            unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
        ),
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


def _arm_result(
    *,
    prepared: PreparedCase,
    arm: str,
    projection: str,
    offline_tokens: int | None,
    client: SupportsComplete,
) -> dict[str, Any]:
    request = _arm_request(prepared=prepared, arm=arm, projection=projection)
    request_hash = canonical_hash(
        {
            "case_id": prepared.case.case_id,
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
                "fact_contract_sha256": prepared.case.contract_hash,
                "fact_ids": [fact.fact_id for fact in prepared.case.facts],
                "fact_coverage": {
                    fact.fact_id: False for fact in prepared.case.facts
                },
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
        "quality": _quality(prepared.case, response),
    }


def _case_status(case_receipt: Mapping[str, Any]) -> str:
    setup_reasons = list(case_receipt.get("setup_reasons") or [])
    if setup_reasons:
        return "needs_followup"
    arms = case_receipt.get("arms") if isinstance(case_receipt.get("arms"), Mapping) else {}
    raw = arms.get("raw") if isinstance(arms.get("raw"), Mapping) else {}
    reusable = arms.get("reusable") if isinstance(arms.get("reusable"), Mapping) else {}
    if not raw or not reusable:
        return "needs_followup"
    for arm in (raw, reusable):
        attempt = arm.get("attempt") if isinstance(arm.get("attempt"), Mapping) else {}
        quality = arm.get("quality") if isinstance(arm.get("quality"), Mapping) else {}
        if attempt.get("status") != "passed":
            return "needs_followup"
        if not attempt.get("finish_reason") or attempt.get("usage_complete") is not True:
            return "needs_followup"
        if quality.get("all_facts_covered") is not True:
            return "needs_followup"
    raw_usage = raw.get("attempt", {}).get("usage", {})
    reusable_usage = reusable.get("attempt", {}).get("usage", {})
    if int(reusable_usage.get("prompt_tokens") or 0) >= int(
        raw_usage.get("prompt_tokens") or 0
    ):
        return "needs_followup"
    return "passed"


def _case_receipt(prepared: PreparedCase, *, arms: Mapping[str, Any] | None) -> dict[str, Any]:
    case_receipt = {
        "case_id": prepared.case.case_id,
        "status": "needs_followup",
        "fact_contract_sha256": prepared.case.contract_hash,
        "fact_ids": [fact.fact_id for fact in prepared.case.facts],
        "setup_reasons": list(prepared.setup_reasons),
        "raw_context_request_hash": prepared.raw_context.get("context_request_hash"),
        "compact_context_request_hash": prepared.compact_context.get("context_request_hash"),
        "raw_builder_projection_sha256": _sha256_text(
            str(prepared.raw_context.get("prompt_text") or "")
        ),
        "compact_builder_projection_sha256": _sha256_text(
            str(prepared.compact_context.get("prompt_text") or "")
        ),
        "selected_candidate_ids": [
            candidate.candidate_id for candidate in prepared.raw_candidates
        ],
        "candidate_digests": [
            _candidate_digest(candidate) for candidate in prepared.raw_candidates
        ],
        "binding_digest": _binding_digest(prepared.binding),
        "required_candidate_ids": list(prepared.required_ids),
        "recent_suffix_ids": list(prepared.recent_ids),
        "admissions": list(prepared.admissions),
        "preflight": prepared.preflight,
        "simulation": prepared.simulation,
        "arms": dict(arms or {}),
        "invariants": {
            "raw_candidates_available": bool(prepared.raw_candidates),
            "compact_builder_produced_one_binding": prepared.binding is not None,
            "discovery_admitted_binding": any(
                admission.get("status") == "admitted"
                for admission in prepared.admissions
            ),
            "discovery_is_shadow_only": all(
                admission.get("used_in_prompt") is False
                for admission in prepared.admissions
            ),
            "preflight_passed": (prepared.preflight or {}).get("status") == "passed",
            "simulation_passed": (prepared.simulation or {}).get("status") == "passed",
            "projection_hashes_match_simulation": (
                bool(prepared.raw_projection_text)
                and bool(prepared.reusable_projection_text)
                and _sha256_text(prepared.raw_projection_text)
                == (prepared.simulation or {}).get("raw_prompt_hash")
                and _sha256_text(prepared.reusable_projection_text)
                == (prepared.simulation or {}).get("reusable_prompt_hash")
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
    }
    case_receipt["status"] = _case_status(case_receipt)
    return case_receipt


def _aggregate_usage(cases: list[dict[str, Any]]) -> dict[str, Any]:
    raw_prompt = 0
    reusable_prompt = 0
    raw_total = 0
    reusable_total = 0
    complete = True
    for case in cases:
        arms = case.get("arms") if isinstance(case.get("arms"), Mapping) else {}
        raw = arms.get("raw") if isinstance(arms.get("raw"), Mapping) else {}
        reusable = arms.get("reusable") if isinstance(arms.get("reusable"), Mapping) else {}
        raw_usage = raw.get("attempt", {}).get("usage", {})
        reusable_usage = reusable.get("attempt", {}).get("usage", {})
        if not isinstance(raw_usage, Mapping) or not isinstance(reusable_usage, Mapping):
            complete = False
            continue
        raw_prompt += int(raw_usage.get("prompt_tokens") or 0)
        reusable_prompt += int(reusable_usage.get("prompt_tokens") or 0)
        raw_total += int(raw_usage.get("total_tokens") or 0)
        reusable_total += int(reusable_usage.get("total_tokens") or 0)
    return {
        "usage_complete": complete and bool(cases),
        "raw_prompt_tokens": raw_prompt,
        "reusable_prompt_tokens": reusable_prompt,
        "prompt_token_delta": raw_prompt - reusable_prompt,
        "raw_total_tokens": raw_total,
        "reusable_total_tokens": reusable_total,
        "total_token_delta": raw_total - reusable_total,
    }


def _real_side_effects(call_count: int) -> dict[str, Any]:
    return dict(ZERO_SIDE_EFFECTS) | {
        "provider_transport_attempted": call_count > 0,
        "provider_calls": call_count,
        "network_side_effects": call_count,
        "used_in_prompt": False,
        "authority_artifact_persisted": False,
    }


def run_read_only_confirmation_matrix(
    *,
    output_root: Path,
    cases: tuple[MatrixCase, ...] = MATRIX_CASES,
    client_factory: Callable[[LLMSettings], SupportsComplete] | None = None,
    settings_factory: Callable[[], LLMSettings] | None = None,
) -> dict[str, Any]:
    with TemporaryDirectory(prefix="h8r2bl-read-only-matrix-") as tmp:
        prepared_cases = [
            _prepare_case(case, tmp_root=Path(tmp) / case.case_id)
            for case in cases
        ]

    setup_reasons = [
        f"{prepared.case.case_id}:{reason}"
        for prepared in prepared_cases
        for reason in prepared.setup_reasons
    ]
    settings: LLMSettings | None = None
    provider_descriptor = _provider_descriptor(None)
    if not setup_reasons:
        try:
            settings = (settings_factory or _settings_from_env)()
            provider_descriptor = _provider_descriptor(settings)
        except Exception as exc:
            setup_reasons.append(f"settings_unavailable:{type(exc).__name__}")

    provider_call_count = 0
    client: SupportsComplete | None = None
    case_receipts: list[dict[str, Any]] = []
    if not setup_reasons and settings is not None:
        client = (
            client_factory(settings)
            if client_factory is not None
            else LLMClient(settings=settings, enable_cache=False)
        )

    for prepared in prepared_cases:
        arms: dict[str, Any] = {}
        if not setup_reasons and client is not None:
            arms["raw"] = _arm_result(
                prepared=prepared,
                arm="raw",
                projection=prepared.raw_projection_text,
                offline_tokens=prepared.raw_projection_tokens,
                client=client,
            )
            provider_call_count += 1
            arms["reusable"] = _arm_result(
                prepared=prepared,
                arm="reusable",
                projection=prepared.reusable_projection_text,
                offline_tokens=prepared.reusable_projection_tokens,
                client=client,
            )
            provider_call_count += 1
        case_receipts.append(_case_receipt(prepared, arms=arms))

    aggregate_usage = _aggregate_usage(case_receipts)
    status = (
        "blocked"
        if any(reason.startswith("settings_unavailable") for reason in setup_reasons)
        else (
            "passed"
            if case_receipts
            and all(case.get("status") == "passed" for case in case_receipts)
            else "needs_followup"
        )
    )
    receipt = {
        "schema": SCHEMA,
        "status": status,
        "claim_boundary": CLAIM_BOUNDARY,
        "setup_reasons": setup_reasons,
        "provider_descriptor": provider_descriptor,
        "case_count": len(case_receipts),
        "cases": case_receipts,
        "aggregate_usage": aggregate_usage,
        "invariants": {
            "all_cases_prepared": len(case_receipts) == len(cases)
            and not [
                reason for reason in setup_reasons if not reason.startswith("settings_")
            ],
            "all_cases_passed": bool(case_receipts)
            and all(case.get("status") == "passed" for case in case_receipts),
            "all_discovery_shadow_only": all(
                case.get("invariants", {}).get("discovery_is_shadow_only") is True
                for case in case_receipts
            ),
            "all_preflight_passed": all(
                case.get("invariants", {}).get("preflight_passed") is True
                for case in case_receipts
            ),
            "all_simulation_passed": all(
                case.get("invariants", {}).get("simulation_passed") is True
                for case in case_receipts
            ),
            "all_provider_usage_complete": all(
                case.get("invariants", {}).get("provider_usage_complete") is True
                for case in case_receipts
            ),
            "all_provider_prompt_tokens_reduced": all(
                case.get("invariants", {}).get("provider_prompt_tokens_reduced") is True
                for case in case_receipts
            ),
            "all_quality_facts_covered": all(
                case.get("invariants", {}).get("quality_facts_covered") is True
                for case in case_receipts
            ),
            "aggregate_prompt_tokens_reduced": (
                aggregate_usage.get("prompt_token_delta", 0) > 0
            ),
            "aggregate_total_tokens_reduced": (
                aggregate_usage.get("total_token_delta", 0) > 0
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
    result = run_read_only_confirmation_matrix(output_root=args.output_root)
    print(
        json.dumps(
            {
                "status": result["status"],
                "receipt_hash": result["receipt_hash"],
                "aggregate_usage": result["aggregate_usage"],
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
