from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from ui.cli import build_parser
from ui.enhanced_cli import _resume_outcome_display


class _CaptureUI:
    def __init__(self) -> None:
        self.console = SimpleNamespace(print=lambda *args, **kwargs: None)
        self.errors: list[tuple[str, str]] = []

    def live_session(self, *_args, **_kwargs):
        return nullcontext()

    def update_main_content(self, *_args, **_kwargs):
        return None

    def create_status_panel(self, *args, **kwargs):
        return (args, kwargs)

    def show_full_task_graph_timeline(self):
        return None

    def show_error(self, title, details):
        self.errors.append((str(title), str(details)))


class _RaisingAutopilot:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def execute(self, *_args, **_kwargs):
        from core.exceptions import InvalidLLMResponseError

        raise InvalidLLMResponseError(
            "Task decomposition response did not match the executable contract.",
            response_text='{"subtasks": [{"kind": "general"}]}'
        )


def test_run_parser_accepts_explicit_checkpoint_and_resume_arguments() -> None:
    parser = build_parser()

    run_args = parser.parse_args(
        ["run", "--once", "Inspect project", "--checkpointing", "--project-path", "/tmp/project"]
    )
    resume_args = parser.parse_args(
        [
            "run",
            "--resume-run-id",
            "run-1",
            "--resume-checkpoint-id",
            "checkpoint-1",
            "--project-path",
            "/tmp/project",
        ]
    )

    assert run_args.checkpointing is True
    assert run_args.project_path == "/tmp/project"
    assert resume_args.resume_run_id == "run-1"
    assert resume_args.resume_checkpoint_id == "checkpoint-1"


def test_resume_outcome_display_uses_typed_control_fields_not_explanation_text() -> None:
    first = _resume_outcome_display(
        {
            "success": False,
            "resume_decision": {
                "recoverability": "not_recoverable",
                "reason_code": "missing_stage_cursor",
                "reason": "first wording",
                "fallback": {
                    "action": "offer_new_linked_run",
                    "instructions": "preserve the original run",
                },
            },
        }
    )
    second = _resume_outcome_display(
        {
            "success": False,
            "resume_decision": {
                "recoverability": "not_recoverable",
                "reason_code": "missing_stage_cursor",
                "reason": "completely different wording",
                "fallback": {
                    "action": "offer_new_linked_run",
                    "instructions": "preserve the original run",
                },
            },
        }
    )

    assert first[0] == second[0] == "Checkpoint is not recoverable"
    assert first[2] is False
    assert "missing_stage_cursor" in first[1]
    assert "offer_new_linked_run" in first[1]


def test_resume_outcome_display_distinguishes_waiting_action_from_success() -> None:
    waiting = _resume_outcome_display(
        {
            "success": False,
            "resume_decision": {
                "recoverability": "recoverable_after_action",
                "reason_code": "recovery_budget_exhausted",
                "fallback": {"action": "request_budget_extension"},
            },
        }
    )
    completed = _resume_outcome_display(
        {
            "success": True,
            "resume_decision": {
                "recoverability": "already_complete",
                "reason_code": "checkpoint_already_complete",
                "fallback": {"action": "none"},
            },
        }
    )

    assert waiting[0] == "Resume action required"
    assert waiting[2] is False
    assert completed[0] == "Checkpoint already completed"
    assert completed[2] is True


def test_failure_details_include_phase_recoverability_and_identifier() -> None:
    from ui import enhanced_cli

    details = enhanced_cli._format_failure_details(
        {
            "failure_reason": "Task decomposition response was invalid.",
            "failure_stage": "Task Decomposition",
            "failed_tool": "task_decomposer",
            "task_id": "cli_task_1",
            "recoverable": True,
            "recoverability": "recoverable_after_action",
        }
    )

    assert "Stage: Task Decomposition" in details
    assert "Task ID: cli_task_1" in details
    assert "Recoverable: yes" in details
    assert "Recovery status: recoverable_after_action" in details


@pytest.mark.parametrize("runner", ["once", "interactive"])
def test_ordinary_autonomous_cli_contains_failure_without_traceback(monkeypatch, runner) -> None:
    import traceback

    from ui import enhanced_cli

    ui = _CaptureUI()
    monkeypatch.setattr(enhanced_cli, "_runtime_diagnostics_enabled", lambda: False)
    monkeypatch.setattr(
        "autonomous_iteration.intelligent_autopilot.IntelligentAutopilot",
        _RaisingAutopilot,
    )
    traceback_calls: list[str] = []
    monkeypatch.setattr(traceback, "print_exc", lambda: traceback_calls.append("called"))

    if runner == "once":
        result = enhanced_cli._run_once_mode(
            "Answer the user",
            ui,
            tracker=None,
            logger=None,
            settings=SimpleNamespace(),
            runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
            llm_client=object(),
        )
    else:
        result = enhanced_cli._execute_autopilot(
            "Answer the user",
            ui,
            tracker=None,
            llm_client=object(),
            logger=None,
            runtime_options=enhanced_cli.OpenPilotRuntimeOptions(),
        )

    if runner == "once":
        assert result == 2
    else:
        assert result["success"] is False
    assert traceback_calls == []
    assert ui.errors
    details = ui.errors[-1][1]
    expected_stage = "CLI" if runner == "once" else "Project Execution"
    assert f"Stage: {expected_stage}" in details
    assert "Recoverable: no" in details
    assert "Traceback" not in details
