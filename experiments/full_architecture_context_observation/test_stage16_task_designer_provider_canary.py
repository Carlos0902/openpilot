from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm import LLMResponse
from core.exceptions import InvalidLLMResponseError
from core.token_counting import default_deepseek_tokenizer_path
from stage12_session_compact_canary_admission import (
    ProjectionFeatureFlag,
    RuntimeKillSwitch,
)
from stage16_task_designer_provider_canary import (
    ProviderCanaryStop,
    run_task_designer_provider_canary,
)


class _FakeProvider:
    def __init__(self, *, usage: dict | None = None) -> None:
        self.settings = SimpleNamespace(
            provider="fake-provider",
            model="deepseek-chat",
            base_url="https://fake.invalid",
            api_key="test",
            tokenizer_path=str(default_deepseek_tokenizer_path()),
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
        )
        self.usage = usage or {
            "prompt_tokens": 1200,
            "completion_tokens": 40,
            "total_tokens": 1240,
        }
        self.calls = 0
        self.kwargs = []

    def complete(self, request, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        return LLMResponse(
            content='{"task":{"description":"Document validation","target_files":["calculator.py"],"acceptance_criteria":["pytest passes"],"risk_notes":[],"evidence_ids":[]}}',
            parsed_json={
                "task": {
                    "description": "Document validation",
                    "target_files": ["calculator.py"],
                    "acceptance_criteria": ["pytest passes"],
                    "risk_notes": [],
                    "evidence_ids": [],
                }
            },
            model="deepseek-chat",
            provider="fake-provider",
            usage=self.usage,
            finish_reason="stop",
        )


class _CompactQualityFailureThenFallback(_FakeProvider):
    def complete(self, request, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        if self.calls == 1:
            return LLMResponse(
                content='["not a task object"]',
                parsed_json=["not a task object"],
                model="deepseek-chat",
                provider="fake-provider",
                usage=self.usage,
                finish_reason="stop",
            )
        return LLMResponse(
            content='{"task":{"description":"Document validation","target_files":["calculator.py"],"acceptance_criteria":["pytest passes"],"risk_notes":[],"evidence_ids":[]}}',
            parsed_json={
                "task": {
                    "description": "Document validation",
                    "target_files": ["calculator.py"],
                    "acceptance_criteria": ["pytest passes"],
                    "risk_notes": [],
                    "evidence_ids": [],
                }
            },
            model="deepseek-chat",
            provider="fake-provider",
            usage=self.usage,
            finish_reason="stop",
        )


class _CompactTruncationThenFallback(_FakeProvider):
    def complete(self, request, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        if self.calls == 1:
            raise InvalidLLMResponseError(
                "truncated task JSON",
                response_text='{"task":{"description":"partial"',
                usage=self.usage,
                finish_reason="length",
            )
        self.calls -= 1
        return _FakeProvider.complete(self, request, **kwargs)


def test_provider_canary_is_dry_run_by_default() -> None:
    result = run_task_designer_provider_canary()

    assert result["status"] == "stopped"
    assert result["provider_execution_admitted"] is False
    assert result["transport_attempted"] is False
    assert result["provider_calls"] == 0


def test_provider_canary_reconciles_two_observed_requests_without_mutation(tmp_path) -> None:
    client = _FakeProvider()

    result = run_task_designer_provider_canary(
        execute_provider=True,
        client=client,
        campaign_state_path=tmp_path / "campaign.json",
    )

    assert result["status"] == "passed"
    assert client.calls == 2
    assert all(item["max_retries"] == 1 for item in client.kwargs)
    assert result["provider_execution_admitted"] is True
    assert result["transport_attempted"] is True
    assert result["provider_calls"] == 2
    assert result["network_calls"] == 2
    assert result["project_mutations"] == 0
    assert result["ledger"]["reservation_count"] == 2
    assert result["ledger"]["observation_count"] == 2
    assert all(item["provider_usage_observed"] for item in result["observations"])
    assert all(item["quality_passed"] for item in result["observations"])
    assert {item["arm"] for item in result["observations"]} == {"compact", "current"}


def test_provider_canary_unknown_usage_stops_before_second_arm(tmp_path) -> None:
    client = _FakeProvider(
        usage={"prompt_tokens": 1200, "completion_tokens": None, "total_tokens": None}
    )

    result = run_task_designer_provider_canary(
        execute_provider=True,
        client=client,
        campaign_state_path=tmp_path / "campaign.json",
    )

    assert result["status"] == "stopped"
    assert client.calls == 1
    assert result["observations"][0]["provider_usage_observed"] is False
    assert "compact:unknown_provider_usage" in result["stop_reasons"]
    assert result["ledger"]["observation_count"] == 0


def test_provider_canary_preserves_observed_usage_when_reservation_overruns(tmp_path) -> None:
    client = _FakeProvider(
        usage={"prompt_tokens": 4000, "completion_tokens": 4000, "total_tokens": 8000}
    )

    result = run_task_designer_provider_canary(
        execute_provider=True,
        client=client,
        campaign_state_path=tmp_path / "campaign.json",
    )

    assert result["status"] == "stopped"
    record = result["observations"][0]
    assert record["provider_usage_observed"] is True
    assert record["provider_total_tokens"] == 8000
    assert "compact:usage_reconcile_failed" in result["stop_reasons"]
    assert result["ledger"]["observation_count"] == 0


def test_provider_canary_uses_current_fallback_after_reconciled_quality_failure(tmp_path) -> None:
    client = _CompactQualityFailureThenFallback()

    result = run_task_designer_provider_canary(
        execute_provider=True,
        client=client,
        campaign_state_path=tmp_path / "campaign.json",
    )

    assert result["status"] == "stopped"
    assert result["fallback_used"] is True
    assert client.calls == 2
    assert result["ledger"]["observation_count"] == 2
    assert result["observations"][0]["quality_passed"] is False
    assert result["observations"][1]["arm"] == "current"
    assert result["observations"][1]["quality_passed"] is True
    assert "compact:task_schema_missing" in result["stop_reasons"]


def test_provider_canary_uses_current_fallback_after_reconciled_truncation(tmp_path) -> None:
    client = _CompactTruncationThenFallback()

    result = run_task_designer_provider_canary(
        execute_provider=True,
        client=client,
        campaign_state_path=tmp_path / "campaign.json",
    )

    assert result["status"] == "stopped"
    assert result["fallback_used"] is True
    assert client.calls == 2
    assert result["ledger"]["observation_count"] == 2
    assert result["observations"][0]["provider_usage_observed"] is True
    assert result["observations"][1]["quality_passed"] is True
    assert "compact:invalid_json_fallback" in result["stop_reasons"]


@pytest.mark.parametrize(
    ("feature_flag", "kill_switch", "reason"),
    [
        (ProjectionFeatureFlag.DISABLED, RuntimeKillSwitch.ARMED, "projection_feature_flag_disabled"),
        (ProjectionFeatureFlag.CANARY_ENABLED, RuntimeKillSwitch.ENGAGED, "runtime_kill_switch_engaged"),
    ],
)
def test_provider_canary_controls_stop_without_calls(
    feature_flag: ProjectionFeatureFlag,
    kill_switch: RuntimeKillSwitch,
    reason: str,
) -> None:
    client = _FakeProvider()
    result = run_task_designer_provider_canary(
        execute_provider=True,
        client=client,
        feature_flag=feature_flag,
        kill_switch=kill_switch,
    )

    assert result["status"] == "stopped"
    assert result["stop_reasons"] == [reason]
    assert client.calls == 0


def test_provider_canary_requires_persistent_campaign_state_before_transport() -> None:
    client = _FakeProvider()
    result = run_task_designer_provider_canary(execute_provider=True, client=client)

    assert result["status"] == "stopped"
    assert result["stop_reasons"] == ["campaign_state_path_required"]
    assert client.calls == 0
