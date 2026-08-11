"""Bounded runtime ledger for checksum-verified provider code artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from enum import Enum
from typing import Any

from pydantic import ValidationError

from core.provider_tool_batch_admission import (
    MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
)
from core.provider_tool_result_projection import (
    MAX_PROVIDER_RESULT_ARTIFACT_CHARS,
)
from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_TOOL_ATTEMPTS
from metadata import CodeArtifactMetadata, ProviderCodeArtifactReference
from metadata.artifacts import MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_BYTES

MAX_PROVIDER_CODE_ARTIFACTS = MAX_PROVIDER_TOOL_ATTEMPTS
MAX_PROVIDER_CODE_ARTIFACT_CHARS = MAX_PROVIDER_RESULT_ARTIFACT_CHARS
MAX_PROVIDER_CODE_ARTIFACT_BYTES = MAX_PROVIDER_CODE_ARTIFACT_REFERENCE_BYTES
MAX_PROVIDER_CODE_LEDGER_CHARS = (
    MAX_PROVIDER_CODE_ARTIFACT_CHARS
    * MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE
)


class ProviderCodeArtifactLedgerError(ValueError):
    """Raised when artifact registration or resolution fails closed."""


class ProviderCodeArtifactLedger:
    """Own bounded code bodies and exact authorized provider references."""

    def __init__(
        self,
        *,
        max_artifacts: int = MAX_PROVIDER_CODE_ARTIFACTS,
        max_total_chars: int = MAX_PROVIDER_CODE_LEDGER_CHARS,
    ) -> None:
        if (
            type(max_artifacts) is not int
            or max_artifacts < 1
            or max_artifacts > MAX_PROVIDER_CODE_ARTIFACTS
        ):
            raise ProviderCodeArtifactLedgerError(
                "max_artifacts must be within provider artifact bounds"
            )
        if (
            type(max_total_chars) is not int
            or max_total_chars < 1
            or max_total_chars > MAX_PROVIDER_CODE_LEDGER_CHARS
        ):
            raise ProviderCodeArtifactLedgerError(
                "max_total_chars must be within provider artifact bounds"
            )
        self._max_artifacts = max_artifacts
        self._max_total_chars = max_total_chars
        self._entries: dict[
            tuple[str, str, str],
            tuple[str, ProviderCodeArtifactReference],
        ] = {}
        self._digest_by_lineage: dict[tuple[str, str], str] = {}
        self._total_chars = 0

    @property
    def artifact_count(self) -> int:
        return len(self._entries)

    @property
    def total_chars(self) -> int:
        return self._total_chars

    def register(
        self,
        artifact: CodeArtifactMetadata | Mapping[str, Any],
        *,
        source_id: str,
        provider_call_id: str,
    ) -> ProviderCodeArtifactReference:
        """Register one code body and return its body-free typed reference."""

        summary = _artifact_summary(artifact)
        code = _artifact_code(summary)
        if len(code) > MAX_PROVIDER_CODE_ARTIFACT_CHARS:
            raise ProviderCodeArtifactLedgerError(
                "code artifact exceeds the provider artifact character limit"
            )
        encoded = code.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        try:
            reference = ProviderCodeArtifactReference(
                kind="code_artifact",
                source_id=source_id,
                provider_call_id=provider_call_id,
                sha256=digest,
                bytes=len(encoded),
                chars=len(code),
                language=_artifact_language(summary),
            )
        except ValidationError as exc:
            raise ProviderCodeArtifactLedgerError(
                "code artifact reference fields are invalid"
            ) from exc

        lineage = (reference.source_id, reference.provider_call_id)
        existing_digest = self._digest_by_lineage.get(lineage)
        if existing_digest is not None and existing_digest != digest:
            raise ProviderCodeArtifactLedgerError(
                "code artifact lineage is already bound to different content"
            )
        key = (digest, reference.source_id, reference.provider_call_id)
        existing = self._entries.get(key)
        if existing is not None:
            existing_code, existing_reference = existing
            if existing_code != code or existing_reference != reference:
                raise ProviderCodeArtifactLedgerError(
                    "code artifact ledger entry is inconsistent"
                )
            return existing_reference

        if self.artifact_count >= self._max_artifacts:
            raise ProviderCodeArtifactLedgerError(
                "code artifact ledger capacity is exhausted"
            )
        if self._total_chars + len(code) > self._max_total_chars:
            raise ProviderCodeArtifactLedgerError(
                "code artifact ledger character capacity is exhausted"
            )
        self._entries[key] = (code, reference)
        self._digest_by_lineage[lineage] = digest
        self._total_chars += len(code)
        return reference

    def resolve(
        self,
        reference: ProviderCodeArtifactReference | Mapping[str, Any],
    ) -> str:
        """Resolve a reference only when every registered fact still matches."""

        normalized = _validated_reference(reference)
        key = (
            normalized.sha256,
            normalized.source_id,
            normalized.provider_call_id,
        )
        entry = self._entries.get(key)
        if entry is None:
            raise ProviderCodeArtifactLedgerError(
                "code artifact reference is stale or unknown"
            )
        code, expected = entry
        if normalized != expected:
            raise ProviderCodeArtifactLedgerError(
                "code artifact reference does not match its ledger entry"
            )
        encoded = code.encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != normalized.sha256:
            raise ProviderCodeArtifactLedgerError(
                "code artifact content checksum does not match"
            )
        if len(code) != normalized.chars or len(encoded) != normalized.bytes:
            raise ProviderCodeArtifactLedgerError(
                "code artifact size does not match its reference"
            )
        return code


def _artifact_summary(
    artifact: CodeArtifactMetadata | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(artifact, CodeArtifactMetadata):
        summary = artifact.to_json_dict()
    elif isinstance(artifact, Mapping):
        summary = dict(artifact)
    else:
        raise ProviderCodeArtifactLedgerError(
            "code artifact must be typed metadata or a mapping"
        )
    kind = summary.get("kind")
    if isinstance(kind, Enum):
        kind = kind.value
    if kind != "code_artifact":
        raise ProviderCodeArtifactLedgerError(
            "code artifact registration requires code_artifact kind"
        )
    return summary


def _artifact_code(summary: Mapping[str, Any]) -> str:
    code = summary.get("code")
    content = summary.get("content")
    if code not in (None, "") and not isinstance(code, str):
        raise ProviderCodeArtifactLedgerError(
            "code artifact code must be a string"
        )
    if content not in (None, "") and not isinstance(content, str):
        raise ProviderCodeArtifactLedgerError(
            "code artifact content must be a string"
        )
    if code not in (None, "") and content not in (None, "") and code != content:
        raise ProviderCodeArtifactLedgerError(
            "code artifact code and content must match"
        )
    value = code or content or ""
    if not value:
        raise ProviderCodeArtifactLedgerError(
            "code artifact registration requires non-empty code"
        )
    return value


def _artifact_language(summary: Mapping[str, Any]) -> str:
    language = summary.get("language") or "python"
    if not isinstance(language, str) or not language.strip():
        raise ProviderCodeArtifactLedgerError(
            "code artifact language must be a non-empty string"
        )
    return language


def _validated_reference(
    value: ProviderCodeArtifactReference | Mapping[str, Any],
) -> ProviderCodeArtifactReference:
    if isinstance(value, ProviderCodeArtifactReference):
        return value
    if not isinstance(value, Mapping):
        raise ProviderCodeArtifactLedgerError(
            "code artifact reference must be a mapping"
        )
    candidate = dict(value)
    digest = candidate.get("sha256")
    if isinstance(digest, str):
        candidate["sha256"] = digest.lower().removeprefix("sha256:")
    try:
        return ProviderCodeArtifactReference.model_validate(candidate)
    except ValidationError as exc:
        raise ProviderCodeArtifactLedgerError(
            "code artifact reference fields are invalid"
        ) from exc


__all__ = [
    "MAX_PROVIDER_CODE_ARTIFACT_BYTES",
    "MAX_PROVIDER_CODE_ARTIFACT_CHARS",
    "MAX_PROVIDER_CODE_ARTIFACTS",
    "MAX_PROVIDER_CODE_LEDGER_CHARS",
    "ProviderCodeArtifactLedger",
    "ProviderCodeArtifactLedgerError",
    "ProviderCodeArtifactReference",
]
