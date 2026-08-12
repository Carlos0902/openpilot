"""Project Improvement Tool - Analyze concrete next-step improvements."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from metadata import ToolContractMetadata, ToolInputMetadata, ToolResultMetadata, metadata_tool_result
from metadata import (
    EnhancementCompletionComplexity,
    EnhancementCompletionDecisionValue,
    EnhancementCompletionRequest,
    EnhancementCompletionRequirement,
    RuntimeBudgetMetadata,
)

from core.exceptions import (
    ContextAssemblyBudgetError,
    InvalidLLMResponseError,
    LLMProviderError,
)
from core.reasoning import routine_tool_reasoning_policy
from autonomous_iteration.enhancement_completion_budget import EnhancementCompletionBudgetCoordinator
from memory.context_assembly import build_context_candidate_request
from metadata import ContextRequestPurpose
from autonomous_iteration.models import ProjectStateSnapshot
from autonomous_iteration.project_improvement_context import (
    build_project_improvement_analysis_candidates,
    compact_project_memory_record,
)
from memory.session_dialog import session_turn_ledger_hash
from metadata import DerivedContextProjection, SessionConstraintState, SessionIngressState
from core.project_stack import load_project_stack_preset
from memory.memory_models import MemoryType
from memory.agents.project_environment_tool import (
    build_dependency_strategy,
    build_project_dependency_context,
    infer_project_dependencies,
)
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
    ToolFailureMode,
)


README_PREVIEW_LIMIT = 1200
PROJECT_FILE_PREVIEW_LIMIT = 2400
MAX_PROJECT_PREVIEWS = 6
MAX_PROJECT_MANIFEST_FILES = 40
_MANIFEST_EXCLUDED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
_DELTA_TEXT_LIMIT = 480
_DELTA_LIST_LIMIT = 5


class _ProjectImprovementDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changed_signals: list[str] = Field(default_factory=list, max_length=_DELTA_LIST_LIMIT)
    proposed_actions: list[str] = Field(default_factory=list, max_length=_DELTA_LIST_LIMIT)
    next_decision_or_goal: str = Field(default="", max_length=_DELTA_TEXT_LIMIT)
    must_satisfy: list[str] = Field(default_factory=list, max_length=_DELTA_LIST_LIMIT)
    blocking_risks: list[str] = Field(default_factory=list, max_length=_DELTA_LIST_LIMIT)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    stack_preset_patch: "_StackPresetPatch" = Field(default_factory=lambda: _StackPresetPatch())

    @model_validator(mode="after")
    def _validate_bounded_values(self) -> "_ProjectImprovementDelta":
        for values in (
            self.changed_signals,
            self.proposed_actions,
            self.must_satisfy,
            self.blocking_risks,
        ):
            if any(not value.strip() or len(value) > _DELTA_TEXT_LIMIT for value in values):
                raise ValueError("delta list values must be non-empty and bounded")
        if any(not value.strip() or len(value) > 160 for value in self.evidence_ids):
            raise ValueError("evidence IDs must be non-empty and bounded")
        return self


class _StackPresetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_surface: str | None = Field(default=None, max_length=80)
    architecture: str | None = Field(default=None, max_length=80)
    frontend_language: str | None = Field(default=None, max_length=80)
    frontend_frameworks: list[str] | None = Field(default=None, max_length=6)
    backend_language: str | None = Field(default=None, max_length=80)
    backend_frameworks: list[str] | None = Field(default=None, max_length=6)
    ui_strategy: str | None = Field(default=None, max_length=120)
    ui_review_required: bool | None = None
    rationale: list[str] | None = Field(default=None, max_length=6)
    evidence: list[str] | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def _bound_lists(self) -> "_StackPresetPatch":
        for values in (
            self.frontend_frameworks,
            self.backend_frameworks,
            self.rationale,
            self.evidence,
        ):
            if values and any(not item.strip() or len(item) > 240 for item in values):
                raise ValueError("stack patch list values must be non-empty and bounded")
        return self


_ProjectImprovementDelta.model_rebuild()


PROJECT_STATE_READER_DEFINITION = ToolDefinition(
    name="project_state_reader",
    display_name="Project State Reader",
    description="Read generated project state, README, safe target files, and relevant memory context",
    version="1.0.0",
    capabilities=[ToolCapability.FILE_READ],
    permission_level=PermissionLevel.LOW,
    contract_metadata=ToolContractMetadata(
        tool_name='project_state_reader',
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=['project_path'],
        input_defaults={'goal': '', 'written_files': [], 'readme_path': '', 'run_command': '', 'memory_query': '', 'validation_context': {}},
    ),
    timeout_seconds=20,
    max_retries=0,
    failure_modes=[
        ToolFailureMode(
            error_type="invalid_input",
            description="Project path is missing or unreadable",
            recovery_strategy="Provide an existing project_path",
        ),
    ],
    tags=["project", "state", "memory", "iteration"],
    audit_required=False,
)


PROJECT_IMPROVEMENT_TOOL_DEFINITION = ToolDefinition(
    name="project_improvement_tool",
    display_name="Project Improvement Tool",
    description="Analyze a generated project and produce concrete next-iteration improvement goals",
    version="1.0.0",
    capabilities=[ToolCapability.LLM_CALL, ToolCapability.FILE_READ],
    permission_level=PermissionLevel.MEDIUM,
    contract_metadata=ToolContractMetadata(
        tool_name='project_improvement_tool',
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=['project_path', 'goal'],
        input_defaults={'written_files': [], 'run_command': '', 'iteration': 0, 'validation_result': {}, 'readme_path': '', 'prompt_context': {}},
    ),
    timeout_seconds=420,
    max_retries=0,
    failure_modes=[
        ToolFailureMode(
            error_type="llm_error",
            description="The LLM failed to return a usable improvement analysis",
            recovery_strategy="Use deterministic validation findings as the next iteration goal",
        ),
        ToolFailureMode(
            error_type="invalid_input",
            description="Project path or goal is missing",
            recovery_strategy="Provide project_path and goal",
        ),
    ],
    tags=["project", "improvement", "iteration", "evaluation"],
    audit_required=False,
)


@metadata_tool_result('project_state_reader')
def project_state_reader_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    params = input_metadata.to_params()
    """Read current project state using a strict tool-style contract."""
    project_path = Path(params["project_path"]).expanduser().resolve(strict=False)
    goal = str(params.get("goal") or "")
    written_files = _coerce_path_list(params.get("written_files", []))
    readme_path = Path(params.get("readme_path") or project_path / "README.md").expanduser()
    run_command = str(params.get("run_command") or "").strip()
    validation_context = params.get("validation_context") or {}
    memory_query = str(params.get("memory_query") or goal or project_path.name)
    memory_store = params.get("_memory_store")

    resolved_files = _resolve_project_files(project_path, written_files)
    readme_text = _read_text(readme_path)
    if not run_command:
        run_command = _extract_run_command(readme_text)

    memory_records: list[dict[str, Any]] = []
    if memory_store and hasattr(memory_store, "query"):
        try:
            query_result = memory_store.query(
                memory_query,
                memory_types=[MemoryType.PROJECT, MemoryType.TASK, MemoryType.FEEDBACK, MemoryType.LONG_TERM, MemoryType.SHORT_TERM],
                limit=8,
            )
            memory_records = [
                compact_project_memory_record(_memory_mapping(memory))
                for memory in query_result.memories
                if _memory_matches_project(memory, project_path)
            ]
            if hasattr(memory_store, "load_all"):
                all_iteration_memories = sorted(
                    (
                        memory
                        for memory_type in (MemoryType.PROJECT, MemoryType.TASK)
                        for memory in memory_store.load_all(memory_type)
                        if "autonomous_iteration" in memory.tags
                        and _memory_matches_project(memory, project_path)
                    ),
                    key=lambda memory: memory.timestamp,
                )
                latest_iteration_memories = all_iteration_memories[-3:]
                exact_iteration_memories = [
                    memory
                    for memory in all_iteration_memories
                    if _memory_matches_exact_identity(
                        memory,
                        goal=goal,
                        memory_query=memory_query,
                    )
                ][-3:]
                iteration_memories = [
                    *latest_iteration_memories,
                    *exact_iteration_memories,
                ]
                known_ids = {item["id"] for item in memory_records}
                for memory in iteration_memories:
                    if memory.id in known_ids:
                        continue
                    memory_records.append(compact_project_memory_record(_memory_mapping(memory)))
                    known_ids.add(memory.id)
                env_memories = [
                    memory
                    for memory in memory_store.load_all(MemoryType.SHORT_TERM)
                    if "project_environment" in memory.tags
                    and _memory_matches_project(memory, project_path)
                ][-3:]
                for memory in env_memories:
                    if memory.id in known_ids:
                        continue
                    memory_records.append(compact_project_memory_record(_memory_mapping(memory)))
                    known_ids.add(memory.id)
        except Exception:
            memory_records = []

    file_summaries = []
    safe_target_files = []
    for path in resolved_files:
        if not path.exists() or path.is_dir():
            continue
        text = _read_text(path)
        file_summaries.append(
            {
                "path": str(path),
                "name": path.name,
                "suffix": path.suffix,
                "chars": str(len(text)),
                "preview": text[:1200],
            }
        )
        safe_target_files.append(str(path))
    known_summary_paths = {str(item.get("path") or "") for item in file_summaries}
    for path in _project_file_manifest(project_path):
        if str(path) in known_summary_paths:
            continue
        file_summaries.append(
            {
                "path": str(path),
                "name": path.name,
                "suffix": path.suffix,
                "chars": "",
                "preview": "",
            }
        )

    validation_errors = validation_context.get("validation_errors") if isinstance(validation_context, dict) else []
    validation_warnings = validation_context.get("warnings") if isinstance(validation_context, dict) else []
    suffixes = sorted({str(item.get("suffix") or "") for item in file_summaries if item.get("suffix")})
    installed_packages = _latest_environment_packages(memory_records)
    detected_packages = infer_project_dependencies(project_path, [str(path) for path in resolved_files])
    dependencies = build_project_dependency_context(
        project_path=project_path,
        files=[str(path) for path in resolved_files],
        detected_packages=detected_packages,
        installed_packages=installed_packages,
        dependency_source="project_state_reader",
        readme_text=readme_text,
    )
    dependency_strategy = build_dependency_strategy(dependencies)
    stack_preset = load_project_stack_preset(project_path)
    return {
        "project_path": str(project_path),
        "goal": goal,
        "written_files": [str(path) for path in resolved_files],
        "file_summaries": file_summaries,
        "readme_summary": readme_text[:1600],
        "run_command": run_command,
        "memory_records": memory_records,
        "validation_context": validation_context,
        "safe_target_files": safe_target_files,
        "diagnostic_evidence": {
            "readme_present": bool(readme_text.strip()),
            "run_command_known": bool(run_command),
            "file_count": len(file_summaries),
            "safe_target_count": len(safe_target_files),
            "file_suffixes": suffixes,
            "memory_count": len(memory_records),
        },
        "runtime_evidence": [str(item) for item in [run_command, *(validation_errors or []), *(validation_warnings or [])] if str(item)],
        "test_evidence": [str(item) for item in validation_errors or [] if "test" in str(item).lower() or "smoke" in str(item).lower()],
        "module_summaries": [f"{item['name']} ({item['suffix']}, {item['chars']} chars)" for item in file_summaries[:8]],
        "dependencies": [dependency.to_json_dict() for dependency in dependencies],
        "dependency_strategy": dependency_strategy.to_json_dict(),
        "stack_preset": stack_preset.to_json_dict() if stack_preset else None,
    }


def _memory_mapping(memory: Any) -> dict[str, Any]:
    return {
        "id": memory.id,
        "type": memory.memory_type.value,
        "content": memory.content,
        "tags": memory.tags,
        "confidence": memory.confidence,
        "timestamp": memory.timestamp,
        "attributes": memory.attributes,
    }


def _memory_matches_project(memory: Any, project_path: Path) -> bool:
    """Fail closed for project-scoped memories without current-project identity."""

    attributes = getattr(memory, "attributes", {}) or {}
    raw_path = attributes.get("project_path") if isinstance(attributes, dict) else None
    if raw_path:
        try:
            return Path(str(raw_path)).expanduser().resolve() == project_path.resolve()
        except OSError:
            return False
    memory_type = getattr(memory, "memory_type", None)
    if memory_type in {MemoryType.FEEDBACK, MemoryType.LONG_TERM}:
        return True
    canonical_project = str(project_path.expanduser().resolve(strict=False)).casefold()
    tags = {str(tag).strip().casefold() for tag in getattr(memory, "tags", []) if str(tag).strip()}
    return canonical_project in tags


def _memory_matches_exact_identity(
    memory: Any,
    *,
    goal: str,
    memory_query: str,
) -> bool:
    """Match typed iteration identity fields without fuzzy text inference."""

    requested = {
        str(value).strip().casefold()
        for value in (goal, memory_query)
        if str(value).strip()
    }
    if not requested:
        return False
    attributes = getattr(memory, "attributes", {}) or {}
    if not isinstance(attributes, dict):
        return False
    recorded = {
        str(attributes.get(key) or "").strip().casefold()
        for key in ("goal", "selected_candidate_id", "selected_candidate")
        if str(attributes.get(key) or "").strip()
    }
    return bool(requested & recorded)


@metadata_tool_result('project_improvement_tool')
def project_improvement_tool_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    params = input_metadata.to_params()
    """Produce a project improvement report using the current LLM when available."""
    project_path = Path(params["project_path"]).expanduser()
    goal = str(params["goal"])
    written_files = _coerce_path_list(params.get("written_files", []))
    run_command = str(params.get("run_command") or "").strip()
    iteration = int(params.get("iteration") or 0)
    validation_result = params.get("validation_result") or {}
    readme_path = Path(params.get("readme_path") or project_path / "README.md").expanduser()
    prompt_context = params.get("prompt_context") if isinstance(params.get("prompt_context"), dict) else {}
    llm_client = params.get("_llm_client")
    session_constraints = params.get("_session_constraints")
    session_ingress_state = params.get("_session_ingress_state")
    context_projection = params.get("_context_projection")
    if session_constraints is not None and not isinstance(session_constraints, SessionConstraintState):
        raise TypeError("session constraints must be a validated SessionConstraintState")
    if session_ingress_state is not None and not isinstance(session_ingress_state, SessionIngressState):
        raise TypeError("session ingress state must be a validated SessionIngressState")
    if session_ingress_state is not None:
        if session_constraints is not None and session_constraints.canonical_hash != session_ingress_state.session_constraints.canonical_hash:
            raise ValueError("session ingress and explicit constraints differ")
        session_constraints = session_ingress_state.session_constraints
        expected_turn_hash = str(input_metadata.session_turn_source_hash or "")
        if expected_turn_hash and expected_turn_hash != session_turn_ledger_hash(session_ingress_state):
            raise ValueError("session turn source hash mismatch")
    if context_projection is not None and not isinstance(context_projection, DerivedContextProjection):
        raise TypeError("context projection must be a validated DerivedContextProjection")
    if context_projection is not None:
        if session_ingress_state is None or session_constraints is None:
            raise ValueError("context projection requires session ingress and constraints")
        if context_projection.session_turn_source_hash != session_turn_ledger_hash(session_ingress_state):
            raise ValueError("context projection turn source hash mismatch")
        if context_projection.session_constraints_hash != session_constraints.canonical_hash:
            raise ValueError("context projection constraint hash mismatch")
    runtime_budget = params.get("_runtime_budget")
    if not isinstance(runtime_budget, RuntimeBudgetMetadata):
        runtime_budget = RuntimeBudgetMetadata()
    completion_budget = EnhancementCompletionBudgetCoordinator(runtime_budget)

    file_previews = _read_project_previews(project_path, written_files)
    fallback = _fallback_report(goal, validation_result)
    deterministic_stack_update = _deterministic_stack_patch(prompt_context)
    fallback.update(
        {
            "project_path": str(project_path),
            "goal": goal,
            "iteration": iteration,
            "stack_preset_update": deterministic_stack_update,
        }
    )
    if not llm_client or not hasattr(llm_client, "complete"):
        if bool(params.get("_enhancement_required")):
            raise LLMProviderError(
                "Required project_improvement provider is unavailable."
            )
        return _mark_fallback(fallback, "No LLM client available for project_improvement_tool.")

    readme_preview = _truncate_text(_read_text(readme_path), README_PREVIEW_LIMIT)
    product_judgment = prompt_context.get("product_judgment") or {}
    quality_rubric = prompt_context.get("quality_rubric") or []
    stack_preset = prompt_context.get("stack_preset") or {}
    ui_iteration_contract = prompt_context.get("ui_iteration_contract") or {}
    project_state = ProjectStateSnapshot(
        project_path=str(project_path),
        goal=goal,
        written_files=[str(path) for path in written_files],
        file_summaries=[
            {
                "path": str(written_files[index]) if index < len(written_files) else f"preview-{index + 1}",
                "name": Path(written_files[index]).name if index < len(written_files) else f"preview-{index + 1}",
                "preview": preview,
            }
            for index, preview in enumerate(file_previews)
        ]
        + [
            {
                "path": str(path),
                "name": path.name,
                "suffix": path.suffix,
                "chars": "",
                "preview": "",
            }
            for path in _project_file_manifest(project_path)
            if _canonical_path_key(path) not in {
                _canonical_path_key(Path(item).expanduser()) for item in written_files
            }
        ],
        readme_summary=readme_preview,
        run_command=run_command,
        validation_context=validation_result if isinstance(validation_result, dict) else {},
        safe_target_files=[str(path) for path in written_files],
        runtime_evidence=[run_command] if run_command else [],
        test_evidence=_coerce_string_list(
            validation_result.get("test_evidence") if isinstance(validation_result, dict) else []
        ),
    )
    analysis_context = {
        "summary": str(validation_result.get("summary") or "") if isinstance(validation_result, dict) else "",
        "prompt_context": prompt_context,
        "product_judgment": product_judgment,
        "quality_rubric": quality_rubric,
        "stack_preset": stack_preset,
        "ui_iteration_contract": ui_iteration_contract,
    }

    reservation = None
    try:
        analysis_candidates = build_project_improvement_analysis_candidates(
            project_state=project_state,
            analysis_context=analysis_context,
            completed_iteration=iteration,
            session_constraints=session_constraints,
            session_ingress_state=session_ingress_state,
            context_projection=context_projection,
        )
        request = build_context_candidate_request(
                llm_client,
                purpose=ContextRequestPurpose.PROJECT_IMPROVEMENT,
                candidates=analysis_candidates,
                response_format="json_object",
                temperature=0.2,
            )
        reservation_key = (
            "project_improvement:"
            f"{Path(project_path).resolve()}:{iteration}:"
            f"{str(goal).strip()}"
        )
        budget_request = EnhancementCompletionRequest(
            logical_key=reservation_key,
            purpose=ContextRequestPurpose.PROJECT_IMPROVEMENT,
            complexity=EnhancementCompletionComplexity.ROUTINE,
            prompt_tokens=int(getattr(request.context_selection, "final_prompt_tokens", 0) or 0),
            remaining_calls=max(1, 4 - iteration),
            remaining_value=EnhancementCompletionDecisionValue.NORMAL,
            requirement=(
                EnhancementCompletionRequirement.REQUIRED
                if bool(params.get("_enhancement_required"))
                else EnhancementCompletionRequirement.OPTIONAL
            ),
        )
        reservation = completion_budget.reserve(
            budget_request
        )
        if reservation is None:
            if bool(params.get("_enhancement_required")):
                raise ContextAssemblyBudgetError(["enhancement_completion_budget:project_improvement"])
            return _mark_fallback(fallback, "Enhancement completion budget is insufficient for project analysis.")
        request = request.model_copy(
            update={
                "max_tokens": reservation.max_tokens,
                "trace_info": {
                    **request.trace_info,
                    "completion_budget": {
                        "purpose": ContextRequestPurpose.PROJECT_IMPROVEMENT.value,
                        "reservation_id": reservation.reservation_id,
                        "reserved_tokens": reservation.max_tokens,
                        "remaining_tokens": runtime_budget.enhancement_completion_tokens_remaining,
                    },
                },
                "reasoning_policy": routine_tool_reasoning_policy(
                    getattr(llm_client, "settings", None),
                    routine=True,
                ),
            }
        )
        response = llm_client.complete(
            request,
            max_retries=1,
            use_cache=False,
        )
        usage = getattr(response, "usage", None)
        actual_tokens = None
        if isinstance(usage, dict):
            actual_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        completion_budget.reconcile(
            reservation,
            actual_tokens=int(actual_tokens) if actual_tokens is not None else None,
            finish_reason=getattr(response, "finish_reason", None),
            response_empty=not bool(str(getattr(response, "content", "") or "")),
        )
    except ContextAssemblyBudgetError:
        raise
    except Exception as exc:
        if reservation is not None:
            completion_budget.reconcile_failure(reservation, exc)
        recovered = False
        if (
            reservation is not None
            and isinstance(exc, InvalidLLMResponseError)
            and str(getattr(exc, "finish_reason", "") or "").lower()
            in {"length", "max_tokens"}
        ):
            recovery = completion_budget.reserve(
                budget_request.model_copy(
                    update={
                        "logical_key": f"{reservation_key}:length_recovery",
                        "recovery_of": reservation.reservation_id,
                    }
                )
            )
            if recovery is not None:
                recovery_request = request.model_copy(
                    update={
                        "max_tokens": recovery.max_tokens,
                        "trace_info": {
                            **request.trace_info,
                            "completion_budget": {
                                "purpose": ContextRequestPurpose.PROJECT_IMPROVEMENT.value,
                                "reservation_id": recovery.reservation_id,
                                "reserved_tokens": recovery.max_tokens,
                                "remaining_tokens": runtime_budget.enhancement_completion_tokens_remaining,
                                "recovery_of": reservation.reservation_id,
                            },
                        },
                    }
                )
                try:
                    response = llm_client.complete(
                        recovery_request,
                        max_retries=1,
                        use_cache=False,
                    )
                    usage = getattr(response, "usage", None)
                    actual_tokens = None
                    if isinstance(usage, dict):
                        actual_tokens = usage.get(
                            "completion_tokens", usage.get("output_tokens")
                        )
                    completion_budget.reconcile(
                        recovery,
                        actual_tokens=(
                            int(actual_tokens) if actual_tokens is not None else None
                        ),
                        finish_reason=getattr(response, "finish_reason", None),
                        response_empty=not bool(
                            str(getattr(response, "content", "") or "")
                        ),
                    )
                    request = recovery_request
                    recovered = True
                except Exception as recovery_exc:
                    completion_budget.reconcile_failure(recovery, recovery_exc)
                    exc = recovery_exc
        if not recovered:
            if bool(params.get("_enhancement_required")):
                raise exc
            return _mark_fallback(fallback, f"LLM improvement analysis failed: {type(exc).__name__}: {str(exc)[:300]}")

    payload = response.parsed_json if isinstance(response.parsed_json, dict) else None
    if payload is None:
        try:
            payload = json.loads(response.content)
        except (TypeError, json.JSONDecodeError):
            if bool(params.get("_enhancement_required")):
                raise InvalidLLMResponseError(
                    "Required project_improvement response was not valid JSON.",
                    response_text=str(getattr(response, "content", "") or ""),
                    usage=getattr(response, "usage", None),
                    finish_reason=getattr(response, "finish_reason", None),
                )
            return _mark_fallback(fallback, "LLM improvement analysis returned non-JSON content.")

    try:
        delta = _validate_improvement_delta(payload)
    except (ValidationError, ValueError, TypeError) as exc:
        if bool(params.get("_enhancement_required")):
            raise InvalidLLMResponseError(
                "Required project_improvement response violated the bounded delta contract.",
                response_text=str(getattr(response, "content", "") or ""),
                usage=getattr(response, "usage", None),
                finish_reason=getattr(response, "finish_reason", None),
            ) from exc
        return _mark_fallback(
            fallback,
            f"LLM improvement analysis violated the bounded delta contract: {type(exc).__name__}",
        )
    report = {
        "project_path": str(project_path),
        "goal": goal,
        "iteration": iteration,
        "summary": delta.changed_signals[0] if delta.changed_signals else fallback["summary"],
        "improvement_opportunities": delta.changed_signals or fallback["improvement_opportunities"],
        "recommended_actions": delta.proposed_actions or fallback["recommended_actions"],
        "next_iteration_goal": delta.next_decision_or_goal or fallback["next_iteration_goal"],
        "must_implement_next": delta.must_satisfy or fallback["must_implement_next"],
        "blocking_risks": delta.blocking_risks or fallback["blocking_risks"],
        "evidence_ids": [
            evidence_id
            for evidence_id in delta.evidence_ids
            if evidence_id in _retained_candidate_ids(request)
        ],
        "stack_preset_update": {
            **delta.stack_preset_patch.model_dump(exclude_none=True),
            **deterministic_stack_update,
        },
        "source": "llm",
    }
    return _sanitize_public_report(report)


def _fallback_report(goal: str, validation_result: Any) -> dict[str, Any]:
    validation = validation_result if isinstance(validation_result, dict) else {}
    errors = _coerce_string_list(validation.get("validation_errors"))
    warnings = _coerce_string_list(validation.get("warnings"))
    actions = _coerce_string_list(validation.get("recommended_actions"))
    opportunities = _coerce_string_list(validation.get("improvement_opportunities")) or warnings
    next_goal = validation.get("next_iteration_goal") or (actions[0] if actions else f"Improve the project for the original goal: {goal}")
    return {
        "summary": "Generated deterministic improvement report from validation context.",
        "improvement_opportunities": _bounded_strings(
            opportunities or ["Improve functional polish, runtime robustness, and user-facing documentation."]
        ),
        "recommended_actions": _bounded_strings(
            actions or ["Apply one focused improvement that better satisfies the original goal."]
        ),
        "next_iteration_goal": _bounded_text(str(next_goal)),
        "must_implement_next": _bounded_strings(
            actions[:2] or ["The next version should include at least one visible behavior improvement."]
        ),
        "blocking_risks": _bounded_strings(errors),
        "evidence_ids": [],
        "stack_preset_update": {},
    }


def _deterministic_stack_patch(prompt_context: dict[str, Any]) -> dict[str, Any]:
    product_judgment = prompt_context.get("product_judgment") if isinstance(prompt_context.get("product_judgment"), dict) else {}
    return (
        product_judgment.get("recommended_stack_preset_update")
        if isinstance(product_judgment.get("recommended_stack_preset_update"), dict)
        else {}
    )


def _validate_improvement_delta(payload: Any) -> _ProjectImprovementDelta:
    if not isinstance(payload, dict):
        raise TypeError("improvement delta must be a JSON object")
    new_keys = {
        "changed_signals",
        "proposed_actions",
        "next_decision_or_goal",
        "must_satisfy",
        "blocking_risks",
        "evidence_ids",
        "stack_preset_patch",
    }
    legacy_keys = {
        "summary",
        "improvement_opportunities",
        "recommended_actions",
        "next_iteration_goal",
        "must_implement_next",
        "blocking_risks",
        "evidence_ids",
        "stack_preset_update",
    }
    allowed = new_keys | legacy_keys
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"forbidden delta fields: {sorted(unknown)}")
    canonical = {
        "changed_signals": payload.get("changed_signals") or payload.get("improvement_opportunities") or [],
        "proposed_actions": payload.get("proposed_actions") or payload.get("recommended_actions") or [],
        "next_decision_or_goal": payload.get("next_decision_or_goal") or payload.get("next_iteration_goal") or "",
        "must_satisfy": payload.get("must_satisfy") or payload.get("must_implement_next") or [],
        "blocking_risks": payload.get("blocking_risks") or [],
        "evidence_ids": payload.get("evidence_ids") or [],
        "stack_preset_patch": payload.get("stack_preset_patch") or payload.get("stack_preset_update") or {},
    }
    return _ProjectImprovementDelta.model_validate(canonical)


def _retained_candidate_ids(request: Any) -> set[str]:
    selection = getattr(request, "context_selection", None)
    decisions = getattr(selection, "candidate_decisions", []) if selection is not None else []
    retained: set[str] = set()
    for decision in decisions:
        action = str(getattr(decision, "action", "") or "")
        if action not in {"omitted", "ContextCandidateAction.OMITTED"}:
            candidate_id = str(getattr(decision, "candidate_id", "") or "")
            if candidate_id:
                retained.add(candidate_id)
    return retained


def _bounded_text(value: str) -> str:
    return str(value or "").strip()[:_DELTA_TEXT_LIMIT]


def _bounded_strings(value: Any) -> list[str]:
    return [
        _bounded_text(item)
        for item in _coerce_string_list(value)[:_DELTA_LIST_LIMIT]
        if _bounded_text(item)
    ]


def _read_project_previews(project_path: Path, written_files: list[str]) -> list[str]:
    previews: list[str] = []
    for raw_path in written_files[:MAX_PROJECT_PREVIEWS]:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = project_path / path
        if not path.exists() or path.is_dir():
            continue
        text = _read_text(path)
        preview = _head_tail_preview(text, PROJECT_FILE_PREVIEW_LIMIT)
        previews.append(f"FILE: {path.name}\n{preview}")
    return previews


def _head_tail_preview(text: str, limit: int) -> str:
    suffix = "\n[Middle omitted; use both visible regions before concluding behavior is missing.]\n"
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return text[:limit]
    available = max(0, limit - len(suffix))
    head_limit = available // 2
    tail_limit = available - head_limit
    return text[:head_limit].rstrip() + suffix + text[-tail_limit:].lstrip()


def _truncate_text(text: str, limit: int, suffix: str = "\n[Truncated.]") -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + suffix


def _mark_fallback(report: dict[str, Any], reason: str) -> dict[str, Any]:
    return _sanitize_public_report(
        {
            **report,
            "source": "fallback",
            "fallback_reason": reason,
        }
    )


def _resolve_project_files(project_path: Path, written_files: list[str]) -> list[Path]:
    if written_files:
        files = []
        for raw_path in written_files:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = project_path / path
            files.append(path)
        return files

    if not project_path.exists():
        return []
    return [
        path
        for path in sorted(project_path.iterdir())
        if path.is_file() and path.name != "README.md"
    ][:12]


def _project_file_manifest(project_path: Path) -> list[Path]:
    """List bounded in-project files without loading their contents."""
    if not project_path.exists() or not project_path.is_dir():
        return []
    try:
        project_root = project_path.expanduser().resolve(strict=False)
    except OSError:
        return []
    manifest: list[Path] = []
    try:
        for root, directory_names, file_names in os.walk(project_root, topdown=True):
            directory_names[:] = sorted(
                name for name in directory_names
                if name not in _MANIFEST_EXCLUDED_DIRS
                and not (Path(root) / name).is_symlink()
            )
            for file_name in sorted(file_names):
                path = Path(root) / file_name
                if path.is_symlink():
                    continue
                manifest.append(path)
                if len(manifest) >= MAX_PROJECT_MANIFEST_FILES:
                    return manifest
    except OSError:
        return manifest
    return manifest


def _canonical_path_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve(strict=False))
    except OSError:
        return str(path.expanduser().absolute())


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _extract_run_command(readme_text: str) -> str:
    for line in readme_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("python ") or stripped.startswith("npm ") or stripped.startswith("uv "):
            return stripped
        if stripped.startswith("`") and stripped.endswith("`"):
            inner = stripped.strip("`").strip()
            if inner.startswith(("python ", "npm ", "uv ")):
                return inner
    return ""


def _coerce_path_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [str(value)]
    if isinstance(value, list):
        return [str(item.get("file_path") or item.get("path") or item) if isinstance(item, dict) else str(item) for item in value if item]
    return []


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def _latest_environment_packages(memory_records: list[dict[str, Any]]) -> list[str]:
    packages: list[str] = []
    for record in memory_records:
        attributes = record.get("attributes") if isinstance(record, dict) else None
        if not isinstance(attributes, dict):
            continue
        candidate = attributes.get("installed_packages") or attributes.get("detected_packages")
        if isinstance(candidate, list) and candidate:
            packages = [str(item) for item in candidate if str(item)]
    return packages


def _sanitize_public_report(report: dict[str, Any]) -> dict[str, Any]:
    forbidden = ("chain-of-thought", "hidden reasoning")
    sanitized = {}
    for key, value in report.items():
        if isinstance(value, list):
            sanitized[key] = [item for item in value if not any(term in item.lower() for term in forbidden)]
        elif isinstance(value, str):
            sanitized[key] = "" if any(term in value.lower() for term in forbidden) else value
        else:
            sanitized[key] = value
    return sanitized
