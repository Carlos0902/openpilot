from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_execution_dispatch import (
    ProviderExecutionDispatchError,
    dispatch_provider_execution_batch,
)
from core.provider_tool_admission import (
    ProviderToolBudgetUsage,
    admit_provider_mutation_tool_call,
    admit_provider_tool_call,
)
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
)
from metadata import RuntimeBudgetMetadata, ToolContractMetadata
from tools.file_patch_writer import FILE_PATCH_WRITER_DEFINITION


class _Registry:
    def __init__(self) -> None:
        reader = ToolDefinition(
            name="file_reader",
            display_name="file_reader",
            description="Read one file",
            permission_level=PermissionLevel.LOW,
            capabilities=[ToolCapability.FILE_READ],
            contract_metadata=ToolContractMetadata(
                tool_name="file_reader",
                input_metadata_type="ToolInputMetadata",
                output_metadata_type="ToolResultMetadata",
                required_input_fields=["file_path"],
            ),
        )
        validation = ToolDefinition(
            name="command_executor",
            display_name="command_executor",
            description="Run exact validation",
            permission_level=PermissionLevel.HIGH,
            capabilities=[ToolCapability.SHELL_EXECUTION],
            contract_metadata=ToolContractMetadata(
                tool_name="command_executor",
                input_metadata_type="ToolInputMetadata",
                output_metadata_type="ToolResultMetadata",
                required_input_fields=["command"],
            ),
        )
        self.definitions = {
            "file_reader": reader,
            "file_patch_writer": FILE_PATCH_WRITER_DEFINITION,
            "command_executor": validation,
        }
        self.executors = {name: object() for name in self.definitions}

    def get(self, name: str):
        return self.definitions.get(name)

    def get_executor(self, name: str):
        return self.executors.get(name)


def _read_admission():
    call = LLMToolCall(
        id="provider-reader",
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments='{"file_path":"README.md"}',
        ),
    )
    return admit_provider_tool_call(
        call,
        task_id="task",
        session_id="session",
        round_index=1,
        ordinal=1,
        registry=_Registry(),
        budget=RuntimeBudgetMetadata(),
        prior_usage=ProviderToolBudgetUsage(),
        user_confirmed=False,
        read_scope=["README.md"],
        project_path=None,
        validation_command=None,
    )


def _mutation_admission(*, confirmed: bool = True):
    call = LLMToolCall(
        id="provider-writer",
        function=LLMToolFunctionCall(
            name="file_patch_writer",
            arguments=json.dumps(
                {
                    "file_path": "app.py",
                    "operation_kind": "add_symbol",
                    "generated_unit": "def added():\n    return True\n",
                }
            ),
        ),
    )
    return admit_provider_mutation_tool_call(
        call,
        task_id="task",
        session_id="session",
        round_index=1,
        ordinal=2,
        registry=_Registry(),
        budget=RuntimeBudgetMetadata(),
        prior_usage=ProviderToolBudgetUsage(),
        user_confirmed=confirmed,
        allow_mutations=True,
        write_scope=["app.py"],
        project_path=None,
        validation_command="python -m compileall -q app.py",
    )


class _Runner:
    def __init__(self) -> None:
        self.owner = SimpleNamespace(_session_id=lambda: "session")
        self.calls = []

    def run_provider_tool_calls(self, task, admissions, *, round_index=1):
        self.calls.append(("read", task, admissions, round_index, {}))
        return "read-result"

    def run_provider_mutation_tool_calls(
        self,
        task,
        admissions,
        *,
        round_index=1,
        code_artifact_ledger=None,
        authorized_post_processing_write_scope=None,
    ):
        self.calls.append(
            (
                "mutation",
                task,
                admissions,
                round_index,
                {
                    "code_artifact_ledger": code_artifact_ledger,
                    "authorized_post_processing_write_scope": (
                        authorized_post_processing_write_scope
                    ),
                },
            )
        )
        return "mutation-result"


def _dispatch(runner, admissions, **kwargs):
    return dispatch_provider_execution_batch(
        runner,
        SimpleNamespace(id="task"),
        admissions,
        round_index=1,
        **kwargs,
    )


def test_dispatch_routes_readonly_batch_to_read_bridge() -> None:
    runner = _Runner()

    result = _dispatch(
        runner,
        [_read_admission()],
        allow_mutations=False,
    )

    assert result == "read-result"
    assert runner.calls[0][0] == "read"


def test_dispatch_routes_mixed_mutation_batch_with_exact_runtime_inputs() -> None:
    runner = _Runner()
    ledger = object()
    scope = ["app.py.index.json", "sketch.json"]

    result = _dispatch(
        runner,
        [_read_admission(), _mutation_admission()],
        allow_mutations=True,
        code_artifact_ledger=ledger,
        authorized_post_processing_write_scope=scope,
    )

    assert result == "mutation-result"
    route, _task, admissions, round_index, runtime_inputs = runner.calls[0]
    assert route == "mutation"
    assert [item.provider_call_id for item in admissions] == [
        "provider-reader",
        "provider-writer",
    ]
    assert round_index == 1
    assert runtime_inputs == {
        "code_artifact_ledger": ledger,
        "authorized_post_processing_write_scope": scope,
    }


def test_dispatch_rejects_unapproved_mutation_before_bridge_call() -> None:
    runner = _Runner()

    with pytest.raises(
        ProviderExecutionDispatchError,
        match="read-only",
    ):
        _dispatch(
            runner,
            [_mutation_admission()],
            allow_mutations=False,
        )

    assert runner.calls == []


def test_dispatch_routes_blocked_mutation_without_using_mutation_inputs() -> None:
    runner = _Runner()

    result = _dispatch(
        runner,
        [_mutation_admission(confirmed=False)],
        allow_mutations=False,
        code_artifact_ledger=object(),
        authorized_post_processing_write_scope=(item for item in ()),
    )

    assert result == "read-result"
    assert runner.calls[0][0] == "read"


@pytest.mark.parametrize("allow_mutations", [None, 0, 1, "true"])
def test_dispatch_requires_literal_mutation_control(allow_mutations) -> None:
    runner = _Runner()

    with pytest.raises(
        ProviderExecutionDispatchError,
        match="literal boolean",
    ):
        _dispatch(
            runner,
            [_read_admission()],
            allow_mutations=allow_mutations,
        )

    assert runner.calls == []
