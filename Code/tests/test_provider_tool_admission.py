import json

import pytest

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_tool_admission import (
    ProviderToolAdmissionError,
    admit_provider_tool_calls,
    provider_tool_error,
)
from core.provider_tool_roundtrip import build_provider_tool_definitions
from core.tool_contracts import PermissionLevel, ToolCapability, ToolDefinition
from metadata import RuntimeBudgetMetadata, ToolCallMetadata, ToolContractMetadata, ToolInputMetadata
from tools.tool_registry import ToolRegistry
from tools.file_patch_writer import FILE_PATCH_WRITER_DEFINITION, file_patch_writer_executor


def _registry(
    *,
    name: str = "file_reader",
    capabilities: list[ToolCapability] | None = None,
    permission: PermissionLevel = PermissionLevel.LOW,
    required: list[str] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    definition = ToolDefinition(
        name=name,
        display_name=name,
        description=f"{name} test tool",
        capabilities=capabilities or [ToolCapability.FILE_READ],
        permission_level=permission,
        contract_metadata=ToolContractMetadata(
            tool_name=name,
            input_metadata_type="ToolInputMetadata",
            output_metadata_type="ToolResultMetadata",
            required_input_fields=required or [],
        ),
    )
    registry.register(definition, lambda _input: None)
    return registry


def _call(name: str = "file_reader", arguments: str = '{"file_path":"README.md"}', call_id: str = "provider_1") -> LLMToolCall:
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(name=name, arguments=arguments),
    )


def _patch_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FILE_PATCH_WRITER_DEFINITION, file_patch_writer_executor)
    return registry


def test_file_patch_writer_provider_schema_exposes_conditional_modify_fields() -> None:
    definition = build_provider_tool_definitions(_patch_registry(), ["file_patch_writer"])[0]
    parameters = definition.function.parameters

    assert {"file_path", "operation_kind", "symbol_name", "replacement_text", "patch"}.issubset(
        parameters["properties"]
    )
    assert parameters["required"] == ["file_path"]
    modify_condition = next(
        condition
        for condition in parameters["allOf"]
        if condition["if"]["properties"]["operation_kind"] == {"const": "modify_symbol"}
    )
    assert modify_condition["then"]["required"] == ["symbol_name"]
    assert modify_condition["then"]["anyOf"] == [
        {"required": ["replacement_text"]},
        {"required": ["patch"]},
    ]


def test_file_patch_writer_admission_enforces_conditional_modify_fields(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    registry = _patch_registry()
    common = {
        "task_id": "task-patch-contract",
        "session_id": "session-patch-contract",
        "round_index": 1,
        "registry": registry,
        "budget": RuntimeBudgetMetadata(),
        "user_confirmed": True,
        "write_scope": [str(target)],
        "project_path": str(tmp_path),
    }

    missing = admit_provider_tool_calls(
        [_call(
            name="file_patch_writer",
            arguments='{"file_path":"calculator.py","operation_kind":"modify_symbol","symbol_name":"divide"}',
            call_id="patch_missing",
        )],
        **common,
    )[0]
    assert missing.status == "blocked"
    assert missing.tool_error is not None
    assert missing.tool_error.error_type == "MissingRequiredInput"

    omitted_operation = admit_provider_tool_calls(
        [_call(
            name="file_patch_writer",
            arguments='{"file_path":"calculator.py"}',
            call_id="patch_default_missing",
        )],
        **common,
    )[0]
    assert omitted_operation.status == "blocked"
    assert omitted_operation.tool_error is not None
    assert omitted_operation.tool_error.error_type == "MissingRequiredInput"

    valid = admit_provider_tool_calls(
        [_call(
            name="file_patch_writer",
            arguments=(
                '{"file_path":"calculator.py","operation_kind":"modify_symbol",'
                '"symbol_name":"divide","replacement_text":"def divide(a, b):\\n    return a / b"}'
            ),
            call_id="patch_valid",
        )],
        **common,
    )[0]
    assert valid.status == "admitted"
    assert valid.selection is not None
    assert valid.selection.input_metadata.symbol_name == "divide"
    assert valid.selection.input_metadata.replacement_text


def test_provider_call_binds_external_id_without_replacing_project_call_id() -> None:
    admissions = admit_provider_tool_calls(
        [_call()],
        task_id="task_1",
        session_id="session_1",
        round_index=2,
        registry=_registry(required=["file_path"]),
        budget=RuntimeBudgetMetadata(),
    )

    admission = admissions[0]
    assert admission.status == "admitted"
    assert admission.provider_call_id == "provider_1"
    assert admission.project_call_id == "task_1:r2:c1"
    assert admission.tool_call.provider_call_id == "provider_1"
    assert admission.tool_call.call_id == "task_1:r2:c1"
    assert admission.selection is not None
    assert admission.selection.requires_confirmation is False
    assert admission.selection.input_metadata.file_path == "README.md"


def test_provider_read_scope_allows_explicit_relative_path(tmp_path) -> None:
    project = tmp_path / "project"
    target = project / "README.md"
    registry = _registry(required=["file_path"])

    admission = admit_provider_tool_calls(
        [_call(arguments='{"file_path":"README.md"}')],
        task_id="task_scope",
        session_id="session_scope",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        read_scope=[str(target)],
        project_path=str(project),
    )[0]

    assert admission.status == "admitted"


def test_provider_read_scope_blocks_path_outside_explicit_files(tmp_path) -> None:
    project = tmp_path / "project"
    registry = _registry(required=["file_path"])

    admission = admit_provider_tool_calls(
        [_call(arguments='{"file_path":"src/main.py"}')],
        task_id="task_scope",
        session_id="session_scope",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        read_scope=[str(project / "README.md")],
        project_path=str(project),
    )[0]

    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "ProviderToolScopeViolation"
    assert admission.tool_error.recoverable is True


@pytest.mark.parametrize(
    "arguments,error_type",
    [
        ('{"file_path":', "InvalidToolArguments"),
        ('[]', "InvalidToolArguments"),
        ('{}', "MissingRequiredInput"),
    ],
)
def test_provider_call_rejects_bad_arguments_before_execution(arguments: str, error_type: str) -> None:
    admission = admit_provider_tool_calls(
        [_call(arguments=arguments)],
        task_id="task_1",
        session_id="session_1",
        round_index=1,
        registry=_registry(required=["file_path"]),
        budget=RuntimeBudgetMetadata(),
    )[0]

    assert admission.status == "blocked"
    assert admission.selection is None
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == error_type
    assert admission.tool_error.provider_call_id == "provider_1"


def test_provider_call_rejects_unknown_and_forbidden_tools() -> None:
    unknown = admit_provider_tool_calls(
        [_call(name="not_registered")],
        task_id="task_1",
        session_id="session_1",
        round_index=1,
        registry=_registry(),
        budget=RuntimeBudgetMetadata(),
    )[0]
    forbidden = admit_provider_tool_calls(
        [_call(name="delete_file")],
        task_id="task_1",
        session_id="session_1",
        round_index=1,
        registry=_registry(
            name="delete_file",
            capabilities=[ToolCapability.FILE_DELETE],
            permission=PermissionLevel.FORBIDDEN,
        ),
        budget=RuntimeBudgetMetadata(),
    )[0]

    assert unknown.tool_error is not None and unknown.tool_error.error_type == "UnknownTool"
    assert forbidden.tool_error is not None and forbidden.tool_error.error_type == "PermissionDenied"
    assert forbidden.tool_error.recoverable is False


def test_provider_mutation_requires_confirmation_and_budget_is_checked_without_consuming() -> None:
    registry = _registry(
        name="file_writer",
        capabilities=[ToolCapability.FILE_WRITE],
        permission=PermissionLevel.MEDIUM,
        required=["file_path", "content"],
    )
    budget = RuntimeBudgetMetadata(max_file_edits=1)
    call = _call(
        name="file_writer",
        arguments='{"file_path":"calculator.py","content":"pass"}',
    )

    blocked = admit_provider_tool_calls(
        [call],
        task_id="task_1",
        session_id="session_1",
        round_index=1,
        registry=registry,
        budget=budget,
    )[0]
    admitted = admit_provider_tool_calls(
        [call],
        task_id="task_1",
        session_id="session_1",
        round_index=1,
        registry=registry,
        budget=budget,
        user_confirmed=True,
    )[0]

    assert blocked.tool_error is not None
    assert blocked.tool_error.error_type == "UserConfirmationRequired"
    assert admitted.status == "admitted"
    assert budget.tool_calls_used == 0
    assert budget.file_edits_used == 0

    exhausted = admit_provider_tool_calls(
        [call],
        task_id="task_1",
        session_id="session_1",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(max_file_edits=0),
        user_confirmed=True,
    )[0]
    assert exhausted.tool_error is not None
    assert exhausted.tool_error.error_type == "ToolBudgetExhausted"


def test_provider_mutation_read_scope_is_enforced_even_when_write_is_enabled(tmp_path) -> None:
    registry = ToolRegistry()
    definition = ToolDefinition(
        name="file_reader",
        display_name="file_reader",
        description="read",
        capabilities=[ToolCapability.FILE_READ],
        permission_level=PermissionLevel.LOW,
        contract_metadata=ToolContractMetadata(
            tool_name="file_reader",
            input_metadata_type="ToolInputMetadata",
            output_metadata_type="ToolResultMetadata",
            required_input_fields=["file_path"],
        ),
    )
    registry.register(definition, lambda _input: None)
    admission = admit_provider_tool_calls(
        [_call(arguments='{"file_path":"tests/test_calculator.py"}')],
        task_id="task_scope_mutation",
        session_id="session_scope_mutation",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        user_confirmed=True,
        read_scope=[str(tmp_path / "calculator.py")],
        write_scope=[str(tmp_path / "calculator.py")],
        project_path=str(tmp_path),
    )[0]
    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "ProviderToolScopeViolation"


def test_provider_mutation_write_scope_denies_unlisted_target(tmp_path) -> None:
    registry = _registry(
        name="file_writer",
        capabilities=[ToolCapability.FILE_WRITE],
        permission=PermissionLevel.MEDIUM,
        required=["file_path", "content"],
    )
    admission = admit_provider_tool_calls(
        [_call(name="file_writer", arguments='{"file_path":"README.md","content":"no"}')],
        task_id="task_write_scope",
        session_id="session_write_scope",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(max_file_edits=1),
        user_confirmed=True,
        write_scope=[str(tmp_path / "calculator.py")],
        project_path=str(tmp_path),
    )[0]
    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "ProviderToolWriteScopeViolation"


def test_provider_scope_rejects_symlink_authority_and_target(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("pass", encoding="utf-8")
    link = tmp_path / "alias.py"
    link.symlink_to(target)
    registry = _registry(required=["file_path"])
    admission = admit_provider_tool_calls(
        [_call(arguments='{"file_path":"alias.py"}')],
        task_id="task_symlink_scope",
        session_id="session_symlink_scope",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        read_scope=[str(link)],
        project_path=str(tmp_path),
    )[0]
    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "ProviderToolScopeViolation"


def test_provider_read_scope_rejects_symlink_authority_for_real_target(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("pass", encoding="utf-8")
    link = tmp_path / "alias.py"
    link.symlink_to(target)
    registry = _registry(required=["file_path"])

    admission = admit_provider_tool_calls(
        [_call(arguments='{"file_path":"calculator.py"}')],
        task_id="task_reverse_symlink_read_scope",
        session_id="session_reverse_symlink_read_scope",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        read_scope=[str(link)],
        project_path=str(tmp_path),
    )[0]

    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "ProviderToolScopeViolation"


def test_provider_write_scope_rejects_symlink_authority_for_real_target(tmp_path) -> None:
    target = tmp_path / "calculator.py"
    target.write_text("pass", encoding="utf-8")
    link = tmp_path / "alias.py"
    link.symlink_to(target)
    registry = _registry(
        name="file_writer",
        capabilities=[ToolCapability.FILE_WRITE],
        permission=PermissionLevel.MEDIUM,
        required=["file_path", "content"],
    )

    admission = admit_provider_tool_calls(
        [_call(name="file_writer", arguments='{"file_path":"calculator.py","content":"updated"}')],
        task_id="task_reverse_symlink_write_scope",
        session_id="session_reverse_symlink_write_scope",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(max_file_edits=1),
        user_confirmed=True,
        write_scope=[str(link)],
        project_path=str(tmp_path),
    )[0]

    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "ProviderToolWriteScopeViolation"


def test_provider_validation_command_is_exact_and_not_repeated(tmp_path) -> None:
    registry = _registry(
        name="command_executor",
        capabilities=[ToolCapability.SHELL_EXECUTION],
        permission=PermissionLevel.HIGH,
        required=["command"],
    )
    common = dict(
        task_id="task_validation",
        session_id="session_validation",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        user_confirmed=True,
        project_path=str(tmp_path),
        validation_command="python -m pytest -q tests/test_calculator.py",
        validation_cwd=str(tmp_path),
    )
    wrong = admit_provider_tool_calls(
        [_call(name="command_executor", arguments='{"command":"python -m pytest -q"}')],
        **common,
    )[0]
    assert wrong.status == "blocked"
    assert wrong.tool_error is not None
    assert wrong.tool_error.error_type == "ProviderToolValidationViolation"

    valid_call = _call(
        name="command_executor",
        arguments='{"command":"python -m pytest -q tests/test_calculator.py"}',
        call_id="validation-1",
    )
    admitted = admit_provider_tool_calls([valid_call], **common)[0]
    assert admitted.status == "admitted"
    assert admitted.selection is not None
    assert admitted.selection.input_metadata.mode == "automatic"

    dry_run = _call(
        name="command_executor",
        arguments='{"command":"python -m pytest -q tests/test_calculator.py","mode":"dry_run"}',
        call_id="validation-dry-run",
    )
    dry_run_admission = admit_provider_tool_calls([dry_run], **common)[0]
    assert dry_run_admission.status == "blocked"
    assert dry_run_admission.tool_error is not None
    assert dry_run_admission.tool_error.error_type == "ProviderToolValidationViolation"

    repeated = admit_provider_tool_calls(
        [valid_call],
        **common,
        validation_commands_used=1,
    )[0]
    assert repeated.status == "blocked"
    assert repeated.tool_error is not None
    assert repeated.tool_error.error_type == "ProviderToolValidationDuplicate"


@pytest.mark.parametrize(
    "command",
    [
        "cd /project && python -m pytest -q tests/test_calculator.py",
        "cd /tmp && python -m pytest -q tests/test_calculator.py",
        "python -m pytest -q tests/test_calculator.py && echo extra",
        "python -m pytest -q tests/test_calculator.py; echo extra",
        "python -m pytest -q tests/test_calculator.py | tee result.txt",
        "python -m pytest -q tests/test_calculator.py > result.txt",
        "python -m pytest -q tests/test_calculator.py $(echo extra)",
    ],
)
def test_provider_validation_rejects_shell_wrappers_and_extra_operations(tmp_path, command: str) -> None:
    registry = _registry(
        name="command_executor",
        capabilities=[ToolCapability.SHELL_EXECUTION],
        permission=PermissionLevel.HIGH,
        required=["command"],
    )
    admission = admit_provider_tool_calls(
        [_call(name="command_executor", arguments=json.dumps({"command": command}))],
        task_id="task-validation-boundary",
        session_id="session-validation-boundary",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        user_confirmed=True,
        project_path=str(tmp_path),
        validation_command="python -m pytest -q tests/test_calculator.py",
        validation_cwd=str(tmp_path),
    )[0]

    assert admission.status == "blocked"
    assert admission.tool_error is not None
    assert admission.tool_error.error_type == "ProviderToolValidationViolation"


def test_provider_validation_accepts_missing_cwd_for_roundtrip_binding(tmp_path) -> None:
    registry = _registry(
        name="command_executor",
        capabilities=[ToolCapability.SHELL_EXECUTION],
        permission=PermissionLevel.HIGH,
        required=["command"],
    )
    admission = admit_provider_tool_calls(
        [
            _call(
                name="command_executor",
                arguments=json.dumps({"command": "python -m pytest -q tests/test_calculator.py"}),
            )
        ],
        task_id="task-validation-cwd-optional",
        session_id="session-validation-cwd-optional",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        user_confirmed=True,
        project_path=str(tmp_path),
        validation_command="python -m pytest -q tests/test_calculator.py",
        validation_cwd=str(tmp_path),
    )[0]

    assert admission.status == "admitted"
    assert admission.selection is not None
    assert admission.selection.input_metadata.cwd is None


def test_provider_validation_rejects_wrong_cwd_but_accepts_canonical_same_root(tmp_path) -> None:
    registry = _registry(
        name="command_executor",
        capabilities=[ToolCapability.SHELL_EXECUTION],
        permission=PermissionLevel.HIGH,
        required=["command"],
    )
    common = dict(
        task_id="task-validation-cwd-boundary",
        session_id="session-validation-cwd-boundary",
        round_index=1,
        registry=registry,
        budget=RuntimeBudgetMetadata(),
        user_confirmed=True,
        project_path=str(tmp_path),
        validation_command="python -m pytest -q tests/test_calculator.py",
        validation_cwd=str(tmp_path),
    )

    wrong = admit_provider_tool_calls(
        [
            _call(
                name="command_executor",
                arguments=json.dumps(
                    {
                        "command": "python -m pytest -q tests/test_calculator.py",
                        "cwd": str(tmp_path / "other"),
                    }
                ),
            )
        ],
        **common,
    )[0]
    assert wrong.status == "blocked"
    assert wrong.tool_error is not None
    assert wrong.tool_error.error_type == "ProviderToolValidationViolation"

    canonical = admit_provider_tool_calls(
        [
            _call(
                name="command_executor",
                arguments=json.dumps(
                    {
                        "command": "python -m pytest -q tests/test_calculator.py",
                        "cwd": str(tmp_path),
                    }
                ),
                call_id="validation-cwd-canonical",
            )
        ],
        **common,
    )[0]
    assert canonical.status == "admitted"


def test_provider_batch_duplicate_ids_fail_closed() -> None:
    with pytest.raises(ProviderToolAdmissionError, match="IDs must be unique"):
        admit_provider_tool_calls(
            [_call(call_id="same"), _call(call_id="same")],
            task_id="task_1",
            session_id="session_1",
            round_index=1,
            registry=_registry(),
            budget=RuntimeBudgetMetadata(),
        )


def test_provider_execution_failure_preserves_both_correlation_ids() -> None:
    tool_call = ToolCallMetadata(
        session_id="session_1",
        task_id="task_1",
        step_id="step_1_1",
        call_id="task_1:r1:c1",
        provider_call_id="provider_1",
        tool_name="file_reader",
        input_metadata=ToolInputMetadata(tool_name="file_reader", file_path="README.md"),
    )
    error = provider_tool_error(
        tool_call,
        error_type="ToolExecutionFailed",
        error_message="read failed",
        recoverable=True,
        suggested_recovery="retry read",
    )

    assert error.call_id == "task_1:r1:c1"
    assert error.provider_call_id == "provider_1"
    assert error.failure is not None
    assert error.failure.details["provider_call_id"] == "provider_1"
