from __future__ import annotations

from copy import deepcopy
import json

import autonomous_iteration.project_improvement_context as context_policy
import pytest
from autonomous_iteration.models import ImprovementGoal, ProjectStateSnapshot
from autonomous_iteration.agents.iteration_agent import AutonomousIterationAgent
from autonomous_iteration.project_improvement_context import (
    build_project_improvement_analysis_candidates,
    build_iteration_task_design_candidates,
)
from autonomous_iteration.tool.project_improvement_tool import project_state_reader_executor
from memory.memory_models import MemoryQueryResult, MemoryRecord, MemoryType
from memory.memory_store import MemoryStore
from memory.context_assembly import ContextAssembler, evaluate_context_quality
from memory.context_assembly.request_builder import ContextRequestBuilder
from memory.session_constraints import (
    activate_constraint_proposal,
    confirm_constraint_proposal,
    extract_constraint_proposals,
)
from metadata import (
    ContextAssemblyPolicy,
    ContextAssemblyStatus,
    ContextCandidate,
    ContextCandidateKind,
    ContextCandidateFreshness,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextQualityExpectation,
    ContextRequestPurpose,
    ConversationIdentity,
    ImprovementCandidateMetadata,
    ProjectDiagnosisMetadata,
    ProjectObjectiveMetadata,
    SuccessMetricMetadata,
    ToolInputMetadata,
    SessionConstraintState,
    SessionIngressState,
    SessionTurn,
)


class _MemoryStore:
    def __init__(self, records: list[MemoryRecord]) -> None:
        self.records = records

    def query(self, *_args, **_kwargs) -> MemoryQueryResult:
        return MemoryQueryResult(query="project", memories=self.records)

    def load_all(self, memory_type: MemoryType) -> list[MemoryRecord]:
        return [record for record in self.records if record.memory_type == memory_type]


def _render(candidates: list[ContextCandidate]) -> str:
    return "\n\n".join(candidate.content for candidate in candidates)


def _large_project_state() -> ProjectStateSnapshot:
    return ProjectStateSnapshot(
        project_path="/tmp/example-project",
        goal="Preserve the calculator API while improving its documented workflow.",
        written_files=[f"/tmp/example-project/module_{index}.py" for index in range(4)],
        file_summaries=[
            {
                "path": f"/tmp/example-project/module_{index}.py",
                "name": f"module_{index}.py",
                "suffix": ".py",
                "chars": "12000",
                "preview": f"FILE_{index}_OPTIONAL_EVIDENCE\n" + ("code\n" * 2400),
            }
            for index in range(4)
        ],
        readme_summary="README_OPTIONAL_EVIDENCE\n" + ("documentation " * 1200),
        run_command="python -m pytest -q",
        memory_records=[
            {
                "id": "memory-1",
                "content": "HISTORICAL_OPTIONAL_EVIDENCE " + ("old observation " * 1200),
                "attributes": {"provider_payload": "history-noise " * 1200},
            }
        ],
        memory_context={
            "prompt_text": "OLDER_CONTEXT_OPTIONAL_EVIDENCE " + ("dialog history " * 1600),
            "context_selection": {"kind": "context_selection", "assembly_status": "ready"},
        },
        validation_context={
            "validation_passed": True,
            "summary": "All requested validation commands passed.",
            "validation_errors": [],
            "warnings": ["One non-blocking documentation warning."],
            "run_command": "python -m pytest -q",
            "provider_dump": "LOW_VALUE_VALIDATION_DETAIL " * 1600,
        },
        safe_target_files=["/tmp/example-project/module_0.py"],
        diagnostic_evidence={"file_count": 4, "safe_target_count": 1},
        runtime_evidence=["python -m pytest -q: passed"],
        test_evidence=["4 passed"],
        module_summaries=[f"module_{index}.py (.py, 12000 chars)" for index in range(4)],
    )


def _improvement_report(*, safety_constraint: str = "Do not modify tests.") -> dict:
    return {
        "summary": "Core behavior is correct; improve the documented workflow.",
        "selected_candidate": {
            "candidate_id": "document-workflow",
            "title": "Document the exact validation workflow.",
            "acceptance_criteria": [
                "README contains the exact pytest command.",
                "Existing calculator behavior remains unchanged.",
            ],
            "evidence": ["Tests passed.", "README omits the validation command."],
        },
        "prompt_context": {
            "original_goal": "Preserve the calculator API while improving its documented workflow.",
            "product_intent": {
                "non_regression_constraints": [
                    safety_constraint,
                    "Preserve the public calculator API.",
                ],
                "disallowed_substitutions": ["Do not replace the Python library with a CLI."],
            },
            "stack_preset": {
                "delivery_surface": "project_native",
                "backend_language": "python",
            },
            "quality_rubric": ["A documented command must match the verified command."],
        },
        "diagnosis": {
            "all_candidates": "LOW_VALUE_DIAGNOSIS_HISTORY " * 1800,
            "reference_history": "LOW_VALUE_REFERENCE_HISTORY " * 1800,
        },
    }


def _goal() -> ImprovementGoal:
    return ImprovementGoal(
        id="document-workflow",
        title="Document the exact validation workflow.",
        rationale="Users need a reproducible verification path.",
        acceptance_criteria=[
            "README contains the exact pytest command.",
            "Existing calculator behavior remains unchanged.",
        ],
        priority="high",
    )


def _session_constraints() -> SessionConstraintState:
    proposals = extract_constraint_proposals(
        [
            {
                "message_id": "user-constraint-1",
                "turn_index": 1,
                "role": "user",
                "content": (
                    "Only module_0.py may be modified. "
                    "The validation command must be `python -m pytest -q`. "
                    "Preserve the existing public API."
                ),
            }
        ],
        session_id="session-constraints-1",
    )
    state = SessionConstraintState(session_id="session-constraints-1")
    for proposal in proposals:
        state = activate_constraint_proposal(
            state,
            confirm_constraint_proposal(proposal),
            confirmation_turn=2,
        )
    return state


def _build_candidates(
    state: ProjectStateSnapshot,
    report: dict,
    *,
    projection_policy: str = "current",
) -> list[ContextCandidate]:
    return build_iteration_task_design_candidates(
        project_state=state,
        goal=_goal(),
        improvement_report=report,
        completed_iteration=0,
        projection_policy=projection_policy,
    )


def test_task_design_projection_policy_defaults_to_current_behavior() -> None:
    state = _large_project_state()
    report = _improvement_report()

    implicit = build_iteration_task_design_candidates(
        project_state=state,
        goal=_goal(),
        improvement_report=report,
        completed_iteration=0,
    )
    explicit = _build_candidates(state, report, projection_policy="current")

    assert explicit == implicit
    assert any(
        "LOW_VALUE_DIAGNOSIS_HISTORY" in candidate.content
        for candidate in explicit
    )


def test_task_design_projection_policy_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported Task Designer projection policy"):
        _build_candidates(
            _large_project_state(),
            _improvement_report(),
            projection_policy="unknown",
        )


def test_compact_task_design_projection_keeps_required_and_project_source_unchanged() -> None:
    state = _large_project_state()
    report = _improvement_report()

    current = _build_candidates(state, report, projection_policy="current")
    compact = _build_candidates(state, report, projection_policy="compact")
    unchanged_kinds = {
        ContextCandidateKind.INSTRUCTION,
        ContextCandidateKind.TOOL_SCHEMA,
        ContextCandidateKind.TASK,
        ContextCandidateKind.CONSTRAINT,
        ContextCandidateKind.RUNTIME_EVIDENCE,
        ContextCandidateKind.PROJECT_FILE,
    }

    assert [
        candidate for candidate in compact if candidate.kind in unchanged_kinds
    ] == [
        candidate for candidate in current if candidate.kind in unchanged_kinds
    ]


def test_goal_and_task_design_project_active_session_constraints_as_required_candidates() -> None:
    state = _session_constraints()
    project_state = _large_project_state()
    report = _improvement_report()

    goal_candidates = context_policy.build_iteration_goal_candidates(
        project_state=project_state,
        improvement_report=report,
        completed_iteration=0,
        session_constraints=state,
    )
    task_candidates = _build_candidates(project_state, report, projection_policy="compact")
    task_candidates_with_state = context_policy.build_iteration_task_design_candidates(
        project_state=project_state,
        goal=_goal(),
        improvement_report=report,
        completed_iteration=0,
        projection_policy="compact",
        session_constraints=state,
    )

    for candidates in (goal_candidates, task_candidates_with_state):
        projected = [candidate for candidate in candidates if candidate.source_id == state.authority_hash]
        assert len(projected) == 1
        assert projected[0].retention == ContextCandidateRetention.REQUIRED
        assert projected[0].truncation == ContextCandidateTruncation.FORBIDDEN
        assert "module_0.py" in projected[0].content

    assert not any(candidate.source_id == state.authority_hash for candidate in task_candidates)


def test_analyzer_goal_and_task_consume_the_same_bounded_session_dialog_projection() -> None:
    project_state = _large_project_state()
    report = _improvement_report()
    identity = ConversationIdentity(
        conversation_id="dialog-1",
        run_id="run-1",
        turn_index=2,
        project_root=project_state.project_path,
    )
    ingress = SessionIngressState(
        identity=identity,
        turns=[
            SessionTurn(
                identity=identity.model_copy(update={"turn_index": 1}),
                message_id="dialog-user-1",
                role="user",
                content="Preserve the calculator API while inspecting the workflow.",
            ),
            SessionTurn(
                identity=identity,
                message_id="dialog-assistant-2",
                role="assistant",
                content="I inspected the workflow and found a documentation gap.",
            ),
        ],
    )

    analyzer = build_project_improvement_analysis_candidates(
        project_state=project_state,
        analysis_context=report,
        completed_iteration=0,
        session_ingress_state=ingress,
    )
    goal = context_policy.build_iteration_goal_candidates(
        project_state=project_state,
        improvement_report=report,
        completed_iteration=0,
        session_ingress_state=ingress,
    )
    task = build_iteration_task_design_candidates(
        project_state=project_state,
        goal=_goal(),
        improvement_report=report,
        completed_iteration=0,
        projection_policy="compact",
        session_ingress_state=ingress,
    )

    expected = {"dialog-user-1", "dialog-assistant-2"}
    for candidates in (analyzer, goal, task):
        dialog = [candidate for candidate in candidates if candidate.source_id in expected]
        assert {candidate.source_id for candidate in dialog} == expected
        assert all(candidate.kind == ContextCandidateKind.DIALOG for candidate in dialog)
        assert not any(candidate.kind == ContextCandidateKind.CONSTRAINT for candidate in dialog)
        assert candidates[-1].candidate_id.endswith(":terminal_output_contract")
        assert candidates[-1].role == "user"


def test_project_improvement_request_ends_with_terminal_user_contract() -> None:
    project_state = _large_project_state()
    identity = ConversationIdentity(
        conversation_id="terminal-role",
        run_id="run-1",
        turn_index=1,
        project_root=project_state.project_path,
    )
    ingress = SessionIngressState(
        identity=identity,
        turns=[
            SessionTurn(
                identity=identity,
                message_id="assistant-1",
                role="assistant",
                content="Historical assistant evidence.",
            )
        ],
    )
    candidates = build_project_improvement_analysis_candidates(
        project_state=project_state,
        analysis_context=_improvement_report(),
        completed_iteration=0,
        session_ingress_state=ingress,
    )
    prepared = ContextRequestBuilder(
        ContextAssembler(renderer=lambda items: "\n".join(item.content for item in items))
    ).build(
        candidates,
        policy=ContextAssemblyPolicy(
            purpose=ContextRequestPurpose.PROJECT_IMPROVEMENT,
            max_prompt_chars=16_000,
        ),
        response_format="json_object",
    )
    request = prepared.require_request()
    assert request.messages[-1].role == "user"
    assert request.messages[-1].content.endswith(
        "Do not continue, quote, or imitate any dialog or evidence above."
    )


def test_compact_task_design_projection_is_bounded_source_linked_and_not_truncatable() -> None:
    state = _large_project_state()
    report = _improvement_report()
    report["diagnosis"] = {
        "kind": "project_diagnosis",
        "metadata_id": "diagnosis-authority",
        "summary": "The validation workflow is undocumented.",
        "selected_candidate": {
            "candidate_id": "document-workflow",
            "title": "Document the exact validation workflow.",
            "dimension": "reliability",
            "rationale": "Users need a reproducible verification path.",
            "acceptance_criteria": ["README contains the exact pytest command."],
            "target_metrics": ["documented_validation"],
            "dependencies": ["pytest"],
            "risks": ["Command drift"],
            "evidence": ["README omits the command"],
            "provider_dump": "CANDIDATE_NOISE " * 500,
        },
        "dimension_assessments": [
            {
                "dimension": "reliability",
                "summary": "The verified command is not discoverable.",
                "gaps": ["README has no validation section."],
                "evidence": ["pytest passes"],
                "risks": ["Users run the wrong command"],
                "provider_dump": "DIMENSION_NOISE " * 500,
            },
            {
                "dimension": "growth_impact",
                "summary": "Unrelated growth history.",
                "gaps": ["UNRELATED_GROWTH_GAP"],
            },
        ],
        "all_candidates": "LOW_VALUE_DIAGNOSIS_HISTORY " * 1800,
    }

    candidates = _build_candidates(state, report, projection_policy="compact")
    diagnosis = next(
        candidate
        for candidate in candidates
        if candidate.candidate_id == "iteration_task_design:artifact:diagnosis"
    )

    assert diagnosis.source_id == "improvement_report:diagnosis:task_designer_compact:v2"
    assert diagnosis.retention == ContextCandidateRetention.OPTIONAL
    assert diagnosis.trust == ContextCandidateTrust.DERIVED
    assert diagnosis.freshness == ContextCandidateFreshness.CURRENT
    assert diagnosis.truncation == ContextCandidateTruncation.FORBIDDEN
    assert "The validation workflow is undocumented." in diagnosis.content
    assert "documented_validation" in diagnosis.content
    assert "README has no validation section." in diagnosis.content
    assert "LOW_VALUE_DIAGNOSIS_HISTORY" not in diagnosis.content
    assert "CANDIDATE_NOISE" not in diagnosis.content
    assert "DIMENSION_NOISE" not in diagnosis.content
    assert "UNRELATED_GROWTH_GAP" not in diagnosis.content
    assert "metadata_id" not in diagnosis.content
    assert len(diagnosis.content) < 2_500


@pytest.mark.parametrize(
    "candidate_identity",
    [
        "exact_id",
        "shared_acceptance_criterion",
    ],
)
def test_compact_task_design_projection_keeps_only_selected_target_metric_details(
    candidate_identity,
) -> None:
    goal = _goal()
    selected_candidate = ImprovementCandidateMetadata(
        candidate_id=(
            goal.id if candidate_identity == "exact_id" else "diagnosis-documentation-gap"
        ),
        title=(
            "Different diagnosis title"
            if candidate_identity == "exact_id"
            else "Expose the verified validation command"
        ),
        dimension="reliability",
        acceptance_criteria=(
            ["A distinct criterion linked by candidate ID."]
            if candidate_identity == "exact_id"
            else [goal.acceptance_criteria[0]]
        ),
        target_metrics=["documented_validation"],
        selected=True,
    )
    diagnosis_metadata = ProjectDiagnosisMetadata(
        project_path="/tmp/example-project",
        objective=ProjectObjectiveMetadata(goal="Document validation"),
        success_metrics=[
            SuccessMetricMetadata(
                metric_id="documented_validation",
                name="Documented validation workflow",
                dimension="reliability",
                metric_type="qualitative",
                target="README names the exact verified command.",
                current_assessment="The command is currently absent.",
                satisfied=False,
                required=True,
                evidence=[
                    "README has no validation section.",
                    *(f"relevant-evidence-{index}-" + ("E" * 400) for index in range(12)),
                ],
                annotations={"provider_dump": "METRIC_ENVELOPE_NOISE " * 400},
            ),
            SuccessMetricMetadata(
                metric_id="unrelated_growth",
                name="Public showcase traffic",
                dimension="growth_impact",
                target="Increase showcase traffic.",
                current_assessment="UNRELATED_METRIC_ASSESSMENT",
                evidence=["UNRELATED_METRIC_EVIDENCE"],
            ),
        ],
        selected_candidate=selected_candidate,
        improvement_candidates=[selected_candidate],
        summary="The validation workflow is undocumented.",
        annotations={"provider_dump": "DIAGNOSIS_ENVELOPE_NOISE " * 400},
    )
    report = _improvement_report()
    report["diagnosis"] = diagnosis_metadata.to_json_dict()

    candidates = build_iteration_task_design_candidates(
        project_state=_large_project_state(),
        goal=goal,
        improvement_report=report,
        completed_iteration=0,
        projection_policy="compact",
    )
    diagnosis_candidate = next(
        candidate
        for candidate in candidates
        if candidate.candidate_id == "iteration_task_design:artifact:diagnosis"
    )
    projection = json.loads(diagnosis_candidate.content.split("\n", 1)[1])
    expected_evidence = ["README has no validation section."] + [
        (f"relevant-evidence-{index}-" + ("E" * 400))[:240]
        for index in range(4)
    ]

    assert projection["target_metric_details"] == [
        {
            "metric_id": "documented_validation",
            "name": "Documented validation workflow",
            "dimension": "reliability",
            "metric_type": "qualitative",
            "target": "README names the exact verified command.",
            "current_assessment": "The command is currently absent.",
            "evidence": expected_evidence,
            "required": True,
            "satisfied": False,
        }
    ]
    assert "unrelated_growth" not in diagnosis_candidate.content
    assert "UNRELATED_METRIC_ASSESSMENT" not in diagnosis_candidate.content
    assert "UNRELATED_METRIC_EVIDENCE" not in diagnosis_candidate.content
    assert "METRIC_ENVELOPE_NOISE" not in diagnosis_candidate.content
    assert "DIAGNOSIS_ENVELOPE_NOISE" not in diagnosis_candidate.content
    assert '"kind": "success_metric"' not in diagnosis_candidate.content
    assert '"source"' not in diagnosis_candidate.content
    assert '"correlation"' not in diagnosis_candidate.content
    assert '"created_at"' not in diagnosis_candidate.content
    assert '"annotations"' not in diagnosis_candidate.content
    assert len(diagnosis_candidate.content) < 2_500


def test_compact_task_design_projection_omits_unrelated_diagnosis() -> None:
    report = _improvement_report()
    report["selected_candidate"] = {
        "candidate_id": "unrelated-growth",
        "title": "Add a public showcase.",
    }
    report["diagnosis"] = {
        "summary": "Growth opportunity.",
        "selected_candidate": {
            "candidate_id": "unrelated-growth",
            "title": "Add a public showcase.",
            "dimension": "growth_impact",
        },
    }

    candidates = _build_candidates(
        _large_project_state(),
        report,
        projection_policy="compact",
    )

    assert all(
        candidate.candidate_id != "iteration_task_design:artifact:diagnosis"
        for candidate in candidates
    )


def test_compact_task_design_projection_keeps_only_latest_relevant_iteration_result() -> None:
    state = _large_project_state()
    state.memory_records = [
        {
            "id": "environment-newest",
            "type": "short_term",
            "content": "ENVIRONMENT_SHOULD_BE_EXCLUDED",
            "tags": ["project_environment"],
            "timestamp": "2026-08-04T12:00:00+00:00",
            "attributes": {"selected_candidate": _goal().title},
        },
        {
            "id": "iteration-old",
            "type": "project",
            "content": "OLDER_RELEVANT_ITERATION_RESULT",
            "tags": ["autonomous_iteration", "succeeded"],
            "timestamp": "2026-08-02T12:00:00+00:00",
            "attributes": {"selected_candidate": _goal().title, "success": True},
        },
        {
            "id": "iteration-unrelated",
            "type": "project",
            "content": "UNRELATED_ITERATION_RESULT",
            "tags": ["autonomous_iteration", "succeeded"],
            "timestamp": "2026-08-04T11:00:00+00:00",
            "attributes": {"selected_candidate": "Add a public showcase."},
        },
        {
            "id": "iteration-latest",
            "type": "task",
            "content": "LATEST_RELEVANT_ITERATION_RESULT " + ("detail " * 300),
            "tags": ["autonomous_iteration", "failed"],
            "timestamp": "2026-08-03T12:00:00+00:00",
            "attributes": {
                "selected_candidate": _goal().title,
                "success": False,
                "unmet_metrics": ["documented_validation"],
                "provider_payload": "MEMORY_NOISE " * 500,
            },
        },
    ]

    candidates = _build_candidates(
        state,
        _improvement_report(),
        projection_policy="compact",
    )
    memories = [
        candidate for candidate in candidates if candidate.kind == ContextCandidateKind.MEMORY
    ]

    assert len(memories) == 1
    assert memories[0].source_id == "memory:iteration-latest:task_designer_compact:v2"
    assert memories[0].retention == ContextCandidateRetention.OPTIONAL
    assert memories[0].trust == ContextCandidateTrust.DERIVED
    assert memories[0].freshness == ContextCandidateFreshness.CURRENT
    assert memories[0].truncation == ContextCandidateTruncation.FORBIDDEN
    assert "LATEST_RELEVANT_ITERATION_RESULT" in memories[0].content
    assert "MEMORY_NOISE" not in memories[0].content
    assert "OLDER_RELEVANT_ITERATION_RESULT" not in memories[0].content
    assert "UNRELATED_ITERATION_RESULT" not in memories[0].content
    assert "ENVIRONMENT_SHOULD_BE_EXCLUDED" not in memories[0].content


def test_iteration_mind_note_reaches_compact_task_design_through_real_memory_store(tmp_path) -> None:
    canonical_project = tmp_path / "calculator"
    canonical_project.mkdir()
    project = canonical_project / ".." / "calculator"
    source = project / "calculator.py"
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    store = MemoryStore(tmp_path / "memory")
    agent = AutonomousIterationAgent(object(), memory_store=store)
    report = {
        "selected_candidate": {
            "candidate_id": _goal().id,
            "title": "Historical title that is not the goal title",
            "dimension": "documentation",
        },
        "diagnosis": {"success_metrics": []},
    }

    agent._record_mind_note(
        goal="Original root goal",
        iteration=1,
        success=True,
        actions=["Document validation"],
        detail="pytest passed",
        project_path=project,
        improvement_report=report,
    )
    reader_result = project_state_reader_executor(
        ToolInputMetadata.from_mapping(
            "project_state_reader",
            {
                "project_path": str(project.resolve()),
                "goal": _goal().title,
                "written_files": [str(source.resolve())],
                "memory_query": "Original root goal",
                "_memory_store": store,
            },
        )
    )
    snapshot = ProjectStateSnapshot(**reader_result.result.to_json_dict())
    candidates = _build_candidates(
        snapshot,
        report,
        projection_policy="compact",
    )
    memory_candidates = [
        candidate for candidate in candidates if candidate.kind == ContextCandidateKind.MEMORY
    ]

    assert len(memory_candidates) == 1
    persisted = store.load_all(MemoryType.PROJECT)[0]
    assert persisted.attributes["project_path"] == str(project.resolve())
    assert persisted.attributes["selected_candidate_id"] == _goal().id
    assert snapshot.memory_records[0]["timestamp"] == persisted.timestamp
    assert memory_candidates[0].source_id.startswith(f"memory:{persisted.id}:")


@pytest.mark.parametrize("global_memory_type", [MemoryType.FEEDBACK, MemoryType.LONG_TERM])
def test_compact_task_design_excludes_global_memory_that_mimics_iteration_result(
    tmp_path,
    global_memory_type,
) -> None:
    project = tmp_path / "calculator"
    project.mkdir()
    source = project / "calculator.py"
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    store = MemoryStore(tmp_path / "memory")
    store.save(
        MemoryRecord(
            id="project-result",
            memory_type=MemoryType.PROJECT,
            content="REAL_PROJECT_ITERATION_RESULT shared",
            tags=["autonomous_iteration", "succeeded", "shared"],
            attributes={
                "project_path": str(project.resolve()),
                "goal": _goal().title,
                "selected_candidate_id": _goal().id,
                "selected_candidate": _goal().title,
                "success": True,
            },
        )
    )
    store.save(
        MemoryRecord(
            id="global-feedback",
            memory_type=global_memory_type,
            content="GLOBAL_MEMORY_MUST_NOT_BECOME_ITERATION_RESULT shared",
            tags=["autonomous_iteration", "shared"],
            attributes={
                "selected_candidate_id": _goal().id,
                "selected_candidate": _goal().title,
            },
        )
    )

    reader_result = project_state_reader_executor(
        ToolInputMetadata.from_mapping(
            "project_state_reader",
            {
                "project_path": str(project),
                "goal": _goal().title,
                "written_files": [str(source)],
                "memory_query": "shared",
                "_memory_store": store,
            },
        )
    )
    snapshot = ProjectStateSnapshot(**reader_result.result.to_json_dict())
    candidates = _build_candidates(
        snapshot,
        _improvement_report(),
        projection_policy="compact",
    )
    memory_candidates = [
        candidate for candidate in candidates if candidate.kind == ContextCandidateKind.MEMORY
    ]

    assert len(memory_candidates) == 1
    assert "REAL_PROJECT_ITERATION_RESULT" in memory_candidates[0].content
    assert "GLOBAL_MEMORY_MUST_NOT_BECOME_ITERATION_RESULT" not in memory_candidates[0].content


def test_project_reader_retains_exact_goal_iteration_memory_before_latest_limit(
    tmp_path,
) -> None:
    project = tmp_path / "calculator"
    project.mkdir()
    source = project / "calculator.py"
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    store = MemoryStore(tmp_path / "memory")
    store.save(
        MemoryRecord(
            id="older-relevant",
            memory_type=MemoryType.PROJECT,
            content="OLDER_EXACT_GOAL_RESULT_WITHOUT_QUERY_TERMS",
            tags=["autonomous_iteration", "succeeded"],
            attributes={
                "project_path": str(project.resolve()),
                "goal": _goal().title,
                "selected_candidate_id": _goal().id,
                "selected_candidate": _goal().title,
                "success": True,
            },
        )
    )
    for index in range(4):
        store.save(
            MemoryRecord(
                id=f"newer-unrelated-{index}",
                memory_type=MemoryType.PROJECT,
                content=f"NEWER_UNRELATED_RESULT_{index}",
                tags=["autonomous_iteration", "succeeded"],
                attributes={
                    "project_path": str(project.resolve()),
                    "goal": f"Unrelated root goal {index}",
                    "selected_candidate_id": f"unrelated-{index}",
                    "selected_candidate": f"Unrelated candidate {index}",
                    "success": True,
                },
            )
        )

    reader_result = project_state_reader_executor(
        ToolInputMetadata.from_mapping(
            "project_state_reader",
            {
                "project_path": str(project),
                "goal": _goal().title,
                "written_files": [str(source)],
                "memory_query": "query-that-does-not-match-memory-content",
                "_memory_store": store,
            },
        )
    )
    snapshot = ProjectStateSnapshot(**reader_result.result.to_json_dict())
    candidates = _build_candidates(
        snapshot,
        _improvement_report(),
        projection_policy="compact",
    )
    memory_candidates = [
        candidate for candidate in candidates if candidate.kind == ContextCandidateKind.MEMORY
    ]

    assert any(record["id"] == "older-relevant" for record in snapshot.memory_records)
    assert len(memory_candidates) == 1
    assert "OLDER_EXACT_GOAL_RESULT_WITHOUT_QUERY_TERMS" in memory_candidates[0].content


def test_compact_task_design_latest_result_prefers_candidate_id_and_real_timestamp() -> None:
    state = _large_project_state()
    state.memory_records = [
        {
            "id": "newer",
            "type": "project",
            "content": "NEWER_RESULT",
            "tags": ["autonomous_iteration", "succeeded"],
            "timestamp": "2026-08-04T12:00:00+00:00",
            "attributes": {
                "selected_candidate_id": _goal().id,
                "selected_candidate": "A title that changed",
            },
        },
        {
            "id": "older",
            "type": "project",
            "content": "OLDER_RESULT",
            "tags": ["autonomous_iteration", "succeeded"],
            "timestamp": "2026-08-03T12:00:00+00:00",
            "attributes": {"selected_candidate": _goal().title},
        },
    ]

    candidates = _build_candidates(state, _improvement_report(), projection_policy="compact")
    memory_candidates = [
        candidate for candidate in candidates if candidate.kind == ContextCandidateKind.MEMORY
    ]

    assert len(memory_candidates) == 1
    assert memory_candidates[0].source_id == "memory:newer:task_designer_compact:v2"
    assert "NEWER_RESULT" in memory_candidates[0].content
    assert "OLDER_RESULT" not in memory_candidates[0].content


def test_task_design_context_stays_ready_and_auditable_when_project_evidence_is_large() -> None:
    state = _large_project_state()
    report = _improvement_report()
    original_state = state.model_dump(mode="json")
    original_report = deepcopy(report)

    candidates = _build_candidates(state, report)
    required = [
        candidate
        for candidate in candidates
        if candidate.retention == ContextCandidateRetention.REQUIRED
    ]
    optional = [
        candidate
        for candidate in candidates
        if candidate.retention == ContextCandidateRetention.OPTIONAL
    ]

    assert {candidate.kind for candidate in required}.issuperset(
        {
            ContextCandidateKind.TOOL_SCHEMA,
            ContextCandidateKind.TASK,
            ContextCandidateKind.CONSTRAINT,
            ContextCandidateKind.RUNTIME_EVIDENCE,
        }
    )
    assert all(
        candidate.truncation == ContextCandidateTruncation.FORBIDDEN
        for candidate in required
    )
    assert optional
    assert any(candidate.kind == ContextCandidateKind.PROJECT_FILE for candidate in optional)
    assert any(
        candidate.kind in {ContextCandidateKind.MEMORY, ContextCandidateKind.ARTIFACT}
        for candidate in optional
    )
    assert all(candidate.source_id for candidate in optional)

    schema = next(candidate for candidate in required if candidate.kind == ContextCandidateKind.TOOL_SCHEMA)
    task = next(candidate for candidate in required if candidate.kind == ContextCandidateKind.TASK)
    safety = next(candidate for candidate in required if candidate.kind == ContextCandidateKind.CONSTRAINT)
    validation = next(
        candidate for candidate in required if candidate.kind == ContextCandidateKind.RUNTIME_EVIDENCE
    )
    assert '"task"' in schema.content
    assert '"evidence_ids"' in schema.content
    assert '"target_files"' in schema.content
    assert "Document the exact validation workflow." in task.content
    assert "README contains the exact pytest command." in task.content
    assert "Do not modify tests." in safety.content
    assert "Preserve the public calculator API." in safety.content
    assert "Do not replace the Python library with a CLI." in safety.content
    assert "All requested validation commands passed." in validation.content
    assert "python -m pytest -q" in validation.content
    assert "LOW_VALUE_VALIDATION_DETAIL" not in validation.content

    required_budget = len(_render(sorted(required, key=lambda item: item.source_order)))
    result = ContextAssembler(renderer=lambda _payload: "").assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(
            purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            max_prompt_chars=required_budget,
        ),
        renderer=_render,
    )

    assert result.selection.assembly_status == ContextAssemblyStatus.READY
    decisions = {
        decision.candidate_id: decision for decision in result.selection.candidate_decisions
    }
    assert set(decisions) == {candidate.candidate_id for candidate in candidates}
    assert all(decisions[candidate.candidate_id].action == "kept" for candidate in required)
    assert all(decisions[candidate.candidate_id].action == "omitted" for candidate in optional)
    assert result.selection.omitted_required_candidate_ids == []
    quality = evaluate_context_quality(
        candidates,
        result,
        ContextQualityExpectation(
            expected_selected_candidate_ids=[candidate.candidate_id for candidate in required],
            expected_omitted_candidate_ids=[candidate.candidate_id for candidate in optional],
        ),
    )
    assert quality.passed, quality.model_dump(mode="json")
    assert state.model_dump(mode="json") == original_state
    assert report == original_report


def test_task_design_context_fails_closed_when_required_safety_cannot_fit() -> None:
    state = _large_project_state()
    oversized_safety = "REQUIRED_SAFETY_BOUNDARY " * 1200
    report = _improvement_report(safety_constraint=oversized_safety)
    candidates = _build_candidates(state, report)
    safety = next(
        candidate
        for candidate in candidates
        if candidate.kind == ContextCandidateKind.CONSTRAINT
    )

    assert oversized_safety in safety.content
    assert safety.retention == ContextCandidateRetention.REQUIRED
    assert safety.truncation == ContextCandidateTruncation.FORBIDDEN

    result = ContextAssembler(renderer=lambda _payload: "").assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(
            purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            max_prompt_chars=1_200,
        ),
        renderer=_render,
    )

    assert result.selection.assembly_status == ContextAssemblyStatus.BUDGET_INSUFFICIENT
    assert safety.candidate_id in result.selection.omitted_required_candidate_ids
    safety_decision = next(
        decision
        for decision in result.selection.candidate_decisions
        if decision.candidate_id == safety.candidate_id
    )
    assert safety_decision.action == "omitted"
    assert safety_decision.reason == "prompt_budget"


def test_project_state_reader_excludes_other_projects_and_compacts_runtime_attributes(tmp_path) -> None:
    project = tmp_path / "calculator"
    project.mkdir()
    source = project / "calculator.py"
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    store = _MemoryStore(
        [
            MemoryRecord(
                id="other-project",
                memory_type=MemoryType.PROJECT,
                content="Snake game uses pygame and a game loop.",
                tags=["project", "snake"],
                attributes={"project_path": str(tmp_path / "snake")},
            ),
            MemoryRecord(
                id="current-environment",
                memory_type=MemoryType.SHORT_TERM,
                content="Calculator project environment synchronized.",
                tags=["project_environment", project.name],
                attributes={
                    "project_path": str(project),
                    "detected_packages": ["pytest"],
                    "installed_packages": ["pytest==9.0.0"],
                    "PATH": "SECRET_OR_LARGE_PATH:" + ("/noise" * 1000),
                    "git_snapshot": {"diff": "LARGE_GIT_PAYLOAD " * 1000},
                    "dependencies": [{"raw": "LARGE_DEPENDENCY_PAYLOAD " * 1000}],
                },
            ),
        ]
    )

    result = project_state_reader_executor(
        ToolInputMetadata.from_mapping(
            "project_state_reader",
            {
                "project_path": str(project),
                "written_files": [str(source)],
                "memory_query": "calculator",
                "_memory_store": store,
            },
        )
    )

    records = result.result.memory_records
    assert [record["id"] for record in records] == ["current-environment"]
    assert records[0]["attributes"] == {
        "project_path": str(project),
        "detected_packages": ["pytest"],
        "installed_packages": ["pytest==9.0.0"],
    }
    serialized = json.dumps(records, ensure_ascii=False)
    assert "SECRET_OR_LARGE_PATH" not in serialized
    assert "LARGE_GIT_PAYLOAD" not in serialized
    assert "LARGE_DEPENDENCY_PAYLOAD" not in serialized


def test_task_design_compacts_untrusted_memory_record_before_candidate_rendering() -> None:
    state = _large_project_state()
    state.memory_records = [
        {
            "id": "memory-1",
            "type": "project",
            "content": "Relevant historical observation. " + ("detail " * 300),
            "tags": ["calculator"],
            "confidence": 0.9,
            "attributes": {
                "project_path": state.project_path,
                "installed_packages": ["pytest==9.0.0"],
                "provider_payload": "SHOULD_NOT_REACH_PROMPT " * 1000,
            },
        }
    ]

    candidates = _build_candidates(state, _improvement_report())
    memory_candidate = next(
        candidate for candidate in candidates if candidate.kind == ContextCandidateKind.MEMORY
    )

    assert "Relevant historical observation." in memory_candidate.content
    assert "pytest==9.0.0" in memory_candidate.content
    assert "SHOULD_NOT_REACH_PROMPT" not in memory_candidate.content
    assert len(memory_candidate.content) < 1_200


def test_task_design_does_not_reassemble_the_previous_memory_prompt_aggregate() -> None:
    state = _large_project_state()
    state.memory_context["prompt_text"] = (
        "FULL_PREVIOUS_MEMORY_AGGREGATE " + ("duplicated context " * 500)
    )

    candidates = _build_candidates(state, _improvement_report())

    assert all(
        candidate.source_id != "project_state:memory_context:prompt_text"
        for candidate in candidates
    )
    assert all(
        "FULL_PREVIOUS_MEMORY_AGGREGATE" not in candidate.content
        for candidate in candidates
    )
    assert any(candidate.kind == ContextCandidateKind.MEMORY for candidate in candidates)


def test_all_improvement_purposes_use_granular_memory_without_previous_prompt_aggregate() -> None:
    state = _large_project_state()
    state.memory_context["prompt_text"] = (
        "FULL_PREVIOUS_MEMORY_AGGREGATE " + ("duplicated context " * 500)
    )
    report = _improvement_report()
    candidates_by_purpose = {
        "project_improvement": context_policy.build_project_improvement_analysis_candidates(
            project_state=state,
            analysis_context=report,
            completed_iteration=0,
        ),
        "iteration_goal": context_policy.build_iteration_goal_candidates(
            project_state=state,
            improvement_report=report,
            completed_iteration=0,
        ),
        "iteration_task_design": _build_candidates(state, report),
    }

    for purpose, candidates in candidates_by_purpose.items():
        assert all(
            candidate.source_id != "project_state:memory_context:prompt_text"
            for candidate in candidates
        ), purpose
        assert all(
            "FULL_PREVIOUS_MEMORY_AGGREGATE" not in candidate.content
            for candidate in candidates
        ), purpose
        assert any(
            candidate.kind == ContextCandidateKind.MEMORY for candidate in candidates
        ), purpose


def _assert_large_optional_context_is_ready(
    candidates: list[ContextCandidate],
) -> None:
    required = [
        candidate
        for candidate in candidates
        if candidate.retention == ContextCandidateRetention.REQUIRED
    ]
    optional = [
        candidate
        for candidate in candidates
        if candidate.retention == ContextCandidateRetention.OPTIONAL
    ]
    assert {candidate.kind for candidate in required}.issuperset(
        {
            ContextCandidateKind.TOOL_SCHEMA,
            ContextCandidateKind.TASK,
            ContextCandidateKind.CONSTRAINT,
            ContextCandidateKind.RUNTIME_EVIDENCE,
        }
    )
    assert all(
        candidate.truncation == ContextCandidateTruncation.FORBIDDEN
        for candidate in required
    )
    assert all(candidate.source_id for candidate in candidates)
    assert any("README_OPTIONAL_EVIDENCE" in candidate.content for candidate in optional)
    assert any("FILE_0_OPTIONAL_EVIDENCE" in candidate.content for candidate in optional)
    assert any("LOW_VALUE_DIAGNOSIS_HISTORY" in candidate.content for candidate in optional)
    assert any("HISTORICAL_OPTIONAL_EVIDENCE" in candidate.content for candidate in optional)

    schema = next(candidate for candidate in required if candidate.kind == ContextCandidateKind.TOOL_SCHEMA)
    task = next(candidate for candidate in required if candidate.kind == ContextCandidateKind.TASK)
    safety = next(candidate for candidate in required if candidate.kind == ContextCandidateKind.CONSTRAINT)
    validation = next(
        candidate for candidate in required if candidate.kind == ContextCandidateKind.RUNTIME_EVIDENCE
    )
    assert any(
        key in schema.content
        for key in ('"changed_signals"', '"goals"')
    )
    assert "Preserve the calculator API" in task.content
    assert "Do not modify tests." in safety.content
    assert "Preserve the public calculator API." in safety.content
    assert "All requested validation commands passed." in validation.content
    assert "python -m pytest -q" in validation.content
    assert "LOW_VALUE_VALIDATION_DETAIL" not in validation.content

    required_budget = len(_render(sorted(required, key=lambda item: item.source_order)))
    result = ContextAssembler(renderer=lambda _payload: "").assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=required_budget),
        renderer=_render,
    )
    decisions = {
        decision.candidate_id: decision for decision in result.selection.candidate_decisions
    }
    assert result.selection.assembly_status == ContextAssemblyStatus.READY
    assert result.selection.omitted_required_candidate_ids == []
    assert all(decisions[candidate.candidate_id].action == "kept" for candidate in required)
    assert all(decisions[candidate.candidate_id].action == "omitted" for candidate in optional)
    quality = evaluate_context_quality(
        candidates,
        result,
        ContextQualityExpectation(
            expected_selected_candidate_ids=[candidate.candidate_id for candidate in required],
            expected_omitted_candidate_ids=[candidate.candidate_id for candidate in optional],
        ),
    )
    assert quality.passed, quality.model_dump(mode="json")


def _assert_required_safety_fails_closed(
    candidates: list[ContextCandidate],
) -> None:
    safety = next(
        candidate
        for candidate in candidates
        if candidate.kind == ContextCandidateKind.CONSTRAINT
    )
    assert "REQUIRED_SAFETY_BOUNDARY" in safety.content
    assert safety.retention == ContextCandidateRetention.REQUIRED
    assert safety.truncation == ContextCandidateTruncation.FORBIDDEN

    result = ContextAssembler(renderer=lambda _payload: "").assemble_candidates(
        candidates,
        policy=ContextAssemblyPolicy(max_prompt_chars=1_200),
        renderer=_render,
    )
    assert result.selection.assembly_status == ContextAssemblyStatus.BUDGET_INSUFFICIENT
    assert safety.candidate_id in result.selection.omitted_required_candidate_ids


def test_project_improvement_analysis_candidates_keep_required_context_and_omit_large_evidence() -> None:
    state = _large_project_state()
    analysis_context = _improvement_report()
    original_state = state.model_dump(mode="json")
    original_context = deepcopy(analysis_context)

    candidates = context_policy.build_project_improvement_analysis_candidates(
        project_state=state,
        analysis_context=analysis_context,
        completed_iteration=0,
    )

    _assert_large_optional_context_is_ready(candidates)
    assert state.model_dump(mode="json") == original_state
    assert analysis_context == original_context


def test_project_improvement_analysis_candidates_fail_closed_for_oversized_required_safety() -> None:
    state = _large_project_state()
    analysis_context = _improvement_report(
        safety_constraint="REQUIRED_SAFETY_BOUNDARY " * 1200
    )

    candidates = context_policy.build_project_improvement_analysis_candidates(
        project_state=state,
        analysis_context=analysis_context,
        completed_iteration=0,
    )

    _assert_required_safety_fails_closed(candidates)


def test_iteration_goal_candidates_keep_required_context_and_omit_large_evidence() -> None:
    state = _large_project_state()
    report = _improvement_report()
    original_state = state.model_dump(mode="json")
    original_report = deepcopy(report)

    candidates = context_policy.build_iteration_goal_candidates(
        project_state=state,
        improvement_report=report,
        completed_iteration=0,
    )

    _assert_large_optional_context_is_ready(candidates)
    assert state.model_dump(mode="json") == original_state
    assert report == original_report


def test_iteration_goal_candidates_fail_closed_for_oversized_required_safety() -> None:
    state = _large_project_state()
    report = _improvement_report(safety_constraint="REQUIRED_SAFETY_BOUNDARY " * 1200)

    candidates = context_policy.build_iteration_goal_candidates(
        project_state=state,
        improvement_report=report,
        completed_iteration=0,
    )

    _assert_required_safety_fails_closed(candidates)


def test_all_improvement_purposes_preserve_validation_product_intent_without_report_prompt_context() -> None:
    state = _large_project_state()
    state.validation_context["product_intent"] = {
        "non_regression_constraints": [
            "Preserve the public calculator API.",
            "Do not modify tests.",
        ],
        "disallowed_substitutions": [
            "Do not replace the Python library with a CLI.",
        ],
        "delivery_surface": "project_native",
        "runtime_mode": "python_library",
    }
    report = _improvement_report()
    report.pop("prompt_context")

    candidates_by_purpose = {
        "project_improvement": context_policy.build_project_improvement_analysis_candidates(
            project_state=state,
            analysis_context=report,
            completed_iteration=0,
        ),
        "iteration_goal": context_policy.build_iteration_goal_candidates(
            project_state=state,
            improvement_report=report,
            completed_iteration=0,
        ),
        "iteration_task_design": build_iteration_task_design_candidates(
            project_state=state,
            goal=_goal(),
            improvement_report=report,
            completed_iteration=0,
        ),
    }

    for purpose, candidates in candidates_by_purpose.items():
        safety = next(
            candidate
            for candidate in candidates
            if candidate.kind == ContextCandidateKind.CONSTRAINT
        )
        payload = json.loads(safety.content.split("\n", 1)[1])
        assert safety.retention == ContextCandidateRetention.REQUIRED, purpose
        assert safety.truncation == ContextCandidateTruncation.FORBIDDEN, purpose
        assert payload["non_regression_constraints"] == [
            "Preserve the public calculator API.",
            "Do not modify tests.",
        ], purpose
        assert payload["disallowed_substitutions"] == [
            "Do not replace the Python library with a CLI.",
        ], purpose
        assert payload["delivery_surface"] == "project_native", purpose
        assert payload["runtime_mode"] == "python_library", purpose
