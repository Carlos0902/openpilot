from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_tool_batch_admission import admit_provider_tool_calls
from core.provider_tool_execution_batch import (
    ProviderToolExecutionBatchError,
    validated_readonly_provider_execution_batch,
)
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
)
from metadata import RuntimeBudgetMetadata, ToolContractMetadata
from tools.tool_registry import ToolRegistry


def _call(
    *,
    call_id: str = "provider-call-1",
    arguments: str = '{"file_path":"README.md"}',
) -> LLMToolCall:
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(
            name="file_reader",
            arguments=arguments,
        ),
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    definition = ToolDefinition(
        name="file_reader",
        display_name="file_reader",
        description="test provider reader",
        capabilities=[ToolCapability.FILE_READ],
        permission_level=PermissionLevel.LOW,
        contract_metadata=ToolContractMetadata(
            tool_name="file_reader",
            input_metadata_type="ToolInputMetadata",
            output_metadata_type="ToolResultMetadata",
            required_input_fields=["file_path"],
            capabilities=[ToolCapability.FILE_READ.value],
            permission_level=PermissionLevel.LOW.value,
        ),
    )
    registry.register(definition, lambda _input: None)
    return registry


def _admissions(*calls: LLMToolCall, task_id: str = "task"):
    return admit_provider_tool_calls(
        list(calls or [_call()]),
        task_id=task_id,
        session_id="session",
        round_index=1,
        registry=_registry(),
        budget=RuntimeBudgetMetadata(),
        read_scope=["README.md"],
    )


def test_execution_batch_accepts_exact_current_readonly_identity() -> None:
    admissions = _admissions(_call())

    validated = validated_readonly_provider_execution_batch(
        admissions,
        task_id="task",
        session_id="session",
        round_index=1,
    )

    assert validated == tuple(admissions)


def test_execution_batch_rejects_unbounded_iterables() -> None:
    admission = _admissions(_call())[0]

    with pytest.raises(
        ProviderToolExecutionBatchError,
        match="bounded list or tuple",
    ):
        validated_readonly_provider_execution_batch(
            (item for item in [admission]),
            task_id="task",
            session_id="session",
            round_index=1,
        )


def test_execution_batch_accepts_exact_call_boundary() -> None:
    admissions = _admissions(
        *[
            _call(call_id=f"provider-{index}")
            for index in range(32)
        ]
    )

    validated = validated_readonly_provider_execution_batch(
        admissions,
        task_id="task",
        session_id="session",
        round_index=1,
    )

    assert len(validated) == 32


def test_execution_batch_rejects_call_overflow_before_iteration() -> None:
    admission = _admissions(_call())[0]

    with pytest.raises(
        ProviderToolExecutionBatchError,
        match="per-response call limit",
    ):
        validated_readonly_provider_execution_batch(
            [admission] * 33,
            task_id="task",
            session_id="session",
            round_index=1,
        )


@pytest.mark.parametrize("round_index", [0, -1, True])
def test_execution_batch_rejects_invalid_round_index(round_index: object) -> None:
    with pytest.raises(
        ProviderToolExecutionBatchError,
        match="positive integer",
    ):
        validated_readonly_provider_execution_batch(
            [],
            task_id="task",
            session_id="session",
            round_index=round_index,
        )


def test_execution_batch_rejects_duplicate_provider_identity() -> None:
    admission = _admissions(_call())[0]

    with pytest.raises(
        ProviderToolExecutionBatchError,
        match="unique provider call IDs",
    ):
        validated_readonly_provider_execution_batch(
            [admission, admission],
            task_id="task",
            session_id="session",
            round_index=1,
        )


def test_execution_batch_rejects_duplicate_project_identity() -> None:
    first, second = _admissions(
        _call(call_id="provider-1"),
        _call(call_id="provider-2"),
    )
    duplicate_project = second.model_copy(
        update={
            "project_call_id": first.project_call_id,
            "tool_call": second.tool_call.model_copy(
                update={"call_id": first.project_call_id}
            ),
        }
    )

    with pytest.raises(
        ProviderToolExecutionBatchError,
        match="unique project call IDs",
    ):
        validated_readonly_provider_execution_batch(
            [first, duplicate_project],
            task_id="task",
            session_id="session",
            round_index=1,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"task_id": "other-task"},
        {"session_id": "other-session"},
        {"round_index": 2},
    ],
)
def test_execution_batch_rejects_cross_lifecycle_admissions(
    updates: dict[str, object],
) -> None:
    admission = _admissions(_call())[0]
    mismatched = admission.model_copy(
        update={
            "tool_call": admission.tool_call.model_copy(update=updates),
        }
    )

    with pytest.raises(
        ProviderToolExecutionBatchError,
        match="current task, session, and round",
    ):
        validated_readonly_provider_execution_batch(
            [mismatched],
            task_id="task",
            session_id="session",
            round_index=1,
        )


def test_execution_batch_rejects_mutation_in_either_admission_view() -> None:
    admission = _admissions(_call())[0]
    mutation = admission.model_copy(
        update={
            "selection": admission.selection.model_copy(
                update={"tool_name": "file_patch_writer"}
            ),
        }
    )

    with pytest.raises(
        ProviderToolExecutionBatchError,
        match="read-only",
    ):
        validated_readonly_provider_execution_batch(
            [mutation],
            task_id="task",
            session_id="session",
            round_index=1,
        )


def test_execution_batch_rejects_non_admission_values() -> None:
    with pytest.raises(
        ProviderToolExecutionBatchError,
        match="ProviderToolAdmission",
    ):
        validated_readonly_provider_execution_batch(
            [SimpleNamespace()],
            task_id="task",
            session_id="session",
            round_index=1,
        )
