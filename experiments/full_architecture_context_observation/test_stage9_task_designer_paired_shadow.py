from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import stage9_task_designer_paired_shadow as paired
from core.llm import LLMMessage, LLMRequest, LLMResponse
from metadata import ContextRequestPurpose, ContextSelectionMetadata
from runtime_diagnostics.recorder import DiagnosticRecorder
from runtime_diagnostics.hooks import RuntimeDiagnosticsHooks
from runtime_diagnostics.llm_proxy import TrajectoryLLMClientProxy
from run_observation import GuardedLLMClient, load_task_designer_context_scope
from stage9_task_designer_scenario_gate import build_scenario_fixtures


SCENARIOS = (
    "strongly_related_diagnosis",
    "partial_shared_criterion",
    "relevant_iteration_memory",
)


def _response(
    marker: str,
    *,
    input_tokens: int | None = 100,
    output_tokens: int | None = 20,
) -> SimpleNamespace:
    usage = {}
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    return SimpleNamespace(
        marker=marker,
        parsed_json={"task": {"description": marker}},
        usage=usage,
        finish_reason="stop",
    )


def _request(policy: str, *, reservation_tokens: int = 1_000) -> SimpleNamespace:
    return SimpleNamespace(
        projection_policy=policy,
        reservation_tokens=reservation_tokens,
        runtime_contract_hash="sha256:one-live-contract",
    )


class _ExactCounter:
    available = True
    tokenizer_id = "paired-shadow-test"
    model = "paired-shadow-test"

    def count_text(self, text: str) -> int:
        return max(1, len(str(text).split()))


class _ProductLedger:
    def __init__(self) -> None:
        self.reserve_count = 0
        self.reconcile_count = 0
        self.budget = {"reserved": 0, "used": 0}

    def reserve(self) -> None:
        self.reserve_count += 1
        self.budget["reserved"] = 100

    def reconcile(self, response: LLMResponse) -> None:
        self.reconcile_count += 1
        self.budget["reserved"] = 0
        self.budget["used"] += int(response.usage["output_tokens"])


class _Provider:
    settings = SimpleNamespace(model="paired-shadow-test", provider="demo")
    token_counter = _ExactCounter()

    def __init__(
        self,
        task_payload: dict,
        *,
        on_shadow=None,
        role_payloads=None,
        role_response_meta=None,
    ) -> None:
        self.task_payload = task_payload
        self.on_shadow = on_shadow
        self.role_payloads = dict(role_payloads or {})
        self.role_response_meta = dict(role_response_meta or {})
        self.roles: list[str] = []

    def complete(self, request: LLMRequest, **kwargs) -> LLMResponse:
        del kwargs
        role = str((request.trace_info.get("paired_shadow") or {}).get("role") or "")
        self.roles.append(role)
        if role == "shadow" and self.on_shadow is not None:
            self.on_shadow()
        payload = deepcopy(self.role_payloads.get(role, self.task_payload))
        payload["task"]["description"] = role or "production"
        response_meta = self.role_response_meta.get(role) or {}
        return LLMResponse(
            content=str(response_meta.get("content", "{}")),
            parsed_json=payload,
            model=self.settings.model,
            provider=self.settings.provider,
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            finish_reason=str(response_meta.get("finish_reason", "stop")),
        )


class _ScopeAgent:
    def __init__(self, client, project: Path, task_payload: dict) -> None:
        self.llm_client = client
        self.enhancement_budget = _ProductLedger()
        self.runtime_budget = {"remaining": 10_000}
        memory_root = project / "memory"
        memory_root.mkdir()
        self.memory_store = SimpleNamespace(data_dir=memory_root)
        self.task_payload = task_payload
        self.coerced_descriptions: list[str] = []

    def _complete_json_candidates(self, candidates, *, purpose, **kwargs):
        del candidates, kwargs
        assert purpose == ContextRequestPurpose.ITERATION_TASK_DESIGN
        self.enhancement_budget.reserve()
        response = self.llm_client.complete(
            LLMRequest(
                messages=[LLMMessage(role="user", content="production")],
                response_format="json_object",
                max_tokens=100,
            ),
            max_retries=1,
            use_cache=False,
        )
        self.enhancement_budget.reconcile(response)
        return response.parsed_json, {"iteration_task_design:goal"}

    def _coerce_task(self, raw_task, *args):
        del args
        self.coerced_descriptions.append(str(raw_task.get("description") or ""))
        return object() if raw_task.get("description") else None

    def _goal_from_candidate(self, *args):
        del args
        return None


def _scope_runtime(
    tmp_path: Path,
    monkeypatch,
    *,
    on_shadow=None,
    role_payloads=None,
    role_response_meta=None,
):
    project = tmp_path / "project"
    project.mkdir(parents=True)
    calculator = project / "calculator.py"
    calculator.write_text("def divide(a, b): return a / b\n", encoding="utf-8")
    fixture = build_scenario_fixtures()["partial_shared_criterion"]
    state = fixture.project_state.model_copy(
        update={
            "project_path": str(project),
            "safe_target_files": [str(calculator)],
            "file_summaries": [
                {"path": str(calculator), "preview": calculator.read_text(encoding="utf-8")}
            ],
        }
    )
    task_payload = {
        "task": {
            "description": "placeholder",
            "target_files": [str(calculator)],
            "acceptance_criteria": list(fixture.goal.acceptance_criteria),
            "risk_notes": [],
            "evidence_ids": [],
        }
    }
    provider = _Provider(
        task_payload,
        on_shadow=on_shadow,
        role_payloads=role_payloads,
        role_response_meta=role_response_meta,
    )
    guard = GuardedLLMClient(
        provider,
        max_calls=10,
        max_total_tokens=10_000,
        max_wall_clock_seconds=60,
        token_counter=_ExactCounter(),
        framing_reserve_tokens=1,
    )
    proxy = TrajectoryLLMClientProxy(
        guard,
        hooks=RuntimeDiagnosticsHooks(enabled=False),
    )
    agent = _ScopeAgent(proxy, project, task_payload)

    def build_request(_client, *, candidates, purpose, response_format, **kwargs):
        del candidates, purpose
        return LLMRequest(
            messages=[LLMMessage(role="user", content="shadow")],
            response_format=response_format,
            temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_tokens"),
            timeout_seconds=kwargs.get("timeout_seconds"),
            transport_retries=kwargs.get("transport_retries"),
            reasoning_policy=kwargs.get("reasoning_policy"),
        )

    monkeypatch.setattr(paired, "build_context_candidate_request", build_request)
    return fixture, state, agent, provider, guard, project


def test_schedule_freezes_six_full_runs_with_production_first_shadow_second() -> None:
    schedule = paired.build_schedule()

    assert [(item["scenario_id"], item["production_policy"]) for item in schedule] == [
        (SCENARIOS[0], "current"),
        (SCENARIOS[0], "compact"),
        (SCENARIOS[1], "compact"),
        (SCENARIOS[1], "current"),
        (SCENARIOS[2], "current"),
        (SCENARIOS[2], "compact"),
    ]
    assert [item["ordinal"] for item in schedule] == list(range(1, 7))
    for item in schedule:
        expected_shadow = (
            "compact" if item["production_policy"] == "current" else "current"
        )
        assert item["shadow_policy"] == expected_shadow
        assert item["transport_order"] == [
            {"role": "production", "policy": item["production_policy"]},
            {"role": "shadow", "policy": expected_shadow},
        ]


def test_pair_assembly_hashes_one_live_contract_and_builds_both_before_transport() -> None:
    runtime = object()
    build_calls: list[tuple[int, str]] = []
    hash_calls: list[int] = []
    transport_started = False

    def hash_runtime(value: object) -> str:
        assert transport_started is False
        hash_calls.append(id(value))
        return "sha256:one-live-contract"

    def build_request(value: object, policy: str) -> SimpleNamespace:
        assert transport_started is False
        build_calls.append((id(value), policy))
        return _request(policy)

    bundle = paired.assemble_request_pair(
        runtime_contract=runtime,
        production_policy="compact",
        hash_runtime_contract=hash_runtime,
        build_request=build_request,
    )
    transport_started = True

    assert hash_calls == [id(runtime)]
    assert build_calls == [(id(runtime), "compact"), (id(runtime), "current")]
    assert bundle.runtime_contract_hash == "sha256:one-live-contract"
    assert bundle.production_request.runtime_contract_hash == bundle.runtime_contract_hash
    assert bundle.shadow_request.runtime_contract_hash == bundle.runtime_contract_hash


def test_pair_combined_reservation_fails_before_first_transport() -> None:
    calls: list[str] = []
    consumed: list[object] = []
    bundle = paired.RequestPair(
        runtime_contract_hash="sha256:one-live-contract",
        production_policy="compact",
        shadow_policy="current",
        production_request=_request("compact", reservation_tokens=700),
        shadow_request=_request("current", reservation_tokens=600),
    )

    with pytest.raises(paired.PairedShadowStopped, match="combined.*reservation"):
        paired.execute_request_pair(
            bundle,
            remaining_provider_tokens=1_299,
            transport=lambda request: calls.append(request.projection_policy),
            consume_production=consumed.append,
        )

    assert calls == []
    assert consumed == []


def test_pair_transports_production_first_and_consumes_only_production() -> None:
    calls: list[str] = []
    consumed: list[str] = []
    production_reconciled: list[dict] = []
    bundle = paired.RequestPair(
        runtime_contract_hash="sha256:one-live-contract",
        production_policy="compact",
        shadow_policy="current",
        production_request=_request("compact"),
        shadow_request=_request("current"),
    )

    def transport(request: SimpleNamespace) -> SimpleNamespace:
        calls.append(request.projection_policy)
        return _response(request.projection_policy)

    result = paired.execute_request_pair(
        bundle,
        remaining_provider_tokens=2_000,
        transport=transport,
        consume_production=lambda response: consumed.append(response.marker),
        reconcile_production=lambda response: production_reconciled.append(
            response.usage
        ),
    )

    assert calls == ["compact", "current"]
    assert consumed == ["compact"]
    assert production_reconciled == [{"input_tokens": 100, "output_tokens": 20}]
    assert result.production_response.marker == "compact"
    assert result.shadow_response.marker == "current"


@pytest.mark.parametrize("failure_at", ["production", "shadow"])
def test_any_transport_exception_stops_second_or_downstream(failure_at: str) -> None:
    calls: list[str] = []
    consumed: list[object] = []
    bundle = paired.RequestPair(
        runtime_contract_hash="sha256:one-live-contract",
        production_policy="compact",
        shadow_policy="current",
        production_request=_request("compact"),
        shadow_request=_request("current"),
    )

    def transport(request: SimpleNamespace) -> SimpleNamespace:
        calls.append(request.projection_policy)
        if (
            failure_at == "production" and request.projection_policy == "compact"
        ) or (failure_at == "shadow" and request.projection_policy == "current"):
            raise RuntimeError("provider failed")
        return _response(request.projection_policy)

    with pytest.raises(paired.PairedShadowStopped, match="provider failed"):
        paired.execute_request_pair(
            bundle,
            remaining_provider_tokens=2_000,
            transport=transport,
            consume_production=consumed.append,
        )

    assert calls == (["compact"] if failure_at == "production" else ["compact", "current"])
    assert consumed == []


@pytest.mark.parametrize(
    ("incomplete_policy", "input_tokens", "output_tokens", "expected_calls"),
    [
        ("compact", None, 20, ["compact"]),
        ("compact", 100, None, ["compact"]),
        ("current", None, None, ["compact", "current"]),
    ],
)
def test_partial_or_unknown_usage_fails_closed_before_second_or_downstream(
    incomplete_policy: str,
    input_tokens: int | None,
    output_tokens: int | None,
    expected_calls: list[str],
) -> None:
    calls: list[str] = []
    consumed: list[object] = []
    bundle = paired.RequestPair(
        runtime_contract_hash="sha256:one-live-contract",
        production_policy="compact",
        shadow_policy="current",
        production_request=_request("compact"),
        shadow_request=_request("current"),
    )

    def transport(request: SimpleNamespace) -> SimpleNamespace:
        calls.append(request.projection_policy)
        if request.projection_policy == incomplete_policy:
            return _response(
                request.projection_policy,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return _response(request.projection_policy)

    with pytest.raises(paired.PairedShadowStopped, match="usage.*censored"):
        paired.execute_request_pair(
            bundle,
            remaining_provider_tokens=2_000,
            transport=transport,
            consume_production=consumed.append,
        )

    assert calls == expected_calls
    assert consumed == []


def test_paired_observation_maps_both_usages_by_execution_id_and_role() -> None:
    events = [
        {
            "event_type": "llm_requested",
            "payload": {
                "correlation": {"execution_id": "production-call"},
                "context_selection": {
                    "request_purpose": "iteration_task_design",
                    "final_prompt_tokens": 90,
                },
                "trace_info": {
                    "paired_shadow": {
                        "role": "production",
                        "projection_policy": "compact",
                        "transport_ordinal": 1,
                        "runtime_contract_hash": "sha256:one-live-contract",
                    }
                },
            },
        },
        {
            "event_type": "llm_requested",
            "payload": {
                "correlation": {"execution_id": "shadow-call"},
                "context_selection": {
                    "request_purpose": "iteration_task_design",
                    "final_prompt_tokens": 140,
                },
                "trace_info": {
                    "paired_shadow": {
                        "role": "shadow",
                        "projection_policy": "current",
                        "transport_ordinal": 2,
                        "runtime_contract_hash": "sha256:one-live-contract",
                    }
                },
            },
        },
        {
            "event_type": "llm_responded",
            "payload": {
                "correlation": {"execution_id": "shadow-call"},
                "usage": {"input_tokens": 150, "output_tokens": 30},
                "finish_reason": "stop",
                "provider_details": {"content_length": 2},
            },
        },
        {
            "event_type": "llm_responded",
            "payload": {
                "correlation": {"execution_id": "production-call"},
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "finish_reason": "stop",
                "provider_details": {"content_length": 2},
            },
        },
    ]

    observation = paired.observe_paired_task_designer(events)

    assert observation["usage_observed"] is True
    assert observation["request_count"] == 2
    assert observation["transport_order"] == ["production", "shadow"]
    assert observation["roles"]["production"]["provider_total_tokens"] == 120
    assert observation["roles"]["shadow"]["provider_total_tokens"] == 180
    assert observation["provider_total_tokens"] == 300
    assert observation["roles"]["production"]["final_prompt_tokens"] == 90
    assert observation["roles"]["shadow"]["final_prompt_tokens"] == 140
    assert observation["roles"]["production"]["finish_reason"] == "stop"
    assert observation["roles"]["shadow"]["finish_reason"] == "stop"
    assert observation["roles"]["production"]["response_content_length"] == 2
    assert observation["roles"]["shadow"]["response_content_length"] == 2


def test_paired_observer_reads_real_trajectory_recorder_response_shape(
    tmp_path: Path,
) -> None:
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    hooks = RuntimeDiagnosticsHooks(recorder)

    class LocalProvider:
        settings = SimpleNamespace(model="paired-local", provider="local-test")

        def complete(self, request, **kwargs):
            del kwargs
            role = request.trace_info["paired_shadow"]["role"]
            content = "production-result" if role == "production" else "shadow-result"
            return LLMResponse(
                content=content,
                parsed_json={"task": {"description": role}},
                model=self.settings.model,
                provider=self.settings.provider,
                usage={
                    "prompt_tokens": 11 if role == "production" else 7,
                    "completion_tokens": 5 if role == "production" else 3,
                    "total_tokens": 16 if role == "production" else 10,
                },
                finish_reason="stop",
            )

    proxy = TrajectoryLLMClientProxy(
        LocalProvider(),
        hooks=hooks,
        task_id_getter=lambda: "paired-e2e-task",
        session_id_getter=lambda: "paired-e2e-session",
    )
    for ordinal, (role, policy) in enumerate(
        [("production", "compact"), ("shadow", "current")], start=1
    ):
        proxy.complete(
            LLMRequest(
                messages=[LLMMessage(role="user", content=role)],
                response_format="json_object",
                max_tokens=2_000,
                trace_info={
                    "completion_budget": {"reserved_tokens": 2_000},
                    "paired_shadow": {
                        "role": role,
                        "projection_policy": policy,
                        "transport_ordinal": ordinal,
                        "runtime_contract_hash": "sha256:real-recorder-contract",
                    },
                },
                context_selection=ContextSelectionMetadata(
                    request_purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
                    max_prompt_chars=1_000,
                    original_prompt_chars=len(role),
                    final_prompt_chars=len(role),
                ),
            ),
            max_retries=0,
            use_cache=False,
        )

    run = recorder.load_run("paired-e2e-session")
    assert run is not None
    events = recorder.load_trajectory_events(run.run_id)
    responded_payloads = [
        event["payload"]
        for event in events
        if event["event_type"] == "llm_responded"
    ]
    assert len(responded_payloads) == 2
    assert all("response_metadata" not in payload for payload in responded_payloads)
    assert all("usage" in payload for payload in responded_payloads)

    observation = paired.observe_paired_task_designer(events)

    assert observation["usage_observed"] is True
    assert observation["roles"]["production"]["provider_total_tokens"] == 16
    assert observation["roles"]["shadow"]["provider_total_tokens"] == 10
    assert observation["roles"]["production"]["finish_reason"] == "stop"
    assert observation["roles"]["shadow"]["finish_reason"] == "stop"
    assert observation["roles"]["production"]["response_content_length"] == len(
        "production-result"
    )
    assert observation["roles"]["shadow"]["response_content_length"] == len(
        "shadow-result"
    )


@pytest.mark.parametrize(
    ("tamper", "failure"),
    [
        (
            lambda events: events[4]["payload"]["trace_info"]["paired_shadow"].update(
                runtime_contract_hash="sha256:drift"
            ),
            "runtime_contract_hash_mismatch",
        ),
        (
            lambda events: events[4]["payload"]["trace_info"]["paired_shadow"].update(
                transport_ordinal=1
            ),
            "transport_ordinal_membership_invalid",
        ),
        (
            lambda events: events.append(deepcopy(events[2])),
            "duplicate_request_execution_id",
        ),
        (
            lambda events: events.append(deepcopy(events[5])),
            "duplicate_response_execution_id",
        ),
    ],
)
def test_paired_observer_strictly_rejects_hash_ordinal_or_duplicate_events(
    tamper, failure
) -> None:
    item = paired.build_schedule()[0]
    events = _paired_events(item)
    tamper(events)

    observation = paired.observe_paired_task_designer(events)

    assert observation["usage_observed"] is False
    assert failure in observation["validation_failures"]


def test_campaign_spend_counts_lifecycle_once_and_holds_censored_reservations() -> None:
    records = [
        {
            "usage": {"lifecycle": {"total_tokens": 10_000}},
            "paired_task_designer": {"provider_total_tokens": 3_000},
            "guard_observation": {
                "usage_censored": False,
                "unsettled_reserved_tokens": 0,
            },
        },
        {
            "usage": {"lifecycle": {"total_tokens": 9_000}},
            "paired_task_designer": {"provider_total_tokens": 2_500},
            "guard_observation": {
                "usage_censored": True,
                "unsettled_reserved_tokens": 4_000,
            },
        },
    ]

    spend = paired.evaluate_campaign_spend(records, campaign_hard=22_999)

    assert spend["observed_lifecycle_tokens"] == 19_000
    assert spend["held_unknown_usage_tokens"] == 4_000
    assert spend["effective_campaign_tokens"] == 23_000
    assert spend["hard_failures"] == ["campaign_hard_limit_exceeded"]


@pytest.mark.parametrize(
    "record",
    [
        {
            "usage": {"lifecycle": {"total_tokens": -1}},
            "guard_observation": {},
        },
        {
            "usage": {"lifecycle": {"total_tokens": 1}},
            "guard_observation": {
                "usage_censored": True,
                "unsettled_reserved_tokens": -1,
            },
        },
    ],
)
def test_campaign_spend_rejects_negative_lifecycle_or_held_usage(record: dict) -> None:
    with pytest.raises(paired.PairedShadowError, match="negative.*token"):
        paired.evaluate_campaign_spend([record], campaign_hard=30_000)


@pytest.mark.parametrize("tamper", ["missing_evidence", "hash", "campaign_total"])
def test_protocol_validates_all_historical_spend_evidence(tamper: str) -> None:
    protocol = deepcopy(paired.load_protocol())

    if tamper == "missing_evidence":
        protocol["historical_stage9_spend"]["evidence"].pop()
    elif tamper == "hash":
        protocol["historical_stage9_spend"]["evidence"][0]["sha256"] = (
            "sha256:" + "0" * 64
        )
    else:
        protocol["historical_stage9_spend"]["observed_complete_tokens"] = 51_390

    with pytest.raises(paired.PairedShadowError, match="historical.*spend"):
        paired.validate_protocol(protocol)


def test_protocol_freezes_scenario_pair_membership() -> None:
    protocol = deepcopy(paired.load_protocol())
    protocol["schedule"][0]["scenario_pair"] = 99

    with pytest.raises(paired.PairedShadowError, match="schedule|scenario pair"):
        paired.validate_protocol(protocol)


def test_shadow_raw_unsafe_target_cannot_pass_via_coercion_filtering() -> None:
    response = SimpleNamespace(
        parsed_json={
            "task": {
                "description": "valid-looking shadow task",
                "target_files": ["calculator.py", "../forbidden.py"],
                "acceptance_criteria": ["criterion"],
                "risk_notes": [],
                "evidence_ids": ["candidate:kept"],
            }
        }
    )
    request = SimpleNamespace(
        context_selection=SimpleNamespace(
            candidate_decisions=[
                SimpleNamespace(candidate_id="candidate:kept", action="kept")
            ]
        )
    )
    agent = SimpleNamespace(_coerce_task=lambda *args: object())
    diagnostics: dict = {}

    assert paired._shadow_task_valid(
        agent,
        response,
        request,
        state=SimpleNamespace(
            project_path=".", safe_target_files=["calculator.py"]
        ),
        goal=object(),
        report={},
        completed_iteration=0,
        diagnostics=diagnostics,
    ) is False
    assert diagnostics["reason"] == "target_scope_violation"


def test_shadow_nonretained_evidence_is_filterable_and_diagnosed() -> None:
    response = SimpleNamespace(
        parsed_json={
            "task": {
                "description": "valid-looking shadow task",
                "target_files": ["calculator.py"],
                "acceptance_criteria": ["criterion"],
                "risk_notes": [],
                "evidence_ids": ["candidate:kept", "candidate:forged"],
            }
        }
    )
    request = SimpleNamespace(
        context_selection=SimpleNamespace(
            candidate_decisions=[
                SimpleNamespace(candidate_id="candidate:kept", action="kept")
            ]
        )
    )
    diagnostics: dict = {}

    assert paired._shadow_task_valid(
        SimpleNamespace(_coerce_task=lambda *args: object()),
        response,
        request,
        state=SimpleNamespace(
            project_path=".", safe_target_files=["calculator.py"]
        ),
        goal=object(),
        report={},
        completed_iteration=0,
        diagnostics=diagnostics,
    ) is True
    assert diagnostics == {
        "raw_evidence_ids": ["candidate:kept", "candidate:forged"],
        "accepted_evidence_ids": ["candidate:kept"],
        "rejected_evidence_ids": ["candidate:forged"],
    }


def test_scope_descriptor_does_not_claim_consumption_before_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, _state, agent, _provider, _guard, _project = _scope_runtime(
        tmp_path, monkeypatch
    )

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ) as descriptor:
        assert descriptor["production_output_consumed_by_downstream"] is False
        assert descriptor["shadow_output_consumed_by_downstream"] is False
        assert descriptor["shadow_completed"] is False


def test_real_scope_unwraps_trajectory_proxy_to_guard_and_uses_one_product_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, state, agent, provider, guard, _project = _scope_runtime(
        tmp_path, monkeypatch
    )

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ) as descriptor:
        candidates = paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=0,
        )
        payload, retained = agent._complete_json_candidates(
            candidates,
            purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
        )
        # Shadow validation calls the same coercer internally, but it must not
        # be mistaken for downstream consumption of the production payload.
        assert descriptor["production_output_consumed_by_downstream"] is False
        assert descriptor["production_response_hash"].startswith("sha256:")
        assert descriptor.get("production_consumed_payload_hash") is None
        agent._coerce_task(payload["task"], fixture.goal, state, 0, {}, retained)

    assert provider.roles == ["production", "shadow"]
    guard_snapshot = guard.observation_snapshot()
    assert guard_snapshot["logical_complete_calls_seen"] == len(provider.roles)
    assert len(guard_snapshot["effective_max_completion_tokens"]) == len(
        provider.roles
    )
    assert descriptor["combined_admission"]["production_reservation_tokens"] > 0
    assert descriptor["combined_admission"]["shadow_reservation_tokens"] > 0
    assert agent.enhancement_budget.reserve_count == 1
    assert agent.enhancement_budget.reconcile_count == 1
    assert agent.coerced_descriptions == ["shadow", "production"]
    assert descriptor["production_output_consumed_by_downstream"] is True
    assert descriptor["shadow_output_consumed_by_downstream"] is False
    assert descriptor["production_consumed_payload_hash"] == descriptor[
        "production_response_hash"
    ]
    assert descriptor["shadow_response_hash"] not in descriptor[
        "downstream_consumed_payload_hashes"
    ]


def test_shadow_failure_occurs_after_product_reconcile_but_before_production_coercion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_shadow() -> None:
        raise RuntimeError("shadow transport failed")

    fixture, state, agent, provider, _guard, _project = _scope_runtime(
        tmp_path, monkeypatch, on_shadow=fail_shadow
    )

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ) as descriptor:
        candidates = paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=0,
        )
        with pytest.raises(paired.PairedShadowStopped, match="shadow provider failed"):
            agent._complete_json_candidates(
                candidates,
                purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            )

    assert provider.roles == ["production", "shadow"]
    assert agent.enhancement_budget.reserve_count == 1
    assert agent.enhancement_budget.reconcile_count == 1
    assert agent.coerced_descriptions == []
    assert descriptor["production_output_consumed_by_downstream"] is False
    assert descriptor["shadow_completed"] is False


@pytest.mark.parametrize("drift", ["project", "memory", "product_budget"])
def test_shadow_project_memory_or_product_budget_drift_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    handles: dict[str, object] = {}

    def mutate_during_shadow() -> None:
        agent = handles["agent"]
        project = handles["project"]
        if drift == "project":
            (project / "calculator.py").write_text("shadow mutation\n", encoding="utf-8")
        elif drift == "memory":
            (Path(agent.memory_store.data_dir) / "shadow.json").write_text(
                "{}", encoding="utf-8"
            )
        else:
            agent.enhancement_budget.budget["used"] += 1

    fixture, state, agent, _provider, _guard, project = _scope_runtime(
        tmp_path, monkeypatch, on_shadow=mutate_during_shadow
    )
    handles.update(agent=agent, project=project)

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ):
        candidates = paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=0,
        )
        with pytest.raises(paired.PairedShadowStopped, match="shadow changed"):
            agent._complete_json_candidates(
                candidates,
                purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            )


def test_scope_rejects_a_second_production_builder_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, state, agent, _provider, _guard, _project = _scope_runtime(
        tmp_path, monkeypatch
    )
    kwargs = {
        "project_state": state,
        "goal": fixture.goal,
        "improvement_report": fixture.improvement_report,
        "completed_iteration": 0,
    }

    with paired.paired_task_designer_scenario_scope(
        "current", fixture, agent=agent
    ) as descriptor:
        paired.iteration_agent_module.build_iteration_task_design_candidates(**kwargs)
        with pytest.raises(paired.PairedShadowStopped, match="entry count"):
            paired.iteration_agent_module.build_iteration_task_design_candidates(**kwargs)

    assert descriptor["builder_entry_count"] == 1


def test_scope_descriptor_persists_recomputable_candidate_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, state, agent, _provider, _guard, _project = _scope_runtime(
        tmp_path, monkeypatch
    )

    with paired.paired_task_designer_scenario_scope(
        "current", fixture, agent=agent
    ) as descriptor:
        paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=fixture.completed_iteration,
        )

    assert descriptor["completed_iteration"] == fixture.completed_iteration
    payloads = descriptor["canonical_candidate_payloads"]
    assert set(payloads) == {"current", "compact"}
    assert all(isinstance(payloads[policy], list) for policy in payloads)
    assert descriptor["candidate_fingerprints"] == {
        policy: paired._sha256(payloads[policy]) for policy in payloads
    }
    assert descriptor["shared_snapshot_hash"] == paired._sha256(
        {
            "runtime_contract_hash": descriptor["runtime_contract_hash"],
            "source_fingerprint": descriptor["source_fingerprint"],
            "goal_hash": descriptor["goal_hash"],
            "completed_iteration": descriptor["completed_iteration"],
        }
    )


@pytest.mark.parametrize(("invalid_stage", "invalid_key"), [("before", "truncated"), ("after", "symlink_paths")])
def test_scope_rejects_untrustworthy_shadow_boundary_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_stage: str,
    invalid_key: str,
) -> None:
    fixture, state, agent, provider, _guard, project = _scope_runtime(
        tmp_path, monkeypatch
    )
    from run_observation import capture_bounded_project_snapshot

    valid = capture_bounded_project_snapshot(project)
    observations = 0

    def snapshot(_path):
        nonlocal observations
        observations += 1
        result = deepcopy(valid)
        # capture 1 is builder entry, capture 2/3 are shadow before/after.
        target = 2 if invalid_stage == "before" else 3
        if observations == target:
            if invalid_key == "truncated":
                result["truncated"] = True
            else:
                result["symlink_paths"] = [str(project / "link")]
        return result

    monkeypatch.setattr(paired, "capture_enhancement_start_snapshot", snapshot)

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ):
        candidates = paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=0,
        )
        with pytest.raises(paired.PairedShadowStopped, match="shadow.*snapshot.*untrustworthy"):
            agent._complete_json_candidates(
                candidates,
                purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            )

    assert provider.roles == (
        ["production"] if invalid_stage == "before" else ["production", "shadow"]
    )


@pytest.mark.parametrize("mutated_surface", ["state", "report"])
def test_builder_rejects_projection_builder_mutating_frozen_state_or_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutated_surface: str,
) -> None:
    fixture, state, agent, _provider, _guard, _project = _scope_runtime(
        tmp_path, monkeypatch
    )
    production_builder = paired.iteration_agent_module.build_iteration_task_design_candidates
    calls = 0

    def mutating_builder(**kwargs):
        nonlocal calls
        calls += 1
        result = production_builder(**kwargs)
        if calls == 1:
            if mutated_surface == "state":
                kwargs["project_state"].safe_target_files.append("forbidden.py")
            else:
                kwargs["improvement_report"]["projection_mutation"] = True
        return result

    monkeypatch.setattr(
        paired.iteration_agent_module,
        "build_iteration_task_design_candidates",
        mutating_builder,
    )

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ):
        with pytest.raises(paired.PairedShadowStopped, match="frozen.*source.*changed"):
            paired.iteration_agent_module.build_iteration_task_design_candidates(
                project_state=state,
                goal=fixture.goal,
                improvement_report=fixture.improvement_report,
                completed_iteration=0,
            )


def test_production_raw_authority_is_validated_before_shadow_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, state, agent, provider, _guard, project = _scope_runtime(
        tmp_path, monkeypatch
    )
    calculator = project / "calculator.py"
    bad_production = {
        "task": {
            "description": "mixed authorized and forbidden targets",
            "target_files": [str(calculator), str(project.parent / "forbidden.py")],
            "acceptance_criteria": list(fixture.goal.acceptance_criteria),
            "risk_notes": [],
            "evidence_ids": [],
        }
    }
    provider.role_payloads["production"] = bad_production

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ):
        candidates = paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=0,
        )
        with pytest.raises(paired.PairedShadowStopped, match="production.*authorized task"):
            agent._complete_json_candidates(
                candidates,
                purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            )

    assert provider.roles == ["production"]


def test_production_uses_coercion_filtering_for_nonretained_evidence_and_records_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, state, agent, provider, _guard, project = _scope_runtime(
        tmp_path, monkeypatch
    )
    retained_id = "iteration_task_design:goal"
    rejected_id = "candidate:not-retained"
    raw = {
        "task": {
            "description": "valid task with one filterable evidence id",
            "target_files": [str(project / "calculator.py")],
            "acceptance_criteria": list(fixture.goal.acceptance_criteria),
            "risk_notes": [],
            "evidence_ids": [retained_id, rejected_id],
        }
    }
    provider.role_payloads["production"] = raw
    coercions: list[dict] = []

    def filtering_coerce(raw_task, _goal, _state, _iteration, _report, retained):
        accepted = deepcopy(raw_task)
        accepted["evidence_ids"] = [
            evidence_id
            for evidence_id in raw_task["evidence_ids"]
            if evidence_id in retained
        ]
        coercions.append(accepted)
        return SimpleNamespace(**accepted)

    agent._coerce_task = filtering_coerce
    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ) as descriptor:
        candidates = paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=0,
        )
        payload, retained = agent._complete_json_candidates(
            candidates,
            purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
        )
        accepted = agent._coerce_task(
            payload["task"],
            fixture.goal,
            state,
            0,
            fixture.improvement_report,
            retained,
        )

    assert provider.roles == ["production", "shadow"]
    assert accepted.evidence_ids == [retained_id]
    assert coercions[-1] == {
        **payload["task"],
        "evidence_ids": [retained_id],
    }
    assert descriptor["production_task_provenance"] == {
        "raw_evidence_ids": [retained_id, rejected_id],
        "accepted_evidence_ids": [retained_id],
        "rejected_evidence_ids": [rejected_id],
    }


def _assert_response_is_auditable(
    descriptor: dict, role: str, expected_payload: dict
) -> None:
    payload = descriptor.get(f"{role}_response_payload")
    artifact_ref = descriptor.get(f"{role}_response_artifact_ref")
    assert payload == expected_payload or (
        isinstance(artifact_ref, str) and artifact_ref.strip()
    )


def test_production_target_authority_failure_is_granular_and_payload_is_auditable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, state, agent, provider, _guard, project = _scope_runtime(
        tmp_path, monkeypatch
    )
    raw = {
        "task": {
            "description": "forbidden target",
            "target_files": [str(project.parent / "forbidden.py")],
            "acceptance_criteria": list(fixture.goal.acceptance_criteria),
            "risk_notes": [],
            "evidence_ids": [],
        }
    }
    provider.role_payloads["production"] = raw

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ) as descriptor:
        candidates = paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=0,
        )
        with pytest.raises(
            paired.PairedShadowStopped,
            match="production.*target.*authority",
        ):
            agent._complete_json_candidates(
                candidates,
                purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            )

    assert descriptor["production_rejection"]["reason"] == "target_scope_violation"
    expected = deepcopy(raw)
    expected["task"]["description"] = "production"
    _assert_response_is_auditable(descriptor, "production", expected)


def test_shadow_rejection_preserves_payload_or_artifact_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, state, agent, provider, _guard, project = _scope_runtime(
        tmp_path, monkeypatch
    )
    raw = {
        "task": {
            "description": "shadow forbidden target",
            "target_files": [str(project.parent / "shadow-forbidden.py")],
            "acceptance_criteria": list(fixture.goal.acceptance_criteria),
            "risk_notes": [],
            "evidence_ids": [],
        }
    }
    provider.role_payloads["shadow"] = raw

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ) as descriptor:
        candidates = paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=0,
        )
        with pytest.raises(
            paired.PairedShadowStopped,
            match="shadow.*target.*authority",
        ):
            agent._complete_json_candidates(
                candidates,
                purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            )

    expected = deepcopy(raw)
    expected["task"]["description"] = "shadow"
    _assert_response_is_auditable(descriptor, "shadow", expected)


@pytest.mark.parametrize(
    ("role", "response_meta", "expected_roles"),
    [
        ("production", {"content": ""}, ["production"]),
        ("production", {"finish_reason": "length"}, ["production"]),
        ("shadow", {"finish_reason": "max_tokens"}, ["production", "shadow"]),
    ],
)
def test_scope_rejects_empty_or_length_truncated_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    response_meta: dict,
    expected_roles: list[str],
) -> None:
    fixture, state, agent, provider, _guard, _project = _scope_runtime(
        tmp_path,
        monkeypatch,
        role_response_meta={role: response_meta},
    )

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ):
        candidates = paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=0,
        )
        with pytest.raises(
            paired.PairedShadowStopped, match="empty|finish_reason|truncated"
        ):
            agent._complete_json_candidates(
                candidates,
                purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
            )

    assert provider.roles == expected_roles


def test_shadow_payload_coerced_by_a_real_downstream_hook_is_marked_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, state, agent, _provider, _guard, project = _scope_runtime(
        tmp_path, monkeypatch
    )
    shadow_raw_task = {
        "description": "shadow",
        "target_files": [str(project / "calculator.py")],
        "acceptance_criteria": list(fixture.goal.acceptance_criteria),
        "risk_notes": [],
        "evidence_ids": [],
    }

    with paired.paired_task_designer_scenario_scope(
        "compact", fixture, agent=agent
    ) as descriptor:
        candidates = paired.iteration_agent_module.build_iteration_task_design_candidates(
            project_state=state,
            goal=fixture.goal,
            improvement_report=fixture.improvement_report,
            completed_iteration=0,
        )
        agent._complete_json_candidates(
            candidates,
            purpose=ContextRequestPurpose.ITERATION_TASK_DESIGN,
        )
        agent._coerce_task(shadow_raw_task, fixture.goal, state, 0, {}, set())

    assert descriptor["production_response_hash"] != descriptor[
        "shadow_response_hash"
    ]
    assert descriptor["shadow_output_consumed_by_downstream"] is True
    record = _paid_record(paired.build_schedule()[0])
    record["task_designer_context_intervention"] = descriptor
    assert "shadow_output_contract_invalid" in paired.arm_stop_reasons(
        record, paired.load_protocol()
    )


def test_run_observation_loads_v2_scope_with_production_policy() -> None:
    fixture = build_scenario_fixtures()["partial_shared_criterion"]
    agent = SimpleNamespace(
        _complete_json_candidates=lambda *args, **kwargs: None,
        _goal_from_candidate=lambda *args: fixture.goal,
        llm_client=SimpleNamespace(complete=lambda *args, **kwargs: None),
    )
    args = SimpleNamespace(
        task_designer_context_arm="compact",
        task_designer_source_protocol=str(paired.PROTOCOL_PATH),
        task_designer_scenario_id=fixture.scenario_id,
    )

    scope = load_task_designer_context_scope(args, agent)

    assert scope is not None
    assert type(scope).__name__ == "_GeneratorContextManager"


def _canonical_candidate_payloads(item: dict) -> dict[str, list[dict]]:
    contracts = paired.load_protocol()["scenario_candidate_contracts"][
        item["scenario_id"]
    ]
    return {
        policy: [
            {
                "candidate_id": f"sentinel:{policy}",
                "kind": "evidence",
                "content": " ".join(contracts[policy]["required_present"]),
                "priority": 1,
            }
        ]
        for policy in ("current", "compact")
    }


def _paid_record(item: dict, *, total_tokens: int = 10_000) -> dict:
    protocol = paired.load_protocol()
    shared_hash = "sha256:paired-runtime"
    completed_iteration = 0
    candidate_payloads = _canonical_candidate_payloads(item)
    source_fingerprint = protocol["scenarios"][item["scenario_id"]][
        "frozen_source_fingerprint"
    ]
    goal_hash = protocol["scenarios"][item["scenario_id"]]["frozen_goal_hash"]
    shared_snapshot_hash = paired._sha256(
        {
            "runtime_contract_hash": shared_hash,
            "source_fingerprint": source_fingerprint,
            "goal_hash": goal_hash,
            "completed_iteration": completed_iteration,
        }
    )
    side_effect_proof = {
        "project_snapshot_before": {"files": {"calculator.py": "before"}},
        "project_snapshot_after": {"files": {"calculator.py": "before"}},
        "memory_hash_before": "sha256:memory",
        "memory_hash_after": "sha256:memory",
        "runtime_budget_hash_before": "sha256:runtime-budget",
        "runtime_budget_hash_after": "sha256:runtime-budget",
        "enhancement_budget_hash_before": "sha256:enhancement-budget",
        "enhancement_budget_hash_after": "sha256:enhancement-budget",
    }
    return {
        **item,
        "provider_identity": paired.load_protocol()["provider_identity"],
        "quality_gate": {
            "passed": True,
            "signature": "sha256:integrated-quality",
            "checks": {
                "run_completed": True,
                "core_success": True,
                "verification_passed": True,
                "improvement_succeeded": True,
                "improvement_count": True,
                "mutation_scope": True,
                "required_commands": True,
                "unchanged_file": True,
                "fixed_decomposition": True,
            },
        },
        "usage": {"lifecycle": {"total_tokens": total_tokens}},
        "overall_usage_coverage": 1.0,
        "unknown_failed_usage_count": 0,
        "transport_retry_count": 0,
        "paired_task_designer": {
            "request_count": 2,
            "usage_observed": True,
            "transport_order": ["production", "shadow"],
            "execution_ids": ["production-call", "shadow-call"],
            "roles": {
                "production": {
                    "execution_id": "production-call",
                    "role": "production",
                    "projection_policy": item["production_policy"],
                    "transport_ordinal": 1,
                    "runtime_contract_hash": shared_hash,
                    "provider_input_tokens": 100,
                    "provider_output_tokens": 20,
                    "provider_total_tokens": 120,
                    "max_completion_tokens": 2_000,
                    "reasoning_mode": "disabled",
                    "usage_observed": True,
                    "finish_reason": "stop",
                    "response_content_length": 2,
                    "candidate_decisions": [
                        {"candidate_id": "sentinel:current", "action": "kept"}
                        if item["production_policy"] == "current"
                        else {"candidate_id": "sentinel:compact", "action": "kept"}
                    ],
                },
                "shadow": {
                    "execution_id": "shadow-call",
                    "role": "shadow",
                    "projection_policy": item["shadow_policy"],
                    "transport_ordinal": 2,
                    "runtime_contract_hash": shared_hash,
                    "provider_input_tokens": 80,
                    "provider_output_tokens": 15,
                    "provider_total_tokens": 95,
                    "max_completion_tokens": 2_000,
                    "reasoning_mode": "disabled",
                    "usage_observed": True,
                    "finish_reason": "stop",
                    "response_content_length": 2,
                    "candidate_decisions": [
                        {"candidate_id": "sentinel:current", "action": "kept"}
                        if item["shadow_policy"] == "current"
                        else {"candidate_id": "sentinel:compact", "action": "kept"}
                    ],
                },
            },
            "provider_total_tokens": 215,
        },
        "provider_usage_reconciliation": {
            "all_request_count": 10,
            "all_execution_ids": [
                "core-1",
                "core-2",
                "core-3",
                "enhancement-1",
                "enhancement-2",
                "enhancement-3",
                "enhancement-4",
                "enhancement-5",
                "production-call",
                "shadow-call",
            ],
            "paired_execution_ids_subset": True,
            "observed_event_total_tokens": total_tokens,
            "lifecycle_total_tokens": total_tokens,
            "passed": True,
        },
        "task_designer_context_intervention": {
            "scenario_id": item["scenario_id"],
            "production_policy": item["production_policy"],
            "shadow_policy": item["shadow_policy"],
            "source_fingerprint": source_fingerprint,
            "goal_hash": goal_hash,
            "completed_iteration": completed_iteration,
            "builder_entry_count": 1,
            "shared_snapshot_hash": shared_snapshot_hash,
            "candidate_contracts": deepcopy(
                protocol["scenario_candidate_contracts"][item["scenario_id"]]
            ),
            "candidate_fingerprints": {
                policy: paired._sha256(candidate_payloads[policy])
                for policy in ("current", "compact")
            },
            "canonical_candidate_payloads": deepcopy(candidate_payloads),
            "sentinel_candidate_ids": {
                "current": ["sentinel:current"],
                "compact": ["sentinel:compact"],
            },
            "runtime_contract_hash": shared_hash,
            "production_output_consumed_by_downstream": True,
            "shadow_output_consumed_by_downstream": False,
            "production_response_hash": "sha256:production-payload",
            "production_consumed_payload_hash": "sha256:production-payload",
            "shadow_response_hash": "sha256:shadow-payload",
            "downstream_consumed_payload_hashes": ["sha256:production-payload"],
            "shadow_completed": True,
            "shadow_side_effect_proof": side_effect_proof,
        },
        "guard_observation": {
            "logical_complete_calls_seen": 10,
            "provider_attempts_with_observed_usage": 10,
            "successful_response_usage_tokens_seen": total_tokens,
            "failed_attempt_usage_tokens_seen": 0,
            "observed_provider_attempt_usage_tokens_seen": total_tokens,
            "effective_max_completion_tokens": [
                4_096,
                2_000,
                2_000,
                4_096,
                2_000,
                2_000,
                600,
                1_000,
                2_000,
                2_000,
            ],
            "usage_censored": False,
            "unsettled_reserved_tokens": 0,
            "reservation_overrun_tokens": 0,
            "blocked_requests_before_transport": 0,
            "reservation_admission_failed": False,
            "blocked_reservation_tokens": 0,
            "blocking_limits": [],
        },
    }


def test_build_run_command_uses_v2_policy_scenario_and_dynamic_remaining_cap(
    tmp_path: Path,
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[-1]
    prior = [
        _paid_record(previous, total_tokens=25_000)
        for previous in paired.build_schedule(protocol)[:-1]
    ]

    command = paired.build_run_command(
        tmp_path / "run",
        item,
        protocol,
        records=prior,
        protocol_path=paired.PROTOCOL_PATH,
    )

    def value(flag: str) -> str:
        return command[command.index(flag) + 1]

    assert value("--task-designer-context-arm") == item["production_policy"]
    assert value("--task-designer-source-protocol") == str(
        paired.PROTOCOL_PATH.resolve()
    )
    assert value("--task-designer-scenario-id") == item["scenario_id"]
    assert value("--max-provider-tokens") == "3609"
    assert "--fixed-decomposition" in command
    assert "--memory-mode" in command
    assert value("--memory-mode") == "isolated_empty"


def test_build_run_command_applies_per_scenario_remaining_cap(tmp_path: Path) -> None:
    protocol = paired.load_protocol()
    first, second = paired.build_schedule(protocol)[:2]
    records = [_paid_record(first, total_tokens=31_000)]

    command = paired.build_run_command(
        tmp_path / "run",
        second,
        protocol,
        records=records,
        protocol_path=paired.PROTOCOL_PATH,
    )

    assert command[command.index("--max-provider-tokens") + 1] == "29000"


def _paired_events(item: dict) -> list[dict]:
    # The pair is embedded in a larger full-architecture trajectory.  The
    # non-paired call makes it impossible to confuse Guard/full-run counts with
    # the two-request Task Designer trace.
    events = [
        {
            "event_type": "llm_requested",
            "payload": {
                "correlation": {"execution_id": "core-call"},
                "context_selection": {"request_purpose": "project_improvement"},
            },
        },
        {
            "event_type": "llm_responded",
            "payload": {
                "correlation": {"execution_id": "core-call"},
                "usage": {
                    "input_tokens": 9_000,
                    "output_tokens": 785,
                    "total_tokens": 9_785,
                },
            },
        },
    ]
    for ordinal, (role, policy, input_tokens, output_tokens) in enumerate(
        [
            ("production", item["production_policy"], 100, 20),
            ("shadow", item["shadow_policy"], 80, 15),
        ],
        start=1,
    ):
        execution_id = f"{role}-call"
        events.append(
            {
                "event_type": "llm_requested",
                "payload": {
                    "correlation": {"execution_id": execution_id},
                    "context_selection": {
                        "request_purpose": "iteration_task_design",
                        "final_prompt_tokens": input_tokens - 5,
                    },
                    "reasoning_policy": {"mode": "disabled"},
                    "trace_info": {
                        "completion_budget": {"reserved_tokens": 2_000},
                        "paired_shadow": {
                            "role": role,
                            "projection_policy": policy,
                            "transport_ordinal": ordinal,
                            "runtime_contract_hash": "sha256:paired-runtime",
                        },
                    },
                },
            }
        )
        events.append(
            {
                "event_type": "llm_responded",
                "payload": {
                    "correlation": {"execution_id": execution_id},
                    "finish_reason": "stop",
                    "provider_details": {"content_length": 2},
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                    },
                },
            }
        )
    return events


def test_observer_uses_real_retry_request_max_not_product_reservation() -> None:
    """Freeze the two Stage9 V2 retry request shapes from 2026-08-04."""

    item = paired.build_schedule()[0]
    events: list[dict] = []
    for ordinal, (role, policy) in enumerate(
        [
            ("production", item["production_policy"]),
            ("shadow", item["shadow_policy"]),
        ],
        start=1,
    ):
        execution_id = f"real-retry-{role}"
        trace_info = {
            "diagnostics": {"max_tokens": 1_000},
            "paired_shadow": {
                "role": role,
                "projection_policy": policy,
                "transport_ordinal": ordinal,
                "runtime_contract_hash": "sha256:real-retry-contract",
            },
        }
        if role == "production":
            trace_info["completion_budget"] = {
                "purpose": "iteration_task_design",
                "remaining_tokens": 10_761,
                "reservation_id": "enhancement:real-retry",
                "reserved_tokens": 1_000,
            }
        events.extend(
            [
                {
                    "event_type": "llm_requested",
                    "payload": {
                        "correlation": {"execution_id": execution_id},
                        "context_selection": {
                            "request_purpose": "iteration_task_design",
                            "final_prompt_tokens": 2_813 if role == "production" else 1_634,
                            "candidate_decisions": [],
                        },
                        "reasoning_policy": {"mode": "disabled"},
                        "trace_info": trace_info,
                    },
                },
                {
                    "event_type": "llm_responded",
                    "payload": {
                        "correlation": {"execution_id": execution_id},
                        "finish_reason": "stop",
                        "provider_details": {"content_length": 600},
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                    },
                },
            ]
        )

    observation = paired.observe_paired_task_designer(events)
    production = observation["roles"]["production"]
    shadow = observation["roles"]["shadow"]

    assert production["max_completion_tokens"] == 1_000
    assert shadow["max_completion_tokens"] == 1_000
    assert production["product_reservation_tokens"] == 1_000
    assert shadow["product_reservation_tokens"] == 0
    assert observation["validation_failures"] == []


def test_integrated_record_extracts_real_pair_and_preserves_v1_quality_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    descriptor = _paid_record(item)["task_designer_context_intervention"]
    integrated_guard = deepcopy(_paid_record(item)["guard_observation"])
    integrated_guard.update(
        logical_complete_calls_seen=3,
        provider_attempts_with_observed_usage=3,
        effective_max_completion_tokens=[4_096, 2_000, 2_000],
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "provider_runtime_identity": protocol["provider_identity"],
                "guard_observation": integrated_guard,
                "task_designer_context_intervention": descriptor,
                "quality_gate": {
                    "passed": True,
                    "signature": "sha256:forged-manifest-self-report",
                },
            }
        ),
        encoding="utf-8",
    )
    base = {
        "quality_gate": {
            "passed": True,
            "signature": "sha256:integrated-adapter-recomputed",
            "checks": {"mutation_scope": True},
        },
        "observed_enhancement_mutations": {
            "all_changed_paths": ["/project/calculator.py"]
        },
        "user_owned_mutations": {
            "all_changed_paths": ["/project/calculator.py"]
        },
        "runtime_owned_mutations": {"all_changed_paths": []},
        "producer_validation": {"passed": True},
        "usage": {"lifecycle": {"total_tokens": 10_000}},
    }
    import run_observation
    import stage9_task_designer_provider_sentinel as v1

    monkeypatch.setattr(run_observation, "_load_events", lambda path: _paired_events(item))
    monkeypatch.setattr(v1, "build_integrated_run_record", lambda *args, **kwargs: deepcopy(base))

    record = paired.build_integrated_run_record(
        run_dir,
        schedule_item=item,
        protocol=protocol,
        code_snapshot="sha256:code",
    )

    assert record["paired_task_designer"]["request_count"] == 2
    assert record["paired_task_designer"]["execution_ids"] == [
        "production-call",
        "shadow-call",
    ]
    assert record["paired_task_designer"]["transport_order"] == [
        "production",
        "shadow",
    ]
    assert record["paired_task_designer"]["roles"]["production"][
        "projection_policy"
    ] == item["production_policy"]
    assert record["paired_task_designer"]["roles"]["shadow"][
        "projection_policy"
    ] == item["shadow_policy"]
    assert record["paired_task_designer"]["roles"]["production"][
        "max_completion_tokens"
    ] == 2_000
    assert record["paired_task_designer"]["roles"]["shadow"][
        "max_completion_tokens"
    ] == 2_000
    assert record["paired_task_designer"]["roles"]["production"][
        "reasoning_mode"
    ] == "disabled"
    assert record["paired_task_designer"]["roles"]["shadow"][
        "reasoning_mode"
    ] == "disabled"
    assert record["quality_gate"] == base["quality_gate"]
    assert record["quality_gate"]["signature"] != (
        "sha256:forged-manifest-self-report"
    )
    assert record["user_owned_mutations"] == base["user_owned_mutations"]
    assert record["producer_validation"] == {"passed": True}
    assert record["provider_usage_reconciliation"] == {
        "all_request_count": 3,
        "all_execution_ids": ["core-call", "production-call", "shadow-call"],
        "paired_execution_ids_subset": True,
        "observed_event_total_tokens": 10_000,
        "lifecycle_total_tokens": 10_000,
        "passed": True,
    }


def test_integrated_record_extracts_primary_scope_stop_from_manifest_and_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    run_dir = tmp_path / "primary-stop"
    run_dir.mkdir()
    reason = "production response did not describe an authorized task"
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "provider_runtime_identity": protocol["provider_identity"],
                "outcome": {
                    "completed": True,
                    "result": {
                        "error_type": "PairedShadowStopped",
                        "error": None,
                        "session_result": {
                            "error_type": "PairedShadowStopped",
                            "error": None,
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    events = [
        {
            "event_type": "pipeline_failed",
            "payload": {
                "error": reason,
                "output_summary": {
                    "error_type": "PairedShadowStopped",
                    "failure_reason": reason,
                },
            },
        }
    ]
    base = {
        "quality_gate": {"passed": False, "checks": {}, "signature": "sha256:x"},
        "usage": {"lifecycle": {"total_tokens": 10_000}},
    }
    import run_observation
    import stage9_task_designer_provider_sentinel as v1

    monkeypatch.setattr(run_observation, "_load_events", lambda path: events)
    monkeypatch.setattr(v1, "build_integrated_run_record", lambda *args, **kwargs: base)

    record = paired.build_integrated_run_record(
        run_dir,
        schedule_item=item,
        protocol=protocol,
        code_snapshot="sha256:code",
    )

    assert record["primary_stop_reason"] == (
        f"PairedShadowStopped: {reason}"
    )


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda record: record["paired_task_designer"].update(request_count=1),
            "paired_request_membership_invalid",
        ),
        (
            lambda record: record["paired_task_designer"]["roles"].pop("shadow"),
            "paired_role_membership_invalid",
        ),
        (
            lambda record: record["paired_task_designer"]["roles"]["shadow"].update(
                projection_policy=record["production_policy"]
            ),
            "paired_projection_policy_mismatch",
        ),
        (
            lambda record: record["paired_task_designer"]["roles"]["shadow"].update(
                runtime_contract_hash="sha256:drift"
            ),
            "paired_runtime_contract_mismatch",
        ),
        (
            lambda record: record["paired_task_designer"]["roles"]["shadow"].update(
                max_completion_tokens=1_999
            ),
            "paired_max_completion_mismatch",
        ),
        (
            lambda record: record["paired_task_designer"]["roles"]["shadow"].update(
                reasoning_mode="enabled"
            ),
            "paired_reasoning_policy_mismatch",
        ),
        (
            lambda record: record["paired_task_designer"].update(usage_observed=False),
            "paired_usage_incomplete",
        ),
        (
            lambda record: record["paired_task_designer"]["roles"]["production"].update(
                response_content_length=0
            ),
            "paired_response_incomplete",
        ),
        (
            lambda record: record["paired_task_designer"]["roles"]["shadow"].update(
                finish_reason="length"
            ),
            "paired_response_incomplete",
        ),
        (
            lambda record: record["task_designer_context_intervention"].pop(
                "shadow_side_effect_proof"
            ),
            "shadow_side_effect_proof_invalid",
        ),
        (
            lambda record: record["task_designer_context_intervention"][
                "shadow_side_effect_proof"
            ].update(memory_hash_after="sha256:drift"),
            "shadow_side_effect_proof_invalid",
        ),
        (
            lambda record: record["task_designer_context_intervention"].update(
                production_output_consumed_by_downstream=False
            ),
            "production_output_not_consumed",
        ),
        (
            lambda record: record["task_designer_context_intervention"].update(
                production_consumed_payload_hash="sha256:not-production"
            ),
            "production_consumption_evidence_invalid",
        ),
        (
            lambda record: record["task_designer_context_intervention"][
                "downstream_consumed_payload_hashes"
            ].append("sha256:shadow-payload"),
            "shadow_output_contract_invalid",
        ),
        (
            lambda record: record["quality_gate"].update(passed=False),
            "quality_gate_failed",
        ),
        (
            lambda record: record["guard_observation"].update(usage_censored=True),
            "guard_usage_censored",
        ),
        (
            lambda record: record["guard_observation"].update(
                reservation_admission_failed=True
            ),
            "guard_reservation_admission_failed",
        ),
        (
            lambda record: record["paired_task_designer"].update(
                execution_ids=["production-call", "not-in-full-events"]
            ),
            "paired_execution_membership_invalid",
        ),
        (
            lambda record: record["guard_observation"].update(
                effective_max_completion_tokens=record["guard_observation"][
                    "effective_max_completion_tokens"
                ][:-1]
            ),
            "guard_effective_completion_count_mismatch",
        ),
        (
            lambda record: record["provider_usage_reconciliation"].update(
                observed_event_total_tokens=9_999, passed=False
            ),
            "provider_usage_lifecycle_mismatch",
        ),
        (
            lambda record: record["usage"]["lifecycle"].update(
                total_tokens=9_999
            ),
            "provider_usage_lifecycle_mismatch",
        ),
        (
            lambda record: record["guard_observation"].update(
                unsettled_reserved_tokens=1
            ),
            "guard_unsettled_reservation",
        ),
        (
            lambda record: record["guard_observation"].update(
                reservation_overrun_tokens=1
            ),
            "guard_reservation_overrun",
        ),
        (
            lambda record: record["guard_observation"].update(
                blocked_requests_before_transport=1
            ),
            "guard_request_blocked",
        ),
    ],
)
def test_arm_gate_fails_closed_for_paired_contract_tampering(mutate, reason) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    record = _paid_record(item)
    mutate(record)

    assert reason in paired.arm_stop_reasons(record, protocol)


def test_arm_gate_accepts_complete_paired_record(tmp_path: Path) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]

    record = _attach_v1_mutation_evidence(_paid_record(item), tmp_path)
    assert paired.arm_stop_reasons(record, protocol) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ordinal", 99),
        ("scenario_pair", 99),
        ("scenario_id", "relevant_iteration_memory"),
        ("production_policy", "compact"),
        ("shadow_policy", "current"),
        ("request_order", ["shadow", "production"]),
    ],
)
def test_arm_gate_binds_record_to_exact_frozen_schedule_item(
    tmp_path: Path, field: str, value
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    record = _attach_v1_mutation_evidence(_paid_record(item), tmp_path)
    record[field] = value

    assert "frozen_schedule_item_mismatch" in paired.arm_stop_reasons(
        record, protocol
    )


@pytest.mark.parametrize(
    ("missing", "reason"),
    [
        ("overall_usage_coverage", "usage_coverage_evidence_missing"),
        ("unknown_failed_usage_count", "unknown_failed_usage_evidence_missing"),
        ("transport_retry_count", "transport_retry_evidence_missing"),
    ],
)
def test_arm_gate_rejects_missing_usage_and_retry_evidence(
    tmp_path: Path, missing: str, reason: str
) -> None:
    protocol = paired.load_protocol()
    record = _attach_v1_mutation_evidence(
        _paid_record(paired.build_schedule(protocol)[0]), tmp_path
    )
    record.pop(missing)

    assert reason in paired.arm_stop_reasons(record, protocol)


def test_arm_gate_recomputes_candidate_fingerprints_from_canonical_payloads(
    tmp_path: Path,
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    record = _attach_v1_mutation_evidence(_paid_record(item), tmp_path)
    record["task_designer_context_intervention"]["candidate_fingerprints"][
        "current"
    ] = "sha256:forged-but-nonempty"

    assert "paired_candidate_fingerprint_mismatch" in paired.arm_stop_reasons(
        record, protocol
    )


def test_arm_gate_recomputes_sentinel_ids_instead_of_trusting_kept_self_report(
    tmp_path: Path,
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    record = _attach_v1_mutation_evidence(_paid_record(item), tmp_path)
    intervention = record["task_designer_context_intervention"]
    intervention["sentinel_candidate_ids"][item["production_policy"]] = [
        "sentinel:forged"
    ]
    record["paired_task_designer"]["roles"]["production"][
        "candidate_decisions"
    ].append({"candidate_id": "sentinel:forged", "action": "kept"})

    assert "paired_candidate_sentinel_mismatch" in paired.arm_stop_reasons(
        record, protocol
    )


@pytest.mark.parametrize("payload_tamper", ["required_present", "required_absent"])
def test_arm_gate_rechecks_candidate_contract_against_canonical_payloads(
    tmp_path: Path, payload_tamper: str
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    record = _attach_v1_mutation_evidence(_paid_record(item), tmp_path)
    intervention = record["task_designer_context_intervention"]
    policy = "compact"
    contract = protocol["scenario_candidate_contracts"][item["scenario_id"]][policy]
    if payload_tamper == "required_present":
        intervention["canonical_candidate_payloads"][policy][0]["content"] = ""
    else:
        intervention["canonical_candidate_payloads"][policy][0]["content"] += (
            " " + contract["required_absent"][0]
        )
    intervention["candidate_fingerprints"][policy] = paired._sha256(
        intervention["canonical_candidate_payloads"][policy]
    )

    assert "paired_candidate_payload_contract_invalid" in paired.arm_stop_reasons(
        record, protocol
    )


def test_arm_gate_recomputes_shared_snapshot_hash_from_descriptor_sources(
    tmp_path: Path,
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    record = _attach_v1_mutation_evidence(_paid_record(item), tmp_path)
    record["task_designer_context_intervention"][
        "shared_snapshot_hash"
    ] = "sha256:forged-but-nonempty"

    assert "paired_shared_snapshot_hash_mismatch" in paired.arm_stop_reasons(
        record, protocol
    )


@pytest.mark.parametrize("signature", [None, ""])
def test_arm_gate_requires_nonempty_integrated_quality_signature(
    tmp_path: Path, signature: str | None
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    record = _attach_v1_mutation_evidence(_paid_record(item), tmp_path)
    record["quality_gate"]["signature"] = signature

    assert "quality_gate_signature_invalid" in paired.arm_stop_reasons(
        record, protocol
    )


def test_default_subprocess_adapter_persists_record_before_gate_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    output = tmp_path / "campaign"
    commands: list[list[str]] = []

    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable", raising=False)
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [item])
    monkeypatch.setattr(
        paired,
        "build_run_command",
        lambda run_dir, schedule_item, protocol, **kwargs: ["runner", str(run_dir)],
        raising=False,
    )

    def run(command, **kwargs):
        del kwargs
        commands.append(command)
        run_dir = Path(command[-1])
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(paired, "subprocess", SimpleNamespace(run=run), raising=False)
    monkeypatch.setattr(
        paired,
        "build_integrated_run_record",
        lambda *args, **kwargs: _paid_record(item),
        raising=False,
    )

    def gate(record, frozen):
        del record, frozen
        record_path = next(output.glob("*/campaign_record.json"))
        assert record_path.is_file()
        return ["paired_usage_incomplete"]

    monkeypatch.setattr(paired, "arm_stop_reasons", gate, raising=False)

    with pytest.raises(paired.PairedShadowStopped, match="paired_usage_incomplete"):
        paired.execute_campaign(output_dir=output, protocol=protocol)

    assert len(commands) == 1
    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    assert state["stop_reasons"] == ["paired_usage_incomplete"]


def test_default_adapter_rejects_nonzero_returncode_even_with_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    built: list[bool] = []

    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [item])
    monkeypatch.setattr(
        paired,
        "build_run_command",
        lambda run_dir, schedule_item, protocol, **kwargs: ["runner", str(run_dir)],
    )

    def run(command, **kwargs):
        del kwargs
        run_dir = Path(command[-1])
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(paired.subprocess, "run", run)
    monkeypatch.setattr(
        paired,
        "build_integrated_run_record",
        lambda *args, **kwargs: built.append(True),
    )

    with pytest.raises(paired.PairedShadowStopped, match="returncode=2"):
        paired.execute_campaign(output_dir=tmp_path / "nonzero", protocol=protocol)

    assert built == []
    state = json.loads(
        (tmp_path / "nonzero" / "campaign_state.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "stopped"


def test_campaign_rejects_cross_arm_execution_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    first, second = paired.build_schedule(protocol)[:2]
    output = tmp_path / "replay"

    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [first, second])

    def run_arm(item, run_dir, frozen):
        del run_dir, frozen
        # Both records intentionally reuse production-call/shadow-call from the
        # helper, while all frozen schedule fields otherwise match each arm.
        return _attach_v1_mutation_evidence(
            _paid_record(item), tmp_path / f"arm-{item['ordinal']}"
        )

    with pytest.raises(paired.PairedShadowStopped, match="replay|duplicate"):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=run_arm,
        )

    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    assert len(state["records"]) == 2
    assert any(
        "replay" in reason or "duplicate" in reason
        for reason in state["stop_reasons"]
    )


def _isolated_campaign_record(item: dict) -> dict:
    record = _paid_record(item)
    production_id = f"production-call-{item['ordinal']}"
    shadow_id = f"shadow-call-{item['ordinal']}"
    paired_observation = record["paired_task_designer"]
    paired_observation["execution_ids"] = [production_id, shadow_id]
    paired_observation["roles"]["production"]["execution_id"] = production_id
    paired_observation["roles"]["shadow"]["execution_id"] = shadow_id
    reconciliation = record["provider_usage_reconciliation"]
    reconciliation["all_execution_ids"][-2:] = [production_id, shadow_id]
    return record


def test_execute_campaign_default_still_runs_full_frozen_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    schedule = paired.build_schedule(protocol)
    seen: list[int] = []
    output = tmp_path / "default-full-campaign"
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "arm_stop_reasons", lambda record, protocol: [])

    def run_arm(item, run_dir, frozen):
        del run_dir, frozen
        seen.append(item["ordinal"])
        return _isolated_campaign_record(item)

    result = paired.execute_campaign(
        output_dir=output,
        protocol=protocol,
        run_arm=run_arm,
    )

    assert seen == [item["ordinal"] for item in schedule]
    assert len(result["records"]) == 6
    assert result["eligible"] is True
    assert result["spend"]["historical_tokens"] == int(
        protocol["historical_stage9_spend"]["observed_complete_tokens"]
    )
    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"


@pytest.mark.parametrize("additional_historical_tokens", [-1, 1.5, "12790", True])
def test_execute_campaign_rejects_invalid_additional_historical_tokens_before_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    additional_historical_tokens,
) -> None:
    protocol = paired.load_protocol()
    output = tmp_path / f"invalid-additional-{additional_historical_tokens}"
    called: list[bool] = []
    monkeypatch.setattr(
        paired,
        "preflight",
        lambda protocol=None: pytest.fail("validation must precede preflight"),
    )

    with pytest.raises(paired.PairedShadowError, match="additional_historical_tokens"):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=lambda *args: called.append(True),
            additional_historical_tokens=additional_historical_tokens,
        )

    assert called == []
    assert output.exists() is False


def test_build_run_command_dynamic_cap_includes_additional_historical_tokens() -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    records = [
        {
            "scenario_id": "partial_shared_criterion",
            "usage": {"lifecycle": {"total_tokens": 100_000}},
        }
    ]

    command = paired.build_run_command(
        Path("/tmp/stage9-budget-ledger-test"),
        item,
        protocol,
        records=records,
        additional_historical_tokens=12_790,
    )

    cap_index = command.index("--max-provider-tokens") + 1
    assert int(command[cap_index]) == (
        int(protocol["token_limits"]["stage9_lifetime_hard"])
        - int(protocol["historical_stage9_spend"]["observed_complete_tokens"])
        - 12_790
        - 100_000
    )


def test_execute_campaign_additional_history_updates_sentinel_spend_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    output = tmp_path / "additional-history-sentinel"
    record = _isolated_campaign_record(item)
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "arm_stop_reasons", lambda record, protocol: [])

    result = paired.execute_campaign(
        output_dir=output,
        protocol=protocol,
        run_arm=lambda *args: record,
        max_runs=1,
        additional_historical_tokens=12_790,
    )

    assert result["eligible"] is False
    assert result["spend"]["historical_tokens"] == 64_181
    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "sentinel_completed"
    assert state["spend"]["historical_tokens"] == 64_181


def test_execute_campaign_max_runs_one_is_sentinel_only_not_campaign_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    first = paired.build_schedule(protocol)[0]
    seen: list[int] = []
    output = tmp_path / "one-arm-sentinel"
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "arm_stop_reasons", lambda record, protocol: [])

    def run_arm(item, run_dir, frozen):
        del run_dir, frozen
        seen.append(item["ordinal"])
        return _isolated_campaign_record(item)

    result = paired.execute_campaign(
        output_dir=output,
        protocol=protocol,
        run_arm=run_arm,
        max_runs=1,
    )

    assert seen == [first["ordinal"]]
    assert len(result["records"]) == 1
    assert result["eligible"] is False
    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "sentinel_completed"
    assert state["stop_reasons"] == []


@pytest.mark.parametrize("max_runs", [0, -1, 7])
def test_execute_campaign_rejects_invalid_max_runs_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_runs: int
) -> None:
    protocol = paired.load_protocol()
    output = tmp_path / f"invalid-max-runs-{max_runs}"
    called: list[bool] = []
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})

    with pytest.raises(paired.PairedShadowError, match="max_runs"):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=lambda *args: called.append(True),
            max_runs=max_runs,
        )

    assert called == []
    assert output.exists() is False


def test_campaign_primary_stop_is_record_first_and_skips_incomplete_arm_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    output = tmp_path / "primary-stop-campaign"
    reason = "PairedShadowStopped: production target authority violation"
    record = _isolated_campaign_record(item)
    record["primary_stop_reason"] = reason
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [item])
    monkeypatch.setattr(
        paired,
        "arm_stop_reasons",
        lambda *args: pytest.fail("incomplete-arm derivative gates must be skipped"),
    )

    with pytest.raises(paired.PairedShadowStopped, match="target authority"):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=lambda *args: record,
        )

    assert next(output.glob("*/campaign_record.json")).is_file()
    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    assert state["stop_reasons"] == [reason]


@pytest.mark.parametrize(
    ("guard_update", "independent_reason"),
    [
        ({"usage_censored": True}, "guard_usage_censored"),
        (
            {"reservation_admission_failed": True},
            "guard_reservation_admission_failed",
        ),
        ({"reservation_overrun_tokens": 1}, "guard_reservation_overrun"),
        ({"unsettled_reserved_tokens": 1}, "guard_unsettled_reservation"),
        ({"blocked_requests_before_transport": 1}, "guard_request_blocked"),
    ],
)
def test_campaign_primary_stop_preserves_independent_guard_hard_failures_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guard_update: dict,
    independent_reason: str,
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    reason = "PairedShadowStopped: production response invalid"
    record = _isolated_campaign_record(item)
    record["primary_stop_reason"] = reason
    record["guard_observation"].update(guard_update)
    # Prove incomplete paired-arm failures remain derivative noise even when a
    # real, independent guard hard failure must survive primary-stop collapse.
    record["paired_task_designer"]["request_count"] = 1
    record["paired_task_designer"]["roles"].pop("shadow")
    output = tmp_path / independent_reason
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [item])

    with pytest.raises(paired.PairedShadowStopped):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=lambda *args: record,
        )

    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["stop_reasons"] == [reason, independent_reason]
    assert not any(
        stop_reason.startswith("paired_")
        for stop_reason in state["stop_reasons"]
    )


@pytest.mark.parametrize(
    ("total_tokens", "independent_reasons"),
    [
        (30_001, ["per_run_hard_limit_exceeded"]),
        (
            60_001,
            ["per_run_hard_limit_exceeded", "per_scenario_hard_limit_exceeded"],
        ),
    ],
)
def test_campaign_primary_stop_preserves_per_run_and_per_scenario_hard_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    total_tokens: int,
    independent_reasons: list[str],
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    reason = "PairedShadowStopped: production response invalid"
    record = _isolated_campaign_record(item)
    record["primary_stop_reason"] = reason
    record["usage"]["lifecycle"]["total_tokens"] = total_tokens
    record["paired_task_designer"]["request_count"] = 1
    record["paired_task_designer"]["roles"].pop("shadow")
    output = tmp_path / f"primary-hard-{total_tokens}"
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [item])

    with pytest.raises(paired.PairedShadowStopped):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=lambda *args: record,
        )

    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["stop_reasons"] == [reason, *independent_reasons]
    assert not any(
        stop_reason.startswith("paired_")
        for stop_reason in state["stop_reasons"]
    )


def test_campaign_primary_stop_preserves_independent_accounting_hard_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    output = tmp_path / "primary-plus-accounting"
    reason = "PairedShadowStopped: production response invalid"
    record = _isolated_campaign_record(item)
    record["primary_stop_reason"] = reason
    record["usage"]["lifecycle"]["total_tokens"] = int(
        protocol["token_limits"]["stage9_lifetime_hard"]
    )
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [item])
    monkeypatch.setattr(
        paired,
        "arm_stop_reasons",
        lambda *args: pytest.fail("incomplete-arm derivative gates must be skipped"),
    )

    with pytest.raises(paired.PairedShadowStopped):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=lambda *args: record,
        )

    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["stop_reasons"] == [
        reason,
        "per_run_hard_limit_exceeded",
        "per_scenario_hard_limit_exceeded",
        "campaign_hard_limit_exceeded",
    ]


def test_campaign_primary_stop_preserves_source_drift_after_record_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    output = tmp_path / "primary-plus-source-drift"
    reason = "PairedShadowStopped: production response invalid"
    record = _isolated_campaign_record(item)
    record["primary_stop_reason"] = reason
    snapshots = 0

    def source_snapshot(_protocol):
        nonlocal snapshots
        snapshots += 1
        return "sha256:drift" if snapshots == 4 else "sha256:stable"

    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", source_snapshot)
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [item])
    monkeypatch.setattr(
        paired,
        "arm_stop_reasons",
        lambda *args: pytest.fail("incomplete-arm derivative gates must be skipped"),
    )

    with pytest.raises(paired.PairedShadowStopped, match="source snapshot changed"):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=lambda *args: record,
        )

    assert next(output.glob("*/campaign_record.json")).is_file()
    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["stop_reasons"] == [
        reason,
        "campaign source snapshot changed after record build",
    ]


def _first_arm_reanalysis_path() -> Path:
    return paired.HERE / "STAGE9_TASK_DESIGNER_PAIRED_SHADOW_V2_FIRST_ARM_REANALYSIS.json"


def test_reanalysis_resume_prefix_rebuilds_hash_verified_corrected_first_record() -> None:
    protocol = paired.load_protocol()

    prefix = paired.load_reanalysis_resume_prefix(
        _first_arm_reanalysis_path(), protocol=protocol
    )

    assert prefix["seed_lifecycle_tokens"] == 14_950
    assert prefix["next_ordinal"] == 2
    assert len(prefix["records"]) == 1
    record = prefix["records"][0]
    assert {
        field: record[field]
        for field in (
            "ordinal",
            "scenario_pair",
            "scenario_id",
            "production_policy",
            "shadow_policy",
            "request_order",
        )
    } == {
        field: paired.build_schedule(protocol)[0][field]
        for field in (
            "ordinal",
            "scenario_pair",
            "scenario_id",
            "production_policy",
            "shadow_policy",
            "request_order",
        )
    }
    assert record["paired_task_designer"]["roles"]["shadow"][
        "max_completion_tokens"
    ] == 1_000
    assert paired.arm_stop_reasons(record, protocol) == []


def test_execute_campaign_resumes_after_frozen_prefix_without_double_counting_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    schedule = paired.build_schedule(protocol)
    output = tmp_path / "resumed-six-arm-campaign"
    seen: list[int] = []
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "arm_stop_reasons", lambda record, protocol: [])

    def run_arm(item, run_dir, frozen):
        del run_dir, frozen
        seen.append(item["ordinal"])
        return _isolated_campaign_record(item)

    result = paired.execute_campaign(
        output_dir=output,
        protocol=protocol,
        run_arm=run_arm,
        resume_reference_path=_first_arm_reanalysis_path(),
        additional_historical_tokens=12_790,
    )

    assert seen == [2, 3, 4, 5, 6]
    assert [record["ordinal"] for record in result["records"]] == [1, 2, 3, 4, 5, 6]
    assert result["eligible"] is True
    assert result["opening_historical_tokens"] == 79_131
    assert result["opening_remaining_tokens"] == 100_869
    assert result["spend"] == {
        "historical_tokens": 79_131,
        "observed_lifecycle_tokens": 50_000,
        "held_unknown_usage_tokens": 0,
        "effective_campaign_tokens": 129_131,
        "remaining_campaign_tokens": 50_869,
        "hard_failures": [],
    }
    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert len(state["records"]) == 6


def test_resume_prefix_seeds_execution_id_replay_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    second = paired.build_schedule(protocol)[1]
    seed_record = json.loads(
        (
            paired.HERE
            / "runs/stage9_v2_first_arm_retry_20260804_02/"
            "01_p1_strongly_related_diagnosis_current_production/campaign_record.json"
        ).read_text(encoding="utf-8")
    )
    replay_ids = seed_record["paired_task_designer"]["execution_ids"]
    record = _isolated_campaign_record(second)
    record["paired_task_designer"]["execution_ids"] = list(replay_ids)
    record["paired_task_designer"]["roles"]["production"]["execution_id"] = replay_ids[0]
    record["paired_task_designer"]["roles"]["shadow"]["execution_id"] = replay_ids[1]
    output = tmp_path / "resume-replay"
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "arm_stop_reasons", lambda record, protocol: [])

    with pytest.raises(paired.PairedShadowStopped, match="replay"):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=lambda *args: record,
            resume_reference_path=_first_arm_reanalysis_path(),
            additional_historical_tokens=12_790,
        )

    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert [item["ordinal"] for item in state["records"]] == [1, 2]
    assert "paired_execution_id_replay" in state["stop_reasons"]


def test_resume_dynamic_caps_do_not_double_count_new_scenario_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    output = tmp_path / "resume-cap-no-double-count"
    totals = {2: 10_000, 3: 20_000, 4: 10_000, 5: 30_000, 6: 10_000}
    caps: dict[int, int] = {}
    historical_carries: dict[int, dict[str, int]] = {}
    original_build_run_command = paired.build_run_command
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "arm_stop_reasons", lambda record, protocol: [])

    def observed_build_run_command(
        run_dir, schedule_item, frozen, *, prior_scenario_tokens=None, **kwargs
    ):
        historical_carries[schedule_item["ordinal"]] = dict(
            prior_scenario_tokens or {}
        )
        return original_build_run_command(
            run_dir,
            schedule_item,
            frozen,
            prior_scenario_tokens=prior_scenario_tokens,
            **kwargs,
        )

    def run(command, **kwargs):
        del kwargs
        run_dir = Path(command[command.index("--output-dir") + 1])
        ordinal = int(run_dir.name.split("_", 1)[0])
        caps[ordinal] = int(command[command.index("--max-provider-tokens") + 1])
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    def integrated_record(run_dir, *, schedule_item, protocol, code_snapshot):
        del run_dir, protocol, code_snapshot
        record = _isolated_campaign_record(schedule_item)
        record["usage"]["lifecycle"]["total_tokens"] = totals[
            schedule_item["ordinal"]
        ]
        return record

    monkeypatch.setattr(paired, "build_run_command", observed_build_run_command)
    monkeypatch.setattr(paired.subprocess, "run", run)
    monkeypatch.setattr(paired, "build_integrated_run_record", integrated_record)

    result = paired.execute_campaign(
        output_dir=output,
        protocol=protocol,
        resume_reference_path=_first_arm_reanalysis_path(),
        additional_historical_tokens=12_790,
    )

    assert caps == {2: 30_000, 3: 30_000, 4: 30_000, 5: 30_000, 6: 30_000}
    assert historical_carries == {
        ordinal: {"strongly_related_diagnosis": 27_740}
        for ordinal in range(2, 7)
    }
    assert [record["ordinal"] for record in result["records"]] == [1, 2, 3, 4, 5, 6]
    assert result["eligible"] is True
    assert result["spend"] == {
        "historical_tokens": 79_131,
        "observed_lifecycle_tokens": 80_000,
        "held_unknown_usage_tokens": 0,
        "effective_campaign_tokens": 159_131,
        "remaining_campaign_tokens": 20_869,
        "hard_failures": [],
    }


@pytest.mark.parametrize("invalid_prefix", ["hash", "noncontiguous", "seed_gate"])
def test_invalid_resume_prefix_fails_before_preflight_provider_or_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_prefix: str,
) -> None:
    protocol = paired.load_protocol()
    reference = json.loads(
        _first_arm_reanalysis_path().read_text(encoding="utf-8")
    )
    if invalid_prefix == "hash":
        reference["immutable_source_hashes"]["campaign_record_sha256"] = (
            "sha256:" + "0" * 64
        )
    elif invalid_prefix == "noncontiguous":
        reference["production_policy"] = "compact"
        reference["shadow_policy"] = "current"
    reference_path = tmp_path / f"{invalid_prefix}.json"
    reference_path.write_text(json.dumps(reference), encoding="utf-8")
    output = tmp_path / f"{invalid_prefix}-output"
    monkeypatch.setattr(
        paired,
        "preflight",
        lambda protocol=None: pytest.fail("resume validation must precede preflight"),
    )
    if invalid_prefix == "seed_gate":
        monkeypatch.setattr(
            paired,
            "arm_stop_reasons",
            lambda record, protocol: ["seed_quality_gate_failed"],
        )

    with pytest.raises(paired.PairedShadowError, match="hash|prefix|seed|schedule|gate"):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=lambda *args: pytest.fail("provider arm must not run"),
            resume_reference_path=reference_path,
            additional_historical_tokens=12_790,
        )

    assert output.exists() is False


def test_default_adapter_stops_on_source_snapshot_drift_before_record_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    hashes = iter(["sha256:start", "sha256:start", "sha256:drift"])
    built: list[bool] = []

    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: next(hashes), raising=False)
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [item])
    monkeypatch.setattr(
        paired,
        "build_run_command",
        lambda run_dir, schedule_item, protocol, **kwargs: ["runner", str(run_dir)],
        raising=False,
    )

    def run(command, **kwargs):
        del kwargs
        run_dir = Path(command[-1])
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(paired, "subprocess", SimpleNamespace(run=run), raising=False)
    monkeypatch.setattr(
        paired,
        "build_integrated_run_record",
        lambda *args, **kwargs: built.append(True),
        raising=False,
    )

    with pytest.raises(paired.PairedShadowStopped, match="snapshot changed"):
        paired.execute_campaign(output_dir=tmp_path / "campaign", protocol=protocol)

    assert built == []


def test_execute_negative_token_record_persists_atomic_accounting_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    record = _paid_record(item)
    record["usage"]["lifecycle"]["total_tokens"] = -1
    output = tmp_path / "negative-accounting"

    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", lambda protocol: "sha256:stable")
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [item])

    with pytest.raises(paired.PairedShadowStopped, match="accounting_error"):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            run_arm=lambda item, run_dir, protocol: record,
        )

    assert next(output.glob("*/campaign_record.json")).is_file()
    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    assert "accounting_error" in state["stop_reasons"]
    assert state["spend"]["accounting_error"] == "negative_token_accounting"


def test_campaign_rejects_protocol_object_path_split_before_output_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = deepcopy(paired.load_protocol())
    protocol["object_only_marker"] = "drift"
    output = tmp_path / "must-not-exist"
    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})

    with pytest.raises(paired.PairedShadowError, match="protocol.*path.*differ"):
        paired.execute_campaign(
            output_dir=output,
            protocol=protocol,
            protocol_path=paired.PROTOCOL_PATH,
            run_arm=lambda *args: pytest.fail("campaign must not start"),
        )

    assert output.exists() is False


@pytest.mark.parametrize("drift_check", ["after_record_build", "before_completed"])
def test_campaign_rejects_source_drift_after_record_and_before_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_check: str,
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    output = tmp_path / drift_check
    checks = 0
    drift_at = 4 if drift_check == "after_record_build" else 5

    def snapshot(_protocol):
        nonlocal checks
        checks += 1
        return "sha256:drift" if checks == drift_at else "sha256:stable"

    monkeypatch.setattr(paired, "preflight", lambda protocol=None: {"provider_calls": 0})
    monkeypatch.setattr(paired, "code_snapshot_sha256", snapshot)
    monkeypatch.setattr(paired, "build_schedule", lambda protocol=None: [item])
    monkeypatch.setattr(
        paired,
        "build_run_command",
        lambda run_dir, schedule_item, protocol, **kwargs: ["runner", str(run_dir)],
    )

    def run(command, **kwargs):
        del kwargs
        run_dir = Path(command[-1])
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(paired.subprocess, "run", run)
    monkeypatch.setattr(
        paired,
        "build_integrated_run_record",
        lambda *args, **kwargs: _paid_record(item),
    )
    monkeypatch.setattr(paired, "arm_stop_reasons", lambda record, protocol: [])

    with pytest.raises(paired.PairedShadowStopped, match="source snapshot changed"):
        paired.execute_campaign(output_dir=output, protocol=protocol)

    state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    assert checks >= drift_at


def _attach_v1_mutation_evidence(record: dict, tmp_path: Path) -> dict:
    project = tmp_path / "v1-project"
    project.mkdir(parents=True)
    calculator = project / "calculator.py"
    calculator.write_text(
        'def divide(a, b):\n'
        '    """Raises ValueError when denominator is zero."""\n'
        '    if b == 0:\n'
        '        raise ValueError("zero")\n'
        '    return a / b\n',
        encoding="utf-8",
    )
    changed = str(calculator.resolve())
    mutations = {
        "added_paths": [],
        "deleted_paths": [],
        "modified_paths": [changed],
        "all_changed_paths": [changed],
        "before_truncated": False,
        "after_truncated": False,
        "symlink_paths": [],
    }
    record.update(
        project_root=str(project.resolve()),
        calculator_path=changed,
        modified_paths=[changed],
        observed_project_mutations=deepcopy(mutations),
        observed_enhancement_mutations=deepcopy(mutations),
        runtime_owned_mutations={
            "added_paths": [],
            "deleted_paths": [],
            "modified_paths": [],
            "all_changed_paths": [],
        },
        user_owned_mutations=deepcopy(mutations),
        mutation_classification_failures=[],
        producer_validation={"passed": True, "failures": []},
    )
    return record


@pytest.mark.parametrize(
    ("remove", "reason"),
    [
        ("observed_project_mutations", "v1_mutation_evidence_missing"),
        ("observed_enhancement_mutations", "v1_mutation_evidence_missing"),
        ("project_root", "v1_mutation_evidence_missing"),
        ("calculator_path", "v1_mutation_evidence_missing"),
        ("producer_validation", "producer_validation_missing"),
    ],
)
def test_arm_gate_requires_v1_mutation_and_producer_evidence(
    tmp_path: Path, remove: str, reason: str
) -> None:
    protocol = paired.load_protocol()
    record = _attach_v1_mutation_evidence(
        _paid_record(paired.build_schedule(protocol)[0]), tmp_path
    )
    record.pop(remove)

    assert reason in paired.arm_stop_reasons(record, protocol)


def test_arm_gate_recomputes_user_mutation_scope_and_docstring(
    tmp_path: Path,
) -> None:
    protocol = paired.load_protocol()
    item = paired.build_schedule(protocol)[0]
    record = _attach_v1_mutation_evidence(_paid_record(item), tmp_path)
    readme = str((Path(record["project_root"]) / "README.md").resolve())
    record["user_owned_mutations"]["modified_paths"].append(readme)
    record["user_owned_mutations"]["all_changed_paths"].append(readme)

    assert "v1_mutation_scope_invalid" in paired.arm_stop_reasons(record, protocol)

    record = _attach_v1_mutation_evidence(
        _paid_record(item), tmp_path / "second"
    )
    Path(record["calculator_path"]).write_text(
        "def divide(a, b): return a / b\n", encoding="utf-8"
    )

    assert "v1_docstring_quality_failed" in paired.arm_stop_reasons(
        record, protocol
    )
