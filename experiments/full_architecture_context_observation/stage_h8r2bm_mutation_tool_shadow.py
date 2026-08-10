"""Run an isolated mutation/tool-task shadow gate for reusable compaction.

H8-R2BL proved a read-only DeepSeek fact-contract matrix.  This stage moves
one step closer to real task execution while keeping the blast radius tiny: a
temporary calculator project, one scoped writer target, and one exact
validation command.  The reusable arm remains explicit harness opt-in; this is
not a production prompt-use transition.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Protocol, Sequence
from unittest.mock import patch

from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.runtime_controller import StateUpdater
from autonomous_iteration.task_models import Task, TaskExecutionContext, TaskStatus
from core.config import LLMSettings, ProviderToolExecutionBudgetProfile
from core.llm import LLMClient, LLMRequest, LLMResponse, LLMToolCall, LLMToolFunctionCall
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
    RuntimeStateMetadata,
    ToolContractMetadata,
)
from tools.command_tool import COMMAND_EXECUTOR_DEFINITION, command_executor
from tools.file_patch_writer import FILE_PATCH_WRITER_DEFINITION, file_patch_writer_executor
from tools.file_reader import FILE_READER_DEFINITION, file_reader_executor
from tools.tool_executor import ToolExecutor
from tools.tool_registry import ToolRegistry

from experiments.full_architecture_context_observation.stage_h8r2aj_real_execution import (
    _OfflineLogger,
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
    _failed_attempt,
    _provider_descriptor,
    _safe_receipt_hash,
)


SCHEMA = "phase-h8r2bm-mutation-tool-shadow-v1"
CLAIM_BOUNDARY = "isolated_mutation_tool_shadow_no_production_prompt_use"
EXACT_VALIDATION_COMMAND = "python -m pytest -q tests/test_calculator.py"
TASK_ID = "h8r2bm-calculator-divide-value-error"
TARGET_FILE = "calculator.py"
TEST_FILE = "tests/test_calculator.py"
TOOL_ALLOWLIST = ["file_reader", "file_patch_writer", "command_executor"]

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


@dataclass
class PreparedMutationContext:
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


class _RecordingProviderLLMClient:
    """Real provider client wrapper that records body-free request telemetry."""

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


class _MockMutationClient:
    """Deterministic provider-shaped client for the local focused gate."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
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
                        id=f"bm-read-{self._index}",
                        function=LLMToolFunctionCall(
                            name="file_reader",
                            arguments=json.dumps({"file_path": TARGET_FILE}),
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
                        id=f"bm-patch-{self._index}",
                        function=LLMToolFunctionCall(
                            name="file_patch_writer",
                            arguments=json.dumps(
                                {
                                    "file_path": TARGET_FILE,
                                    "operation_kind": "modify_symbol",
                                    "symbol_name": "divide",
                                    "replacement_text": (
                                        "def divide(a, b):\n"
                                        "    if b == 0:\n"
                                        "        raise ValueError(\"division by zero\")\n"
                                        "    return a / b"
                                    ),
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
                        id=f"bm-validate-{self._index}",
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


def _mutation_settings(settings: LLMSettings) -> LLMSettings:
    return settings.model_copy(
        update={
            "provider_tool_execution_enabled": True,
            "provider_tool_initial_context_projection_enabled": False,
            "provider_tool_initial_context_mutation_enabled": True,
            "provider_tool_completion_outcome_feedback_enabled": False,
            "provider_tool_execution_budget_profile": (
                ProviderToolExecutionBudgetProfile.REAL_MUTATION
            ),
            "provider_tool_execution_max_rounds": 8,
            "context_max_prompt_tokens": 12_288,
            "context_reserved_prompt_tokens": 128,
            "tool_event_reasoning_mode": ReasoningMode.DISABLED,
            "transport_retries": 0,
            "retry_initial_delay": 0.0,
            "retry_max_delay": 0.0,
            "temperature": 0.0,
            "reasoning_capability_profile": (
                settings.reasoning_capability_profile
                or ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN
            ),
        }
    )


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
            timeout_seconds=60.0,
            temperature=0.0,
            transport_retries=0,
            retry_initial_delay=0.0,
            retry_max_delay=0.0,
            reasoning_capability_profile=(
                ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN
            ),
        )
    )


def _seed_dialog(short_memory: ShortMemory, count: int = 12) -> None:
    markers = (
        "write_scope=calculator.py",
        "forbidden_target=README.md",
        "target_symbol=divide",
        "required_exception=ValueError",
        "validation_command=python_m_pytest_q_tests_test_calculator_py",
        "api_rule=preserve_api",
    )
    for index in range(count):
        marker = markers[index % len(markers)]
        repetitions = 128 if index < count - 2 else 10
        short_memory.add_message(
            "assistant",
            (
                f"Mutation planning note {index}: {marker}. "
                "The stable constraint is scoped and must be retained. "
                + (f"low-value-h8r2bm-{index} " * repetitions)
            ),
        )


def _compaction_sink(record: dict[str, Any]) -> DurableArtifactReference:
    artifact_id = f"h8r2bm-artifact-{record['compaction_id']}"
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
        "Mutation tool shadow setup. Stable facts: write_scope=calculator.py; "
        "forbidden_target=README.md; target_symbol=divide; "
        "required_exception=ValueError; validation_command="
        "python_m_pytest_q_tests_test_calculator_py; api_rule=preserve_api."
    )
    raw_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / "raw-memory"),
        max_prompt_chars=140_000,
    )
    raw_context = raw_builder.build(
        "h8r2bm mutation raw",
        include_environment=False,
        limit=12,
        system_prompt=system_prompt,
    )
    compact_builder = MemoryContextBuilder(
        short_memory=short_memory,
        memory_store=MemoryStore(tmp_root / "compact-memory"),
        max_prompt_chars=4_500,
    )
    compact_builder.set_checkpoint_handlers(compaction_sink=_compaction_sink)
    compact_context = compact_builder.build(
        "h8r2bm mutation compact",
        include_environment=False,
        limit=12,
        system_prompt=system_prompt,
    )
    return raw_context, compact_context


def _task_authority_candidate() -> ContextCandidate:
    return ContextCandidate(
        candidate_id="h8r2bm-task-authority",
        kind=ContextCandidateKind.TASK,
        source_id="h8r2bm:task-contract",
        role="user",
        content=(
            "Task contract: Modify only calculator.py. Target symbol: divide. "
            "If b == 0, raise ValueError. Preserve the existing public API. "
            "Do not modify README.md or tests. Use tools in this order: "
            "file_reader for calculator.py, one scoped file_patch_writer call "
            "for calculator.py, then command_executor with exact command "
            f"{EXACT_VALIDATION_COMMAND}. The command cwd must be the project root."
        ),
        retention=ContextCandidateRetention.REQUIRED,
        priority=100,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.AUTHORITATIVE,
        freshness=ContextCandidateFreshness.CURRENT,
    )


def _summary_candidate_from_binding(
    binding: ContextCompactionBinding,
    *,
    candidate_id: str,
) -> ContextCandidate:
    return ContextCandidate(
        candidate_id=candidate_id,
        kind=ContextCandidateKind.ARTIFACT,
        source_id=binding.record.source_fingerprint,
        role="user",
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
    assembler = ContextAssembler(renderer=lambda _payload: "", token_counter=token_counter)
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


def _prepare_context(tmp_root: Path) -> PreparedMutationContext:
    raw_context, compact_context = _build_contexts(tmp_root)
    authority = _task_authority_candidate()
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
            context_id="h8r2bm-compact-context",
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
                preflight_id="h8r2bm-preflight",
                token_counter=counter,
            )
            simulation = simulate_reusable_compaction_prompt_use(
                binding=binding,
                candidates=raw_candidates,
                policy=policy,
                renderer=MemoryContextBuilder._render_candidates,
                preflight=preflight,
                simulation_id="h8r2bm-simulation",
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

    return PreparedMutationContext(
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


def _build_fixture(project_root: Path) -> dict[str, str]:
    (project_root / "tests").mkdir(parents=True, exist_ok=True)
    (project_root / "pytest.ini").write_text(
        "[pytest]\naddopts = -p no:cacheprovider\n",
        encoding="utf-8",
    )
    (project_root / TARGET_FILE).write_text(
        """\
def add(a, b):
    return a + b


def divide(a, b):
    return a / b
""",
        encoding="utf-8",
    )
    (project_root / TEST_FILE).write_text(
        """\
import pytest

from calculator import add, divide


def test_add():
    assert add(2, 3) == 5


def test_divide():
    assert divide(6, 3) == 2


def test_divide_zero_raises_value_error():
    with pytest.raises(ValueError):
        divide(1, 0)
""",
        encoding="utf-8",
    )
    return _source_file_hashes(project_root)


def _source_file_hashes(project_root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(project_root.rglob("*")):
        if path.is_file() and not _ignored_runtime_artifact(path, project_root):
            hashes[path.relative_to(project_root).as_posix()] = canonical_hash(
                path.read_bytes().hex()
            )
    return hashes


def _ignored_runtime_artifact(path: Path, project_root: Path) -> bool:
    relative = path.relative_to(project_root)
    parts = set(relative.parts)
    return (
        "__pycache__" in parts
        or ".pytest_cache" in parts
        or relative.name.endswith(".pyc")
    )


def _changed_files(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    paths = sorted(set(before) | set(after))
    return [path for path in paths if before.get(path) != after.get(path)]


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FILE_READER_DEFINITION, file_reader_executor)
    registry.register(FILE_PATCH_WRITER_DEFINITION, file_patch_writer_executor)
    registry.register(COMMAND_EXECUTOR_DEFINITION, command_executor)
    return registry


def _runtime(client: SupportsComplete) -> Any:
    registry = _registry()
    state = RuntimeStateMetadata(goal="H8-R2BM isolated mutation shadow")
    controller = SimpleNamespace(
        state=state,
        state_updater=StateUpdater(),
        replay_tool_result=lambda *_args, **_kwargs: None,
        prepare_tool_call=lambda *_args, **_kwargs: True,
        observe_tool_result=lambda *_args, **_kwargs: True,
    )
    return SimpleNamespace(
        llm_client=client,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, logger=_OfflineLogger()),
        runtime_controller=controller,
        runtime_diagnostics_hooks=None,
        logger=_OfflineLogger(),
        enhanced_ui=None,
        session_id="h8r2bm-mutation-shadow-session",
        _project_environments={},
        _sanitize_tool_metadata=lambda value: value,
        _map_reason_to_enum=lambda _value: "capability_match",
        _show_tool_running=lambda *_args, **_kwargs: None,
        _show_tool_result=lambda *_args, **_kwargs: None,
        _log_tool_start=lambda *_args, **_kwargs: None,
        _log_tool_complete=lambda *_args, **_kwargs: None,
        _summarize_metadata_output=lambda value: value,
    )


def _task() -> Task:
    return Task(
        id=TASK_ID,
        description=(
            "Modify only calculator.py: update divide so division by zero raises "
            "ValueError while preserving the existing add/divide API. Then run "
            "the exact validation command."
        ),
        kind="implement",
        read_files=[TARGET_FILE, TEST_FILE],
        write_files=[TARGET_FILE],
        validation_command=EXACT_VALIDATION_COMMAND,
        tags=["h8r2bm", "mutation_shadow", "isolated"],
    )


def _tool_event_summary(
    attributes: Mapping[str, Any],
    *,
    project_root: Path,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for loop in attributes.get("tool_loops") or []:
        for event in loop.get("events") or []:
            if not isinstance(event, Mapping):
                continue
            input_metadata = event.get("input_metadata") or {}
            output_metadata = event.get("output_metadata") or {}
            result = output_metadata.get("result") if isinstance(output_metadata, Mapping) else {}
            result = result if isinstance(result, Mapping) else {}
            tool_name = str(event.get("tool_name") or input_metadata.get("tool_name") or "")
            file_path = str(input_metadata.get("file_path") or "")
            command = str(input_metadata.get("command") or "")
            cwd = str(input_metadata.get("cwd") or "")
            relative_file = ""
            if file_path:
                try:
                    relative_file = (
                        Path(file_path)
                        .expanduser()
                        .resolve()
                        .relative_to(project_root.resolve())
                        .as_posix()
                    )
                except Exception:
                    relative_file = ""
            events.append(
                {
                    "tool_name": tool_name,
                    "event_type": str(event.get("event_type") or ""),
                    "status": str(event.get("status") or ""),
                    "round_index": int(event.get("round_index") or 0),
                    "file_path_sha256": _sha256_text(file_path) if file_path else "",
                    "relative_file": relative_file,
                    "command_sha256": canonical_hash(command) if command else "",
                    "command_is_exact_validation": command == EXACT_VALIDATION_COMMAND,
                    "cwd_sha256": _sha256_text(cwd) if cwd else "",
                    "cwd_is_project_root": bool(cwd)
                    and Path(cwd).expanduser().resolve() == project_root.resolve(),
                    "mode": str(input_metadata.get("mode") or ""),
                    "output_success": result.get("success"),
                    "exit_code": result.get("exit_code"),
                    "error_type": str((event.get("failure") or {}).get("error_type") or ""),
                }
            )
    return events


def _attempts_summary(attributes: Mapping[str, Any]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for attempt in attributes.get("attempts") or []:
        if not isinstance(attempt, Mapping):
            continue
        attempts.append(
            {
                "tool_name": str(attempt.get("tool_name") or ""),
                "round_index": int(attempt.get("round_index") or 0),
                "success": bool(attempt.get("success")),
                "error_type": str(attempt.get("error_type") or ""),
                "duplicate": bool(attempt.get("duplicate_of")),
            }
        )
    return attempts


def _usage_summary(responses: Sequence[LLMResponse]) -> dict[str, Any]:
    prompt = 0
    completion = 0
    total = 0
    complete = bool(responses)
    finish_reasons: list[str] = []
    for response in responses:
        usage = dict(response.usage or {})
        if not all(key in usage for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
            complete = False
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        total += int(usage.get("total_tokens") or 0)
        finish_reasons.append(str(response.finish_reason or ""))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "usage_complete": complete,
        "finish_reasons": finish_reasons,
    }


@contextmanager
def _validation_environment() -> Any:
    keys = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
    }
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _independent_validation(project_root: Path) -> dict[str, Any]:
    result = subprocess.run(
        EXACT_VALIDATION_COMMAND,
        shell=True,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy() | {"PYTHONDONTWRITEBYTECODE": "1"},
    )
    return {
        "command_sha256": canonical_hash(EXACT_VALIDATION_COMMAND),
        "exit_zero": result.returncode == 0,
        "exit_code": result.returncode,
    }


def _mutation_gate(
    *,
    result_status: TaskStatus,
    before_hashes: Mapping[str, str],
    after_hashes: Mapping[str, str],
    target_before_sha256: str,
    target_after_sha256: str,
    tool_events: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
    independent_validation: Mapping[str, Any],
) -> dict[str, Any]:
    changed = _changed_files(before_hashes, after_hashes)
    writer_observed = any(
        attempt.get("tool_name") == "file_patch_writer"
        and attempt.get("success") is True
        for attempt in attempts
    )
    validation_events = [
        event
        for event in tool_events
        if event.get("tool_name") == "command_executor"
        and event.get("event_type") == "completed"
        and event.get("command_is_exact_validation") is True
        and event.get("cwd_is_project_root") is True
    ]
    provider_exact_validation_observed = bool(validation_events)
    provider_validation_exit_zero = any(
        event.get("output_success") is True and event.get("exit_code") == 0
        for event in validation_events
    )
    only_scoped = changed == [TARGET_FILE]
    suspicious_success = bool(target_before_sha256 != target_after_sha256) and (
        not provider_exact_validation_observed or not provider_validation_exit_zero
    )
    return {
        "target_before_sha256": target_before_sha256,
        "target_after_sha256": target_after_sha256,
        "target_changed": target_before_sha256 != target_after_sha256,
        "changed_source_files": changed,
        "only_scoped_source_file_changed": only_scoped,
        "writer_observed": writer_observed,
        "provider_exact_validation_observed": provider_exact_validation_observed,
        "provider_validation_exit_zero": provider_validation_exit_zero,
        "independent_exact_validation_exit_zero": bool(
            independent_validation.get("exit_zero")
        ),
        "task_status": result_status.value,
        "suspicious_success": suspicious_success,
        "passed": bool(
            result_status == TaskStatus.COMPLETED
            and writer_observed
            and provider_exact_validation_observed
            and provider_validation_exit_zero
            and independent_validation.get("exit_zero") is True
            and only_scoped
            and target_before_sha256 != target_after_sha256
            and not suspicious_success
        ),
    }


def _run_arm(
    *,
    arm_id: str,
    candidates: list[ContextCandidate],
    output_root: Path,
    settings: LLMSettings,
    client_factory: Callable[[LLMSettings, Path, str], SupportsComplete],
    provider_transport: bool,
    token_counter: Any | None,
) -> dict[str, Any]:
    with TemporaryDirectory(prefix=f"h8r2bm-{arm_id}-") as tmp:
        project_root = Path(tmp) / "calculator_project"
        project_root.mkdir(parents=True, exist_ok=True)
        before_hashes = _build_fixture(project_root)
        target = project_root / TARGET_FILE
        target_before_sha256 = canonical_hash(target.read_bytes().hex())
        client = client_factory(settings, project_root, arm_id)
        if getattr(client, "settings", None) is None:
            setattr(client, "settings", settings)
        task = _task()
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
        status = "passed" if mutation_gate["passed"] else "needs_followup"
        return {
            "arm_id": arm_id,
            "status": status,
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


def _aggregate_usage(arms: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_id = {str(arm.get("arm_id")): arm for arm in arms}
    raw = by_id.get("raw", {})
    reusable = by_id.get("reusable", {})
    raw_usage = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {}
    reusable_usage = (
        reusable.get("usage") if isinstance(reusable.get("usage"), Mapping) else {}
    )
    raw_prompt = int(raw_usage.get("prompt_tokens") or 0)
    reusable_prompt = int(reusable_usage.get("prompt_tokens") or 0)
    raw_total = int(raw_usage.get("total_tokens") or 0)
    reusable_total = int(reusable_usage.get("total_tokens") or 0)
    return {
        "raw_prompt_tokens": raw_prompt,
        "reusable_prompt_tokens": reusable_prompt,
        "prompt_token_delta": raw_prompt - reusable_prompt,
        "raw_total_tokens": raw_total,
        "reusable_total_tokens": reusable_total,
        "total_token_delta": raw_total - reusable_total,
        "usage_complete": bool(arms)
        and all((arm.get("usage") or {}).get("usage_complete") is True for arm in arms),
    }


def _side_effects(
    *,
    arms: Sequence[Mapping[str, Any]],
    provider_transport: bool,
) -> dict[str, Any]:
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
    if usage.get("prompt_token_delta", 0) <= 0 or usage.get("total_token_delta", 0) <= 0:
        return "needs_followup"
    return "passed"


def _default_client_factory(settings: LLMSettings, _project_root: Path, _arm_id: str) -> SupportsComplete:
    return _RecordingProviderLLMClient(settings)


def run_mutation_tool_shadow(
    *,
    output_root: Path,
    settings_factory: Callable[[], LLMSettings] | None = None,
    client_factory: Callable[[LLMSettings, Path, str], SupportsComplete] | None = None,
    provider_transport: bool = True,
    token_counter: Any | None = None,
) -> dict[str, Any]:
    with TemporaryDirectory(prefix="h8r2bm-context-") as tmp:
        prepared = _prepare_context(Path(tmp))

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
        factory = client_factory or _default_client_factory
        arms.append(
            _run_arm(
                arm_id="raw",
                candidates=prepared.raw_candidates,
                output_root=output_root,
                settings=settings,
                client_factory=factory,
                provider_transport=provider_transport,
                token_counter=token_counter,
            )
        )
        arms.append(
            _run_arm(
                arm_id="reusable",
                candidates=prepared.reusable_candidates,
                output_root=output_root,
                settings=settings,
                client_factory=factory,
                provider_transport=provider_transport,
                token_counter=token_counter,
            )
        )

    aggregate_usage = _aggregate_usage(arms)
    status = _status(arms, setup_reasons)
    receipt = {
        "schema": SCHEMA,
        "status": status,
        "claim_boundary": CLAIM_BOUNDARY,
        "setup_reasons": setup_reasons,
        "provider_descriptor": provider_descriptor,
        "task": {
            "task_id": TASK_ID,
            "kind": "implement",
            "read_files": [TARGET_FILE, TEST_FILE],
            "write_files": [TARGET_FILE],
            "description_sha256": canonical_hash(_task().description),
        },
        "validation_command_sha256": canonical_hash(EXACT_VALIDATION_COMMAND),
        "tool_allowlist": list(TOOL_ALLOWLIST),
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
            "provider_usage_complete": bool(arms)
            and all((arm.get("usage") or {}).get("usage_complete") is True for arm in arms),
            "provider_prompt_tokens_reduced": (
                aggregate_usage.get("prompt_token_delta", 0) > 0
            ),
            "provider_total_tokens_reduced": (
                aggregate_usage.get("total_token_delta", 0) > 0
            ),
            "no_suspicious_success": bool(arms)
            and all(
                (arm.get("mutation_gate") or {}).get("suspicious_success") is False
                for arm in arms
            ),
        },
        "side_effects": _side_effects(arms=arms, provider_transport=provider_transport),
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


def build_mock_mutation_pair(*, output_root: Path) -> dict[str, Any]:
    return run_mutation_tool_shadow(
        output_root=output_root,
        settings_factory=lambda: LLMSettings(
            _env_file=None,
            provider="openai-compatible",
            base_url="https://api.deepseek.com",
            api_key="test-key",
            model="fake-deepseek-v4-flash",
            temperature=0.0,
            transport_retries=0,
            reasoning_capability_profile=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
        ),
        client_factory=lambda _settings, project_root, _arm_id: _MockMutationClient(
            project_root
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


def validate_mutation_shadow_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(receipt)
    if data.get("schema") != SCHEMA:
        raise ValueError("BM receipt schema mismatch")
    if data.get("receipt_hash") != _safe_receipt_hash(
        {key: value for key, value in data.items() if key != "receipt_hash"}
    ):
        raise ValueError("BM receipt hash mismatch")
    if data.get("status") not in {"passed", "needs_followup", "blocked"}:
        raise ValueError("BM receipt status mismatch")
    if data.get("validation_command_sha256") != canonical_hash(EXACT_VALIDATION_COMMAND):
        raise ValueError("BM validation command hash mismatch")
    if data.get("tool_allowlist") != TOOL_ALLOWLIST:
        raise ValueError("BM tool allowlist mismatch")
    side_effects = data.get("side_effects") or {}
    if side_effects.get("used_in_prompt") is not False:
        raise ValueError("BM reusable admission must remain prompt-use disabled")
    if (data.get("secret_handling") or {}).get("credential_serialized") is not False:
        raise ValueError("BM credential serialization flag mismatch")
    for arm in data.get("arms") or []:
        gate = arm.get("mutation_gate") or {}
        if arm.get("status") == "passed" and gate.get("passed") is not True:
            raise ValueError("BM passed arm lacks mutation gate pass")
        if gate.get("changed_source_files") and gate.get("changed_source_files") != [TARGET_FILE]:
            raise ValueError("BM arm changed an out-of-scope source file")
        if arm.get("status") == "passed":
            if gate.get("writer_observed") is not True:
                raise ValueError("BM passed arm lacks writer evidence")
            if gate.get("provider_exact_validation_observed") is not True:
                raise ValueError("BM passed arm lacks exact provider validation")
            if gate.get("provider_validation_exit_zero") is not True:
                raise ValueError("BM passed arm lacks successful provider validation")
            if gate.get("independent_exact_validation_exit_zero") is not True:
                raise ValueError("BM passed arm lacks independent validation")
        for event in arm.get("tool_events") or []:
            if event.get("tool_name") not in TOOL_ALLOWLIST:
                raise ValueError("BM tool escaped allowlist")
            if event.get("tool_name") == "command_executor" and event.get(
                "command_is_exact_validation"
            ):
                if event.get("cwd_is_project_root") is not True:
                    raise ValueError("BM exact validation cwd was not project root")
    if data.get("status") == "passed":
        invariants = data.get("invariants") or {}
        if not invariants or not all(invariants.values()):
            raise ValueError("BM passed receipt has failing invariant")
        usage = data.get("aggregate_usage") or {}
        if usage.get("prompt_token_delta", 0) <= 0 or usage.get("total_token_delta", 0) <= 0:
            raise ValueError("BM passed receipt lacks token reduction")
    encoded = json.dumps(data, ensure_ascii=False)
    if _SECRET_RE.search(encoded):
        raise ValueError("BM receipt contains a secret-shaped value")
    _assert_no_bodies(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_mutation_tool_shadow(output_root=args.output_root)
    validate_mutation_shadow_receipt(result)
    print(
        json.dumps(
            {
                "status": result["status"],
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
