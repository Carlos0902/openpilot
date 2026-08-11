from __future__ import annotations

import pytest

from core.provider_tool_roundtrip_contracts import MAX_PROVIDER_TOOL_ATTEMPTS
from core.provider_validation_observation import (
    ProviderValidationObservation,
    ProviderValidationObservationError,
    provider_validation_observation,
)
from core.tool_event_loop import ToolEventLoopRunResult
from metadata import ToolLoopMetadata


EXPECTED = "python -m pytest -q tests/test_example.py"


def _loop_result(*tool_results: dict) -> ToolEventLoopRunResult:
    return ToolEventLoopRunResult(
        success=True,
        tool_results=list(tool_results),
        last_output=None,
        loop_metadata=ToolLoopMetadata(
            session_id="session",
            task_id="task",
            status="completed",
            success=True,
        ),
    )


def _validation_result(
    *,
    command: str = EXPECTED,
    item_success: bool = True,
    result_success: bool = True,
    exit_code: int = 0,
) -> dict:
    return {
        "tool": "command_executor",
        "success": item_success,
        "input_metadata": {
            "requested_command": command,
            "command": command,
        },
        "result": {
            "success": result_success,
            "exit_code": exit_code,
        },
    }


def test_observation_accepts_one_argv_equivalent_success() -> None:
    result = _validation_result(
        command="  python   -m pytest -q tests/test_example.py  "
    )

    observation = provider_validation_observation(
        _loop_result(result),
        validation_command=EXPECTED,
    )

    assert observation is ProviderValidationObservation.SUCCEEDED


@pytest.mark.parametrize(
    "result",
    [
        _validation_result(item_success=False, result_success=False, exit_code=1),
        _validation_result(result_success=False, exit_code=0),
        _validation_result(result_success=True, exit_code=1),
    ],
)
def test_observation_classifies_any_exact_failure_evidence_as_failed(
    result: dict,
) -> None:
    assert provider_validation_observation(
        _loop_result(result),
        validation_command=EXPECTED,
    ) is ProviderValidationObservation.FAILED


def test_observation_ignores_nonmatching_and_non_command_results() -> None:
    observation = provider_validation_observation(
        _loop_result(
            _validation_result(command="python -m pytest -q tests/test_other.py"),
            {"tool": "file_reader", "success": True},
        ),
        validation_command=EXPECTED,
    )

    assert observation is ProviderValidationObservation.NOT_OBSERVED


def test_observation_rejects_repeated_exact_validation() -> None:
    with pytest.raises(
        ProviderValidationObservationError,
        match="exactly once",
    ):
        provider_validation_observation(
            _loop_result(_validation_result(), _validation_result()),
            validation_command=EXPECTED,
        )


@pytest.mark.parametrize(
    "result",
    [
        _validation_result(result_success=True, exit_code=True),
        {
            **_validation_result(),
            "result": {"success": True},
        },
        {
            **_validation_result(),
            "result": {"exit_code": 0},
        },
        {
            **_validation_result(),
            "success": "yes",
        },
    ],
)
def test_observation_rejects_ambiguous_exact_outcomes(result: dict) -> None:
    with pytest.raises(
        ProviderValidationObservationError,
        match="outcome",
    ):
        provider_validation_observation(
            _loop_result(result),
            validation_command=EXPECTED,
        )


@pytest.mark.parametrize("validation_command", ["", "   ", None, 7])
def test_observation_rejects_missing_validation_authority(
    validation_command,
) -> None:
    with pytest.raises(
        ProviderValidationObservationError,
        match="validation_command",
    ):
        provider_validation_observation(
            _loop_result(),
            validation_command=validation_command,
        )


def test_observation_rejects_unbounded_or_malformed_result_collections() -> None:
    loop_result = _loop_result()
    loop_result.tool_results = (_validation_result() for _ in range(2))
    with pytest.raises(
        ProviderValidationObservationError,
        match="bounded list",
    ):
        provider_validation_observation(
            loop_result,
            validation_command=EXPECTED,
        )

    loop_result.tool_results = [
        {"tool": "file_reader", "success": True}
        for _ in range(MAX_PROVIDER_TOOL_ATTEMPTS + 1)
    ]
    with pytest.raises(
        ProviderValidationObservationError,
        match="attempt limit",
    ):
        provider_validation_observation(
            loop_result,
            validation_command=EXPECTED,
        )

    loop_result.tool_results = ["not-a-result"]
    with pytest.raises(
        ProviderValidationObservationError,
        match=r"tool_results\[0\]",
    ):
        provider_validation_observation(
            loop_result,
            validation_command=EXPECTED,
        )
