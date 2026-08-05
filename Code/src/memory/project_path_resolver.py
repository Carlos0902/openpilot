"""Deterministic project-rooted path grounding for path-sensitive actions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from metadata import PathIntentMetadata, PathResolutionMetadata
from utils.path_boundary import (
    HALLUCINATED_PROJECT_ROOTS,
    PathBoundaryError,
    resolve_project_path,
    resolve_within_project,
)


ALLOWED_EXTERNAL_COMMAND_ROOTS = (
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/System",
    "/Library/Developer/CommandLineTools/usr/bin",
)

KNOWN_EXTERNAL_EXECUTABLE_NAMES = {
    "bash",
    "env",
    "node",
    "npm",
    "pip",
    "pip3",
    "pnpm",
    "py",
    "pytest",
    "python",
    "python3",
    "sh",
    "uv",
    "yarn",
    "zsh",
}


@dataclass(frozen=True)
class CommandPathReference:
    token_index: int
    start: int
    end: int
    token: str
    prefix: str
    raw_path: str
    suffix: str
    intent_kind: str
    operation: str


class ProjectPathResolver:
    """Ground model-proposed paths against project-local filesystem truth."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = resolve_project_path(project_root)
        self._indexed_files_cache: list[tuple[str, str]] | None = None
        self._sketch_file_cache: list[tuple[str, str]] | None = None
        self._sketch_directory_cache: list[tuple[str, str]] | None = None

    def resolve(self, intent: PathIntentMetadata) -> PathResolutionMetadata:
        project_root = self.project_root
        raw_text = str(intent.raw_path or "").strip()
        if not raw_text:
            if intent.intent_kind == "command_cwd":
                return self._build_resolution(
                    intent,
                    resolved_path=str(project_root),
                    status="resolved",
                    confidence=1.0,
                    correction_rule="project_root_default",
                    inside_project=True,
                    exists_verified=project_root.exists(),
                    parent_exists=project_root.parent.exists(),
                    reason="Empty command cwd defaulted to project root.",
                )
            return self._build_resolution(
                intent,
                status="blocked",
                confidence=0.0,
                correction_rule="empty_path",
                inside_project=False,
                exists_verified=False,
                parent_exists=False,
                reason="Path is empty and cannot be grounded.",
            )

        expect_directory = intent.intent_kind in {"existing_directory", "planned_new_directory", "command_cwd"}
        expect_file = intent.intent_kind in {
            "existing_file",
            "planned_new_file",
            "command_executable_path",
            "command_redirection_path",
        }
        require_exists = intent.intent_kind in {
            "existing_file",
            "existing_directory",
            "command_cwd",
            "command_executable_path",
        } or intent.operation in {
            "read",
            "search",
            "patch",
            "delete",
        }
        allow_planned = intent.intent_kind in {
            "planned_new_file",
            "planned_new_directory",
            "command_redirection_path",
        } or intent.operation == "write"
        alias_rewritten = _rewrite_hallucinated_root(raw_text, project_root)

        direct_path: Path | None = None
        direct_error = ""
        try:
            direct_path = resolve_within_project(alias_rewritten or raw_text, project_root)
        except PathBoundaryError as exc:
            direct_error = str(exc)

        if direct_path is not None:
            if self._matches_type(direct_path, expect_file=expect_file, expect_directory=expect_directory):
                if direct_path.exists():
                    return self._build_resolution(
                        intent,
                        resolved_path=str(direct_path),
                        status="corrected" if alias_rewritten else "resolved",
                        confidence=0.98 if alias_rewritten else 1.0,
                        correction_rule="hallucinated_root_alias" if alias_rewritten else "",
                        inside_project=True,
                        exists_verified=True,
                        parent_exists=direct_path.parent.exists(),
                        reason="Path resolved directly within project boundary.",
                    )
                if allow_planned and self._planned_path_allowed(direct_path):
                    return self._build_resolution(
                        intent,
                        resolved_path=str(direct_path),
                        status="planned",
                        confidence=0.9,
                        correction_rule="planned_within_project",
                        inside_project=True,
                        exists_verified=False,
                        parent_exists=direct_path.parent.exists(),
                        reason="Path is a valid in-project planned target.",
                    )

        candidates, used_file_index, used_sketch, correction_rule = self._correction_candidates(
            raw_text,
            expect_file=expect_file,
            expect_directory=expect_directory,
        )
        unique_candidates = list(dict.fromkeys(str(candidate.resolve(strict=False)) for candidate in candidates))
        if len(unique_candidates) == 1:
            candidate = Path(unique_candidates[0])
            if self._matches_type(candidate, expect_file=expect_file, expect_directory=expect_directory):
                return self._build_resolution(
                    intent,
                    resolved_path=str(candidate),
                    status="corrected",
                    confidence=0.9 if used_file_index else 0.82,
                    correction_rule=correction_rule,
                    candidate_paths=unique_candidates,
                    inside_project=True,
                    exists_verified=candidate.exists(),
                    parent_exists=candidate.parent.exists(),
                    used_sketch=used_sketch,
                    used_file_index=used_file_index,
                    reason="Path was corrected against indexed project structure.",
                )

        if len(unique_candidates) > 1:
            return self._build_resolution(
                intent,
                status="ambiguous",
                confidence=0.0,
                correction_rule=correction_rule or "multiple_project_candidates",
                candidate_paths=unique_candidates,
                inside_project=True,
                exists_verified=False,
                parent_exists=False,
                used_sketch=used_sketch,
                used_file_index=used_file_index,
                reason="Multiple in-project candidates matched the requested path; refusing to auto-correct.",
            )

        if direct_path is not None and allow_planned and self._matches_type(direct_path, expect_file=expect_file, expect_directory=expect_directory):
            return self._build_resolution(
                intent,
                resolved_path=str(direct_path),
                status="planned",
                confidence=0.8,
                correction_rule="planned_within_project",
                inside_project=True,
                exists_verified=False,
                parent_exists=direct_path.parent.exists(),
                reason="Path did not exist yet but remains a valid in-project planned target.",
            )

        reason = direct_error or f"Could not ground path within project root {project_root}."
        return self._build_resolution(
            intent,
            status="blocked",
            confidence=0.0,
            correction_rule="outside_project_boundary" if direct_error else "no_project_match",
            inside_project=False,
            exists_verified=False,
            parent_exists=False,
            used_sketch=used_sketch,
            used_file_index=used_file_index,
            reason=reason,
        )

    def resolve_many(self, intent_template: PathIntentMetadata, raw_paths: Iterable[str | Path]) -> list[PathResolutionMetadata]:
        resolutions: list[PathResolutionMetadata] = []
        for raw_path in raw_paths:
            resolutions.append(self.resolve(intent_template.model_copy(update={"raw_path": str(raw_path)})))
        return resolutions

    def _correction_candidates(
        self,
        raw_text: str,
        *,
        expect_file: bool,
        expect_directory: bool,
    ) -> tuple[list[Path], bool, bool, str]:
        raw_posix = raw_text.replace("\\", "/").rstrip("/")
        raw_name = Path(raw_text).name
        if expect_file:
            indexed = self._indexed_files()
            suffix_matches = [
                Path(abs_path)
                for relative, abs_path in indexed
                if raw_posix.endswith(relative) or raw_posix == relative
            ]
            if suffix_matches:
                return suffix_matches, True, False, "file_index_suffix_match"
            basename_matches = [Path(abs_path) for _relative, abs_path in indexed if Path(abs_path).name == raw_name]
            if basename_matches:
                return basename_matches, True, False, "file_index_basename_match"

            sketch_files = self._sketch_files()
            suffix_matches = [
                Path(abs_path)
                for relative, abs_path in sketch_files
                if raw_posix.endswith(relative) or raw_posix == relative
            ]
            if suffix_matches:
                return suffix_matches, False, True, "directory_sketch_suffix_match"
            basename_matches = [Path(abs_path) for _relative, abs_path in sketch_files if Path(abs_path).name == raw_name]
            if basename_matches:
                return basename_matches, False, True, "directory_sketch_basename_match"

        if expect_directory:
            sketch_directories = self._sketch_directories()
            suffix_matches = [
                Path(abs_path)
                for relative, abs_path in sketch_directories
                if relative and (raw_posix.endswith(relative) or raw_posix == relative)
            ]
            if suffix_matches:
                return suffix_matches, False, True, "directory_sketch_suffix_match"
            basename_matches = [Path(abs_path) for _relative, abs_path in sketch_directories if Path(abs_path).name == raw_name]
            if basename_matches:
                return basename_matches, False, True, "directory_sketch_basename_match"

        filesystem_candidates = self._filesystem_suffix_candidates(raw_text, expect_file=expect_file, expect_directory=expect_directory)
        if filesystem_candidates:
            return filesystem_candidates, False, False, "filesystem_suffix_match"
        return [], False, False, ""

    def _filesystem_suffix_candidates(
        self,
        raw_text: str,
        *,
        expect_file: bool,
        expect_directory: bool,
    ) -> list[Path]:
        cleaned = [part for part in Path(raw_text).parts if part not in {"/", "\\"}]
        candidates: list[Path] = []
        for index in range(len(cleaned)):
            candidate = (self.project_root / Path(*cleaned[index:])).resolve(strict=False)
            if not self._is_within_project(candidate):
                continue
            if not candidate.exists():
                continue
            if not self._matches_type(candidate, expect_file=expect_file, expect_directory=expect_directory):
                continue
            candidates.append(candidate)
        return list(dict.fromkeys(candidates))

    def _indexed_files(self) -> list[tuple[str, str]]:
        if self._indexed_files_cache is not None:
            return self._indexed_files_cache
        entries: list[tuple[str, str]] = []
        index_root = self.project_root / ".openpilot" / "file_indexes"
        if index_root.exists():
            for index_file in index_root.rglob("*.index.json"):
                try:
                    payload = json.loads(index_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                absolute = str(payload.get("file_path") or "")
                relative = str(payload.get("relative_path") or "")
                if absolute and relative:
                    entries.append((_normalize_relative(relative), str(Path(absolute).expanduser().resolve(strict=False))))
        self._indexed_files_cache = entries
        return entries

    def _sketch_files(self) -> list[tuple[str, str]]:
        self._load_sketch_cache()
        return self._sketch_file_cache or []

    def _sketch_directories(self) -> list[tuple[str, str]]:
        self._load_sketch_cache()
        return self._sketch_directory_cache or []

    def _load_sketch_cache(self) -> None:
        if self._sketch_file_cache is not None and self._sketch_directory_cache is not None:
            return
        file_entries: list[tuple[str, str]] = []
        directory_entries: list[tuple[str, str]] = []
        for sketch_path in self.project_root.rglob("sketch.json"):
            if ".openpilot" in sketch_path.parts:
                continue
            try:
                payload = json.loads(sketch_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            directory = str(payload.get("directory") or "")
            if directory:
                directory_path = Path(directory).expanduser().resolve(strict=False)
                if self._is_within_project(directory_path):
                    directory_entries.append((_relative_to_project(directory_path, self.project_root), str(directory_path)))
            files = payload.get("files") or {}
            if isinstance(files, dict):
                for item in files.values():
                    if not isinstance(item, dict):
                        continue
                    file_path = str(item.get("path") or "")
                    if not file_path:
                        continue
                    absolute = Path(file_path).expanduser().resolve(strict=False)
                    if not self._is_within_project(absolute):
                        continue
                    file_entries.append((_relative_to_project(absolute, self.project_root), str(absolute)))
        self._sketch_file_cache = file_entries
        self._sketch_directory_cache = directory_entries

    def _planned_path_allowed(self, path: Path) -> bool:
        return self._is_within_project(path) and path != self.project_root

    def _is_within_project(self, path: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(self.project_root)
            return True
        except ValueError:
            return False

    def _matches_type(self, path: Path, *, expect_file: bool, expect_directory: bool) -> bool:
        if expect_file:
            return not path.exists() or path.is_file()
        if expect_directory:
            return not path.exists() or path.is_dir()
        return True

    def _build_resolution(
        self,
        intent: PathIntentMetadata,
        *,
        resolved_path: str = "",
        status: str,
        confidence: float,
        correction_rule: str,
        inside_project: bool,
        exists_verified: bool,
        parent_exists: bool,
        used_sketch: bool = False,
        used_file_index: bool = False,
        candidate_paths: list[str] | None = None,
        reason: str,
    ) -> PathResolutionMetadata:
        return PathResolutionMetadata(
            project_root=str(self.project_root),
            raw_path=intent.raw_path,
            resolved_path=resolved_path,
            status=status,
            confidence=confidence,
            correction_rule=correction_rule,
            candidate_paths=candidate_paths or [],
            intent_kind=intent.intent_kind,
            operation=intent.operation,
            path_source=intent.path_source,
            inside_project=inside_project,
            exists_verified=exists_verified,
            parent_exists=parent_exists,
            used_sketch=used_sketch,
            used_file_index=used_file_index,
            reason=reason,
        )


def resolve_path_within_project(
    raw_path: str | Path,
    project_root: str | Path,
    *,
    operation: str,
    intent_kind: str,
    source: str = "tool_input",
    evidence: list[str] | None = None,
    candidate_paths: list[str] | None = None,
) -> tuple[PathIntentMetadata, PathResolutionMetadata]:
    resolver = ProjectPathResolver(project_root)
    intent = PathIntentMetadata(
        project_root=str(resolve_project_path(project_root)),
        raw_path=str(raw_path),
        intent_kind=intent_kind,
        operation=operation,
        path_source=source,
        evidence=evidence or [],
        candidate_paths=list(candidate_paths or []),
    )
    return intent, resolver.resolve(intent)


def ensure_resolved_path(
    raw_path: str | Path,
    project_root: str | Path,
    *,
    operation: str,
    intent_kind: str,
    source: str = "tool_input",
    evidence: list[str] | None = None,
) -> Path:
    _intent, resolution = resolve_path_within_project(
        raw_path,
        project_root,
        operation=operation,
        intent_kind=intent_kind,
        source=source,
        evidence=evidence,
    )
    if resolution.status in {"blocked", "ambiguous"}:
        raise ValueError(resolution.reason)
    return Path(resolution.resolved_path).expanduser().resolve(strict=False)


def ground_command_paths_within_project(
    command: str,
    project_root: str | Path,
    *,
    source: str = "command_input",
    evidence: list[str] | None = None,
) -> tuple[str, list[PathIntentMetadata], list[PathResolutionMetadata]]:
    """Ground absolute command-path fragments against the declared project root.

    Only absolute path-shaped fragments are inspected. Relative paths keep their
    original semantics. Known system executable roots are allowed only for the
    first command token so local project path governance does not block normal
    interpreter binaries such as /usr/bin/env.
    """

    command = str(command or "")
    if not command.strip():
        return command, [], []

    root = resolve_project_path(project_root)
    resolver = ProjectPathResolver(root)
    intents: list[PathIntentMetadata] = []
    resolutions: list[PathResolutionMetadata] = []
    rewrites: list[tuple[int, int, str]] = []

    for reference in extract_command_path_references(command):
        intent = PathIntentMetadata(
            project_root=str(root),
            raw_path=reference.raw_path,
            intent_kind=reference.intent_kind,
            operation=reference.operation,
            path_source=source,
            evidence=list(evidence or []),
            attributes={"command_token": reference.token, "token_index": reference.token_index},
        )
        if (
            reference.intent_kind == "command_executable_path"
            and _is_allowed_external_command_executable(reference.raw_path, root)
            and reference.prefix in {"", "'", '"'}
        ):
            resolution = resolver._build_resolution(
                intent,
                resolved_path=reference.raw_path,
                status="external_allowed",
                confidence=1.0,
                correction_rule="allowed_external_command_executable",
                inside_project=False,
                exists_verified=Path(reference.raw_path).expanduser().exists(),
                parent_exists=Path(reference.raw_path).expanduser().parent.exists(),
                reason="External executable path is allowed outside the project boundary.",
            )
        else:
            resolution = resolver.resolve(intent)
        intents.append(intent)
        resolutions.append(resolution)
        if resolution.status in {"blocked", "ambiguous"}:
            continue
        rewritten = f"{reference.prefix}{resolution.resolved_path}{reference.suffix}"
        if rewritten != reference.token:
            rewrites.append((reference.start, reference.end, rewritten))

    if not rewrites:
        return command, intents, resolutions

    parts: list[str] = []
    cursor = 0
    for start, end, replacement in sorted(rewrites, key=lambda item: item[0]):
        parts.append(command[cursor:start])
        parts.append(replacement)
        cursor = end
    parts.append(command[cursor:])
    return "".join(parts), intents, resolutions


def _rewrite_hallucinated_root(path_text: str, project_root: Path) -> str | None:
    normalized = path_text.rstrip("/") or path_text
    for raw_root in HALLUCINATED_PROJECT_ROOTS:
        alias = raw_root.rstrip("/")
        if normalized == alias:
            return str(project_root)
        if normalized.startswith(alias + "/"):
            return str(project_root / normalized[len(alias) + 1 :])
    return None


def _relative_to_project(path: Path, project_root: Path) -> str:
    try:
        return _normalize_relative(path.relative_to(project_root).as_posix())
    except ValueError:
        return ""


def _normalize_relative(path_text: str) -> str:
    return str(path_text).replace("\\", "/").lstrip("./")


def _shell_token_spans(command: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    index = 0
    length = len(command)
    while index < length:
        while index < length and command[index].isspace():
            index += 1
        if index >= length:
            break
        start = index
        quote = ""
        while index < length:
            char = command[index]
            if quote:
                if char == "\\" and index + 1 < length:
                    index += 2
                    continue
                if char == quote:
                    quote = ""
                index += 1
                continue
            if char in {"'", '"'}:
                quote = char
                index += 1
                continue
            if char.isspace():
                break
            index += 1
        spans.append((start, index, command[start:index]))
    return spans


def extract_command_path_references(command: str) -> list[CommandPathReference]:
    references: list[CommandPathReference] = []
    spans = _shell_token_spans(command)
    tokens = [token for _start, _end, token in spans]
    executable_indexes = _command_executable_token_indexes(tokens)

    for token_index, (start, end, token) in enumerate(spans):
        fragment = _extract_command_path_fragment(token)
        if fragment is None:
            continue
        prefix, raw_path, suffix = fragment
        previous_token = tokens[token_index - 1] if token_index > 0 else ""
        intent_kind = _classify_command_path_intent(
            token_index,
            token,
            prefix,
            previous_token,
            executable_indexes,
        )
        operation = _operation_for_command_path_intent(intent_kind, prefix, previous_token)
        references.append(
            CommandPathReference(
                token_index=token_index,
                start=start,
                end=end,
                token=token,
                prefix=prefix,
                raw_path=raw_path,
                suffix=suffix,
                intent_kind=intent_kind,
                operation=operation,
            )
        )
    return references


def _extract_command_path_fragment(token: str) -> tuple[str, str, str] | None:
    for offset in _candidate_path_offsets(token):
        fragment = _extract_command_path_fragment_at(token, offset)
        if fragment is not None:
            return fragment
    return None


def _command_executable_token_indexes(tokens: list[str]) -> set[int]:
    if not tokens:
        return set()
    indexes = {0}
    first_name = _token_basename(tokens[0])
    if first_name == "env":
        env_command_index = _env_command_token_index(tokens)
        if env_command_index is not None:
            indexes.add(env_command_index)
    return indexes


def _env_command_token_index(tokens: list[str]) -> int | None:
    for index in range(1, len(tokens)):
        token = _strip_wrapping_quotes(tokens[index]).strip()
        if not token or token == "--":
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            continue
        if token.startswith("-"):
            continue
        return index
    return None


def _strip_wrapping_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def _token_basename(token: str) -> str:
    cleaned = _strip_wrapping_quotes(token).strip()
    if not cleaned:
        return ""
    return Path(cleaned).name.lower()


def _classify_command_path_intent(
    token_index: int,
    token: str,
    prefix: str,
    previous_token: str,
    executable_indexes: set[int],
) -> str:
    if token_index in executable_indexes:
        return "command_executable_path"
    if _looks_like_command_directory_slot(token, prefix):
        return "command_cwd"
    if _is_redirection_prefix(prefix) or _is_redirection_token(previous_token):
        return "command_redirection_path"
    return "command_data_path"


def _operation_for_command_path_intent(intent_kind: str, prefix: str, previous_token: str) -> str:
    if intent_kind == "command_redirection_path":
        marker = prefix or previous_token
        if ">" in marker:
            return "write"
        return "read"
    return "execute"


def _is_redirection_prefix(prefix: str) -> bool:
    return bool(prefix) and re.fullmatch(r"\d*(?:>>?|<<?)", prefix) is not None


def _is_redirection_token(token: str) -> bool:
    return re.fullmatch(r"\d*(?:>>?|<<?)", str(token or "").strip()) is not None


def _candidate_path_offsets(token: str) -> list[int]:
    offsets = [0]
    if "=" in token:
        offsets.append(token.index("=") + 1)
    redirect = re.match(r"\d*(?:>>?|<<?)", token)
    if redirect and 0 < redirect.end() < len(token):
        offsets.append(redirect.end())
    unique: list[int] = []
    for offset in offsets:
        if offset not in unique:
            unique.append(offset)
    return unique


def _extract_command_path_fragment_at(token: str, offset: int) -> tuple[str, str, str] | None:
    if offset >= len(token):
        return None
    prefix = token[:offset]
    start = offset
    if token[start] in {"'", '"'}:
        prefix = token[: start + 1]
        start += 1
    if start >= len(token) or token[start] != "/":
        return None

    end = start
    while end < len(token) and token[end] not in {"'", '"'}:
        end += 1
    fragment = token[start:end]
    path_text, suffix = _split_command_path_suffix(fragment)
    if not path_text:
        return None
    suffix = f"{suffix}{token[end:]}"
    return prefix, path_text, suffix


def _split_command_path_suffix(fragment: str) -> tuple[str, str]:
    path_text = fragment
    suffix = ""
    if "::" in path_text:
        path_text, pytest_suffix = path_text.split("::", 1)
        suffix = f"::{pytest_suffix}"
    stripped = path_text.rstrip(",)]}")
    if stripped != path_text:
        suffix = f"{path_text[len(stripped):]}{suffix}"
        path_text = stripped
    return path_text, suffix


def _is_allowed_external_command_executable(raw_path: str, project_root: Path) -> bool:
    try:
        candidate = Path(raw_path).expanduser().resolve(strict=False)
    except OSError:
        return False
    if not candidate.exists() or not candidate.is_file():
        return False
    try:
        candidate.relative_to(project_root)
        return False
    except ValueError:
        pass
    for allowed_root in ALLOWED_EXTERNAL_COMMAND_ROOTS:
        root = Path(allowed_root).expanduser().resolve(strict=False)
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            continue
    candidate_name = candidate.name.lower()
    parent_name = candidate.parent.name.lower()
    if parent_name in {"bin", "scripts", "shims"} and (
        candidate_name in KNOWN_EXTERNAL_EXECUTABLE_NAMES or re.fullmatch(r"python\d+(?:\.\d+)*", candidate_name)
    ):
        return True
    return False


def _looks_like_command_directory_slot(token: str, prefix: str) -> bool:
    lowered = token.lower()
    if prefix.startswith((">", ">>", "<", "<<")):
        return False
    directory_markers = (
        "--cwd=",
        "--rootdir=",
        "--root=",
        "--project=",
        "--project-dir=",
        "--directory=",
        "--dir=",
    )
    return any(lowered.startswith(marker) for marker in directory_markers) or prefix in {
        "--cwd=",
        "--rootdir=",
        "--root=",
        "--project=",
        "--project-dir=",
        "--directory=",
        "--dir=",
    }
