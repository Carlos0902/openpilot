from __future__ import annotations

import shutil
from types import SimpleNamespace

from memory.agents.virtual_environment_manager import (
    EnvOperationResult,
    EnvironmentManager as MemoryEnvironmentManager,
    VirtualEnvironmentManager,
)
from memory.agents.project_environment_tool import (
    EnvironmentManager,
    infer_project_dependencies,
    inspect_project_environment,
    project_environment_tool_executor,
)
from metadata import (
    EnvironmentOperation,
    EnvironmentReadiness,
    EnvironmentSyncMetadata,
    ToolInputMetadata,
)


class FakeEnvironmentManager:
    def __init__(self) -> None:
        self.created = []
        self.installed_packages = []

    def create_env(self, env_name):
        self.created.append(env_name)
        return EnvOperationResult(success=True, message="created")

    def install_package(self, env_name, package):
        self.installed_packages.append((env_name, package))
        return EnvOperationResult(success=True, message=f"installed {package}")

    def install_requirements(self, env_name, requirements_file):
        return EnvOperationResult(success=True, message="requirements installed")

    def list_packages(self, env_name):
        return [f"{package}==1.0" for _, package in self.installed_packages]

    def get_env_info(self, env_name):
        return SimpleNamespace(python_version="3.13.0")


def test_environment_lifecycle_metadata_is_typed_and_legacy_defaults_fail_closed() -> None:
    legacy = EnvironmentSyncMetadata(project_path="/tmp/project")

    assert legacy.operation == EnvironmentOperation.LEGACY_SYNC
    assert legacy.readiness == EnvironmentReadiness.UNKNOWN
    assert legacy.environment_id == ""

    ready = EnvironmentSyncMetadata(
        project_path="/tmp/project",
        operation=EnvironmentOperation.PREFLIGHT,
        readiness=EnvironmentReadiness.READY,
        environment_id="env:123",
        python_executable="/tmp/project/.venv/bin/python",
        command_cwd="/tmp/project",
    )

    assert EnvironmentSyncMetadata.model_validate(ready.to_json_dict()) == ready


def test_environment_preflight_has_zero_project_side_effects(tmp_path) -> None:
    project = tmp_path / "calculator"
    project.mkdir()
    (project / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    before = sorted(path.relative_to(project) for path in project.rglob("*"))

    result = inspect_project_environment(project_path=project, run_command="python -m pytest -q")

    after = sorted(path.relative_to(project) for path in project.rglob("*"))
    assert after == before
    assert result.operation == EnvironmentOperation.PREFLIGHT
    assert result.readiness == EnvironmentReadiness.SETUP_REQUIRED
    assert result.detected_packages == ["pytest"]
    assert result.missing_packages == ["pytest"]
    assert result.environment_id
    assert not (project / ".venv").exists()
    assert not (project / ".git").exists()
    assert not (project / ".openpilot").exists()


def test_environment_preflight_attaches_existing_ready_venv_without_executing_it(tmp_path) -> None:
    project = tmp_path / "calculator"
    project.mkdir()
    (project / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    python = project / ".venv" / "bin" / "python"
    pip = project / ".venv" / "bin" / "pip"
    python.parent.mkdir(parents=True)
    python.write_text("not executable and must not be invoked", encoding="utf-8")
    pip.write_text("not executable and must not be invoked", encoding="utf-8")
    dist_info = project / ".venv" / "lib" / "python3.13" / "site-packages" / "pytest-9.0.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text("Name: pytest\nVersion: 9.0.0\n", encoding="utf-8")

    result = inspect_project_environment(project_path=project, run_command="python -m pytest -q")

    assert result.readiness == EnvironmentReadiness.READY
    assert result.installed_packages == ["pytest==9.0.0"]
    assert result.missing_packages == []
    assert result.python_executable == str(python)
    assert result.python_command == str(python)


def test_environment_identity_changes_when_installed_distribution_set_changes(tmp_path) -> None:
    project = tmp_path / "calculator"
    project.mkdir()
    python = project / ".venv" / "bin" / "python"
    pip = project / ".venv" / "bin" / "pip"
    python.parent.mkdir(parents=True)
    python.write_text("python marker", encoding="utf-8")
    pip.write_text("pip marker", encoding="utf-8")
    site_packages = project / ".venv" / "lib" / "python3.13" / "site-packages"
    first = inspect_project_environment(project_path=project)
    dist_info = site_packages / "pytest-9.0.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text("Name: pytest\nVersion: 9.0.0\n", encoding="utf-8")

    second = inspect_project_environment(project_path=project)

    assert first.readiness == EnvironmentReadiness.READY
    assert second.readiness == EnvironmentReadiness.READY
    assert first.environment_id != second.environment_id


def test_virtual_environment_manager_agent_exposes_instruction_functions(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("rich\n", encoding="utf-8")
    manager = VirtualEnvironmentManager(tmp_path)

    creator = manager.environment_creator()
    installer = manager.packet_installer(["rich"])
    context = manager.get_environment_context()

    assert creator["commands"] == ["python -m venv .venv"]
    assert installer["commands"] == [".venv/bin/pip install rich"]
    assert context["packages"] == ["rich"]
    assert ".venv/bin/python" == context["python_executable"]


def test_env_tools_environment_manager_is_memory_manager_alias() -> None:
    assert EnvironmentManager is MemoryEnvironmentManager


def test_project_environment_tool_uses_injected_manager_without_real_venv(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("import pygame\nprint('ok')\n", encoding="utf-8")
    fake_manager = FakeEnvironmentManager()

    result = project_environment_tool_executor(
        ToolInputMetadata.from_mapping("project_environment_tool", {
            "project_path": str(project),
            "written_files": ["app.py"],
            "install": True,
            "_environment_manager": fake_manager,
        })
    )

    assert result["env_name"] == ".venv"
    assert result["detected_packages"] == ["pygame"]
    assert fake_manager.created == [".venv"]
    assert fake_manager.installed_packages == [(".venv", "pygame")]
    assert not (project / ".venv").exists()
    assert result.result.command_cwd == str(project)
    assert result.result.python_command.endswith(".venv/bin/python")
    assert result.result.pip_command.endswith(".venv/bin/pip")
    assert result.result.command_env["VIRTUAL_ENV"].endswith(".venv")
    assert str(project / ".venv" / "bin") in result.result.command_env["PATH"]
    assert result.result.dependencies[0].package_name == "pygame"
    assert result.result.dependencies[0].import_names == ["pygame"]
    assert "pygame" in result.result.dependency_strategy.preserve_packages
    if shutil.which("git"):
        assert result.result.git_repository is not None
        assert result.result.git_repository.initialized is True
        assert result.result.git_snapshot is not None
        assert ".venv/" in (project / ".gitignore").read_text(encoding="utf-8")
    else:
        assert any("Git safety unavailable" in warning for warning in result.result.warnings)


def test_project_environment_tool_maps_import_name_to_published_distribution(tmp_path) -> None:
    app = tmp_path / "assistant.py"
    app.write_text("import speech_recognition\nimport pyttsx3\n", encoding="utf-8")

    detected = infer_project_dependencies(tmp_path, ["assistant.py"])

    assert detected == ["pyttsx3", "SpeechRecognition"]


def test_project_environment_tool_scans_project_tests_outside_written_files(tmp_path) -> None:
    project = tmp_path / "calculator"
    project.mkdir()
    (project / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (project / "test_calculator.py").write_text(
        "import pytest\n\nfrom calculator import add\n",
        encoding="utf-8",
    )

    detected = infer_project_dependencies(project, ["calculator.py"])

    assert detected == ["pytest"]


def test_project_environment_tool_persists_and_updates_stack_preset(tmp_path) -> None:
    project = tmp_path / "assistant"
    project.mkdir()
    (project / "assistant.py").write_text("print('assistant')\n", encoding="utf-8")
    fake_manager = FakeEnvironmentManager()

    initial = project_environment_tool_executor(
        ToolInputMetadata.from_mapping(
            "project_environment_tool",
            {
                "project_path": str(project),
                "goal": "帮我做一个个人数字助手",
                "written_files": ["assistant.py"],
                "install": False,
                "_environment_manager": fake_manager,
            },
        )
    )
    updated = project_environment_tool_executor(
        ToolInputMetadata.from_mapping(
            "project_environment_tool",
            {
                "project_path": str(project),
                "written_files": ["assistant.py"],
                "install": False,
                "stack_preset_update": {
                    "delivery_surface": "terminal",
                    "architecture": "terminal_application",
                    "frontend_language": "terminal_text",
                    "ui_strategy": "terminal_ui",
                },
                "_environment_manager": fake_manager,
            },
        )
    )

    assert initial.result.stack_preset.delivery_surface == "browser"
    assert initial.result.stack_preset.revision == 1
    assert updated.result.stack_preset.delivery_surface == "terminal"
    assert updated.result.stack_preset.revision == 2
    assert (project / ".openpilot" / "project_stack.json").exists()
