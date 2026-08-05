"""Code Unit Generator Tool - Generate a function/class/module fragment."""

from __future__ import annotations

import ast
import re
import uuid

from core.llm import LLMMessage
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
        generated = _extract_code(_call_llm(llm_client, _build_prompt(params, language)), language).strip()

    if language == "python":
        _validate_python_unit(generated)

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

SURROUNDING CONTEXT:
{params.get("context") or ""}

Return only the generated code unit in a fenced code block.
"""


def _call_llm(llm_client: object, prompt: str) -> str:
    request = build_context_llm_request(
        llm_client,
        messages=[LLMMessage(role="user", content=prompt)],
        purpose=ContextRequestPurpose.CODE_UNIT_GENERATION,
        response_format="text",
        temperature=0.2,
        user_truncation=ContextCandidateTruncation.FORBIDDEN,
    )
    if hasattr(llm_client, "complete"):
        response = llm_client.complete(request)
        return str(response.content)
    if hasattr(llm_client, "generate"):
        return str(llm_client.generate("\n\n".join(message.content for message in request.messages)))
    if hasattr(llm_client, "chat"):
        return str(llm_client.chat([message.model_dump() for message in request.messages]))
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


def _extract_functions(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    return [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
