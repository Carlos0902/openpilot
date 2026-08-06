from __future__ import annotations

from pathlib import Path

from core.config import LLMSettings
from core.reasoning import select_reasoning_capability_profile
from memory.context_builder import MemoryContextBuilder
from memory.memory_store import MemoryStore
from memory.rolling_compaction import (
    RollingSummaryAttemptEvidence,
    RollingSummaryRequest,
    RollingSummaryAdapter,
)
from memory.session_constraints import (
    activate_constraint_proposal,
    confirm_constraint_proposal,
    extract_constraint_proposals,
)
from memory.short_memory import ShortMemory
from metadata import DurableArtifactReference, SessionConstraintState


def _active_constraint() -> SessionConstraintState:
    proposal = extract_constraint_proposals(
        [
            {
                "message_id": "user-1",
                "turn_index": 1,
                "role": "user",
                "content": "Only calculator.py may be modified.",
            }
        ],
        session_id="session-1",
    )[0]
    return activate_constraint_proposal(
        SessionConstraintState(session_id="session-1"),
        confirm_constraint_proposal(proposal),
        confirmation_turn=2,
    )


def _persist(records: list[dict]):
    def sink(record: dict) -> DurableArtifactReference:
        records.append(record)
        return DurableArtifactReference(
            artifact_id=f"stage5-{len(records)}",
            kind="context_compaction",
            integrity_checksum="sha256:" + str(len(records)) * 64,
            bytes=100,
        )

    return sink


def _seed(path: Path) -> ShortMemory:
    short = ShortMemory(repo_path=path)
    for index in range(8):
        short.add_message("assistant", f"dialog-{index}-" + (str(index) * 120))
    return short


def test_stage5_offline_arms_keep_required_constraints_and_isolate_reasoning(tmp_path) -> None:
    state = _active_constraint()
    calls: list[str] = []

    def request_factory(candidates, limit):
        calls.append("factory")
        deterministic = MemoryContextBuilder._dialog_compaction_record(list(candidates))
        return RollingSummaryRequest(
            source_candidate_ids=tuple(deterministic.source_candidate_ids),
            source_fingerprint=deterministic.source_fingerprint,
            provider_payload={"unknown": "field"},
            attempt=RollingSummaryAttemptEvidence(
                usage={"completion_tokens": 1},
                usage_observed=False,
                finish_reason="stop",
            ),
            max_summary_tokens=limit,
            original_chars=deterministic.original_chars,
        )

    records_current: list[dict] = []
    current_builder = MemoryContextBuilder(
        short_memory=_seed(tmp_path / "current"),
        memory_store=MemoryStore(tmp_path / "current-memory"),
        max_prompt_chars=1300,
    )
    current_builder.set_checkpoint_handlers(compaction_sink=_persist(records_current))
    current = current_builder.build(
        "stage5 current",
        include_environment=False,
        limit=8,
        system_prompt="Preserve the active constraint.",
        session_constraints=state,
    )

    records_treatment: list[dict] = []
    treatment_builder = MemoryContextBuilder(
        short_memory=_seed(tmp_path / "treatment"),
        memory_store=MemoryStore(tmp_path / "treatment-memory"),
        max_prompt_chars=1300,
        rolling_summary_enabled=True,
        rolling_summary_adapter=RollingSummaryAdapter(count_tokens=lambda text: len(text.split())),
        rolling_summary_request_factory=request_factory,
    )
    treatment_builder.set_checkpoint_handlers(compaction_sink=_persist(records_treatment))
    treatment = treatment_builder.build(
        "stage5 treatment",
        include_environment=False,
        limit=8,
        system_prompt="Preserve the active constraint.",
        session_constraints=state,
    )

    assert records_current[0]["algorithm"] == "deterministic_observation_mask_v1"
    assert records_treatment[0]["algorithm"] == "deterministic_observation_mask_v1"
    assert calls and all(item == "factory" for item in calls)
    assert current["session_constraints"][0]["constraints"][0]["constraint_key"] == "write_scope"
    assert treatment["session_constraints"][0]["constraints"][0]["constraint_key"] == "write_scope"
    assert "calculator.py" in current["prompt_text"]
    assert "calculator.py" in treatment["prompt_text"]

    generic = LLMSettings(
        OPENPILOT_LLM_API_KEY="test-key",
        OPENPILOT_LLM_MODEL="gpt-5.6-terra",
        OPENPILOT_LLM_BASE_URL="https://api.openai.com/v1",
    )
    explicit = LLMSettings(
        OPENPILOT_LLM_API_KEY="test-key",
        OPENPILOT_LLM_MODEL="custom-model",
        OPENPILOT_LLM_BASE_URL="https://proxy.invalid/v1",
        OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE="openai-chat-known",
    )
    assert select_reasoning_capability_profile(generic).profile_id.value == "generic-openai-compatible"
    assert select_reasoning_capability_profile(explicit).profile_id.value == "openai-chat-known"
