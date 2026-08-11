from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.provider_tool_definitions import (
    ProviderToolDefinitionError,
    build_provider_tool_definitions,
)
from metadata import ToolContractMetadata


class _Registry:
    def __init__(self, definitions):
        self.definitions = definitions

    def get(self, name):
        return self.definitions.get(name)


def _definition(name: str = "file_patch_writer"):
    return SimpleNamespace(
        name=name,
        description="Apply one bounded patch",
        contract_metadata=ToolContractMetadata(
            tool_name=name,
            input_metadata_type="ToolInputMetadata",
            output_metadata_type="ToolResultMetadata",
            required_input_fields=["file_path"],
            required_any_of=[["replacement_text"], ["patch"]],
            input_defaults={"mode": "automatic"},
            conditional_requirements=[
                {
                    "when": {"operation_kind": "modify_symbol"},
                    "required_any_of": [["replacement_text"], ["patch"]],
                }
            ],
        ),
    )


def test_projection_exposes_only_declared_contract_fields() -> None:
    registry = _Registry({"file_patch_writer": _definition()})

    definition = build_provider_tool_definitions(
        registry, ["file_patch_writer"]
    )[0]
    parameters = definition.function.parameters

    assert definition.function.name == "file_patch_writer"
    assert set(parameters["properties"]) == {
        "file_path",
        "replacement_text",
        "patch",
        "mode",
        "operation_kind",
    }
    assert parameters["required"] == ["file_path"]
    assert parameters["anyOf"] == [
        {"required": ["replacement_text"]},
        {"required": ["patch"]},
    ]
    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["patch"] == {"type": "object"}
    assert parameters["properties"]["mode"] == {
        "type": "string",
        "enum": ["automatic", "execute", "run", "exec"],
        "default": "automatic",
    }
    assert parameters["allOf"][0]["if"]["required"] == ["operation_kind"]


@pytest.mark.parametrize("tool_names", [[""], ["missing"], ["tool", "tool"]])
def test_projection_rejects_empty_unknown_or_duplicate_names(tool_names) -> None:
    registry = _Registry({"tool": _definition("tool")})

    with pytest.raises(ProviderToolDefinitionError):
        build_provider_tool_definitions(registry, tool_names)


def test_projection_rejects_definition_without_typed_contract() -> None:
    registry = _Registry(
        {
            "untyped": SimpleNamespace(
                name="untyped",
                description="missing contract",
                contract_metadata=None,
            )
        }
    )

    with pytest.raises(ProviderToolDefinitionError, match="no typed contract"):
        build_provider_tool_definitions(registry, ["untyped"])


def test_projection_rejects_unbounded_tool_or_field_sets() -> None:
    definitions = {f"tool_{index}": _definition(f"tool_{index}") for index in range(33)}
    with pytest.raises(ProviderToolDefinitionError, match="at most 32"):
        build_provider_tool_definitions(_Registry(definitions), list(definitions))

    oversized = _definition("oversized")
    oversized.contract_metadata.required_input_fields = [
        f"field_{index}" for index in range(65)
    ]
    with pytest.raises(ProviderToolDefinitionError, match="at most 64"):
        build_provider_tool_definitions(
            _Registry({"oversized": oversized}),
            ["oversized"],
        )
