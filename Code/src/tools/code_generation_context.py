"""Purpose-specific context projection for contextual code generation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from metadata import (
    ContextCandidate,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
)
from tools.code_models import CodeGenerationRequest


_FILE_REPLACEMENT_OPERATIONS = {"file_replace", "full_file_replace"}


def build_code_generation_candidates(request: CodeGenerationRequest) -> list[ContextCandidate]:
    """Project one contextual request without copying the full prompt-context tree."""

    context = _mapping(request.prompt_context)
    project = _mapping(context.get("project_context"))
    product_intent = _mapping(context.get("product_intent"))
    stack_preset = _mapping(context.get("stack_preset"))
    ui_contract = _mapping(context.get("ui_iteration_contract"))
    operation_kind = str(context.get("operation_kind") or "file_create")
    target_file = str(project.get("target_file") or context.get("target_file") or "")
    current_code = _current_code(context, project)
    if operation_kind in _FILE_REPLACEMENT_OPERATIONS and target_file and not current_code.strip():
        raise ValueError("existing-file replacement requires current code context")

    candidates = [
        _required(
            candidate_id="code_generation:instruction",
            kind=ContextCandidateKind.INSTRUCTION,
            source_id="code_generator:instruction:v1",
            source_order=0,
            content=(
                "You are OpenPilot's Code Generator Tool. Apply only the declared tool task, "
                "preserve existing useful behavior and product intent, and return only executable "
                f"{request.language.value} code in one fenced code block. Do not reveal hidden reasoning."
            ),
        ),
        _required(
            candidate_id="code_generation:task",
            kind=ContextCandidateKind.TASK,
            source_id=f"code_generation_request:{request.request_id}:task",
            source_order=1,
            content="Code-generation task:\n" + _json_text(
                {
                    "task_description": request.task_description,
                    "language": request.language.value,
                    "operation_kind": operation_kind,
                    "original_goal": str(context.get("original_goal") or ""),
                    "iteration_goal": str(context.get("iteration_goal") or ""),
                    "tool_task": str(context.get("tool_task") or ""),
                    "agent_instruction": str(context.get("agent_instruction") or ""),
                    "acceptance_criteria": _string_list(context.get("acceptance_criteria"))[:8],
                }
            ),
        ),
        _required(
            candidate_id="code_generation:permission",
            kind=ContextCandidateKind.CONSTRAINT,
            source_id="tool_input:code_generation:write_scope",
            source_order=2,
            content="Target and mutation boundary:\n" + _json_text(
                {
                    "operation_kind": operation_kind,
                    "project_path": str(project.get("project_path") or ""),
                    "target_file": target_file,
                    "written_files": _string_list(project.get("written_files"))[:20],
                    "target_scope": str(context.get("target_scope") or ""),
                    "symbol_name": str(context.get("symbol_name") or ""),
                    "rule": "Do not create or modify files outside the declared target/write scope.",
                }
            ),
        ),
        _required(
            candidate_id="code_generation:safety",
            kind=ContextCandidateKind.CONSTRAINT,
            source_id="prompt_context:product_intent_and_stack",
            source_order=3,
            content="Required product, safety, and stack constraints:\n" + _json_text(
                {
                    "delivery_surface": str(
                        product_intent.get("delivery_surface")
                        or stack_preset.get("delivery_surface")
                        or ""
                    ),
                    "runtime_mode": str(product_intent.get("runtime_mode") or ""),
                    "core_capabilities": _string_list(product_intent.get("core_capabilities"))[:10],
                    "non_regression_constraints": _string_list(
                        product_intent.get("non_regression_constraints")
                    )[:10],
                    "disallowed_substitutions": _string_list(
                        product_intent.get("disallowed_substitutions")
                    )[:10],
                    "architecture": str(stack_preset.get("architecture") or ""),
                    "frontend_language": str(stack_preset.get("frontend_language") or ""),
                    "backend_language": str(stack_preset.get("backend_language") or ""),
                    "frontend_frameworks": _string_list(stack_preset.get("frontend_frameworks"))[:8],
                    "backend_frameworks": _string_list(stack_preset.get("backend_frameworks"))[:8],
                    "ui_strategy": str(stack_preset.get("ui_strategy") or ""),
                    "ui_assessment_required": bool(ui_contract.get("assessment_required")),
                    "ui_implementation_required": bool(
                        ui_contract.get("implementation_required_for_user_facing_change")
                    ),
                }
            ),
        ),
        _required(
            candidate_id="code_generation:current_code",
            kind=ContextCandidateKind.PROJECT_FILE,
            source_id=f"project_file:{target_file or 'new-file'}",
            source_order=4,
            content=(
                "Current target source (preserve useful behavior in the replacement):\n"
                + current_code
                if current_code.strip()
                else "New target file: there is no existing source to preserve."
            ),
        ),
        _required(
            candidate_id="code_generation:output_contract",
            kind=ContextCandidateKind.TOOL_SCHEMA,
            source_id="code_generator:output_contract:v1",
            source_order=5,
            content="Code output contract:\n" + _json_text(
                {
                    "max_lines_when_practical": request.max_lines,
                    "allowed_imports": list(request.allowed_imports or []),
                    "forbidden_operations": list(request.forbidden_operations),
                    "return_format": f"one fenced {request.language.value} code block; no prose",
                    "file_replacement_rule": (
                        "Return complete replacement source only for file_create, directory_generate, "
                        "file_replace, or full_file_replace. Preserve imports, entry point, and useful behavior."
                    ),
                    "symbol_edit_rule": (
                        "Do not use full-file generation for symbol-level edits; those belong to code_editor "
                        "or code_unit_generator."
                    ),
                }
            ),
        ),
    ]

    _append_if_present(
        candidates,
        candidate_id="code_generation:validation",
        kind=ContextCandidateKind.RUNTIME_EVIDENCE,
        source_id="project_context:validation",
        source_order=100,
        retention=ContextCandidateRetention.PREFERRED,
        content="Compact validation evidence:\n" + _json_text(
            {
                "validation_passed": project.get("validation_passed"),
                "validation_errors": _string_list(project.get("validation_errors"))[:5],
                "warnings": _string_list(project.get("warnings"))[:5],
            }
        ),
        present=any(
            key in project
            for key in ("validation_passed", "validation_errors", "warnings")
        ),
    )
    _append_mapping_candidate(
        candidates,
        candidate_id="code_generation:dependency_strategy",
        kind=ContextCandidateKind.RUNTIME_EVIDENCE,
        source_id="prompt_context:dependency_strategy",
        source_order=101,
        retention=ContextCandidateRetention.PREFERRED,
        label="Dependency strategy",
        value=context.get("dependency_strategy"),
    )
    _append_if_present(
        candidates,
        candidate_id="code_generation:quality_rubric",
        kind=ContextCandidateKind.CONSTRAINT,
        source_id="prompt_context:quality_rubric",
        source_order=102,
        retention=ContextCandidateRetention.PREFERRED,
        content="Additional quality rubric:\n" + _json_text(
            _string_list(context.get("quality_rubric"))[:10]
        ),
        present=bool(_string_list(context.get("quality_rubric"))),
    )
    _append_mapping_candidate(
        candidates,
        candidate_id="code_generation:report_summary",
        kind=ContextCandidateKind.RUNTIME_EVIDENCE,
        source_id="prompt_context:improvement_report_summary",
        source_order=103,
        retention=ContextCandidateRetention.PREFERRED,
        label="Improvement report summary",
        value=context.get("improvement_report_summary"),
    )
    _append_mapping_candidate(
        candidates,
        candidate_id="code_generation:selected_candidate",
        kind=ContextCandidateKind.RUNTIME_EVIDENCE,
        source_id="prompt_context:selected_candidate",
        source_order=104,
        retention=ContextCandidateRetention.PREFERRED,
        label="Selected improvement candidate",
        value=context.get("selected_candidate"),
    )
    _append_mapping_candidate(
        candidates,
        candidate_id="code_generation:diagnosis",
        kind=ContextCandidateKind.ARTIFACT,
        source_id="prompt_context:diagnosis",
        source_order=200,
        retention=ContextCandidateRetention.OPTIONAL,
        label="Additional diagnosis summary",
        value=_pick_mapping_fields(
            context.get("diagnosis"),
            "summary",
            "confidence",
            "ranked_candidate_ids",
            "candidate_shortage_reason",
        ),
        freshness=ContextCandidateFreshness.HISTORICAL,
    )
    _append_mapping_candidate(
        candidates,
        candidate_id="code_generation:environment",
        kind=ContextCandidateKind.ARTIFACT,
        source_id="project_context:environment",
        source_order=201,
        retention=ContextCandidateRetention.OPTIONAL,
        label="Additional environment summary",
        value=_pick_mapping_fields(
            project.get("environment"),
            "python_version",
            "dependency_source",
            "detected_packages",
            "missing_packages",
            "run_command",
            "warnings",
        ),
    )
    _append_mapping_candidate(
        candidates,
        candidate_id="code_generation:product_judgment",
        kind=ContextCandidateKind.ARTIFACT,
        source_id="prompt_context:product_judgment",
        source_order=202,
        retention=ContextCandidateRetention.OPTIONAL,
        label="Additional product-judgment summary",
        value=_pick_mapping_fields(
            context.get("product_judgment"),
            "project_type",
            "current_runtime",
            "preferred_runtime",
            "preferred_surface",
            "recommendation",
            "ui_review_required",
        ),
    )
    return candidates


def _required(
    *,
    candidate_id: str,
    kind: ContextCandidateKind,
    source_id: str,
    source_order: int,
    content: str,
) -> ContextCandidate:
    return ContextCandidate(
        candidate_id=candidate_id,
        kind=kind,
        source_id=source_id,
        content=content,
        role="user",
        retention=ContextCandidateRetention.REQUIRED,
        priority=100,
        source_order=source_order,
        truncation=ContextCandidateTruncation.FORBIDDEN,
        trust=ContextCandidateTrust.DIRECT,
        freshness=ContextCandidateFreshness.CURRENT,
    )


def _append_if_present(
    candidates: list[ContextCandidate],
    *,
    candidate_id: str,
    kind: ContextCandidateKind,
    source_id: str,
    source_order: int,
    retention: ContextCandidateRetention,
    content: str,
    present: bool,
    freshness: ContextCandidateFreshness = ContextCandidateFreshness.CURRENT,
) -> None:
    if not present:
        return
    candidates.append(
        ContextCandidate(
            candidate_id=candidate_id,
            kind=kind,
            source_id=source_id,
            content=content,
            role="user",
            retention=retention,
            priority=70 if retention == ContextCandidateRetention.PREFERRED else 40,
            source_order=source_order,
            truncation=ContextCandidateTruncation.HEAD,
            trust=ContextCandidateTrust.OBSERVED,
            freshness=freshness,
        )
    )


def _append_mapping_candidate(
    candidates: list[ContextCandidate],
    *,
    candidate_id: str,
    kind: ContextCandidateKind,
    source_id: str,
    source_order: int,
    retention: ContextCandidateRetention,
    label: str,
    value: Any,
    freshness: ContextCandidateFreshness = ContextCandidateFreshness.CURRENT,
) -> None:
    mapping = _mapping(value)
    _append_if_present(
        candidates,
        candidate_id=candidate_id,
        kind=kind,
        source_id=source_id,
        source_order=source_order,
        retention=retention,
        content=f"{label}:\n" + _json_text(dict(mapping)),
        present=bool(mapping),
        freshness=freshness,
    )


def _current_code(context: Mapping[str, Any], project: Mapping[str, Any]) -> str:
    for value in (
        context.get("current_code"),
        context.get("existing_file_content"),
        project.get("current_code_context"),
    ):
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pick_mapping_fields(value: Any, *field_names: str) -> dict[str, Any]:
    mapping = _mapping(value)
    return {
        field_name: mapping[field_name]
        for field_name in field_names
        if field_name in mapping and mapping[field_name] not in (None, "", [], {})
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
