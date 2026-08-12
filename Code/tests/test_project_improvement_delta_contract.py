from __future__ import annotations

from types import SimpleNamespace

import pytest

from autonomous_iteration.tool.project_improvement_tool import (
    _head_tail_preview,
    project_improvement_tool_executor,
)
from memory.session_dialog import session_turn_ledger_hash
from metadata import ConversationIdentity, SessionIngressState, SessionTurn, ToolInputMetadata


class _DeltaLLM:
    def __init__(self, *, parsed_json=None, content: str = "") -> None:
        self.settings = SimpleNamespace(
            provider="openai_compatible",
            model="test-model",
            context_max_prompt_tokens=4096,
            context_reserved_prompt_tokens=128,
        )
        self.parsed_json = parsed_json
        self.content = content
        self.requests = []

    def complete(self, request, **kwargs):
        self.requests.append((request, kwargs))
        return SimpleNamespace(parsed_json=self.parsed_json, content=self.content)


def _execute(tmp_path, llm: _DeltaLLM | None, **overrides):
    source = tmp_path / "calculator.py"
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    params = {
        "project_path": str(tmp_path),
        "goal": "Improve the calculator without changing its public API.",
        "written_files": [str(source)],
        "run_command": "python -m pytest -q",
        "validation_result": {
            "validation_passed": True,
            "summary": "Tests pass.",
            "recommended_actions": ["Add one bounded error-handling improvement."],
        },
        "prompt_context": {
            "product_judgment": {"preferred_surface": "terminal"},
            "private_full_state": "MUST_NOT_ESCAPE_THE_MODEL_BOUNDARY",
        },
    }
    if llm is not None:
        params["_llm_client"] = llm
    params.update(overrides)
    return project_improvement_tool_executor(
        ToolInputMetadata.from_mapping("project_improvement_tool", params)
    )


def test_project_improvement_accepts_only_a_bounded_analysis_delta_and_maps_legacy_fields(tmp_path) -> None:
    llm = _DeltaLLM(
        parsed_json={
            "changed_signals": ["The divide path has no explicit zero-divisor behavior."],
            "proposed_actions": ["Add a zero-divisor guard in calculator.py."],
            "next_decision_or_goal": "Implement and verify the zero-divisor guard.",
            "must_satisfy": ["Preserve the public add API.", "Run python -m pytest -q."],
            "blocking_risks": ["Do not modify test files."],
            "evidence_ids": ["project_improvement:validation", "unknown:calculator.py"],
            "stack_preset_patch": {"delivery_surface": "terminal"},
        }
    )

    result = _execute(tmp_path, llm).result

    # The model-facing delta maps into the existing public analysis vocabulary;
    # callers do not need two authoritative copies of the same facts.
    assert result.improvement_opportunities == [
        "The divide path has no explicit zero-divisor behavior."
    ]
    assert result.recommended_actions == ["Add a zero-divisor guard in calculator.py."]
    assert result.next_iteration_goal == "Implement and verify the zero-divisor guard."
    assert result.must_implement_next == ["Preserve the public add API.", "Run python -m pytest -q."]
    assert result.blocking_risks == ["Do not modify test files."]
    assert result.evidence_ids == ["project_improvement:validation"]
    assert result.stack_preset_update == {"delivery_surface": "terminal"}
    assert result.annotations["source"] == "llm"

    request, _ = llm.requests[0]
    rendered = "\n".join(message.content for message in request.messages)
    assert "changed_signals" in rendered
    assert "evidence_ids" in rendered
    assert "prompt_context" not in result.annotations
    assert "MUST_NOT_ESCAPE_THE_MODEL_BOUNDARY" not in str(result.to_json_dict())


def test_project_improvement_request_includes_bounded_head_and_tail_project_evidence(tmp_path) -> None:
    llm = _DeltaLLM(
        parsed_json={
            "changed_signals": ["A remaining gap exists."],
            "proposed_actions": ["Implement one remaining gap."],
            "next_decision_or_goal": "Implement the remaining gap.",
            "must_satisfy": ["Preserve existing behavior."],
            "blocking_risks": [],
            "evidence_ids": [],
            "stack_preset_patch": {},
        }
    )
    source = tmp_path / "game.py"
    source.write_text(
        "HEAD_SENTINEL\n" + ("x = 1\n" * 500) + "TAIL_SCORE_AND_RESTART_SENTINEL\n",
        encoding="utf-8",
    )

    _execute(tmp_path, llm, written_files=[str(source)])

    request, _ = llm.requests[0]
    rendered = "\n".join(message.content for message in request.messages)
    assert "HEAD_SENTINEL" in rendered
    assert "TAIL_SCORE_AND_RESTART_SENTINEL" in rendered
    assert "Do not propose behavior already present" in rendered


def test_head_tail_preview_remains_bounded_when_limit_is_smaller_than_marker() -> None:
    preview = _head_tail_preview("0123456789" * 20, 8)

    assert len(preview) <= 8


def test_project_improvement_request_consumes_ingress_dialog_and_typed_constraints(tmp_path) -> None:
    llm = _DeltaLLM(
        parsed_json={
            "changed_signals": ["The workflow is undocumented."],
            "proposed_actions": ["Document the verified command."],
            "next_decision_or_goal": "Document and verify the workflow.",
            "must_satisfy": ["Preserve the public API."],
            "blocking_risks": [],
            "evidence_ids": [],
            "stack_preset_patch": {},
        }
    )
    identity = ConversationIdentity(
        conversation_id="conversation-1",
        run_id="run-1",
        turn_index=2,
        project_root=str(tmp_path),
    )
    ingress = SessionIngressState(
        identity=identity,
        turns=[
            SessionTurn(
                identity=identity.model_copy(update={"turn_index": 1}),
                message_id="dialog-user-1",
                role="user",
                content="Only calculator.py may be modified.",
            ),
            SessionTurn(
                identity=identity,
                message_id="dialog-assistant-2",
                role="assistant",
                content="I will keep the validation command unchanged.",
            ),
        ],
    )

    _execute(
        tmp_path,
        llm,
        _session_ingress_state=ingress,
        _session_constraints=ingress.session_constraints,
        session_turn_source_hash=session_turn_ledger_hash(ingress),
    )

    request, _ = llm.requests[0]
    rendered = "\n".join(message.content for message in request.messages)
    assert "Only calculator.py may be modified." in rendered
    assert "I will keep the validation command unchanged." in rendered
    assert "session_ingress_state" not in rendered


@pytest.mark.parametrize(
    ("parsed_json", "content"),
    [
        (None, "{not valid json"),
        (
            {
                "changed_signals": ["OVERSIZED_MODEL_OUTPUT " * 2_000],
                "proposed_actions": [f"action-{index}" for index in range(100)],
                "next_decision_or_goal": "goal",
                "must_satisfy": [],
                "blocking_risks": [],
                "evidence_ids": [],
                "stack_preset_patch": {},
            },
            "",
        ),
        (
            {
                "changed_signals": ["A bounded signal."],
                "proposed_actions": ["A bounded action."],
                "next_decision_or_goal": "A bounded next goal.",
                "must_satisfy": [],
                "blocking_risks": [],
                "evidence_ids": ["validation:1"],
                "stack_preset_patch": {},
                "prompt_context": {"secret": "FORBIDDEN_OUTPUT_STATE"},
                "diagnosis": {"full": "FORBIDDEN_OUTPUT_STATE"},
                "full_state": {"history": "FORBIDDEN_OUTPUT_STATE"},
            },
            "",
        ),
    ],
    ids=["malformed-json", "oversized-delta", "forbidden-full-state"],
)
def test_project_improvement_invalid_delta_fails_safe_without_broadening_fallback(
    tmp_path,
    parsed_json,
    content,
) -> None:
    llm = _DeltaLLM(parsed_json=parsed_json, content=content)

    result = _execute(tmp_path, llm).result
    serialized = str(result.to_json_dict())

    assert len(llm.requests) == 1
    assert result.annotations["source"] == "fallback"
    assert result.annotations["fallback_reason"]
    assert "OVERSIZED_MODEL_OUTPUT" not in serialized
    assert "FORBIDDEN_OUTPUT_STATE" not in serialized
    assert "MUST_NOT_ESCAPE_THE_MODEL_BOUNDARY" not in serialized
    assert "prompt_context" not in result.annotations
    assert result.product_judgment == {}
    assert result.stack_preset == {}
    assert result.ui_iteration_contract == {}


def test_project_improvement_deterministic_fallback_is_bounded_by_the_same_contract(tmp_path) -> None:
    huge = "FALLBACK_SENTINEL " * 2_000

    result = _execute(
        tmp_path,
        None,
        validation_result={
            "validation_errors": [huge for _ in range(50)],
            "warnings": [huge for _ in range(50)],
            "recommended_actions": [huge for _ in range(50)],
            "next_iteration_goal": huge,
        },
    ).result

    bounded_lists = (
        result.improvement_opportunities,
        result.recommended_actions,
        result.must_implement_next,
        result.blocking_risks,
    )
    assert all(len(items) <= 8 for items in bounded_lists)
    assert all(len(item) <= 1_200 for items in bounded_lists for item in items)
    assert len(result.next_iteration_goal) <= 1_200
    assert len(str(result.to_json_dict())) < 16_000
