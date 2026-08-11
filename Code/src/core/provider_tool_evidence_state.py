"""Bounded runtime owner for provider evidence coverage facts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from core.provider_tool_roundtrip_contracts import (
    MAX_PROVIDER_EVIDENCE_PATHS,
    MAX_PROVIDER_ROUND_TRIP_ROUNDS,
    MAX_PROVIDER_TOOL_ATTEMPTS,
    ProviderDeclaredReadWindow,
    ProviderPageReadCount,
    ProviderToolEvidenceCoverage,
)

ProviderEvidenceProjection = Literal[
    "inline",
    "bounded_preview",
    "bounded_window",
]


class ProviderToolEvidenceStateError(ValueError):
    """Raised when evidence observations are invalid or exceed static bounds."""


class ProviderToolEvidenceState:
    """Own bounded evidence observations for one provider round trip."""

    def __init__(
        self,
        *,
        project_path: str | None = None,
        page_read_cap: int = 3,
    ) -> None:
        if project_path is not None and (
            not isinstance(project_path, str) or not project_path.strip()
        ):
            raise ProviderToolEvidenceStateError(
                "project_path must be a non-empty string when provided"
            )
        if (
            type(page_read_cap) is not int
            or page_read_cap < 1
            or page_read_cap > 64
        ):
            raise ProviderToolEvidenceStateError(
                "page_read_cap must be a positive integer no greater than 64"
            )
        self._project_path = (
            str(Path(project_path).expanduser().resolve(strict=False))
            if project_path
            else None
        )
        self._page_read_cap = page_read_cap
        self._source_paths: set[str] = set()
        self._completed_read_paths: set[str] = set()
        self._projection_by_path: dict[str, ProviderEvidenceProjection] = {}
        self._declared_windows: dict[
            tuple[str, str, int, int],
            ProviderDeclaredReadWindow,
        ] = {}
        self._page_reads_by_path: dict[str, int] = {}
        self._evidence_keys: set[str] = set()
        self._duplicate_only_rounds: set[int] = set()
        self._finalization_rounds: set[int] = set()

    def record_completed_read(
        self,
        file_path: str,
        *,
        projection: ProviderEvidenceProjection = "inline",
    ) -> bool:
        if projection not in {
            "inline",
            "bounded_preview",
            "bounded_window",
        }:
            raise ProviderToolEvidenceStateError(
                "projection must be inline, bounded_preview, or bounded_window"
            )
        canonical = self._canonical_path(file_path)
        self._preflight_source_path(canonical)
        is_new = canonical not in self._completed_read_paths
        self._source_paths.add(canonical)
        self._completed_read_paths.add(canonical)
        self._projection_by_path[canonical] = projection
        return is_new

    def record_declared_window(
        self,
        window: ProviderDeclaredReadWindow,
    ) -> bool:
        if not isinstance(window, ProviderDeclaredReadWindow):
            raise ProviderToolEvidenceStateError(
                "window must be ProviderDeclaredReadWindow"
            )
        canonical = self._canonical_path(window.file_path)
        normalized = ProviderDeclaredReadWindow(
            file_path=canonical,
            read_mode=window.read_mode,
            offset=window.offset,
            max_lines=window.max_lines,
        )
        key = (
            normalized.file_path,
            normalized.read_mode,
            normalized.offset,
            normalized.max_lines,
        )
        if key in self._declared_windows:
            return False
        self._preflight_source_path(canonical)
        if len(self._declared_windows) >= MAX_PROVIDER_EVIDENCE_PATHS:
            raise ProviderToolEvidenceStateError(
                "declared read windows exceed the static evidence limit"
            )
        self._source_paths.add(canonical)
        self._completed_read_paths.add(canonical)
        self._projection_by_path[canonical] = "bounded_window"
        self._declared_windows[key] = normalized
        return True

    def record_page_read(self, file_path: str) -> int:
        canonical = self._canonical_path(file_path)
        self._preflight_source_path(canonical)
        current = self._page_reads_by_path.get(canonical, 0)
        if current >= self._page_read_cap:
            raise ProviderToolEvidenceStateError(
                "page-read count reached the configured cap"
            )
        updated = current + 1
        self._source_paths.add(canonical)
        self._page_reads_by_path[canonical] = updated
        return updated

    def record_evidence_key(self, evidence_key: str) -> bool:
        if not isinstance(evidence_key, str) or not evidence_key.strip():
            raise ProviderToolEvidenceStateError(
                "evidence_key must be a non-empty string"
            )
        normalized = evidence_key.strip()
        if len(normalized) > 512:
            raise ProviderToolEvidenceStateError(
                "evidence_key exceeds 512 characters"
            )
        if normalized in self._evidence_keys:
            return False
        if len(self._evidence_keys) >= MAX_PROVIDER_TOOL_ATTEMPTS:
            raise ProviderToolEvidenceStateError(
                "evidence keys exceed the static attempt limit"
            )
        self._evidence_keys.add(normalized)
        return True

    def record_duplicate_only_round(self, round_index: int) -> bool:
        return self._record_round(round_index, self._duplicate_only_rounds)

    def record_finalization_request(self, round_index: int) -> bool:
        return self._record_round(round_index, self._finalization_rounds)

    def coverage(self) -> ProviderToolEvidenceCoverage:
        page_counts = tuple(
            ProviderPageReadCount(file_path=path, count=count)
            for path, count in sorted(self._page_reads_by_path.items())
        )
        return ProviderToolEvidenceCoverage(
            completed_read_paths=tuple(sorted(self._completed_read_paths)),
            completed_declared_windows=tuple(
                self._declared_windows[key]
                for key in sorted(self._declared_windows)
            ),
            bounded_projection_paths=tuple(
                sorted(
                    path
                    for path, projection in self._projection_by_path.items()
                    if projection in {"bounded_preview", "bounded_window"}
                )
            ),
            page_reads_by_path=page_counts,
            page_cap_paths=tuple(
                sorted(
                    path
                    for path, count in self._page_reads_by_path.items()
                    if count >= self._page_read_cap
                )
            ),
            page_read_cap=self._page_read_cap,
            observed_evidence_keys=tuple(sorted(self._evidence_keys)),
            duplicate_only_rounds=len(self._duplicate_only_rounds),
            finalization_requests=len(self._finalization_rounds),
        )

    def _canonical_path(self, file_path: str) -> str:
        if not isinstance(file_path, str) or not file_path.strip():
            raise ProviderToolEvidenceStateError(
                "file_path must be a non-empty string"
            )
        path = Path(file_path.strip()).expanduser()
        if not path.is_absolute() and self._project_path:
            path = Path(self._project_path) / path
        resolved = path.resolve(strict=False)
        if self._project_path and not resolved.is_relative_to(
            Path(self._project_path)
        ):
            raise ProviderToolEvidenceStateError(
                "evidence path must remain inside the project root"
            )
        return str(resolved)

    def _preflight_source_path(self, canonical: str) -> None:
        if (
            canonical not in self._source_paths
            and len(self._source_paths) >= MAX_PROVIDER_EVIDENCE_PATHS
        ):
            raise ProviderToolEvidenceStateError(
                "source paths exceed the static evidence limit"
            )

    @staticmethod
    def _record_round(round_index: int, rounds: set[int]) -> bool:
        if (
            type(round_index) is not int
            or round_index < 1
            or round_index > MAX_PROVIDER_ROUND_TRIP_ROUNDS
        ):
            raise ProviderToolEvidenceStateError(
                "round_index must be a positive integer within the static limit"
            )
        if round_index in rounds:
            return False
        rounds.add(round_index)
        return True


__all__ = [
    "ProviderEvidenceProjection",
    "ProviderToolEvidenceState",
    "ProviderToolEvidenceStateError",
]
