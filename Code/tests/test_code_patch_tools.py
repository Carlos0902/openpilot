from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from metadata import ReasoningMode, RuntimeBudgetMetadata, ToolInputMetadata
from tools.code_editor import code_editor_executor
from tools.file_delete_tool import file_delete_tool_executor
from tools.file_patch_writer import file_patch_writer_executor
from tools.file_writer import file_writer_executor


def test_file_writer_rejects_existing_file_without_explicit_replace(tmp_path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="operation_kind=file_replace"):
        file_writer_executor(
            ToolInputMetadata.from_mapping(
                "file_writer",
                {
                    "file_path": str(target),
                    "content": "print('new')\n",
                    "overwrite": True,
                },
            )
        )

    result = file_writer_executor(
        ToolInputMetadata.from_mapping(
            "file_writer",
            {
                "file_path": str(target),
                "content": "print('new')\n",
                "overwrite": True,
                "operation_kind": "file_replace",
            },
        )
    )

    assert result.result.file_path == str(target.absolute())
    assert target.read_text(encoding="utf-8") == "print('new')\n"


def test_file_writer_rejects_python_source_written_as_requirements(tmp_path) -> None:
    target = tmp_path / "requirements.txt"

    with pytest.raises(ValueError, match="rejected malformed requirements content"):
        file_writer_executor(
            ToolInputMetadata.from_mapping(
                "file_writer",
                {
                    "file_path": str(target),
                    "content": "#!/usr/bin/env python3\nimport json\nclass Reminder:\n    pass\n",
                },
            )
        )

    assert not target.exists()


def test_file_patch_writer_inserts_generated_unit_without_rewriting_existing_content(tmp_path) -> None:
    target = tmp_path / "app.py"
    target.write_text("def existing():\n    return 1\n", encoding="utf-8")

    result = file_patch_writer_executor(
        ToolInputMetadata.from_mapping(
            "file_patch_writer",
            {
                "file_path": str(target),
                "operation_kind": "add_symbol",
                "generated_unit": "def added():\n    return 2",
            },
        )
    )

    updated = target.read_text(encoding="utf-8")
    assert "def existing():" in updated
    assert "def added():" in updated
    assert result.result.attributes["changed_ranges"] == [{"line_start": 4, "line_end": 5}]


def test_code_editor_and_patch_writer_replace_only_target_symbol(tmp_path) -> None:
    target = tmp_path / "app.py"
    target.write_text(
        "def keep():\n"
        "    return 'keep'\n\n"
        "def change():\n"
        "    return 'old'\n",
        encoding="utf-8",
    )

    edit_result = code_editor_executor(
        ToolInputMetadata.from_mapping(
            "code_editor",
            {
                "file_path": str(target),
                "task_description": "Return the new value from change",
                "language": "python",
                "symbol_name": "change",
                "replacement_text": "def change():\n    return 'new'",
            },
        )
    )
    patch_result = file_patch_writer_executor(
        ToolInputMetadata.from_mapping(
            "file_patch_writer",
            {
                "file_path": str(target),
                "operation_kind": "modify_symbol",
                "patch": edit_result.result.attributes["patch"],
            },
        )
    )

    updated = target.read_text(encoding="utf-8")
    assert "def keep():\n    return 'keep'" in updated
    assert "def change():\n    return 'new'" in updated
    assert "return 'old'" not in updated
    assert patch_result.result.attributes["changed_ranges"] == [{"line_start": 4, "line_end": 5}]


def test_code_editor_fails_when_python_symbol_cannot_be_located(tmp_path) -> None:
    target = tmp_path / "app.py"
    target.write_text("def existing():\n    return 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Python symbol not found"):
        code_editor_executor(
            ToolInputMetadata.from_mapping(
                "code_editor",
                {
                    "file_path": str(target),
                    "task_description": "Change missing",
                    "language": "python",
                    "symbol_name": "missing",
                    "replacement_text": "def missing():\n    return 2",
                },
            )
        )


def test_enhancement_code_editor_uses_shared_completion_budget_and_routine_reasoning(
    tmp_path,
) -> None:
    target = tmp_path / "app.py"
    target.write_text("def change():\n    return 'old'\n", encoding="utf-8")

    class CapturingLLM:
        settings = SimpleNamespace(tool_event_reasoning_mode=ReasoningMode.DISABLED)

        def __init__(self) -> None:
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            return SimpleNamespace(
                content="```python\ndef change():\n    return 'new'\n```",
                usage={"completion_tokens": 80},
                finish_reason="stop",
            )

    budget = RuntimeBudgetMetadata()
    llm = CapturingLLM()
    input_metadata = ToolInputMetadata.from_mapping(
        "code_editor",
        {
            "file_path": str(target),
            "task_description": "Change the return value",
            "language": "python",
            "symbol_name": "change",
            "code": target.read_text(encoding="utf-8"),
        },
    ).model_copy(
        update={
            "runtime_handles": {
                "_llm_client": llm,
                "_runtime_budget": budget,
            }
        }
    )

    result = code_editor_executor(input_metadata)

    request = llm.requests[0]
    assert request.max_tokens is not None
    assert 400 <= request.max_tokens <= 1_600
    assert request.trace_info["completion_budget"]["purpose"] == "code_edit"
    assert request.trace_info["completion_budget"]["reserved_tokens"] == request.max_tokens
    assert request.reasoning_policy.mode == ReasoningMode.DISABLED
    assert budget.enhancement_completion_tokens_used == 80
    assert budget.enhancement_completion_tokens_reserved == 0
    assert "return 'new'" in result.result.code


def test_file_mutation_tools_refresh_indexes_and_directory_sketch(tmp_path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    target = project / "app.py"

    write_result = file_writer_executor(
        ToolInputMetadata.from_mapping(
            "file_writer",
            {
                "file_path": str(target),
                "content": "def run():\n    return 1\n",
            },
        )
    )

    index_update = write_result.result.attributes["index_update"]
    index_file = Path(index_update["index_file"])
    assert index_file.exists()
    index_payload = json.loads(index_file.read_text(encoding="utf-8"))
    assert index_payload["sections"][0]["title"] == "function run"
    old_hash = index_payload["content_sha256"]

    sketch = json.loads((project / "sketch.json").read_text(encoding="utf-8"))
    assert sketch["files"]["app.py"]["content_index"]["index_file"] == str(index_file)
    assert sketch["files"]["app.py"]["content_index"]["sections"][0]["line_start"] == 1

    patch_result = file_patch_writer_executor(
        ToolInputMetadata.from_mapping(
            "file_patch_writer",
            {
                "file_path": str(target),
                "operation_kind": "modify_symbol",
                "symbol_name": "run",
                "replacement_text": "def run():\n    return 2",
            },
        )
    )
    patched_index_file = Path(patch_result.result.attributes["index_update"]["index_file"])
    patched_payload = json.loads(patched_index_file.read_text(encoding="utf-8"))
    assert patched_payload["content_sha256"] != old_hash
    assert "return 2" in target.read_text(encoding="utf-8")

    delete_result = file_delete_tool_executor(
        ToolInputMetadata.from_mapping("file_delete_tool", {"file_path": str(target)})
    )

    assert delete_result.result.attributes["deleted"] is True
    assert not target.exists()
    assert not index_file.exists()
    sketch_after_delete = json.loads((project / "sketch.json").read_text(encoding="utf-8"))
    assert "app.py" not in sketch_after_delete["files"]


def test_file_patch_writer_can_target_index_sections_and_delete_ranges(tmp_path) -> None:
    target = tmp_path / "notes.md"
    file_writer_executor(
        ToolInputMetadata.from_mapping(
            "file_writer",
            {
                "file_path": str(target),
                "content": "# One\nold body\n\n# Two\nkeep body\n",
            },
        )
    )
    sketch = json.loads((tmp_path / "sketch.json").read_text(encoding="utf-8"))
    first_section = sketch["files"]["notes.md"]["content_index"]["sections"][0]

    file_patch_writer_executor(
        ToolInputMetadata.from_mapping(
            "file_patch_writer",
            {
                "file_path": str(target),
                "section_id": first_section["section_id"],
                "replacement_text": "# One\nnew body",
            },
        )
    )

    assert "# One\nnew body" in target.read_text(encoding="utf-8")

    file_patch_writer_executor(
        ToolInputMetadata.from_mapping(
            "file_patch_writer",
            {
                "file_path": str(target),
                "operation_kind": "delete_section",
                "section_title": "Two",
            },
        )
    )

    updated = target.read_text(encoding="utf-8")
    assert "# One\nnew body" in updated
    assert "# Two" not in updated
