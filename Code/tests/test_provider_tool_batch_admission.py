from __future__ import annotations

import json

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_tool_admission import ProviderToolAdmissionError
from core.provider_tool_batch_admission import (
    MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
    admit_provider_tool_calls,
)
from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition
from metadata import RuntimeBudgetMetadata, ToolContractMetadata


class _Registry:
    def __init__(self):
        self.definitions = {}
        self.executors = {}

    def register(self, definition):
        self.definitions[definition.name] = definition
        self.executors[definition.name] = object()

    def get(self, name):
        return self.definitions.get(name)

    def get_executor(self, name):
        return self.executors.get(name)


def _definition(name, capability, *, required, permission=PermissionLevel.LOW):
    return ToolDefinition(
        name=name,
        display_name=name,
        description=f"Execute {name}",
        permission_level=permission,
        capabilities=[capability],
        contract_metadata=ToolContractMetadata(
            tool_name=name,
            input_metadata_type="ToolInputMetadata",
            output_metadata_type="ToolResultMetadata",
            required_input_fields=list(required),
        ),
    )


def _registry():
    registry = _Registry()
    registry.register(
        _definition(
            "file_reader",
            ToolCapability.FILE_READ,
            required=("file_path",),
        )
    )
    registry.register(
        _definition(
            "file_patch_writer",
            ToolCapability.FILE_WRITE,
            required=("file_path",),
            permission=PermissionLevel.MEDIUM,
        )
    )
    registry.register(
        _definition(
            "command_executor",
            ToolCapability.SHELL_EXECUTION,
            required=("command",),
            permission=PermissionLevel.MEDIUM,
        )
    )
    return registry


def _call(name, arguments, call_id):
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(
            name=name,
            arguments=json.dumps(arguments),
        ),
    )


def _admit(calls, **updates):
    values = {
        "task_id": "task",
        "session_id": "session",
        "round_index": 1,
        "registry": _registry(),
        "budget": RuntimeBudgetMetadata(),
        "user_confirmed": True,
        "allow_mutations": False,
        "read_scope": ["a.py", "b.py"],
        "write_scope": ["app.py"],
        "project_path": None,
        "validation_command": "python -m compileall -q app.py",
    }
    values.update(updates)
    return admit_provider_tool_calls(calls, **values)


def test_batch_assigns_project_ids_and_accumulates_call_budget() -> None:
    calls = [
        _call("file_reader", {"file_path": "a.py"}, "provider-1"),
        _call("file_reader", {"file_path": "b.py"}, "provider-2"),
    ]
    admissions = _admit(
        calls,
        budget=RuntimeBudgetMetadata(max_tool_calls=1),
    )

    assert [item.project_call_id for item in admissions] == [
        "task:r1:c1",
        "task:r1:c2",
    ]
    assert admissions[0].status == "admitted"
    assert admissions[1].tool_error.error_type == "ToolBudgetExhausted"


def test_blocked_call_does_not_consume_batch_budget() -> None:
    calls = [
        _call("file_patch_writer", {"file_path": "app.py"}, "provider-1"),
        _call("file_reader", {"file_path": "a.py"}, "provider-2"),
    ]
    admissions = _admit(
        calls,
        budget=RuntimeBudgetMetadata(max_tool_calls=1),
    )

    assert admissions[0].tool_error.error_type == "PermissionDenied"
    assert admissions[1].status == "admitted"


def test_batch_admits_patch_then_exact_validation() -> None:
    command = "python -m compileall -q app.py"
    calls = [
        _call("file_patch_writer", {"file_path": "app.py"}, "provider-1"),
        _call("command_executor", {"command": command}, "provider-2"),
    ]
    admissions = _admit(
        calls,
        allow_mutations=True,
        validation_command=command,
        budget=RuntimeBudgetMetadata(
            max_file_edits=1,
            max_verification_attempts=1,
        ),
    )

    assert [item.status for item in admissions] == ["admitted", "admitted"]
    assert admissions[1].selection.input_metadata.mode == "automatic"


def test_batch_allows_only_one_validation_command() -> None:
    command = "python -m compileall -q app.py"
    calls = [
        _call("command_executor", {"command": command}, "provider-1"),
        _call("command_executor", {"command": command}, "provider-2"),
    ]
    admissions = _admit(calls, validation_command=command)

    assert admissions[0].status == "admitted"
    assert (
        admissions[1].tool_error.error_type
        == "ProviderToolValidationDuplicate"
    )


def test_batch_rejects_duplicate_provider_ids() -> None:
    calls = [
        _call("file_reader", {"file_path": "a.py"}, "provider-1"),
        _call("file_reader", {"file_path": "b.py"}, "provider-1"),
    ]

    with pytest.raises(ProviderToolAdmissionError):
        _admit(calls)


def test_batch_rejects_more_than_static_call_limit() -> None:
    calls = [
        _call("file_reader", {"file_path": "a.py"}, f"provider-{index}")
        for index in range(MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE + 1)
    ]

    with pytest.raises(ProviderToolAdmissionError):
        _admit(calls)


def test_batch_accepts_exact_static_call_limit() -> None:
    calls = [
        _call("file_reader", {"file_path": "a.py"}, f"provider-{index}")
        for index in range(MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE)
    ]
    admissions = _admit(
        calls,
        budget=RuntimeBudgetMetadata(
            max_tool_calls=MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
            max_file_reads=MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
        ),
    )

    assert len(admissions) == MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE
    assert all(item.status == "admitted" for item in admissions)


@pytest.mark.parametrize(
    "updates",
    [
        {"task_id": ""},
        {"session_id": ""},
        {"round_index": 0},
        {"user_confirmed": 1},
        {"allow_mutations": 1},
        {"validation_commands_used": -1},
    ],
)
def test_batch_entry_rejects_invalid_authority_inputs(updates) -> None:
    with pytest.raises(ProviderToolAdmissionError):
        _admit([], **updates)
