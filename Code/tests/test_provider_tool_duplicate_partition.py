from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from core.llm import LLMToolCall, LLMToolFunctionCall
from core.provider_tool_attempt_ledger import ProviderToolAttemptLedger
from core.provider_tool_call_signature import provider_tool_call_signature
from core.provider_tool_duplicate_partition import (
    ProviderToolDuplicatePartitionError,
    ProviderToolCallPartition,
    ProviderToolDuplicateBlock,
    partition_provider_tool_calls,
)
from core.provider_tool_roundtrip_contracts import ProviderToolAttempt
from core.provider_tool_batch_admission import (
    MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE,
)


def _call(
    path="README.md",
    *,
    call_id="provider-1",
    name="file_reader",
):
    return LLMToolCall(
        id=call_id,
        function=LLMToolFunctionCall(
            name=name,
            arguments=json.dumps({"file_path": path}),
        ),
    )


def _record(
    ledger,
    call,
    *,
    project_path=None,
    success=True,
    error_type=None,
):
    attempt = ProviderToolAttempt(
        signature=provider_tool_call_signature(
            call,
            project_path=project_path,
        ),
        tool_name=call.function.name,
        provider_call_id=call.id,
        round_index=1,
        success=success,
        error_type=error_type,
    )
    ledger.record(attempt)
    return attempt


def test_partition_keeps_unseen_calls_in_provider_order() -> None:
    calls = (
        _call("a.py", call_id="provider-1"),
        _call("b.py", call_id="provider-2"),
    )

    partition = partition_provider_tool_calls(
        calls,
        ledger=ProviderToolAttemptLedger(),
        round_index=1,
    )

    assert partition.new_calls == calls
    assert partition.duplicate_blocks == ()
    assert partition.total_calls == 2


def test_partition_blocks_existing_signature_and_records_lineage(tmp_path) -> None:
    ledger = ProviderToolAttemptLedger()
    first = _call("README.md", call_id="provider-1")
    first_attempt = _record(ledger, first, project_path=str(tmp_path))
    repeated = _call(
        str(tmp_path / "README.md"),
        call_id="provider-2",
    )

    partition = partition_provider_tool_calls(
        (repeated,),
        ledger=ledger,
        round_index=2,
        project_path=str(tmp_path),
    )

    assert partition.new_calls == ()
    assert len(partition.duplicate_blocks) == 1
    block = partition.duplicate_blocks[0]
    assert block.provider_call_id == "provider-2"
    assert block.previous_provider_call_id == "provider-1"
    assert block.error_type == "ProviderToolDuplicateAttempt"
    assert ledger.attempts[-1].duplicate_of == first_attempt.provider_call_id


def test_partition_preserves_new_and_duplicate_bucket_order() -> None:
    ledger = ProviderToolAttemptLedger()
    first = _call("old-a.py", call_id="old-a")
    second = _call("old-b.py", call_id="old-b")
    _record(ledger, first)
    _record(ledger, second)
    calls = (
        _call("new-a.py", call_id="new-a"),
        _call("old-b.py", call_id="repeat-b"),
        _call("new-b.py", call_id="new-b"),
        _call("old-a.py", call_id="repeat-a"),
    )

    partition = partition_provider_tool_calls(
        calls,
        ledger=ledger,
        round_index=2,
    )

    assert [call.id for call in partition.new_calls] == ["new-a", "new-b"]
    assert [
        block.provider_call_id for block in partition.duplicate_blocks
    ] == ["repeat-b", "repeat-a"]


def test_partition_rejects_provider_id_reused_across_rounds() -> None:
    ledger = ProviderToolAttemptLedger()
    _record(ledger, _call("old.py", call_id="provider-1"))

    with pytest.raises(ProviderToolDuplicatePartitionError):
        partition_provider_tool_calls(
            (_call("new.py", call_id="provider-1"),),
            ledger=ledger,
            round_index=2,
        )


def test_partition_rejects_duplicate_ids_within_one_response() -> None:
    calls = (
        _call("a.py", call_id="provider-1"),
        _call("b.py", call_id="provider-1"),
    )

    with pytest.raises(ProviderToolDuplicatePartitionError):
        partition_provider_tool_calls(
            calls,
            ledger=ProviderToolAttemptLedger(),
            round_index=1,
        )


def test_partition_accepts_exact_call_limit_and_rejects_overflow() -> None:
    calls = tuple(
        _call("same.py", call_id=f"provider-{index}")
        for index in range(MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE)
    )
    partition = partition_provider_tool_calls(
        calls,
        ledger=ProviderToolAttemptLedger(),
        round_index=1,
    )

    assert len(partition.new_calls) == MAX_PROVIDER_TOOL_CALLS_PER_RESPONSE
    with pytest.raises(ProviderToolDuplicatePartitionError):
        partition_provider_tool_calls(
            calls + (_call("overflow.py", call_id="overflow"),),
            ledger=ProviderToolAttemptLedger(),
            round_index=1,
        )


def test_partition_capacity_failure_is_atomic() -> None:
    ledger = ProviderToolAttemptLedger(max_attempts=2)
    _record(ledger, _call("old.py", call_id="provider-1"))
    calls = (
        _call("old.py", call_id="provider-2"),
        _call("old.py", call_id="provider-3"),
    )

    with pytest.raises(ProviderToolDuplicatePartitionError):
        partition_provider_tool_calls(
            calls,
            ledger=ledger,
            round_index=2,
        )

    assert len(ledger) == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"ledger": object()},
        {"round_index": 0},
        {"round_index": True},
    ],
)
def test_partition_rejects_invalid_control_inputs(updates) -> None:
    values = {
        "ledger": ProviderToolAttemptLedger(),
        "round_index": 1,
    }
    values.update(updates)

    with pytest.raises(ProviderToolDuplicatePartitionError):
        partition_provider_tool_calls((_call(),), **values)


@pytest.mark.parametrize(
    "calls, updates",
    [
        ((object(),), {}),
        ((_call() for _ in range(1)), {}),
        ((_call(),), {"project_path": ""}),
        ((_call(),), {"project_path": 1}),
    ],
)
def test_partition_rejects_invalid_call_and_path_inputs(calls, updates) -> None:
    with pytest.raises(ProviderToolDuplicatePartitionError):
        partition_provider_tool_calls(
            calls,
            ledger=ProviderToolAttemptLedger(),
            round_index=1,
            **updates,
        )


def test_partition_contracts_reject_contradictory_payloads() -> None:
    with pytest.raises(ValidationError):
        ProviderToolDuplicateBlock(
            provider_call_id="provider-1",
            previous_provider_call_id="provider-1",
            tool_name="file_reader",
            signature="a" * 64,
            round_index=2,
        )
    with pytest.raises(ValidationError):
        ProviderToolCallPartition(total_calls=1)
