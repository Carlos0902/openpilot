from __future__ import annotations

from types import SimpleNamespace

from autonomous_iteration.models import IterationResult
from ui import enhanced_cli


def test_success_details_explain_each_accepted_iteration() -> None:
    details = enhanced_cli._format_success_details(
        {
            "completed_improvements": 1,
            "required_improvements": 1,
            "iterations": [
                IterationResult(
                    iteration=1,
                    validation_passed=True,
                    completed_successful_iteration=True,
                    applied_actions=["Add a visible score display."],
                    changed_files=["/project/snake_game.py"],
                    success=True,
                )
            ],
        },
        delivery_environment=SimpleNamespace(
            project_path="/project",
            run_command=".venv/bin/python snake_game.py",
        ),
    )

    assert "代码优化: 1/1" in details
    assert "第 1 轮: Add a visible score display." in details
    assert "修改文件: snake_game.py" in details
    assert "验证: 通过" in details
    assert "运行: cd /project && .venv/bin/python snake_game.py" in details


def test_success_details_unwrap_checkpointed_session_result() -> None:
    details = enhanced_cli._format_success_details(
        {
            "success": True,
            "session_result": {
                "completed_improvements": 1,
                "required_improvements": 1,
                "iterations": [
                    {
                        "iteration": 1,
                        "validation_passed": True,
                        "applied_actions": ["Add a start screen."],
                        "changed_files": ["/project/snake_game.py"],
                    }
                ],
            },
        }
    )

    assert "代码优化: 1/1" in details
    assert "第 1 轮: Add a start screen." in details
    assert "修改文件: snake_game.py" in details


def test_confirmed_interactive_handoff_launches_through_autopilot(monkeypatch) -> None:
    environment = SimpleNamespace(
        project_path="/project",
        command_cwd="/project",
        run_command=".venv/bin/python snake_game.py",
    )
    calls: list[tuple[object, bool]] = []

    class FakeAutopilot:
        def project_delivery_environment(self, _result):
            return environment

        def launch_interactive_application(self, selected, *, user_confirmed):
            calls.append((selected, user_confirmed))
            return SimpleNamespace(
                success=True,
                output=SimpleNamespace(
                    get=lambda key, default=None: 4321 if key == "process_id" else default
                ),
            )

    class FakeUI:
        def __init__(self) -> None:
            self.console = SimpleNamespace(print=lambda *args, **kwargs: None)
            self.successes: list[tuple[str, str]] = []
            self.errors: list[tuple[str, str]] = []

        def show_success(self, title, details="") -> None:
            self.successes.append((str(title), str(details)))

        def show_error(self, title, details="") -> None:
            self.errors.append((str(title), str(details)))

    monkeypatch.setattr("ui.question_ui.QuestionUI.ask_confirm", lambda *args, **kwargs: True)
    ui = FakeUI()

    launch = enhanced_cli._offer_interactive_application_launch(
        FakeAutopilot(),
        {"success": True},
        ui,
    )

    assert launch is not None
    assert calls == [(environment, True)]
    assert ui.errors == []
    assert ui.successes[-1] == ("应用已启动", "PID: 4321\n关闭应用窗口即可结束进程。")
