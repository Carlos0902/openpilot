from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mini_swe_active_iteration.core_benefit_screen import (
    CoreBenefitScreenEligibleCandidate,
    CoreBenefitScreenPoolReceipt,
    build_stage_a_manifest,
    write_core_benefit_screen_artifact,
)
from mini_swe_active_iteration.screen_execution_protocol import (
    PairArmOrder,
    ScreenExecutionProtocol,
    evaluator_fingerprint,
    execution_runner_fingerprint,
    validate_screen_execution_protocol_bindings,
)
from mini_swe_active_iteration.provider import SharedProviderSpec


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _manifest():
    candidates = tuple(
        CoreBenefitScreenEligibleCandidate(
            instance_id=f"repo-{index // 3}__issue-{index}",
            repository=f"repo-{index // 3}",
            selection_rank=index + 1,
            nonexecution_receipt_sha256=_digest(f"nonexecution-{index}"),
            first_review_sha256=_digest(f"first-{index}"),
            second_review_sha256=_digest(f"second-{index}"),
            primary_stratum="measurement_disambiguation",
            adjudication_sha256=None,
            mechanism_review_sha256=_digest(f"mechanism-{index}"),
            diagnostic_measurement=True,
            post_action_validation=False,
            recovery_or_safe_stop=False,
        )
        for index in range(12)
    )
    pool = CoreBenefitScreenPoolReceipt(
        schema_version="1.0",
        screen_protocol_sha256=_digest("screen"),
        acquisition_rules_sha256=_digest("rules"),
        candidates=candidates,
        eligible_candidate_count=12,
        provider_execution_authorized=False,
        production_execution_authorized=False,
        task_outcomes_generated=False,
    )
    return build_stage_a_manifest(
        pool=pool,
        selection_seed=20260801,
        maximum_tasks_per_repository=4,
    )


def _protocol(manifest) -> ScreenExecutionProtocol:
    task_ids = tuple(entry.instance_id for entry in manifest.entries)
    provider = SharedProviderSpec(
        provider="openai-compatible",
        model_name="test-model",
        model_class="litellm",
        base_url="https://example.invalid",
        temperature=0.0,
        max_output_tokens=2048,
        cache_policy="disabled",
        request_timeout_seconds=60.0,
        model_kwargs={},
    )
    return ScreenExecutionProtocol(
        schema_version="1.0",
        protocol_id="mini-swe-core-benefit-screen-execution-v1",
        lifecycle="exploratory_screen",
        stage_manifest_sha256=_digest("placeholder"),
        pool_sha256=manifest.pool_sha256,
        task_ids=task_ids,
        arms=("ordinary", "active_iteration"),
        provider=provider,
        provider_fingerprint=provider.fingerprint(),
        common_budget={
            "max_provider_calls": 8,
            "max_total_tokens": 30000,
            "max_tool_calls": 12,
            "max_iterations": 8,
            "max_wall_time_seconds": 300,
        },
        model_retry_attempts=1,
        pair_arm_orders=tuple(
            PairArmOrder(
                task_id=task_id,
                order=("active_first" if index < 6 else "ordinary_first"),
            )
            for index, task_id in enumerate(task_ids)
        ),
        provider_transport_failure_policy="fail_closed_count_in_denominator",
        timeout_policy="fail_closed_count_in_denominator",
        output_directory="core_benefit_screen_v1/execution",
        controller_prompt_sha256=_digest("controller"),
        bash_tool_schema_sha256=_digest("bash"),
        runner_sha256=_digest("runner"),
        evaluator_sha256=_digest("evaluator"),
        task_arm_provider_execution_authorized=False,
        production_execution_authorized=False,
        hypothesis_evidence_eligible=False,
        outcomes_generated=False,
    )


def test_protocol_requires_balanced_orders_and_fail_closed_policy() -> None:
    manifest = _manifest()
    protocol = _protocol(manifest)

    assert protocol.task_arm_provider_execution_authorized is False
    assert len(protocol.pair_arm_orders) == 12

    with pytest.raises(ValueError, match="six active-first"):
        ScreenExecutionProtocol.model_validate(
            {
                **protocol.model_dump(mode="json"),
                "pair_arm_orders": [
                    {"task_id": order.task_id, "order": "active_first"}
                    for order in protocol.pair_arm_orders
                ],
            }
        )


def test_protocol_binds_its_stage_manifest(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest_path = tmp_path / "stage-a.json"
    write_core_benefit_screen_artifact(artifact=manifest, output_path=manifest_path)
    protocol = _protocol(manifest).model_copy(
        update={"stage_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
    )

    assert validate_screen_execution_protocol_bindings(
        protocol=protocol,
        stage_manifest_path=manifest_path,
    ) == ()

    tampered = json.loads(manifest_path.read_text())
    tampered["pool_sha256"] = _digest("different-pool")
    manifest_path.write_text(json.dumps(tampered))
    assert "stage_manifest_sha256_mismatch" in validate_screen_execution_protocol_bindings(
        protocol=protocol,
        stage_manifest_path=manifest_path,
    )


def test_frozen_screen_protocol_binds_current_implementation() -> None:
    package_root = Path(__file__).parents[1]
    protocol_path = (
        package_root
        / "core_benefit_screen_v1"
        / "SCREEN_EXECUTION_PROTOCOL_V1.json"
    )
    manifest_path = package_root / "core_benefit_screen_v1" / "STAGE_A_MANIFEST_V1.json"
    protocol = ScreenExecutionProtocol.model_validate_json(protocol_path.read_text())

    assert validate_screen_execution_protocol_bindings(
        protocol=protocol,
        stage_manifest_path=manifest_path,
    ) == ()
    assert protocol.runner_sha256 == execution_runner_fingerprint(package_root)
    assert protocol.evaluator_sha256 == evaluator_fingerprint(package_root)
    assert protocol.task_arm_provider_execution_authorized is False
    assert protocol.outcomes_generated is False
