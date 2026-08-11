from __future__ import annotations

import pytest
from pydantic import ValidationError

from metadata import ProviderCodeArtifactReference, ToolInputMetadata
from metadata.artifacts import (
    MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_BYTES,
    MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS,
    MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_ID_CHARS,
)


def _reference_payload(**updates):
    payload = {
        "kind": "code_artifact",
        "source_id": "project-call-1",
        "provider_call_id": "provider-call-1",
        "sha256": "a" * 64,
        "bytes": 12,
        "chars": 12,
        "language": "python",
    }
    payload.update(updates)
    return payload


def test_reference_contract_is_strict_frozen_and_json_round_trips() -> None:
    reference = ProviderCodeArtifactReference(**_reference_payload())

    restored = ProviderCodeArtifactReference.model_validate_json(
        reference.model_dump_json()
    )

    assert restored == reference
    with pytest.raises(ValidationError):
        ProviderCodeArtifactReference(
            **_reference_payload(unexpected=True)
        )
    with pytest.raises(ValidationError):
        reference.language = "javascript"


def test_tool_input_parses_artifact_reference_as_typed_field() -> None:
    metadata = ToolInputMetadata.from_mapping(
        "file_patch_writer",
        {
            "file_path": "README.md",
            "operation_kind": "add_symbol",
            "artifact_ref": _reference_payload(),
        },
    )

    assert isinstance(metadata.artifact_ref, ProviderCodeArtifactReference)
    assert metadata.artifact_ref.provider_call_id == "provider-call-1"
    assert "artifact_ref" not in metadata.attributes
    assert metadata.to_params()["artifact_ref"] == _reference_payload()


def test_tool_input_json_round_trip_preserves_typed_reference() -> None:
    metadata = ToolInputMetadata.from_mapping(
        "file_patch_writer",
        {"artifact_ref": _reference_payload()},
    )

    restored = ToolInputMetadata.model_validate_json(metadata.model_dump_json())

    assert restored.artifact_ref == metadata.artifact_ref


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in _reference_payload().items() if key != "kind"},
        _reference_payload(kind="file_artifact"),
        _reference_payload(sha256="sha256:" + "a" * 64),
        _reference_payload(sha256="A" * 64),
        _reference_payload(source_id=""),
        _reference_payload(provider_call_id=""),
        _reference_payload(bytes=0),
        _reference_payload(chars=0),
        _reference_payload(language=""),
        _reference_payload(extra="not-allowed"),
    ],
)
def test_tool_input_rejects_invalid_artifact_reference(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ToolInputMetadata.from_mapping(
            "file_patch_writer",
            {"artifact_ref": payload},
        )


@pytest.mark.parametrize("artifact_ref", ["sha256:abc", [], 1, True])
def test_tool_input_rejects_non_object_artifact_reference(artifact_ref) -> None:
    with pytest.raises(ValidationError):
        ToolInputMetadata.from_mapping(
            "file_patch_writer",
            {"artifact_ref": artifact_ref},
        )


def test_reference_accepts_exact_field_boundaries_and_rejects_overflow() -> None:
    exact = ProviderCodeArtifactReference(
        **_reference_payload(
            source_id="s" * MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_ID_CHARS,
            provider_call_id=(
                "p" * MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_ID_CHARS
            ),
            bytes=MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_BYTES,
            chars=MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS,
            language="l" * 64,
        )
    )

    assert exact.chars == MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS
    with pytest.raises(ValidationError):
        ProviderCodeArtifactReference(
            **_reference_payload(
                source_id=(
                    "s" * (MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_ID_CHARS + 1)
                )
            )
        )
    with pytest.raises(ValidationError):
        ProviderCodeArtifactReference(
            **_reference_payload(
                provider_call_id=(
                    "p" * (MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_ID_CHARS + 1)
                )
            )
        )
    with pytest.raises(ValidationError):
        ProviderCodeArtifactReference(
            **_reference_payload(
                chars=MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_CHARS + 1
            )
        )
    with pytest.raises(ValidationError):
        ProviderCodeArtifactReference(
            **_reference_payload(
                bytes=MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_BYTES + 1
            )
        )
    with pytest.raises(ValidationError):
        ProviderCodeArtifactReference(
            **_reference_payload(language="l" * 65)
        )


def test_unrelated_unknown_tool_input_still_uses_diagnostic_attributes() -> None:
    metadata = ToolInputMetadata.from_mapping(
        "custom_tool",
        {"diagnostic_only": "value"},
    )

    assert metadata.attributes == {"diagnostic_only": "value"}
    assert metadata.artifact_ref is None
