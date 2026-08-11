from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from autonomous_iteration.runtime_controller import StateUpdater
from autonomous_iteration.task_models import Task
from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_tool_admission import (
    ProviderToolBudgetUsage,
    admit_provider_mutation_tool_call,
)
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
)
from core.tool_event_loop import ToolEventLoopRunner
from metadata import (
    CodeArtifactMetadata,
    ResultStatus,
    RuntimeBudgetMetadata,
    RuntimeStateMetadata,
    TextArtifactMetadata,
    ToolContractMetadata,
    ToolResultMetadata,
)
from tools.file_patch_writer import FILE_PATCH_WRITER_DEFINITION


_VALIDATION = "python -m compileall -q app.py"


class _Registry:
    def __init__(self) -> None:
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
            "file_patch_writer": FILE_PATCH_WRITER_DEFINITION,
            "command_executor": validation,
        }
        self.executors = {name: object() for name in self.definitions}

    def get(self, name: str):
        return self.definitions.get(name)

    def get_executor(self, name: str):
        return self.executors.get(name)


class _Owner:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.logs = []

    def _session_id(self) -> str:
        return "session"

    def _log(self, *args, **kwargs) -> None:
        self.logs.append((args, kwargs))

    def _show_tool_running(self, *args, **kwargs) -> None:
        return None

    def _show_tool_result(self, *args, **kwargs) -> None:
        return None

    def _log_tool_start(self, *args, **kwargs) -> None:
        return None

    def _log_tool_complete(self, *args, **kwargs) -> None:
        return None

    def _summarize_metadata_output(self, output_metadata):
        return output_metadata


def _admission(
    arguments: dict,
    *,
    confirmed: bool = True,
):
    call = LLMToolCall(
        id="provider-writer",
        function=LLMToolFunctionCall(
            name="file_patch_writer",
            arguments=json.dumps(arguments),
        ),
    )
    return admit_provider_mutation_tool_call(
        call,
        task_id="task",
        session_id="session",
        round_index=1,
        ordinal=1,
        registry=_Registry(),
        budget=RuntimeBudgetMetadata(),
        prior_usage=ProviderToolBudgetUsage(),
        user_confirmed=confirmed,
        allow_mutations=True,
        write_scope=["app.py"],
        project_path=None,
        validation_command=_VALIDATION,
    )


def _result():
    return SimpleNamespace(
        success=True,
        output_metadata=ToolResultMetadata(
            tool_name="file_patch_writer",
            status=ResultStatus.SUCCESS,
            result=TextArtifactMetadata(content="patched"),
        ),
        error=None,
    )


def _runtime(executor, *, prepare=None, observe=None, verifier=None):
    state = RuntimeStateMetadata(goal="provider mutation execution")
    controller = SimpleNamespace(
        state=state,
        state_updater=StateUpdater(),
        replay_tool_result=lambda *_args: None,
        prepare_tool_call=prepare or (lambda *_args: True),
        observe_tool_result=observe or (lambda *_args: True),
        set_pending_verification=lambda _plan: None,
        verifier=verifier,
    )
    return SimpleNamespace(
        tool_registry=_Registry(),
        tool_executor=executor,
        runtime_controller=controller,
        runtime_diagnostics_hooks=None,
        _project_environments={},
    )


def _task() -> Task:
    return Task(
        id="task",
        description="patch app.py",
        validation_command=_VALIDATION,
    )


def _assert_generated_unit_redacted(result, generated_unit: str) -> None:
    digest = hashlib.sha256(generated_unit.encode("utf-8")).hexdigest()
    result_input = result.tool_results[0]["input_metadata"]
    assert result_input["generated_unit"] is None
    assert result_input["generated_unit_chars"] == len(generated_unit)
    assert result_input["generated_unit_sha256"] == digest
    typed_input = result.loop_metadata.tool_invocations[0].input_metadata
    assert typed_input.generated_unit is None
    assert typed_input.runtime_handles["_generated_unit_chars"] == len(
        generated_unit
    )
    assert typed_input.runtime_handles["_generated_unit_sha256"] == digest


def test_mutation_bridge_executes_prepared_inline_patch() -> None:
    executed = []

    class Executor:
        def execute_single(self, selection, context=None):
            executed.append(selection)
            return _result()

    runtime = _runtime(Executor())
    scope = ["app.py.index.json", "sketch.json"]
    generated_unit = "def added():\n    return True\n"
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_mutation_tool_calls(
        _task(),
        [
            _admission(
                {
                    "file_path": "app.py",
                    "operation_kind": "add_symbol",
                    "generated_unit": generated_unit,
                }
            )
        ],
        authorized_post_processing_write_scope=scope,
    )

    assert result.success is True
    assert len(executed) == 1
    assert executed[0].input_metadata.runtime_handles[
        "_post_processing_write_scope"
    ] == tuple(scope)
    assert result.loop_metadata.provider_executed is True
    assert result.tool_results[0]["provider_call_id"] == "provider-writer"
    assert runtime.runtime_controller.state.budget.tool_calls_used == 1
    assert runtime.runtime_controller.state.budget.file_edits_used == 1
    _assert_generated_unit_redacted(result, generated_unit)


def test_mutation_bridge_resolves_artifact_before_executor() -> None:
    verified = "def verified():\n    return True\n"
    ledger = ProviderCodeArtifactLedger()
    reference = ledger.register(
        CodeArtifactMetadata(code=verified, language="python"),
        source_id="project-generator",
        provider_call_id="provider-generator",
    )
    executed = []

    class Executor:
        def execute_single(self, selection, context=None):
            executed.append(selection.input_metadata.generated_unit)
            return _result()

    runtime = _runtime(Executor())
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_mutation_tool_calls(
        _task(),
        [
            _admission(
                {
                    "file_path": "app.py",
                    "operation_kind": "add_symbol",
                    "artifact_ref": reference.model_dump(mode="json"),
                    "generated_unit": "def untrusted():\n    return False\n",
                }
            )
        ],
        code_artifact_ledger=ledger,
        authorized_post_processing_write_scope=[],
    )

    assert result.success is True
    assert executed == [verified]
    _assert_generated_unit_redacted(result, verified)


def test_mutation_bridge_prepare_failure_prevents_execution() -> None:
    executed = []

    class Executor:
        def execute_single(self, selection, context=None):
            executed.append(selection)
            return _result()

    runtime = _runtime(Executor(), prepare=lambda *_args: False)
    generated_unit = "def added():\n    return True\n"
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_mutation_tool_calls(
        _task(),
        [
            _admission(
                {
                    "file_path": "app.py",
                    "operation_kind": "add_symbol",
                    "generated_unit": generated_unit,
                }
            )
        ],
        authorized_post_processing_write_scope=[],
    )

    assert result.success is False
    assert result.loop_metadata.final_error.error_type == "CheckpointPrepareFailed"
    assert executed == []
    assert runtime.runtime_controller.state.budget.tool_calls_used == 0
    _assert_generated_unit_redacted(result, generated_unit)


def test_mutation_bridge_observation_failure_does_not_apply_state() -> None:
    executed = []

    class Executor:
        def execute_single(self, selection, context=None):
            executed.append(selection)
            return _result()

    runtime = _runtime(
        Executor(),
        observe=lambda *_args: False,
    )
    generated_unit = "def added():\n    return True\n"
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_mutation_tool_calls(
        _task(),
        [
            _admission(
                {
                    "file_path": "app.py",
                    "operation_kind": "add_symbol",
                    "generated_unit": generated_unit,
                }
            )
        ],
        authorized_post_processing_write_scope=[],
    )

    assert result.success is False
    assert result.loop_metadata.final_error.error_type == (
        "CheckpointObservationFailed"
    )
    assert len(executed) == 1
    assert runtime.runtime_controller.state.budget.tool_calls_used == 0
    _assert_generated_unit_redacted(result, generated_unit)


def test_mutation_bridge_defers_generic_verifier_for_exact_task_command() -> None:
    class ExplodingVerifier:
        def plan(self, *_args, **_kwargs):
            raise AssertionError("generic verifier must be deferred")

    runtime = _runtime(
        SimpleNamespace(execute_single=lambda *_args, **_kwargs: _result()),
        verifier=ExplodingVerifier(),
    )
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_mutation_tool_calls(
        _task(),
        [
            _admission(
                {
                    "file_path": "app.py",
                    "operation_kind": "add_symbol",
                    "generated_unit": "def added():\n    return True\n",
                }
            )
        ],
        authorized_post_processing_write_scope=[],
    )

    assert result.success is True


def test_blocked_mutation_never_executes_or_validates_binding_inputs() -> None:
    class Executor:
        def execute_single(self, selection, context=None):
            raise AssertionError("blocked mutation must not execute")

    runtime = _runtime(Executor())
    generated_unit = "def added():\n    return True\n"
    admission = _admission(
        {
            "file_path": "app.py",
            "operation_kind": "add_symbol",
            "generated_unit": generated_unit,
        },
        confirmed=False,
    )
    result = ToolEventLoopRunner(_Owner(runtime)).run_provider_mutation_tool_calls(
        _task(),
        [admission],
        code_artifact_ledger=object(),
        authorized_post_processing_write_scope=(item for item in ()),
    )

    assert result.success is False
    assert result.loop_metadata.events[-1].event_type == "error"
    assert runtime.runtime_controller.state.budget.tool_calls_used == 0
    _assert_generated_unit_redacted(result, generated_unit)
    assert admission.tool_call.input_metadata.generated_unit == generated_unit
