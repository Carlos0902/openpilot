from __future__ import annotations

from types import SimpleNamespace

from autonomous_iteration.intelligent_autopilot import IntelligentAutopilot
from autonomous_iteration.runtime_controller import AgentRuntimeController
from autonomous_iteration.task_models import Task, TaskPriority
from metadata import (
    FileArtifactMetadata,
    ResultStatus,
    ToolInputMetadata,
    ToolResultMetadata,
    VerificationPlanMetadata,
)
from tools.tool_selection import SelectionReason, ToolSelection


class _NoProviderLLM:
    def complete(self, _request):
        raise AssertionError("fast checkpoint recovery must not call the provider")


def _execution_result(*, file_path: str, content: str, recovered: bool = False):
    value = SimpleNamespace(
        success=True,
        error=None,
        status=ResultStatus.SUCCESS,
        output_metadata=ToolResultMetadata(
            tool_name="file_writer",
            status=ResultStatus.SUCCESS,
            result=FileArtifactMetadata(file_path=file_path, content=content),
        ),
        duration_seconds=0.01,
        attempt_number=1,
        retry_count=0,
    )
    if recovered:
        value.recovery_already_applied = True
    return value


def _fast_write(autopilot: IntelligentAutopilot, file_path: str):
    return autopilot._execute_fast_tool(
        task=Task(
            id="fast-mutation",
            description="Update app.py",
            kind="implement",
            write_files=[file_path],
            priority=TaskPriority.HIGH,
        ),
        step_id="write_app",
        tool_name="file_writer",
        input_metadata=ToolInputMetadata.from_mapping(
            "file_writer",
            {"file_path": file_path, "content": "after\n"},
        ),
    )


def test_fast_mutation_does_not_execute_when_prepared_checkpoint_is_not_durable(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")
    autopilot = IntelligentAutopilot(_NoProviderLLM(), log_file=tmp_path / "autopilot.jsonl")
    prepared: list[str] = []
    executed: list[str] = []
    autopilot.runtime_controller = SimpleNamespace(
        state=None,
        replay_tool_result=lambda *_args: None,
        prepare_tool_call=lambda tool_call, _selection: prepared.append(tool_call.call_id) or False,
        observe_tool_result=lambda *_args: (_ for _ in ()).throw(
            AssertionError("an unexecuted mutation must not be observed")
        ),
    )
    monkeypatch.setattr(
        autopilot,
        "_execute_tool_with_fast_retry",
        lambda _selection: executed.append("file_writer")
        or (_execution_result(file_path=str(target), content="after\n"), []),
    )

    result = _fast_write(autopilot, str(target))

    assert prepared == [result.call_id]
    assert executed == []
    assert result.success is False
    assert result.failure is not None
    assert result.failure.error_type == "CheckpointPrepareFailed"
    assert target.read_text(encoding="utf-8") == "before\n"


def test_fast_mutation_observation_failure_cannot_be_reported_as_success(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")
    autopilot = IntelligentAutopilot(_NoProviderLLM(), log_file=tmp_path / "autopilot.jsonl")
    observed: list[str] = []
    autopilot.runtime_controller = SimpleNamespace(
        state=None,
        replay_tool_result=lambda *_args: None,
        prepare_tool_call=lambda *_args: True,
        observe_tool_result=lambda tool_call, _selection, _result: observed.append(tool_call.call_id) or False,
    )
    monkeypatch.setattr(
        autopilot,
        "_execute_tool_with_fast_retry",
        lambda _selection: (_execution_result(file_path=str(target), content="after\n"), []),
    )

    result = _fast_write(autopilot, str(target))

    assert observed == [result.call_id]
    assert result.success is False
    assert result.failure is not None
    assert result.failure.error_type == "CheckpointObservationFailed"


def test_fast_tool_uses_durable_replay_without_reexecuting_tool_or_provider(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "app.py"
    recovered = _execution_result(file_path=str(target), content="already applied\n", recovered=True)
    autopilot = IntelligentAutopilot(_NoProviderLLM(), log_file=tmp_path / "autopilot.jsonl")
    replayed: list[str] = []
    autopilot.runtime_controller = SimpleNamespace(
        state=None,
        replay_tool_result=lambda tool_call, _selection: replayed.append(tool_call.call_id) or recovered,
        prepare_tool_call=lambda *_args: (_ for _ in ()).throw(
            AssertionError("a replayed result must not prepare another mutation")
        ),
        observe_tool_result=lambda *_args: (_ for _ in ()).throw(
            AssertionError("a replayed result must not be observed twice")
        ),
    )
    monkeypatch.setattr(
        autopilot,
        "_execute_tool_with_fast_retry",
        lambda _selection: (_ for _ in ()).throw(
            AssertionError("a durable replay must not execute the tool")
        ),
    )

    result = _fast_write(autopilot, str(target))

    assert replayed == [result.call_id]
    assert result.success is True
    assert isinstance(result.output_metadata.result, FileArtifactMetadata)
    assert result.output_metadata.result.content == "already applied\n"


def test_fast_mutation_preserves_exact_task_validation_command(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")
    autopilot = IntelligentAutopilot(_NoProviderLLM(), log_file=tmp_path / "autopilot.jsonl")
    pending: list[VerificationPlanMetadata] = []
    autopilot.runtime_controller = SimpleNamespace(
        state=None,
        set_pending_verification=pending.append,
        replay_tool_result=lambda *_args: None,
        prepare_tool_call=lambda *_args: False,
    )
    monkeypatch.setattr(
        autopilot,
        "_execute_tool_with_fast_retry",
        lambda _selection: (_ for _ in ()).throw(AssertionError("prepare failure must prevent execution")),
    )
    task = Task(
        id="validated-fast-mutation",
        description="Update app.py and run its requested test",
        kind="implement",
        write_files=[str(target)],
        validation_command="python -m pytest -q tests/test_app.py",
    )

    result = autopilot._execute_fast_tool(
        task=task,
        step_id="write_app",
        tool_name="file_writer",
        input_metadata=ToolInputMetadata.from_mapping(
            "file_writer",
            {"file_path": str(target), "content": "after\n"},
        ),
    )

    assert result.success is False
    assert len(pending) == 1
    assert pending[0].commands == ["python -m pytest -q tests/test_app.py"]
    assert pending[0].target_files == [str(target)]


def test_real_runtime_controller_classifies_readme_and_bugfix_as_mutations(tmp_path) -> None:
    controller = AgentRuntimeController(SimpleNamespace(runtime_diagnostics_hooks=None, session_id="test"))
    project = tmp_path / "project"
    source = project / "app.py"
    selections = [
        ToolSelection(
            step_id="readme",
            tool_name="readme_tool",
            reason=SelectionReason.CAPABILITY_MATCH,
            input_metadata=ToolInputMetadata.from_mapping(
                "readme_tool",
                {"project_path": str(project), "project_summary": "Example"},
            ),
        ),
        ToolSelection(
            step_id="repair",
            tool_name="bug_fix_tool",
            reason=SelectionReason.CAPABILITY_MATCH,
            input_metadata=ToolInputMetadata.from_mapping(
                "bug_fix_tool",
                {"command": "python app.py", "cwd": str(project), "file_paths": [str(source)]},
            ),
        ),
    ]

    assert [controller._checkpointed_mutation_class(item) for item in selections] == [
        "mutating",
        "mutating",
    ]
    assert IntelligentAutopilot._fast_mutation_targets(selections[0]) == [str(project / "README.md")]
    assert IntelligentAutopilot._fast_mutation_targets(selections[1]) == [str(source)]
