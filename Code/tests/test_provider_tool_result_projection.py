from __future__ import annotations

import hashlib

import pytest

from core.provider_tool_result_projection import (
    MAX_PROVIDER_RESULT_ARTIFACT_CHARS,
    MAX_PROVIDER_RESULT_FILES,
    MAX_PROVIDER_RESULT_PREVIEW_CHARS,
    ProviderToolResultProjectionError,
    project_provider_tool_result,
)
from metadata import CodeArtifactMetadata, FileArtifactMetadata, TextArtifactMetadata


def test_large_text_artifact_is_bounded_with_wire_lineage() -> None:
    content = "x" * 5_000

    projected, artifact_ref = project_provider_tool_result(
        TextArtifactMetadata(content=content, title="large.txt"),
        source_id="project-call-1",
        provider_call_id="provider-call-1",
    )

    assert projected["kind"] == "text_artifact"
    assert projected["title"] == "large.txt"
    assert projected["content_type"] == "text/plain"
    assert len(projected["preview"]) == MAX_PROVIDER_RESULT_PREVIEW_CHARS
    assert projected["preview"].endswith("...[truncated]")
    assert artifact_ref == {
        "kind": "text_artifact",
        "source_id": "project-call-1",
        "provider_call_id": "provider-call-1",
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "bytes": 5_000,
        "chars": 5_000,
    }


def test_complete_short_file_is_inline_complete_evidence() -> None:
    projected, artifact_ref = project_provider_tool_result(
        FileArtifactMetadata(
            content="complete README content",
            file_path="README.md",
            lines_read=2,
            total_lines=2,
            truncated=False,
        ),
        source_id="project-call-2",
        provider_call_id="provider-call-2",
    )

    assert projected["evidence_status"] == "complete"
    assert projected["projection_status"] == "inline"
    assert projected["content"] == "complete README content"
    assert "preview" not in projected
    assert artifact_ref is not None
    assert artifact_ref["file_path"] == "README.md"


def test_partial_file_uses_bounded_preview() -> None:
    projected, _ = project_provider_tool_result(
        FileArtifactMetadata(
            content="line\n" * 1_000,
            file_path="large.py",
            lines_read=100,
            total_lines=1_000,
            truncated=True,
        ),
        source_id="project-call-3",
        provider_call_id="provider-call-3",
    )

    assert projected["evidence_status"] == "partial"
    assert projected["projection_status"] == "bounded_preview"
    assert len(projected["preview"]) <= MAX_PROVIDER_RESULT_PREVIEW_CHARS
    assert "content" not in projected


def test_declared_window_keeps_complete_window_semantics() -> None:
    result = {
        "kind": "file_artifact",
        "content": "def anchor():\n    return True\n" * 100,
        "file_path": "source.py",
        "lines_read": 120,
        "total_lines": 2_000,
        "truncated": True,
        "read_window": {
            "read_mode": "adaptive",
            "offset": 1_240,
            "max_lines": 120,
        },
    }

    projected, _ = project_provider_tool_result(
        result,
        source_id="project-call-4",
        provider_call_id="provider-call-4",
        declared_window_complete=True,
    )

    assert projected["evidence_status"] == "complete"
    assert projected["projection_status"] == "bounded_window"
    assert projected["read_window"] == result["read_window"]
    assert len(projected["preview"]) <= MAX_PROVIDER_RESULT_PREVIEW_CHARS


def test_code_artifact_projects_handoff_reference_without_storing_code() -> None:
    code = "def game():\n    return 'snake'\n" * 200

    projected, artifact_ref = project_provider_tool_result(
        CodeArtifactMetadata(code=code, language="python"),
        source_id="project-call-5",
        provider_call_id="provider-call-5",
    )

    assert projected["kind"] == "code_artifact"
    assert len(projected["preview"]) <= MAX_PROVIDER_RESULT_PREVIEW_CHARS
    assert "artifact_ref" in projected["artifact_handoff"]
    assert code not in projected.values()
    assert artifact_ref is not None
    assert artifact_ref["language"] == "python"
    assert "content" not in artifact_ref
    assert "code" not in artifact_ref


def test_projection_does_not_mutate_mapping_input() -> None:
    result = {
        "kind": "text_artifact",
        "content": "evidence" * 100,
        "title": "notes.txt",
    }
    original = dict(result)

    project_provider_tool_result(
        result,
        source_id="project-call-6",
        provider_call_id="provider-call-6",
    )

    assert result == original


def test_non_mapping_result_is_bounded_as_scalar_value() -> None:
    projected, artifact_ref = project_provider_tool_result(
        "x" * 1_000,
        source_id="project-call-7",
        provider_call_id="provider-call-7",
    )

    assert len(projected["value"]) <= 320
    assert artifact_ref is None


@pytest.mark.parametrize("declared_window_complete", [1, "true", None])
def test_projection_requires_literal_declared_window_control(
    declared_window_complete,
) -> None:
    with pytest.raises(ProviderToolResultProjectionError):
        project_provider_tool_result(
            {},
            source_id="project-call-8",
            provider_call_id="provider-call-8",
            declared_window_complete=declared_window_complete,
        )


@pytest.mark.parametrize(
    ("source_id", "provider_call_id"),
    [
        ("", "provider-call"),
        ("project-call", ""),
        (" " * 3, "provider-call"),
        ("project-call", " " * 3),
        ("x" * 257, "provider-call"),
        ("project-call", "x" * 257),
    ],
)
def test_projection_rejects_invalid_lineage_ids(
    source_id: str,
    provider_call_id: str,
) -> None:
    with pytest.raises(ProviderToolResultProjectionError):
        project_provider_tool_result(
            {},
            source_id=source_id,
            provider_call_id=provider_call_id,
        )


def test_projection_accepts_exact_artifact_limit_and_rejects_overflow() -> None:
    exact = TextArtifactMetadata(
        content="x" * MAX_PROVIDER_RESULT_ARTIFACT_CHARS,
    )

    projected, artifact_ref = project_provider_tool_result(
        exact,
        source_id="project-call-9",
        provider_call_id="provider-call-9",
    )

    assert len(projected["preview"]) == MAX_PROVIDER_RESULT_PREVIEW_CHARS
    assert artifact_ref is not None
    assert artifact_ref["chars"] == MAX_PROVIDER_RESULT_ARTIFACT_CHARS

    with pytest.raises(ProviderToolResultProjectionError):
        project_provider_tool_result(
            TextArtifactMetadata(
                content="x" * (MAX_PROVIDER_RESULT_ARTIFACT_CHARS + 1),
            ),
            source_id="project-call-10",
            provider_call_id="provider-call-10",
        )


def test_projection_enforces_exact_file_list_character_boundary() -> None:
    projected, _ = project_provider_tool_result(
        {
            "kind": "file_artifact",
            "files": ["x" * MAX_PROVIDER_RESULT_ARTIFACT_CHARS],
        },
        source_id="project-call-11",
        provider_call_id="provider-call-11",
    )

    assert len(projected["files"][0]) == 4_096

    with pytest.raises(ProviderToolResultProjectionError):
        project_provider_tool_result(
            {
                "kind": "file_artifact",
                "files": ["x" * (MAX_PROVIDER_RESULT_ARTIFACT_CHARS + 1)],
            },
            source_id="project-call-11",
            provider_call_id="provider-call-11",
        )


def test_projection_enforces_exact_file_list_item_boundary() -> None:
    projected, _ = project_provider_tool_result(
        {
            "kind": "file_artifact",
            "files": ["x"] * MAX_PROVIDER_RESULT_FILES,
        },
        source_id="project-call-11-items",
        provider_call_id="provider-call-11-items",
    )

    assert len(projected["files"]) == MAX_PROVIDER_RESULT_FILES

    with pytest.raises(ProviderToolResultProjectionError):
        project_provider_tool_result(
            {
                "kind": "file_artifact",
                "files": ["x"] * (MAX_PROVIDER_RESULT_FILES + 1),
            },
            source_id="project-call-11-items",
            provider_call_id="provider-call-11-items",
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_projection_rejects_non_finite_scalar_fields(value: float) -> None:
    with pytest.raises(ProviderToolResultProjectionError):
        project_provider_tool_result(
            {"kind": "command_result", "count": value},
            source_id="project-call-12",
            provider_call_id="provider-call-12",
        )
