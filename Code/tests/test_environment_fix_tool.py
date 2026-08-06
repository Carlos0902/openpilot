from __future__ import annotations

import pytest

from core.command_approval import CommandApprovalGate
from memory.memory_models import MemoryType
from memory.memory_store import MemoryStore
from metadata import ResultStatus, ToolInputMetadata
from tools.command_tool import command_executor
from tools.environment_fix_tool import (
    diagnose_environment_failure,
    environment_fix_tool_executor,
    summarize_environment_failure,
)


PIP_INVALID_REQUIREMENT_ERROR = """
[notice] A new release of pip is available: 25.3 -> 26.1.1
[notice] To update, run: /tmp/project/.venv/bin/python -m pip install --upgrade pip
ERROR: Invalid requirement: '\"\"\"': Expected package name at the start of dependency specifier
    \"\"\"
    ^ (from line 2 of /tmp/project/requirements.txt)
"""

PIP_NO_MATCHING_DISTRIBUTION_ERROR = """
ERROR: Could not find a version that satisfies the requirement speech_recognition (from versions: none)
ERROR: No matching distribution found for speech_recognition
"""

PIP_NOTICE_ONLY = """
[notice] A new release of pip is available: 25.3 -> 26.1.1
[notice] To update, run: /tmp/project/.venv/bin/python -m pip install --upgrade pip
"""


def test_environment_failure_diagnosis_prefers_error_over_pip_notice(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = project / "requirements.txt"
    requirements.write_text("rich\n\"\"\"\n", encoding="utf-8")
    error = PIP_INVALID_REQUIREMENT_ERROR.replace("/tmp/project", str(project))

    diagnosis = diagnose_environment_failure(project, error)

    assert diagnosis.error_type == "invalid_requirements_file"
    assert diagnosis.affected_file == str(requirements)
    assert diagnosis.line_number == 2
    assert diagnosis.root_cause.startswith("ERROR: Invalid requirement")
    assert diagnosis.pip_notices
    assert "new release of pip" not in diagnosis.root_cause


def test_environment_fix_tool_sanitizes_invalid_requirements_line(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = project / "requirements.txt"
    requirements.write_text("rich\n\"\"\"\nrequests>=2\n", encoding="utf-8")
    error = PIP_INVALID_REQUIREMENT_ERROR.replace("/tmp/project", str(project))

    result = environment_fix_tool_executor(
        ToolInputMetadata.from_mapping(
            "environment_fix_tool",
            {"project_path": str(project), "stderr": error},
        )
    )

    assert result.status == ResultStatus.SUCCESS
    assert result.result.applied is True
    assert result.result.changed_files == [str(requirements)]
    assert requirements.read_text(encoding="utf-8") == "rich\nrequests>=2\n"


def test_environment_fix_tool_removes_python_source_contamination_in_one_pass(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = project / "requirements.txt"
    requirements.write_text(
        "#!/usr/bin/env python3\n"
        '"""\n'
        "Reminder module\n"
        '"""\n'
        "import json\n"
        "class Reminder:\n"
        "    pass\n",
        encoding="utf-8",
    )
    error = PIP_INVALID_REQUIREMENT_ERROR.replace("/tmp/project", str(project))

    result = environment_fix_tool_executor(
        ToolInputMetadata.from_mapping(
            "environment_fix_tool",
            {"project_path": str(project), "stderr": error},
        )
    )

    assert result.status == ResultStatus.SUCCESS
    assert result.result.applied is True
    assert requirements.read_text(encoding="utf-8") == (
        "# OpenPilot removed Python source accidentally written to this requirements file.\n"
    )
    assert any("class Reminder:" in action for action in result.result.repair_actions)


def test_environment_failure_diagnosis_handles_invalid_requirement_without_location(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = project / "requirements.txt"
    requirements.write_text('"""\n', encoding="utf-8")

    diagnosis = diagnose_environment_failure(
        project,
        "ERROR: Invalid requirement: '\"\"\"': Expected package name at the start of dependency specifier",
    )

    assert diagnosis.error_type == "invalid_requirements_file"
    assert diagnosis.affected_file == str(requirements)
    assert diagnosis.line_number is None


def test_pip_upgrade_notice_is_not_an_environment_repair_command(tmp_path) -> None:
    diagnosis = diagnose_environment_failure(tmp_path, PIP_NOTICE_ONLY)

    assert diagnosis.error_type == "environment_setup_failed"
    assert diagnosis.pip_notices
    assert diagnosis.suggested_command == ""
    assert "pip notices were ignored" in summarize_environment_failure(PIP_NOTICE_ONLY)


def test_environment_fix_tool_resolves_pypi_distribution_alias_and_remembers_fix(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = project / "requirements.txt"
    requirements.write_text("speech_recognition>=3\npyttsx3\n", encoding="utf-8")
    memory_store = MemoryStore(tmp_path / "memory")
    queries = []

    result = environment_fix_tool_executor(
        ToolInputMetadata.from_mapping(
            "environment_fix_tool",
            {
                "project_path": str(project),
                "stderr": PIP_NO_MATCHING_DISTRIBUTION_ERROR,
                "_memory_store": memory_store,
                "_web_searcher": lambda query: queries.append(query)
                or [{"url": "https://pypi.org/project/SpeechRecognition/", "title": "SpeechRecognition"}],
            },
        )
    )

    assert result.status == ResultStatus.SUCCESS
    assert result.result.environment_failure.error_type == "unavailable_distribution"
    assert result.result.environment_failure.failed_requirement == "speech_recognition"
    assert result.result.replacement_requirement == "SpeechRecognition"
    assert requirements.read_text(encoding="utf-8") == "SpeechRecognition>=3\npyttsx3\n"
    assert queries == ["site:pypi.org/project speech_recognition Python package install"]
    memories = memory_store.load_all(MemoryType.REFERENCE)
    assert len(memories) == 1
    assert memories[0].attributes["replacement_requirement"] == "SpeechRecognition"


def test_environment_fix_tool_reuses_resolved_alias_memory_before_web_search(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = project / "requirements.txt"
    requirements.write_text("speech_recognition\n", encoding="utf-8")
    memory_store = MemoryStore(tmp_path / "memory")

    first = environment_fix_tool_executor(
        ToolInputMetadata.from_mapping(
            "environment_fix_tool",
            {
                "project_path": str(project),
                "stderr": PIP_NO_MATCHING_DISTRIBUTION_ERROR,
                "_memory_store": memory_store,
                "_web_searcher": lambda query: [{"url": "https://pypi.org/project/SpeechRecognition/"}],
            },
        )
    )
    requirements.write_text("speech_recognition\n", encoding="utf-8")

    second = environment_fix_tool_executor(
        ToolInputMetadata.from_mapping(
            "environment_fix_tool",
            {
                "project_path": str(project),
                "stderr": PIP_NO_MATCHING_DISTRIBUTION_ERROR,
                "_memory_store": memory_store,
                "_web_searcher": lambda query: (_ for _ in ()).throw(AssertionError("web search should not run")),
            },
        )
    )

    assert first.status == ResultStatus.SUCCESS
    assert second.status == ResultStatus.SUCCESS
    assert second.result.research_queries == []
    assert second.result.research_results == [{"source": "memory", "candidate": "SpeechRecognition"}]
    assert requirements.read_text(encoding="utf-8") == "SpeechRecognition\n"


def test_environment_fix_tool_uses_known_alias_when_web_search_is_unavailable(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    requirements = project / "requirements.txt"
    requirements.write_text("speech_recognition\n", encoding="utf-8")

    result = environment_fix_tool_executor(
        ToolInputMetadata.from_mapping(
            "environment_fix_tool",
            {
                "project_path": str(project),
                "stderr": PIP_NO_MATCHING_DISTRIBUTION_ERROR,
                "_web_searcher": lambda query: (_ for _ in ()).throw(OSError("offline")),
            },
        )
    )

    assert result.status == ResultStatus.SUCCESS
    assert result.result.replacement_requirement == "SpeechRecognition"
    assert result.result.research_results[0]["error"] == "offline"
    assert result.result.research_results[-1] == {
        "source": "known_import_alias",
        "candidate": "SpeechRecognition",
    }
    assert requirements.read_text(encoding="utf-8") == "SpeechRecognition\n"


def test_command_approval_gate_requires_user_for_sudo_and_allows_local_venv_pip(tmp_path) -> None:
    gate = CommandApprovalGate()
    local = gate.evaluate(f"{tmp_path}/.venv/bin/python -m pip install -r requirements.txt", cwd=str(tmp_path))
    risky = gate.evaluate("sudo pip install rich", cwd=str(tmp_path))

    assert local.auto_approved is True
    assert local.requires_confirmation is False
    assert risky.requires_confirmation is True
    assert risky.risk_level == "high"


def test_command_approval_gate_allows_local_single_file_executable_bit(tmp_path) -> None:
    script = tmp_path / "run.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    decision = CommandApprovalGate().evaluate("chmod +x run.py", cwd=str(tmp_path))

    assert decision.auto_approved is True
    assert decision.requires_confirmation is False


def test_command_approval_gate_does_not_treat_external_executable_path_as_system_write(tmp_path) -> None:
    tool_root = tmp_path / "opt" / "anaconda3" / "bin"
    tool_root.mkdir(parents=True)
    interpreter = tool_root / "python3.13"
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)

    decision = CommandApprovalGate().evaluate(f"/usr/bin/env {interpreter} --version", cwd=str(tmp_path))

    assert decision.auto_approved is True
    assert decision.requires_confirmation is False


def test_command_executor_stops_when_user_declines_high_risk_command(tmp_path) -> None:
    approvals = []

    with pytest.raises(PermissionError) as exc:
        command_executor(
            ToolInputMetadata.from_mapping(
                "command_executor",
                {
                    "command": "sudo pip install rich",
                    "mode": "automatic",
                    "cwd": str(tmp_path),
                    "_command_approval_callback": lambda decision: approvals.append(decision.command) or False,
                },
            )
        )

    assert approvals == ["sudo pip install rich"]
    assert "User declined command execution" in str(exc.value)


def test_command_executor_runs_after_user_approves_high_risk_command(tmp_path) -> None:
    approvals = []

    result = command_executor(
        ToolInputMetadata.from_mapping(
            "command_executor",
            {
                "command": "sudo --version",
                "mode": "automatic",
                "cwd": str(tmp_path),
                "_command_approval_callback": lambda decision: approvals.append(decision.command) or True,
            },
        )
    )

    assert approvals == ["sudo --version"]
    assert result.result.attributes["command_approval"]["requires_confirmation"] is True


def test_command_executor_blocks_project_external_absolute_command_path(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside project boundary"):
        command_executor(
            ToolInputMetadata.from_mapping(
                "command_executor",
                {
                    "command": f"cat {outside}",
                    "mode": "dry_run",
                    "cwd": str(project),
                    "project_path": str(project),
                },
            )
        )


def test_command_executor_corrects_hallucinated_absolute_project_path_in_dry_run(tmp_path) -> None:
    project = tmp_path / "project"
    target = project / "src" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('ok')\n", encoding="utf-8")

    result = command_executor(
        ToolInputMetadata.from_mapping(
            "command_executor",
            {
                "command": "python /workspace/openpilot/src/main.py --help",
                "mode": "dry_run",
                "cwd": str(project),
                "project_path": str(project),
            },
        )
    )

    assert result.result.command == f"python {target.resolve()} --help"
    assert result.result.attributes["command_approval"]["command"] == f"python {target.resolve()} --help"
