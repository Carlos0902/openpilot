import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from run_observation import (
    GuardedLLMClient,
    ObservationLimitExceeded,
    PROVIDER_ARM_PROTOCOL_PATH,
    PROTOCOL_PATH,
    analyze,
    apply_protocol_intervention,
    build_guard_manifest_observation,
    resolve_protocol_path,
    provider_runtime_identity,
    build_argument_parser,
    load_task_designer_context_scope,
    validate_task_designer_context_args,
    capture_bounded_project_snapshot,
    build_enhancement_mutation_observation,
    diff_project_snapshots,
)
from core.config import LLMSettings
from core.llm import LLMMessage, LLMRequest


ROOT = Path(__file__).resolve().parent


class _ExactCounter:
    available = True
    tokenizer_id = "test-exact-counter"
    model = "test-model"

    def count_text(self, text: str) -> int:
        return len(str(text).split())


def _request(*, content: str = "one two", max_tokens: int = 3) -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content=content)],
        max_tokens=max_tokens,
    )


def test_guard_forces_transport_retries_off_at_the_transport_boundary() -> None:
    class CapturingClient:
        settings = object()
        token_counter = _ExactCounter()

        def __init__(self) -> None:
            self.request = None

        def complete(self, request, **kwargs):
            self.request = request
            return SimpleNamespace(
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
            )

    client = CapturingClient()
    guarded = GuardedLLMClient(
        client,
        max_calls=1,
        max_total_tokens=10,
        max_wall_clock_seconds=60,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="test")],
        max_tokens=1,
        transport_retries=2,
    )

    guarded.complete(request)

    assert client.request.transport_retries == 0
    assert request.transport_retries == 2


def test_guard_defaults_missing_completion_limit_before_reservation_and_transport() -> None:
    class CapturingClient:
        settings = object()
        token_counter = _ExactCounter()
        calls = 0
        request = None

        def complete(self, request, **kwargs):
            self.calls += 1
            self.request = request
            return SimpleNamespace(
                usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
            )

    client = CapturingClient()
    guarded = GuardedLLMClient(
        client,
        max_calls=2,
        max_total_tokens=10,
        max_wall_clock_seconds=60,
        default_max_completion_tokens=7,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )
    request = LLMRequest(messages=[LLMMessage(role="user", content="one two")])

    guarded.complete(request)

    assert request.max_tokens is None
    assert client.calls == 1
    assert client.request.max_tokens == 7
    snapshot = guarded.observation_snapshot()
    assert snapshot["default_max_completion_tokens"] == 7
    assert snapshot["defaulted_max_completion_request_count"] == 1
    assert snapshot["effective_max_completion_tokens"] == [7]
    assert snapshot["last_effective_max_completion_tokens"] == 7
    assert snapshot["last_request_reservation_tokens"] == 10


def test_guard_preserves_explicit_completion_limit_over_experiment_default() -> None:
    captured = []
    client = SimpleNamespace(
        settings=object(),
        token_counter=_ExactCounter(),
        complete=lambda request, **kwargs: (
            captured.append(request)
            or SimpleNamespace(
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
            )
        ),
    )
    guarded = GuardedLLMClient(
        client,
        max_calls=1,
        max_total_tokens=10,
        max_wall_clock_seconds=60,
        default_max_completion_tokens=7,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )

    guarded.complete(_request(content="one", max_tokens=3))

    assert captured[0].max_tokens == 3
    snapshot = guarded.observation_snapshot()
    assert snapshot["defaulted_max_completion_request_count"] == 0
    assert snapshot["effective_max_completion_tokens"] == [3]


@pytest.mark.parametrize("default_limit", [None, 0, -1])
def test_guard_missing_or_invalid_default_fails_closed_for_unbounded_request(
    default_limit,
) -> None:
    client = SimpleNamespace(
        settings=object(),
        token_counter=_ExactCounter(),
        calls=0,
    )

    def complete(*args, **kwargs):
        client.calls += 1
        raise AssertionError("transport must not run")

    client.complete = complete
    guarded = GuardedLLMClient(
        client,
        max_calls=1,
        max_total_tokens=10,
        max_wall_clock_seconds=60,
        default_max_completion_tokens=default_limit,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )

    with pytest.raises(ObservationLimitExceeded, match="default max completion"):
        guarded.complete(
            LLMRequest(messages=[LLMMessage(role="user", content="one")])
        )

    assert client.calls == 0


def test_provider_runtime_identity_records_typed_reasoning_arm_without_secret() -> None:
    settings = LLMSettings(
        OPENPILOT_LLM_API_KEY="secret-value",
        OPENPILOT_LLM_BASE_URL="https://api.deepseek.com?token=hidden",
        OPENPILOT_LLM_MODEL="deepseek-v4-flash",
        OPENPILOT_TOOL_EVENT_REASONING_MODE="disabled",
    )

    identity = provider_runtime_identity(settings)

    assert identity["endpoint"] == "https://api.deepseek.com"
    assert identity["tool_event_reasoning_mode"] == "disabled"
    assert identity["reasoning_profile"] == "deepseek-chat-known:v1"
    assert "secret" not in json.dumps(identity)


def _event(event_type: str, call_id: str, *, purpose: str = "controller", input_tokens: int = 0, output_tokens: int = 0):
    if event_type == "llm_requested":
        payload = {
            "purpose": purpose,
            "trace_info": {"diagnostics": {"message_count": 2, "prompt_chars": input_tokens * 4}},
            "context_selection": {},
            "correlation": {"execution_id": call_id},
        }
    else:
        payload = {
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            "provider_details": {"duration_ms": 10, "content_length": 20},
            "correlation": {"execution_id": call_id},
        }
    return {"event_type": event_type, "payload": payload, "phase": "execute"}


def _failed_event(
    call_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int | None,
    finish_reason: str,
) -> dict:
    usage = {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    if reasoning_tokens is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return {
        "event_type": "llm_failed",
        "phase": "execute",
        "payload": {
            "correlation": {"execution_id": call_id},
            "details": {
                "provider_attempt": {
                    "usage": usage,
                    "finish_reason": finish_reason,
                    "response_length": 120,
                }
            },
        },
    }


def test_analyze_detects_repeated_purpose_and_consecutive_growth() -> None:
    events = []
    for ordinal, tokens in enumerate((100, 130, 160), start=1):
        call_id = f"call-{ordinal}"
        events.extend(
            (
                _event("llm_requested", call_id),
                _event("llm_responded", call_id, input_tokens=tokens, output_tokens=600),
            )
        )

    result = analyze(events)

    assert result["aligned_call_count"] == 3
    assert result["responded_totals"] == {
        "input_tokens": 390,
        "output_tokens": 1800,
        "total_tokens": 2190,
    }
    assert result["inflation_signal_observed"] is True
    assert result["signals"]["repeated_purpose_growth"] == ["controller"]
    assert result["signals"]["consecutive_growth_windows"] == [[1, 3]]
    assert result["signals"]["controller_output_bloat"] == ["controller"]


def test_analyze_separates_failed_attempt_usage_and_preserves_unknown_reasoning() -> None:
    events = [
        _event("llm_requested", "responded", purpose="iteration_goal"),
        _event(
            "llm_responded",
            "responded",
            input_tokens=100,
            output_tokens=20,
        ),
        _event("llm_requested", "failed", purpose="iteration_task_design"),
        _failed_event(
            "failed",
            input_tokens=80,
            output_tokens=40,
            reasoning_tokens=30,
            finish_reason="length",
        ),
    ]

    result = analyze(events)

    assert result["logical_request_count"] == 2
    assert result["responded_call_count"] == 1
    assert result["failed_call_count"] == 1
    assert result["responded_totals"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }
    assert result["failed_attempt_totals"] == {
        "input_tokens": 80,
        "output_tokens": 40,
        "total_tokens": 120,
    }
    assert result["observed_attempt_totals"] == {
        "input_tokens": 180,
        "output_tokens": 60,
        "total_tokens": 240,
    }
    responded = next(call for call in result["calls"] if call["call_id"] == "responded")
    failed = next(attempt for attempt in result["failed_attempts"] if attempt["call_id"] == "failed")
    assert responded["reasoning_tokens"] is None
    assert failed["reasoning_tokens"] == 30
    assert failed["finish_reason"] == "length"
    assert result["usage_coverage"] == {
        "logical_requests": 2,
        "logical_requests_with_observed_usage": 2,
        "logical_request_usage_fraction": 1.0,
        "observed_outcomes": 2,
        "outcomes_with_reasoning_usage": 1,
        "reasoning_usage_fraction": 0.5,
    }
    assert result["secondary_signals"]["reasoning_output_share"] == 0.75
    assert "provider_totals" not in result


def test_analyze_treats_partial_success_and_failure_usage_as_unknown() -> None:
    responded_request = _event("llm_requested", "partial-success")
    responded = _event("llm_responded", "partial-success")
    responded["payload"]["usage"] = {"prompt_tokens": 7, "total_tokens": 9}
    failed_request = _event("llm_requested", "partial-failure")
    failed = _failed_event(
        "partial-failure",
        input_tokens=5,
        output_tokens=2,
        reasoning_tokens=None,
        finish_reason="length",
    )
    failed["payload"]["details"]["provider_attempt"]["usage"] = {
        "completion_tokens": 2,
        "total_tokens": 7,
    }

    result = analyze([responded_request, responded, failed_request, failed])

    assert result["calls"][0]["usage_observed"] is False
    assert result["failed_attempts"][0]["usage_observed"] is False
    assert result["responded_totals"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    assert result["failed_attempt_totals"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    assert result["usage_coverage"]["logical_request_usage_fraction"] == 0.0


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_guard_counts_failed_attempt_usage_and_blocks_the_next_request() -> None:
    class FailedAttemptError(RuntimeError):
        details = {
            "provider_attempt": {
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "total_tokens": 100,
                }
            }
        }

    class RaisingClient:
        settings = object()
        token_counter = _ExactCounter()
        calls = 0

        def complete(self, request, **kwargs):
            self.calls += 1
            raise FailedAttemptError("provider attempt failed")

    client = RaisingClient()
    guarded = GuardedLLMClient(
        client,
        max_calls=3,
        max_total_tokens=100,
        max_wall_clock_seconds=60,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )

    try:
        guarded.complete(_request(content="one", max_tokens=98))
    except FailedAttemptError:
        pass
    else:
        raise AssertionError("expected provider failure")

    snapshot = guarded.observation_snapshot()
    assert snapshot["logical_complete_calls_seen"] == 1
    assert snapshot["successful_response_usage_tokens_seen"] == 0
    assert snapshot["failed_attempt_usage_tokens_seen"] == 100
    assert snapshot["observed_provider_attempt_usage_tokens_seen"] == 100
    assert snapshot["next_request_allowed"] is False
    assert snapshot["blocking_limits"] == ["max_cumulative_provider_tokens"]
    assert snapshot["last_attempt_reached_or_crossed_limits"] == [
        "max_cumulative_provider_tokens"
    ]

    with pytest.raises(ObservationLimitExceeded, match="before provider request"):
        guarded.complete(_request(max_tokens=10))
    assert client.calls == 1


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": 4, "total_tokens": 5},
        {"output_tokens": 1, "total_tokens": 5},
        {"total_tokens": 5},
    ],
)
def test_guard_marks_partial_success_usage_unknown_and_censors_followups(usage) -> None:
    class PartialUsageClient:
        settings = object()
        token_counter = _ExactCounter()
        calls = 0

        def complete(self, request, **kwargs):
            self.calls += 1
            return SimpleNamespace(usage=usage)

    client = PartialUsageClient()
    guarded = GuardedLLMClient(
        client,
        max_calls=3,
        max_total_tokens=20,
        max_wall_clock_seconds=60,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )

    guarded.complete(_request(max_tokens=3))

    snapshot = guarded.observation_snapshot()
    assert snapshot["attempts_without_observed_usage"] == 1
    assert snapshot["successful_attempts_without_observed_usage"] == 1
    assert snapshot["usage_censored"] is True
    assert snapshot["unsettled_reserved_tokens"] == 6
    assert "unknown_provider_usage" in snapshot["blocking_limits"]
    manifest = build_guard_manifest_observation(
        guarded,
        {"completed": True, "result": {"success": True}},
    )
    assert manifest["run_termination"] == "runtime_finished_with_unknown_provider_usage"
    with pytest.raises(ObservationLimitExceeded, match="unknown_provider_usage"):
        guarded.complete(_request(max_tokens=3))
    assert client.calls == 1


def test_guard_accepts_complete_usage_without_explicit_total_and_derives_it() -> None:
    client = SimpleNamespace(
        settings=object(),
        token_counter=_ExactCounter(),
        complete=lambda *args, **kwargs: SimpleNamespace(
            usage={"prompt_tokens": 4, "completion_tokens": 2}
        ),
    )
    guarded = GuardedLLMClient(
        client,
        max_calls=2,
        max_total_tokens=20,
        max_wall_clock_seconds=60,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )

    guarded.complete(_request(max_tokens=3))

    snapshot = guarded.observation_snapshot()
    assert snapshot["observed_provider_attempt_usage_tokens_seen"] == 6
    assert snapshot["attempts_without_observed_usage"] == 0
    assert snapshot["unsettled_reserved_tokens"] == 0


def test_guard_reservation_includes_settled_usage_input_framing_and_completion() -> None:
    class ReturningClient:
        settings = object()
        token_counter = _ExactCounter()
        calls = 0

        def complete(self, request, **kwargs):
            self.calls += 1
            return SimpleNamespace(
                usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
            )

    client = ReturningClient()
    guarded = GuardedLLMClient(
        client,
        max_calls=3,
        max_total_tokens=10,
        max_wall_clock_seconds=60,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )

    guarded.complete(_request(content="one two", max_tokens=3))
    with pytest.raises(ObservationLimitExceeded, match="token reservation"):
        guarded.complete(_request(content="one", max_tokens=4))

    assert client.calls == 1
    snapshot = guarded.observation_snapshot()
    assert snapshot["last_request_reservation_tokens"] == 6
    assert snapshot["blocked_reservation_tokens"] == 6


def test_guard_fails_closed_when_exact_counter_is_unavailable() -> None:
    client = SimpleNamespace(
        settings=object(),
        token_counter=SimpleNamespace(available=False, tokenizer_id="unavailable"),
        complete=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("transport must not run")
        ),
    )
    guarded = GuardedLLMClient(
        client,
        max_calls=1,
        max_total_tokens=10,
        max_wall_clock_seconds=60,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )

    with pytest.raises(ObservationLimitExceeded, match="token counter"):
        guarded.complete(_request(max_tokens=1))

    snapshot = guarded.observation_snapshot()
    assert snapshot["logical_complete_calls_seen"] == 0
    assert snapshot["next_request_allowed"] is False
    assert "request_token_reservation_blocked" in snapshot["blocking_limits"]


def test_guard_marks_partial_failed_usage_unknown_and_keeps_reservation() -> None:
    class PartialFailure(RuntimeError):
        details = {"provider_attempt": {"usage": {"prompt_tokens": 2}}}

    class RaisingClient:
        settings = object()
        token_counter = _ExactCounter()

        def complete(self, request, **kwargs):
            raise PartialFailure("failed")

    guarded = GuardedLLMClient(
        RaisingClient(),
        max_calls=2,
        max_total_tokens=20,
        max_wall_clock_seconds=60,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )

    with pytest.raises(PartialFailure):
        guarded.complete(_request(max_tokens=3))

    snapshot = guarded.observation_snapshot()
    assert snapshot["failed_attempts_without_observed_usage"] == 1
    assert snapshot["attempts_without_observed_usage"] == 1
    assert snapshot["usage_censored"] is True
    assert snapshot["unsettled_reserved_tokens"] == 6


def test_guard_blocks_a_request_whose_reservation_would_cross_token_limit() -> None:
    class ReturningClient:
        settings = object()
        token_counter = _ExactCounter()
        calls = 0

        def complete(self, request, **kwargs):
            self.calls += 1
            return SimpleNamespace(
                usage={"input_tokens": 80, "output_tokens": 40, "total_tokens": 120}
            )

    client = ReturningClient()
    guarded = GuardedLLMClient(
        client,
        max_calls=3,
        max_total_tokens=100,
        max_wall_clock_seconds=60,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )

    with pytest.raises(ObservationLimitExceeded, match="token reservation"):
        guarded.complete(_request(content="one two", max_tokens=98))

    assert client.calls == 0
    observation = build_guard_manifest_observation(
        guarded,
        {"completed": False, "error_type": "ObservationLimitExceeded"},
    )
    assert observation["run_termination"] == "guard_blocked_request_before_transport"
    assert observation["next_request_allowed"] is False
    assert "request_token_reservation_blocked" in observation["blocking_limits"]


def test_guard_preserves_inflight_result_that_crosses_wall_clock_then_blocks() -> None:
    clock = _FakeClock()

    class SlowClient:
        settings = object()
        token_counter = _ExactCounter()
        calls = 0

        def complete(self, request, **kwargs):
            self.calls += 1
            clock.now = 11.0
            return SimpleNamespace(
                usage={"input_tokens": 6, "output_tokens": 4, "total_tokens": 10}
            )

    client = SlowClient()
    guarded = GuardedLLMClient(
        client,
        max_calls=3,
        max_total_tokens=100,
        max_wall_clock_seconds=10,
        framing_reserve_tokens=1,
        clock=clock,
    )

    assert guarded.complete(_request(max_tokens=10)).usage["total_tokens"] == 10
    snapshot = guarded.observation_snapshot()
    assert snapshot["elapsed_wall_clock_seconds"] == 11.0
    assert snapshot["blocking_limits"] == ["wall_clock_seconds"]
    with pytest.raises(ObservationLimitExceeded, match="wall_clock_seconds"):
        guarded.complete(_request(max_tokens=10))
    assert client.calls == 1


def test_guard_manifest_marks_a_request_blocked_before_provider_transport() -> None:
    guarded = GuardedLLMClient(
        SimpleNamespace(
            settings=object(),
            token_counter=_ExactCounter(),
            complete=lambda *args, **kwargs: None,
        ),
        max_calls=1,
        max_total_tokens=100,
        max_wall_clock_seconds=10,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )
    guarded.calls = 1

    observation = build_guard_manifest_observation(
        guarded,
        {
            "completed": False,
            "error_type": "ObservationLimitExceeded",
            "error": "blocked",
        },
    )

    assert observation["run_termination"] == "guard_blocked_request_before_transport"


def test_guard_manifest_exposes_a_blocked_request_even_if_runtime_catches_it() -> None:
    guarded = GuardedLLMClient(
        SimpleNamespace(
            settings=object(),
            token_counter=_ExactCounter(),
            complete=lambda *args, **kwargs: None,
        ),
        max_calls=1,
        max_total_tokens=100,
        max_wall_clock_seconds=10,
        framing_reserve_tokens=1,
        clock=_FakeClock(),
    )
    guarded.calls = 1
    with pytest.raises(ObservationLimitExceeded):
        guarded.complete(_request())

    observation = build_guard_manifest_observation(
        guarded,
        {"completed": True, "result": {"success": True}},
    )

    assert observation["safety_limit_reached"] is True
    assert observation["run_termination"] == (
        "runtime_finished_after_guard_blocked_request_before_transport"
    )


def test_default_protocol_is_observational_and_does_not_install_fixed_decomposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    calls: list[object] = []
    monkeypatch.setattr(
        "run_observation.install_fixed_decomposition",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    descriptor = apply_protocol_intervention(SimpleNamespace(), protocol)

    assert protocol["intervention"] == "none"
    assert protocol["safety_limits"]["default_max_completion_tokens"] == 4096
    assert "fixed_decomposition" not in protocol
    assert descriptor is None
    assert calls == []


def test_explicit_fixed_arm_selects_provider_protocol_and_installs_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = resolve_protocol_path(protocol_path=None, fixed_decomposition=True)
    protocol = json.loads(selected.read_text(encoding="utf-8"))
    expected = protocol["fixed_decomposition"]
    installed = {
        "fixture_id": expected["fixture_id"],
        "fixture_sha256": expected["fixture_sha256"],
        "source_run": expected["source_run"],
        "injection_point": expected["injection_point"],
        "replaced_method": expected["replaced_method"],
        "validation_command_sha256": expected["validation_command_sha256"],
        "delegated_methods": expected["delegated_methods"],
    }
    calls: list[dict] = []

    def _install(_autopilot, *, fixture_path, expected_sha256):
        calls.append(
            {"fixture_path": fixture_path, "expected_sha256": expected_sha256}
        )
        return installed

    monkeypatch.setattr("run_observation.install_fixed_decomposition", _install)

    descriptor = apply_protocol_intervention(SimpleNamespace(), protocol)

    assert selected == PROVIDER_ARM_PROTOCOL_PATH
    assert protocol["intervention"] == "fixed_decomposition"
    assert protocol["safety_limits"]["default_max_completion_tokens"] == 4096
    assert protocol["related_protocols"]["context_ab"]["protocol_id"] == (
        "project-improvement-context-ab-v1"
    )
    assert calls == [
        {
            "fixture_path": ROOT / expected["fixture_path"],
            "expected_sha256": expected["fixture_sha256"],
        }
    ]
    assert descriptor == installed

    assert resolve_protocol_path(
        protocol_path=PROVIDER_ARM_PROTOCOL_PATH,
        fixed_decomposition=False,
    ) == PROVIDER_ARM_PROTOCOL_PATH


def test_protocol_and_fixed_shortcut_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        resolve_protocol_path(
            protocol_path=ROOT / "OBSERVATION_PROTOCOL_V1.json",
            fixed_decomposition=True,
        )


def test_stage9_task_designer_cli_reuses_arm_and_source_with_scenario() -> None:
    parser = build_argument_parser()
    source = ROOT / "STAGE9_TASK_DESIGNER_PROVIDER_SENTINEL_V1.json"

    args = parser.parse_args(
        [
            "--task-designer-context-arm",
            "compact",
            "--task-designer-source-protocol",
            str(source),
            "--task-designer-scenario-id",
            "partial_shared_criterion",
        ]
    )

    validate_task_designer_context_args(args)
    assert args.task_designer_context_arm == "compact"
    assert args.task_designer_source_protocol == source
    assert args.task_designer_scenario_id == "partial_shared_criterion"


@pytest.mark.parametrize(
    "argv",
    [
        ["--task-designer-scenario-id", "strongly_related_diagnosis"],
        ["--task-designer-context-arm", "current"],
        [
            "--task-designer-source-protocol",
            "STAGE9_TASK_DESIGNER_PROVIDER_SENTINEL_V1.json",
        ],
    ],
)
def test_task_designer_cli_context_arguments_fail_closed_when_incomplete(argv) -> None:
    args = build_argument_parser().parse_args(argv)

    with pytest.raises(ValueError, match="must be supplied together"):
        validate_task_designer_context_args(args)


def test_stage9_scope_loader_uses_provider_fixture_and_records_descriptor() -> None:
    args = build_argument_parser().parse_args(
        [
            "--task-designer-context-arm",
            "compact",
            "--task-designer-source-protocol",
            str(ROOT / "STAGE9_TASK_DESIGNER_PROVIDER_SENTINEL_V1.json"),
            "--task-designer-scenario-id",
            "strongly_related_diagnosis",
        ]
    )

    class Agent:
        def _goal_from_candidate(self, selected_candidate, report, evaluation):
            return None

    scope = load_task_designer_context_scope(args, Agent())

    with scope as descriptor:
        assert descriptor["scenario_id"] == "strongly_related_diagnosis"
        assert descriptor["projection_policy"] == "compact"
        assert descriptor["source_fingerprint"].startswith("sha256:")
        assert descriptor["goal_hash"].startswith("sha256:")


def test_bounded_project_snapshot_observes_added_deleted_and_modified_files(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calculator = project / "calculator.py"
    calculator.write_text("before\n", encoding="utf-8")
    deleted = project / "delete_me.py"
    deleted.write_text("old\n", encoding="utf-8")
    (project / ".venv").mkdir()
    (project / ".venv" / "ignored.py").write_text("ignored\n", encoding="utf-8")
    before = capture_bounded_project_snapshot(project)

    calculator.write_text("after\n", encoding="utf-8")
    deleted.unlink()
    (project / "added.py").write_text("new\n", encoding="utf-8")
    after = capture_bounded_project_snapshot(project)
    observed = diff_project_snapshots(before, after)

    assert observed["modified_paths"] == [str(calculator.resolve())]
    assert observed["added_paths"] == [str((project / "added.py").resolve())]
    assert observed["deleted_paths"] == [str(deleted.resolve())]
    assert observed["all_changed_paths"] == sorted(
        [
            str(calculator.resolve()),
            str((project / "added.py").resolve()),
            str(deleted.resolve()),
        ]
    )
    assert all(".venv" not in path for path in before["files"])


def test_bounded_project_snapshot_records_symlinks_and_fails_on_limit(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    (project / "linked.py").symlink_to(outside)
    (project / "one.py").write_text("one\n", encoding="utf-8")
    (project / "two.py").write_text("two\n", encoding="utf-8")

    snapshot = capture_bounded_project_snapshot(project, max_files=1)

    assert snapshot["truncated"] is True
    assert snapshot["symlink_paths"] == [str((project / "linked.py").absolute())]


def test_enhancement_mutation_observation_uses_post_core_boundary(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calculator = project / "calculator.py"
    calculator.write_text("core result\n", encoding="utf-8")
    (project / "README.md").write_text("core docs\n", encoding="utf-8")
    metadata = project / ".openpilot"
    metadata.mkdir()
    (metadata / "project_stack.json").write_text("{}\n", encoding="utf-8")
    boundary = capture_bounded_project_snapshot(project)
    calculator.write_text("enhanced\n", encoding="utf-8")
    final = capture_bounded_project_snapshot(project)

    observed = build_enhancement_mutation_observation(
        {
            "enhancement_start_project_snapshot": boundary,
            "enhancement_start_snapshot_capture_count": 1,
            "enhancement_start_snapshot_observation_count": 1,
            "enhancement_start_snapshot_consistent": True,
        },
        final,
    )

    assert observed["all_changed_paths"] == [str(calculator.resolve())]
    assert observed["snapshot_missing"] is False
    assert observed["start_capture_count"] == 1
    assert observed["start_observation_count"] == 1
    assert observed["start_consistent"] is True


def test_enhancement_mutation_observation_marks_missing_boundary(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    final = capture_bounded_project_snapshot(project)

    observed = build_enhancement_mutation_observation({}, final)

    assert observed["snapshot_missing"] is True
    assert observed["all_changed_paths"] == []
    assert observed["after_truncated"] is False
