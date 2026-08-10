"""Run a minimal real-project raw/Compact provider mutation pair.

H8-R2BO proved the real-project task shape with a deterministic mock provider.
This stage keeps the same disposable workspace and task scope, then optionally
uses a real DeepSeek-compatible provider for one raw/Compact pair.  Receipts are
body-free: prompt/source/response/patch/stdout/stderr and credentials are never
persisted.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Protocol, Sequence
from unittest.mock import patch

from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.task_models import TaskExecutionContext
from core.config import LLMSettings
from core.llm import LLMClient, LLMRequest, LLMResponse
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
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextCompactionBinding,
    DurableArtifactReference,
    ReasoningDecisionComplexity,
    ReasoningCapabilityProfileId,
)

from experiments.full_architecture_context_observation.stage_h8r2aj_real_execution import (
    _request_summary,
    _response_summary,
)
from experiments.full_architecture_context_observation.stage_h8r2at_provider_summary_shadow import (
    _write_receipt,
)
from experiments.full_architecture_context_observation.stage_h8r2av_multi_window_summary_quality import (
    canonical_hash,
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
    _provider_descriptor,
    _safe_receipt_hash,
)
from experiments.full_architecture_context_observation.stage_h8r2bm_mutation_tool_shadow import (
    _attempts_summary,
    _mutation_settings,
    _runtime,
    _summary_candidate_from_binding,
    _usage_summary,
    _validation_environment,
)
from experiments.full_architecture_context_observation.stage_h8r2bo_real_project_tool_choice_admission import (
    CLAIM_BOUNDARY as BO_CLAIM_BOUNDARY,
    EXACT_VALIDATION_COMMAND,
    READ_FILES,
    SUPPORT_FILES,
    TARGET_FILE,
    TOOL_ALLOWLIST,
    WRITE_FILES,
    _authority_candidate,
    _constraint_candidate,
    _copy_source_workspace,
    _independent_validation,
    _mutation_gate,
    _request_shape_gate,
    _source_file_hashes,
    _task,
    _tool_event_summary,
    _MockRealProjectClient,
)


SCHEMA = "phase-h8r2bp-real-project-provider-pair-v1"
CLAIM_BOUNDARY = "real_project_provider_pair_no_production_prompt_use"

_SECRET_RE = __import__("re").compile(
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}(?![A-Za-z0-9])"
)
_FORBIDDEN_BODY_KEYS = {
    "prompt",
    "prompt_text",
    "body",
    "content",
    "content_preview",
    "response_text",
    "raw_response",
    "stdout",
    "stderr",
    "generated_unit",
    "replacement_text",
    "patch",
    "source_text",
    "source_body",
    "source_payload",
    "source_snapshot",
}


class SupportsComplete(Protocol):
    settings: Any

    def complete(self, request: LLMRequest, **kwargs: Any) -> LLMResponse: ...


@dataclass
class PreparedPairContext:
    raw_context: dict[str, Any]
    compact_context: dict[str, Any]
    raw_candidates: list[ContextCandidate]
    reusable_candidates: list[ContextCandidate]
    binding: ContextCompactionBinding | None
    required_ids: list[str]
    recent_ids: list[str]
    admissions: list[dict[str, Any]]
    preflight: dict[str, Any] | None
    simulation: dict[str, Any] | None
    raw_projection_tokens: int | None
    reusable_projection_tokens: int | None
    setup_reasons: list[str]


class _RecordingProviderClient:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self._client = LLMClient(settings=settings, enable_cache=False)
        self.requests: list[LLMRequest] = []
        self.response_history: list[LLMResponse] = []

    def complete(self, request: LLMRequest, **kwargs: Any) -> LLMResponse:
        self.requests.append(request)
        response = self._client.complete(request, **kwargs)
        self.response_history.append(response)
        return response


class _RoutineMutationExecutor(ToolPlanningTaskExecutor):
    """Experiment-only executor that keeps this frozen mutation lane economical."""

    def _reasoning_complexity_for_task(  # type: ignore[override]
        self,
        task: Any | None = None,
    ) -> ReasoningDecisionComplexity:
        return ReasoningDecisionComplexity.ROUTINE


def _settings_from_env() -> LLMSettings:
    credential = (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENPILOT_LLM_API_KEY")
        or ""
    )
    if not credential.strip():
        raise ValueError("DEEPSEEK_API_KEY or OPENPILOT_LLM_API_KEY is required")
    return _mutation_settings(
        LLMSettings(
            _env_file=None,
            provider="openai-compatible",
            base_url=os.environ.get("OPENPILOT_LLM_BASE_URL")
            or "https://api.deepseek.com",
            api_key=credential,
            model=os.environ.get("OPENPILOT_LLM_MODEL") or "deepseek-v4-flash",
            timeout_seconds=90.0,
            temperature=0.0,
            transport_retries=0,
            retry_initial_delay=0.0,
            retry_max_delay=0.0,
            reasoning_capability_profile=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
        )
    )


def _seed_dialog(short_memory: ShortMemory, count: int = 14) -> None:
    markers = (
        f"write_scope={TARGET_FILE}",
        "forbidden_target=README.md",
        "forbidden_target=Code/src/core/provider_tool_roundtrip.py",
        "required_tool_choice=required_until_finalization",
        "finalization=no_tools_no_tool_choice",
        "validation_command=pytest_provider_tool_roundtrip_exact",
        "api_rule=preserve_provider_runtime_api",
    )
    for index in range(count):
        marker = markers[index % len(markers)]
        repetitions = 150 if index < count - 2 else 8
        short_memory.add_message(
            "assistant",
            (
                f"Real-project provider-pair note {index}: {marker}. "
                "The stable task constraint is scoped and must be retained. "
                + (f"low-value-h8r2bp-{index} " * repetitions)
            ),
        )


def _compaction_sink(record: dict[str, Any]) -> DurableArtifactReference:
    artifact_id = f"h8r2bp-artifact-{record['compaction_id']}"
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
        "Real-project provider-pair setup. Stable facts: "
        f"write_scope={TARGET_FILE}; forbidden_target=README.md; "
        "forbidden_target=Code/src/core/provider_tool_roundtrip.py; "
        "required_tool_choice=required_until_finalization; "
        "finalization=no_tools_no_tool_choice; "
        "validation_command=pytest_provider_tool_roundtrip_exact."
    )
    raw_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / "raw-memory"),
        max_prompt_chars=160_000,
    )
    raw_context = raw_builder.build(
        "h8r2bp real-project raw",
        include_environment=False,
        limit=14,
        system_prompt=system_prompt,
    )
    compact_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / "compact-memory"),
        max_prompt_chars=5_000,
    )
    compact_builder.set_checkpoint_handlers(compaction_sink=_compaction_sink)
    compact_context = compact_builder.build(
        "h8r2bp real-project compact",
        include_environment=False,
        limit=14,
        system_prompt=system_prompt,
    )
    return raw_context, compact_context


def prepare_pair_context_for_test(tmp_root: Path) -> PreparedPairContext:
    return _prepare_pair_context(tmp_root)


def _prepare_pair_context(tmp_root: Path) -> PreparedPairContext:
    raw_context, compact_context = _build_contexts(tmp_root)
    raw_candidates = [
        _authority_candidate(),
        _constraint_candidate(),
        *[
            ContextCandidate.model_validate(item)
            for item in raw_context.get("selected_context_candidates") or []
        ],
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
    raw_projection_tokens: int | None = None
    reusable_projection_tokens: int | None = None
    reusable_candidates: list[ContextCandidate] = list(raw_candidates)
    setup_reasons: list[str] = []
    counter = _WhitespaceTokenCounter()
    policy = ContextAssemblyPolicy(
        max_prompt_chars=160_000,
        max_prompt_tokens=30_000,
        reserved_prompt_tokens=512,
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
            context_id="h8r2bp-compact-context",
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
                preflight_id="h8r2bp-preflight",
                token_counter=counter,
            )
            simulation = simulate_reusable_compaction_prompt_use(
                binding=binding,
                candidates=raw_candidates,
                policy=policy,
                renderer=MemoryContextBuilder._render_candidates,
                preflight=preflight,
                simulation_id="h8r2bp-simulation",
                token_counter=counter,
            )
            preflight_payload = preflight.model_dump(mode="json")
            simulation_payload = simulation.model_dump(mode="json")
            if simulation.status != "passed":
                setup_reasons.append("simulation_not_passed")
            else:
                assembler = __import__(
                    "memory.context_assembly",
                    fromlist=["ContextAssembler"],
                ).ContextAssembler(renderer=lambda _payload: "", token_counter=counter)
                raw_projection = assembler.assemble_candidates(
                    list(raw_candidates),
                    policy=policy,
                    renderer=MemoryContextBuilder._render_candidates,
                )
                reusable_projection = assembler.assemble_candidates(
                    [
                        *raw_candidates,
                        _summary_candidate_from_binding(
                            binding,
                            candidate_id=simulation.summary_candidate_id,
                        ),
                    ],
                    policy=policy,
                    renderer=MemoryContextBuilder._render_candidates,
                )
                raw_projection_tokens = raw_projection.selection.final_prompt_tokens
                reusable_projection_tokens = reusable_projection.selection.final_prompt_tokens
                if (
                    raw_projection.selection.assembly_status
                    != ContextAssemblyStatus.READY
                    or reusable_projection.selection.assembly_status
                    != ContextAssemblyStatus.READY
                ):
                    setup_reasons.append("projection_not_ready")
                reusable_candidates = [
                    *raw_candidates,
                    _summary_candidate_from_binding(
                        binding,
                        candidate_id=simulation.summary_candidate_id,
                    ),
                ]
    return PreparedPairContext(
        raw_context=raw_context,
        compact_context=compact_context,
        raw_candidates=raw_candidates,
        reusable_candidates=reusable_candidates,
        binding=binding,
        required_ids=required_ids,
        recent_ids=recent_ids,
        admissions=admissions,
        preflight=preflight_payload,
        simulation=simulation_payload,
        raw_projection_tokens=raw_projection_tokens,
        reusable_projection_tokens=reusable_projection_tokens,
        setup_reasons=setup_reasons,
    )


def _run_arm(
    *,
    arm_id: str,
    candidates: list[ContextCandidate],
    source_root: Path,
    settings: LLMSettings,
    client_factory: Callable[[LLMSettings, Path, str], SupportsComplete],
    provider_transport: bool,
) -> dict[str, Any]:
    with TemporaryDirectory(prefix=f"h8r2bp-{arm_id}-") as tmp:
        project_root = Path(tmp) / "source"
        _copy_source_workspace(source_root, project_root)
        before_hashes = _source_file_hashes(project_root)
        target = project_root / TARGET_FILE
        target_before_sha256 = canonical_hash(target.read_bytes().hex())
        client = client_factory(settings, project_root, arm_id)
        if getattr(client, "settings", None) is None:
            setattr(client, "settings", settings)
        runtime = _runtime(client)
        executor = _RoutineMutationExecutor(runtime)
        task = _task()
        context = TaskExecutionContext(
            task=task,
            parent_context={"goal": task.description, "project_path": str(project_root)},
        )
        with _validation_environment():
            counter = None if provider_transport else _WhitespaceTokenCounter()
            if counter is None:
                result = executor.execute_provider_tool_task(
                    task,
                    context,
                    tool_names=list(TOOL_ALLOWLIST),
                    user_confirmed=True,
                    allow_mutations=True,
                    max_rounds=8,
                    initial_context_candidates=list(candidates),
                )
            else:
                with patch(
                    "memory.context_assembly.request_builder.ProviderTokenCounter.from_settings",
                    return_value=counter,
                ):
                    result = executor.execute_provider_tool_task(
                        task,
                        context,
                        tool_names=list(TOOL_ALLOWLIST),
                        user_confirmed=True,
                        allow_mutations=True,
                        max_rounds=8,
                        initial_context_candidates=list(candidates),
                    )
            independent_validation = _independent_validation(project_root)
        after_hashes = _source_file_hashes(project_root)
        target_after_sha256 = canonical_hash(target.read_bytes().hex())
        attributes = dict(result.attributes or {})
        requests = [_request_summary(request) for request in getattr(client, "requests", [])]
        responses = [
            _response_summary(response)
            for response in getattr(client, "response_history", [])
        ]
        response_history = list(getattr(client, "response_history", []) or [])
        attempts = _attempts_summary(attributes)
        tool_events = _tool_event_summary(attributes, project_root=project_root)
        mutation_gate = _mutation_gate(
            result_status=result.status,
            before_hashes=before_hashes,
            after_hashes=after_hashes,
            target_before_sha256=target_before_sha256,
            target_after_sha256=target_after_sha256,
            tool_events=tool_events,
            attempts=attempts,
            independent_validation=independent_validation,
        )
        request_shape_gate = _request_shape_gate(requests)
        return {
            "arm_id": arm_id,
            "status": (
                "passed"
                if mutation_gate["passed"]
                and request_shape_gate["passed"]
                and result.status.value == "completed"
                else "needs_followup"
            ),
            "candidate_count": len(candidates),
            "candidate_digests": [_candidate_digest(candidate) for candidate in candidates],
            "selected_candidate_ids": [
                item
                for request in requests[:1]
                for item in request.get("selected_candidate_ids", [])
            ],
            "execution": {
                "execution_mode": attributes.get("execution_mode"),
                "allow_mutations": attributes.get("allow_mutations"),
                "user_confirmed": attributes.get("user_confirmed"),
                "budget_profile": attributes.get("budget_profile"),
                "reasoning_mode": attributes.get("reasoning_mode"),
                "rounds_used": attributes.get("rounds_used"),
                "task_status": result.status.value,
                "task_error_type": type(result.error).__name__ if result.error else "",
                "task_error_present": bool(result.error),
                "task_error_sha256": canonical_hash(str(result.error)) if result.error else "",
                "request_diagnostic_count": len(attributes.get("request_diagnostics") or []),
                "budget_diagnostic_count": len(attributes.get("budget_diagnostics") or []),
            },
            "usage": _usage_summary(response_history),
            "requests": requests,
            "responses": responses,
            "attempts": attempts,
            "tool_events": tool_events,
            "mutation_gate": mutation_gate,
            "request_shape_gate": request_shape_gate,
            "independent_validation": independent_validation,
            "provider_transport_attempted": provider_transport,
            "completion_call_count": len(getattr(client, "requests", []) or []),
        }


def _mock_client_factory(
    _settings: LLMSettings,
    project_root: Path,
    _arm_id: str,
) -> SupportsComplete:
    return _MockRealProjectClient(project_root)


def _provider_client_factory(
    settings: LLMSettings,
    _project_root: Path,
    _arm_id: str,
) -> SupportsComplete:
    return _RecordingProviderClient(settings)


def _aggregate_usage(arms: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_id = {str(arm.get("arm_id")): arm for arm in arms}
    raw_usage = by_id.get("raw", {}).get("usage") or {}
    reusable_usage = by_id.get("reusable", {}).get("usage") or {}
    raw_prompt = int(raw_usage.get("prompt_tokens") or 0)
    reusable_prompt = int(reusable_usage.get("prompt_tokens") or 0)
    raw_completion = int(raw_usage.get("completion_tokens") or 0)
    reusable_completion = int(reusable_usage.get("completion_tokens") or 0)
    raw_total = int(raw_usage.get("total_tokens") or 0)
    reusable_total = int(reusable_usage.get("total_tokens") or 0)
    return {
        "raw_prompt_tokens": raw_prompt,
        "reusable_prompt_tokens": reusable_prompt,
        "prompt_token_delta": raw_prompt - reusable_prompt,
        "raw_completion_tokens": raw_completion,
        "reusable_completion_tokens": reusable_completion,
        "completion_token_delta": raw_completion - reusable_completion,
        "raw_total_tokens": raw_total,
        "reusable_total_tokens": reusable_total,
        "total_token_delta": raw_total - reusable_total,
        "usage_complete": bool(arms)
        and all((arm.get("usage") or {}).get("usage_complete") is True for arm in arms),
    }


def _side_effects(*, arms: Sequence[Mapping[str, Any]], provider_transport: bool) -> dict[str, Any]:
    completion_calls = sum(int(arm.get("completion_call_count") or 0) for arm in arms)
    writer_actions = sum(
        1
        for arm in arms
        for attempt in arm.get("attempts") or []
        if attempt.get("tool_name") == "file_patch_writer"
        and attempt.get("success") is True
    )
    command_actions = sum(
        1
        for arm in arms
        for attempt in arm.get("attempts") or []
        if attempt.get("tool_name") == "command_executor"
        and attempt.get("success") is True
    )
    return {
        "provider_transport_attempted": provider_transport and completion_calls > 0,
        "provider_calls": completion_calls if provider_transport else 0,
        "mock_completion_calls": 0 if provider_transport else completion_calls,
        "network_side_effects": completion_calls if provider_transport else 0,
        "project_mutations": writer_actions,
        "memory_mutations": 0,
        "writer_actions": writer_actions,
        "command_actions": command_actions,
        "verification_runs": len(arms),
        "retry_count": 0,
        "fallback_count": 0,
        "used_in_prompt": False,
    }


def _status(arms: Sequence[Mapping[str, Any]], setup_reasons: Sequence[str]) -> str:
    if any(reason.startswith("settings_unavailable") for reason in setup_reasons):
        return "blocked"
    if setup_reasons:
        return "needs_followup"
    if len(arms) != 2:
        return "needs_followup"
    if not all(arm.get("status") == "passed" for arm in arms):
        return "needs_followup"
    usage = _aggregate_usage(arms)
    if usage.get("usage_complete") is not True:
        return "needs_followup"
    return "passed"


def run_real_project_provider_pair(
    *,
    output_root: Path,
    source_root: Path | None = None,
    settings_factory: Callable[[], LLMSettings] | None = None,
    client_factory: Callable[[LLMSettings, Path, str], SupportsComplete] | None = None,
    provider_transport: bool = True,
) -> dict[str, Any]:
    source_root = (source_root or Path.cwd()).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="h8r2bp-context-") as tmp:
        prepared = _prepare_pair_context(Path(tmp))

    setup_reasons = list(prepared.setup_reasons)
    settings: LLMSettings | None = None
    provider_descriptor = _provider_descriptor(None)
    if not setup_reasons:
        try:
            settings = _mutation_settings((settings_factory or _settings_from_env)())
            provider_descriptor = _provider_descriptor(settings)
        except Exception as exc:
            setup_reasons.append(f"settings_unavailable:{type(exc).__name__}")

    arms: list[dict[str, Any]] = []
    if not setup_reasons and settings is not None:
        factory = client_factory or _provider_client_factory
        arms.append(
            _run_arm(
                arm_id="raw",
                candidates=prepared.raw_candidates,
                source_root=source_root,
                settings=settings,
                client_factory=factory,
                provider_transport=provider_transport,
            )
        )
        arms.append(
            _run_arm(
                arm_id="reusable",
                candidates=prepared.reusable_candidates,
                source_root=source_root,
                settings=settings,
                client_factory=factory,
                provider_transport=provider_transport,
            )
        )

    aggregate_usage = _aggregate_usage(arms)
    status = _status(arms, setup_reasons)
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": status,
        "claim_boundary": CLAIM_BOUNDARY,
        "upstream_replay_boundary": BO_CLAIM_BOUNDARY,
        "setup_reasons": setup_reasons,
        "provider_descriptor": provider_descriptor,
        "task": {
            "task_id": _task().id,
            "kind": "implement",
            "read_files": list(READ_FILES),
            "write_files": list(WRITE_FILES),
            "description_sha256": canonical_hash(_task().description),
        },
        "validation_command_sha256": canonical_hash(EXACT_VALIDATION_COMMAND),
        "tool_allowlist": list(TOOL_ALLOWLIST),
        "snapshot_hashes": {
            "root_path_sha256": _sha256_text(str(source_root)),
            "target_file_sha256": canonical_hash((source_root / TARGET_FILE).read_bytes().hex()),
            "support_file_hashes": {
                path: canonical_hash((source_root / path).read_bytes().hex())
                for path in SUPPORT_FILES
            },
        },
        "context_setup": {
            "raw_context_request_hash": prepared.raw_context.get("context_request_hash"),
            "compact_context_request_hash": prepared.compact_context.get(
                "context_request_hash"
            ),
            "selected_candidate_ids": [
                candidate.candidate_id for candidate in prepared.raw_candidates
            ],
            "binding_digest": _binding_digest(prepared.binding),
            "required_candidate_ids": list(prepared.required_ids),
            "recent_suffix_ids": list(prepared.recent_ids),
            "admissions": list(prepared.admissions),
            "preflight": prepared.preflight,
            "simulation": prepared.simulation,
            "raw_projection_tokens": prepared.raw_projection_tokens,
            "reusable_projection_tokens": prepared.reusable_projection_tokens,
        },
        "arm_count": len(arms),
        "arms": arms,
        "aggregate_usage": aggregate_usage,
        "invariants": {
            "setup_prepared": not [
                reason for reason in setup_reasons if not reason.startswith("settings_")
            ],
            "discovery_shadow_only": all(
                admission.get("used_in_prompt") is False
                for admission in prepared.admissions
            ),
            "preflight_passed": (prepared.preflight or {}).get("status") == "passed",
            "simulation_passed": (prepared.simulation or {}).get("status") == "passed",
            "both_arms_executed": len(arms) == 2,
            "both_arms_passed_mutation_gate": bool(arms)
            and all((arm.get("mutation_gate") or {}).get("passed") is True for arm in arms),
            "both_arms_passed_request_shape": bool(arms)
            and all((arm.get("request_shape_gate") or {}).get("passed") is True for arm in arms),
            "provider_usage_complete": bool(arms)
            and all((arm.get("usage") or {}).get("usage_complete") is True for arm in arms),
            "no_suspicious_success": bool(arms)
            and all(
                (arm.get("mutation_gate") or {}).get("suspicious_success") is False
                for arm in arms
            ),
            "aggregate_prompt_tokens_reduced": aggregate_usage.get("prompt_token_delta", 0) > 0,
            "aggregate_total_tokens_reduced": aggregate_usage.get("total_token_delta", 0) > 0,
        },
        "side_effects": _side_effects(arms=arms, provider_transport=provider_transport),
        "secret_handling": {
            "credential_present": bool(settings and settings.api_key and settings.api_key.strip()),
            "credential_serialized": False,
        },
        "receipt_body_free": True,
    }
    receipt["receipt_hash"] = _safe_receipt_hash(receipt)
    _write_receipt(output_root / "aggregate" / "receipt.json", receipt)
    return receipt


def build_mock_real_project_provider_pair(*, output_root: Path) -> dict[str, Any]:
    return run_real_project_provider_pair(
        output_root=output_root,
        source_root=Path.cwd(),
        settings_factory=lambda: _mutation_settings(
            LLMSettings(
                _env_file=None,
                provider="openai-compatible",
                base_url="https://api.deepseek.com",
                api_key="test-key",
                model="fake-deepseek-v4-flash",
                temperature=0.0,
                transport_retries=0,
                reasoning_capability_profile=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
            )
        ),
        client_factory=_mock_client_factory,
        provider_transport=False,
    )


def _assert_no_bodies(value: Any, *, location: str = "receipt") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_BODY_KEYS:
                raise ValueError(f"full prompt/body field was recorded at {location}.{key}")
            _assert_no_bodies(child, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_bodies(child, location=f"{location}[{index}]")


def validate_real_project_provider_pair_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(receipt)
    if data.get("schema") != SCHEMA:
        raise ValueError("BP receipt schema mismatch")
    if data.get("receipt_hash") != _safe_receipt_hash(data):
        raise ValueError("BP receipt hash mismatch")
    if data.get("tool_allowlist") != TOOL_ALLOWLIST:
        raise ValueError("BP tool allowlist mismatch")
    if data.get("validation_command_sha256") != canonical_hash(EXACT_VALIDATION_COMMAND):
        raise ValueError("BP validation command hash mismatch")
    if (data.get("secret_handling") or {}).get("credential_serialized") is not False:
        raise ValueError("BP credential serialization flag mismatch")
    if (data.get("side_effects") or {}).get("used_in_prompt") is not False:
        raise ValueError("BP prompt-use flag mismatch")
    for arm in data.get("arms") or []:
        for event in arm.get("tool_events") or []:
            if event.get("tool_name") not in TOOL_ALLOWLIST:
                raise ValueError("BP tool escaped allowlist")
    if data.get("status") == "passed":
        if data.get("arm_count") != 2:
            raise ValueError("BP passed receipt must have two arms")
        for arm in data.get("arms") or []:
            gate = arm.get("mutation_gate") or {}
            request_gate = arm.get("request_shape_gate") or {}
            if arm.get("status") != "passed":
                raise ValueError("BP passed receipt contains non-passed arm")
            if gate.get("passed") is not True:
                raise ValueError("BP passed arm lacks mutation gate pass")
            if request_gate.get("passed") is not True:
                raise ValueError("BP passed arm lacks request-shape gate pass")
            if gate.get("changed_source_files") != [TARGET_FILE]:
                raise ValueError("BP passed arm changed an out-of-scope file")
            if gate.get("provider_exact_validation_observed") is not True:
                raise ValueError("BP passed arm lacks provider exact validation")
            if gate.get("independent_exact_validation_exit_zero") is not True:
                raise ValueError("BP passed arm lacks independent validation")
        if (data.get("aggregate_usage") or {}).get("usage_complete") is not True:
            raise ValueError("BP passed receipt lacks complete usage")
    encoded = json.dumps(data, ensure_ascii=False)
    if _SECRET_RE.search(encoded):
        raise ValueError("BP receipt contains a secret-shaped value")
    _assert_no_bodies(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    if args.mock:
        result = build_mock_real_project_provider_pair(output_root=args.output_root)
    else:
        result = run_real_project_provider_pair(
            output_root=args.output_root,
            source_root=args.source_root,
        )
    validate_real_project_provider_pair_receipt(result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "arm_count": result["arm_count"],
                "receipt_hash": result["receipt_hash"],
                "aggregate_usage": result["aggregate_usage"],
                "side_effects": result["side_effects"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
