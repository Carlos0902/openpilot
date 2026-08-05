from __future__ import annotations

import json
from types import SimpleNamespace

from autonomous_iteration.agents.execution_task_decomposer import TaskDecomposer
from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.task_models import Task, TaskExecutionContext
from metadata import (
    DifficultyAssessmentMetadata,
    ProblemSignalMetadata,
    ResolutionPlanMetadata,
    SessionConstraintState,
)
from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from memory.session_constraints import (
    activate_constraint_proposal,
    confirm_constraint_proposal,
    extract_constraint_proposals,
)


def _state() -> SessionConstraintState:
    proposals = extract_constraint_proposals(
        [
            {
                "message_id": "user-1",
                "turn_index": 1,
                "role": "user",
                "content": (
                    "Only calculator.py may be modified. "
                    "The validation command must be `python -m pytest -q`."
                ),
            }
        ],
        session_id="conversation-1",
    )
    state = SessionConstraintState(session_id="conversation-1")
    for proposal in proposals:
        state = activate_constraint_proposal(
            state,
            confirm_constraint_proposal(proposal),
            confirmation_turn=1,
        )
    return state


class _DecompositionLLM:
    def __init__(self) -> None:
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            parsed_json={
                "rationale": "bounded",
                "subtasks": [
                    {
                        "description": "Inspect calculator.py",
                        "kind": "inspect",
                        "read_files": ["calculator.py"],
                    }
                ],
            },
            content="",
        )


def test_task_decomposer_projects_active_constraints_once() -> None:
    llm = _DecompositionLLM()
    TaskDecomposer(llm).decompose(
        "Repair divide",
        context={"goal": "Repair divide", "session_constraints": _state()},
    )

    prompt = "\n".join(message.content for message in llm.requests[0].messages)
    assert "Active Session Constraints" in prompt
    assert prompt.count("calculator.py") == 1
    assert "python -m pytest -q" in prompt
    assert "SessionConstraintState(" not in prompt


def test_task_decomposer_keeps_constraint_when_unrelated_context_is_truncated() -> None:
    llm = _DecompositionLLM()
    TaskDecomposer(llm).decompose(
        "Repair divide",
        context={"noise": "unrelated context " * 4_000, "session_constraints": _state()},
    )

    request = llm.requests[0]
    prompt = "\n".join(message.content for message in request.messages)
    assert "calculator.py" in prompt
    decisions = request.context_selection.candidate_decisions
    constraint_decision = next(item for item in decisions if item.kind == "constraint")
    assert constraint_decision.action == "kept"


class _Runtime:
    def __init__(self, tmp_path) -> None:
        self.session_id = "run-1"
        self.llm_client = SimpleNamespace()
        self.tool_registry = None
        self.logger = SimpleNamespace(log_event=lambda *args, **kwargs: None)
        self.tool_io = SimpleNamespace()

    def _format_planning_surface(self, *args, **kwargs):
        return "surface"


def test_tool_planner_projects_active_constraints_once(tmp_path) -> None:
    task = Task(id="task-1", description="Repair divide")
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "Repair divide", "session_constraints": _state()},
        shared_state={},
        execution_history=[],
    )
    executor = ToolPlanningTaskExecutor(_Runtime(tmp_path))

    prompt = executor._build_tool_plan_prompt(
        task.description,
        "Repair divide",
        "surface",
        context,
    )

    assert "Active Session Constraints" in prompt
    assert prompt.count("calculator.py") == 1
    assert "python -m pytest -q" in prompt
    assert "SessionConstraintState(" not in prompt


def test_tool_planner_retry_prompt_keeps_active_constraints(tmp_path) -> None:
    task = Task(id="task-1", description="Repair divide")
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "Repair divide", "session_constraints": _state()},
        shared_state={},
        execution_history=[],
    )
    executor = ToolPlanningTaskExecutor(_Runtime(tmp_path))
    executor._active_context = context
    executor._active_task_description = task.description
    executor._active_goal = "Repair divide"
    prompt = executor._empty_plan_retry_prompt(
        {},
        ProblemSignalMetadata(signal_source="test", category="planning_gap", message="empty"),
        DifficultyAssessmentMetadata(level="simple"),
        ResolutionPlanMetadata(strategy="direct_retry", acceptance_check="recover"),
    )

    assert prompt.count("calculator.py") == 1
    assert "python -m pytest -q" in prompt


def test_prompt_owners_reject_malformed_constraint_context(tmp_path) -> None:
    llm = _DecompositionLLM()
    try:
        TaskDecomposer(llm).decompose("Repair divide", context={"session_constraints": {"bad": True}})
    except TypeError as exc:
        assert "validated SessionConstraintState" in str(exc)
    else:
        raise AssertionError("decomposer must fail closed on malformed constraints")

    task = Task(id="task-1", description="Repair divide")
    context = TaskExecutionContext(
        task=task,
        parent_context={"goal": "Repair divide", "session_constraints": {"bad": True}},
        shared_state={},
        execution_history=[],
    )
    executor = ToolPlanningTaskExecutor(_Runtime(tmp_path))
    try:
        executor._build_tool_plan_prompt(task.description, "Repair divide", "surface", context)
    except TypeError as exc:
        assert "validated SessionConstraintState" in str(exc)
    else:
        raise AssertionError("planner must fail closed on malformed constraints")


def test_tool_planner_rejects_conflicting_constraint_copies(tmp_path) -> None:
    task = Task(id="task-1", description="Repair divide")
    alternate = SessionConstraintState(session_id="conversation-1")
    context = TaskExecutionContext(
        task=task,
        parent_context={"session_constraints": _state()},
        shared_state={"session_constraints": alternate},
        execution_history=[],
    )
    executor = ToolPlanningTaskExecutor(_Runtime(tmp_path))

    try:
        executor._build_tool_plan_prompt(task.description, "Repair divide", "surface", context)
    except ValueError as exc:
        assert "conflicting session constraint states" in str(exc)
    else:
        raise AssertionError("planner must reject divergent constraint copies")


def test_task_parent_context_propagates_only_typed_constraint_state() -> None:
    autopilot = object.__new__(IntelligentAutopilot)
    autopilot.session_id = "run-1"
    autopilot.conversation_id = "conversation-1"
    autopilot._current_execution_context = {
        "session_constraints": _state(),
        "session_ingress_state": {"raw": "turns must not be copied"},
    }

    parent = autopilot._task_parent_context("Repair divide")

    assert parent["session_constraints"].session_id == "conversation-1"
    assert "session_ingress_state" not in parent
