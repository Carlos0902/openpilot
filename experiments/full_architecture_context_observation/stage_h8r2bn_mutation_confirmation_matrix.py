"""Run an isolated mutation/tool-task confirmation matrix.

H8-R2BM proved one calculator mutation shadow.  This stage repeats the same
production provider-tool execution boundary across three small, disposable
single-file fixtures.  It remains experiment-only: reusable projection is
explicit harness opt-in and never changes production builder prompt behavior.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Protocol, Sequence
from unittest.mock import patch

from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.task_models import Task, TaskExecutionContext
from core.config import LLMSettings
from core.llm import LLMRequest, LLMResponse, LLMToolCall, LLMToolFunctionCall
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
    EXACT_VALIDATION_COMMAND,
    TARGET_FILE,
    TEST_FILE,
    TOOL_ALLOWLIST,
    _RecordingProviderLLMClient,
    _aggregate_usage as _arm_pair_usage,
    _attempts_summary,
    _changed_files,
    _ignored_runtime_artifact,
    _independent_validation,
    _mutation_gate,
    _mutation_settings,
    _projection_pair,
    _runtime,
    _settings_from_env,
    _summary_candidate_from_binding,
    _tool_event_summary,
    _usage_summary,
    _validation_environment,
)


SCHEMA = "phase-h8r2bn-mutation-confirmation-matrix-v1"
CLAIM_BOUNDARY = "isolated_mutation_tool_confirmation_matrix_no_production_prompt_use"

_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}(?![A-Za-z0-9])")
_FORBIDDEN_BODY_KEYS = {
    "prompt",
    "body",
    "content",
    "content_preview",
    "response_text",
    "raw_response",
    "stdout",
    "stderr",
    "replacement_text",
}


class SupportsComplete(Protocol):
    settings: Any

    def complete(self, request: LLMRequest, **kwargs: Any) -> LLMResponse: ...


@dataclass(frozen=True)
class MutationMatrixCase:
    case_id: str
    target_file: str
    test_file: str
    target_symbol: str
    behavior_marker: str
    task_description: str
    source_text: str
    test_text: str
    mock_replacement_text: str

    @property
    def contract_hash(self) -> str:
        return canonical_hash(
            {
                "case_id": self.case_id,
                "target_file": self.target_file,
                "test_file": self.test_file,
                "target_symbol": self.target_symbol,
                "behavior_marker": self.behavior_marker,
                "validation_command": EXACT_VALIDATION_COMMAND,
            }
        )


@dataclass
class PreparedCaseContext:
    case: MutationMatrixCase
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


CASES: tuple[MutationMatrixCase, ...] = (
    MutationMatrixCase(
        case_id="divide_zero_value_error",
        target_file=TARGET_FILE,
        test_file=TEST_FILE,
        target_symbol="divide",
        behavior_marker="required_behavior=divide_zero_value_error",
        task_description=(
            "Modify only calculator.py: update divide so division by zero raises "
            "ValueError while preserving normal division."
        ),
        source_text="""\
def divide(a, b):
    return a / b
""",
        test_text="""\
import pytest

from calculator import divide


def test_divide_normal():
    assert divide(6, 3) == 2


def test_divide_zero_raises_value_error():
    with pytest.raises(ValueError):
        divide(1, 0)
""",
        mock_replacement_text="""\
def divide(a, b):
    if b == 0:
        raise ValueError("division by zero")
    return a / b
""",
    ),
    MutationMatrixCase(
        case_id="clamp_bounds",
        target_file=TARGET_FILE,
        test_file=TEST_FILE,
        target_symbol="clamp",
        behavior_marker="required_behavior=clamp_bounds",
        task_description=(
            "Modify only calculator.py: update clamp so values below low return low, "
            "values above high return high, and in-range values are unchanged."
        ),
        source_text="""\
def clamp(value, low, high):
    return value
""",
        test_text="""\
from calculator import clamp


def test_clamp_in_range():
    assert clamp(5, 0, 10) == 5


def test_clamp_low_bound():
    assert clamp(-3, 0, 10) == 0


def test_clamp_high_bound():
    assert clamp(13, 0, 10) == 10
""",
        mock_replacement_text="""\
def clamp(value, low, high):
    return max(low, min(value, high))
""",
    ),
    MutationMatrixCase(
        case_id="safe_get_default",
        target_file=TARGET_FILE,
        test_file=TEST_FILE,
        target_symbol="safe_get",
        behavior_marker="required_behavior=safe_get_default",
        task_description=(
            "Modify only calculator.py: update safe_get so missing keys return "
            "the provided default while existing keys still return their value."
        ),
        source_text="""\
def safe_get(mapping, key, default=None):
    return mapping[key]
""",
        test_text="""\
from calculator import safe_get


def test_safe_get_existing_key():
    assert safe_get({"a": 1}, "a", 0) == 1


def test_safe_get_missing_key_returns_default():
    assert safe_get({"a": 1}, "b", 0) == 0
""",
        mock_replacement_text="""\
def safe_get(mapping, key, default=None):
    return mapping.get(key, default)
""",
    ),
)


class _MockMatrixMutationClient:
    """Deterministic provider-shaped client for the BN focused gate."""

    def __init__(self, project_root: Path, case: MutationMatrixCase) -> None:
        self.project_root = project_root
        self.case = case
        self.requests: list[LLMRequest] = []
        self.response_history: list[LLMResponse] = []
        self._index = 0

    def complete(self, request: LLMRequest, **_kwargs: Any) -> LLMResponse:
        self.requests.append(request)
        prompt_tokens = max(
            1,
            sum(len(message.content.split()) for message in request.messages),
        )
        sequence = self._index % 4
        self._index += 1
        if sequence == 0:
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id=f"bn-read-{self.case.case_id}-{self._index}",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments=json.dumps({"file_path": self.case.target_file}),
                        ),
                    )
                ],
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="tool_calls",
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 24,
                    "total_tokens": prompt_tokens + 24,
                },
            )
        elif sequence == 1:
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id=f"bn-patch-{self.case.case_id}-{self._index}",
                        function=LLMToolFunctionCall(
                            name="file_patch_writer",
                            arguments=json.dumps(
                                {
                                    "file_path": self.case.target_file,
                                    "operation_kind": "modify_symbol",
                                    "symbol_name": self.case.target_symbol,
                                    "replacement_text": self.case.mock_replacement_text,
                                }
                            ),
                        ),
                    )
                ],
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="tool_calls",
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 64,
                    "total_tokens": prompt_tokens + 64,
                },
            )
        elif sequence == 2:
            response = LLMResponse(
                content="",
                tool_calls=[
                    LLMToolCall(
                        id=f"bn-validate-{self.case.case_id}-{self._index}",
                        function=LLMToolFunctionCall(
                            name="command_executor",
                            arguments=json.dumps(
                                {
                                    "command": EXACT_VALIDATION_COMMAND,
                                    "cwd": str(self.project_root),
                                    "mode": "automatic",
                                    "timeout": 30,
                                    "env": {"PYTHONDONTWRITEBYTECODE": "1"},
                                }
                            ),
                        ),
                    )
                ],
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="tool_calls",
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 48,
                    "total_tokens": prompt_tokens + 48,
                },
            )
        else:
            response = LLMResponse(
                content="Scoped mutation and exact validation completed.",
                model="fake-deepseek-v4-flash",
                provider="offline-mock",
                finish_reason="stop",
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 16,
                    "total_tokens": prompt_tokens + 16,
                },
            )
        self.response_history.append(response)
        return response


def _mock_settings() -> LLMSettings:
    return LLMSettings(
        _env_file=None,
        provider="openai-compatible",
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="fake-deepseek-v4-flash",
        temperature=0.0,
        transport_retries=0,
        reasoning_capability_profile=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
    )


def _case_by_id(case_id: str) -> MutationMatrixCase:
    for case in CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(case_id)


def _seed_dialog(short_memory: ShortMemory, case: MutationMatrixCase, count: int = 12) -> None:
    markers = (
        f"case_id={case.case_id}",
        f"write_scope={case.target_file}",
        "forbidden_target=README.md",
        f"target_symbol={case.target_symbol}",
        case.behavior_marker,
        "validation_command=python_m_pytest_q_tests_test_calculator_py",
        "api_rule=preserve_api",
    )
    for index in range(count):
        marker = markers[index % len(markers)]
        repetitions = 128 if index < count - 2 else 10
        short_memory.add_message(
            "assistant",
            (
                f"Mutation matrix note {index}: {marker}. "
                "The stable constraint is scoped and must be retained. "
                + (f"low-value-h8r2bn-{case.case_id}-{index} " * repetitions)
            ),
        )


def _compaction_sink_for(case: MutationMatrixCase) -> Callable[[dict[str, Any]], DurableArtifactReference]:
    def sink(record: dict[str, Any]) -> DurableArtifactReference:
        artifact_id = f"h8r2bn-{case.case_id}-{record['compaction_id']}"
        return DurableArtifactReference(
            artifact_id=artifact_id,
            kind="context_compaction",
            integrity_checksum=canonical_hash(record),
            bytes=len(json.dumps(record, ensure_ascii=False, sort_keys=True)),
        )

    return sink


def _build_contexts(
    case: MutationMatrixCase,
    tmp_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    short_memory = ShortMemory(repo_path=tmp_root / f"{case.case_id}-short")
    _seed_dialog(short_memory, case)
    system_prompt = (
        "Mutation confirmation matrix setup. Stable facts: "
        f"case_id={case.case_id}; write_scope={case.target_file}; "
        "forbidden_target=README.md; "
        f"target_symbol={case.target_symbol}; {case.behavior_marker}; "
        "validation_command=python_m_pytest_q_tests_test_calculator_py; "
        "api_rule=preserve_api."
    )
    raw_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / f"{case.case_id}-raw-memory"),
        max_prompt_chars=140_000,
    )
    raw_context = raw_builder.build(
        f"h8r2bn {case.case_id} raw",
        include_environment=False,
        limit=12,
        system_prompt=system_prompt,
    )
    compact_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / f"{case.case_id}-compact-memory"),
        max_prompt_chars=4_500,
    )
    compact_builder.set_checkpoint_handlers(compaction_sink=_compaction_sink_for(case))
    compact_context = compact_builder.build(
        f"h8r2bn {case.case_id} compact",
        include_environment=False,
        limit=12,
        system_prompt=system_prompt,
    )
    return raw_context, compact_context


def _task_authority_candidate(case: MutationMatrixCase) -> ContextCandidate:
    return ContextCandidate(
        candidate_id=f"h8r2bn-task-authority-{case.case_id}",
        kind=ContextCandidateKind.TASK,
        source_id=f"h8r2bn:{case.case_id}:task-contract",
        role="system",
        content=(
            "Provider-tool mode is mandatory. Your first response must call file_reader "
            "for the authorized target file. Do not answer in prose before the first tool call. "
            "After reading, issue exactly one scoped file_patch_writer call for the authorized "
            "target, then run command_executor with the exact validation command. "
            f"Task contract for {case.case_id}: {case.task_description} "
            f"Read only {case.target_file} and {case.test_file}. "
            f"Modify only {case.target_file}. Target symbol: {case.target_symbol}. "
            "Do not modify README.md or tests. "
            f"The exact validation command is {EXACT_VALIDATION_COMMAND}. "
            "The command cwd must be the project root."
        ),
        retention=ContextCandidateRetention.REQUIRED,
        priority=100,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.AUTHORITATIVE,
        freshness=ContextCandidateFreshness.CURRENT,
    )


def prepare_case_context_for_test(case: MutationMatrixCase, tmp_root: Path) -> PreparedCaseContext:
    """Expose the case context contract to focused tests without running providers."""

    return _prepare_case_context(case, tmp_root)


def _prepare_case_context(case: MutationMatrixCase, tmp_root: Path) -> PreparedCaseContext:
    raw_context, compact_context = _build_contexts(case, tmp_root)
    authority = _task_authority_candidate(case)
    raw_candidates = [authority] + [
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
    raw_projection_tokens: int | None = None
    reusable_projection_tokens: int | None = None
    reusable_candidates: list[ContextCandidate] = list(raw_candidates)
    setup_reasons: list[str] = []
    counter = _WhitespaceTokenCounter()
    policy = ContextAssemblyPolicy(
        max_prompt_chars=140_000,
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
            context_id=f"h8r2bn-{case.case_id}-compact-context",
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
                preflight_id=f"h8r2bn-{case.case_id}-preflight",
                token_counter=counter,
            )
            simulation = simulate_reusable_compaction_prompt_use(
                binding=binding,
                candidates=raw_candidates,
                policy=policy,
                renderer=MemoryContextBuilder._render_candidates,
                preflight=preflight,
                simulation_id=f"h8r2bn-{case.case_id}-simulation",
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
                raw_projection_tokens = raw_projection.selection.final_prompt_tokens
                reusable_projection_tokens = (
                    reusable_projection.selection.final_prompt_tokens
                )
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

    return PreparedCaseContext(
        case=case,
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


def _source_file_hashes(project_root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(project_root.rglob("*")):
        if path.is_file() and not _ignored_runtime_artifact(path, project_root):
            hashes[path.relative_to(project_root).as_posix()] = canonical_hash(
                path.read_bytes().hex()
            )
    return hashes


def _build_fixture(case: MutationMatrixCase, project_root: Path) -> dict[str, str]:
    (project_root / "tests").mkdir(parents=True, exist_ok=True)
    (project_root / "pytest.ini").write_text(
        "[pytest]\naddopts = -p no:cacheprovider\n",
        encoding="utf-8",
    )
    (project_root / case.target_file).write_text(case.source_text, encoding="utf-8")
    (project_root / case.test_file).write_text(case.test_text, encoding="utf-8")
    return _source_file_hashes(project_root)


def _task(case: MutationMatrixCase) -> Task:
    return Task(
        id=f"h8r2bn-{case.case_id}",
        description=case.task_description + " Then run the exact validation command.",
        kind="implement",
        read_files=[case.target_file, case.test_file],
        write_files=[case.target_file],
        validation_command=EXACT_VALIDATION_COMMAND,
        tags=["h8r2bn", "mutation_confirmation", case.case_id],
    )


def _run_case_arm(
    *,
    prepared: PreparedCaseContext,
    arm_id: str,
    candidates: list[ContextCandidate],
    settings: LLMSettings,
    client_factory: Callable[[LLMSettings, Path, str, str], SupportsComplete],
    provider_transport: bool,
    token_counter: Any | None,
) -> dict[str, Any]:
    case = prepared.case
    with TemporaryDirectory(prefix=f"h8r2bn-{case.case_id}-{arm_id}-") as tmp:
        project_root = Path(tmp) / "fixture"
        project_root.mkdir(parents=True, exist_ok=True)
        before_hashes = _build_fixture(case, project_root)
        target = project_root / case.target_file
        target_before_sha256 = canonical_hash(target.read_bytes().hex())
        client = client_factory(settings, project_root, case.case_id, arm_id)
        if getattr(client, "settings", None) is None:
            setattr(client, "settings", settings)
        task = _task(case)
        runtime = _runtime(client)
        executor = ToolPlanningTaskExecutor(runtime)
        context = TaskExecutionContext(
            task=task,
            parent_context={"goal": task.description, "project_path": str(project_root)},
        )
        resolved_token_counter = (
            token_counter
            if token_counter is not None
            else (_WhitespaceTokenCounter() if not provider_transport else None)
        )
        with _validation_environment():
            if resolved_token_counter is None:
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
                    return_value=resolved_token_counter,
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
        tool_events = _tool_event_summary(attributes, project_root=project_root)
        attempts = _attempts_summary(attributes)
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
        return {
            "arm_id": arm_id,
            "status": "passed" if mutation_gate["passed"] else "needs_followup",
            "candidate_count": len(candidates),
            "selected_candidate_ids": [
                item
                for request in requests[:1]
                for item in request.get("selected_candidate_ids", [])
            ],
            "candidate_digests": [_candidate_digest(candidate) for candidate in candidates],
            "execution": {
                "execution_mode": attributes.get("execution_mode"),
                "allow_mutations": attributes.get("allow_mutations"),
                "user_confirmed": attributes.get("user_confirmed"),
                "budget_profile": attributes.get("budget_profile"),
                "reasoning_mode": attributes.get("reasoning_mode"),
                "rounds_used": attributes.get("rounds_used"),
                "task_status": result.status.value,
                "task_error_type": type(result.error).__name__ if result.error else "",
                "request_diagnostic_count": len(attributes.get("request_diagnostics") or []),
                "budget_diagnostic_count": len(attributes.get("budget_diagnostics") or []),
            },
            "usage": _usage_summary(response_history),
            "requests": requests,
            "responses": responses,
            "tool_events": tool_events,
            "attempts": attempts,
            "mutation_gate": mutation_gate,
            "independent_validation": independent_validation,
            "provider_transport_attempted": provider_transport,
            "completion_call_count": len(getattr(client, "requests", []) or []),
        }


def _case_receipt(
    prepared: PreparedCaseContext,
    *,
    arms: Mapping[str, Any],
) -> dict[str, Any]:
    case = prepared.case
    usage = _arm_pair_usage(list(arms.values()))
    status = (
        "passed"
        if not prepared.setup_reasons
        and set(arms) == {"raw", "reusable"}
        and all((arm.get("mutation_gate") or {}).get("passed") is True for arm in arms.values())
        else "needs_followup"
    )
    return {
        "case_id": case.case_id,
        "status": status,
        "case_contract_sha256": case.contract_hash,
        "target_file": case.target_file,
        "test_file": case.test_file,
        "target_symbol": case.target_symbol,
        "setup_reasons": list(prepared.setup_reasons),
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
        "arms": dict(arms),
        "usage": usage,
        "invariants": {
            "setup_prepared": not prepared.setup_reasons,
            "discovery_shadow_only": all(
                admission.get("used_in_prompt") is False
                for admission in prepared.admissions
            ),
            "preflight_passed": (prepared.preflight or {}).get("status") == "passed",
            "simulation_passed": (prepared.simulation or {}).get("status") == "passed",
            "both_arms_executed": set(arms) == {"raw", "reusable"},
            "both_arms_passed_mutation_gate": bool(arms)
            and all((arm.get("mutation_gate") or {}).get("passed") is True for arm in arms.values()),
            "provider_usage_complete": bool(arms)
            and all((arm.get("usage") or {}).get("usage_complete") is True for arm in arms.values()),
            "no_suspicious_success": bool(arms)
            and all(
                (arm.get("mutation_gate") or {}).get("suspicious_success") is False
                for arm in arms.values()
            ),
        },
    }


def _aggregate_usage(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    raw_prompt = reusable_prompt = raw_completion = reusable_completion = 0
    raw_total = reusable_total = 0
    complete = bool(cases)
    for case in cases:
        usage = case.get("usage") if isinstance(case.get("usage"), Mapping) else {}
        raw_prompt += int(usage.get("raw_prompt_tokens") or 0)
        reusable_prompt += int(usage.get("reusable_prompt_tokens") or 0)
        raw_total += int(usage.get("raw_total_tokens") or 0)
        reusable_total += int(usage.get("reusable_total_tokens") or 0)
        arms = case.get("arms") if isinstance(case.get("arms"), Mapping) else {}
        raw = arms.get("raw") if isinstance(arms.get("raw"), Mapping) else {}
        reusable = arms.get("reusable") if isinstance(arms.get("reusable"), Mapping) else {}
        raw_completion += int((raw.get("usage") or {}).get("completion_tokens") or 0)
        reusable_completion += int((reusable.get("usage") or {}).get("completion_tokens") or 0)
        complete = complete and bool(usage.get("usage_complete"))
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
        "usage_complete": complete,
    }


def _side_effects(
    *,
    cases: Sequence[Mapping[str, Any]],
    provider_transport: bool,
) -> dict[str, Any]:
    arms = [
        arm
        for case in cases
        for arm in (case.get("arms") or {}).values()
        if isinstance(arm, Mapping)
    ]
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


def _status(cases: Sequence[Mapping[str, Any]], setup_reasons: Sequence[str]) -> str:
    if any(reason.startswith("settings_unavailable") for reason in setup_reasons):
        return "blocked"
    if setup_reasons:
        return "needs_followup"
    if len(cases) != len(CASES):
        return "needs_followup"
    if not all(case.get("status") == "passed" for case in cases):
        return "needs_followup"
    usage = _aggregate_usage(cases)
    if usage.get("prompt_token_delta", 0) <= 0 or usage.get("total_token_delta", 0) <= 0:
        return "needs_followup"
    return "passed"


def _default_client_factory(
    settings: LLMSettings,
    _project_root: Path,
    _case_id: str,
    _arm_id: str,
) -> SupportsComplete:
    return _RecordingProviderLLMClient(settings)


def run_mutation_confirmation_matrix(
    *,
    output_root: Path,
    cases: tuple[MutationMatrixCase, ...] = CASES,
    settings_factory: Callable[[], LLMSettings] | None = None,
    client_factory: Callable[[LLMSettings, Path, str, str], SupportsComplete] | None = None,
    provider_transport: bool = True,
    token_counter: Any | None = None,
) -> dict[str, Any]:
    with TemporaryDirectory(prefix="h8r2bn-context-") as tmp:
        prepared_cases = [
            _prepare_case_context(case, Path(tmp) / case.case_id)
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
            settings = _mutation_settings((settings_factory or _settings_from_env)())
            provider_descriptor = _provider_descriptor(settings)
        except Exception as exc:
            setup_reasons.append(f"settings_unavailable:{type(exc).__name__}")

    case_receipts: list[dict[str, Any]] = []
    if not setup_reasons and settings is not None:
        factory = client_factory or _default_client_factory
        for prepared in prepared_cases:
            raw = _run_case_arm(
                prepared=prepared,
                arm_id="raw",
                candidates=prepared.raw_candidates,
                settings=settings,
                client_factory=factory,
                provider_transport=provider_transport,
                token_counter=token_counter,
            )
            reusable = _run_case_arm(
                prepared=prepared,
                arm_id="reusable",
                candidates=prepared.reusable_candidates,
                settings=settings,
                client_factory=factory,
                provider_transport=provider_transport,
                token_counter=token_counter,
            )
            case_receipts.append(
                _case_receipt(prepared, arms={"raw": raw, "reusable": reusable})
            )
    else:
        case_receipts = [
            _case_receipt(prepared, arms={})
            for prepared in prepared_cases
        ]

    aggregate_usage = _aggregate_usage(case_receipts)
    status = _status(case_receipts, setup_reasons)
    receipt = {
        "schema": SCHEMA,
        "status": status,
        "claim_boundary": CLAIM_BOUNDARY,
        "setup_reasons": setup_reasons,
        "provider_descriptor": provider_descriptor,
        "case_count": len(case_receipts),
        "arm_count": sum(len(case.get("arms") or {}) for case in case_receipts),
        "case_ids": [case.case_id for case in cases],
        "validation_command_sha256": canonical_hash(EXACT_VALIDATION_COMMAND),
        "tool_allowlist": list(TOOL_ALLOWLIST),
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
                case.get("invariants", {}).get("discovery_shadow_only") is True
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
            "all_arms_passed_mutation_gate": all(
                case.get("invariants", {}).get("both_arms_passed_mutation_gate") is True
                for case in case_receipts
            ),
            "all_provider_usage_complete": all(
                case.get("invariants", {}).get("provider_usage_complete") is True
                for case in case_receipts
            ),
            "aggregate_prompt_tokens_reduced": (
                aggregate_usage.get("prompt_token_delta", 0) > 0
            ),
            "aggregate_total_tokens_reduced": (
                aggregate_usage.get("total_token_delta", 0) > 0
            ),
            "no_suspicious_success": all(
                case.get("invariants", {}).get("no_suspicious_success") is True
                for case in case_receipts
            ),
        },
        "side_effects": _side_effects(
            cases=case_receipts,
            provider_transport=provider_transport,
        ),
        "secret_handling": {
            "credential_present": bool(
                settings is not None and settings.api_key and settings.api_key.strip()
            ),
            "credential_serialized": False,
            "receipt_body_free": True,
        },
    }
    receipt["receipt_hash"] = _safe_receipt_hash(receipt)
    _write_receipt(output_root / "aggregate" / "receipt.json", receipt)
    return receipt


def build_mock_mutation_confirmation_matrix(*, output_root: Path) -> dict[str, Any]:
    return run_mutation_confirmation_matrix(
        output_root=output_root,
        settings_factory=_mock_settings,
        client_factory=lambda _settings, project_root, case_id, _arm_id: _MockMatrixMutationClient(
            project_root,
            _case_by_id(case_id),
        ),
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


def validate_mutation_confirmation_matrix_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(receipt)
    if data.get("schema") != SCHEMA:
        raise ValueError("BN receipt schema mismatch")
    if data.get("receipt_hash") != _safe_receipt_hash(
        {key: value for key, value in data.items() if key != "receipt_hash"}
    ):
        raise ValueError("BN receipt hash mismatch")
    if data.get("status") not in {"passed", "needs_followup", "blocked"}:
        raise ValueError("BN receipt status mismatch")
    if data.get("validation_command_sha256") != canonical_hash(EXACT_VALIDATION_COMMAND):
        raise ValueError("BN validation command hash mismatch")
    if data.get("tool_allowlist") != TOOL_ALLOWLIST:
        raise ValueError("BN tool allowlist mismatch")
    if (data.get("secret_handling") or {}).get("credential_serialized") is not False:
        raise ValueError("BN credential serialization flag mismatch")
    if (data.get("side_effects") or {}).get("used_in_prompt") is not False:
        raise ValueError("BN prompt-use flag mismatch")
    for case in data.get("cases") or []:
        arms = case.get("arms") if isinstance(case.get("arms"), Mapping) else {}
        for arm in arms.values():
            gate = arm.get("mutation_gate") or {}
            if arm.get("status") == "passed":
                if gate.get("passed") is not True:
                    raise ValueError("BN passed arm lacks mutation gate pass")
                if gate.get("writer_observed") is not True:
                    raise ValueError("BN passed arm lacks writer evidence")
                if gate.get("provider_exact_validation_observed") is not True:
                    raise ValueError("BN passed arm lacks exact provider validation")
                if gate.get("provider_validation_exit_zero") is not True:
                    raise ValueError("BN passed arm lacks successful provider validation")
                if gate.get("independent_exact_validation_exit_zero") is not True:
                    raise ValueError("BN passed arm lacks independent validation")
            if gate.get("changed_source_files") and gate.get("changed_source_files") != [TARGET_FILE]:
                raise ValueError("BN arm changed an out-of-scope source file")
            for event in arm.get("tool_events") or []:
                if event.get("tool_name") not in TOOL_ALLOWLIST:
                    raise ValueError("BN tool escaped allowlist")
                if event.get("tool_name") == "command_executor" and event.get(
                    "command_is_exact_validation"
                ):
                    if event.get("cwd_is_project_root") is not True:
                        raise ValueError("BN exact validation cwd was not project root")
    if data.get("status") == "passed":
        invariants = data.get("invariants") or {}
        if not invariants or not all(invariants.values()):
            raise ValueError("BN passed receipt has failing invariant")
        usage = data.get("aggregate_usage") or {}
        if usage.get("prompt_token_delta", 0) <= 0 or usage.get("total_token_delta", 0) <= 0:
            raise ValueError("BN passed receipt lacks aggregate token reduction")
    encoded = json.dumps(data, ensure_ascii=False)
    if _SECRET_RE.search(encoded):
        raise ValueError("BN receipt contains a secret-shaped value")
    _assert_no_bodies(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_mutation_confirmation_matrix(output_root=args.output_root)
    validate_mutation_confirmation_matrix_receipt(result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "case_count": result["case_count"],
                "arm_count": result["arm_count"],
                "receipt_hash": result["receipt_hash"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
