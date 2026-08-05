from __future__ import annotations

from memory.session_constraints import (
    activate_constraint_proposal,
    confirm_constraint_proposal,
    extract_constraint_proposals,
    session_constraint_violation,
)
from metadata import (
    RuntimeExecutionMode,
    SessionConstraintAuthority,
    SessionConstraintCategory,
    SessionConstraintEntry,
    SessionConstraintSourceKind,
    SessionConstraintState,
    SessionConstraintValue,
    SessionConstraintViolationCode,
)
from autonomous_iteration.runtime_controller import AgentRuntimeController
from autonomous_iteration.agents.tool_planning_executor import ToolPlanningTaskExecutor
from autonomous_iteration.task_models import Task
from metadata import RuntimeStateMetadata, ToolInputMetadata
from metadata import ToolCallMetadata
from core.tool_event_loop import ToolEventLoopRunner
from tools.tool_selection import SelectionReason, ToolSelection
from types import SimpleNamespace


def _message(message_id: str, turn: int, content: str) -> dict[str, object]:
    return {
        "message_id": message_id,
        "turn_index": turn,
        "role": "user",
        "content": content,
    }


def _state(content: str) -> SessionConstraintState:
    proposals = extract_constraint_proposals(
        [_message("user-1", 1, content)],
        session_id="session-1",
    )
    state = SessionConstraintState(session_id="session-1", project_root="/project")
    for proposal in proposals:
        state = activate_constraint_proposal(
            state,
            confirm_constraint_proposal(proposal),
            confirmation_turn=1,
        )
    return state


def test_write_scope_is_a_narrowing_final_gate() -> None:
    state = _state("Only calculator.py may be modified. Do not modify README.md.")

    assert session_constraint_violation(
        state,
        target_files=["/project/calculator.py"],
        command=None,
    ) is None
    assert session_constraint_violation(
        state,
        target_files=["/project/README.md"],
        command=None,
    ) == SessionConstraintViolationCode.WRITE_SCOPE
    assert session_constraint_violation(
        state,
        target_files=["/project/other.py"],
        command=None,
    ) == SessionConstraintViolationCode.WRITE_SCOPE


def test_required_validation_command_is_exact_for_validation_like_commands() -> None:
    state = _state("The validation command must be `python -m pytest -q`.")

    assert session_constraint_violation(
        state,
        target_files=[],
        command="python -m pytest -q",
    ) is None
    assert session_constraint_violation(
        state,
        target_files=[],
        command="pytest -q",
    ) == SessionConstraintViolationCode.VALIDATION_COMMAND
    assert session_constraint_violation(
        state,
        target_files=[],
        command="echo diagnostic",
    ) is None
    assert session_constraint_violation(
        state,
        target_files=[],
        command="echo diagnostic",
        task_validation_command="python -m pytest -q",
    ) == SessionConstraintViolationCode.VALIDATION_COMMAND


def test_read_only_constraint_cannot_be_overridden_by_mutation_target() -> None:
    state = _state("Only analysis is allowed.")
    # This fixture has no execution-mode proposal; a separately constructed
    # active state is used to prove the typed gate independently of extraction.
    from metadata import (
        SessionConstraintAuthority,
        SessionConstraintCategory,
        SessionConstraintEntry,
        SessionConstraintSourceKind,
        SessionConstraintStatus,
        SessionConstraintValue,
        RuntimeExecutionMode,
    )

    state = state.model_copy(
        update={
            "entries": [
                SessionConstraintEntry(
                    constraint_id="constraint-read-only",
                    constraint_key="execution_mode",
                    category=SessionConstraintCategory.EXECUTION_MODE,
                    value=SessionConstraintValue(execution_mode=RuntimeExecutionMode.READ_ONLY),
                    status=SessionConstraintStatus.ACTIVE,
                    statement="Analysis only.",
                    source_kind=SessionConstraintSourceKind.USER_CONFIRMATION,
                    source_id="user-1",
                    source_turn_index=1,
                    source_hash="sha256:" + "a" * 64,
                    authority=SessionConstraintAuthority.USER_CONFIRMED,
                )
            ],
        }
    )

    assert session_constraint_violation(
        state,
        target_files=["/project/calculator.py"],
        command=None,
    ) == SessionConstraintViolationCode.READ_ONLY
    assert session_constraint_violation(
        state,
        target_files=[],
        command="rm -f /project/calculator.py",
        command_is_mutation=True,
    ) == SessionConstraintViolationCode.READ_ONLY


def test_runtime_prepare_gate_rejects_scope_even_when_checkpointing_is_disabled() -> None:
    runtime = SimpleNamespace(session_id="session-1", tool_registry=None)
    controller = AgentRuntimeController(runtime)
    controller.state = RuntimeStateMetadata(
        goal="repair",
        session_constraints=_state("Only calculator.py may be modified."),
    )
    controller._checkpointing_enabled = False
    selection = ToolSelection(
        step_id="step-1",
        tool_name="file_writer",
        reason=SelectionReason.CAPABILITY_MATCH,
        input_metadata=ToolInputMetadata(
            tool_name="file_writer",
            file_path="/project/README.md",
            project_path="/project",
            content="bad",
        ),
    )

    assert controller.prepare_tool_call(SimpleNamespace(call_id="call-1"), selection) is False
    assert controller.state.guard_history[-1].approved is False


def test_normal_tool_guard_checks_session_scope_before_dynamic_edit_plan() -> None:
    runtime = SimpleNamespace(session_id="session-1", tool_registry=None)
    controller = AgentRuntimeController(runtime)
    controller.state = RuntimeStateMetadata(
        goal="repair",
        session_constraints=_state("Only calculator.py may be modified."),
    )
    runtime.runtime_controller = controller
    executor = ToolPlanningTaskExecutor(runtime)
    task = Task(
        id="task-1",
        description="repair calculator",
        kind="repair",
        write_files=["/project/README.md"],
    )
    selection = ToolSelection(
        step_id="step-1",
        tool_name="file_writer",
        reason=SelectionReason.CAPABILITY_MATCH,
        input_metadata=ToolInputMetadata(
            tool_name="file_writer",
            file_path="/project/README.md",
            project_path="/project",
            content="bad",
        ),
    )
    call = ToolCallMetadata(
        session_id="session-1",
        task_id="task-1",
        step_id="step-1",
        call_id="call-1",
        tool_name="file_writer",
        input_metadata=selection.input_metadata,
    )

    error = executor.guard_preselected_tool_call(task, call, selection)

    assert error is not None
    assert error.error_type == "SessionConstraintViolation"


def test_command_only_read_only_and_project_root_mismatch_fail_closed() -> None:
    runtime = SimpleNamespace(session_id="session-1", tool_registry=None)
    controller = AgentRuntimeController(runtime)
    read_only_state = _state("Only analysis is allowed.").model_copy(
        update={
            "processed_through_turn": 1,
            "entries": [
                # Reuse the strict constructor from the preceding test without
                # allowing the root state to be widened by this ledger.
                SessionConstraintEntry(
                    constraint_id="constraint-read-only-2",
                    constraint_key="execution_mode",
                    category=SessionConstraintCategory.EXECUTION_MODE,
                    value=SessionConstraintValue(execution_mode=RuntimeExecutionMode.READ_ONLY),
                    statement="Analysis only.",
                    source_kind=SessionConstraintSourceKind.USER_CONFIRMATION,
                    source_id="user-1",
                    source_turn_index=1,
                    source_hash="sha256:" + "a" * 64,
                    authority=SessionConstraintAuthority.USER_CONFIRMED,
                )
            ]
        }
    )
    controller.state = RuntimeStateMetadata(goal="inspect", session_constraints=read_only_state)
    command_selection = ToolSelection(
        step_id="step-command",
        tool_name="command_executor",
        reason=SelectionReason.CAPABILITY_MATCH,
        input_metadata=ToolInputMetadata(
            tool_name="command_executor",
            command="rm -f /project/calculator.py",
            project_path="/project",
        ),
    )
    assert controller.session_constraint_violation(command_selection) == SessionConstraintViolationCode.READ_ONLY

    scope_state = _state("Only calculator.py may be modified.")
    controller.state = RuntimeStateMetadata(goal="repair", session_constraints=scope_state)
    mismatch_selection = command_selection.model_copy(
        update={
            "tool_name": "file_writer",
            "input_metadata": ToolInputMetadata(
                tool_name="file_writer",
                file_path="/other/calculator.py",
                project_path="/other",
                content="bad",
            ),
        }
    )
    assert controller.session_constraint_violation(mismatch_selection) == SessionConstraintViolationCode.PROJECT_ROOT_MISMATCH
