"""Explicit provider-native tool round-trip orchestration."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.tool_roundtrip import append_tool_round_trip
from core.exceptions import ContextAssemblyBudgetError
from core.llm import LLMMessage, LLMResponse, LLMToolDefinition, LLMToolFunction, LLMToolResult
from core.provider_tool_admission import ProviderToolAdmission, admit_provider_tool_calls
from core.tool_contracts import ToolCapability
from core.tool_event_loop import ToolEventLoopRunResult, ToolEventLoopRunner
from core.validation_command import validation_commands_match
from memory.context_assembly import build_context_candidate_request, build_context_llm_request
from metadata import (
    ContextCandidate,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTruncation,
    ContextCandidateTrust,
    ContextRequestPurpose,
    FileReadWindowSpec,
    ReasoningDecisionComplexity,
    ReasoningMode,
    ReasoningPolicy,
    ProviderBudgetDiagnostic,
    RuntimeBudgetMetadata,
    ToolEventCompletionOutcome,
    UnsupportedReasoningBehavior,
    metadata_summary,
)
from core.reasoning import resolve_reasoning_policy
from tools.mutation_descriptor import FILE_MUTATION_TOOLS


_PROJECTION_MARKERS = (
    "...[source excerpt clipped;",
    "...[symbol index clipped]",
    "...[truncated]",
)
_STRUCTURED_PARSE_CHAR_LIMIT = 200_000
_MAX_STRUCTURED_CANDIDATES = 128
_MAX_STRUCTURED_CANDIDATE_CHARS = 128
_MAX_STRUCTURED_CALLABLE_CANDIDATES = 64
_MAX_STRUCTURED_CALLABLE_CHARS = 256
_MAX_STRUCTURED_CALLSITE_CANDIDATES = 32
_MAX_STRUCTURED_CALLSITE_CHARS = 256
_MAX_STRUCTURED_CALLSITE_DEPTH = 3


def _contains_projection_marker(source: str) -> bool:
    return any(marker in str(source or "") for marker in _PROJECTION_MARKERS)


def _import_details(nodes: Sequence[ast.AST]) -> tuple[set[str], set[str]]:
    """Return module paths and bound names from already parsed top-level nodes."""

    modules: set[str] = set()
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            module = "." * int(node.level or 0) + str(node.module or "")
            if module:
                modules.add(module)
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return modules, names


def _complete_parenthesized_import_nodes(source: str) -> list[ast.AST]:
    """Parse only complete top-level parenthesized imports from a partial window.

    A bounded read may end in the middle of a class or function, making the
    complete source unparsable.  Import blocks are recovered independently,
    but only after their parentheses close and the isolated block parses.  An
    incomplete or ambiguous block therefore contributes no authority.
    """

    lines = str(source or "").splitlines()
    nodes: list[ast.AST] = []
    for index, line in enumerate(lines):
        if line[:1].isspace():
            continue
        if not re.match(r"^from\s+[.A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s+import\s*\(", line):
            continue
        block = [line]
        depth = line.count("(") - line.count(")")
        cursor = index
        while depth > 0 and cursor + 1 < len(lines):
            cursor += 1
            next_line = lines[cursor]
            block.append(next_line)
            depth += next_line.count("(") - next_line.count(")")
        if depth != 0:
            continue
        try:
            tree = ast.parse("\n".join(block))
        except (MemoryError, RecursionError, SyntaxError, ValueError):
            continue
        nodes.extend(
            node
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
        )
    return nodes


def _fallback_source_nodes(source: str) -> tuple[list[ast.AST], set[str]]:
    """Parse safe top-level fragments from a syntactically incomplete window."""

    nodes = _complete_parenthesized_import_nodes(source)
    names: set[str] = set()
    for line in str(source or "").splitlines():
        if line[:1].isspace():
            continue
        match = re.match(r"(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", line)
        if match:
            names.add(match.group(1))
            continue
        match = re.match(r"class\s+([A-Za-z_]\w*)\b", line)
        if match:
            names.add(match.group(1))
            continue
        try:
            line_tree = ast.parse(line)
        except (MemoryError, RecursionError, SyntaxError, ValueError):
            continue
        nodes.extend(line_tree.body)
    return nodes, names


def _structured_source_candidates(source: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Derive bounded module paths and bindings from authoritative raw source."""

    # Synthetic display markers are never authoritative source.  Callers may
    # still show them as CONTENT, but they must not reach AST or fallback parse.
    if _contains_projection_marker(source):
        return (), ()
    source_text = str(source or "")[:_STRUCTURED_PARSE_CHAR_LIMIT]
    try:
        tree = ast.parse(source_text)
    except (MemoryError, RecursionError, SyntaxError, ValueError):
        nodes, names = _fallback_source_nodes(source_text)
    else:
        nodes = list(tree.body)
        names = set()
    modules, imported_names = _import_details(nodes)
    names.update(imported_names)
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    def bounded(values: set[str]) -> tuple[str, ...]:
        return tuple(
            sorted(
                value
                for value in values
                if len(value) <= _MAX_STRUCTURED_CANDIDATE_CHARS
            )
        )[:_MAX_STRUCTURED_CANDIDATES]

    return bounded(modules), bounded(names)


def _module_symbol_candidates(source: str) -> tuple[str, ...]:
    """Derive bounded, source-linked module bindings for generator grounding."""

    return _structured_source_candidates(source)[1]


def _callable_candidate_from_node(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    """Render only a bounded callable signature from a parsed function header."""

    name = str(node.name or "").strip()
    if not name:
        return None
    try:
        arguments = ast.unparse(node.args)
    except (MemoryError, RecursionError, ValueError):
        return None
    arguments = re.sub(r"^self\s*,\s*", "", arguments)
    candidate = f"{name}({arguments})"
    return candidate if len(candidate) <= _MAX_STRUCTURED_CALLABLE_CHARS else None


def _class_constructor_candidate(node: ast.ClassDef) -> str | None:
    """Render a class constructor signature when an explicit ``__init__`` exists."""

    constructor = next(
        (
            child
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == "__init__"
        ),
        None,
    )
    if constructor is None:
        return None
    try:
        arguments = ast.unparse(constructor.args)
    except (MemoryError, RecursionError, ValueError):
        return None
    arguments = re.sub(r"^self\s*,\s*", "", arguments)
    candidate = f"{node.name}({arguments})"
    return candidate if len(candidate) <= _MAX_STRUCTURED_CALLABLE_CHARS else None


def _bounded_class_constructor_candidate(node: ast.ClassDef) -> str | None:
    """Keep required constructor inputs when the full signature is too large.

    The projection is a construction hint, not a second API contract.  Keep
    positional inputs, required keyword-only inputs, and the common bounded
    ``max_rounds`` default; omit annotations and unrelated optional controls
    when the complete signature would exceed the prompt candidate limit.
    """

    constructor = next(
        (
            child
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == "__init__"
        ),
        None,
    )
    if constructor is None:
        return None
    arguments = constructor.args
    positional = [argument.arg for argument in (*arguments.posonlyargs, *arguments.args)]
    if positional and positional[0] == "self":
        positional = positional[1:]
    required_keyword_only = [
        argument.arg
        for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults)
        if default is None
    ]
    optional_hints: list[str] = []
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        if argument.arg != "max_rounds" or default is None:
            continue
        try:
            rendered_default = ast.unparse(default)
        except (MemoryError, RecursionError, ValueError):
            continue
        optional_hints.append(f"max_rounds={rendered_default}")
        break
    parts = [*positional]
    if required_keyword_only or optional_hints:
        parts.append("*")
        parts.extend(required_keyword_only)
        parts.extend(optional_hints)
    candidate = f"{node.name}({', '.join(parts)})"
    return candidate if len(candidate) <= _MAX_STRUCTURED_CALLABLE_CHARS else None


def _parse_callable_header(header: str) -> ast.AST | None:
    """Parse one complete header by replacing its body with ``pass``."""

    text = str(header or "").strip()
    if not text or not text.rstrip().endswith(":"):
        return None
    try:
        tree = ast.parse(text + "\n    pass")
    except (MemoryError, RecursionError, SyntaxError, ValueError):
        return None
    return tree.body[0] if tree.body else None


def _fallback_callable_nodes(source: str) -> list[ast.AST]:
    """Recover complete top-level headers from a syntactically partial window."""

    lines = str(source or "").splitlines()
    nodes: list[ast.AST] = []
    current_class: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        class_match = re.match(r"^class\s+([A-Za-z_]\w*)\b.*:", stripped)
        if indent == 0 and class_match:
            current_class = class_match.group(1)
            node = _parse_callable_header(stripped[: stripped.rfind(":") + 1])
            if node is not None:
                nodes.append(node)
            index += 1
            continue
        function_match = re.match(r"^(?:async\s+)?def\s+[A-Za-z_]\w*\s*\(", stripped)
        if function_match and (indent == 0 or (current_class and stripped.startswith("def __init__"))):
            header = [stripped]
            depth = stripped.count("(") - stripped.count(")")
            cursor = index
            while depth > 0 and cursor + 1 < len(lines):
                cursor += 1
                next_line = lines[cursor].strip()
                header.append(next_line)
                depth += next_line.count("(") - next_line.count(")")
            if depth == 0:
                node = _parse_callable_header(" ".join(header))
                if node is not None:
                    if indent == 0:
                        nodes.append(node)
                    elif current_class and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
                        nodes.append((current_class, node))
            index = max(index + 1, cursor + 1)
            continue
        if indent == 0 and stripped and not stripped.startswith("#"):
            current_class = None
        index += 1
    return nodes


def _module_callable_candidates(source: str) -> tuple[str, ...]:
    """Derive bounded callable construction signatures from raw source only."""

    if _contains_projection_marker(source):
        return ()
    source_text = str(source or "")[:_STRUCTURED_PARSE_CHAR_LIMIT]
    try:
        nodes: list[ast.AST | tuple[str, ast.AST]] = list(ast.parse(source_text).body)
    except (MemoryError, RecursionError, SyntaxError, ValueError):
        nodes = _fallback_callable_nodes(source_text)
    candidates: set[str] = set()
    for node in nodes:
        if isinstance(node, tuple):
            class_name, constructor = node
            if isinstance(constructor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                try:
                    arguments = re.sub(r"^self\s*,\s*", "", ast.unparse(constructor.args))
                except (MemoryError, RecursionError, ValueError):
                    continue
                candidate = f"{class_name}({arguments})"
                if len(candidate) <= _MAX_STRUCTURED_CALLABLE_CHARS:
                    candidates.add(candidate)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidate = _callable_candidate_from_node(node)
            if candidate is not None:
                candidates.add(candidate)
        elif isinstance(node, ast.ClassDef):
            candidate = _class_constructor_candidate(node)
            if candidate is None:
                candidate = _bounded_class_constructor_candidate(node)
            if candidate is not None:
                candidates.add(candidate)
    return tuple(sorted(candidates))[:_MAX_STRUCTURED_CALLABLE_CANDIDATES]


def _callsite_bound_names(tree: ast.AST) -> set[str]:
    """Collect names that are explicitly bound in one raw source window."""

    bound: set[str] = set()
    nodes = list(getattr(tree, "body", ()) or ())
    _, imported_names = _import_details(nodes)
    bound.update(imported_names)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            arguments = node.args if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
            if arguments is not None:
                bound.update(argument.arg for argument in arguments.posonlyargs)
                bound.update(argument.arg for argument in arguments.args)
                bound.update(argument.arg for argument in arguments.kwonlyargs)
                if arguments.vararg is not None:
                    bound.add(arguments.vararg.arg)
                if arguments.kwarg is not None:
                    bound.add(arguments.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    return bound


def _safe_callsite_expression(
    node: ast.AST,
    bound_names: set[str],
    *,
    depth: int = 0,
) -> str | None:
    """Render a small source-visible call expression without evaluating it."""

    if depth > _MAX_STRUCTURED_CALLSITE_DEPTH:
        return None
    if isinstance(node, ast.Name):
        return node.id if node.id in bound_names else None
    if isinstance(node, ast.Constant):
        if node.value is None or isinstance(node.value, (bool, int, float, str)):
            rendered = repr(node.value)
            return rendered if len(rendered) <= 80 else None
        return None
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return None
    if node.func.id not in bound_names:
        return None
    rendered_args: list[str] = []
    for argument in node.args:
        if isinstance(argument, ast.Starred):
            return None
        rendered = _safe_callsite_expression(argument, bound_names, depth=depth + 1)
        if rendered is None:
            return None
        rendered_args.append(rendered)
    for keyword in node.keywords:
        if keyword.arg is None:
            return None
        rendered = _safe_callsite_expression(keyword.value, bound_names, depth=depth + 1)
        if rendered is None:
            return None
        rendered_args.append(f"{keyword.arg}={rendered}")
    result = f"{node.func.id}({', '.join(rendered_args)})"
    return result if len(result) <= _MAX_STRUCTURED_CALLSITE_CHARS else None


def _callsite_source_prefix(source: str) -> str:
    """Keep only raw source before the first synthetic clipping marker."""

    text = str(source or "")[:_STRUCTURED_PARSE_CHAR_LIMIT]
    positions = [text.find(marker) for marker in _PROJECTION_MARKERS if text.find(marker) >= 0]
    return text[: min(positions)] if positions else text


def _partial_callsite_bound_names(source: str) -> set[str]:
    """Collect conservative bindings without parsing the whole partial window."""

    text = _callsite_source_prefix(source)
    bound = set(_module_symbol_candidates(text))
    for line in text.splitlines():
        stripped = line.strip()
        definition = re.match(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\((.*)", stripped)
        if definition:
            bound.add(definition.group(1))
            header = stripped[: stripped.rfind(")") + 1] if ")" in stripped else stripped
            try:
                parsed = ast.parse(header + ":\n    pass")
            except (MemoryError, RecursionError, SyntaxError, ValueError):
                parsed = None
            if parsed and isinstance(parsed.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = parsed.body[0].args
                bound.update(argument.arg for argument in arguments.posonlyargs)
                bound.update(argument.arg for argument in arguments.args)
                bound.update(argument.arg for argument in arguments.kwonlyargs)
        class_match = re.match(r"^class\s+([A-Za-z_]\w*)\b", stripped)
        if class_match:
            bound.add(class_match.group(1))
        assignment = re.match(r"^([A-Za-z_]\w*)\s*=", stripped)
        if assignment:
            bound.add(assignment.group(1))
    return bound


def _delimiter_balance(text: str) -> int:
    """Return a conservative bracket balance for one candidate fragment."""

    return sum(text.count(opening) - text.count(closing) for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")))


def _partial_callsite_candidates(source: str) -> tuple[str, ...]:
    """Recover complete isolated calls from a syntactically partial window."""

    text = _callsite_source_prefix(source)
    lines = text.splitlines()
    bound_names = _partial_callsite_bound_names(text)
    candidates: set[str] = set()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        assignment_match = re.match(r"^([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*\(", stripped)
        expression_match = re.match(r"^([A-Za-z_]\w*)\s*\(", stripped)
        if assignment_match:
            target = assignment_match.group(1)
            base_indent = len(line) - len(line.lstrip())
        elif expression_match and not stripped.startswith(("def ", "class ", "return ")):
            target = None
            base_indent = len(line) - len(line.lstrip())
        else:
            index += 1
            continue
        fragment_lines = [stripped]
        balance = _delimiter_balance(stripped)
        cursor = index
        complete = True
        while balance > 0:
            cursor += 1
            if cursor >= len(lines):
                complete = False
                break
            continuation = lines[cursor]
            continuation_stripped = continuation.strip()
            if not continuation_stripped:
                fragment_lines.append(continuation_stripped)
                continue
            continuation_indent = len(continuation) - len(continuation.lstrip())
            if continuation_indent <= base_indent and not continuation_stripped.startswith((")", "]", "}")):
                complete = False
                break
            fragment_lines.append(continuation_stripped)
            balance = _delimiter_balance(" ".join(fragment_lines))
        if complete and balance == 0:
            fragment = " ".join(fragment_lines)
            try:
                parsed = ast.parse(fragment)
            except (MemoryError, RecursionError, SyntaxError, ValueError):
                parsed = None
            if parsed and parsed.body:
                node = parsed.body[0]
                value: ast.AST | None = None
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    target = node.targets[0].id
                    value = node.value
                elif isinstance(node, ast.Expr):
                    value = node.value
                expression = _safe_callsite_expression(value, bound_names) if value is not None else None
                if expression is not None:
                    candidate = f"{target} = {expression}" if target else expression
                    if len(candidate) <= _MAX_STRUCTURED_CALLSITE_CHARS:
                        candidates.add(candidate)
        index = max(index + 1, cursor + 1 if complete else index + 1)
    return tuple(sorted(candidates))[:_MAX_STRUCTURED_CALLSITE_CANDIDATES]


def _module_callsite_candidates(source: str) -> tuple[str, ...]:
    """Derive bounded helper-composition hints from one raw source window."""

    source_text = str(source or "")[:_STRUCTURED_PARSE_CHAR_LIMIT]
    if _contains_projection_marker(source_text):
        return _partial_callsite_candidates(source_text)
    try:
        tree = ast.parse(source_text)
    except (MemoryError, RecursionError, SyntaxError, ValueError):
        return _partial_callsite_candidates(source_text)
    bound_names = _callsite_bound_names(tree)
    candidates: set[str] = set()
    for node in ast.walk(tree):
        target: str | None = None
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            assignment_target = node.targets[0]
            if isinstance(assignment_target, ast.Name):
                target = assignment_target.id
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
            value = node.value
        elif isinstance(node, ast.Expr):
            value = node.value
        if value is None:
            continue
        expression = _safe_callsite_expression(value, bound_names)
        if expression is None:
            continue
        candidate = f"{target} = {expression}" if target else expression
        if len(candidate) <= _MAX_STRUCTURED_CALLSITE_CHARS:
            candidates.add(candidate)
    return tuple(sorted(candidates))[:_MAX_STRUCTURED_CALLSITE_CANDIDATES]


def _module_import_candidates(source: str) -> tuple[str, ...]:
    """Derive source import paths without trusting display or prompt text."""

    return _structured_source_candidates(source)[0]


class ProviderToolRoundTripError(ValueError):
    """Raised when a provider-native task cannot preserve round-trip state."""


@dataclass(frozen=True)
class ProviderToolAttempt:
    """Bounded evidence for one normalized provider tool attempt."""

    signature: str
    tool_name: str
    provider_call_id: str
    round_index: int
    success: bool
    error_type: str | None = None
    duplicate_of: str | None = None


@dataclass(frozen=True)
class ProviderToolEvidenceCoverage:
    """Bounded runtime fact about evidence collected by a provider task."""

    completed_read_paths: tuple[str, ...] = ()
    completed_declared_windows: tuple[tuple[str, str, int, int], ...] = ()
    bounded_projection_paths: tuple[str, ...] = ()
    page_reads_by_path: tuple[tuple[str, int], ...] = ()
    page_cap_paths: tuple[str, ...] = ()
    page_read_cap: int = 0
    observed_evidence_keys: tuple[str, ...] = ()
    duplicate_only_rounds: int = 0
    finalization_requests: int = 0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "completed_read_paths": list(self.completed_read_paths),
            "completed_declared_windows": [
                {
                    "file_path": file_path,
                    "read_mode": read_mode,
                    "offset": offset,
                    "max_lines": max_lines,
                }
                for file_path, read_mode, offset, max_lines in self.completed_declared_windows
            ],
            "bounded_projection_paths": list(self.bounded_projection_paths),
            "page_reads_by_path": {
                path: count for path, count in self.page_reads_by_path
            },
            "page_cap_paths": list(self.page_cap_paths),
            "page_read_cap": self.page_read_cap,
            "observed_evidence_keys": list(self.observed_evidence_keys),
            "duplicate_only_rounds": self.duplicate_only_rounds,
            "finalization_requests": self.finalization_requests,
        }


@dataclass(frozen=True)
class ProviderToolRoundTripResult:
    """Bounded provider conversation and typed execution evidence."""

    success: bool
    final_response: LLMResponse | None
    messages: list[LLMMessage]
    tool_loop_results: list[ToolEventLoopRunResult]
    rounds_used: int
    error_message: str | None = None
    attempts: list[ProviderToolAttempt] = field(default_factory=list)
    evidence_coverage: ProviderToolEvidenceCoverage = field(
        default_factory=ProviderToolEvidenceCoverage
    )
    request_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    budget_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    handoff_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    outcome_feedback_enabled: bool = False
    reasoning_complexity: ReasoningDecisionComplexity = ReasoningDecisionComplexity.STANDARD
    reasoning_mode: ReasoningMode | None = None


def build_provider_tool_definitions(
    registry: Any,
    tool_names: Sequence[str],
) -> list[LLMToolDefinition]:
    """Build narrow provider schemas from registered typed contracts.

    Only required fields, alternative required fields, and declared defaults
    are exposed. This avoids sending the entire ``ToolInputMetadata`` schema to
    the provider and keeps optional internal/runtime fields out of the prompt.
    """

    definitions: list[LLMToolDefinition] = []
    for raw_name in tool_names:
        tool_name = str(raw_name or "").strip()
        if not tool_name:
            raise ProviderToolRoundTripError("provider tool names must be non-empty")
        definition = getattr(registry, "get", lambda _name: None)(tool_name)
        if definition is None:
            raise ProviderToolRoundTripError(f"cannot expose unknown provider tool: {tool_name}")
        contract = getattr(definition, "contract_metadata", None)
        if contract is None:
            raise ProviderToolRoundTripError(f"tool has no typed contract: {tool_name}")
        required_fields = list(getattr(contract, "required_input_fields", []) or [])
        any_of = [list(group) for group in (getattr(contract, "required_any_of", []) or [])]
        defaults = dict(getattr(contract, "input_defaults", {}) or {})
        conditional_requirements = [
            requirement
            for requirement in (getattr(contract, "conditional_requirements", []) or [])
            if isinstance(requirement, dict)
        ]
        field_names: list[str] = []
        conditional_fields = [
            field
            for requirement in conditional_requirements
            for field in [
                *(requirement.get("when", {}) or {}).keys(),
                *(requirement.get("required", []) or []),
                *[
                    field
                    for group in (requirement.get("required_any_of", []) or [])
                    for field in group
                ],
            ]
        ]
        for field_name in [
            *required_fields,
            *[field for group in any_of for field in group],
            *defaults,
            *conditional_fields,
        ]:
            field_name = str(field_name)
            if field_name and field_name not in field_names:
                field_names.append(field_name)
        properties = {
            field_name: _provider_field_schema(field_name, defaults.get(field_name))
            for field_name in field_names
        }
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required_fields:
            parameters["required"] = required_fields
        if any_of:
            parameters["anyOf"] = [{"required": group} for group in any_of]
        conditional_schemas: list[dict[str, Any]] = []
        for requirement in conditional_requirements:
            when = requirement.get("when")
            if not isinstance(when, dict) or not when:
                continue
            condition_properties = {
                str(field): {"const": value}
                for field, value in when.items()
            }
            then_schema: dict[str, Any] = {}
            required = [str(field) for field in (requirement.get("required", []) or [])]
            if required:
                then_schema["required"] = required
            required_any_of = [
                [str(field) for field in group]
                for group in (requirement.get("required_any_of", []) or [])
            ]
            if required_any_of:
                then_schema["anyOf"] = [{"required": group} for group in required_any_of]
            if then_schema:
                conditional_schemas.append(
                    {
                        "if": {
                            "properties": condition_properties,
                            "required": [str(field) for field in when],
                        },
                        "then": then_schema,
                    }
                )
        if conditional_schemas:
            parameters["allOf"] = conditional_schemas
        definitions.append(
            LLMToolDefinition(
                function=LLMToolFunction(
                    name=tool_name,
                    description=str(getattr(definition, "description", "") or ""),
                    parameters=parameters,
                )
            )
        )
    return definitions


class ProviderToolRoundTripRunner:
    """Run a bounded provider-native conversation with typed tool execution."""

    _MAX_TOOL_RESULT_CHARS = 1_600
    _MIN_TOOL_RESULT_CHARS = 640
    _HISTORICAL_TOOL_RESULT_CHARS = 640
    _MAX_PROVIDER_CALLS_PER_ROUND = 4
    _MAX_SOURCE_PAGE_READS = 3
    _TOKENS_PER_TOOL_RESULT_RESERVE = 512
    _GENERATOR_CONTEXT_MAX_CHARS = 8_000
    _GENERATOR_CONTEXT_ENTRY_CHARS = 2_400

    def __init__(
        self,
        owner: Any,
        task: Any,
        *,
        tools: Sequence[LLMToolDefinition],
        max_rounds: int = 3,
        user_confirmed: bool = False,
        allow_mutations: bool = False,
        max_tokens: int | None = None,
        read_scope: Sequence[str] | None = None,
        write_scope: Sequence[str] | None = None,
        project_path: str | None = None,
        validation_command: str | None = None,
        validation_cwd: str | None = None,
        max_no_progress_rounds: int = 2,
        context_max_prompt_tokens: int | None = None,
        initial_context_candidates: Sequence[ContextCandidate] | None = None,
        bounded_read_windows: Sequence[FileReadWindowSpec] | None = None,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        if max_no_progress_rounds < 1:
            raise ValueError("max_no_progress_rounds must be positive")
        self.owner = owner
        self.runtime = owner.runtime
        self.task = task
        self.tools = list(tools)
        self.max_rounds = max_rounds
        self.user_confirmed = user_confirmed
        self.allow_mutations = allow_mutations
        self.max_tokens = max_tokens
        self.read_scope = list(read_scope) if read_scope is not None else None
        self.write_scope = list(write_scope) if write_scope is not None else None
        self.project_path = project_path or None
        self.validation_command = validation_command or None
        self.validation_cwd = validation_cwd or self.project_path
        self._validation_commands_used = 0
        self.max_no_progress_rounds = max_no_progress_rounds
        self.context_max_prompt_tokens = context_max_prompt_tokens
        self.initial_context_candidates = list(initial_context_candidates or [])
        self.bounded_read_windows = tuple(bounded_read_windows or ())
        self._initial_context_consumed = False
        self._initial_context_message_count = 0
        self._attempts_by_signature: dict[str, ProviderToolAttempt] = {}
        self._attempt_ledger: list[ProviderToolAttempt] = []
        self._completed_read_sources: dict[str, ProviderToolAttempt] = {}
        self._completed_declared_windows: set[tuple[str, str, int, int]] = set()
        self._completed_declared_window_attempts: dict[
            tuple[str, str, int, int], ProviderToolAttempt
        ] = {}
        self._completed_read_projection: dict[str, str] = {}
        self._completed_read_content: dict[str, str] = {}
        # Runtime-only derived views.  The raw read result is parsed before
        # display clipping; only bounded candidate names are retained here.
        self._completed_read_structured_candidates: dict[
            str, tuple[tuple[str, ...], tuple[str, ...]]
        ] = {}
        self._completed_read_callable_candidates: dict[str, tuple[str, ...]] = {}
        self._completed_read_callsite_candidates: dict[str, tuple[str, ...]] = {}
        self._completed_page_content: dict[str, list[dict[str, Any]]] = {}
        self._code_artifact_ledger: dict[str, dict[str, Any]] = {}
        self._page_reads_by_path: dict[str, int] = {}
        self._evidence_keys: set[str] = set()
        self._last_round_progress = False
        self._no_progress_rounds = 0
        self._duplicate_only_rounds = 0
        self._finalization_requests = 0
        self._finalization_pending = False
        self._mutation_duplicate_guidance_sent = False
        self._mutation_evidence_ready_guidance_sent = False
        self._post_mutation_receipt: dict[str, Any] | None = None
        self._post_mutation_active = False
        self._post_mutation_wire_messages: list[LLMMessage] = []
        self._request_diagnostics: list[dict[str, Any]] = []
        self._budget_diagnostics: list[dict[str, Any]] = []
        self._handoff_diagnostics: list[dict[str, Any]] = []
        self._handoff_diagnostic_keys: set[tuple[str, str]] = set()

    def _result(self, **kwargs: Any) -> ProviderToolRoundTripResult:
        """Return a result with bounded per-request context evidence attached."""

        return ProviderToolRoundTripResult(
            request_diagnostics=[dict(item) for item in self._request_diagnostics],
            budget_diagnostics=[dict(item) for item in self._budget_diagnostics],
            handoff_diagnostics=[dict(item) for item in self._handoff_diagnostics],
            outcome_feedback_enabled=self._outcome_feedback_enabled(),
            reasoning_complexity=self._reasoning_complexity(),
            reasoning_mode=getattr(self._request_reasoning_policy(), "mode", None),
            **kwargs,
        )

    def _outcome_feedback_enabled(self) -> bool:
        """Return the typed feedback route actually attached to this runner."""

        try:
            return bool(self._runtime_budget().tool_event_completion_outcome_feedback_enabled)
        except Exception:
            return False

    def _record_failed_budget_diagnostic(
        self,
        *,
        round_index: int,
        budget: RuntimeBudgetMetadata | None,
        requested_limit: int | None,
        consumed: bool,
        error: BaseException,
        budget_tokens_used_before: int | None = None,
        budget_tokens_remaining_before: int | None = None,
        recovery_bonus_before: int | None = None,
    ) -> None:
        """Preserve a provider attempt even when transport returns no response."""

        if not consumed or budget is None or requested_limit is None:
            return
        response = getattr(error, "response", None)
        usage = getattr(error, "usage", None) or getattr(response, "usage", None)
        if not isinstance(usage, Mapping):
            usage = {}
        raw_actual = usage.get("completion_tokens")
        try:
            actual_completion_tokens = int(raw_actual) if raw_actual is not None else None
        except (TypeError, ValueError):
            actual_completion_tokens = None
        if actual_completion_tokens is not None and not 0 <= actual_completion_tokens <= requested_limit:
            actual_completion_tokens = None
        if actual_completion_tokens is not None:
            budget.reconcile_tool_event_completion(
                reserved=requested_limit,
                actual=actual_completion_tokens,
            )
        finish_reason = getattr(error, "finish_reason", None) or getattr(response, "finish_reason", None)
        finish_reason = str(finish_reason) if finish_reason is not None else None
        outcome = (
            ToolEventCompletionOutcome.TRUNCATED
            if finish_reason and finish_reason.lower() in {"length", "max_tokens"}
            else None
        )
        self._append_budget_diagnostic(
            {
                "round_index": round_index,
                "requested_limit": requested_limit,
                "reserved_tokens": requested_limit,
                "actual_completion_tokens": actual_completion_tokens,
                "usage_known": actual_completion_tokens is not None,
                "budget_tokens_used_before": (
                    budget_tokens_used_before
                    if budget_tokens_used_before is not None
                    else max(0, budget.tool_event_completion_tokens_used - requested_limit)
                ),
                "budget_tokens_used_after": budget.tool_event_completion_tokens_used,
                "budget_tokens_remaining_before": (
                    budget_tokens_remaining_before
                    if budget_tokens_remaining_before is not None
                    else budget.tool_event_completion_tokens_remaining + requested_limit
                ),
                "budget_tokens_remaining_after": budget.tool_event_completion_tokens_remaining,
                "recovery_bonus_before": (
                    recovery_bonus_before
                    if recovery_bonus_before is not None
                    else budget.tool_event_completion_recovery_bonus
                ),
                "recovery_bonus_after": budget.tool_event_completion_recovery_bonus,
                "finish_reason": finish_reason,
                "outcome": outcome.value if outcome is not None else None,
                "provider_cap_hit": outcome is ToolEventCompletionOutcome.TRUNCATED,
                "outcome_feedback_enabled": self._outcome_feedback_enabled(),
                "provider_attempt_failed": True,
                "error_type": type(error).__name__,
            }
        )

    def _append_budget_diagnostic(self, payload: dict[str, Any]) -> None:
        """Validate and store one strict budget diagnostic projection."""

        diagnostic = ProviderBudgetDiagnostic.model_validate(payload)
        self._budget_diagnostics.append(diagnostic.model_dump(mode="json"))

    def _mark_last_round_no_progress(self, round_index: int) -> None:
        """Convert a completed response into the typed no-progress outcome."""

        budget = self._runtime_budget()
        budget.observe_tool_event_outcome(ToolEventCompletionOutcome.NO_PROGRESS)
        for index in range(len(self._budget_diagnostics) - 1, -1, -1):
            diagnostic = self._budget_diagnostics[index]
            if diagnostic.get("round_index") != round_index:
                continue
            updated = dict(diagnostic)
            updated["outcome"] = ToolEventCompletionOutcome.NO_PROGRESS.value
            self._budget_diagnostics[index] = ProviderBudgetDiagnostic.model_validate(
                updated
            ).model_dump(mode="json")
            return

    @staticmethod
    def _completion_outcome(response: LLMResponse) -> ToolEventCompletionOutcome:
        """Classify one response using typed wire facts only."""

        finish_reason = str(response.finish_reason or "").strip().lower()
        if finish_reason in {"length", "max_tokens"}:
            return ToolEventCompletionOutcome.TRUNCATED
        if response.tool_calls:
            return ToolEventCompletionOutcome.TOOL_PROGRESS
        if not str(response.content or "").strip():
            return ToolEventCompletionOutcome.EMPTY_RESPONSE
        return ToolEventCompletionOutcome.NORMAL

    @staticmethod
    def _request_diagnostic(request: Any, *, round_index: int) -> dict[str, Any]:
        selection = getattr(request, "context_selection", None)
        if selection is not None and hasattr(selection, "to_json_dict"):
            selection_payload = selection.to_json_dict()
        elif selection is not None:
            selection_payload = dict(selection)
        else:
            selection_payload = {}
        trace_info = getattr(request, "trace_info", {}) or {}
        return {
            "round_index": round_index,
            "assembly_status": selection_payload.get("assembly_status"),
            "selected_candidate_ids": list(trace_info.get("selected_candidate_ids") or []),
            "omitted_required_candidate_ids": list(
                selection_payload.get("omitted_required_candidate_ids") or []
            ),
            "message_roles": [str(message.role) for message in request.messages],
            "message_content_chars": [len(message.content or "") for message in request.messages],
            "max_tokens": request.max_tokens,
            "tool_choice": getattr(request, "tool_choice", None),
            "reasoning_complexity": trace_info.get("reasoning_complexity"),
            "completion_budget": dict(trace_info.get("completion_budget") or {}),
            "context_selection": selection_payload,
        }

    def run(self, messages: Sequence[LLMMessage]) -> ProviderToolRoundTripResult:
        current_messages = list(messages)
        loop_results: list[ToolEventLoopRunResult] = []
        last_response: LLMResponse | None = None
        mutation_boundary_error = self._mutation_boundary_error()
        if mutation_boundary_error is not None:
            return self._result(
                success=False,
                final_response=None,
                messages=current_messages,
                tool_loop_results=loop_results,
                rounds_used=0,
                error_message=mutation_boundary_error,
                attempts=list(self._attempt_ledger),
                evidence_coverage=self._evidence_coverage(),
            )
        for round_index in range(1, self.max_rounds + 1):
            budget: RuntimeBudgetMetadata | None = None
            request_max_tokens: int | None = None
            budget_consumed = False
            budget_tokens_used_before: int | None = None
            budget_tokens_remaining_before: int | None = None
            recovery_bonus_before: int | None = None
            try:
                current_messages = (
                    self._post_mutation_messages(current_messages)
                    if self._post_mutation_active
                    else self._build_finalization_messages(current_messages)
                    if self._finalization_pending
                    else self._compact_historical_tool_messages(current_messages)
                )
                budget = self._runtime_budget()
                calls_remaining = self.max_rounds - round_index + 1
                completion_limit = budget.tool_event_completion_limit(
                    round_index=round_index,
                    calls_remaining=calls_remaining,
                )
                if completion_limit <= 0:
                    raise ProviderToolRoundTripError(
                        "provider tool completion budget is exhausted"
                    )
                request_max_tokens = min(
                    completion_limit,
                    self.max_tokens if self.max_tokens is not None else completion_limit,
                )
                trace_info = {
                    "provider_tool_round": round_index,
                    "provider_tool_rounds_remaining": self.max_rounds - round_index + 1,
                    "reasoning_complexity": self._reasoning_complexity().value,
                    "completion_budget": {
                        "dynamic_limit": request_max_tokens,
                        "total_remaining": budget.tool_event_completion_tokens_remaining,
                        "calls_remaining": calls_remaining,
                    },
                }
                if self._post_mutation_active:
                    request = build_context_llm_request(
                        self.runtime.llm_client,
                        messages=current_messages,
                        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
                        context_max_prompt_tokens=self.context_max_prompt_tokens,
                        response_format="text",
                        max_tokens=request_max_tokens,
                        timeout_seconds=45.0,
                        transport_retries=0,
                        reasoning_policy=self._request_reasoning_policy(),
                        tools=self._tools_for_request(),
                        tool_choice=self._tool_choice_for_request(),
                        trace_info=trace_info,
                    )
                elif self.initial_context_candidates and not self._initial_context_consumed and round_index == 1:
                    structured_messages = [
                        LLMMessage(role=candidate.role, content=candidate.content)
                        for candidate in self.initial_context_candidates
                    ]
                    request = build_context_candidate_request(
                        self.runtime.llm_client,
                        candidates=self.initial_context_candidates,
                        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
                        max_tokens=request_max_tokens,
                        timeout_seconds=45.0,
                        transport_retries=0,
                        reasoning_policy=self._request_reasoning_policy(),
                        tools=self._tools_for_request(),
                        tool_choice=self._tool_choice_for_request(),
                        trace_info=trace_info,
                        structured_messages=structured_messages,
                    )
                    current_messages = list(request.messages)
                    self._initial_context_consumed = True
                    self._initial_context_message_count = self._selected_initial_message_count(
                        request
                    )
                elif self.initial_context_candidates and not self._finalization_pending:
                    dynamic_messages = current_messages[self._initial_context_message_count :]
                    candidates, structured_messages = self._initial_context_with_dynamic_messages(
                        dynamic_messages
                    )
                    request = build_context_candidate_request(
                        self.runtime.llm_client,
                        candidates=candidates,
                        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
                        max_tokens=request_max_tokens,
                        timeout_seconds=45.0,
                        transport_retries=0,
                        reasoning_policy=self._request_reasoning_policy(),
                        tools=self._tools_for_request(),
                        tool_choice=self._tool_choice_for_request(),
                        trace_info=trace_info,
                        structured_messages=structured_messages,
                    )
                    current_messages = list(request.messages)
                    self._initial_context_message_count = self._selected_initial_message_count(
                        request
                    )
                else:
                    request = build_context_llm_request(
                        self.runtime.llm_client,
                        messages=current_messages,
                        purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
                        context_max_prompt_tokens=self.context_max_prompt_tokens,
                        response_format="text",
                        max_tokens=request_max_tokens,
                        timeout_seconds=45.0,
                        transport_retries=0,
                        reasoning_policy=self._request_reasoning_policy(),
                        tools=self._tools_for_request(),
                        tool_choice=self._tool_choice_for_request(),
                        trace_info=trace_info,
                    )
                self._request_diagnostics.append(
                    self._request_diagnostic(request, round_index=round_index)
                )
                budget_tokens_used_before = budget.tool_event_completion_tokens_used
                budget_tokens_remaining_before = budget.tool_event_completion_tokens_remaining
                recovery_bonus_before = budget.tool_event_completion_recovery_bonus
                budget.consume_tool_event_completion(request_max_tokens)
                budget_consumed = True
                response = self.runtime.llm_client.complete(request)
                actual_completion_tokens = self._response_completion_tokens(response)
                if actual_completion_tokens is not None:
                    budget.reconcile_tool_event_completion(
                        reserved=request_max_tokens,
                        actual=actual_completion_tokens,
                    )
                outcome = self._completion_outcome(response)
                budget.observe_tool_event_outcome(outcome)
                self._append_budget_diagnostic(
                    {
                        "round_index": round_index,
                        "requested_limit": request_max_tokens,
                        "reserved_tokens": request_max_tokens,
                        "actual_completion_tokens": actual_completion_tokens,
                        "usage_known": actual_completion_tokens is not None,
                        "budget_tokens_used_before": budget_tokens_used_before,
                        "budget_tokens_used_after": budget.tool_event_completion_tokens_used,
                        "budget_tokens_remaining_before": budget_tokens_remaining_before,
                        "budget_tokens_remaining_after": budget.tool_event_completion_tokens_remaining,
                        "recovery_bonus_before": recovery_bonus_before,
                        "recovery_bonus_after": budget.tool_event_completion_recovery_bonus,
                        "finish_reason": response.finish_reason,
                        "outcome": outcome.value,
                        "provider_cap_hit": str(response.finish_reason or "").lower()
                        in {"length", "max_tokens"},
                        "outcome_feedback_enabled": self._outcome_feedback_enabled(),
                    }
                )
            except ContextAssemblyBudgetError as exc:
                self._record_failed_budget_diagnostic(
                    round_index=round_index,
                    budget=budget,
                    requested_limit=request_max_tokens,
                    consumed=budget_consumed,
                    error=exc,
                    budget_tokens_used_before=budget_tokens_used_before,
                    budget_tokens_remaining_before=budget_tokens_remaining_before,
                    recovery_bonus_before=recovery_bonus_before,
                )
                if self._post_mutation_active:
                    return self._result(
                        success=False,
                        final_response=last_response,
                        messages=current_messages,
                        tool_loop_results=loop_results,
                        rounds_used=round_index,
                        error_message="ProviderToolPostMutationContextBudgetFailure",
                        attempts=list(self._attempt_ledger),
                        evidence_coverage=self._evidence_coverage(),
                    )
                return self._result(
                    success=False,
                    final_response=last_response,
                    messages=current_messages,
                    tool_loop_results=loop_results,
                    rounds_used=round_index,
                    error_message=f"provider round-trip request failed: {exc}",
                    attempts=list(self._attempt_ledger),
                    evidence_coverage=self._evidence_coverage(),
                )
            except Exception as exc:
                self._record_failed_budget_diagnostic(
                    round_index=round_index,
                    budget=budget,
                    requested_limit=request_max_tokens,
                    consumed=budget_consumed,
                    error=exc,
                    budget_tokens_used_before=budget_tokens_used_before,
                    budget_tokens_remaining_before=budget_tokens_remaining_before,
                    recovery_bonus_before=recovery_bonus_before,
                )
                return self._result(
                    success=False,
                    final_response=last_response,
                    messages=current_messages,
                    tool_loop_results=loop_results,
                    rounds_used=round_index,
                    error_message=f"provider round-trip request failed: {exc}",
                    attempts=list(self._attempt_ledger),
                    evidence_coverage=self._evidence_coverage(),
                )
            last_response = response
            if not response.tool_calls:
                if self._finalization_pending:
                    self._finalization_pending = False
                    if not str(response.content or "").strip():
                        return self._result(
                            success=False,
                            final_response=response,
                            messages=current_messages,
                            tool_loop_results=loop_results,
                            rounds_used=round_index,
                            error_message=self._finalization_empty_error(response),
                            attempts=list(self._attempt_ledger),
                            evidence_coverage=self._evidence_coverage(),
                        )
                return self._result(
                    success=True,
                    final_response=response,
                    messages=current_messages,
                    tool_loop_results=loop_results,
                    rounds_used=round_index,
                    attempts=list(self._attempt_ledger),
                    evidence_coverage=self._evidence_coverage(),
                )
            if self._finalization_pending:
                return self._result(
                    success=False,
                    final_response=response,
                    messages=current_messages,
                    tool_loop_results=loop_results,
                    rounds_used=round_index,
                    error_message="ProviderToolFinalizationToolCall",
                    attempts=list(self._attempt_ledger),
                    evidence_coverage=self._evidence_coverage(),
                )

            try:
                max_calls = self._max_calls_for_round(request)
                new_tool_calls, preblocked_results = self._partition_duplicate_calls(
                    response.tool_calls,
                    round_index=round_index,
                )
                admitted_tool_calls = [
                    self._prepare_provider_tool_call(call)
                    for call in new_tool_calls[:max_calls]
                ]
                admissions = admit_provider_tool_calls(
                    admitted_tool_calls,
                    task_id=str(getattr(self.task, "id", "unknown")),
                    session_id=self.owner._session_id(),
                    round_index=round_index,
                    registry=self.runtime.tool_registry,
                    budget=self._runtime_budget(),
                    user_confirmed=self.user_confirmed,
                    read_scope=self.read_scope,
                    write_scope=self.write_scope,
                    project_path=self.project_path,
                    validation_command=self.validation_command,
                    validation_cwd=self.validation_cwd,
                    validation_commands_used=self._validation_commands_used,
                )
                admissions = [
                    self._bind_project_path(admission, round_index=round_index)
                    for admission in admissions
                ]
                self._validation_commands_used += sum(
                    1
                    for admission in admissions
                    if admission.status == "admitted"
                    and admission.tool_call.tool_name == "command_executor"
                    and self.validation_command
                )
                loop_result = ToolEventLoopRunner(self.owner).run_provider_tool_calls(
                    self.task,
                    admissions,
                    round_index=round_index,
                )
                self._redact_internal_generated_units(loop_result)
            except Exception as exc:
                return self._result(
                    success=False,
                    final_response=response,
                    messages=current_messages,
                    tool_loop_results=loop_results,
                    rounds_used=round_index,
                    error_message=f"provider tool admission/execution failed: {exc}",
                    attempts=list(self._attempt_ledger),
                    evidence_coverage=self._evidence_coverage(),
                )
            loop_results.append(loop_result)
            self._record_attempts(
                response.tool_calls,
                admitted_tool_calls,
                loop_result,
                round_index=round_index,
            )
            mutation_receipt = self._mutation_receipt_from_loop_result(loop_result)
            validation_observed = self._validation_succeeded_in_loop(loop_result)
            if mutation_receipt is not None and not validation_observed:
                self._post_mutation_receipt = mutation_receipt
                self._post_mutation_active = True
                self._post_mutation_wire_messages = []
            try:
                tool_results = self._tool_results_for_response(
                    response,
                    loop_result,
                    preblocked_results=preblocked_results,
                    char_budget=self._tool_result_char_budget(request, response),
                )
                current_messages = append_tool_round_trip(
                    current_messages,
                    response,
                    tool_results,
                    require_reasoning_content=self._requires_reasoning_content(response),
                )
                if self._post_mutation_active:
                    self._post_mutation_wire_messages = self._wire_exchange(
                        response,
                        tool_results,
                    )
                if self._post_mutation_receipt is not None and self._validation_failed_in_loop(loop_result):
                    return self._result(
                        success=False,
                        final_response=response,
                        messages=current_messages,
                        tool_loop_results=loop_results,
                        rounds_used=round_index,
                        error_message="ProviderToolValidationFailed",
                        attempts=list(self._attempt_ledger),
                        evidence_coverage=self._evidence_coverage(),
                    )
                if validation_observed:
                    if round_index >= self.max_rounds:
                        return self._result(
                            success=False,
                            final_response=response,
                            messages=current_messages,
                            tool_loop_results=loop_results,
                            rounds_used=round_index,
                            error_message="ProviderToolFinalizationBudgetUnavailable",
                            attempts=list(self._attempt_ledger),
                            evidence_coverage=self._evidence_coverage(),
                        )
                    self._post_mutation_active = False
                    self._post_mutation_wire_messages = []
                    self._finalization_requests += 1
                    self._finalization_pending = True
                    current_messages.append(
                        LLMMessage(
                            role="user",
                            content=self._finalization_instruction(),
                        )
                    )
                    self._no_progress_rounds = 0
                    continue
            except Exception as exc:
                return self._result(
                    success=False,
                    final_response=response,
                    messages=current_messages,
                    tool_loop_results=loop_results,
                    rounds_used=round_index,
                    error_message=f"provider tool result round-trip failed: {exc}",
                    attempts=list(self._attempt_ledger),
                    evidence_coverage=self._evidence_coverage(),
                )
            if (
                self._mutation_tools_exposed()
                and not self._mutation_evidence_ready_guidance_sent
                and self._mutation_evidence_ready()
            ):
                self._mutation_evidence_ready_guidance_sent = True
                current_messages.append(
                    LLMMessage(
                        role="user",
                        content=self._mutation_evidence_ready_instruction(),
                    )
                )
            if not loop_result.success and not (
                self._is_recoverable_admission_failure(loop_result)
                or self._is_recoverable_execution_failure(loop_result)
            ):
                return self._result(
                    success=False,
                    final_response=response,
                    messages=current_messages,
                    tool_loop_results=loop_results,
                    rounds_used=round_index,
                    error_message=loop_result.error_message or "provider tool execution failed",
                    attempts=list(self._attempt_ledger),
                    evidence_coverage=self._evidence_coverage(),
                )
            if (
                self._page_cap_ready()
                and self._finalization_requests == 0
                and self._all_scoped_reads_complete()
                and self._read_only_tool_set()
            ):
                if round_index >= self.max_rounds:
                    return self._result(
                        success=False,
                        final_response=response,
                        messages=current_messages,
                        tool_loop_results=loop_results,
                        rounds_used=round_index,
                        error_message="ProviderToolFinalizationBudgetUnavailable",
                        attempts=list(self._attempt_ledger),
                        evidence_coverage=self._evidence_coverage(),
                    )
                self._finalization_requests += 1
                self._finalization_pending = True
                current_messages.append(
                    LLMMessage(
                        role="user",
                        content=self._finalization_instruction(),
                    )
                )
                self._no_progress_rounds = 0
                continue
            if (
                not self._round_made_progress(loop_result)
                and self._finalization_requests == 0
                and self._all_scoped_reads_complete()
                and self._read_only_tool_set()
                and self._has_bounded_projection()
            ):
                self._finalization_requests += 1
                self._finalization_pending = True
                current_messages.append(
                    LLMMessage(
                        role="user",
                        content=self._finalization_instruction(),
                    )
                )
                self._no_progress_rounds = 0
                continue
            duplicate_only_covered = (
                not admitted_tool_calls
                and bool(preblocked_results)
                and len(preblocked_results) == len(response.tool_calls)
                and self._all_scoped_reads_complete()
                and self._duplicate_calls_are_covered(response.tool_calls)
            )
            if (
                duplicate_only_covered
                and self._mutation_tools_exposed()
                and not self._mutation_duplicate_guidance_sent
            ):
                self._mutation_duplicate_guidance_sent = True
                current_messages.append(
                    LLMMessage(
                        role="user",
                        content=self._mutation_evidence_ready_instruction(),
                    )
                )
                self._no_progress_rounds = 0
                continue
            if duplicate_only_covered and self._finalization_requests == 0:
                if not self._read_only_tool_set():
                    self._no_progress_rounds += 1
                    if self._no_progress_rounds >= self.max_no_progress_rounds:
                        self._mark_last_round_no_progress(round_index)
                        return self._result(
                            success=False,
                            final_response=response,
                            messages=current_messages,
                            tool_loop_results=loop_results,
                            rounds_used=round_index,
                            error_message=(
                                "ProviderToolNoProgress after "
                                f"{self._no_progress_rounds} round(s)"
                            ),
                            attempts=list(self._attempt_ledger),
                            evidence_coverage=self._evidence_coverage(),
                        )
                    continue
                self._duplicate_only_rounds += 1
                if round_index >= self.max_rounds:
                    return self._result(
                        success=False,
                        final_response=response,
                        messages=current_messages,
                        tool_loop_results=loop_results,
                        rounds_used=round_index,
                        error_message="ProviderToolFinalizationBudgetUnavailable",
                        attempts=list(self._attempt_ledger),
                        evidence_coverage=self._evidence_coverage(),
                    )
                self._finalization_requests += 1
                self._finalization_pending = True
                current_messages.append(
                    LLMMessage(
                        role="user",
                        content=self._finalization_instruction(),
                    )
                )
                self._no_progress_rounds = 0
                continue
            if self._round_made_progress(loop_result):
                self._no_progress_rounds = 0
            else:
                self._no_progress_rounds += 1
                if self._no_progress_rounds >= self.max_no_progress_rounds:
                    self._mark_last_round_no_progress(round_index)
                    return self._result(
                        success=False,
                        final_response=response,
                        messages=current_messages,
                        tool_loop_results=loop_results,
                        rounds_used=round_index,
                        error_message=(
                            "ProviderToolNoProgress after "
                            f"{self._no_progress_rounds} round(s)"
                        ),
                        attempts=list(self._attempt_ledger),
                        evidence_coverage=self._evidence_coverage(),
                    )
        return self._result(
            success=False,
            final_response=last_response,
            messages=current_messages,
            tool_loop_results=loop_results,
            rounds_used=self.max_rounds,
            error_message=f"provider tool round limit exceeded ({self.max_rounds})",
            attempts=list(self._attempt_ledger),
            evidence_coverage=self._evidence_coverage(),
        )

    def _evidence_coverage(self) -> ProviderToolEvidenceCoverage:
        page_read_cap = self._source_page_read_cap()
        return ProviderToolEvidenceCoverage(
            completed_read_paths=tuple(sorted(self._completed_read_sources)),
            completed_declared_windows=tuple(sorted(self._completed_declared_windows)),
            bounded_projection_paths=tuple(
                sorted(
                    path
                    for path, projection in self._completed_read_projection.items()
                    if projection in {"bounded_preview", "bounded_window"}
                )
            ),
            page_reads_by_path=tuple(sorted(self._page_reads_by_path.items())),
            page_cap_paths=tuple(
                sorted(
                    path
                    for path, count in self._page_reads_by_path.items()
                    if count >= page_read_cap
                )
            ),
            page_read_cap=page_read_cap,
            observed_evidence_keys=tuple(sorted(self._evidence_keys)),
            duplicate_only_rounds=self._duplicate_only_rounds,
            finalization_requests=self._finalization_requests,
        )

    def _all_scoped_reads_complete(self) -> bool:
        if not self.read_scope:
            return False
        required = {self._canonical_path(path) for path in self.read_scope}
        if not required or not required.issubset(self._completed_read_sources):
            return False
        for path in required:
            declared = self._declared_windows_for_path(path)
            if not declared:
                continue
            # A complete full-file artifact remains sufficient.  Bounded
            # evidence, however, must cover every exact declared window before
            # routing can advance to mutation or generator work.
            if path in self._completed_read_content:
                continue
            required_windows = {
                self._declared_window_key(spec)
                for spec in declared
            }
            if not required_windows.issubset(self._completed_declared_windows):
                return False
        return True

    def _page_cap_ready(self) -> bool:
        page_read_cap = self._source_page_read_cap()
        return any(
            projection == "bounded_preview"
            and self._page_reads_by_path.get(path, 0) >= page_read_cap
            for path, projection in self._completed_read_projection.items()
        )

    def _has_bounded_projection(self) -> bool:
        return any(
            projection in {"bounded_preview", "bounded_window"}
            for projection in self._completed_read_projection.values()
        )

    def _mutation_evidence_ready(self) -> bool:
        """Return whether all declared evidence is ready for mutation routing."""

        return self._all_scoped_reads_complete() and self._has_bounded_projection()

    def _source_page_read_cap(self) -> int:
        return min(self._MAX_SOURCE_PAGE_READS, max(1, self.max_rounds - 2))

    def _duplicate_calls_are_covered(self, calls: Sequence[Any]) -> bool:
        return bool(calls) and all(
            (path := self._read_path_for_call(call)) is not None
            and path in self._completed_read_sources
            for call in calls
        )

    def _read_only_tool_set(self) -> bool:
        """Allow evidence finalization only for a pure file-read route."""
        registry = getattr(self.runtime, "tool_registry", None)
        for tool in self.tools:
            name = str(getattr(getattr(tool, "function", None), "name", "") or "")
            if name in FILE_MUTATION_TOOLS or registry is None:
                return False
            definition = registry.get(name) if hasattr(registry, "get") else None
            capabilities = set(getattr(definition, "capabilities", []) or []) if definition else set()
            if capabilities != {ToolCapability.FILE_READ}:
                return False
        return bool(self.tools)

    def _mutation_boundary_error(self) -> str | None:
        if not self._mutation_tools_exposed():
            return None
        if not self.allow_mutations:
            return "ProviderToolMutationOptInRequired"
        if not self.user_confirmed:
            return "ProviderToolMutationConfirmationRequired"
        return None

    def _mutation_tools_exposed(self) -> bool:
        registry = getattr(self.runtime, "tool_registry", None)
        for tool in self.tools:
            name = str(getattr(getattr(tool, "function", None), "name", "") or "")
            if name in FILE_MUTATION_TOOLS:
                return True
            definition = registry.get(name) if registry is not None and hasattr(registry, "get") else None
            capabilities = set(getattr(definition, "capabilities", []) or []) if definition else set()
            if capabilities & {ToolCapability.FILE_WRITE, ToolCapability.FILE_DELETE}:
                return True
        return False

    def _tools_for_request(self) -> list[LLMToolDefinition]:
        """Expose only the next safe tool surface for the current handoff state."""

        if self._finalization_pending:
            return []
        if self._post_mutation_active:
            return [
                tool
                for tool in self.tools
                if str(getattr(getattr(tool, "function", None), "name", ""))
                == "command_executor"
            ]
        if self._mutation_tools_exposed() and self._all_scoped_reads_complete():
            return [
                tool
                for tool in self.tools
                if str(getattr(getattr(tool, "function", None), "name", ""))
                not in {"file_reader", "command_executor"}
            ]
        return list(self.tools)

    def _tool_choice_for_request(self) -> str | None:
        """Require a provider tool call until the explicit finalization phase."""

        if self._finalization_pending:
            return None
        return "required" if self._tools_for_request() else None

    def _selected_initial_message_count(self, request: Any) -> int:
        selected = set((request.trace_info or {}).get("selected_candidate_ids", ()) or ())
        return sum(
            1
            for candidate in self.initial_context_candidates
            if candidate.candidate_id in selected
        )

    def _initial_context_with_dynamic_messages(
        self,
        dynamic_messages: Sequence[LLMMessage],
    ) -> tuple[list[ContextCandidate], list[LLMMessage]]:
        """Retain required facts while compacting superseded provider wire history.

        Only the latest assistant tool-call and its following results/guidance
        remain raw wire state. Earlier rounds are represented by one bounded,
        derived history marker; task, scope, evidence, and artifact facts stay
        in the initial typed candidates.
        """
        candidates = list(self.initial_context_candidates)
        structured_messages = [
            LLMMessage(role=candidate.role, content=candidate.content)
            for candidate in self.initial_context_candidates
        ]
        next_order = max((candidate.source_order for candidate in candidates), default=-1) + 1
        active_start = -1
        for index, message in enumerate(dynamic_messages):
            if message.role == "assistant" and message.tool_calls:
                active_start = index
        superseded = list(dynamic_messages[:active_start]) if active_start >= 0 else list(dynamic_messages)
        active = list(dynamic_messages[active_start:]) if active_start >= 0 else []

        if superseded:
            summary_marker = "COMPACTED_PROVIDER_ROUND_HISTORY:"
            existing_summaries = [
                str(message.content or "").strip()
                for message in superseded
                if message.role == "user"
                and str(message.content or "").lstrip().startswith(summary_marker)
            ]
            newly_superseded = [
                message
                for message in superseded
                if not (
                    message.role == "user"
                    and str(message.content or "").lstrip().startswith(summary_marker)
                )
            ]
            if existing_summaries and not newly_superseded:
                summary = existing_summaries[-1]
            else:
                tool_names = sorted(
                    {
                        str(call.function.name)
                        for message in newly_superseded
                        for call in message.tool_calls
                        if call.function.name
                    }
                )
                prior_summary = existing_summaries[-1] if existing_summaries else ""
                addition = (
                    f" Additional superseded provider messages: {len(newly_superseded)}."
                    if prior_summary and newly_superseded
                    else ""
                )
                summary = (
                    prior_summary
                    or f"{summary_marker} {len(newly_superseded)} superseded provider messages "
                    "omitted from raw wire history."
                )
                if not prior_summary:
                    summary += (
                        f" Superseded tool names: {', '.join(tool_names) if tool_names else 'none'}. "
                        "Use the authoritative typed task, scope, declared evidence, and artifact facts; "
                        "do not reconstruct omitted tool payloads."
                    )
                elif addition:
                    summary += addition
                summary = self._bounded_text(summary, self._HISTORICAL_TOOL_RESULT_CHARS) or summary
            candidates.append(
                ContextCandidate(
                    candidate_id="provider:round-history-summary",
                    source_id="provider-round-history-summary",
                    kind=ContextCandidateKind.PREVIOUS_OUTPUT,
                    content=summary,
                    role="user",
                    retention=ContextCandidateRetention.OPTIONAL,
                    priority=30,
                    source_order=next_order,
                    truncation=ContextCandidateTruncation.HEAD,
                    trust=ContextCandidateTrust.DERIVED,
                )
            )
            structured_messages.append(LLMMessage(role="user", content=summary))
            next_order += 1

        for index, message in enumerate(active):
            round_trip = message.role == "tool" or bool(message.tool_calls)
            content = str(message.content or "").strip()
            if not content:
                content = str(message.reasoning_content or "").strip()
            if not content:
                content = "[empty provider tool-call turn]"
            candidates.append(
                ContextCandidate(
                    candidate_id=f"provider:round-message:{index + 1}",
                    source_id=f"provider-round-message:{index + 1}",
                    kind=(
                        ContextCandidateKind.RUNTIME_EVIDENCE
                        if message.role == "tool"
                        else ContextCandidateKind.PREVIOUS_OUTPUT
                    ),
                    content=content,
                    role=message.role,
                    retention=ContextCandidateRetention.REQUIRED,
                    priority=100,
                    source_order=next_order + index,
                    truncation=(
                        ContextCandidateTruncation.FORBIDDEN
                        if round_trip
                        else ContextCandidateTruncation.HEAD
                    ),
                    trust=(
                        ContextCandidateTrust.DIRECT
                        if round_trip
                        else ContextCandidateTrust.OBSERVED
                    ),
                )
            )
            structured_messages.append(message)
        return candidates, structured_messages

    def _finalization_instruction(self) -> str:
        paths = ", ".join(sorted(self._completed_read_sources))
        return (
            "The allowed read scope is complete and the requested evidence has already been returned. "
            "Do not call tools or request another page. Produce the final answer now, using only the "
            f"returned evidence. Completed paths: {paths}"
        )

    def _mutation_evidence_ready_instruction(self) -> str:
        """Tell a mutation-capable provider when declared reads are sufficient.

        This is a derived prompt view of typed evidence coverage.  It does not
        grant permission, choose a tool, or copy source content; the normal
        admission, scope, artifact, and semantic-grounding gates still apply.
        """

        paths = ", ".join(sorted(self._completed_read_sources))
        windows = "; ".join(
            f"{file_path} [{read_mode}, offset={offset}, max_lines={max_lines}]"
            for file_path, read_mode, offset, max_lines in self._evidence_coverage().completed_declared_windows
        )
        return (
            "READ_EVIDENCE_READY: every declared read window has complete typed "
            "evidence and is sufficient for the requested mutation. Do not call "
            "file_reader again; it is no longer an available action. The next "
            "action must be code_unit_generator using only the authoritative "
            "declared evidence, followed by the verified artifact_ref through "
            "file_patch_writer and the exact validation command. Declared windows: "
            f"{windows or 'none'}. Completed paths: {paths}"
        )

    def _build_finalization_messages(self, messages: Sequence[LLMMessage]) -> list[LLMMessage]:
        """Build a bounded no-tools context without replaying old tool wire state."""
        system_message = next((message for message in messages if message.role == "system"), None)
        user_message = next((message for message in messages if message.role == "user"), None)
        evidence: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        # The provider-facing result projection is intentionally small, but a
        # no-tools finalization request still needs enough source evidence to
        # answer symbol-level questions. Keep a bounded excerpt derived from
        # the already-authorized typed artifact; never replay the full wire
        # history or bypass the read scope.
        prompt_budget = self.context_max_prompt_tokens or 4_096
        digest_char_budget = min(12_000, max(4_000, int(prompt_budget)))
        source_count = max(1, len(self._completed_read_content))
        source_budget = max(
            800,
            min(6_000, (digest_char_budget - 512) // source_count),
        )
        for path, content in sorted(self._completed_read_content.items()):
            excerpt = self._bounded_source_excerpt(content, source_budget)
            evidence.append(
                {
                    "file_path": path,
                    "evidence_status": "complete",
                    "projection_status": "bounded_source_excerpt",
                    "source_excerpt": excerpt,
                }
            )
            seen.add((path, excerpt))
            for page_index, page in enumerate(self._completed_page_content.get(path, ())):
                page_excerpt = str(page.get("excerpt") or "")
                evidence.append(
                    {
                        "file_path": path,
                        "page_index": page_index,
                        "offset": page.get("offset"),
                        "max_lines": page.get("max_lines"),
                        "read_mode": page.get("read_mode"),
                        "evidence_status": "partial",
                        "projection_status": "bounded_page_excerpt",
                        "source_excerpt": page_excerpt,
                    }
                )
                seen.add((path, page_excerpt))

        for message in messages:
            if message.role != "tool":
                continue
            payload = self._parse_tool_result_payload(message.content)
            result = payload.get("result") if isinstance(payload, dict) else None
            artifact_ref = payload.get("artifact_ref") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                continue
            path = str(result.get("file_path") or "")
            if not path and isinstance(artifact_ref, dict):
                path = str(artifact_ref.get("file_path") or "")
            if not path:
                continue
            path = self._canonical_path(path)
            if path in self._completed_read_content:
                continue
            preview = self._bounded_text(result.get("preview"), 320) or ""
            identity = (path, preview)
            if identity in seen:
                continue
            seen.add(identity)
            evidence.append(
                {
                    "file_path": path,
                    "lines_read": result.get("lines_read"),
                    "total_lines": result.get("total_lines"),
                    "source_truncated": result.get("truncated"),
                    "evidence_status": result.get("evidence_status"),
                    "projection_status": result.get("projection_status"),
                    "preview": preview,
                }
            )
        max_items = max(
            1,
            len(self._completed_read_sources) * (1 + self._MAX_SOURCE_PAGE_READS),
        )
        evidence = evidence[-max_items:]
        digest = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        if len(digest) > digest_char_budget and evidence:
            per_item = max(800, (digest_char_budget - 512) // len(evidence))
            for item in evidence:
                if isinstance(item.get("source_excerpt"), str):
                    item["source_excerpt"] = self._bounded_source_excerpt(
                        item["source_excerpt"],
                        per_item,
                    )
                elif isinstance(item.get("preview"), str):
                    item["preview"] = self._bounded_text(item["preview"], per_item)
            digest = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        finalization_message = LLMMessage(
            role="user",
            content=(
                f"{self._finalization_instruction()}\n"
                "Bounded evidence digest from the allowed reads (do not call tools): "
                f"{digest}"
            ),
        )
        compacted = [message for message in (system_message, user_message) if message is not None]
        compacted.append(finalization_message)
        return compacted

    @staticmethod
    def _bounded_source_excerpt(content: str, limit: int) -> str:
        """Keep complete small files and a deterministic symbol excerpt for large ones."""
        text = str(content or "")
        if len(text) <= limit:
            return text
        lines = text.splitlines()
        head_chars = max(240, limit // 4)
        tail_chars = max(240, limit // 4)
        symbol_budget = max(240, limit - head_chars - tail_chars - 80)
        symbol_matches = [
            (index, match.group(1))
            for index, line in enumerate(lines)
            if (match := re.match(r"^\s*(?:class|async\s+def|def)\s+(\w+)", line))
        ]
        symbol_lines = [
            f"{index + 1}: {line}"
            for index, line in enumerate(lines)
            if re.match(r"^\s*(?:class|async\s+def|def)\s+", line)
        ]
        symbol_index = "\n".join(symbol_lines)

        # A symbol name alone is insufficient when the relevant implementation
        # is outside the head/tail window. Add a compact local-call index for
        # executable symbols so finalization can recover bounded call edges
        # without replaying the full source or widening the read scope.
        defined_names = {name for _, name in symbol_matches}
        call_edges: list[str] = []
        call_site_lines: list[str] = []
        for position, (start, name) in enumerate(symbol_matches):
            end = symbol_matches[position + 1][0] if position + 1 < len(symbol_matches) else len(lines)
            body = "\n".join(lines[start:end])
            called = sorted(
                candidate
                for candidate in defined_names
                if candidate != name and re.search(rf"\b{re.escape(candidate)}\s*\(", body)
            )
            if not called:
                continue
            for line_index, line in enumerate(lines[start:end], start=start):
                if re.match(r"^\s*(?:class|async\s+def|def)\s+", line):
                    continue
                if any(re.search(rf"\b{re.escape(candidate)}\s*\(", line) for candidate in called):
                    window_start = max(start, line_index - 2)
                    window = " | ".join(
                        current.strip()
                        for current in lines[window_start : line_index + 1]
                        if current.strip()
                    )
                    call_site_lines.append(f"{window_start + 1}-{line_index + 1}: {window}")
            # Prioritize orchestration edges, which are the most useful when a
            # long source projection must be compacted aggressively.
            if re.search(r"(?:^|_)run|(?:^|_)execute|^main$", name):
                call_edges.insert(0, f"{name} -> {', '.join(called)}")
            else:
                call_edges.append(f"{name} -> {', '.join(called)}")
        if call_edges:
            # Keep call edges before the exhaustive symbol list so aggressive
            # clipping preserves behaviorally useful relationships first.
            target_call_sites = [
                line
                for line in call_site_lines
                if re.search(r"_run_once_mode|_execute_agent_generator", line)
            ]
            priority_call_sites = [
                line
                for line in call_site_lines
                if line not in target_call_sites
                and re.search(r"(?:_run|_execute|run_enhanced_cli)", line)
            ]
            other_call_sites = [
                line for line in call_site_lines if line not in target_call_sites and line not in priority_call_sites
            ]
            call_sites = "\n".join(target_call_sites + priority_call_sites + other_call_sites)
            symbols = (
                f"...[local call-site hints]\n{call_sites}\n"
                f"...[local call index]\n{chr(10).join(call_edges)}\n"
                f"...[symbol index]\n{symbol_index}"
            )
        else:
            symbols = symbol_index
        if len(symbols) > symbol_budget:
            symbols = symbols[:symbol_budget] + "\n...[symbol index clipped]"
        excerpt = (
            text[:head_chars]
            + "\n...[source excerpt clipped; symbol index follows]\n"
            + symbols
            + "\n...[tail]\n"
            + text[-tail_chars:]
        )
        return excerpt[:limit]

    def _runtime_budget(self) -> RuntimeBudgetMetadata:
        controller = getattr(self.runtime, "runtime_controller", None)
        state = getattr(controller, "state", None)
        budget = getattr(state, "budget", None)
        if not isinstance(budget, RuntimeBudgetMetadata):
            raise ProviderToolRoundTripError("provider tool execution requires active runtime budget")
        return budget

    def _reasoning_policy(self) -> Any:
        policy_for_task = getattr(self.owner, "_reasoning_policy_for_task", None)
        return policy_for_task(self.task) if callable(policy_for_task) else None

    def _reasoning_complexity(self) -> ReasoningDecisionComplexity:
        """Return the typed task route used for each provider round."""
        complexity_for_task = getattr(self.owner, "_reasoning_complexity_for_task", None)
        if callable(complexity_for_task):
            complexity = complexity_for_task(self.task)
            if isinstance(complexity, ReasoningDecisionComplexity):
                return complexity
        return ReasoningDecisionComplexity.STANDARD

    def _request_reasoning_policy(self) -> Any:
        """Use an explicit disabled intent for bounded finalization when supported."""
        if self._finalization_pending:
            return ReasoningPolicy(
                mode=ReasoningMode.DISABLED,
                unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
            )
        return self._reasoning_policy()

    def _requires_reasoning_content(self, response: LLMResponse) -> bool:
        """Decide whether this response must carry DeepSeek continuation state.

        ``provider_default`` delegates the reasoning decision to the provider.  A
        tool response without ``reasoning_content`` is therefore a valid
        continuation, while a response that includes it must still be preserved.
        Explicit enabled/adaptive requests remain fail-closed when the provider
        omits the state; explicit disabled requests never require it.
        """
        policy = self._request_reasoning_policy()
        settings = getattr(self.runtime.llm_client, "settings", None)
        if policy is None or settings is None:
            return True
        try:
            resolved = resolve_reasoning_policy(policy, settings)
        except Exception:
            return True
        if resolved.effective_mode == ReasoningMode.DISABLED:
            return False
        if resolved.effective_mode == ReasoningMode.PROVIDER_DEFAULT:
            return bool(response.reasoning_content)
        return True

    def _tool_results_for_response(
        self,
        response: LLMResponse,
        loop_result: ToolEventLoopRunResult,
        *,
        preblocked_results: dict[str, dict[str, Any]] | None = None,
        char_budget: int | None = None,
    ) -> list[LLMToolResult]:
        by_provider_id = {
            str(item.get("provider_call_id")): item
            for item in loop_result.tool_results
            if item.get("provider_call_id")
        }
        error_by_provider_id = {
            error.provider_call_id: error
            for error in loop_result.loop_metadata.recoverable_errors
            if error.provider_call_id
        }
        results: list[LLMToolResult] = []
        for call in response.tool_calls:
            preblocked = (preblocked_results or {}).get(call.id)
            item = by_provider_id.get(call.id)
            if preblocked is not None:
                payload = preblocked
            elif item is None:
                payload = {
                    "success": False,
                    "error_type": "ProviderToolBatchAborted",
                    "error": "The project stopped this batch before executing this call; retry it in a later round.",
                }
            else:
                error = error_by_provider_id.get(call.id)
                result_projection, artifact_ref = self._result_projection(
                    item.get("result"),
                    source_id=str(item.get("call_id") or call.id),
                    provider_call_id=call.id,
                    call=call,
                )
                payload = {
                    "success": bool(item.get("success")),
                    "tool": item.get("tool"),
                    "result": result_projection,
                    "artifact_ref": artifact_ref,
                    "error_type": error.error_type if error is not None else None,
                    "error": self._bounded_text(item.get("error"), 320),
                    "suggested_recovery": self._bounded_text(item.get("suggested_recovery"), 320),
                }
            content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            content = self._fit_tool_result_payload(
                payload,
                limit=char_budget or self._MAX_TOOL_RESULT_CHARS,
            )
            results.append(LLMToolResult(tool_call_id=call.id, content=content))
        return results

    def _partition_duplicate_calls(
        self,
        calls: Sequence[Any],
        *,
        round_index: int,
    ) -> tuple[list[Any], dict[str, dict[str, Any]]]:
        """Keep new calls for admission and return bounded duplicate errors."""
        new_calls: list[Any] = []
        preblocked: dict[str, dict[str, Any]] = {}
        for call in calls:
            window_mismatch = self._declared_window_mismatch(call)
            if window_mismatch is not None:
                preblocked[call.id] = {
                    "success": False,
                    "tool": call.function.name,
                    "error_type": "ProviderToolBoundedWindowMismatch",
                    "error": window_mismatch,
                    "suggested_recovery": "Retry the same declared path with the exact typed bounded window.",
                }
                continue
            signature = self._call_signature(call)
            previous = self._attempts_by_signature.get(signature)
            read_path = self._read_path_for_call(call)
            completed_source = self._completed_read_sources.get(read_path) if read_path else None
            declared_window_keys = self._declared_window_keys_for_path(read_path)
            declared_window_key = self._declared_window_key_for_call(call)
            declared_window_pending = bool(
                declared_window_keys
                and declared_window_key in declared_window_keys
                and declared_window_key not in self._completed_declared_windows
            )
            page_cap_reached = bool(
                read_path
                and self._page_reads_by_path.get(read_path, 0) >= self._source_page_read_cap()
                and not declared_window_pending
            )
            if previous is None and declared_window_keys:
                # For a bounded task, duplicate identity is the exact declared
                # window. A completed header must not preblock a distinct body
                # window for the same path.
                if declared_window_key in self._completed_declared_windows:
                    previous = self._completed_declared_window_attempts.get(
                        declared_window_key
                    )
            elif (
                previous is None
                and completed_source is not None
                and (
                    self._completed_read_projection.get(read_path or "") != "bounded_preview"
                    or page_cap_reached
                )
            ):
                # Legacy full-read routes retain path-level duplicate behavior.
                previous = completed_source
            if previous is None:
                new_calls.append(call)
                continue
            error_type = (
                "ProviderToolEvidencePageCap"
                if page_cap_reached
                else "ProviderToolDuplicateAttempt"
            )
            preblocked[call.id] = {
                "success": False,
                "tool": call.function.name,
                "error_type": error_type,
                "error": (
                    "The source page-read cap was reached; finish with the evidence already returned."
                    if page_cap_reached
                    else "The same normalized tool input was already attempted."
                ),
                "previous_call_id": previous.provider_call_id,
                "suggested_recovery": "Choose a new evidence-backed path or finish with the evidence already returned.",
            }
            self._attempt_ledger.append(
                ProviderToolAttempt(
                    signature=signature,
                    tool_name=call.function.name,
                    provider_call_id=call.id,
                    round_index=round_index,
                    success=False,
                    error_type=error_type,
                    duplicate_of=previous.provider_call_id,
                )
            )
        return new_calls, preblocked

    def _bind_project_path(
        self,
        admission: ProviderToolAdmission,
        *,
        round_index: int,
    ) -> ProviderToolAdmission:
        """Bind the known project root before the event-loop contract recheck.

        Providers may emit a safe relative path even when the task scope is
        canonical absolute paths. Admission already checked that relative path
        against ``self.project_path``; carrying the same root into the typed
        tool input prevents the second guard from resolving it against the
        source file's parent directory. No scope is widened and no path is
        rewritten here—the file tool performs the canonical resolution.
        """

        input_metadata = admission.tool_call.input_metadata
        updates: dict[str, Any] = {}
        if self.project_path and not str(input_metadata.project_path or "").strip():
            updates["project_path"] = self.project_path
        if (
            input_metadata.tool_name == "command_executor"
            and self.validation_command
            and self.validation_cwd
            and not str(input_metadata.cwd or "").strip()
        ):
            # Admission validates an optional provider cwd against the typed
            # validation root, but command_executor actually executes using
            # ``cwd`` (not project_path).  Bind the exact disposable root
            # before the event loop so a host checkout can never masquerade as
            # validation evidence.
            updates["cwd"] = self.validation_cwd

        # Nested LLM tools execute inside the provider-tool lifecycle. Carry
        # the same typed reasoning intent and a dynamic completion ceiling into
        # those calls instead of letting a tool silently resolve provider
        # default reasoning with an unbounded completion request.
        if input_metadata.tool_name in {"code_unit_generator", "code_generator", "code_editor"}:
            runtime_handles = dict(input_metadata.runtime_handles)
            runtime_handles["_reasoning_policy"] = self._request_reasoning_policy()
            budget = self._runtime_budget()
            nested_limit = budget.tool_event_completion_limit(
                round_index=round_index,
                calls_remaining=max(1, self.max_rounds - round_index + 1),
            )
            if nested_limit > 0:
                runtime_handles["_max_tokens"] = min(nested_limit, 1_200)
            runtime_handles["_runtime_budget"] = budget
            if self.write_scope is not None:
                runtime_handles["_post_processing_write_scope"] = tuple(self.write_scope)
            updates["runtime_handles"] = runtime_handles
            if input_metadata.tool_name == "code_unit_generator":
                grounding_source_ids = input_metadata.runtime_handles.get(
                    "_generator_grounding_source_ids"
                )
                if not str(input_metadata.context or "").strip():
                    grounded_context, source_ids = self._declared_generator_context()
                    if grounded_context:
                        updates["context"] = grounded_context
                        grounding_source_ids = source_ids
                if isinstance(grounding_source_ids, list) and grounding_source_ids:
                    runtime_handles["_generator_grounding_enforced"] = True
                    prompt_context = dict(input_metadata.prompt_context)
                    prompt_context["grounding"] = {
                        "mode": "declared_read_evidence",
                        "source_ids": [str(item) for item in grounding_source_ids],
                    }
                    updates["prompt_context"] = prompt_context
                if not str(input_metadata.file_path or "").strip():
                    write_targets = [
                        self._canonical_path(path)
                        for path in (self.write_scope or ())
                        if str(path or "").strip()
                    ]
                    # A single declared write target is unambiguous.  Never
                    # select one target from a multi-file scope on the
                    # provider's behalf.
                    if len(write_targets) == 1:
                        updates["file_path"] = write_targets[0]
        elif input_metadata.tool_name == "file_patch_writer" and input_metadata.artifact_ref:
            updates["generated_unit"] = self._resolve_code_artifact(
                input_metadata.artifact_ref
            )
            if self.write_scope is not None:
                runtime_handles = dict(input_metadata.runtime_handles)
                runtime_handles["_post_processing_write_scope"] = tuple(self.write_scope)
                updates["runtime_handles"] = runtime_handles
        elif self.write_scope is not None:
            runtime_handles = dict(input_metadata.runtime_handles)
            runtime_handles["_post_processing_write_scope"] = tuple(self.write_scope)
            updates["runtime_handles"] = runtime_handles

        if not updates:
            return admission
        bound_input = input_metadata.model_copy(update=updates)
        tool_call = admission.tool_call.model_copy(update={"input_metadata": bound_input})
        selection = (
            admission.selection.model_copy(update={"input_metadata": bound_input})
            if admission.selection is not None
            else None
        )
        return admission.model_copy(update={"tool_call": tool_call, "selection": selection})

    def _prepare_provider_tool_call(self, call: Any) -> Any:
        """Supply safe generator defaults before typed admission.

        ``code_unit_generator`` is a mutating-capable nested tool, so its
        declared write target must be present before admission can apply the
        write-scope predicate.  The provider may omit it; only a single
        declared target is eligible for this deterministic binding.  The
        completed read evidence is similarly injected before admission and is
        marked as an internal lineage handle, never as provider authority.
        """

        if getattr(getattr(call, "function", None), "name", "") != "code_unit_generator":
            return call
        raw_arguments = str(getattr(call.function, "arguments", "") or "").strip()
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError:
            return call
        if not isinstance(arguments, dict):
            return call

        changed = False
        # A provider-supplied context is explanatory input, not authority. If
        # exact declared evidence is complete, always replace it with the
        # typed source-linked projection so the semantic gate cannot be
        # bypassed by a plausible-looking summary or invented name. Explicit
        # context remains compatible for calls with no completed evidence.
        grounded_context, source_ids = self._declared_generator_context()
        if grounded_context:
            arguments["context"] = grounded_context
            arguments["_generator_grounding_source_ids"] = source_ids
            arguments["_generator_grounding_enforced"] = True
            changed = True
        elif not str(arguments.get("context") or "").strip():
            # There is no authoritative evidence to inject. Leave the
            # existing behavior (empty context) unchanged.
            pass
        if not str(arguments.get("file_path") or "").strip():
            write_targets = [
                self._canonical_path(path)
                for path in (self.write_scope or ())
                if str(path or "").strip()
            ]
            if len(write_targets) == 1:
                arguments["file_path"] = write_targets[0]
                changed = True
        if not changed:
            return call
        encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        function = call.function.model_copy(update={"arguments": encoded})
        return call.model_copy(update={"function": function})

    def _declared_generator_context(self) -> tuple[str, list[str]]:
        """Build bounded generator grounding from completed typed read evidence.

        The provider may omit ``context`` on a nested generator call.  In that
        case, expose only evidence already collected through the declared read
        scope.  Windowed pages are eligible only when their exact window was
        declared, so a partial exploratory preview cannot become an
        authoritative source by accident.
        """

        allowed_paths = None
        if self.read_scope is not None:
            allowed_paths = {
                self._canonical_path(path)
                for path in self.read_scope
                if str(path or "").strip()
            }

        def allowed(path: str) -> bool:
            canonical = self._canonical_path(path)
            return allowed_paths is None or canonical in allowed_paths

        def source_id(path: str) -> str:
            canonical = self._canonical_path(path)
            attempt = self._completed_read_sources.get(canonical)
            provider_call_id = str(attempt.provider_call_id) if attempt else ""
            suffix = f":{provider_call_id}" if provider_call_id else ""
            return f"declared-read:{canonical}{suffix}"

        def import_candidate(path: str) -> str | None:
            return self._module_import_candidate_for_path(path)

        def candidate_values(value: Any, *, modules: bool = False) -> tuple[str, ...]:
            """Validate locally-derived candidate fields before prompt projection."""

            if not isinstance(value, (list, tuple, set, frozenset)):
                return ()
            pattern = (
                r"\.?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
                if modules
                else r"[A-Za-z_]\w*"
            )
            values = {
                item.strip()
                for item in value
                if isinstance(item, str) and re.fullmatch(pattern, item.strip())
            }
            return tuple(sorted(values))[:128]

        def projection_is_clipped(value: Any) -> bool:
            return _contains_projection_marker(str(value or ""))

        def callsite_values(value: Any) -> tuple[str, ...]:
            """Validate bounded call-site text before prompt projection."""

            if not isinstance(value, (list, tuple, set, frozenset)):
                return ()
            values = {
                item.strip()
                for item in value
                if (
                    isinstance(item, str)
                    and item.strip()
                    and len(item.strip()) <= _MAX_STRUCTURED_CALLSITE_CHARS
                    and not projection_is_clipped(item)
                )
            }
            return tuple(sorted(values))[:_MAX_STRUCTURED_CALLSITE_CANDIDATES]

        entries: list[tuple[str, str]] = []
        complete_paths = {
            self._canonical_path(path)
            for path in self._completed_read_content
            if allowed(path)
        }
        for path, content in sorted(self._completed_read_content.items()):
            canonical = self._canonical_path(path)
            if canonical not in complete_paths:
                continue
            excerpt = self._bounded_text(content, self._GENERATOR_CONTEXT_ENTRY_CHARS) or ""
            if not excerpt.strip():
                continue
            projection = self._completed_read_projection.get(canonical, "complete")
            structured = self._completed_read_structured_candidates.get(canonical)
            callable_candidates = self._completed_read_callable_candidates.get(canonical, ())
            callsite_candidates = self._completed_read_callsite_candidates.get(canonical, ())
            if structured is not None:
                raw_modules, raw_symbols = structured
            elif projection_is_clipped(excerpt):
                raw_modules, raw_symbols = (), ()
            else:
                raw_modules = _module_import_candidates(excerpt)
                raw_symbols = _module_symbol_candidates(excerpt)
                callsite_candidates = _module_callsite_candidates(excerpt)
            module_candidates = list(candidate_values(raw_modules, modules=True))
            path_module = import_candidate(canonical)
            if path_module and path_module not in module_candidates:
                module_candidates.insert(0, path_module)
            symbol_candidates = candidate_values(raw_symbols)
            callsite_candidates = callsite_values(callsite_candidates)
            entries.append(
                (
                    source_id(canonical),
                    "\n".join(
                        [
                            f"SOURCE_ID: {source_id(canonical)}",
                            f"FILE_PATH: {canonical}",
                            *[
                                f"MODULE_IMPORT_CANDIDATE: {module}"
                                for module in module_candidates
                            ],
                            *[
                                f"MODULE_CALLSITE_HINT: {candidate}"
                                for candidate in callsite_candidates
                            ],
                            *[
                                f"MODULE_SYMBOL_CANDIDATE: {name}"
                                for name in symbol_candidates
                            ],
                            *[
                                f"MODULE_CALLABLE_CANDIDATE: {candidate}"
                                for candidate in callable_candidates
                            ],
                            "EVIDENCE_STATUS: complete",
                            f"PROJECTION_STATUS: {projection}",
                            "CONTENT:",
                            excerpt,
                        ]
                    ),
                )
            )

        for path, pages in sorted(self._completed_page_content.items()):
            canonical = self._canonical_path(path)
            if canonical in complete_paths or not allowed(canonical):
                continue
            declared_window_keys = self._declared_window_keys_for_path(canonical)
            if not declared_window_keys:
                continue
            for index, page in enumerate(pages):
                excerpt = self._bounded_text(page.get("excerpt"), self._GENERATOR_CONTEXT_ENTRY_CHARS) or ""
                if not excerpt.strip():
                    continue
                window = {
                    key: page.get(key)
                    for key in ("read_mode", "offset", "max_lines")
                    if page.get(key) is not None
                }
                window_key = self._window_key_from_mapping(canonical, window)
                # Only exact declared windows are complete enough to ground
                # code generation. Other pages remain diagnostic and are not
                # copied, even when the same path has another declared window.
                if window_key not in declared_window_keys:
                    continue
                window_attempt = self._completed_declared_window_attempts.get(window_key)
                if window_attempt is None:
                    # A declared shape alone is not evidence that the provider
                    # actually returned that exact window.
                    continue
                page_structured = page.get("structured_candidates")
                if isinstance(page_structured, Mapping):
                    raw_modules = page_structured.get("module_import_candidates")
                    raw_symbols = page_structured.get("module_symbol_candidates")
                    raw_callables = page_structured.get("module_callable_candidates")
                    raw_callsites = page_structured.get("module_callsite_candidates")
                else:
                    raw_modules = page.get("module_import_candidates")
                    raw_symbols = page.get("module_symbol_candidates")
                    raw_callables = page.get("module_callable_candidates")
                    raw_callsites = page.get("module_callsite_candidates")
                if not isinstance(raw_symbols, (list, tuple, set, frozenset)):
                    raw_symbols = (
                        ()
                        if projection_is_clipped(excerpt)
                        else _module_symbol_candidates(excerpt)
                    )
                if not isinstance(raw_modules, (list, tuple, set, frozenset)):
                    raw_modules = (
                        ()
                        if projection_is_clipped(excerpt)
                        else _module_import_candidates(excerpt)
                    )
                module_candidates = list(candidate_values(raw_modules, modules=True))
                path_module = import_candidate(canonical)
                if path_module and path_module not in module_candidates:
                    module_candidates.insert(0, path_module)
                symbol_candidates = candidate_values(raw_symbols)
                callable_candidates = (
                    tuple(
                        candidate
                        for candidate in raw_callables
                        if isinstance(candidate, str)
                        and len(candidate) <= _MAX_STRUCTURED_CALLABLE_CHARS
                    )[:_MAX_STRUCTURED_CALLABLE_CANDIDATES]
                    if isinstance(raw_callables, (list, tuple, set, frozenset))
                    else ()
                )
                callsite_candidates = (
                    callsite_values(raw_callsites)
                    if isinstance(raw_callsites, (list, tuple, set, frozenset))
                    else (
                        ()
                        if projection_is_clipped(excerpt)
                        else _module_callsite_candidates(excerpt)
                    )
                )
                window_label = "-".join(str(value) for value in window_key[1:])
                page_source_id = (
                    f"declared-read:{canonical}:{window_attempt.provider_call_id}:"
                    f"page-{index + 1}:window-{window_label}"
                )
                entries.append(
                    (
                        page_source_id,
                        "\n".join(
                            [
                                f"SOURCE_ID: {page_source_id}",
                                f"FILE_PATH: {canonical}",
                                *[
                                    f"MODULE_IMPORT_CANDIDATE: {module}"
                                    for module in module_candidates
                                ],
                                *[
                                    f"MODULE_CALLSITE_HINT: {candidate}"
                                    for candidate in callsite_candidates
                                ],
                                *[
                                    f"MODULE_SYMBOL_CANDIDATE: {name}"
                                    for name in symbol_candidates
                                ],
                                *[
                                    f"MODULE_CALLABLE_CANDIDATE: {candidate}"
                                    for candidate in callable_candidates
                                ],
                                "EVIDENCE_STATUS: complete",
                                "PROJECTION_STATUS: bounded_window",
                                f"READ_WINDOW: {json.dumps(window, sort_keys=True)}",
                                "CONTENT:",
                                excerpt,
                            ]
                        ),
                    )
                )

        if not entries:
            return "", []

        sections = [
            "AUTHORITATIVE DECLARED READ EVIDENCE",
            "Use only names, imports, APIs, and paths visible in these sections.",
        ]
        selected_source_ids: list[str] = []
        remaining = self._GENERATOR_CONTEXT_MAX_CHARS - sum(len(item) + 1 for item in sections)
        for entry_source_id, entry in entries:
            if remaining <= 0:
                break
            bounded_entry = entry if len(entry) <= remaining else (self._bounded_text(entry, remaining) or "")
            if not bounded_entry.strip():
                continue
            sections.append(bounded_entry)
            selected_source_ids.append(entry_source_id)
            remaining -= len(bounded_entry) + 1
        return "\n\n".join(sections), selected_source_ids

    def _record_attempts(
        self,
        response_calls: Sequence[Any],
        executed_calls: Sequence[Any],
        loop_result: ToolEventLoopRunResult,
        *,
        round_index: int,
    ) -> None:
        executed_ids = {call.id for call in executed_calls}
        result_by_id = {
            str(item.get("provider_call_id")): item
            for item in loop_result.tool_results
            if item.get("provider_call_id")
        }
        error_by_id = {
            str(error.provider_call_id): error
            for error in loop_result.loop_metadata.recoverable_errors
            if error.provider_call_id
        }
        self._last_round_progress = False
        for call in response_calls:
            if call.id not in executed_ids:
                continue
            signature = self._call_signature(call)
            item = result_by_id.get(call.id, {})
            error = error_by_id.get(call.id)
            attempt = ProviderToolAttempt(
                signature=signature,
                tool_name=call.function.name,
                provider_call_id=call.id,
                round_index=round_index,
                success=bool(item.get("success")),
                error_type=error.error_type if error is not None else None,
            )
            self._attempt_ledger.append(attempt)
            self._attempts_by_signature.setdefault(signature, attempt)
            if not attempt.success:
                continue
            raw_result = item.get("result")
            result = metadata_summary(item.get("result"))
            read_path = self._read_path_for_call(call)
            if call.function.name == "file_reader" and isinstance(result, dict):
                result_path = result.get("file_path")
                if result_path:
                    read_path = self._canonical_path(result_path)
                raw_content = (
                    raw_result.get("content")
                    if isinstance(raw_result, dict)
                    else getattr(raw_result, "content", None)
                )
                evidence_key = self._result_evidence_key(result)
                is_complete = self._is_complete_read_result(result)
                is_declared_window = self._matches_declared_window(
                    call,
                    result,
                    read_path,
                )
                structured_modules, structured_symbols = (
                    _structured_source_candidates(raw_content)
                    if isinstance(raw_content, str) and raw_content
                    else ((), ())
                )
                structured_callables = (
                    _module_callable_candidates(raw_content)
                    if isinstance(raw_content, str) and raw_content
                    else ()
                )
                structured_callsites = (
                    _module_callsite_candidates(raw_content)
                    if isinstance(raw_content, str) and raw_content
                    else ()
                )
                if is_complete and read_path and isinstance(raw_content, str) and raw_content:
                    self._completed_read_structured_candidates[read_path] = (
                        structured_modules,
                        structured_symbols,
                    )
                    self._completed_read_callable_candidates[read_path] = structured_callables
                    self._completed_read_callsite_candidates[read_path] = structured_callsites
                    self._completed_read_content[read_path] = self._bounded_source_excerpt(
                        raw_content,
                        12_000,
                    )
                elif (
                    read_path
                    and isinstance(raw_content, str)
                    and raw_content
                    and self._is_windowed_read_call(call)
                ):
                    pages = self._completed_page_content.setdefault(read_path, [])
                    if len(pages) < self._MAX_SOURCE_PAGE_READS:
                        pages.append(
                            {
                                "excerpt": self._bounded_source_excerpt(raw_content, 1_200),
                                "module_import_candidates": list(structured_modules),
                                "module_symbol_candidates": list(structured_symbols),
                                "module_callable_candidates": list(structured_callables),
                                "module_callsite_candidates": list(structured_callsites),
                                "structured_candidates": {
                                    "module_import_candidates": list(structured_modules),
                                    "module_symbol_candidates": list(structured_symbols),
                                    "module_callable_candidates": list(structured_callables),
                                    "module_callsite_candidates": list(structured_callsites),
                                },
                                **self._read_window_for_call(call),
                            }
                        )
                if (
                    self._is_windowed_read_call(call)
                    and read_path
                    and not is_complete
                ):
                    self._page_reads_by_path[read_path] = self._page_reads_by_path.get(read_path, 0) + 1
                if evidence_key not in self._evidence_keys and (
                    read_path not in self._completed_read_sources
                    or self._completed_read_projection.get(read_path or "")
                    in {"bounded_preview", "bounded_window"}
                ):
                    self._last_round_progress = True
                self._evidence_keys.add(evidence_key)
                if (is_complete or is_declared_window) and read_path:
                    self._completed_read_sources.setdefault(read_path, attempt)
                    if is_declared_window:
                        declared_window_key = self._window_key_from_mapping(
                            read_path,
                            result.get("read_window"),
                        )
                        if declared_window_key is not None:
                            self._completed_declared_windows.add(declared_window_key)
                            self._completed_declared_window_attempts.setdefault(
                                declared_window_key,
                                attempt,
                            )
                    raw_content = result.get("content")
                    self._completed_read_projection[read_path] = (
                        "bounded_window"
                        if is_declared_window and not is_complete
                        else "bounded_preview"
                        if not isinstance(raw_content, str) or len(raw_content) > 480
                        else "inline"
                    )
            else:
                self._last_round_progress = True

    def _mutation_receipt_from_loop_result(
        self,
        loop_result: ToolEventLoopRunResult,
    ) -> dict[str, Any] | None:
        for item in loop_result.tool_results:
            if not item.get("success") or item.get("tool") not in FILE_MUTATION_TOOLS:
                continue
            receipt = self._mutation_receipt(item)
            if receipt is not None:
                return receipt
        return None

    @staticmethod
    def _redact_internal_generated_units(loop_result: ToolEventLoopRunResult) -> None:
        """Remove full generated code from retained event projections after execution."""

        for item in loop_result.tool_results:
            input_metadata = item.get("input_metadata")
            if not isinstance(input_metadata, dict):
                continue
            generated = input_metadata.get("generated_unit")
            if not isinstance(generated, str):
                continue
            input_metadata["generated_unit"] = None
            input_metadata["generated_unit_chars"] = len(generated)
            input_metadata["generated_unit_sha256"] = hashlib.sha256(
                generated.encode("utf-8")
            ).hexdigest()

        def redact_model(value: Any) -> Any:
            input_metadata = getattr(value, "input_metadata", None)
            if input_metadata is None or not isinstance(
                getattr(input_metadata, "generated_unit", None), str
            ):
                return value
            generated = input_metadata.generated_unit
            redacted = input_metadata.model_copy(
                update={
                    "generated_unit": None,
                    "runtime_handles": {
                        **dict(input_metadata.runtime_handles),
                        "_generated_unit_chars": len(generated),
                        "_generated_unit_sha256": hashlib.sha256(
                            generated.encode("utf-8")
                        ).hexdigest(),
                    },
                }
            )
            return value.model_copy(update={"input_metadata": redacted})

        loop_metadata = loop_result.loop_metadata
        loop_metadata.events = [redact_model(event) for event in loop_metadata.events]
        loop_metadata.tool_invocations = [
            redact_model(invocation) for invocation in loop_metadata.tool_invocations
        ]
        loop_metadata.recoverable_errors = [
            redact_model(error) for error in loop_metadata.recoverable_errors
        ]

    def _mutation_receipt(self, item: Mapping[str, Any]) -> dict[str, Any] | None:
        """Return bounded post-write evidence without copying generated code."""

        if not item.get("success") or item.get("tool") not in FILE_MUTATION_TOOLS:
            return None
        input_metadata = item.get("input_metadata")
        if not isinstance(input_metadata, Mapping):
            return None
        file_path = str(input_metadata.get("file_path") or "").strip()
        if not file_path:
            return None
        result = metadata_summary(item.get("result"))
        result = result if isinstance(result, Mapping) else {}
        attributes = result.get("attributes")
        attributes = attributes if isinstance(attributes, Mapping) else {}
        artifact_ref = input_metadata.get("artifact_ref")
        if isinstance(artifact_ref, Mapping):
            artifact_ref = {
                key: artifact_ref[key]
                for key in (
                    "kind",
                    "source_id",
                    "provider_call_id",
                    "sha256",
                    "bytes",
                    "chars",
                    "language",
                )
                if key in artifact_ref
            }
        else:
            artifact_ref = None
        changed_ranges = attributes.get("changed_ranges")
        if isinstance(changed_ranges, list):
            changed_ranges = [
                {
                    key: value
                    for key, value in entry.items()
                    if key in {"line_start", "line_end"}
                }
                for entry in changed_ranges[:8]
                if isinstance(entry, Mapping)
            ]
        else:
            changed_ranges = []
        return {
            "status": "mutation_applied",
            "tool": str(item.get("tool") or ""),
            "file_path": file_path,
            "operation_kind": str(input_metadata.get("operation_kind") or ""),
            "artifact_ref": artifact_ref,
            "bytes_written": result.get("bytes_written"),
            "changed_ranges": changed_ranges,
            "validation_command": self.validation_command,
        }

    def _validation_succeeded_in_loop(self, loop_result: ToolEventLoopRunResult) -> bool:
        expected = str(self.validation_command or "").strip()
        if not expected:
            return False
        for item in loop_result.tool_results:
            if not item.get("success") or item.get("tool") != "command_executor":
                continue
            input_metadata = item.get("input_metadata")
            if not isinstance(input_metadata, Mapping):
                continue
            actual = str(
                input_metadata.get("requested_command")
                or input_metadata.get("command")
                or ""
            ).strip()
            if validation_commands_match(expected, actual):
                return True
        return False

    def _validation_failed_in_loop(self, loop_result: ToolEventLoopRunResult) -> bool:
        """Detect an executed exact validation command with a failed result."""

        expected = str(self.validation_command or "").strip()
        if not expected:
            return False
        for item in loop_result.tool_results:
            if item.get("tool") != "command_executor":
                continue
            input_metadata = item.get("input_metadata")
            if not isinstance(input_metadata, Mapping):
                continue
            actual = str(
                input_metadata.get("requested_command")
                or input_metadata.get("command")
                or ""
            ).strip()
            if not validation_commands_match(expected, actual):
                continue
            if not item.get("success"):
                return True
            result = metadata_summary(item.get("result"))
            if isinstance(result, Mapping):
                try:
                    if int(result.get("exit_code")) != 0:
                        return True
                except (TypeError, ValueError):
                    pass
                if result.get("success") is False:
                    return True
        return False

    @staticmethod
    def _wire_exchange(
        response: LLMResponse,
        tool_results: Sequence[LLMToolResult],
    ) -> list[LLMMessage]:
        exchange = [
            LLMMessage(
                role="assistant",
                content=response.content or "",
                reasoning_content=response.reasoning_content,
                tool_calls=list(response.tool_calls),
            )
        ]
        exchange.extend(
            LLMMessage(role="tool", content=result.content, tool_call_id=result.tool_call_id)
            for result in tool_results
        )
        return exchange

    def _post_mutation_messages(self, messages: Sequence[LLMMessage]) -> list[LLMMessage]:
        """Build a narrow continuation after mutation, dropping old wire history."""

        base: list[LLMMessage] = []
        for message in messages:
            if message.role not in {"system", "user"}:
                continue
            if any(existing.role == message.role for existing in base):
                continue
            base.append(message.model_copy(deep=True))
            if len(base) == 2:
                break
        receipt = self._post_mutation_receipt or {
            "status": "mutation_applied",
            "validation_command": self.validation_command,
        }
        receipt_text = json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        instruction = (
            "The declared mutation has been applied. Do not regenerate code or repeat the write. "
            "Use command_executor exactly once with the required validation command, then report the "
            "evidence. Bounded mutation receipt: "
            f"{self._bounded_text(receipt_text, 1_200)}"
        )
        return [
            *base,
            LLMMessage(role="user", content=instruction),
            *self._post_mutation_wire_messages,
        ]

    def _round_made_progress(self, loop_result: ToolEventLoopRunResult) -> bool:
        del loop_result
        return self._last_round_progress

    def _call_signature(self, call: Any) -> str:
        raw_arguments = str(call.function.arguments or "").strip()
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError:
            arguments = {"_raw_arguments": raw_arguments}
        canonical = self._canonicalize_call_arguments(arguments)
        payload = {"tool": str(call.function.name), "arguments": canonical}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _read_path_for_call(self, call: Any) -> str | None:
        raw_arguments = str(call.function.arguments or "").strip()
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError:
            return None
        if not isinstance(arguments, dict) or call.function.name != "file_reader":
            return None
        value = arguments.get("file_path")
        return self._canonical_path(value) if value else None

    @staticmethod
    def _is_windowed_read_call(call: Any) -> bool:
        raw_arguments = str(call.function.arguments or "").strip()
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError:
            return False
        if not isinstance(arguments, dict) or call.function.name != "file_reader":
            return False
        return bool(
            arguments.get("offset")
            or arguments.get("max_lines") is not None
            or str(arguments.get("read_mode") or "full").lower() in {"adaptive", "sample", "tail"}
        )

    @staticmethod
    def _read_window_for_call(call: Any) -> dict[str, Any]:
        raw_arguments = str(call.function.arguments or "").strip()
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError:
            return {}
        if not isinstance(arguments, dict):
            return {}
        return {
            "offset": arguments.get("offset"),
            "max_lines": arguments.get("max_lines"),
            "read_mode": arguments.get("read_mode"),
        }

    def _declared_windows_for_path(self, read_path: str | None) -> tuple[FileReadWindowSpec, ...]:
        if not read_path:
            return ()
        canonical = self._canonical_path(read_path)
        return tuple(
            spec
            for spec in self.bounded_read_windows
            if self._canonical_path(spec.file_path) == canonical
        )

    def _declared_window_key(
        self,
        spec: FileReadWindowSpec,
    ) -> tuple[str, str, int, int]:
        return (
            self._canonical_path(spec.file_path),
            str(getattr(spec.read_mode, "value", spec.read_mode)).lower(),
            int(spec.offset),
            int(spec.max_lines),
        )

    def _window_key_from_mapping(
        self,
        read_path: str | None,
        window: Mapping[str, Any] | None,
    ) -> tuple[str, str, int, int] | None:
        if not read_path or not isinstance(window, Mapping):
            return None
        try:
            read_mode = str(window.get("read_mode") or "full").lower()
            offset = int(window.get("offset") or 0)
            max_lines = int(window.get("max_lines"))
        except (TypeError, ValueError):
            return None
        if offset < 0 or max_lines < 1:
            return None
        return (self._canonical_path(read_path), read_mode, offset, max_lines)

    def _declared_window_for_path(self, read_path: str | None) -> FileReadWindowSpec | None:
        declared = self._declared_windows_for_path(read_path)
        return declared[0] if declared else None

    def _declared_window_keys_for_path(self, read_path: str | None) -> set[tuple[str, str, int, int]]:
        return {
            self._declared_window_key(spec)
            for spec in self._declared_windows_for_path(read_path)
        }

    def _declared_window_key_for_call(self, call: Any) -> tuple[str, str, int, int] | None:
        return self._window_key_from_mapping(
            self._read_path_for_call(call),
            self._read_window_for_call(call),
        )

    def _declared_window_for_path_legacy(self, read_path: str | None) -> FileReadWindowSpec | None:
        """Compatibility alias for older callers that expect one path window."""

        return self._declared_window_for_path(read_path)

    def _declared_window_mismatch(self, call: Any) -> str | None:
        """Return a typed mismatch when a declared window is read differently."""

        if call.function.name != "file_reader":
            return None
        read_path = self._read_path_for_call(call)
        declared = self._declared_windows_for_path(read_path)
        if not declared:
            return None
        actual_key = self._declared_window_key_for_call(call)
        declared_keys = {self._declared_window_key(spec) for spec in declared}
        if actual_key in declared_keys:
            return None
        expected = ", ".join(
            f"read_mode={getattr(spec.read_mode, 'value', spec.read_mode)}, "
            f"offset={spec.offset}, max_lines={spec.max_lines}"
            for spec in declared
        )
        return f"Declared bounded window requires one of: {expected}."

    def _matches_declared_window(
        self,
        call: Any,
        result: Mapping[str, Any],
        read_path: str | None,
    ) -> bool:
        declared = self._declared_windows_for_path(read_path)
        if not declared:
            return False
        window = result.get("read_window")
        actual_key = self._window_key_from_mapping(read_path, window)
        if actual_key is None:
            return False
        return (
            actual_key in {self._declared_window_key(spec) for spec in declared}
            and self._declared_window_mismatch(call) is None
        )

    def _canonical_path(self, value: Any) -> str:
        path = Path(str(value or ""))
        if not path.is_absolute() and self.project_path:
            path = Path(self.project_path) / path
        return str(path.expanduser().resolve(strict=False))

    def _module_import_candidate_for_path(self, path: str) -> str | None:
        """Map an in-scope source path to its project module, if unambiguous."""

        if not self.project_path:
            return None
        source_root = Path(self.project_path).expanduser().resolve(strict=False) / "Code" / "src"
        candidate = Path(path).expanduser().resolve(strict=False)
        try:
            relative = candidate.relative_to(source_root)
        except ValueError:
            return None
        if relative.suffix != ".py":
            return None
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts) or None

    @staticmethod
    def _is_complete_read_result(result: dict[str, Any]) -> bool:
        if bool(result.get("truncated")):
            return False
        total_lines = result.get("total_lines")
        lines_read = result.get("lines_read")
        if total_lines is not None and lines_read is not None:
            return int(lines_read) >= int(total_lines)
        return True

    @staticmethod
    def _result_evidence_key(result: dict[str, Any]) -> str:
        raw_content = result.get("content")
        if isinstance(raw_content, str):
            payload = raw_content.encode("utf-8")
        else:
            payload = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _canonicalize_call_arguments(self, value: Any, *, key: str = "") -> Any:
        if isinstance(value, dict):
            return {
                str(name): self._canonicalize_call_arguments(child, key=str(name))
                for name, child in sorted(value.items(), key=lambda item: str(item[0]))
            }
        if isinstance(value, list):
            items = [self._canonicalize_call_arguments(child, key=key) for child in value]
            return sorted(items) if key == "file_paths" else items
        if isinstance(value, str) and key in {"file_path", "directory_path", "file_paths"}:
            path = Path(value)
            if not path.is_absolute() and self.project_path:
                path = Path(self.project_path) / path
            return str(path.expanduser().resolve(strict=False))
        return value

    def _result_projection(
        self,
        result: Any,
        *,
        source_id: str,
        provider_call_id: str,
        call: Any | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Project a typed result without sending the full artifact content."""
        summary = metadata_summary(result)
        if not isinstance(summary, dict):
            return {"value": self._bounded_text(summary, 320)}, None

        text_fields = ("content", "stdout", "stderr", "text", "research_summary")
        raw_text = next(
            (summary.get(field) for field in text_fields if isinstance(summary.get(field), str)),
            None,
        )
        artifact_ref: dict[str, Any] | None = None
        if raw_text is not None:
            encoded = raw_text.encode("utf-8")
            artifact_ref = {
                "kind": str(summary.get("kind") or "tool_result_artifact"),
                "source_id": source_id,
                "provider_call_id": provider_call_id,
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
                "chars": len(raw_text),
            }
            if summary.get("file_path"):
                artifact_ref["file_path"] = str(summary["file_path"])
            if summary.get("kind") == "code_artifact":
                artifact_ref = self._register_code_artifact(
                    summary,
                    source_id=source_id,
                    provider_call_id=provider_call_id,
                )
                diagnostic_key = (
                    str(provider_call_id),
                    str(artifact_ref.get("sha256") or ""),
                )
                if diagnostic_key not in self._handoff_diagnostic_keys:
                    self._handoff_diagnostic_keys.add(diagnostic_key)
                    tool_name = str(
                        getattr(getattr(call, "function", None), "name", "")
                        or "code_unit_generator"
                    )
                    self._handoff_diagnostics.append(
                        {
                            "tool_name": tool_name,
                            "source_id": str(source_id),
                            "provider_call_id": str(provider_call_id),
                            "result_kind": "code_artifact",
                            "projection_status": "bounded_preview",
                            "artifact_ref": {
                                key: artifact_ref[key]
                                for key in (
                                    "kind",
                                    "source_id",
                                    "provider_call_id",
                                    "sha256",
                                    "bytes",
                                    "chars",
                                    "language",
                                )
                                if key in artifact_ref
                            },
                        }
                    )

        projected: dict[str, Any] = {}
        for key in (
            "kind",
            "file_path",
            "files",
            "size_bytes",
            "bytes_written",
            "file_type",
            "lines_read",
            "total_lines",
            "truncated",
            "read_window",
            "title",
            "content_type",
            "exit_code",
            "success",
            "count",
            "provider",
        ):
            value = summary.get(key)
            if value not in (None, "", [], {}):
                projected[key] = value
        if summary.get("kind") == "file_artifact":
            complete = self._is_complete_read_result(summary)
            declared_window = False
            if call is not None:
                result_path = summary.get("file_path")
                read_path = self._canonical_path(result_path) if result_path else None
                declared_window = self._matches_declared_window(call, summary, read_path)
            projected["evidence_status"] = "complete" if complete or declared_window else "partial"
            if declared_window:
                if raw_text:
                    projected["preview"] = self._bounded_text(raw_text, 480)
                projected["projection_status"] = "bounded_window"
            elif raw_text and complete and len(raw_text) <= 480:
                # A complete short artifact is not a preview.  Naming it
                # ``content`` prevents providers from treating usable
                # evidence as an incomplete window and issuing a duplicate
                # read merely to obtain the rest of the file.
                projected["content"] = raw_text
                projected["projection_status"] = "inline"
            else:
                if raw_text:
                    projected["preview"] = self._bounded_text(raw_text, 480)
                projected["projection_status"] = "bounded_preview" if raw_text else "inline"
        elif raw_text:
            projected["preview"] = self._bounded_text(raw_text, 480)
            if summary.get("kind") == "code_artifact":
                projected["artifact_handoff"] = (
                    "Pass artifact_ref unchanged to file_patch_writer.artifact_ref; "
                    "do not copy the bounded preview into generated_unit."
                )
        return projected, artifact_ref

    def _register_code_artifact(
        self,
        artifact: Any,
        *,
        source_id: str,
        provider_call_id: str,
    ) -> dict[str, Any]:
        """Register one generated code artifact for a bounded writer handoff."""

        summary = artifact if isinstance(artifact, Mapping) else metadata_summary(artifact)
        if not isinstance(summary, dict) or summary.get("kind") != "code_artifact":
            raise ProviderToolRoundTripError("code artifact handoff requires a code_artifact result")
        code = str(summary.get("code") or summary.get("content") or "")
        if not code:
            raise ProviderToolRoundTripError("code artifact handoff requires non-empty code")
        encoded = code.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        reference = {
            "kind": "code_artifact",
            "source_id": str(source_id),
            "provider_call_id": str(provider_call_id),
            "sha256": digest,
            "bytes": len(encoded),
            "chars": len(code),
            "language": str(summary.get("language") or "python"),
        }
        self._code_artifact_ledger[digest] = {
            "code": code,
            "reference": reference,
        }
        return reference

    def _resolve_code_artifact(self, reference: Any) -> str:
        """Resolve and verify a provider-supplied code artifact reference."""

        if not isinstance(reference, Mapping):
            raise ProviderToolRoundTripError("code artifact reference must be an object")
        if str(reference.get("kind") or "") != "code_artifact":
            raise ProviderToolRoundTripError("unsupported code artifact reference kind")
        digest = str(reference.get("sha256") or "").lower().removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ProviderToolRoundTripError("code artifact reference checksum is invalid")
        entry = self._code_artifact_ledger.get(digest)
        if not isinstance(entry, dict):
            raise ProviderToolRoundTripError("code artifact reference is stale or unknown")
        expected = entry.get("reference")
        if not isinstance(expected, Mapping):
            raise ProviderToolRoundTripError("code artifact reference ledger entry is invalid")
        for field in ("source_id", "provider_call_id"):
            if str(reference.get(field) or "") != str(expected.get(field) or ""):
                raise ProviderToolRoundTripError(f"code artifact reference {field} does not match")
        code = str(entry.get("code") or "")
        if hashlib.sha256(code.encode("utf-8")).hexdigest() != digest:
            raise ProviderToolRoundTripError("code artifact reference content checksum mismatch")
        if reference.get("chars") is not None and int(reference["chars"]) != len(code):
            raise ProviderToolRoundTripError("code artifact reference character count does not match")
        return code

    @staticmethod
    def _bounded_text(value: Any, limit: int) -> str | None:
        if value is None:
            return None
        text = str(value)
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 20)] + "...[truncated]"

    def _fit_tool_result_payload(self, payload: dict[str, Any], *, limit: int) -> str:
        limit = max(self._MIN_TOOL_RESULT_CHARS, int(limit))
        candidate = dict(payload)
        content = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        if len(content) <= limit:
            return content
        result = candidate.get("result")
        text_key: str | None = None
        original_preview = ""
        text_was_compacted = False
        declared_window_complete = bool(
            isinstance(result, dict)
            and result.get("evidence_status") == "complete"
            and result.get("projection_status") == "bounded_window"
        )
        if isinstance(result, dict) and ("preview" in result or "content" in result):
            text_key = "preview" if "preview" in result else "content"
            preview = str(result.get(text_key) or "")
            # The binary search below mutates the candidate projection. Keep
            # the source preview intact for the compacted-artifact fallback;
            # otherwise a large lineage envelope can leave a complete result
            # with ``preview=""`` even though bounded evidence would fit.
            original_preview = preview
            low, high = 0, len(preview)
            best = ""
            while low <= high:
                midpoint = (low + high) // 2
                result[text_key] = preview[:midpoint]
                content = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
                if len(content) <= limit:
                    best = preview[:midpoint]
                    low = midpoint + 1
                else:
                    high = midpoint - 1
            result[text_key] = best
            if best != preview:
                # A complete declared window is semantically sufficient even
                # when its display is shortened to fit the tool-result wire
                # budget.  ``projection_compacted`` used to make providers
                # treat this as missing evidence and repeat the exact read.
                # Keep an explicit display marker while preserving the typed
                # evidence/projection fields below.
                if declared_window_complete:
                    candidate["display_truncated"] = True
                else:
                    candidate["projection_compacted"] = True
                text_was_compacted = True
                if text_key == "content":
                    result["preview"] = result.pop("content")
            content = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        # A complete short file represented by ``content`` may have been
        # moved to a bounded ``preview`` only to make room for the lineage
        # envelope. Let the file-artifact compaction below try a smaller
        # envelope that can still carry the complete content.
        if len(content) <= limit and not (text_key == "content" and text_was_compacted):
            return content
        if isinstance(result, dict) and result.get("kind") == "file_artifact":
            compacted_result = {
                key: result[key]
                for key in (
                    "kind",
                    "file_path",
                    "lines_read",
                    "total_lines",
                    "truncated",
                    "read_window",
                    "evidence_status",
                    "projection_status",
                )
                if key in result
            }
            artifact_ref = candidate.get("artifact_ref")
            if isinstance(artifact_ref, dict):
                compacted_artifact_ref = {
                    key: artifact_ref[key]
                    for key in ("kind", "source_id", "sha256", "chars", "file_path")
                    if key in artifact_ref
                }
            else:
                compacted_artifact_ref = artifact_ref
            compacted = {
                "success": bool(candidate.get("success")),
                "tool": candidate.get("tool"),
                "result": compacted_result,
                "artifact_ref": compacted_artifact_ref,
            }
            if declared_window_complete:
                compacted["display_truncated"] = True
            else:
                compacted["projection_compacted"] = True
            if text_key == "content" and len(original_preview) <= 480:
                inline_result = dict(compacted_result)
                inline_result["projection_status"] = "inline"
                inline_result["content"] = original_preview
                inline_artifact_ref = {
                    key: artifact_ref[key]
                    for key in ("sha256", "file_path")
                    if isinstance(artifact_ref, dict) and key in artifact_ref
                }
                inline = dict(compacted)
                inline["result"] = inline_result
                inline["artifact_ref"] = inline_artifact_ref or None
                inline_content = json.dumps(inline, ensure_ascii=False, separators=(",", ":"))
                if len(inline_content) <= limit:
                    return inline_content
            compacted_result["preview"] = self._bounded_text(original_preview, 96)
            content = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
            if len(content) <= limit:
                return content
            compacted_result.pop("preview", None)
            content = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
            if len(content) <= limit:
                return content
            compacted["artifact_ref"] = (
                {"sha256": compacted_artifact_ref.get("sha256")}
                if isinstance(compacted_artifact_ref, dict) and compacted_artifact_ref.get("sha256")
                else None
            )
            content = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
            if len(content) <= limit:
                return content
        minimal = {
            "success": bool(candidate.get("success")),
            "tool": candidate.get("tool"),
            "error_type": candidate.get("error_type"),
            "error": candidate.get("error"),
            "artifact_ref": candidate.get("artifact_ref"),
        }
        minimal["display_truncated" if declared_window_complete else "projection_compacted"] = True
        content = json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))
        return content if len(content) <= limit else json.dumps(
            {
                "success": bool(candidate.get("success")),
                "tool": candidate.get("tool"),
                "projection_compacted": True,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _tool_result_char_budget(self, request: Any, response: LLMResponse) -> int:
        settings = getattr(self.runtime.llm_client, "settings", None)
        prompt_budget = int(
            self.context_max_prompt_tokens
            or getattr(settings, "context_max_prompt_tokens", 4_096)
            or 4_096
        )
        call_count = max(1, len(response.tool_calls))
        budget = min(self._MAX_TOOL_RESULT_CHARS, prompt_budget // call_count)
        budget = min(
            budget,
            max(self._MIN_TOOL_RESULT_CHARS, self._MAX_TOOL_RESULT_CHARS // call_count),
        )
        remaining = getattr(getattr(request, "context_selection", None), "remaining_prompt_tokens", None)
        if remaining is not None:
            budget = min(budget, max(self._MIN_TOOL_RESULT_CHARS, int(remaining) * 4 // call_count))
        return max(self._MIN_TOOL_RESULT_CHARS, budget)

    @staticmethod
    def _response_completion_tokens(response: LLMResponse) -> int | None:
        usage = getattr(response, "usage", None)
        if not isinstance(usage, dict):
            return None
        raw = usage.get("completion_tokens", usage.get("output_tokens"))
        try:
            return max(0, int(raw)) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def _finalization_empty_error(self, response: LLMResponse) -> str:
        """Classify an empty finalization without mistaking reasoning exhaustion for success."""
        finish_reason = str(response.finish_reason or "").lower()
        if finish_reason in {"length", "max_tokens"}:
            completion_tokens = self._response_completion_tokens(response)
            details = (response.usage or {}).get("completion_tokens_details", {})
            reasoning_tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
            try:
                if (
                    completion_tokens is not None
                    and reasoning_tokens is not None
                    and int(reasoning_tokens) >= completion_tokens
                ):
                    return "ProviderToolFinalizationReasoningExhausted"
            except (TypeError, ValueError):
                pass
        return "ProviderToolFinalizationEmpty"

    def _max_calls_for_round(self, request: Any) -> int:
        remaining = getattr(getattr(request, "context_selection", None), "remaining_prompt_tokens", None)
        if remaining is None:
            return 2
        return max(
            1,
            min(
                self._MAX_PROVIDER_CALLS_PER_ROUND,
                int(remaining) // self._TOKENS_PER_TOOL_RESULT_RESERVE,
            ),
        )

    def _compact_historical_tool_messages(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        latest_tool_assistant = max(
            (index for index, message in enumerate(messages) if message.role == "assistant" and message.tool_calls),
            default=-1,
        )
        if latest_tool_assistant < 0:
            return messages
        compacted: list[LLMMessage] = []
        for index, message in enumerate(messages):
            if message.role != "tool" or index >= latest_tool_assistant:
                compacted.append(message)
                continue
            content = self._fit_tool_result_payload(
                self._parse_tool_result_payload(message.content),
                limit=self._HISTORICAL_TOOL_RESULT_CHARS,
            )
            compacted.append(message.model_copy(update={"content": content}))
        return compacted

    @staticmethod
    def _parse_tool_result_payload(content: str) -> dict[str, Any]:
        try:
            value = json.loads(content)
        except (TypeError, ValueError):
            return {"success": False, "truncated": True, "content": str(content)[:480]}
        return value if isinstance(value, dict) else {"value": str(value)}

    @staticmethod
    def _is_recoverable_admission_failure(loop_result: ToolEventLoopRunResult) -> bool:
        failure = loop_result.loop_metadata.final_error
        if failure is None:
            return False
        return failure.error_type in {
            "InvalidToolArguments",
            "MissingRequiredInput",
            "MissingRequiredInputGroup",
            "UnknownTool",
            "ToolConfirmationRequired",
            "UserConfirmationRequired",
            "ProviderToolScopeViolation",
        } and bool(failure.recoverable)

    def _is_recoverable_execution_failure(self, loop_result: ToolEventLoopRunResult) -> bool:
        """Allow only safe read-only execution failures to reach the provider.

        The tool loop intentionally stops a batch after an execution error so
        later calls are represented as aborted in the provider-facing result.
        A provider may continue only when the failed call was read-only and the
        failure is not an indeterminate checkpoint/state boundary. Mutation,
        command, and checkpoint failures remain terminal even if their generic
        metadata happens to mark them recoverable.
        """
        failure = loop_result.loop_metadata.final_error
        if failure is None or not bool(failure.recoverable):
            return False
        if failure.error_type in {
            "CheckpointPrepareFailed",
            "CheckpointObservationFailed",
            "IndeterminateSideEffect",
            "MutationVerificationFailed",
        }:
            return False
        failed_tools = {
            str(item.get("tool") or "")
            for item in loop_result.tool_results
            if not bool(item.get("success"))
        }
        if not failed_tools:
            return False
        registry = getattr(self.runtime, "tool_registry", None)
        for tool_name in failed_tools:
            if tool_name in FILE_MUTATION_TOOLS:
                return False
            definition = registry.get(tool_name) if registry is not None and hasattr(registry, "get") else None
            capabilities = {
                str(getattr(capability, "value", capability))
                for capability in (getattr(definition, "capabilities", []) or [])
            }
            unsafe = {
                ToolCapability.FILE_WRITE.value,
                ToolCapability.FILE_DELETE.value,
                ToolCapability.CODE_EXECUTION.value,
                ToolCapability.SHELL_EXECUTION.value,
            }
            if ToolCapability.FILE_READ.value not in capabilities or capabilities & unsafe:
                return False
        return True


def _provider_field_schema(field_name: str, default: Any = None) -> dict[str, Any]:
    lowered = field_name.lower()
    if lowered == "mode":
        # Validation commands are a typed execution boundary.  Exposing a
        # free-form string lets providers invent values such as ``standard``
        # that the admission layer must reject after mutation.  Keep the wire
        # schema aligned with the accepted command-tool modes and let the
        # admission layer retain the final fail-closed check.
        schema = {
            "type": "string",
            "enum": ["automatic", "execute", "run", "exec"],
        }
    elif lowered.endswith(("_paths", "_files")) or lowered in {"files", "file_paths"}:
        field_type = "array"
        schema: dict[str, Any] = {"type": field_type, "items": {"type": "string"}}
    elif lowered in {"recursive", "overwrite", "create_dirs", "follow_redirects", "safe_search"}:
        schema = {"type": "boolean"}
    elif lowered.endswith(("_lines", "_size_mb", "_tokens", "_results", "_pages", "_seconds", "_depth", "_offset", "_files")) or lowered in {"timeout", "limit"}:
        schema = {"type": "integer"}
    elif lowered in {"patch", "attributes", "validation_context", "artifact_ref"}:
        schema = {"type": "object"}
    else:
        schema = {"type": "string"}
    if default is not None:
        schema["default"] = default
    return schema


__all__ = [
    "ProviderToolAttempt",
    "ProviderToolEvidenceCoverage",
    "ProviderToolRoundTripError",
    "ProviderToolRoundTripResult",
    "ProviderToolRoundTripRunner",
    "build_provider_tool_definitions",
]
