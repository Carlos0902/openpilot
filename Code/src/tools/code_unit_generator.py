"""Code Unit Generator Tool - Generate a function/class/module fragment."""

from __future__ import annotations

import ast
import builtins
import re
import symtable
import uuid
from typing import Any

from core.llm import LLMMessage, render_llm_message
from memory.context_assembly import build_context_llm_request
from metadata import (
    ContextCandidateTruncation,
    ContextRequestPurpose,
    ToolContractMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
    metadata_tool_result,
)

from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition, ToolFailureMode


CODE_UNIT_GENERATOR_DEFINITION = ToolDefinition(
    name="code_unit_generator",
    display_name="Code Unit Generator",
    description="Generate a new code unit for insertion into an existing file",
    version="1.0.0",
    capabilities=[ToolCapability.CODE_EXECUTION, ToolCapability.LLM_CALL],
    permission_level=PermissionLevel.MEDIUM,
    contract_metadata=ToolContractMetadata(
        tool_name="code_unit_generator",
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=["task_description", "language"],
        input_defaults={"operation_kind": "add_symbol", "target_scope": "symbol", "context": ""},
    ),
    timeout_seconds=300,
    max_retries=2,
    failure_modes=[
        ToolFailureMode(
            error_type="llm_error",
            description="LLM failed to generate the requested code unit",
            recovery_strategy="Retry with a smaller symbol-level request and surrounding context",
        )
    ],
    tags=["code", "generation", "symbol", "unit"],
    audit_required=True,
)

_EVIDENCE_PROJECTION_MARKERS = (
    "...[source excerpt clipped;",
    "...[symbol index clipped]",
    "...[truncated]",
)


@metadata_tool_result("code_unit_generator")
def code_unit_generator_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    params = input_metadata.to_params()
    language = str(params.get("language") or "python").lower()
    if language not in {"python", "shell", "bash"}:
        raise ValueError(f"Unsupported language: {language}. Use python, shell, or bash.")

    generated = str(params.get("generated_unit") or params.get("code") or "").strip()
    if not generated:
        llm_client = params.get("_llm_client")
        if llm_client is None:
            from core.config import LLMSettings
            from core.llm import LLMClient

            llm_client = LLMClient(LLMSettings())
        raw_max_tokens = params.get("_max_tokens")
        if raw_max_tokens is not None:
            if isinstance(raw_max_tokens, bool) or not isinstance(raw_max_tokens, int) or raw_max_tokens < 1:
                raise ValueError("_max_tokens must be a positive integer when provided")
        generated = _extract_code(
            _call_llm(
                llm_client,
                _build_prompt(params, language),
                reasoning_policy=params.get("_reasoning_policy"),
                max_tokens=raw_max_tokens,
                runtime_budget=params.get("_runtime_budget"),
            ),
            language,
        ).strip()

    if language == "python":
        _validate_python_unit(generated)
        if params.get("_generator_grounding_enforced"):
            _validate_grounded_python_unit(generated, str(params.get("context") or ""))

    return {
        "code": generated,
        "generated_unit": generated,
        "language": language,
        "operation_kind": "add_symbol",
        "target_scope": params.get("target_scope") or "symbol",
        "symbol_name": params.get("symbol_name"),
        "symbol_type": params.get("symbol_type"),
        "insertion_hint": params.get("insertion_hint") or "end_of_file",
        "functions": _extract_functions(generated) if language == "python" else [],
        "code_id": f"code_unit_{uuid.uuid4().hex[:8]}",
    }


def _build_prompt(params: dict[str, object], language: str) -> str:
    return f"""You are OpenPilot's Code Unit Generator.
Generate only the new {language} code unit requested below. Do not return a full file.

TASK:
{params.get("task_description") or ""}

TARGET FILE:
{params.get("file_path") or ""}

TARGET SCOPE:
{params.get("target_scope") or "symbol"}

SYMBOL:
{params.get("symbol_type") or ""} {params.get("symbol_name") or ""}

EVIDENCE POLICY:
- The context below is AUTHORITATIVE DECLARED READ EVIDENCE.
- Only reuse imports, symbols, APIs, and paths that appear in that evidence.
- For imports, use an exact MODULE_IMPORT_CANDIDATE or import statement shown
  in the evidence; do not add a package prefix based on guesswork.
- When a MODULE_CALLSITE_HINT is present, prefer its visible helper composition
  over inventing a constructor shape; do not treat the hint as permission.
- Do not invent module, function, class, or tool names from memory.
- If a required symbol is not shown, do not guess its name or import path.
- Every referenced name must be locally bound, imported by the generated unit,
  a safe Python builtin, or an explicitly declared evidence symbol. Do not rely
  on ambient names from the target file or from this prompt.

SURROUNDING CONTEXT:
{params.get("context") or ""}

Return only the generated code unit in a fenced code block.
"""


def _call_llm(
    llm_client: object,
    prompt: str,
    *,
    reasoning_policy: Any = None,
    max_tokens: int | None = None,
    runtime_budget: Any = None,
) -> str:
    request = build_context_llm_request(
        llm_client,
        messages=[LLMMessage(role="user", content=prompt)],
        purpose=ContextRequestPurpose.CODE_UNIT_GENERATION,
        response_format="text",
        temperature=0.2,
        max_tokens=max_tokens,
        reasoning_policy=reasoning_policy,
        user_truncation=ContextCandidateTruncation.FORBIDDEN,
    )
    if hasattr(llm_client, "complete"):
        reserved = False
        if runtime_budget is not None and max_tokens is not None:
            consume = getattr(runtime_budget, "consume_tool_event_completion", None)
            if callable(consume):
                consume(max_tokens)
                reserved = True
        try:
            response = llm_client.complete(request)
        except Exception:
            if reserved:
                reconcile = getattr(runtime_budget, "reconcile_tool_event_completion", None)
                if callable(reconcile):
                    reconcile(reserved=max_tokens or 0, actual=0)
            raise
        if reserved:
            usage = getattr(response, "usage", None)
            actual = usage.get("completion_tokens") if isinstance(usage, dict) else None
            if actual is None and isinstance(usage, dict):
                actual = usage.get("output_tokens")
            if actual is not None:
                reconcile = getattr(runtime_budget, "reconcile_tool_event_completion", None)
                if callable(reconcile):
                    reconcile(reserved=max_tokens or 0, actual=int(actual))
        finish_reason = str(getattr(response, "finish_reason", "") or "").lower()
        if finish_reason in {"length", "max_tokens"}:
            raise ValueError("Code unit generation reached its completion limit; truncated code is unsafe to apply")
        return str(response.content)
    if hasattr(llm_client, "generate"):
        return str(llm_client.generate("\n\n".join(message.content for message in request.messages)))
    if hasattr(llm_client, "chat"):
        return str(llm_client.chat([render_llm_message(message) for message in request.messages]))
    return str(llm_client("\n\n".join(message.content for message in request.messages)))


def _extract_code(raw_response: str, language: str) -> str:
    pattern = rf"```(?:{re.escape(language)}|python|bash|shell)?\s*\n(.*?)```"
    match = re.search(pattern, raw_response, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return raw_response.strip()


def _validate_python_unit(code: str) -> None:
    try:
        ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"Generated Python unit has syntax error on line {exc.lineno}: {exc.msg}") from exc


def _validate_grounded_python_unit(code: str, context: str) -> None:
    """Reject imports and names absent from authoritative evidence.

    Grounding is opt-in for nested provider calls.  Standalone code generation
    keeps its existing behavior, while a provider-grounded mutation fails
    closed before a writer can apply an invented package, API, or ambient name.
    """

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return
    allowed_modules, evidence_names = _grounded_evidence_symbols(context)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in allowed_modules:
                    violations.append(f"import:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = "." * int(node.level or 0) + str(node.module or "")
            if module not in allowed_modules:
                violations.append(f"from:{module}")
            for alias in node.names:
                if alias.name != "*" and alias.name not in evidence_names:
                    violations.append(f"name:{alias.name}")
    violations.extend(_unbound_grounded_names(tree, evidence_names))
    violations.extend(_unknown_grounded_test_fixture_parameters(tree, evidence_names))
    if violations:
        bounded = ", ".join(sorted(set(violations))[:8])
        raise ValueError(
            "generator grounding violation: generated imports or names "
            f"are absent from authoritative evidence ({bounded})"
        )


_GROUNDING_HEADER_LINES = {
    "AUTHORITATIVE DECLARED READ EVIDENCE",
    "Use only names, imports, APIs, and paths visible in these sections.",
}
_SAFE_MODULE_NAMES = {
    "__annotations__",
    "__builtins__",
    "__doc__",
    "__file__",
    "__loader__",
    "__name__",
    "__package__",
    "__spec__",
}

# These standard fixtures may be supplied by pytest without being defined in
# the target module. Other fixture-like parameters must be visible in the
# authoritative source evidence before an evidence-bound writer can apply a
# generated test.
_STANDARD_PYTEST_FIXTURES = frozenset(
    {
        "anyio_backend",
        "anyio_backend_name",
        "anyio_backend_options",
        "cache",
        "capfd",
        "capfdbinary",
        "caplog",
        "capsys",
        "capsysbinary",
        "capteesys",
        "doctest_namespace",
        "free_tcp_port",
        "free_tcp_port_factory",
        "monkeypatch",
        "pytestconfig",
        "record_property",
        "record_testsuite_property",
        "record_xml_attribute",
        "recwarn",
        "request",
        "subtests",
        "tmp_path",
        "tmp_path_factory",
        "tmpdir",
        "tmpdir_factory",
    }
)


def _grounded_evidence_symbols(context: str) -> tuple[set[str], set[str]]:
    """Return import/module and top-level symbol candidates from source evidence.

    Only the authoritative evidence section is parsed.  Task prose, provider
    explanations, and other prompt text are intentionally ignored so a name
    mentioned outside source evidence cannot become an ambient binding.
    """

    marker = "AUTHORITATIVE DECLARED READ EVIDENCE"
    if marker not in context:
        return set(), set()
    tail = context.split(marker, 1)[1]
    allowed_modules = {
        match.group(1)
        for match in re.finditer(
            r"^\s*MODULE_IMPORT_CANDIDATE:\s*([A-Za-z_][\w.]*)\s*$",
            tail,
            re.MULTILINE,
        )
    }
    evidence_names = {
        match.group(1)
        for match in re.finditer(
            r"^\s*MODULE_SYMBOL_CANDIDATE:\s*([A-Za-z_][A-Za-z0-9_]*)\s*$",
            tail,
            re.MULTILINE,
        )
    }

    # Every source entry is delimited by SOURCE_ID.  A compact fixture may
    # omit that label, so the tail itself remains a valid one-entry payload.
    entries = re.split(r"(?=^SOURCE_ID:\s*)", tail, flags=re.MULTILINE)
    for entry in entries:
        if not entry.strip():
            continue
        file_path_match = re.search(r"^FILE_PATH:\s*(.+?)\s*$", entry, re.MULTILINE)
        file_path = file_path_match.group(1).strip() if file_path_match else ""
        content_match = re.search(r"^CONTENT:\s*\n?(.*)$", entry, re.MULTILINE | re.DOTALL)
        if content_match:
            payload = content_match.group(1)
        else:
            payload_lines = []
            for line in entry.splitlines():
                stripped = line.strip()
                if not stripped or stripped in _GROUNDING_HEADER_LINES:
                    continue
                if stripped.startswith(("SOURCE_ID:", "FILE_PATH:", "EVIDENCE_STATUS:", "PROJECTION_STATUS:", "READ_WINDOW:")):
                    continue
                if stripped.startswith(("MODULE_IMPORT_CANDIDATE:", "MODULE_SYMBOL_CANDIDATE:")):
                    continue
                payload_lines.append(line)
            payload = "\n".join(payload_lines)
        if not payload.strip():
            continue
        modules, names = _parse_evidence_source(payload, file_path=file_path)
        allowed_modules.update(modules)
        evidence_names.update(names)
    return allowed_modules, evidence_names


def _parse_evidence_source(source: str, *, file_path: str = "") -> tuple[set[str], set[str]]:
    """Extract module imports and module-level bindings from one source excerpt."""

    modules: set[str] = set()
    names: set[str] = set()
    # Structured MODULE_* candidates are the authoritative view when CONTENT
    # is clipped.  Never let synthetic display markers reach AST/fallback
    # parsing and accidentally become evidence.
    if any(marker in source for marker in _EVIDENCE_PROJECTION_MARKERS):
        return modules, names
    try:
        tree = ast.parse(source)
    except (MemoryError, RecursionError, SyntaxError, ValueError):
        tree = None
    if tree is not None:
        nodes = list(tree.body)
    else:
        # Bounded windows may end mid-block.  Keep the fallback deliberately
        # narrow: only top-level declarations/imports can become evidence.
        nodes = []
        for line in source.splitlines():
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
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
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
        else:
            names.update(_bound_names_from_node(node))
    return modules, names


def _bound_names_from_node(node: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        names.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for child in node.elts:
            names.update(_bound_names_from_node(child))
    elif isinstance(node, ast.Starred):
        names.update(_bound_names_from_node(node.value))
    elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        else:
            targets = [node.target]
        for target in targets:
            names.update(_bound_names_from_node(target))
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        names.update(_bound_names_from_node(node.target))
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                names.update(_bound_names_from_node(item.optional_vars))
    elif isinstance(node, ast.ExceptHandler) and node.name:
        names.add(node.name)
    return names


def _unbound_grounded_names(tree: ast.AST, evidence_names: set[str]) -> list[str]:
    """Use Python's symbol table to reject unresolved global/free references."""

    try:
        table = symtable.symtable(ast.unparse(tree), "<generated-unit>", "exec")
    except (SyntaxError, ValueError):
        # Syntax was already validated; if the symbol table cannot be built,
        # fail closed rather than allowing an unverifiable mutation.
        return ["name:<symbol-table-unavailable>"]
    allowed = set(evidence_names) | set(dir(builtins)) | _SAFE_MODULE_NAMES
    violations: set[str] = set()

    def visit(scope: symtable.SymbolTable) -> None:
        for symbol in scope.get_symbols():
            name = symbol.get_name()
            if not symbol.is_referenced():
                continue
            if symbol.is_local() or symbol.is_free() or symbol.is_imported() or name in allowed:
                continue
            violations.add(f"unbound:{name}")
        for child in scope.get_children():
            visit(child)

    visit(table)
    return sorted(violations)


def _unknown_grounded_test_fixture_parameters(
    tree: ast.AST,
    evidence_names: set[str],
) -> list[str]:
    """Reject ambient pytest fixture parameters in evidence-bound test units.

    Function parameters are normally valid local bindings, so the general
    symbol-table check cannot distinguish a real function API from an
    invented pytest fixture. Restrict this additional check to ``test_*``
    functions and accept only source-visible names or pytest's standard
    fixture set. Ordinary non-test function parameters remain untouched.
    """

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        positional_parameters = [*node.args.posonlyargs, *node.args.args]
        positional_defaults = [False] * (
            len(positional_parameters) - len(node.args.defaults)
        ) + [True] * len(node.args.defaults)
        parameters = list(zip(positional_parameters, positional_defaults))
        parameters.extend(
            (parameter, default is not None)
            for parameter, default in zip(node.args.kwonlyargs, node.args.kw_defaults)
        )
        if node.args.vararg is not None:
            parameters.append((node.args.vararg, False))
        if node.args.kwarg is not None:
            parameters.append((node.args.kwarg, False))
        for parameter, has_default in parameters:
            name = parameter.arg
            if name in {"self", "cls"}:
                continue
            if has_default:
                continue
            if name in evidence_names or name in _STANDARD_PYTEST_FIXTURES:
                continue
            violations.append(f"fixture:{name}")
    return violations


def _extract_functions(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    return [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
