from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from core.provider_code_artifact_ledger import (
    MAX_PROVIDER_CODE_ARTIFACT_CHARS,
    MAX_PROVIDER_CODE_ARTIFACTS,
    MAX_PROVIDER_CODE_LEDGER_CHARS,
    ProviderCodeArtifactLedger,
    ProviderCodeArtifactLedgerError,
    ProviderCodeArtifactReference,
)
from metadata import CodeArtifactMetadata


def _artifact(code: str = "def generated():\n    return True\n") -> CodeArtifactMetadata:
    return CodeArtifactMetadata(code=code, language="python")


def test_reference_is_strict_frozen_and_json_round_trips() -> None:
    reference = ProviderCodeArtifactReference(
        kind="code_artifact",
        source_id="project-call-1",
        provider_call_id="provider-call-1",
        sha256="a" * 64,
        bytes=12,
        chars=12,
        language="python",
    )

    restored = ProviderCodeArtifactReference.model_validate_json(
        reference.model_dump_json()
    )

    assert restored == reference
    with pytest.raises(ValidationError):
        ProviderCodeArtifactReference(
            **reference.model_dump(),
            unexpected=True,
        )
    with pytest.raises(ValidationError):
        reference.language = "javascript"


def test_register_and_resolve_verified_code_reference() -> None:
    code = "def test_generated():\n    assert True\n"
    ledger = ProviderCodeArtifactLedger()

    reference = ledger.register(
        _artifact(code),
        source_id="project-call-2",
        provider_call_id="provider-call-2",
    )

    assert ledger.resolve(reference) == code
    assert ledger.resolve(reference.model_dump()) == code
    assert reference.sha256 == hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert reference.chars == len(code)
    assert reference.bytes == len(code.encode("utf-8"))


def test_resolve_accepts_normalized_sha256_prefix() -> None:
    ledger = ProviderCodeArtifactLedger()
    reference = ledger.register(
        _artifact(),
        source_id="project-call-prefix",
        provider_call_id="provider-call-prefix",
    ).model_dump()
    reference["sha256"] = f"sha256:{reference['sha256'].upper()}"

    assert "generated" in ledger.resolve(reference)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", "0" * 64),
        ("source_id", "different-source"),
        ("provider_call_id", "different-provider-call"),
        ("chars", 1),
        ("bytes", 1),
        ("language", "javascript"),
    ],
)
def test_resolve_rejects_reference_mismatch(field: str, value) -> None:
    ledger = ProviderCodeArtifactLedger()
    reference = ledger.register(
        _artifact(),
        source_id="project-call-3",
        provider_call_id="provider-call-3",
    ).model_dump()
    reference[field] = value

    with pytest.raises(ProviderCodeArtifactLedgerError):
        ledger.resolve(reference)


def test_resolve_rejects_unknown_or_invalid_reference() -> None:
    ledger = ProviderCodeArtifactLedger()

    with pytest.raises(ProviderCodeArtifactLedgerError):
        ledger.resolve(
            {
                "kind": "code_artifact",
                "source_id": "project-call-unknown",
                "provider_call_id": "provider-call-unknown",
                "sha256": "a" * 64,
                "bytes": 10,
                "chars": 10,
                "language": "python",
            }
        )
    with pytest.raises(ProviderCodeArtifactLedgerError):
        ledger.resolve("not-a-reference")
    with pytest.raises(ProviderCodeArtifactLedgerError):
        ledger.resolve(
            {
                "source_id": "project-call-missing-kind",
                "provider_call_id": "provider-call-missing-kind",
                "sha256": "a" * 64,
                "bytes": 10,
                "chars": 10,
                "language": "python",
            }
        )


def test_register_rejects_conflicting_code_and_content() -> None:
    ledger = ProviderCodeArtifactLedger()

    with pytest.raises(ProviderCodeArtifactLedgerError):
        ledger.register(
            {
                "kind": "code_artifact",
                "code": "print('code')",
                "content": "print('different')",
                "language": "python",
            },
            source_id="project-call-4",
            provider_call_id="provider-call-4",
        )


def test_exact_registration_is_idempotent() -> None:
    ledger = ProviderCodeArtifactLedger(max_artifacts=1, max_total_chars=100)

    first = ledger.register(
        _artifact("print('same')"),
        source_id="project-call-5",
        provider_call_id="provider-call-5",
    )
    second = ledger.register(
        _artifact("print('same')"),
        source_id="project-call-5",
        provider_call_id="provider-call-5",
    )

    assert second == first
    assert ledger.artifact_count == 1
    assert ledger.total_chars == len("print('same')")


def test_same_code_with_distinct_lineage_is_independently_authorized() -> None:
    ledger = ProviderCodeArtifactLedger(max_artifacts=2, max_total_chars=100)
    code = "print('same')"

    first = ledger.register(
        _artifact(code),
        source_id="project-call-6a",
        provider_call_id="provider-call-6a",
    )
    second = ledger.register(
        _artifact(code),
        source_id="project-call-6b",
        provider_call_id="provider-call-6b",
    )

    assert first.sha256 == second.sha256
    assert first.source_id != second.source_id
    assert ledger.resolve(first) == code
    assert ledger.resolve(second) == code
    assert ledger.artifact_count == 2


def test_same_lineage_cannot_rebind_different_code() -> None:
    ledger = ProviderCodeArtifactLedger(max_artifacts=2, max_total_chars=100)
    ledger.register(
        _artifact("print('first')"),
        source_id="project-call-lineage",
        provider_call_id="provider-call-lineage",
    )

    with pytest.raises(ProviderCodeArtifactLedgerError):
        ledger.register(
            _artifact("print('second')"),
            source_id="project-call-lineage",
            provider_call_id="provider-call-lineage",
        )

    assert ledger.artifact_count == 1
    assert ledger.total_chars == len("print('first')")


def test_ledger_enforces_exact_artifact_capacity_atomically() -> None:
    ledger = ProviderCodeArtifactLedger(max_artifacts=2, max_total_chars=100)
    for index in range(2):
        ledger.register(
            _artifact(f"print({index})"),
            source_id=f"project-call-7-{index}",
            provider_call_id=f"provider-call-7-{index}",
        )

    before_chars = ledger.total_chars
    with pytest.raises(ProviderCodeArtifactLedgerError):
        ledger.register(
            _artifact("print('overflow')"),
            source_id="project-call-7-overflow",
            provider_call_id="provider-call-7-overflow",
        )

    assert ledger.artifact_count == 2
    assert ledger.total_chars == before_chars


def test_ledger_enforces_exact_total_character_capacity_atomically() -> None:
    ledger = ProviderCodeArtifactLedger(max_artifacts=2, max_total_chars=10)
    ledger.register(
        _artifact("x" * 10),
        source_id="project-call-8",
        provider_call_id="provider-call-8",
    )

    with pytest.raises(ProviderCodeArtifactLedgerError):
        ledger.register(
            _artifact("y"),
            source_id="project-call-8-overflow",
            provider_call_id="provider-call-8-overflow",
        )

    assert ledger.artifact_count == 1
    assert ledger.total_chars == 10


def test_register_accepts_exact_artifact_limit_and_rejects_overflow() -> None:
    ledger = ProviderCodeArtifactLedger()
    exact = "x" * MAX_PROVIDER_CODE_ARTIFACT_CHARS

    reference = ledger.register(
        _artifact(exact),
        source_id="project-call-9",
        provider_call_id="provider-call-9",
    )

    assert reference.chars == MAX_PROVIDER_CODE_ARTIFACT_CHARS
    with pytest.raises(ProviderCodeArtifactLedgerError):
        ledger.register(
            _artifact(exact + "x"),
            source_id="project-call-9-overflow",
            provider_call_id="provider-call-9-overflow",
        )


def test_reference_tracks_unicode_byte_and_character_counts_separately() -> None:
    ledger = ProviderCodeArtifactLedger()
    code = "变量 = '蛇'\n"

    reference = ledger.register(
        _artifact(code),
        source_id="project-call-10",
        provider_call_id="provider-call-10",
    )

    assert reference.chars == len(code)
    assert reference.bytes == len(code.encode("utf-8"))
    assert reference.bytes > reference.chars


@pytest.mark.parametrize(
    ("max_artifacts", "max_total_chars"),
    [
        (0, 1),
        (MAX_PROVIDER_CODE_ARTIFACTS + 1, 1),
        (True, 1),
        (1, 0),
        (1, MAX_PROVIDER_CODE_LEDGER_CHARS + 1),
        (1, True),
    ],
)
def test_ledger_rejects_invalid_configured_bounds(
    max_artifacts,
    max_total_chars,
) -> None:
    with pytest.raises(ProviderCodeArtifactLedgerError):
        ProviderCodeArtifactLedger(
            max_artifacts=max_artifacts,
            max_total_chars=max_total_chars,
        )


def test_registration_does_not_mutate_mapping_input() -> None:
    artifact = {
        "kind": "code_artifact",
        "code": "print('safe')",
        "language": "python",
    }
    original = dict(artifact)

    ProviderCodeArtifactLedger().register(
        artifact,
        source_id="project-call-11",
        provider_call_id="provider-call-11",
    )

    assert artifact == original
