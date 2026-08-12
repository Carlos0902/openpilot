"""Connect bounded provider requests to the admitted mutation round executor."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from core.llm import LLMMessage, LLMResponse, LLMToolDefinition
from core.provider_completion_outcome import provider_completion_outcome
from core.provider_completion_usage_observation import provider_completion_tokens
from core.provider_execution_dispatch import dispatch_provider_execution_batch
from core.provider_mutation_round_execution import (
    ProviderMutationRoundResult,
    execute_provider_mutation_round,
)
from core.provider_round_request_builder import build_provider_round_llm_request
from core.provider_round_request_plan import build_provider_round_request_plan
from core.provider_tool_attempt_ledger import ProviderToolAttemptLedger
from core.provider_tool_batch_admission import admit_provider_tool_calls
from core.provider_tool_call_signature import provider_tool_call_signature
from core.provider_tool_duplicate_partition import partition_provider_tool_calls
from core.provider_tool_evidence_state import ProviderToolEvidenceState
from core.provider_tool_result_batch import provider_tool_result_batch
from core.provider_tool_roundtrip_contracts import (
    MAX_PROVIDER_ROUND_TRIP_ROUNDS,
    ProviderToolAttempt,
)
from core.provider_tool_wire_exchange import provider_tool_wire_exchange
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import ContextRequestPurpose, RuntimeBudgetMetadata, metadata_summary
from tools.mutation_descriptor import FILE_MUTATION_TOOLS


class ProviderMutationRoundTripError(ValueError):
    """Raised when mutation request-loop inputs are not safely bounded."""


@dataclass(frozen=True)
class ProviderMutationRoundRequestResult:
    """Result of finding and executing one admitted mutation round.

    ``completed`` means the mutation round itself completed. It does not mean
    the user task is complete: exact validation and final response remain a
    separate follow-up transition.
    """

    completed: bool
    mutation: ProviderMutationRoundResult | None
    messages: tuple[LLMMessage, ...]
    read_loop_results: tuple[ToolEventLoopRunResult, ...]
    attempts: tuple[ProviderToolAttempt, ...]
    rounds_used: int
    error_message: str | None = None


class ProviderMutationRoundTripRunner:
    """Run bounded read/request rounds until one patch mutation is admitted."""

    def __init__(
        self,
        owner: Any,
        task: Any,
        *,
        tools: Sequence[LLMToolDefinition],
        read_scope: Sequence[str] | None = None,
        write_scope: Sequence[str],
        project_path: str | None = None,
        validation_command: str,
        authorized_post_processing_write_scope: Sequence[str],
        max_rounds: int = 3,
        max_tokens: int | None = None,
        context_max_prompt_tokens: int | None = None,
        user_confirmed: bool = False,
        allow_mutations: bool = False,
    ) -> None:
        if type(user_confirmed) is not bool or type(allow_mutations) is not bool:
            raise ProviderMutationRoundTripError(
                "mutation authority controls must be literal booleans"
            )
        if not allow_mutations or not user_confirmed:
            raise ProviderMutationRoundTripError(
                "mutation round requires explicit mutation opt-in and confirmation"
            )
        if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes)):
            raise ProviderMutationRoundTripError("tools must be a bounded sequence")
        if not tools or any(not isinstance(tool, LLMToolDefinition) for tool in tools):
            raise ProviderMutationRoundTripError("mutation round requires typed tools")
        if not isinstance(write_scope, Sequence) or isinstance(write_scope, (str, bytes)) or not write_scope:
            raise ProviderMutationRoundTripError("mutation round requires a non-empty write scope")
        if not isinstance(validation_command, str) or not validation_command.strip():
            raise ProviderMutationRoundTripError("mutation round requires an exact validation command")
        if not isinstance(authorized_post_processing_write_scope, Sequence) or isinstance(
            authorized_post_processing_write_scope, (str, bytes)
        ) or not authorized_post_processing_write_scope:
            raise ProviderMutationRoundTripError(
                "mutation round requires a non-empty post-processing write scope"
            )
        if type(max_rounds) is not int or not 1 <= max_rounds <= MAX_PROVIDER_ROUND_TRIP_ROUNDS:
            raise ProviderMutationRoundTripError(
                "max_rounds must be within provider round bounds"
            )

        self.owner = owner
        self.runtime = owner.runtime
        self.task = task
        self.tools = tuple(tool.model_copy(deep=True) for tool in tools)
        self.read_scope = tuple(read_scope or ())
        self.write_scope = tuple(str(path) for path in write_scope)
        self.project_path = str(project_path or "").strip() or None
        self.validation_command = validation_command.strip()
        self.authorized_post_processing_write_scope = tuple(
            str(path) for path in authorized_post_processing_write_scope
        )
        self.max_rounds = max_rounds
        self.max_tokens = max_tokens
        self.context_max_prompt_tokens = context_max_prompt_tokens
        self.user_confirmed = user_confirmed
        self.allow_mutations = allow_mutations
        self._attempt_ledger = ProviderToolAttemptLedger()
        self._evidence = ProviderToolEvidenceState(project_path=self.project_path)
        self._messages: list[LLMMessage] = []

        if not any(tool.function.name == "file_patch_writer" for tool in self.tools):
            raise ProviderMutationRoundTripError(
                "mutation round requires file_patch_writer in the tool surface"
            )

    def run(self, messages: Sequence[LLMMessage]) -> ProviderMutationRoundRequestResult:
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
            raise ProviderMutationRoundTripError("messages must be a bounded sequence")
        if any(not isinstance(message, LLMMessage) for message in messages):
            raise ProviderMutationRoundTripError("messages must contain LLMMessage values")
        self._messages = [message.model_copy(deep=True) for message in messages]
        read_loop_results: list[ToolEventLoopRunResult] = []
        last_round = 0

        for round_index in range(1, self.max_rounds + 1):
            last_round = round_index
            try:
                budget = self._runtime_budget()
                calls_remaining = self.max_rounds - round_index + 1
                prompt_budget = self.context_max_prompt_tokens or int(
                    getattr(self.runtime.llm_client.settings, "context_max_prompt_tokens", 4096)
                    or 4096
                )
                plan = build_provider_round_request_plan(
                    messages=self._messages,
                    tool_names=[tool.function.name for tool in self.tools],
                    finalization_pending=False,
                    post_mutation_active=False,
                    mutation_tools_exposed=True,
                    all_scoped_reads_complete=self._all_scoped_reads_complete(),
                    prompt_budget_tokens=prompt_budget,
                    response_call_count=calls_remaining,
                )
                request_limit = budget.tool_event_completion_limit(
                    round_index=round_index,
                    calls_remaining=calls_remaining,
                )
                if request_limit <= 0:
                    raise ProviderMutationRoundTripError(
                        "provider mutation completion budget is exhausted"
                    )
                request = build_provider_round_llm_request(
                    self.runtime.llm_client,
                    plan=plan,
                    tool_definitions=[
                        tool for tool in self.tools if tool.function.name in plan.tool_names
                    ],
                    purpose=ContextRequestPurpose.TOOL_EVENT_DECISION,
                    max_tokens=min(
                        request_limit,
                        self.max_tokens if self.max_tokens is not None else request_limit,
                    ),
                    timeout_seconds=45.0,
                    transport_retries=0,
                    reasoning_policy=self._reasoning_policy(),
                    trace_info={"provider_mutation_round": round_index},
                )
                reserved = request.max_tokens or request_limit
                budget.consume_tool_event_completion(reserved)
                response = self.runtime.llm_client.complete(request)
                if not isinstance(response, LLMResponse):
                    raise ProviderMutationRoundTripError(
                        "provider returned a non-LLMResponse value"
                    )
                usage = provider_completion_tokens(response)
                if usage is not None:
                    if usage > reserved:
                        raise ProviderMutationRoundTripError(
                            "provider completion usage exceeded request limit"
                        )
                    budget.reconcile_tool_event_completion(reserved=reserved, actual=usage)
                budget.observe_tool_event_outcome(provider_completion_outcome(response))
                if not response.tool_calls:
                    return self._result(
                        completed=False,
                        error_message="ProviderMutationNotObserved",
                        rounds_used=round_index,
                        read_loop_results=read_loop_results,
                    )

                partition = partition_provider_tool_calls(
                    response.tool_calls,
                    ledger=self._attempt_ledger,
                    round_index=round_index,
                    project_path=self.project_path,
                )
                admissions = admit_provider_tool_calls(
                    list(partition.new_calls),
                    task_id=str(getattr(self.task, "id", "unknown")),
                    session_id=self.owner._session_id(),
                    round_index=round_index,
                    registry=self.runtime.tool_registry,
                    budget=budget,
                    user_confirmed=self.user_confirmed,
                    allow_mutations=self.allow_mutations,
                    read_scope=self.read_scope,
                    write_scope=self.write_scope,
                    project_path=self.project_path,
                    validation_command=self.validation_command,
                )
                admitted_mutations = [
                    item
                    for item in admissions
                    if item.status == "admitted"
                    and item.tool_call.tool_name in FILE_MUTATION_TOOLS
                ]
                if admitted_mutations:
                    if len(admitted_mutations) != len(admissions) or partition.duplicate_blocks:
                        return self._result(
                            completed=False,
                            error_message="ProviderMutationRoundMixedCalls",
                            rounds_used=round_index,
                            read_loop_results=read_loop_results,
                        )
                    mutation = execute_provider_mutation_round(
                        self._event_runner(),
                        self.task,
                        response,
                        admissions,
                        round_index=round_index,
                        validation_command=self.validation_command,
                        authorized_post_processing_write_scope=(
                            self.authorized_post_processing_write_scope
                        ),
                    )
                    self._messages.extend(mutation.wire_messages)
                    self._record_attempts(response, mutation.loop_result, round_index)
                    return self._result(
                        completed=True,
                        mutation=mutation,
                        rounds_used=round_index,
                        read_loop_results=read_loop_results,
                    )

                loop_result = dispatch_provider_execution_batch(
                    self._event_runner(),
                    self.task,
                    admissions,
                    round_index=round_index,
                    allow_mutations=False,
                )
                tool_results = provider_tool_result_batch(
                    response,
                    loop_result,
                    duplicate_blocks=partition.duplicate_blocks,
                    char_budget=plan.tool_result_char_budget,
                )
                self._messages.extend(provider_tool_wire_exchange(response, tool_results))
                self._record_attempts(response, loop_result, round_index)
                self._observe_read_evidence(response, loop_result)
                read_loop_results.append(loop_result)
            except Exception as exc:
                return self._result(
                    completed=False,
                    error_message=str(exc)[:2000] or type(exc).__name__,
                    rounds_used=round_index,
                    read_loop_results=read_loop_results,
                )

        return self._result(
            completed=False,
            error_message="ProviderMutationRoundLimitExceeded",
            rounds_used=last_round,
            read_loop_results=read_loop_results,
        )

    def _result(self, **kwargs: Any) -> ProviderMutationRoundRequestResult:
        return ProviderMutationRoundRequestResult(
            messages=tuple(self._messages),
            attempts=self._attempt_ledger.attempts,
            **kwargs,
        )

    def _runtime_budget(self) -> RuntimeBudgetMetadata:
        state = getattr(getattr(self.runtime, "runtime_controller", None), "state", None)
        budget = getattr(state, "budget", None)
        if not isinstance(budget, RuntimeBudgetMetadata):
            raise ProviderMutationRoundTripError(
                "provider mutation round requires active runtime budget"
            )
        return budget

    def _reasoning_policy(self) -> Any:
        policy = getattr(self.owner, "_reasoning_policy_for_task", None)
        return policy(self.task) if callable(policy) else None

    def _event_runner(self) -> Any:
        from core.tool_event_loop import ToolEventLoopRunner

        return ToolEventLoopRunner(self.owner)

    def _all_scoped_reads_complete(self) -> bool:
        expected = {
            self._canonical_path(path)
            for path in self.read_scope
            if str(path).strip()
        }
        observed = set(self._evidence.coverage().completed_read_paths)
        return bool(expected) and expected.issubset(observed)

    def _canonical_path(self, raw_path: str) -> str:
        path = Path(str(raw_path).strip()).expanduser()
        if not path.is_absolute() and self.project_path:
            path = Path(self.project_path) / path
        return str(path.resolve(strict=False))

    def _observe_read_evidence(
        self,
        response: LLMResponse,
        loop_result: ToolEventLoopRunResult,
    ) -> None:
        result_by_id = {
            str(item.get("provider_call_id")): item
            for item in loop_result.tool_results
            if item.get("provider_call_id")
        }
        for call in response.tool_calls:
            if call.function.name != "file_reader":
                continue
            item = result_by_id.get(call.id)
            if not item or not item.get("success"):
                continue
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except (TypeError, ValueError, RecursionError):
                arguments = {}
            arguments = arguments if isinstance(arguments, dict) else {}
            summary = metadata_summary(item.get("result"))
            summary = summary if isinstance(summary, dict) else {}
            path = str(summary.get("file_path") or arguments.get("file_path") or "").strip()
            if path:
                self._evidence.record_completed_read(
                    self._canonical_path(path),
                    projection="inline",
                )

    def _record_attempts(
        self,
        response: LLMResponse,
        loop_result: ToolEventLoopRunResult,
        round_index: int,
    ) -> None:
        results = {
            str(item.get("provider_call_id")): item
            for item in loop_result.tool_results
        }
        errors = {
            str(item.provider_call_id): item
            for item in loop_result.loop_metadata.recoverable_errors
            if item.provider_call_id
        }
        for call in response.tool_calls:
            item = results.get(call.id)
            if item is None or self._attempt_ledger.provider_call_seen(call.id):
                continue
            succeeded = bool(item.get("success"))
            self._attempt_ledger.record(
                ProviderToolAttempt(
                    signature=provider_tool_call_signature(
                        call,
                        project_path=self.project_path,
                    ),
                    tool_name=call.function.name,
                    provider_call_id=call.id,
                    round_index=round_index,
                    success=succeeded,
                    error_type=(
                        errors[call.id].error_type
                        if call.id in errors
                        else "ProviderToolExecutionFailed"
                    )
                    if not succeeded
                    else None,
                )
            )


__all__ = [
    "ProviderMutationRoundRequestResult",
    "ProviderMutationRoundTripError",
    "ProviderMutationRoundTripRunner",
]
