from __future__ import annotations

import pytest

from autonomous_iteration.decomposition_policy import (
    DecompositionPolicyResolver,
    SingleTaskPlanBuilder,
)
from metadata import (
    AgentPhase,
    DecisionNeedMetadata,
    DecompositionDecisionKind,
    DecompositionPolicyDecision,
    SessionExecutionCursor,
    SessionSemanticSnapshot,
    SessionStage,
    RuntimeStateMetadata,
    TaskGraphNodeMetadata,
)


def _semantic(
    task_type: str,
    *,
    resources: tuple[str, ...] = (),
    deliverables: tuple[str, ...] = (),
) -> SessionSemanticSnapshot:
    return SessionSemanticSnapshot(
        task_type=task_type,
        risk_level="low",
        required_resources=list(resources),
        expected_deliverables=list(deliverables),
        confidence=0.9,
    )


def test_read_only_research_selects_single_task_without_provider_decomposition() -> None:
    decision = DecompositionPolicyResolver().resolve(
        "Find the latest Python release notes",
        semantic=_semantic(
            "research",
            resources=("web_search", "llm"),
            deliverables=("release summary",),
        ),
        context={},
    )

    assert decision.kind == DecompositionDecisionKind.SINGLE_TASK
    assert decision.provider_required is False
    assert decision.reason_code == "single_bounded_read"


def test_coding_without_typed_scope_keeps_initial_decomposition() -> None:
    decision = DecompositionPolicyResolver().resolve(
        "Fix the calculator",
        semantic=_semantic("coding", resources=("local_file", "code_execution")),
        context={},
    )

    assert decision.kind == DecompositionDecisionKind.INITIAL_DECOMPOSITION
    assert decision.provider_required is True
    assert decision.reason_code == "mutation_scope_requires_planning"


def test_preselected_read_only_task_preserves_exact_scope() -> None:
    node = TaskGraphNodeMetadata(
        task_id="evidence-task",
        description="Inspect repository structure",
        task_kind="inspect",
        read_files=["README.md"],
        write_files=[],
        can_run_parallel=False,
        tags=["response-evidence", "read-only"],
    )
    decision = DecompositionPolicyResolver().resolve(
        node.description,
        semantic=_semantic("research", resources=("local_file",)),
        context={"preselected_task_node": node},
    )
    plan = SingleTaskPlanBuilder().build(
        node.description,
        semantic=_semantic("research", resources=("local_file",)),
        decision=decision,
        context={"preselected_task_node": node},
    )

    assert decision.source == "pre_task_handoff"
    assert [task.id for task in plan.subtasks] == ["evidence-task"]
    assert plan.subtasks[0].read_files == ["README.md"]
    assert plan.subtasks[0].write_files == []


def test_preselected_evidence_needs_are_preserved_as_typed_single_task_inputs() -> None:
    node = TaskGraphNodeMetadata(
        task_id="evidence-task",
        description="Inspect repository structure",
        task_kind="inspect",
        expected_outputs=["ground:claim-1"],
        tags=["response-evidence", "read-only"],
    )
    need = DecisionNeedMetadata(
        need_type="project_structure",
        question="Collect repository evidence",
        phase=AgentPhase.UNDERSTAND_PROJECT,
        decision_to_unlock="ground:claim-1",
        attributes={
            "obligation_id": "ground:claim-1",
            "source_class": "project",
            "read_only": True,
        },
    )
    context = {
        "preselected_task_node": node,
        "preselected_decision_needs": (need,),
    }
    decision = DecompositionPolicyResolver().resolve(
        node.description,
        semantic=_semantic("research", resources=("local_file",)),
        context=context,
    )

    plan = SingleTaskPlanBuilder().build(
        node.description,
        semantic=_semantic("research", resources=("local_file",)),
        decision=decision,
        context=context,
    )

    encoded = plan.subtasks[0].attributes["preselected_decision_needs"]
    assert len(encoded) == 1
    restored = DecisionNeedMetadata.model_validate(encoded[0])
    assert restored == need


def test_preselected_evidence_needs_must_exactly_match_task_obligations() -> None:
    node = TaskGraphNodeMetadata(
        task_id="evidence-task",
        description="Inspect repository structure",
        task_kind="inspect",
        expected_outputs=["ground:claim-1"],
        tags=["response-evidence", "read-only"],
    )
    need = DecisionNeedMetadata(
        need_type="project_structure",
        question="Collect repository evidence",
        decision_to_unlock="ground:other-claim",
        attributes={
            "obligation_id": "ground:other-claim",
            "source_class": "project",
            "read_only": True,
        },
    )
    context = {
        "preselected_task_node": node,
        "preselected_decision_needs": (need,),
    }
    decision = DecompositionPolicyResolver().resolve(
        node.description,
        semantic=_semantic("research"),
        context=context,
    )

    with pytest.raises(ValueError, match="obligations"):
        SingleTaskPlanBuilder().build(
            node.description,
            semantic=_semantic("research"),
            decision=decision,
            context=context,
        )


def test_preselected_mutation_task_is_not_admitted_as_single_read_handoff() -> None:
    node = TaskGraphNodeMetadata(
        task_id="unsafe",
        description="Rewrite app",
        task_kind="implement",
        write_files=["app.py"],
    )

    with pytest.raises(ValueError, match="read-only"):
        DecompositionPolicyResolver().resolve(
            node.description,
            semantic=_semantic("coding"),
            context={"preselected_task_node": node},
        )


def test_single_task_cursor_rejects_multiple_plan_nodes() -> None:
    decision = DecompositionPolicyDecision(
        kind="single_task",
        reason_code="single_bounded_read",
        source="runtime_policy",
        evidence=("task_type:research",),
    )
    original = TaskGraphNodeMetadata(task_id="root", description="Research")
    tasks = [
        TaskGraphNodeMetadata(task_id="one", description="First"),
        TaskGraphNodeMetadata(task_id="two", description="Second"),
    ]

    with pytest.raises(ValueError, match="single-task cursor"):
        SessionExecutionCursor(
            stage=SessionStage.PLAN_RECORDED,
            plan_hash="sha256:" + "a" * 64,
            decomposition_decision=decision,
            semantic=_semantic("research"),
            original_task=original,
            tasks=tasks,
            execution_order=["one", "two"],
        )


def test_runtime_replan_records_typed_decomposition_decision() -> None:
    state = RuntimeStateMetadata(goal="Inspect project")

    state.request_replan("repeated tool failure")

    assert state.decomposition_decisions[-1].kind == "replan_decomposition"
    assert state.decomposition_decisions[-1].reason_code == "replan_required"
    assert state.replan_count == 1
