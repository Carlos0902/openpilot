"""Read-only projection of registered tool contracts into provider schemas."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.llm import LLMToolDefinition, LLMToolFunction

MAX_PROVIDER_TOOL_DEFINITIONS = 32
MAX_PROVIDER_FIELDS_PER_TOOL = 64


class ProviderToolDefinitionError(ValueError):
    """Raised when a registered tool cannot be exposed safely to a provider."""


def build_provider_tool_definitions(
    registry: Any,
    tool_names: Sequence[str],
) -> list[LLMToolDefinition]:
    """Project only declared typed contract fields into provider JSON schemas."""

    normalized_names = [str(name or "").strip() for name in tool_names]
    if any(not name for name in normalized_names):
        raise ProviderToolDefinitionError("provider tool names must be non-empty")
    if len(normalized_names) != len(set(normalized_names)):
        raise ProviderToolDefinitionError("provider tool names must be unique")
    if len(normalized_names) > MAX_PROVIDER_TOOL_DEFINITIONS:
        raise ProviderToolDefinitionError(
            f"provider requests may expose at most {MAX_PROVIDER_TOOL_DEFINITIONS} tools"
        )

    definitions: list[LLMToolDefinition] = []
    for tool_name in normalized_names:
        definition = getattr(registry, "get", lambda _name: None)(tool_name)
        if definition is None:
            raise ProviderToolDefinitionError(
                f"cannot expose unknown provider tool: {tool_name}"
            )
        contract = getattr(definition, "contract_metadata", None)
        if contract is None:
            raise ProviderToolDefinitionError(
                f"tool has no typed contract: {tool_name}"
            )

        required_fields = list(getattr(contract, "required_input_fields", []) or [])
        any_of = [list(group) for group in (getattr(contract, "required_any_of", []) or [])]
        defaults = dict(getattr(contract, "input_defaults", {}) or {})
        conditional_requirements = [
            requirement
            for requirement in (getattr(contract, "conditional_requirements", []) or [])
            if isinstance(requirement, dict)
        ]
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
        field_names: list[str] = []
        for field_name in [
            *required_fields,
            *[field for group in any_of for field in group],
            *defaults,
            *conditional_fields,
        ]:
            normalized = str(field_name)
            if normalized and normalized not in field_names:
                field_names.append(normalized)
        if len(field_names) > MAX_PROVIDER_FIELDS_PER_TOOL:
            raise ProviderToolDefinitionError(
                "provider tool schemas may expose at most "
                f"{MAX_PROVIDER_FIELDS_PER_TOOL} fields"
            )

        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                field_name: _provider_field_schema(
                    field_name, defaults.get(field_name)
                )
                for field_name in field_names
            },
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
            then_schema: dict[str, Any] = {}
            required = [str(field) for field in (requirement.get("required", []) or [])]
            if required:
                then_schema["required"] = required
            required_any_of = [
                [str(field) for field in group]
                for group in (requirement.get("required_any_of", []) or [])
            ]
            if required_any_of:
                then_schema["anyOf"] = [
                    {"required": group} for group in required_any_of
                ]
            if then_schema:
                conditional_schemas.append(
                    {
                        "if": {
                            "properties": {
                                str(field): {"const": value}
                                for field, value in when.items()
                            },
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


def _provider_field_schema(field_name: str, default: Any = None) -> dict[str, Any]:
    lowered = field_name.lower()
    if lowered == "mode":
        schema: dict[str, Any] = {
            "type": "string",
            "enum": ["automatic", "execute", "run", "exec"],
        }
    elif lowered.endswith(("_paths", "_files")) or lowered in {
        "files",
        "file_paths",
    }:
        schema = {"type": "array", "items": {"type": "string"}}
    elif lowered in {
        "recursive",
        "overwrite",
        "create_dirs",
        "follow_redirects",
        "safe_search",
    }:
        schema = {"type": "boolean"}
    elif lowered.endswith(
        ("_lines", "_size_mb", "_tokens", "_results", "_pages", "_seconds", "_depth", "_offset")
    ) or lowered in {"timeout", "limit"}:
        schema = {"type": "integer"}
    elif lowered in {"patch", "attributes", "validation_context", "artifact_ref"}:
        schema = {"type": "object"}
    else:
        schema = {"type": "string"}
    if default is not None:
        schema["default"] = default
    return schema


__all__ = [
    "MAX_PROVIDER_FIELDS_PER_TOOL",
    "MAX_PROVIDER_TOOL_DEFINITIONS",
    "ProviderToolDefinitionError",
    "build_provider_tool_definitions",
]
