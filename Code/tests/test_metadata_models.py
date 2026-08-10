from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Literal, get_args, get_origin

import pytest
import metadata as metadata_module

from metadata import (
    BugFixAttemptMetadata,
    BugFixResultMetadata,
    CodeArtifactMetadata,
    CommandArtifactMetadata,
    ContextSelectionMetadata,
    ContextAssemblyPolicy,
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextCandidate,
    ContextCandidateDecision,
    ContextCandidateFreshness,
    ContextCandidateKind,
    ContextCandidateRetention,
    ContextCandidateTrust,
    ContextCandidateTruncation,
    ContextCompactionBinding,
    ContextCompactionAttempt,
    ContextCompactionFallbackReason,
    ContextCompactionProviderStatus,
    ContextCompactionRecord,
    ContextCompactionReuseAdmission,
    ContextCompactionReuseAdmissionStatus,
    ContextCompactionReuseRejectionReason,
    ContextCompactionReuseShadowFailure,
    ContextCompactionReuseShadowFailureReason,
    ContextCompactionSelectionOutcome,
    ContextCompactionSelectionStatus,
    ContextQualityEvaluation,
    ContextQualityExpectation,
    ContextQualityIssueCode,
    ContextRequestPurpose,
    DependencyStrategyMetadata,
    DifficultyAssessmentMetadata,
    DurableArtifactReference,
    ExecutionStateMetadata,
    FailureMetadata,
    GitDiffContextMetadata,
    GitRepositoryMetadata,
    GitSnapshotMetadata,
    MetadataBase,
    MetadataKind,
    MetadataSource,
    PathIntentMetadata,
    PathResolutionMetadata,
    ProductIntentMetadata,
    ProblemJudgmentMetadata,
    ProblemSignalMetadata,
    ProjectDiagnosisMetadata,
    ProjectDimensionAssessmentMetadata,
    ProjectDependencyMetadata,
    ProjectImprovementPolicy,
    ProjectImprovementPolicySource,
    ProjectImprovementRequirement,
    ProjectObjectiveMetadata,
    ProjectStackPresetMetadata,
    ImprovementCandidateMetadata,
    ReferenceInsightMetadata,
    RelatedProjectFileMetadata,
    ResolutionPlanMetadata,
    Recoverability,
    RecoveryAutomationPolicy,
    RecoveryBlocker,
    RecoveryFallback,
    RecoveryFallbackAction,
    RecoveryMode,
    RecoveryReasonCode,
    RecoveryStatus,
    ResultStatus,
    RuntimeResumeDecisionMetadata,
    RuntimeFinalizationCursor,
    RuntimeFinalizationStage,
    RuntimePromptContextSnapshot,
    RuntimeBudgetMetadata,
    ProviderBudgetDiagnostic,
    RuntimeStateMetadata,
    ToolEventCompletionOutcome,
    SuccessMetricMetadata,
    TaskResultMetadata,
    TaskRouteMetadata,
    TaskGraphEdgeMetadata,
    TaskGraphNodeMetadata,
    TaskFileResolutionMetadata,
    TaskFileResolutionRequestMetadata,
    ToolCallMetadata,
    ToolContextMetadata,
    ToolErrorMetadata,
    ToolEventMetadata,
    ToolInputMetadata,
    ToolLoopMetadata,
    ToolResultMetadata,
    VerificationCommandSpec,
    VerificationPlanMetadata,
    VerificationStatus,
    ValidationIssueMetadata,
    WarningCheckResultMetadata,
    WarningItemMetadata,
    artifact_to_tool_input,
    json_safe,
    metadata_summary,
)


def test_project_improvement_policy_has_one_typed_completion_authority() -> None:
    automatic = ProjectImprovementPolicy()

    assert automatic.requirement == ProjectImprovementRequirement.OPTIONAL
    assert automatic.source == ProjectImprovementPolicySource.AUTOMATIC_DEFAULT
    assert automatic.target_successes == 2
    assert automatic.max_attempts == 4
    assert automatic.enabled is True
    assert automatic.controls_top_level_success is False
    assert ProjectImprovementPolicy.model_validate_json(automatic.model_dump_json()) == automatic

    required = ProjectImprovementPolicy(
        requirement=ProjectImprovementRequirement.REQUIRED,
        source=ProjectImprovementPolicySource.USER_SELECTED,
        target_successes=1,
        max_attempts=3,
    )
    assert required.controls_top_level_success is True


def test_project_improvement_policy_rejects_ambiguous_disabled_and_enabled_counts() -> None:
    with pytest.raises(ValueError, match="disabled"):
        ProjectImprovementPolicy(
            requirement=ProjectImprovementRequirement.DISABLED,
            target_successes=1,
            max_attempts=1,
        )
    with pytest.raises(ValueError, match="enabled"):
        ProjectImprovementPolicy(
            requirement=ProjectImprovementRequirement.OPTIONAL,
            target_successes=0,
            max_attempts=0,
        )
    with pytest.raises(ValueError, match="max_attempts"):
        ProjectImprovementPolicy(
            requirement=ProjectImprovementRequirement.REQUIRED,
            target_successes=3,
            max_attempts=2,
        )


def test_runtime_finalization_cursor_enforces_monotonic_stage_evidence() -> None:
    report_ref = DurableArtifactReference(
        artifact_id="report-1",
        kind="runtime_report",
        integrity_checksum="sha256:" + "a" * 64,
        bytes=128,
    )
    report_persisted = RuntimeFinalizationCursor(
        finalization_id="final-1",
        stage=RuntimeFinalizationStage.REPORT_PERSISTED,
        outcome="success",
        report_source_hash="sha256:" + "b" * 64,
        report_artifact=report_ref,
    )
    finalized = report_persisted.model_copy(
        update={
            "stage": RuntimeFinalizationStage.RUN_FINALIZED,
            "run_finalized_event_id": "event-1",
        }
    )

    assert RuntimeFinalizationCursor.model_validate_json(finalized.model_dump_json()) == finalized
    with pytest.raises(ValueError, match="report artifact"):
        RuntimeFinalizationCursor(
            finalization_id="final-2",
            stage=RuntimeFinalizationStage.REPORT_PERSISTED,
            outcome="success",
            report_source_hash="sha256:" + "c" * 64,
        )
    with pytest.raises(ValueError, match="run finalized event"):
        RuntimeFinalizationCursor(
            finalization_id="final-3",
            stage=RuntimeFinalizationStage.RUN_FINALIZED,
            outcome="failed",
            report_source_hash="sha256:" + "d" * 64,
            report_artifact=report_ref,
        )


def test_runtime_prompt_context_snapshot_requires_typed_prompt_artifact() -> None:
    selection = ContextSelectionMetadata(
        max_prompt_chars=1000,
        original_prompt_chars=120,
        final_prompt_chars=120,
    )
    snapshot = RuntimePromptContextSnapshot(
        context_id="context-1",
        request_hash="sha256:" + "a" * 64,
        prompt_hash="sha256:" + "b" * 64,
        selection=selection,
        context_artifact=DurableArtifactReference(
            artifact_id="artifact-1",
            kind="prompt_context",
            integrity_checksum="sha256:" + "c" * 64,
            bytes=120,
        ),
    )

    assert RuntimePromptContextSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    with pytest.raises(ValueError, match="prompt_context"):
        RuntimePromptContextSnapshot.model_validate(
            {
                **snapshot.model_dump(mode="python"),
                "context_artifact": {
                    **snapshot.context_artifact.model_dump(mode="python"),
                    "kind": "runtime_report",
                },
            }
        )


@pytest.mark.parametrize(
    "algorithm",
    [
        "deterministic_dialog_extract_v1",
        "deterministic_observation_mask_v1",
    ],
)
def test_context_compaction_contract_binds_only_compaction_artifact(
    algorithm: str,
) -> None:
    record = ContextCompactionRecord(
        compaction_id="compaction-1",
        source_fingerprint="sha256:" + "d" * 64,
        source_candidate_ids=["observation-1", "observation-2"],
        algorithm=algorithm,
        summary="Earlier dialog summary.",
        original_chars=200,
        compacted_chars=23,
    )
    binding = ContextCompactionBinding(
        record=record,
        artifact=DurableArtifactReference(
            artifact_id="compaction-artifact-1",
            kind="context_compaction",
            integrity_checksum="sha256:" + "e" * 64,
            bytes=200,
        ),
        source_binding_hash="sha256:" + "f" * 64,
    )

    restored = ContextCompactionBinding.model_validate_json(binding.model_dump_json())
    assert restored == binding
    assert restored.record.algorithm == algorithm
    assert restored.record.source_candidate_ids == ["observation-1", "observation-2"]
    assert restored.record.source_fingerprint == "sha256:" + "d" * 64
    assert restored.artifact.integrity_checksum == "sha256:" + "e" * 64
    assert restored.source_binding_hash == "sha256:" + "f" * 64
    historical = ContextCompactionBinding.model_validate(
        {
            "record": record.model_dump(mode="python"),
            "artifact": {
                "artifact_id": "legacy-compaction-artifact",
                "kind": "context_compaction",
                "integrity_checksum": "sha256:" + "a" * 64,
                "bytes": 200,
            },
        }
    )
    assert historical.source_binding_hash == ""
    with pytest.raises(ValueError, match="context_compaction"):
        ContextCompactionBinding.model_validate(
            {
                **binding.model_dump(mode="python"),
                "artifact": {
                    **binding.artifact.model_dump(mode="python"),
                    "kind": "prompt_context",
                },
            }
        )
    with pytest.raises(ValueError, match="source_binding_hash"):
        ContextCompactionBinding.model_validate(
            {
                **binding.model_dump(mode="python"),
                "source_binding_hash": "not-a-hash",
            }
        )


def test_context_compaction_attempt_distinguishes_provider_acceptance_and_builder_selection() -> None:
    source_fingerprint = "sha256:" + "f" * 64
    accepted = ContextCompactionAttempt(
        attempt_ordinal=1,
        source_candidate_ids=["dialog-1", "dialog-2"],
        source_fingerprint=source_fingerprint,
        algorithm="llm_rolling_summary_v1",
        provider_status=ContextCompactionProviderStatus.ACCEPTED,
        selection_status=ContextCompactionSelectionStatus.NOT_SELECTED,
        fallback_reason=ContextCompactionFallbackReason.SUMMARY_NOT_FIT_ATOMICALLY,
        summary_token_limit=80,
        summary_token_count=20,
        usage_complete=True,
        finish_reason="stop",
        provider_prompt_tokens=100,
        provider_completion_tokens=20,
        provider_total_tokens=120,
    )
    deterministic = ContextCompactionAttempt(
        attempt_ordinal=2,
        source_candidate_ids=["dialog-1", "dialog-2"],
        source_fingerprint=source_fingerprint,
        algorithm="deterministic_observation_mask_v1",
        provider_status=ContextCompactionProviderStatus.NOT_ATTEMPTED,
        selection_status=ContextCompactionSelectionStatus.SELECTED,
        fallback_reason=ContextCompactionFallbackReason.DETERMINISTIC_FALLBACK,
        artifact_sink_status="persisted",
        artifact_binding=True,
        used_in_prompt=True,
    )
    selection = ContextSelectionMetadata(
        max_prompt_chars=1000,
        original_prompt_chars=500,
        final_prompt_chars=400,
        compaction_attempts=[accepted, deterministic],
    )

    restored = ContextSelectionMetadata.model_validate_json(selection.model_dump_json())
    assert restored.compaction_attempts[0].provider_status == "accepted"
    assert restored.compaction_attempts[0].selection_status == "not_selected"
    assert restored.compaction_attempts[1].fallback_reason == "deterministic_fallback"

    with pytest.raises(ValueError, match="deterministic compaction cannot be provider accepted"):
        ContextCompactionAttempt(
            attempt_ordinal=1,
            source_candidate_ids=["dialog-1"],
            source_fingerprint=source_fingerprint,
            algorithm="deterministic_observation_mask_v1",
            provider_status=ContextCompactionProviderStatus.ACCEPTED,
            selection_status=ContextCompactionSelectionStatus.SELECTED,
        )
    with pytest.raises(ValueError, match="not_selected compaction attempt requires a fallback"):
        ContextCompactionAttempt(
            attempt_ordinal=1,
            source_candidate_ids=["dialog-1"],
            source_fingerprint=source_fingerprint,
            algorithm="llm_rolling_summary_v1",
            provider_status=ContextCompactionProviderStatus.ACCEPTED,
            selection_status=ContextCompactionSelectionStatus.NOT_SELECTED,
            summary_token_limit=80,
            summary_token_count=20,
            usage_complete=True,
            finish_reason="stop",
            provider_prompt_tokens=100,
            provider_completion_tokens=20,
            provider_total_tokens=120,
        )
    with pytest.raises(ValueError, match="usage_complete=true requires all provider token fields"):
        ContextCompactionAttempt(
            attempt_ordinal=1,
            source_candidate_ids=["dialog-1"],
            source_fingerprint=source_fingerprint,
            algorithm="llm_rolling_summary_v1",
            provider_status=ContextCompactionProviderStatus.REJECTED,
            selection_status=ContextCompactionSelectionStatus.NOT_SELECTED,
            fallback_reason=ContextCompactionFallbackReason.REQUEST_OR_PROVIDER_FAILURE,
            summary_token_limit=80,
            usage_complete=True,
            finish_reason="length",
        )
    with pytest.raises(ValueError):
        ContextCompactionAttempt(
            attempt_ordinal=1,
            source_candidate_ids=["dialog-1"],
            source_fingerprint=source_fingerprint,
            algorithm="llm_rolling_summary_v1",
            provider_status=ContextCompactionProviderStatus.REJECTED,
            selection_status=ContextCompactionSelectionStatus.NOT_SELECTED,
            fallback_reason=ContextCompactionFallbackReason.REQUEST_OR_PROVIDER_FAILURE,
            provider_prompt_tokens=True,
        )


def test_context_compaction_attempt_observed_only_is_body_free_and_not_authority() -> None:
    source_fingerprint = "sha256:" + "a" * 64
    attempt = ContextCompactionAttempt(
        attempt_ordinal=1,
        source_candidate_ids=["dialog-1", "dialog-2"],
        source_fingerprint=source_fingerprint,
        source_chars=500,
        algorithm="llm_rolling_summary_v1",
        provider_status=ContextCompactionProviderStatus.ACCEPTED,
        selection_status=ContextCompactionSelectionStatus.NOT_SELECTED,
        summary_token_limit=80,
        summary_token_count=18,
        usage_complete=True,
        finish_reason="stop",
        provider_prompt_tokens=100,
        provider_completion_tokens=18,
        provider_total_tokens=118,
        generated_candidate_id="compaction:generated-1",
        generated_summary_fingerprint="sha256:" + "b" * 64,
        generated_summary_chars=120,
        selection_outcome=ContextCompactionSelectionOutcome.GENERATED_OBSERVED_ONLY,
        artifact_sink_status="observed_only",
    )
    payload = attempt.model_dump(mode="json")
    assert "summary" not in payload
    assert "content" not in payload
    assert payload["selection_outcome"] == "generated_observed_only"
    assert payload["artifact_binding"] is False
    assert payload["used_in_prompt"] is False

    with pytest.raises(ValueError, match="observed-only sink status"):
        ContextCompactionAttempt(
            **{
                **attempt.model_dump(mode="python"),
                "artifact_sink_status": "observed_only",
                "selection_outcome": ContextCompactionSelectionOutcome.DETERMINISTIC_FALLBACK,
                "fallback_reason": ContextCompactionFallbackReason.DETERMINISTIC_FALLBACK,
            }
        )


def test_context_compaction_reuse_admission_is_shadow_only_and_body_free() -> None:
    admission = ContextCompactionReuseAdmission(
        admission_id="reuse-1",
        status=ContextCompactionReuseAdmissionStatus.ADMITTED,
        source_candidate_ids=["dialog-1", "dialog-2"],
        source_fingerprint="sha256:" + "1" * 64,
        source_binding_hash="sha256:" + "2" * 64,
        required_candidate_ids=["system", "task"],
        recent_suffix_ids=["dialog-9", "dialog-10"],
        session_constraints_hash="sha256:" + "3" * 64,
        artifact_id="artifact-1",
        artifact_kind="context_compaction",
        artifact_integrity_checksum="sha256:" + "4" * 64,
        generated_summary_fingerprint="sha256:" + "5" * 64,
    )
    selection = ContextSelectionMetadata(
        max_prompt_chars=1000,
        original_prompt_chars=500,
        final_prompt_chars=400,
        compaction_reuse_admissions=[admission],
    )

    restored = ContextSelectionMetadata.model_validate_json(selection.model_dump_json())
    payload = restored.model_dump(mode="json")
    assert payload["compaction_reuse_admissions"][0]["status"] == "admitted"
    assert payload["compaction_reuse_admissions"][0]["used_in_prompt"] is False
    encoded = json.dumps(payload)
    assert "summary_text" not in encoded
    assert "summary_payload" not in encoded
    assert "content" not in encoded

    rejected = ContextCompactionReuseAdmission(
        admission_id="reuse-2",
        status=ContextCompactionReuseAdmissionStatus.REJECTED,
        rejection_reason=ContextCompactionReuseRejectionReason.SOURCE_FINGERPRINT_MISMATCH,
        source_candidate_ids=["dialog-1"],
        source_fingerprint="sha256:" + "6" * 64,
        source_binding_hash="sha256:" + "7" * 64,
        artifact_id="artifact-1",
        artifact_kind="context_compaction",
        artifact_integrity_checksum="sha256:" + "8" * 64,
    )
    assert rejected.rejection_reason == "source_fingerprint_mismatch"

    with pytest.raises(ValueError, match="rejected reuse admission requires"):
        ContextCompactionReuseAdmission(
            admission_id="reuse-3",
            status=ContextCompactionReuseAdmissionStatus.REJECTED,
            source_candidate_ids=["dialog-1"],
            source_fingerprint="sha256:" + "6" * 64,
            source_binding_hash="sha256:" + "7" * 64,
            artifact_id="artifact-1",
            artifact_kind="context_compaction",
            artifact_integrity_checksum="sha256:" + "8" * 64,
        )
    with pytest.raises(ValueError, match="cannot enter the prompt"):
        ContextCompactionReuseAdmission(
            **{
                **admission.model_dump(mode="python"),
                "used_in_prompt": True,
            }
        )
    with pytest.raises(ValueError, match="reuse admission source candidate IDs must be unique"):
        ContextCompactionReuseAdmission(
            **{
                **admission.model_dump(mode="python"),
                "source_candidate_ids": ["dialog-1", "dialog-1"],
            }
        )
    with pytest.raises(ValueError, match="compaction reuse admission IDs must be unique"):
        ContextSelectionMetadata(
            max_prompt_chars=1000,
            original_prompt_chars=500,
            final_prompt_chars=400,
            compaction_reuse_admissions=[admission, admission.model_copy()],
        )


def test_context_compaction_shadow_failure_is_typed_and_body_free() -> None:
    failure = ContextCompactionReuseShadowFailure(
        failure_id="compaction-reuse-shadow:provider_exception",
        reason=ContextCompactionReuseShadowFailureReason.PROVIDER_EXCEPTION,
        exception_type="RuntimeError",
    )
    selection = ContextSelectionMetadata(
        max_prompt_chars=1000,
        original_prompt_chars=500,
        final_prompt_chars=400,
        compaction_reuse_shadow_failures=[failure],
    )
    restored = ContextSelectionMetadata.model_validate_json(selection.model_dump_json())
    payload = restored.model_dump(mode="json")
    assert payload["compaction_reuse_shadow_failures"][0]["reason"] == "provider_exception"
    assert payload["compaction_reuse_shadow_failures"][0]["exception_type"] == "RuntimeError"
    assert "shadow admission unavailable" not in json.dumps(payload)

    with pytest.raises(ValueError, match="empty shadow provider result"):
        ContextCompactionReuseShadowFailure(
            failure_id="empty",
            reason=ContextCompactionReuseShadowFailureReason.PROVIDER_EMPTY,
            exception_type="RuntimeError",
        )
    with pytest.raises(ValueError, match="strict shadow failures"):
        ContextCompactionReuseShadowFailure(
            failure_id="strict",
            reason=ContextCompactionReuseShadowFailureReason.PROVIDER_EXCEPTION,
            exception_type="RuntimeError",
            strict_sources=True,
        )


def test_admitted_reuse_requires_production_artifact_identity() -> None:
    payload = {
        "admission_id": "invalid-admitted",
        "status": ContextCompactionReuseAdmissionStatus.ADMITTED,
        "source_candidate_ids": ["dialog-1"],
        "source_fingerprint": "sha256:" + "1" * 64,
        "source_binding_hash": "sha256:" + "2" * 64,
        "artifact_id": "artifact-1",
        "artifact_kind": "wrong_kind",
        "artifact_integrity_checksum": "sha256:" + "3" * 64,
    }
    with pytest.raises(ValueError, match="context_compaction artifact kind"):
        ContextCompactionReuseAdmission(**payload)
    with pytest.raises(ValueError, match="summary fingerprint"):
        ContextCompactionReuseAdmission(
            **{**payload, "artifact_kind": "context_compaction"}
        )


def test_context_quality_values_round_trip_without_runtime_metadata_owner() -> None:
    expectation = ContextQualityExpectation(
        expected_selected_candidate_ids=["required"],
        expected_omitted_candidate_ids=["optional"],
    )
    evaluation = ContextQualityEvaluation(
        passed=False,
        issue_codes=[ContextQualityIssueCode.EXPECTED_CANDIDATE_MISSING],
        issue_candidate_ids={"expected_candidate_missing": ["required"]},
        selected_candidate_ids=[],
        omitted_candidate_ids=["optional"],
        character_budget_utilization=0.25,
    )

    assert ContextQualityExpectation.model_validate_json(
        expectation.model_dump_json()
    ) == expectation
    assert ContextQualityEvaluation.model_validate_json(
        evaluation.model_dump_json()
    ) == evaluation


def test_runtime_budget_derives_static_and_dynamic_tool_event_completion_limits() -> None:
    budget = RuntimeBudgetMetadata(
        max_tool_event_completion_tokens=12000,
        tool_event_completion_ceiling=2000,
        tool_event_completion_floor=800,
        tool_event_completion_recovery_step=400,
    )

    assert budget.tool_event_completion_limit(round_index=1, calls_remaining=5) == 2000
    assert budget.tool_event_completion_limit(round_index=2, calls_remaining=4) == 1600
    assert budget.tool_event_completion_limit(round_index=3, calls_remaining=3) == 1200
    budget.grant_tool_event_completion_recovery(400)
    assert budget.tool_event_completion_limit(round_index=1, calls_remaining=5) == 2400
    budget.consume_tool_event_completion(2400)
    assert budget.tool_event_completion_recovery_bonus == 0
    budget.reconcile_tool_event_completion(reserved=2400, actual=0)
    budget.consume_tool_event_completion(11600)
    assert budget.tool_event_completion_tokens_remaining == 400
    assert budget.tool_event_completion_limit(round_index=1, calls_remaining=2) == 200

    with pytest.raises(ValueError, match="completion floor"):
        RuntimeBudgetMetadata(
            tool_event_completion_ceiling=700,
            tool_event_completion_floor=800,
        )


def test_runtime_budget_outcome_feedback_is_opt_in_and_signal_sensitive() -> None:
    baseline = RuntimeBudgetMetadata(
        max_tool_event_completion_tokens=8_000,
        tool_event_completion_ceiling=2_000,
        tool_event_completion_floor=800,
        tool_event_completion_recovery_step=400,
        tool_event_completion_outcome_feedback_enabled=False,
    )
    baseline_limit = baseline.tool_event_completion_limit(round_index=2, calls_remaining=3)
    baseline.observe_tool_event_outcome(ToolEventCompletionOutcome.EMPTY_RESPONSE)
    assert baseline.tool_event_completion_last_outcome == ToolEventCompletionOutcome.EMPTY_RESPONSE.value
    assert baseline.tool_event_completion_limit(round_index=2, calls_remaining=3) == baseline_limit

    adaptive = baseline.model_copy(
        update={"tool_event_completion_outcome_feedback_enabled": True}
    )
    adaptive.observe_tool_event_outcome(ToolEventCompletionOutcome.EMPTY_RESPONSE)
    assert adaptive.tool_event_completion_last_outcome == ToolEventCompletionOutcome.EMPTY_RESPONSE.value
    assert adaptive.tool_event_completion_limit(round_index=2, calls_remaining=3) == baseline_limit + 400
    adaptive.observe_tool_event_outcome(ToolEventCompletionOutcome.TRUNCATED)
    assert adaptive.tool_event_completion_limit(round_index=2, calls_remaining=3) == baseline_limit + 400
    adaptive.observe_tool_event_outcome(ToolEventCompletionOutcome.TOOL_PROGRESS)
    assert adaptive.tool_event_completion_last_outcome == ToolEventCompletionOutcome.TOOL_PROGRESS.value


def test_runtime_budget_outcome_feedback_serializes_and_rejects_unknown_states() -> None:
    budget = RuntimeBudgetMetadata(
        tool_event_completion_outcome_feedback_enabled=True,
        tool_event_completion_last_outcome=ToolEventCompletionOutcome.NO_PROGRESS,
    )
    restored = RuntimeBudgetMetadata.model_validate_json(budget.model_dump_json())
    assert restored.tool_event_completion_last_outcome == ToolEventCompletionOutcome.NO_PROGRESS.value
    with pytest.raises(ValueError):
        RuntimeBudgetMetadata.model_validate(
            {"tool_event_completion_last_outcome": "invented"}
        )


def test_provider_budget_diagnostic_preserves_unknown_usage_and_failed_attempts() -> None:
    unknown = ProviderBudgetDiagnostic(
        round_index=1,
        requested_limit=1200,
        reserved_tokens=1200,
        actual_completion_tokens=None,
        usage_known=False,
        budget_tokens_used_before=0,
        budget_tokens_used_after=1200,
        budget_tokens_remaining_before=3000,
        budget_tokens_remaining_after=1800,
        recovery_bonus_before=0,
        recovery_bonus_after=200,
        finish_reason="length",
        outcome=ToolEventCompletionOutcome.TRUNCATED,
        provider_cap_hit=True,
        outcome_feedback_enabled=True,
    )
    failed = ProviderBudgetDiagnostic(
        round_index=2,
        requested_limit=1200,
        reserved_tokens=1200,
        usage_known=False,
        budget_tokens_used_before=1200,
        budget_tokens_used_after=2400,
        budget_tokens_remaining_before=1800,
        budget_tokens_remaining_after=600,
        recovery_bonus_before=200,
        recovery_bonus_after=200,
        provider_cap_hit=False,
        outcome_feedback_enabled=True,
        provider_attempt_failed=True,
        error_type="TimeoutError",
    )

    assert unknown.model_dump(mode="json")["actual_completion_tokens"] is None
    assert failed.model_dump(mode="json")["provider_attempt_failed"] is True
    with pytest.raises(ValueError, match="usage-known"):
        ProviderBudgetDiagnostic(
            round_index=1,
            requested_limit=100,
            reserved_tokens=100,
            actual_completion_tokens=10,
            usage_known=False,
            budget_tokens_used_before=0,
            budget_tokens_used_after=100,
            budget_tokens_remaining_before=100,
            budget_tokens_remaining_after=0,
            recovery_bonus_before=0,
            recovery_bonus_after=0,
            provider_cap_hit=False,
            outcome_feedback_enabled=False,
        )


def test_context_selection_token_budget_requires_provider_tokenizer_evidence() -> None:
    selection = ContextSelectionMetadata(
        budget_unit="tokens",
        max_prompt_chars=16000,
        max_prompt_tokens=100,
        original_prompt_chars=500,
        final_prompt_chars=200,
        original_prompt_tokens=180,
        final_prompt_tokens=90,
        token_count_method="provider_tokenizer",
        tokenizer_id="deepseek-official-api-tokenizer",
        model="deepseek-v4-flash",
    )

    assert ContextSelectionMetadata.model_validate_json(selection.model_dump_json()) == selection
    with pytest.raises(ValueError, match="complete provider tokenizer evidence"):
        ContextSelectionMetadata(
            budget_unit="tokens",
            max_prompt_chars=16000,
            max_prompt_tokens=100,
            original_prompt_tokens=180,
            final_prompt_tokens=90,
        )


def test_typed_context_assembly_values_round_trip_and_reject_contradictory_status() -> None:
    candidate = ContextCandidate(
        candidate_id="constraint:goal",
        kind=ContextCandidateKind.CONSTRAINT,
        source_id="goal-1",
        content="Preserve the requested output format.",
        retention=ContextCandidateRetention.REQUIRED,
        priority=90,
        source_order=0,
        truncation=ContextCandidateTruncation.FORBIDDEN,
    )
    decision = ContextCandidateDecision(
        candidate_id=candidate.candidate_id,
        kind=candidate.kind,
        source_id=candidate.source_id,
        retention=candidate.retention,
        action="kept",
        reason="within_budget",
        original_chars=len(candidate.content),
        selected_chars=len(candidate.content),
    )
    selection = ContextSelectionMetadata(
        max_prompt_chars=1000,
        original_prompt_chars=len(candidate.content),
        final_prompt_chars=len(candidate.content),
        candidate_decisions=[decision],
    )
    result = ContextAssemblyResult(
        prompt_text=candidate.content,
        selected_candidates=[candidate],
        selection=selection,
    )
    policy = ContextAssemblyPolicy(max_prompt_chars=1000)

    assert ContextAssemblyResult.model_validate_json(result.model_dump_json()) == result
    assert ContextAssemblyPolicy.model_validate_json(policy.model_dump_json()) == policy
    with pytest.raises(ValueError, match="ready assembly cannot omit required candidates"):
        ContextSelectionMetadata(
            max_prompt_chars=100,
            assembly_status=ContextAssemblyStatus.READY,
            omitted_required_candidate_ids=[candidate.candidate_id],
        )
    with pytest.raises(ValueError, match="non-empty content"):
        ContextCandidate(
            candidate_id="empty",
            kind=ContextCandidateKind.TASK,
            content="   ",
        )


def test_context_governance_fields_have_backward_compatible_defaults() -> None:
    candidate = ContextCandidate.model_validate(
        {
            "candidate_id": "legacy-candidate",
            "kind": "memory",
            "content": "legacy content",
        }
    )
    decision = ContextCandidateDecision.model_validate(
        {
            "candidate_id": "legacy-candidate",
            "kind": "memory",
            "retention": "preferred",
            "action": "kept",
            "reason": "within_budget",
            "original_chars": 14,
            "selected_chars": 14,
        }
    )

    assert candidate.trust == ContextCandidateTrust.UNVERIFIED
    assert candidate.freshness == ContextCandidateFreshness.UNKNOWN
    assert decision.trust == ContextCandidateTrust.UNVERIFIED
    assert decision.freshness == ContextCandidateFreshness.UNKNOWN
    with pytest.raises(ValueError, match="requires blocked candidates"):
        ContextSelectionMetadata(
            max_prompt_chars=100,
            assembly_status=ContextAssemblyStatus.GOVERNANCE_BLOCKED,
        )


def test_context_prompt_reserve_arithmetic_is_explicit_and_consistent() -> None:
    policy = ContextAssemblyPolicy(
        max_prompt_chars=1000,
        max_prompt_tokens=120,
        reserved_prompt_tokens=20,
    )
    selection = ContextSelectionMetadata(
        budget_unit="tokens",
        max_prompt_chars=1000,
        requested_prompt_tokens=120,
        reserved_prompt_tokens=20,
        max_prompt_tokens=100,
        original_prompt_tokens=140,
        final_prompt_tokens=90,
        remaining_prompt_tokens=10,
        token_count_method="provider_tokenizer",
        tokenizer_id="exact-test-tokenizer",
        model="test-model",
    )

    assert ContextAssemblyPolicy.model_validate_json(policy.model_dump_json()) == policy
    assert ContextSelectionMetadata.model_validate_json(selection.model_dump_json()) == selection
    with pytest.raises(ValueError, match="smaller than max_prompt_tokens"):
        ContextAssemblyPolicy(
            max_prompt_chars=1000,
            max_prompt_tokens=20,
            reserved_prompt_tokens=20,
        )
    with pytest.raises(ValueError, match="prompt token budget arithmetic"):
        ContextSelectionMetadata.model_validate(
            {
                **selection.model_dump(mode="python"),
                "remaining_prompt_tokens": 11,
            }
        )


def test_context_request_purpose_is_typed_and_round_trips_with_selection() -> None:
    policy = ContextAssemblyPolicy(
        purpose=ContextRequestPurpose.SEMANTIC_GOAL,
        max_prompt_chars=1000,
    )
    selection = ContextSelectionMetadata(
        request_purpose=ContextRequestPurpose.SEMANTIC_GOAL,
        max_prompt_chars=1000,
    )

    assert ContextAssemblyPolicy.model_validate_json(policy.model_dump_json()) == policy
    assert ContextSelectionMetadata.model_validate_json(selection.model_dump_json()) == selection


def test_verification_plan_tracks_a_strict_completed_command_prefix() -> None:
    plan = VerificationPlanMetadata(
        reason="Ordered recovery validation",
        commands=["pytest -q", "python -m compileall -q src"],
        command_specs=[
            VerificationCommandSpec(command="pytest -q", cwd="/tmp/project", timeout=30),
            VerificationCommandSpec(command="python -m compileall -q src", cwd="/tmp/project", timeout=30),
        ],
        next_command_index=1,
        completed_commands=["pytest -q"],
    )

    restored = VerificationPlanMetadata.model_validate_json(plan.model_dump_json())

    assert restored.next_command_index == 1
    assert restored.completed_commands == ["pytest -q"]
    assert [spec.command for spec in restored.command_specs] == plan.commands


@pytest.mark.parametrize(
    "updates",
    [
        {"next_command_index": 2, "completed_commands": ["pytest -q"]},
        {"next_command_index": 1, "completed_commands": ["python -m compileall -q src"]},
        {"next_command_index": 3, "completed_commands": ["pytest -q", "python -m compileall -q src"]},
    ],
)
def test_verification_plan_rejects_non_contiguous_or_out_of_range_progress(updates) -> None:
    with pytest.raises(ValueError):
        VerificationPlanMetadata(
            reason="Invalid recovery progress",
            commands=["pytest -q", "python -m compileall -q src"],
            **updates,
        )


def _exported_metadata_models() -> list[type[MetadataBase]]:
    return [
        value
        for name in metadata_module.__all__
        if inspect.isclass(value := getattr(metadata_module, name))
        and issubclass(value, MetadataBase)
        and value is not MetadataBase
    ]


def test_exported_metadata_models_preserve_protocol_envelope() -> None:
    models = _exported_metadata_models()
    kinds: dict[MetadataKind, str] = {}

    for model in models:
        source_field = model.model_fields["source"]
        assert source_field.annotation is MetadataSource, model.__name__

        kind_field = model.model_fields["kind"]
        assert get_origin(kind_field.annotation) is Literal, model.__name__
        literal_kinds = get_args(kind_field.annotation)
        assert len(literal_kinds) == 1, model.__name__
        assert literal_kinds[0] == kind_field.default, model.__name__
        assert kind_field.default not in kinds, (
            f"{model.__name__} and {kinds[kind_field.default]} share {kind_field.default}"
        )
        kinds[kind_field.default] = model.__name__

    assert set(kinds) == set(MetadataKind)


def test_metadata_catalog_lists_every_exported_contract() -> None:
    catalog_path = Path(__file__).parents[2] / "docs" / "metadata" / "CONTRACT_CATALOG.md"
    catalog = catalog_path.read_text(encoding="utf-8")

    missing = [model.__name__ for model in _exported_metadata_models() if f"`{model.__name__}`" not in catalog]

    assert missing == []


@pytest.mark.parametrize(
    ("metadata_type", "semantic_field"),
    [
        (PathIntentMetadata, "path_source"),
        (PathResolutionMetadata, "path_source"),
        (ProblemSignalMetadata, "signal_source"),
    ],
)
def test_legacy_semantic_source_is_migrated_without_breaking_envelope(
    metadata_type: type[MetadataBase],
    semantic_field: str,
) -> None:
    value = metadata_type.model_validate({"source": "legacy_source"})

    assert value.source == MetadataSource()
    assert getattr(value, semantic_field) == "legacy_source"
    assert value.to_json_dict()["source"] == {
        "source_type": "system",
        "source_name": "openpilot",
    }


def test_metadata_base_fields_and_json_serialization() -> None:
    artifact = CodeArtifactMetadata(code="print('ok')", language="python")

    payload = artifact.to_json_dict()

    assert payload["kind"] == MetadataKind.CODE_ARTIFACT
    assert payload["schema_version"] == "1.0"
    assert payload["code"] == "print('ok')"
    assert payload["content"] == "print('ok')"


def test_project_stack_preset_metadata_serializes_frontend_backend_decision() -> None:
    preset = ProjectStackPresetMetadata(
        project_path="/tmp/assistant",
        preset_file="/tmp/assistant/.openpilot/project_stack.json",
        delivery_surface="browser",
        architecture="frontend_backend_split",
        frontend_language="html_css_javascript",
        backend_language="python",
        ui_strategy="browser_application",
        ui_review_required=True,
    )

    payload = preset.to_json_dict()

    assert payload["kind"] == MetadataKind.PROJECT_STACK_PRESET
    assert payload["architecture"] == "frontend_backend_split"
    assert payload["ui_review_required"] is True


def test_problem_resolution_and_task_graph_metadata_serialize() -> None:
    signal = ProblemSignalMetadata(
        signal_source="tool_planning",
        category="planning_gap",
        message="empty plan",
        evidence=["decision_needs_count:0"],
        task_id="task-1",
        tool_name="tool_planning_executor",
        target_files=["app.py"],
        raw_payload={"decision_needs": []},
    )
    judgment = ProblemJudgmentMetadata(
        is_problem=True,
        severity="blocking",
        requires_fix=True,
        user_visible=True,
        recommended_repair_kind="recover_tool_plan",
        confidence=0.9,
        reason="No routable tool plan.",
    )
    difficulty = DifficultyAssessmentMetadata(
        level="simple",
        needs_decomposition=False,
        blocking_factors=["target file is known"],
        recommended_task_count=1,
    )
    resolution = ResolutionPlanMetadata(
        strategy="direct_retry",
        target_tasks=["task-1"],
        max_attempts=2,
        acceptance_check="A routable decision_need is produced.",
    )
    node = TaskGraphNodeMetadata(
        task_id="task-1",
        description="Repair planner",
        task_kind="repair",
        difficulty="simple",
        read_files=["planner.py"],
        support_context_files=["planner_helpers.py"],
        write_files=["planner.py"],
        expected_outputs=["planner retries empty plan"],
    )
    edge = TaskGraphEdgeMetadata(from_task="task-1", to_task="task-2", edge_type="validates")
    state = ExecutionStateMetadata(
        completed_tasks=["task-1"],
        failed_tasks=[],
        blocked_tasks=[],
        changed_files=["planner.py"],
        validation_result={"all_completed": True},
        execution_batches=[["task-1"], ["task-2"]],
    )

    payload = {
        "signal": signal.to_json_dict(),
        "judgment": judgment.to_json_dict(),
        "difficulty": difficulty.to_json_dict(),
        "resolution": resolution.to_json_dict(),
        "node": node.to_json_dict(),
        "edge": edge.to_json_dict(),
        "state": state.to_json_dict(),
    }

    json.dumps(payload)
    assert payload["signal"]["kind"] == MetadataKind.PROBLEM_SIGNAL
    assert payload["judgment"]["kind"] == MetadataKind.PROBLEM_JUDGMENT
    assert payload["difficulty"]["kind"] == MetadataKind.DIFFICULTY_ASSESSMENT
    assert payload["resolution"]["kind"] == MetadataKind.RESOLUTION_PLAN
    assert payload["node"]["kind"] == MetadataKind.TASK_GRAPH_NODE
    assert payload["node"]["support_context_files"] == ["planner_helpers.py"]
    assert TaskGraphNodeMetadata.model_validate(payload["node"]).support_context_files == [
        "planner_helpers.py"
    ]
    assert payload["edge"]["edge_type"] == "validates"
    assert payload["state"]["execution_batches"] == [["task-1"], ["task-2"]]


def test_json_safe_summarizes_callables_and_drops_internal_handles() -> None:
    class CallbackOwner:
        def approve(self) -> bool:
            return True

    owner = CallbackOwner()

    payload = json_safe(
        {
            "callback": owner.approve,
            "_internal_callback": owner.approve,
            "nested": [owner.approve],
        }
    )

    assert payload == {
        "callback": "<callable:approve>",
        "nested": ["<callable:approve>"],
    }


def test_metadata_summary_summarizes_callables() -> None:
    class CallbackOwner:
        def approve(self) -> bool:
            return True

    owner = CallbackOwner()

    assert metadata_summary({"callback": owner.approve}) == {"callback": "<callable:approve>"}


def test_runtime_state_json_export_handles_method_values() -> None:
    class CallbackOwner:
        def approve(self) -> bool:
            return True

    owner = CallbackOwner()
    input_metadata = ToolInputMetadata.from_mapping(
        "command_executor",
        {
            "command": "pytest",
            "_command_approval_callback": owner.approve,
        },
    )
    state = RuntimeStateMetadata(goal="Serialize runtime callbacks")
    state.annotations["callback"] = owner.approve
    state.record_tool_event(
        {
            "event_type": "tool_run",
            "input": input_metadata.to_params(),
            "callback": owner.approve,
        }
    )

    payload = state.to_json_dict()

    json.dumps(payload)
    assert payload["annotations"]["callback"] == "<callable:approve>"
    assert payload["tool_history"][0]["callback"] == "<callable:approve>"
    assert "_command_approval_callback" not in payload["tool_history"][0]["input"]
    assert "runtime_handles" not in input_metadata.to_json_dict()
    assert callable(input_metadata.runtime_handles["_command_approval_callback"])


def test_runtime_recovery_state_and_decision_round_trip_typed_control_fields() -> None:
    state = RuntimeStateMetadata(
        goal="Resume safely",
        recovery_status=RecoveryStatus.RESUME_READY,
        recovery_reason_code=RecoveryReasonCode.CHECKPOINT_VALID,
        active_resume_attempt_id="resume-1",
    )
    decision = RuntimeResumeDecisionMetadata(
        checkpoint_id="checkpoint-1",
        run_id="run-1",
        root_task_id="task-1",
        session_id="session-1",
        resume_attempt_id="resume-1",
        decision="exact_resume",
        recoverability=Recoverability.RECOVERABLE_NOW,
        recovery_mode=RecoveryMode.EXACT_RESUME,
        automation_policy=RecoveryAutomationPolicy.AUTOMATIC_ALLOWED,
        reason_code=RecoveryReasonCode.CHECKPOINT_VALID,
        safe_boundary="task_normalized",
        reason="human explanation may change",
        next_action="continue runtime session",
        fallback=RecoveryFallback(
            action=RecoveryFallbackAction.NONE,
            reason_code=RecoveryReasonCode.CHECKPOINT_VALID,
        ),
    )

    restored_state = RuntimeStateMetadata.model_validate(state.to_json_dict())
    restored_decision = RuntimeResumeDecisionMetadata.model_validate(decision.to_json_dict())

    assert restored_state.recovery_status == RecoveryStatus.RESUME_READY
    assert restored_decision.recoverability == Recoverability.RECOVERABLE_NOW
    assert restored_decision.recovery_mode == RecoveryMode.EXACT_RESUME
    assert restored_decision.reason_code == RecoveryReasonCode.CHECKPOINT_VALID


def test_runtime_resume_decision_rejects_contradictory_control_fields() -> None:
    with pytest.raises(ValueError, match="not_recoverable"):
        RuntimeResumeDecisionMetadata(
            checkpoint_id="checkpoint-1",
            run_id="run-1",
            root_task_id="task-1",
            session_id="session-1",
            resume_attempt_id="resume-1",
            decision="exact_resume",
            recoverability=Recoverability.NOT_RECOVERABLE,
            recovery_mode=RecoveryMode.EXACT_RESUME,
            automation_policy=RecoveryAutomationPolicy.AUTOMATIC_ALLOWED,
            reason_code=RecoveryReasonCode.CHECKPOINT_CORRUPT,
            safe_boundary="controlled_stop",
            reason="corrupt",
            next_action="stop",
            fallback=RecoveryFallback(
                action=RecoveryFallbackAction.TERMINATE_PRESERVING_EVIDENCE,
                reason_code=RecoveryReasonCode.CHECKPOINT_CORRUPT,
                preserve_original_run=True,
            ),
        )

    with pytest.raises(ValueError, match="legacy decision"):
        RuntimeResumeDecisionMetadata(
            checkpoint_id="checkpoint-1",
            run_id="run-1",
            root_task_id="task-1",
            session_id="session-1",
            resume_attempt_id="resume-1",
            decision="exact_resume",
            recoverability=Recoverability.NOT_RECOVERABLE,
            recovery_mode=RecoveryMode.NONE,
            automation_policy=RecoveryAutomationPolicy.FORBIDDEN,
            reason_code=RecoveryReasonCode.CHECKPOINT_CORRUPT,
            safe_boundary="controlled_stop",
            reason="corrupt",
            next_action="stop",
            fallback=RecoveryFallback(
                action=RecoveryFallbackAction.TERMINATE_PRESERVING_EVIDENCE,
                reason_code=RecoveryReasonCode.CHECKPOINT_CORRUPT,
                preserve_original_run=True,
            ),
        )

    with pytest.raises(ValueError, match="automatic_allowed"):
        RuntimeResumeDecisionMetadata(
            checkpoint_id="checkpoint-1",
            run_id="run-1",
            root_task_id="task-1",
            session_id="session-1",
            resume_attempt_id="resume-1",
            decision="blocked",
            recoverability=Recoverability.RECOVERABLE_AFTER_ACTION,
            recovery_mode=RecoveryMode.USER_ASSISTED_RESUME,
            automation_policy=RecoveryAutomationPolicy.AUTOMATIC_ALLOWED,
            reason_code=RecoveryReasonCode.PERMISSION_REQUIRED,
            safe_boundary="controlled_stop",
            reason="approval needed",
            next_action="ask user",
            blockers=[
                RecoveryBlocker(
                    reason_code=RecoveryReasonCode.PERMISSION_REQUIRED,
                    resolvable=True,
                    requires_user_action=True,
                )
            ],
            fallback=RecoveryFallback(
                action=RecoveryFallbackAction.REQUEST_USER_INPUT,
                reason_code=RecoveryReasonCode.PERMISSION_REQUIRED,
                requires_user_authorization=True,
            ),
        )


def test_legacy_runtime_resume_decision_is_conservatively_migrated_without_text_matching() -> None:
    restored = RuntimeResumeDecisionMetadata.model_validate(
        {
            "checkpoint_id": "checkpoint-1",
            "run_id": "run-1",
            "root_task_id": "task-1",
            "session_id": "session-1",
            "resume_attempt_id": "resume-1",
            "decision": "blocked",
            "safe_boundary": "controlled_stop",
            "reason": "arbitrary historical wording",
            "next_action": "arbitrary historical instruction",
        }
    )

    assert restored.recoverability == Recoverability.RECOVERABLE_AFTER_ACTION
    assert restored.recovery_mode == RecoveryMode.USER_ASSISTED_RESUME
    assert restored.automation_policy == RecoveryAutomationPolicy.MANUAL_ONLY
    assert restored.reason_code == RecoveryReasonCode.LEGACY_BLOCKED
    assert restored.fallback is not None
    assert restored.fallback.action == RecoveryFallbackAction.REQUEST_MANUAL_RECONCILIATION


def test_runtime_state_rejects_unknown_verification_control_status() -> None:
    with pytest.raises(ValueError, match="verification_status"):
        RuntimeStateMetadata(goal="Verify", verification_status="looks_good")


def test_tool_input_from_mapping_normalizes_llm_aliases_and_preserves_extras() -> None:
    metadata = ToolInputMetadata.from_mapping(
        "file_writer",
        {
            "file_path": "assistant.py",
            "content": "print('ok')",
            "create_intermediate": True,
            "unexpected_planner_hint": "keep as context",
        },
    )

    assert metadata.create_dirs is True
    assert "create_intermediate" not in metadata.attributes
    assert metadata.attributes["unexpected_planner_hint"] == "keep as context"
    assert metadata.to_params()["create_dirs"] is True


def test_task_route_metadata_serializes_typed_route_fields() -> None:
    route = TaskRouteMetadata(
        route="agent_generator",
        confidence=0.88,
        reason="Task asks for a reusable agent.",
    )

    payload = route.to_json_dict()

    assert payload["kind"] == MetadataKind.TASK_ROUTE
    assert payload["route"] == "agent_generator"
    assert payload["confidence"] == 0.88


def test_tool_result_requires_result_or_failure_by_status() -> None:
    success = ToolResultMetadata(
        tool_name="code_generator",
        status=ResultStatus.SUCCESS,
        result=CodeArtifactMetadata(code="print('ok')", language="python"),
    )

    assert isinstance(success.result, CodeArtifactMetadata)

    failure = ToolResultMetadata(
        tool_name="code_generator",
        status=ResultStatus.FAIL,
        failure=FailureMetadata(error_type="InvalidInput", error_message="missing task"),
    )
    assert failure.failure.error_type == "InvalidInput"

    with pytest.raises(ValueError):
        ToolResultMetadata(tool_name="code_generator", status=ResultStatus.SUCCESS)


def test_task_result_and_tool_chain_routing_use_metadata_types() -> None:
    tool_result = ToolResultMetadata(
        tool_name="code_generator",
        status=ResultStatus.SUCCESS,
        result=CodeArtifactMetadata(code="print('ok')", language="python"),
    )

    task_result = TaskResultMetadata(
        task_id="task",
        status=ResultStatus.SUCCESS,
        result=tool_result,
    )
    writer_input = artifact_to_tool_input("file_writer", task_result.result)
    executor_input = artifact_to_tool_input("code_executor", task_result.result)

    assert isinstance(writer_input, ToolInputMetadata)
    assert writer_input.content == "print('ok')"
    assert writer_input.code is None
    assert executor_input.code == "print('ok')"
    assert executor_input.language == "python"


def test_tool_event_loop_metadata_serializes() -> None:
    input_metadata = ToolInputMetadata(tool_name="code_generator", task_description="make app", language="python")
    tool_context = ToolContextMetadata(
        session_id="session",
        task_id="task",
        step_id="step_1",
        call_id="call_1",
        project_path="/tmp/project",
        cwd="/tmp/project",
        env={"VIRTUAL_ENV": "/tmp/project/.venv"},
        python_command="/tmp/project/.venv/bin/python",
        git_snapshot={"commit_hash": "abc1234", "created": True},
        safety_notes=["git snapshot available: abc1234"],
    )
    tool_call = ToolCallMetadata(
        session_id="session",
        task_id="task",
        step_id="step_1",
        call_id="call_1",
        tool_name="code_generator",
        input_metadata=input_metadata,
        tool_context=tool_context,
    )
    failure = FailureMetadata(error_type="UnsupportedLanguage", error_message="language=text", recoverable=True)
    tool_error = ToolErrorMetadata(
        session_id="session",
        task_id="task",
        step_id="step_1",
        call_id="call_1",
        tool_name="code_generator",
        error_type=failure.error_type,
        error_message=failure.error_message,
        failure=failure,
        input_metadata=input_metadata,
        tool_context=tool_context,
    )
    event = ToolEventMetadata(
        session_id="session",
        task_id="task",
        step_id="step_1",
        call_id="call_1",
        tool_name="code_generator",
        event_type="error",
        status="error",
        tool_call=tool_call,
        tool_error=tool_error,
        tool_context=tool_context,
        failure=failure,
    )
    loop = ToolLoopMetadata(
        session_id="session",
        task_id="task",
        status="failed",
        success=False,
        events=[event],
        tool_invocations=[tool_call],
        recoverable_errors=[tool_error],
        tool_contexts=[tool_context],
        final_error=failure,
    )

    payload = loop.to_json_dict()

    assert payload["kind"] == MetadataKind.TOOL_LOOP
    assert payload["events"][0]["kind"] == MetadataKind.TOOL_EVENT
    assert payload["tool_contexts"][0]["kind"] == MetadataKind.TOOL_CONTEXT
    assert payload["tool_invocations"][0]["kind"] == MetadataKind.TOOL_CALL
    assert payload["tool_invocations"][0]["tool_context"]["python_command"].endswith("/python")
    assert payload["recoverable_errors"][0]["kind"] == MetadataKind.TOOL_ERROR
    assert payload["events"][0]["call_id"] == "call_1"


def test_tool_loop_metadata_accepts_runtime_invocation_trace_without_context() -> None:
    payload = {
        "kind": MetadataKind.TOOL_LOOP,
        "session_id": "session",
        "task_id": "task",
        "status": "completed",
        "success": True,
        "events": [],
        "tool_invocations": [],
        "recoverable_errors": [],
    }

    loop = ToolLoopMetadata.model_validate(payload)

    assert loop.success is True
    assert loop.tool_contexts == []


def test_bug_fix_metadata_serializes_attempts_and_failure_result() -> None:
    command_result = CommandArtifactMetadata(
        command="python app.py",
        success=False,
        stderr="SyntaxError",
        exit_code=1,
    )
    attempt = BugFixAttemptMetadata(
        iteration=1,
        command_result=command_result,
        error_summary="SyntaxError",
        modified_files=["app.py"],
        rationale="Fix broken syntax",
    )
    result = BugFixResultMetadata(
        command="python app.py",
        target_files=["app.py"],
        fixed=False,
        iterations_used=1,
        attempts=[attempt],
        final_command_result=command_result,
        requires_user_decision=True,
    )

    envelope = ToolResultMetadata(
        tool_name="bug_fix_tool",
        status=ResultStatus.FAIL,
        result=result,
        failure=FailureMetadata(
            error_type="MaxBugFixIterationsReached",
            error_message="still failing",
            retry_recommended=True,
        ),
    )
    payload = envelope.to_json_dict()

    assert payload["result"]["kind"] == MetadataKind.BUG_FIX_RESULT
    assert payload["result"]["attempts"][0]["kind"] == MetadataKind.BUG_FIX_ATTEMPT
    assert payload["result"]["requires_user_decision"] is True


def test_warning_check_metadata_serializes_items() -> None:
    item = WarningItemMetadata(
        warning_text="System fonts cannot be loaded",
        warning_source="pygame.sysfont",
        category="font_rendering",
        severity="fix_required",
        affects_user_experience=True,
        requires_fix=True,
        reason="Text may render as boxes.",
    )
    result = WarningCheckResultMetadata(
        command="python main.py",
        cwd="/tmp/project",
        warnings=[item],
        requires_fix=True,
        reason=item.reason,
        recommended_fix="Use a bundled font or pygame.font.Font(None, size).",
    )

    payload = result.to_json_dict()

    assert payload["kind"] == MetadataKind.WARNING_CHECK_RESULT
    assert payload["warnings"][0]["kind"] == MetadataKind.WARNING_ITEM
    assert payload["requires_fix"] is True


def test_git_metadata_serializes_repository_snapshot_and_diff() -> None:
    repository = GitRepositoryMetadata(
        project_path="/tmp/project",
        initialized=True,
        branch="main",
        head="abc123",
        dirty=False,
        ignored_paths=[".venv/"],
    )
    snapshot = GitSnapshotMetadata(
        project_path="/tmp/project",
        reason="before_write",
        message="openpilot: safety snapshot before write",
        commit_hash="abc123",
        created=True,
        changed_files=["app.py"],
    )
    diff = GitDiffContextMetadata(
        project_path="/tmp/project",
        base_ref="abc123",
        head_ref="def456",
        changed_files=["app.py"],
        diff_stat="app.py | 2 +-",
    )

    assert repository.to_json_dict()["kind"] == MetadataKind.GIT_REPOSITORY
    assert snapshot.to_json_dict()["kind"] == MetadataKind.GIT_SNAPSHOT
    assert diff.to_json_dict()["kind"] == MetadataKind.GIT_DIFF_CONTEXT


def test_product_intent_and_validation_issue_metadata_serialize() -> None:
    intent = ProductIntentMetadata(
        experience_type="interactive_app",
        runtime_mode="standalone_gui",
        delivery_surface="native_window",
        core_capabilities=["visible_feedback"],
        non_regression_constraints=["Preserve native window delivery."],
        disallowed_substitutions=["terminal_ui"],
    )
    issue = ValidationIssueMetadata(
        category="product_intent_drift",
        severity="blocking",
        message="Implementation changed delivery surface.",
        recommended_action="Regenerate while preserving product intent.",
        product_intent=intent,
        preserves_product_intent=False,
    )

    payload = issue.to_json_dict()

    assert payload["kind"] == MetadataKind.VALIDATION_ISSUE
    assert payload["product_intent"]["kind"] == MetadataKind.PRODUCT_INTENT
    assert payload["product_intent"]["disallowed_substitutions"] == ["terminal_ui"]


def test_project_diagnosis_metadata_serializes_ranked_candidates() -> None:
    objective = ProjectObjectiveMetadata(
        goal="Build a CLI formatter",
        project_type="cli_tool",
        target_users=["terminal users"],
        core_value=["Format input reliably."],
    )
    metric = SuccessMetricMetadata(
        metric_id="runtime_ready",
        name="Runnable delivery",
        dimension="reliability",
        target="CLI command runs.",
        required=True,
        satisfied=True,
    )
    assessment = ProjectDimensionAssessmentMetadata(
        dimension="user_experience",
        score=0.4,
        gaps=["Help output is unclear."],
    )
    candidate = ImprovementCandidateMetadata(
        candidate_id="gap_cli_help",
        title="Clarify CLI usage feedback",
        dimension="user_experience",
        acceptance_criteria=["Help output documents required arguments."],
        priority_score=0.8,
        selected=True,
    )
    diagnosis = ProjectDiagnosisMetadata(
        project_path="/tmp/tool",
        objective=objective,
        success_metrics=[metric],
        dimension_assessments=[assessment],
        improvement_candidates=[candidate],
        ranked_candidate_ids=[candidate.candidate_id],
        selected_candidate=candidate,
        reference_insights=[ReferenceInsightMetadata(summary="CLI tools should expose useful help text.", confidence=0.6)],
    )

    payload = diagnosis.to_json_dict()

    assert payload["kind"] == MetadataKind.PROJECT_DIAGNOSIS
    assert payload["objective"]["kind"] == MetadataKind.PROJECT_OBJECTIVE
    assert payload["success_metrics"][0]["kind"] == MetadataKind.SUCCESS_METRIC
    assert payload["selected_candidate"]["candidate_id"] == "gap_cli_help"
    assert payload["reference_insights"][0]["kind"] == MetadataKind.REFERENCE_INSIGHT


def test_dependency_metadata_serializes_with_diagnosis() -> None:
    dependency = ProjectDependencyMetadata(
        package_name="pygame",
        version="2.6.1",
        import_names=["pygame"],
        dependency_sources=["installed", "import_scan"],
        import_usage=["import pygame"],
        role="interactive_window_rendering_input_game_loop",
        confidence=0.91,
    )
    strategy = DependencyStrategyMetadata(
        preserve_packages=["pygame"],
        rationale=["Preserve pygame as existing rendering/input capability."],
        confidence=0.85,
    )
    objective = ProjectObjectiveMetadata(goal="Build a game", project_type="interactive_app")
    diagnosis = ProjectDiagnosisMetadata(
        project_path="/tmp/game",
        objective=objective,
        dependencies=[dependency],
        dependency_strategy=strategy,
    )

    payload = diagnosis.to_json_dict()

    assert payload["dependencies"][0]["kind"] == MetadataKind.PROJECT_DEPENDENCY
    assert payload["dependency_strategy"]["kind"] == MetadataKind.DEPENDENCY_STRATEGY
    assert payload["dependency_strategy"]["preserve_packages"] == ["pygame"]


def test_task_file_resolution_metadata_serializes() -> None:
    request = TaskFileResolutionRequestMetadata(
        project_path="/tmp/project",
        task_description="Update README controls",
        acceptance_criteria=["README documents controls."],
        target_file_hints=["README.md"],
    )
    file = RelatedProjectFileMetadata(
        file_path="/tmp/project/README.md",
        name="README.md",
        suffix=".md",
        role="documentation",
        relevance_score=1.0,
        relation_source="target_hint",
    )
    resolution = TaskFileResolutionMetadata(
        task_description=request.task_description,
        project_path=request.project_path,
        related_files=[file],
        primary_file=file,
        recommended_edit_kind="documentation",
    )

    payload = resolution.to_json_dict()

    assert request.to_json_dict()["kind"] == MetadataKind.TASK_FILE_RESOLUTION_REQUEST
    assert payload["kind"] == MetadataKind.TASK_FILE_RESOLUTION
    assert payload["primary_file"]["kind"] == MetadataKind.RELATED_PROJECT_FILE
    assert payload["recommended_edit_kind"] == "documentation"
