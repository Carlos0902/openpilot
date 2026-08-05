from __future__ import annotations

from types import SimpleNamespace

from core.llm import LLMResponse
from core.exceptions import InvalidLLMResponseError
from core.token_counting import default_deepseek_tokenizer_path
from stage12_session_compact_canary_admission import ProjectionFeatureFlag, RuntimeKillSwitch
from stage6_full_session_canary import run_full_session_canary
from metadata import ReasoningDecisionComplexity


class _FakeProvider:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            provider="fake-stage6",
            model="deepseek-chat",
            base_url="https://fake.invalid/v1",
            api_key="test",
            tokenizer_path=str(default_deepseek_tokenizer_path()),
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
        )
        self.calls = 0

    def complete(self, request, **_kwargs):
        self.calls += 1
        purpose = str((getattr(request, "trace_info", {}) or {}).get("context_purpose") or "")
        if purpose == "project_improvement":
            payload = {
                "changed_signals": ["A bounded workflow improvement is available."],
                "proposed_actions": ["Inspect calculator.py within the active scope."],
                "next_decision_or_goal": "Inspect one bounded project behavior.",
                "must_satisfy": ["Preserve the existing public API."],
                "blocking_risks": [],
                "evidence_ids": [],
                "stack_preset_patch": {},
            }
        elif purpose == "iteration_goal":
            payload = {
                "goals": [{
                    "title": "Improve one bounded project behavior",
                    "category": "robustness",
                    "rationale": "Stage 6 fixture",
                    "acceptance_criteria": ["The selected target remains in scope."],
                    "priority": "high",
                }]
            }
        else:
            payload = {
                "task": {
                    "description": "Inspect calculator.py within the active session scope.",
                    "target_files": ["calculator.py"],
                    "acceptance_criteria": ["The selected target remains in scope."],
                    "risk_notes": [],
                    "evidence_ids": [],
                }
            }
        return LLMResponse(
            content="{}",
            parsed_json=payload,
            model="deepseek-chat",
            provider="fake-stage6",
            usage={"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240},
            finish_reason="stop",
        )


class _CompactQualityFailureProvider(_FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    def complete(self, request, **kwargs):
        purpose = str((getattr(request, "trace_info", {}) or {}).get("context_purpose") or "")
        if purpose == "iteration_task_design" and self.calls == 2 and not self.failed_once:
            self.failed_once = True
            return LLMResponse(
                content="[]",
                parsed_json=[],
                model="deepseek-chat",
                provider="fake-stage6",
                usage={"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240},
                finish_reason="stop",
            )
        return super().complete(request, **kwargs)


class _AnalyzerFailureProvider(_FakeProvider):
    def complete(self, request, **kwargs):
        purpose = str((getattr(request, "trace_info", {}) or {}).get("context_purpose") or "")
        if purpose == "project_improvement":
            raise InvalidLLMResponseError(
                "malformed analyzer response",
                response_text='{"changed_signals":',
                usage={"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240},
                finish_reason="length",
            )
        return super().complete(request, **kwargs)


def test_full_session_dry_run_uses_one_source_and_no_provider_or_mutation() -> None:
    result = run_full_session_canary()

    assert result["status"] == "passed"
    assert result["claim_boundary"] == "full_session_post_core_context_canary"
    assert result["provider_calls"] == 0
    assert result["offline_model_calls"] == 4
    assert result["network_calls"] == 0
    assert result["project_mutations"] == 0
    assert result["memory_mutations"] == 0
    assert result["checkpoint"]["context_compaction_count"] >= 1
    source_hash = result["source"]["source_snapshot_hash"]
    turn_hash = result["source"]["session_turn_source_hash"]
    requests = list(result["session_result"]["shared_requests"])
    for arm in result["session_result"]["arms"].values():
        requests.extend(arm["requests"])
    assert requests
    assert {item["source_snapshot_hash"] for item in requests} == {source_hash}
    assert {item["session_turn_source_hash"] for item in requests} == {turn_hash}
    assert result["checkpoint"]["turn_hash"] == turn_hash
    assert result["identity_map"]["checkpoint_conversation_id"] == result["identity_map"]["conversation_id"]
    assert all(arm["quality_passed"] for arm in result["session_result"]["arms"].values())
    compact_request = result["session_result"]["arms"]["compact"]["requests"][0]
    current_request = result["session_result"]["arms"]["current"]["requests"][0]
    assert compact_request["selected_compaction_candidate_ids"]
    assert not current_request["selected_compaction_candidate_ids"]
    assert not any(
        item.startswith("session_dialog:")
        for item in compact_request["selected_candidate_ids"]
    )
    assert any(
        item.startswith("session_dialog:")
        for item in current_request["selected_candidate_ids"]
    )


def test_full_session_provider_pair_reconciles_usage_and_preserves_source(tmp_path) -> None:
    client = _FakeProvider()
    result = run_full_session_canary(
        execute_provider=True,
        client=client,
        campaign_state_path=tmp_path / "campaign.json",
    )

    assert result["status"] == "passed"
    assert result["provider_calls"] == 4
    assert client.calls == 4
    assert result["network_calls"] == 4
    assert result["project_mutations"] == 0
    assert result["memory_mutations"] == 0
    assert len(result["ledger"]["observations"]) == 4
    assert all(receipt["usage"]["usage_observed"] for receipt in result["receipts"])
    assert all(receipt["session_turn_source_hash"] == result["source"]["session_turn_source_hash"] for receipt in result["receipts"])
    compact_request = result["session_result"]["arms"]["compact"]["requests"][0]
    current_request = result["session_result"]["arms"]["current"]["requests"][0]
    assert compact_request["selected_compaction_candidate_ids"]
    assert not current_request["selected_compaction_candidate_ids"]
    assert compact_request["rendered_input_tokens"] < current_request["rendered_input_tokens"]


def test_full_session_canary_can_route_goal_reasoning_without_changing_completion_contract() -> None:
    result = run_full_session_canary(
        goal_reasoning_complexity=ReasoningDecisionComplexity.ROUTINE,
    )

    assert result["status"] == "passed"
    goal_requests = [
        item
        for item in result["session_result"]["shared_requests"]
        if item["purpose"] == "iteration_goal"
    ]
    assert len(goal_requests) == 1
    assert goal_requests[0]["reasoning_requested_mode"] == "disabled"


def test_full_session_controls_stop_before_any_work() -> None:
    for feature_flag, kill_switch, reason in (
        (ProjectionFeatureFlag.DISABLED, RuntimeKillSwitch.ARMED, "projection_feature_flag_disabled"),
        (ProjectionFeatureFlag.CANARY_ENABLED, RuntimeKillSwitch.ENGAGED, "runtime_kill_switch_engaged"),
    ):
        result = run_full_session_canary(feature_flag=feature_flag, kill_switch=kill_switch)
        assert result["status"] == "stopped"
        assert result["stop_reasons"] == [reason]
        assert result["provider_calls"] == 0


def test_full_session_uses_one_current_fallback_after_known_compact_quality_failure(tmp_path) -> None:
    client = _CompactQualityFailureProvider()
    result = run_full_session_canary(
        execute_provider=True,
        client=client,
        campaign_state_path=tmp_path / "campaign.json",
    )

    assert result["status"] == "stopped"
    assert result["provider_calls"] == 4
    assert client.calls == 3
    fallback = result["session_result"]["fallback_receipt"]
    assert fallback["reason_code"] == "compact_projection_failed"
    assert fallback["effective_projection"] == "current"
    assert fallback["project_environment_mode"] == "read_only"
    assert fallback["write_scope_hash"].startswith("sha256:")
    assert result["session_result"]["arms"]["compact"]["fallback_used"] is True
    assert result["session_result"]["arms"]["current"]["fallback_for"] == "compact"
    assert len(result["ledger"]["observations"]) == 4


def test_full_session_preserves_known_failed_attempt_usage_before_stopping(tmp_path) -> None:
    result = run_full_session_canary(
        execute_provider=True,
        client=_AnalyzerFailureProvider(),
        campaign_state_path=tmp_path / "campaign.json",
    )

    assert result["status"] == "stopped"
    assert result["provider_calls"] == 2
    assert result["network_calls"] == 2
    assert result["project_mutations"] == 0
    assert result["memory_mutations"] == 0
    assert len(result["ledger"]["observations"]) == 2
    assert all(item["status"] == "failed" for item in result["receipts"])
    assert all(item["usage"]["usage_observed"] for item in result["receipts"])
    assert all(item["finish_reason"] == "length" for item in result["receipts"])
    assert len(result["provider_requests"]) == 2
    assert all(item["status"] == "failed" for item in result["provider_requests"])
    assert all(item["purpose"] == "project_improvement" for item in result["provider_requests"])
