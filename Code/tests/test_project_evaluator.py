from __future__ import annotations

import json
import socket
import subprocess
from types import SimpleNamespace

from autonomous_iteration.agents.iteration_agent import AutonomousIterationAgent
from autonomous_iteration.agents import project_evaluator as project_evaluator_module
from autonomous_iteration.agents.project_evaluator import ProjectEvaluatorAgent
from autonomous_iteration.models import EvaluationResult, IterationResult
from core.openpilot_log import OpenPilotLogger
from metadata import ValidationIssueMetadata
from tools.terminal_smoke import TerminalSmokeResult


def _write_project(tmp_path, code: str) -> tuple[str, str]:
    app = tmp_path / "main.py"
    readme = tmp_path / "README.md"
    app.write_text(code, encoding="utf-8")
    readme.write_text("## Run\n\n```bash\npython main.py\n```\n", encoding="utf-8")
    return str(app), str(readme)


def _fake_port_conflict_run(expected_port: int):
    calls = []

    def fake_run(args, *, project, timeout, env):
        calls.append({"args": args, "project": project, "timeout": timeout, "env": env})
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                probe.bind(("127.0.0.1", expected_port))
                occupied = False
            except OSError:
                occupied = True
        finally:
            probe.close()
        assert occupied is True
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": f"Address already in use\nPort {expected_port} is in use by another program.",
            "timed_out": False,
        }

    return fake_run, calls


def test_interactive_guarded_slow_import_is_warning_not_failure(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import pygame

pygame.init()

def main():
    pygame.display.set_mode((640, 480))

if __name__ == "__main__":
    main()
""",
    )

    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build snake game",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert result.validation_passed is True
    assert any("requires a real terminal/window" in warning for warning in result.warnings)
    assert commands
    assert commands[0][1:3] == ["-m", "py_compile"]


def test_interactive_unprotected_startup_timeout_is_failure(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import pygame

while True:
    pygame.init()
""",
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs.get("args") or args[0], timeout=kwargs.get("timeout"), output="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build snake game",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert result.validation_passed is False
    assert any("top-level event loop" in error for error in result.validation_errors)


def test_import_only_traceback_remains_failure(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import pygame

def main():
    pass

if __name__ == "__main__":
    main()
""",
    )

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="Traceback (most recent call last):\\nImportError: boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    evaluator = ProjectEvaluatorAgent(smoke_timeout_seconds=1)
    result = evaluator._import_only_smoke_test(
        tmp_path,
        ["python", "main.py"],
        [tmp_path / "main.py"],
    )

    assert result["passed"] is False
    assert "ImportError: boom" in result["message"]


def test_placeholder_validation_issue_targets_only_failing_file(tmp_path) -> None:
    good = tmp_path / "personal_assistant.py"
    bad = tmp_path / "assistant.py"
    readme = tmp_path / "README.md"
    good.write_text("print('ready')\n", encoding="utf-8")
    bad.write_text("{{code_generator.output}}\n", encoding="utf-8")
    readme.write_text("## Run\n\n```bash\npython assistant.py\n```\n", encoding="utf-8")

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build personal assistant",
        project_path=tmp_path,
        written_files=[str(good), str(bad)],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    placeholder_issues = [
        issue for issue in result.validation_issues if "template placeholders" in issue.message
    ]
    assert result.validation_passed is False
    assert len(placeholder_issues) == 1
    assert placeholder_issues[0].target_files == [str(bad)]
    assert placeholder_issues[0].issue_fingerprint.startswith("generated_placeholder:assistant.py:")
    assert placeholder_issues[0].recommended_repair_kind == "replace_generated_placeholder"
    assert placeholder_issues[0].evidence_spans[0]["line"] == 1


def test_jinja_template_variables_are_not_generated_placeholder_failures(tmp_path) -> None:
    app = tmp_path / "personal_assistant.py"
    readme = tmp_path / "README.md"
    app.write_text(
        '''
from flask import Flask, render_template_string

app = Flask(__name__)
TEMPLATE = """
{% if user_input %}
<div>{{ user_input }}</div>
<div>{{ reply }}</div>
{% endif %}
"""

if __name__ == "__main__":
    print(render_template_string(TEMPLATE, user_input="hello", reply="hi"))
''',
        encoding="utf-8",
    )
    readme.write_text("## Run\n\n```bash\npython personal_assistant.py\n```\n", encoding="utf-8")

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build personal assistant",
        project_path=tmp_path,
        written_files=[str(app)],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert not any("template placeholders" in error for error in result.validation_errors)
    assert not any(issue.recommended_repair_kind == "replace_generated_placeholder" for issue in result.validation_issues)


def test_generated_placeholder_inside_python_string_is_still_blocking(tmp_path) -> None:
    app = tmp_path / "assistant.py"
    readme = tmp_path / "README.md"
    app.write_text('PROMPT = "{{code_generator_output}}"\nprint(PROMPT)\n', encoding="utf-8")
    readme.write_text("## Run\n\n```bash\npython assistant.py\n```\n", encoding="utf-8")

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build personal assistant",
        project_path=tmp_path,
        written_files=[str(app)],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    placeholder_issue = next(issue for issue in result.validation_issues if issue.recommended_repair_kind == "replace_generated_placeholder")
    assert result.validation_passed is False
    assert placeholder_issue.target_files == [str(app)]
    assert placeholder_issue.syntax_context.startswith("python_string:")


def test_runtime_traceback_validation_issue_targets_traceback_file(tmp_path, monkeypatch) -> None:
    app = tmp_path / "personal_assistant.py"
    helper = tmp_path / "assistant.py"
    readme = tmp_path / "README.md"
    app.write_text("import assistant\nassistant.main()\n", encoding="utf-8")
    helper.write_text("def main():\n    raise RuntimeError('boom')\n", encoding="utf-8")
    readme.write_text("## Run\n\n```bash\npython personal_assistant.py\n```\n", encoding="utf-8")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "Traceback (most recent call last):\n"
                f"  File \"{app}\", line 1, in <module>\n"
                f"  File \"{helper}\", line 2, in main\n"
                "RuntimeError: boom\n"
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build personal assistant",
        project_path=tmp_path,
        written_files=[str(app), str(helper)],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    runtime_issue = next(issue for issue in result.validation_issues if issue.category == "runtime_error")
    assert result.validation_passed is False
    assert runtime_issue.target_files == [str(app), str(helper)]


def test_entrypoint_import_contract_failure_is_blocking_even_when_entry_not_written_file(tmp_path) -> None:
    server = tmp_path / "server.py"
    helper = tmp_path / "assistant.py"
    readme = tmp_path / "README.md"
    server.write_text(
        """
import sys

try:
    from assistant import ask_question
except ImportError as exc:
    print(f"FATAL: cannot import assistant module - {exc}")
    sys.exit(1)

print(ask_question("hello"))
""",
        encoding="utf-8",
    )
    helper.write_text("def answer_question(question):\n    return 'ok'\n", encoding="utf-8")
    readme.write_text("## Run\n\n```bash\npython server.py\n```\n", encoding="utf-8")

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build personal assistant",
        project_path=tmp_path,
        written_files=[str(helper)],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    issue = next(issue for issue in result.validation_issues if issue.recommended_repair_kind == "fix_startup_import_contract")
    assert result.validation_passed is False
    assert "cannot import" in issue.message
    assert issue.category == "runtime_error"
    assert issue.target_files == [str(server), str(helper)]
    assert "actual local module API" in issue.recommended_action


def test_direct_run_missing_api_key_is_blocking_even_when_parent_env_has_key(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import os

if not os.environ.get("OPENAI_API_KEY"):
    raise RuntimeError("OpenAI API key not found. Set OPENAI_API_KEY environment variable")

print("ready")
""",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent-env")

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build personal assistant",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    issue = next(issue for issue in result.validation_issues if issue.recommended_repair_kind == "defer_required_secret_validation")
    assert result.validation_passed is False
    assert "OPENAI_API_KEY" in issue.message
    assert '`export OPENAI_API_KEY="..."`' in issue.recommended_action
    assert "no spaces" in issue.recommended_action
    assert issue.target_files == [str(tmp_path / "main.py")]


def test_runtime_system_output_uses_llm_decision_for_repair(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(tmp_path, "print('startup')\n")
    calls = []

    class FakeLLM:
        def complete(self, request, **kwargs):
            calls.append({"request": request, "kwargs": kwargs})
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "requires_fix": True,
                        "category": "port_conflict",
                        "reason": "The app cannot start because a selected port is occupied.",
                        "recommended_fix": "Select a free fallback port and print the actual URL.",
                        "recommended_repair_kind": "make_web_port_configurable",
                    }
                )
            )

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Address already in use\nPort 8765 is in use by another program.",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ProjectEvaluatorAgent(llm_client=FakeLLM(), smoke_timeout_seconds=1).evaluate_project(
        goal="build browser app",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    issue = next(issue for issue in result.validation_issues if issue.recommended_repair_kind == "make_web_port_configurable")
    assert calls
    assert result.validation_passed is False
    assert issue.category == "environment"
    assert "selected port is occupied" in issue.message
    assert "free fallback port" in issue.recommended_action


def test_flask_fixed_port_conflict_probe_uses_real_run_output(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "ok"

if __name__ == "__main__":
    app.run(port=8765, debug=True)
""",
    )
    fake_run, calls = _fake_port_conflict_run(8765)
    agent = ProjectEvaluatorAgent(smoke_timeout_seconds=1)
    monkeypatch.setattr(agent, "_run_startup_command", fake_run)

    result = agent.evaluate_project(
        goal="build browser app",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    issue = next(issue for issue in result.validation_issues if issue.recommended_repair_kind == "make_web_port_configurable")
    assert calls
    assert result.validation_passed is False
    assert "port 8765 unavailable" in issue.message
    assert "actual local URL" in issue.recommended_action
    assert issue.target_files == [str(tmp_path / "main.py")]


def test_flask_port_probe_ignores_non_server_run_helpers(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import anyio
from flask import Flask

app = Flask(__name__)

async def startup():
    return None

if __name__ == "__main__":
    anyio.run(startup)
    app.run(host="127.0.0.1", port=8766, debug=True)
""",
    )
    fake_run, _calls = _fake_port_conflict_run(8766)
    agent = ProjectEvaluatorAgent(smoke_timeout_seconds=1)
    monkeypatch.setattr(agent, "_run_startup_command", fake_run)

    result = agent.evaluate_project(
        goal="build browser app",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    issue = next(issue for issue in result.validation_issues if issue.recommended_repair_kind == "make_web_port_configurable")
    assert result.validation_passed is False
    assert "port 8766 unavailable" in issue.message
    assert issue.evidence_spans[0]["text"].startswith("app.run(")


def test_flask_env_port_override_allows_direct_startup_validation(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import os
from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)
""",
    )

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    agent = ProjectEvaluatorAgent(smoke_timeout_seconds=1)
    monkeypatch.setattr(
        agent,
        "_run_startup_command",
        lambda args, *, project, timeout, env: {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False},
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = agent.evaluate_project(
        goal="build browser app",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert result.validation_passed is True
    assert not any(issue.recommended_repair_kind == "make_web_port_configurable" for issue in result.validation_issues)


def test_pygame_font_warning_requires_repair(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import pygame

def main():
    pygame.init()

if __name__ == "__main__":
    main()
""",
    )

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="",
            stderr=(
                "/site-packages/pygame/sysfont.py:226: UserWarning: Process running "
                "'/usr/X11/bin/fc-list' timed-out! System fonts cannot be loaded on your platform"
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build snake game",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert result.validation_passed is False
    assert result.warning_check_result is not None
    assert result.warning_check_result.requires_fix is True
    assert result.product_intent is not None
    assert result.validation_issues[0].category == "runtime_warning"
    assert result.validation_issues[0].product_intent is not None
    assert any("Runtime warning requires repair" in error for error in result.validation_errors)
    assert "Fix the runtime warning" in result.recommended_actions[0]


def test_ignored_platform_warning_keeps_validation_passed(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import pygame

def main():
    pygame.init()

if __name__ == "__main__":
    main()
""",
    )

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="TSM AdjustCapsLockLEDForKeyTransitionHandling - _ISSetPhysicalKeyboardCapsLockLED Inhibit",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build snake game",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert result.validation_passed is True
    assert result.warning_check_result is None
    assert result.validation_errors == []


def test_curses_terminal_smoke_failure_is_blocking(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import curses
import sys

if not sys.stdout.isatty():
    sys.exit(0)

def main(stdscr):
    stdscr.addstr(25, 0, "too low")

if __name__ == "__main__":
    curses.wrapper(main)
""",
    )
    calls = []

    def fake_terminal_smoke(command, **kwargs):
        calls.append(command)
        return TerminalSmokeResult(
            command="python main.py",
            success=False,
            stdout="",
            stderr="Traceback (most recent call last):\n_curses.error: addwstr() returned ERR",
            exit_code=1,
            duration=0.1,
        )

    monkeypatch.setattr(project_evaluator_module, "run_terminal_command", fake_terminal_smoke)

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build terminal game",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert calls
    assert result.validation_passed is False
    assert any("_curses.error" in error for error in result.validation_errors)
    assert result.validation_issues[0].category == "runtime_error"
    assert "terminal size" in result.validation_issues[0].recommended_action


def test_safe_curses_terminal_smoke_passes(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import curses

def main(stdscr):
    max_y, max_x = stdscr.getmaxyx()
    if max_y < 2 or max_x < 10:
        stdscr.addstr(0, 0, "small"[:max_x])
        return
    stdscr.addstr(0, 0, "ok")

if __name__ == "__main__":
    curses.wrapper(main)
""",
    )

    def fake_terminal_smoke(command, **kwargs):
        return TerminalSmokeResult(
            command="python main.py",
            success=True,
            stdout="ok",
            stderr="",
            exit_code=0,
            duration=0.1,
        )

    monkeypatch.setattr(project_evaluator_module, "run_terminal_command", fake_terminal_smoke)

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build terminal game",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert result.validation_passed is True
    assert result.validation_errors == []


def test_relative_project_path_does_not_double_relative_import_entry(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    app, readme = _write_project(
        project,
        """
import curses

def main(stdscr):
    max_y, max_x = stdscr.getmaxyx()
    if max_y and max_x:
        stdscr.addstr(0, 0, "ok"[:max_x])

if __name__ == "__main__":
    curses.wrapper(main)
""",
    )

    def fake_terminal_smoke(command, **kwargs):
        return TerminalSmokeResult(
            command="python main.py",
            success=True,
            stdout="ok",
            stderr="",
            exit_code=0,
            duration=0.1,
        )

    monkeypatch.setattr(project_evaluator_module, "run_terminal_command", fake_terminal_smoke)
    monkeypatch.chdir(tmp_path)

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build terminal game",
        project_path="project",
        written_files=["project/main.py"],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert result.validation_passed is True
    assert not any("Import-only smoke test exited" in error for error in result.validation_errors)


def test_terminal_smoke_skipped_keeps_static_risk_warning(tmp_path, monkeypatch) -> None:
    app, readme = _write_project(
        tmp_path,
        """
import curses

def main(stdscr):
    stdscr.addstr(25, 0, "too low")

if __name__ == "__main__":
    curses.wrapper(main)
""",
    )

    def fake_terminal_smoke(command, **kwargs):
        return TerminalSmokeResult(
            command="python main.py",
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            duration=0.0,
            skipped=True,
            skip_reason="PTY terminal smoke is not available on this platform.",
        )

    monkeypatch.setattr(project_evaluator_module, "run_terminal_command", fake_terminal_smoke)

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build terminal game",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert result.validation_passed is True
    assert any("Static curses risk" in warning for warning in result.warnings)


def test_readme_runtime_contract_mismatch_is_validation_issue(tmp_path, monkeypatch) -> None:
    app = tmp_path / "main.py"
    readme = tmp_path / "README.md"
    app.write_text(
        """
import curses

def main(stdscr):
    pass

if __name__ == "__main__":
    curses.wrapper(main)
""",
        encoding="utf-8",
    )
    readme.write_text(
        "## Overview\n\nA game implemented using pygame.\n\n## Run\n\n```bash\npython main.py\n```\n",
        encoding="utf-8",
    )

    def fake_terminal_smoke(command, **kwargs):
        return TerminalSmokeResult(
            command="python main.py",
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            duration=0.1,
        )

    monkeypatch.setattr(project_evaluator_module, "run_terminal_command", fake_terminal_smoke)

    result = ProjectEvaluatorAgent(smoke_timeout_seconds=1).evaluate_project(
        goal="build game",
        project_path=tmp_path,
        written_files=[str(app)],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    assert result.validation_passed is False
    assert any("Runtime contract mismatch" in error for error in result.validation_errors)
    assert any(issue.category == "product_intent_drift" for issue in result.validation_issues)


def test_modification_evaluator_failure_defaults_to_project_evaluator_tool() -> None:
    agent = AutonomousIterationAgent(ProjectEvaluatorAgent())
    iteration_result = IterationResult(
        iteration=1,
        validation_passed=False,
        completed_successful_iteration=False,
        success=False,
        changed_files=["main.py"],
        failure_reason="Hard validation did not pass after modification.",
    )

    context = agent._failure_context(
        iteration_result,
        iteration=1,
        stage="Modification Evaluator",
        actions=["Validate main.py"],
        improvement_report={},
        completed_improvements=0,
    )

    assert context["failed_tool"] == "project_evaluator"
    assert iteration_result.failed_tool == "project_evaluator"


def test_project_evaluator_logs_blocking_issue_details_at_error_level(tmp_path) -> None:
    app, readme = _write_project(tmp_path, "raise RuntimeError('boom')\n")
    log_file = tmp_path / "evaluator.jsonl"
    logger = OpenPilotLogger(log_file)

    result = ProjectEvaluatorAgent(
        smoke_timeout_seconds=1,
        logger=logger,
        session_id_getter=lambda: "session",
    ).evaluate_project(
        goal="build assistant",
        project_path=tmp_path,
        written_files=[app],
        readme_path=readme,
        static_review={"approved": True, "issues": [], "syntax_errors": []},
    )

    entries = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    validation_entry = next(entry for entry in entries if entry["event_type"] == "project_validation_completed")
    summary = validation_entry["payload"]["output_summary"]

    assert result.validation_passed is False
    assert validation_entry["level"] == "ERROR"
    assert "RuntimeError" in summary["validation_errors"][0]
    assert "RuntimeError" in summary["validation_issues"][0]["message"]
    assert summary["target_files"]
    assert summary["recommended_actions"]


def test_modification_evaluator_failure_reason_uses_validation_issue_details() -> None:
    agent = AutonomousIterationAgent(ProjectEvaluatorAgent())
    iteration_result = IterationResult(
        iteration=1,
        validation_passed=False,
        completed_successful_iteration=False,
        success=True,
        changed_files=["main.py"],
    )
    evaluation = EvaluationResult(
        validation_passed=False,
        runnable=False,
        has_blocking_bugs=True,
        summary="Project validation failed with 1 blocking issue(s).",
        validation_errors=["Smoke test exited with code 1"],
        validation_issues=[
            ValidationIssueMetadata(
                category="runtime_error",
                message="Smoke test exited with code 1: cannot import name NOTE_FILE",
                recommended_action="Align main.py imports with assistant.py.",
                target_files=["/tmp/project/main.py"],
            )
        ],
        recommended_actions=["Align main.py imports with assistant.py."],
    )

    success = agent._evaluate_modification(evaluation, iteration_result, [], False)

    assert success is False
    assert iteration_result.failure_stage == "Modification Evaluator"
    assert iteration_result.failed_tool == "project_evaluator"
    assert "cannot import name NOTE_FILE" in (iteration_result.failure_reason or "")
    assert "target=main.py" in (iteration_result.failure_reason or "")
    assert "Hard validation did not pass" not in (iteration_result.failure_reason or "")
