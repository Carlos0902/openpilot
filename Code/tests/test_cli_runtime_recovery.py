from ui.cli import build_parser
from ui.enhanced_cli import _format_failure_details, _resume_outcome_display


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


def test_failure_details_show_bounded_recovery_without_provider_payload() -> None:
    details = _format_failure_details(
        {
            "failure_reason": "Task decomposition response did not match the executable task contract.",
            "failure_stage": "Task Decomposition",
            "failed_tool": "task_decomposer",
            "task_id": "cli_task_1",
            "failure_id": "cli_task_1:task_decomposition",
            "recoverable": True,
            "recoverability": "recoverable_after_action",
            "error_type": "InvalidLLMResponseError",
            "response_text": '{"api_key":"sk-test-secret"}',
        }
    )

    assert "Stage: Task Decomposition" in details
    assert "Failure ID: cli_task_1:task_decomposition" in details
    assert "Recoverable: yes" in details
    assert "sk-test-secret" not in details
