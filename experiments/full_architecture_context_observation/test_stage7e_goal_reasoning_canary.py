from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.llm import LLMResponse
from stage7e_goal_reasoning_canary import (
    _run_arm,
    run_goal_reasoning_canary,
    run_interleaved_goal_reasoning_pairs,
)
from stage12_session_compact_canary_admission import ProjectionFeatureFlag
from stage13_session_compact_pipeline_offline import _improvement_report, _ingress_state, _project_state
from autonomous_iteration.project_improvement_context import build_iteration_goal_candidates
from metadata import ReasoningDecisionComplexity


class _FakeProvider:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            provider="fake",
            model="fake-model",
            base_url="https://fake.invalid",
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
            tool_event_reasoning_mode="disabled",
        )
        self.calls = 0

    def complete(self, request, **_kwargs):
        self.calls += 1
        return LLMResponse(
            content='{"goals":[{"title":"Bound behavior","category":"robustness","rationale":"test","acceptance_criteria":["scope"],"priority":"high"}]}',
            parsed_json={"goals": [{"title": "Bound behavior", "category": "robustness", "rationale": "test", "acceptance_criteria": ["scope"], "priority": "high"}]},
            model="fake-model",
            provider="fake",
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            finish_reason="stop",
        )


def test_goal_reasoning_canary_is_dry_run_by_default() -> None:
    result = run_goal_reasoning_canary()
    assert result["status"] == "stopped"
    assert result["provider_calls"] == 0


def test_goal_reasoning_arms_share_completion_reservation_and_change_only_reasoning(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "calculator.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (project_root / "README.md").write_text("Run `python -m pytest -q`.\n", encoding="utf-8")
    state = _ingress_state(str(project_root))
    candidates = build_iteration_goal_candidates(
        project_state=_project_state(str(project_root)),
        improvement_report=_improvement_report(),
        completed_iteration=0,
        session_constraints=state.session_constraints,
        session_ingress_state=state,
    )
    provider = _FakeProvider()
    routine = _run_arm(
        client=provider,
        candidates=candidates,
        source_snapshot_hash="sha256:" + "a" * 64,
        arm=ReasoningDecisionComplexity.ROUTINE,
    )
    standard = _run_arm(
        client=provider,
        candidates=candidates,
        source_snapshot_hash="sha256:" + "a" * 64,
        arm=ReasoningDecisionComplexity.STANDARD,
    )
    assert routine.max_tokens == standard.max_tokens
    assert routine.reasoning_mode == "disabled"
    assert standard.reasoning_mode == "provider_default"
    assert routine.goal_valid and standard.goal_valid


def test_interleaved_goal_reasoning_pairs_are_dry_run_by_default() -> None:
    result = run_interleaved_goal_reasoning_pairs()
    assert result["status"] == "stopped"
    assert result["provider_calls"] == 0


def test_interleaved_goal_reasoning_pairs_keep_initial_reservation_fixed(tmp_path: Path) -> None:
    result_path = tmp_path / "interleaved.json"
    provider = _FakeProvider()
    result = run_interleaved_goal_reasoning_pairs(
        execute_provider=True,
        result_path=result_path,
        pair_count=3,
        client=provider,
    )

    assert result["status"] == "passed"
    assert result["provider_calls"] == 6
    assert provider.calls == 6
    assert len(result["pairs"]) == 3
    assert result["attempt_total_tokens"] == 720
    assert result["attempt_reasoning_tokens"] is None
    assert result["attempt_total_tokens"] <= result["attempt_token_cap"]
    assert len({pair["source_snapshot_hash"] for pair in result["pairs"]}) == 1
    assert result["session_turn_source_hash"].startswith("sha256:")
    assert result["session_constraints_hash"].startswith("sha256:")
    for pair in result["pairs"]:
        assert pair["initial_max_tokens"] == pair["routine"]["max_tokens"]
        assert pair["initial_max_tokens"] == pair["standard"]["max_tokens"]
        assert pair["routine_quality"] is True
        assert pair["standard_quality"] is True
        assert pair["routine"]["attempt_error_types"] == [None]
        assert pair["standard"]["attempt_error_types"] == [None]
    assert result_path.exists()
