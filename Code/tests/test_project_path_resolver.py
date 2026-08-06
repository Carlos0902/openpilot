from __future__ import annotations

from pathlib import Path

from memory.project_index import ProjectIndexManager
from memory.project_path_resolver import ProjectPathResolver, extract_command_path_references
from metadata import PathIntentMetadata


def test_project_path_resolver_corrects_hallucinated_workspace_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    resolver = ProjectPathResolver(project)
    intent = PathIntentMetadata(
        project_root=str(project),
        raw_path="/workspace/openpilot",
        intent_kind="existing_directory",
        operation="read",
        path_source="decision_need",
    )

    resolution = resolver.resolve(intent)

    assert resolution.status == "corrected"
    assert resolution.resolved_path == str(project)
    assert resolution.correction_rule == "hallucinated_root_alias"
    assert resolution.used_sketch is False
    assert resolution.inside_project is True


def test_project_path_resolver_uses_file_index_for_dropped_repo_segment(tmp_path: Path) -> None:
    project = tmp_path / "Code"
    target = project / "src" / "ui" / "cli.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('ok')\n", encoding="utf-8")
    ProjectIndexManager(project).update_file_index(target)

    resolver = ProjectPathResolver(project)
    intent = PathIntentMetadata(
        project_root=str(project),
        raw_path=str(tmp_path / "src" / "ui" / "cli.py"),
        intent_kind="existing_file",
        operation="read",
        path_source="decision_need",
    )

    resolution = resolver.resolve(intent)

    assert resolution.status == "corrected"
    assert resolution.resolved_path == str(target.resolve())
    assert resolution.correction_rule == "file_index_suffix_match"
    assert resolution.used_file_index is True
    assert resolution.exists_verified is True


def test_project_path_resolver_marks_ambiguous_suffix_matches(tmp_path: Path) -> None:
    project = tmp_path / "project"
    first = project / "pkg_a" / "app.py"
    second = project / "pkg_b" / "app.py"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("print('a')\n", encoding="utf-8")
    second.write_text("print('b')\n", encoding="utf-8")

    manager = ProjectIndexManager(project)
    manager.update_file_index(first)
    manager.update_file_index(second)

    resolver = ProjectPathResolver(project)
    intent = PathIntentMetadata(
        project_root=str(project),
        raw_path=str(tmp_path / "external" / "app.py"),
        intent_kind="existing_file",
        operation="read",
        path_source="decision_need",
    )

    resolution = resolver.resolve(intent)

    assert resolution.status == "ambiguous"
    assert resolution.resolved_path == ""
    assert resolution.used_file_index is True
    assert len(resolution.candidate_paths) == 2


def test_extract_command_path_references_distinguishes_executable_data_and_redirection(tmp_path: Path) -> None:
    command = "/usr/bin/env /opt/anaconda3/bin/python3.13 /workspace/openpilot/src/main.py > /tmp/out.txt"

    references = extract_command_path_references(command)

    assert [reference.intent_kind for reference in references] == [
        "command_executable_path",
        "command_executable_path",
        "command_data_path",
        "command_redirection_path",
    ]
    assert references[0].raw_path == "/usr/bin/env"
    assert references[1].raw_path == "/opt/anaconda3/bin/python3.13"
    assert references[2].raw_path == "/workspace/openpilot/src/main.py"
    assert references[3].raw_path == "/tmp/out.txt"
    assert references[3].operation == "write"
