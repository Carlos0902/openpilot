"""Runtime-owned governed decomposition admission and single-task construction."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from autonomous_iteration.task_models import Task, TaskDecompositionResult
from metadata import (
    ClaimSourceClass,
    DecompositionDecisionKind,
    DecompositionDecisionSource,
    DecompositionPolicyDecision,
    DecompositionReasonCode,
    DecisionNeedMetadata,
    SessionSemanticSnapshot,
    TaskGraphNodeMetadata,
)


class DecompositionPolicyResolver:
    """Select one typed planning path without granting capabilities."""

    _MUTATION_OR_MULTI_STAGE_TYPES = {
        "coding",
        "file_workflow",
        "data_analysis",
        "automation",
        "calendar_related",
        "communication",
    }
    _EXPLICIT_PLAN_PATTERNS = (
        r"\b(?:create|make|give me|write) (?:a )?plan\b",
        r"\b(?:break|split) .+ into (?:steps|tasks)\b",
        r"\bdecompos(?:e|ition)\b",
        r"(?:制定|给出|生成).{0,4}计划",
        r"(?:拆解|分解).{0,8}(?:任务|步骤|工作)",
    )

    def resolve(
        self,
        goal: str,
        *,
        semantic: SessionSemanticSnapshot,
        context: dict[str, Any],
    ) -> DecompositionPolicyDecision:
        preselected = context.get("preselected_task_node")
        if preselected is not None:
            if not isinstance(preselected, TaskGraphNodeMetadata):
                raise TypeError("preselected_task_node must be a TaskGraphNodeMetadata")
            if preselected.write_files or preselected.task_kind in {"implement", "repair"}:
                raise ValueError("preselected single-task handoff must remain read-only")
            return DecompositionPolicyDecision(
                kind=DecompositionDecisionKind.SINGLE_TASK,
                reason_code=DecompositionReasonCode.PRESELECTED_READ_TASK,
                source=DecompositionDecisionSource.PRE_TASK_HANDOFF,
                evidence=(
                    f"task_id:{preselected.task_id}",
                    f"task_kind:{preselected.task_kind}",
                    "write_scope:empty",
                ),
            )

        if self._explicit_plan_requested(goal) or semantic.task_type == "planning":
            return DecompositionPolicyDecision(
                kind=DecompositionDecisionKind.INITIAL_DECOMPOSITION,
                reason_code=DecompositionReasonCode.USER_REQUESTED_PLAN,
                source=DecompositionDecisionSource.USER_INTENT,
                evidence=(f"task_type:{semantic.task_type}",),
            )
        if len(semantic.expected_deliverables) > 1:
            return DecompositionPolicyDecision(
                kind=DecompositionDecisionKind.INITIAL_DECOMPOSITION,
                reason_code=DecompositionReasonCode.MULTIPLE_DELIVERABLES,
                source=DecompositionDecisionSource.RUNTIME_POLICY,
                evidence=tuple(
                    f"deliverable:{item[:160]}" for item in semantic.expected_deliverables
                ),
            )
        if context.get("write_files"):
            return DecompositionPolicyDecision(
                kind=DecompositionDecisionKind.INITIAL_DECOMPOSITION,
                reason_code=DecompositionReasonCode.MUTATION_SCOPE_REQUIRES_PLANNING,
                source=DecompositionDecisionSource.RUNTIME_POLICY,
                evidence=("typed_write_scope:present",),
            )
        if semantic.task_type in self._MUTATION_OR_MULTI_STAGE_TYPES:
            return DecompositionPolicyDecision(
                kind=DecompositionDecisionKind.INITIAL_DECOMPOSITION,
                reason_code=DecompositionReasonCode.MUTATION_SCOPE_REQUIRES_PLANNING,
                source=DecompositionDecisionSource.RUNTIME_POLICY,
                evidence=(
                    f"task_type:{semantic.task_type}",
                    *tuple(f"resource:{item}" for item in semantic.required_resources),
                ),
            )
        if semantic.task_type not in {"research", "document_summary", "unknown"}:
            return DecompositionPolicyDecision(
                kind=DecompositionDecisionKind.INITIAL_DECOMPOSITION,
                reason_code=DecompositionReasonCode.UNSUPPORTED_SINGLE_TASK_TYPE,
                source=DecompositionDecisionSource.RUNTIME_POLICY,
                evidence=(f"task_type:{semantic.task_type}",),
            )
        return DecompositionPolicyDecision(
            kind=DecompositionDecisionKind.SINGLE_TASK,
            reason_code=DecompositionReasonCode.SINGLE_BOUNDED_READ,
            source=DecompositionDecisionSource.RUNTIME_POLICY,
            evidence=(
                f"task_type:{semantic.task_type}",
                "deliverable_count:0_or_1",
                "write_scope:empty",
            ),
        )

    @classmethod
    def _explicit_plan_requested(cls, goal: str) -> bool:
        text = " ".join(str(goal).casefold().split())
        return any(re.search(pattern, text) for pattern in cls._EXPLICIT_PLAN_PATTERNS)


class SingleTaskPlanBuilder:
    """Build one compatibility TaskDecompositionResult from a governed decision."""

    def build(
        self,
        goal: str,
        *,
        semantic: SessionSemanticSnapshot,
        decision: DecompositionPolicyDecision,
        context: dict[str, Any],
    ) -> TaskDecompositionResult:
        if decision.kind != DecompositionDecisionKind.SINGLE_TASK:
            raise ValueError("single-task builder requires a single_task decision")
        preselected = context.get("preselected_task_node")
        if preselected is not None:
            if not isinstance(preselected, TaskGraphNodeMetadata):
                raise TypeError("preselected_task_node must be a TaskGraphNodeMetadata")
            task = self._task_from_node(preselected)
            encoded_needs = self._preselected_evidence_needs(preselected, context)
            if encoded_needs:
                task.attributes["preselected_decision_needs"] = encoded_needs
        else:
            task = Task(
                id=self._stable_id("single", goal, semantic, decision),
                description=goal,
                kind=("analysis" if semantic.task_type == "document_summary" else "investigate"),
                difficulty="simple",
                required_inputs=[goal],
                expected_outputs=list(semantic.expected_deliverables) or ["Grounded task result"],
                read_files=self._string_list(context.get("read_files")),
                support_context_files=self._string_list(context.get("support_context_files")),
                write_files=[],
                can_run_parallel=False,
                tags=["governed-single-task", "read-only"],
                attributes={"decomposition_decision": decision.model_dump(mode="json")},
            )
        original = Task(
            id=self._stable_id("root", goal, semantic, decision),
            description=goal,
            kind="general",
            can_run_parallel=False,
            attributes={"decomposition_decision": decision.model_dump(mode="json")},
        )
        task.parent_id = original.id
        return TaskDecompositionResult(
            original_task=original,
            subtasks=[task],
            task_graph_summary="One governed bounded task; Provider decomposition skipped.",
            decomposition_rationale=decision.reason_code,
            estimated_total_effort=float(task.estimated_effort or 1.0),
        )

    @staticmethod
    def _task_from_node(node: TaskGraphNodeMetadata) -> Task:
        return Task(
            id=node.task_id,
            description=node.description,
            kind=node.task_kind,
            difficulty=node.difficulty,
            required_inputs=list(node.required_inputs),
            expected_outputs=list(node.expected_outputs),
            read_files=list(node.read_files),
            support_context_files=list(node.support_context_files),
            write_files=[],
            dependencies=list(node.dependencies),
            can_run_parallel=False,
            validation_command=node.validation_command,
            tags=list(node.tags),
        )

    @staticmethod
    def _preselected_evidence_needs(
        node: TaskGraphNodeMetadata,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        raw_needs = context.get("preselected_decision_needs")
        if raw_needs is None:
            return []
        if not isinstance(raw_needs, (list, tuple)) or not raw_needs:
            raise TypeError("preselected_decision_needs must be a non-empty typed sequence")
        needs: list[DecisionNeedMetadata] = []
        for raw_need in raw_needs:
            if not isinstance(raw_need, DecisionNeedMetadata):
                raise TypeError("preselected_decision_needs must contain DecisionNeedMetadata")
            obligation_id = str(raw_need.attributes.get("obligation_id") or "").strip()
            source_class = str(raw_need.attributes.get("source_class") or "").strip()
            if (
                raw_need.attributes.get("read_only") is not True
                or not obligation_id
                or raw_need.decision_to_unlock != obligation_id
                or source_class
                not in {
                    ClaimSourceClass.PROJECT.value,
                    ClaimSourceClass.CURRENT_EXTERNAL.value,
                }
            ):
                raise ValueError("preselected evidence needs require exact read-only obligation identity")
            expected_need_type = (
                "project_structure"
                if source_class == ClaimSourceClass.PROJECT.value
                else "web_search"
            )
            if raw_need.need_type != expected_need_type:
                raise ValueError("preselected evidence need source and tool purpose differ")
            needs.append(raw_need)
        obligation_ids = [str(need.decision_to_unlock) for need in needs]
        if len(set(obligation_ids)) != len(obligation_ids):
            raise ValueError("preselected evidence needs contain duplicate obligations")
        if set(obligation_ids) != set(node.expected_outputs):
            raise ValueError("preselected evidence needs must exactly match task obligations")
        return [need.model_dump(mode="json") for need in needs]

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            raise TypeError("single-task file scopes must be string lists")
        return [item for item in value if item.strip()]

    @staticmethod
    def _stable_id(
        prefix: str,
        goal: str,
        semantic: SessionSemanticSnapshot,
        decision: DecompositionPolicyDecision,
    ) -> str:
        payload = {
            "goal": goal,
            "semantic": semantic.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "purpose": prefix,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return f"{prefix}-task-{digest[:32]}"


__all__ = [
    "DecompositionPolicyResolver",
    "SingleTaskPlanBuilder",
]
