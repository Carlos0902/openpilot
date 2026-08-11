"""Governed execution of one materialized response-evidence task."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autonomous_iteration.bounded_model_response import BoundedModelResponseResult
from autonomous_iteration.checkpoint_store import RuntimeCheckpointStore
from autonomous_iteration.evidence_escalation import (
    EvidenceEscalationController,
    EvidenceRuntimeBridge,
)
from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from autonomous_iteration.iteration_turn_store import IterationTurnStore
from autonomous_iteration.runtime_controller import AgentRuntimeController
from metadata import (
    IterationTurnRecordMetadata,
    ProjectImprovementPolicy,
    ProjectImprovementRequirement,
    RuntimeTaskPurpose,
    SessionIngressState,
)


@dataclass(frozen=True)
class ResponseEvidenceExecutionResult:
    """Exact durable response restored after evidence completion."""

    content: str
    ingress: SessionIngressState
    record: IterationTurnRecordMetadata


def execute_response_evidence_task(
    candidate: BoundedModelResponseResult,
    *,
    turn_store: IterationTurnStore,
    llm_client: Any,
    console: Any,
    logger: Any,
    tracker: Any | None,
    enhanced_ui: Any | None,
    project_improvement_policy: ProjectImprovementPolicy,
    diagnostics_hooks: Any,
) -> ResponseEvidenceExecutionResult:
    """Run exact preselected needs through the existing task/tool runtime."""
    recorder = getattr(diagnostics_hooks, "recorder", None)
    if recorder is None:
        raise RuntimeError("response evidence checkpoint recorder is unavailable")
    project_root = str(Path(candidate.ingress.identity.project_root).expanduser().resolve())
    checkpoint_store = RuntimeCheckpointStore(recorder.trajectory_dir)
    fingerprint = AgentRuntimeController._build_project_fingerprint(
        project_root=project_root,
        cwd=project_root,
    )
    escalation = EvidenceEscalationController(turn_store, checkpoint_store)
    active, needs = escalation.materialize_read_only_task(
        candidate.record,
        current_ingress=candidate.ingress,
        project_fingerprint=fingerprint,
    )
    bridge = EvidenceRuntimeBridge(
        escalation,
        active,
        current_ingress=candidate.ingress,
        current_project_fingerprint=fingerprint,
    )
    task_node = escalation.active_task_node(active)
    task_id = str(active.task_binding.task_id or "")
    checkpoint_id = str(active.task_binding.checkpoint_id or "")
    if not task_id or not checkpoint_id:
        raise RuntimeError("response evidence task binding is incomplete")
    autopilot = IntelligentAutopilot(
        llm_client=llm_client,
        console=console,
        auto_approve=True,
        logger=logger,
        use_enhanced_ui=tracker is not None,
        enhanced_ui=enhanced_ui if tracker is not None else None,
        tracker=tracker,
        enable_iterative_improvement=False,
        required_successful_improvements=0,
        project_improvement_policy=ProjectImprovementPolicy(
            requirement=ProjectImprovementRequirement.DISABLED,
            source=project_improvement_policy.source,
            required_accepted_transactions=0,
            max_accepted_transactions=0,
            max_attempts=0,
        ),
        runtime_diagnostics_hooks=diagnostics_hooks,
        evidence_bridge=bridge,
    )
    result = autopilot.execute(
        task_node.description,
        context={
            "task_id": task_id,
            "conversation_id": active.identity.conversation_id,
            "run_id": active.identity.run_id,
            "session_ingress_state": candidate.ingress,
            "project_path": project_root,
            "cwd": project_root,
            "checkpointing_enabled": True,
            "prepared_task_checkpoint_id": checkpoint_id,
            "task_purpose": RuntimeTaskPurpose.RESPONSE_EVIDENCE,
            "preselected_task_node": task_node,
            "preselected_decision_needs": needs,
        },
    )
    if not bool(result.get("success")):
        raise RuntimeError("response evidence execution stopped before completion")
    completed = bridge.complete()
    restored_ingress, _revision = turn_store.load_ingress(active.identity.conversation_id)
    if restored_ingress is None:
        raise RuntimeError("completed response evidence lost the durable assistant ingress")
    return ResponseEvidenceExecutionResult(
        content=bridge.response_content(completed),
        ingress=restored_ingress,
        record=completed,
    )


__all__ = ["ResponseEvidenceExecutionResult", "execute_response_evidence_task"]
