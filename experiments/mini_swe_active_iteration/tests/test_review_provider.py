from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from mini_swe_active_iteration.review_provider import (
    CandidateReviewInput,
    ProviderReviewProtocol,
    build_review_request,
    materialize_recorded_review,
    parse_review_response,
    review_candidate,
)


def test_request_contains_only_frozen_candidate_fields() -> None:
    package_root = Path(__file__).parents[1]
    protocol = ProviderReviewProtocol.load(
        package_root / "CORE_BENEFIT_SCREEN_REVIEW_PROVIDER_PROTOCOL_V1.json"
    )
    request = build_review_request(
        protocol=protocol,
        candidate=CandidateReviewInput(
            instance_id="repo__issue-1",
            problem_statement="Observed public failure.",
            patch="diff --git a/a.py b/a.py\n",
            test_patch="diff --git a/test_a.py b/test_a.py\n",
        ),
    )

    candidate_payload = json.loads(request["messages"][1]["content"])
    assert set(candidate_payload) == {"instance_id", "problem_statement", "patch", "test_patch"}
    for forbidden in (
        "first_review_decision",
        "first_review_rationale",
        "ordinary_trajectory",
        "active_iteration_trajectory",
        "task_arm_outcome",
        "hidden_evaluator_result",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
    ):
        assert forbidden not in candidate_payload


def test_response_parser_fails_closed_on_extra_or_invalid_content() -> None:
    assert parse_review_response(
        '{"primary_stratum":"localization","rationale":"The public issue leaves the file unresolved."}'
    ) == ("localization", "The public issue leaves the file unresolved.")

    for response in (
        '{"primary_stratum":"localization","rationale":"x","extra":true}',
        '{"primary_stratum":"invalid","rationale":"x"}',
        '{"primary_stratum":"localization","rationale":""}',
    ):
        try:
            parse_review_response(response)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe response must fail closed")


def test_protocol_binds_prompt_and_review_identity() -> None:
    package_root = Path(__file__).parents[1]
    protocol_path = package_root / "CORE_BENEFIT_SCREEN_REVIEW_PROVIDER_PROTOCOL_V1.json"
    protocol = ProviderReviewProtocol.load(protocol_path)
    payload = json.loads(protocol_path.read_text())

    assert protocol.prompt_sha256 == hashlib.sha256(protocol.prompt_path.read_bytes()).hexdigest()
    assert protocol.reviewer_identity_sha256 != "e763a4b9f70aa5e210595521a9d465bab206c802ff035d32148a2d3859587a04"
    assert payload["task_arm_provider_execution_authorized"] is False
    assert payload["task_outcomes_generated"] is False


def test_one_mocked_call_writes_only_redacted_public_artifacts(tmp_path: Path) -> None:
    package_root = Path(__file__).parents[1]
    root_protocol = ProviderReviewProtocol.load(
        package_root / "CORE_BENEFIT_SCREEN_REVIEW_PROVIDER_PROTOCOL_V1.json"
    )
    protocol = replace(
        root_protocol,
        private_output_root=tmp_path / "private",
        public_output_root=tmp_path / "public-decisions",
        public_receipt_root=tmp_path / "public-receipts",
    )
    instance_id = "matplotlib__matplotlib-20826"
    calls: list[dict[str, object]] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"primary_stratum":"localization",'
                                '"rationale":"The issue does not identify the target file."}'
                            )
                        }
                    }
                ],
            }

    def post(*_args: object, **kwargs: object) -> _Response:
        calls.append(kwargs)
        return _Response()

    public_decision = review_candidate(
        protocol=protocol,
        candidate=CandidateReviewInput(
            instance_id=instance_id,
            problem_statement="Public issue text.",
            patch="diff --git a/a.py b/a.py\n",
            test_patch="diff --git a/test_a.py b/test_a.py\n",
        ),
        api_key="test-key",
        nonexecution_receipt_path=(
            package_root / "exploratory_candidate_nonexecution_v1" / f"{instance_id}.json"
        ),
        host_preflight_path=package_root / "EXPLORATORY_ACQUISITION_PREFLIGHT_V6.json",
        inventory_path=package_root / "EXPLORATORY_CANDIDATE_INVENTORY_V4.json",
        image_receipt_path=(
            package_root / "exploratory_candidate_image_v1" / f"{instance_id}.json"
        ),
        execution_receipt_path=(
            package_root / "exploratory_candidate_execution_v1" / f"{instance_id}.json"
        ),
        post=post,
    )

    assert len(calls) == 1
    assert public_decision.is_file()
    public_text = (tmp_path / "public-decisions" / public_decision.name).read_text()
    receipt_text = (tmp_path / "public-receipts" / f"{instance_id}.json").read_text()
    assert "Public issue text" not in public_text + receipt_text
    assert "diff --git" not in public_text + receipt_text
    assert "target file" not in public_text + receipt_text
    assert (tmp_path / "private" / f"{instance_id}.json").is_file()


def test_recorded_review_can_be_materialized_without_a_provider_call(tmp_path: Path) -> None:
    package_root = Path(__file__).parents[1]
    protocol = replace(
        ProviderReviewProtocol.load(
            package_root / "CORE_BENEFIT_SCREEN_REVIEW_PROVIDER_PROTOCOL_V1.json"
        ),
        private_output_root=tmp_path / "private",
        public_output_root=tmp_path / "public-decisions",
        public_receipt_root=tmp_path / "public-receipts",
    )
    instance_id = "matplotlib__matplotlib-20826"
    candidate = CandidateReviewInput(
        instance_id=instance_id,
        problem_statement="Public issue text.",
        patch="diff --git a/a.py b/a.py\n",
        test_patch="diff --git a/test_a.py b/test_a.py\n",
    )
    raw_response = {
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "message": {
                    "content": (
                        '{"primary_stratum":"localization",'
                        '"rationale":"The issue does not identify the target file."}'
                    )
                }
            }
        ],
    }
    private_path = protocol.private_output_root / f"{instance_id}.json"
    private_path.parent.mkdir(parents=True)
    private_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "review_id": f"core-benefit-screen-review-{instance_id}-deepseek-v4-flash-v1",
                "protocol_sha256": protocol.sha256,
                "prompt_sha256": protocol.prompt_sha256,
                "instance_id": instance_id,
                "request_sha256": hashlib.sha256(
                    json.dumps(build_review_request(protocol=protocol, candidate=candidate), sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "response_sha256": hashlib.sha256(
                    json.dumps(raw_response, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "model_name": protocol.model_name,
                "response_model_name": protocol.model_name,
                "candidate_input": candidate.as_payload(),
                "raw_response": raw_response,
                "rationale": "The issue does not identify the target file.",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    public_decision = materialize_recorded_review(
        protocol=protocol,
        private_path=private_path,
        nonexecution_receipt_path=(
            package_root / "exploratory_candidate_nonexecution_v1" / f"{instance_id}.json"
        ),
        host_preflight_path=package_root / "EXPLORATORY_ACQUISITION_PREFLIGHT_V6.json",
        inventory_path=package_root / "EXPLORATORY_CANDIDATE_INVENTORY_V4.json",
        image_receipt_path=(
            package_root / "exploratory_candidate_image_v1" / f"{instance_id}.json"
        ),
        execution_receipt_path=(
            package_root / "exploratory_candidate_execution_v1" / f"{instance_id}.json"
        ),
    )

    public_text = public_decision.read_text()
    receipt_text = (tmp_path / "public-receipts" / f"{instance_id}.json").read_text()
    assert "Public issue text" not in public_text + receipt_text
    assert "diff --git" not in public_text + receipt_text
    assert json.loads(receipt_text)["task_arm_outcomes_generated"] is False


def test_failed_call_records_no_retry_receipt(tmp_path: Path) -> None:
    package_root = Path(__file__).parents[1]
    protocol = replace(
        ProviderReviewProtocol.load(
            package_root / "CORE_BENEFIT_SCREEN_REVIEW_PROVIDER_PROTOCOL_V1.json"
        ),
        private_output_root=tmp_path / "private",
        public_output_root=tmp_path / "public-decisions",
        public_receipt_root=tmp_path / "public-receipts",
    )
    instance_id = "matplotlib__matplotlib-20826"

    def post(*_args: object, **_kwargs: object) -> object:
        raise ConnectionError("offline")

    try:
        review_candidate(
            protocol=protocol,
            candidate=CandidateReviewInput(
                instance_id=instance_id,
                problem_statement="Public issue text.",
                patch="diff --git a/a.py b/a.py\n",
                test_patch="diff --git a/test_a.py b/test_a.py\n",
            ),
            api_key="test-key",
            nonexecution_receipt_path=(
                package_root / "exploratory_candidate_nonexecution_v1" / f"{instance_id}.json"
            ),
            host_preflight_path=package_root / "EXPLORATORY_ACQUISITION_PREFLIGHT_V6.json",
            inventory_path=package_root / "EXPLORATORY_CANDIDATE_INVENTORY_V4.json",
            image_receipt_path=(
                package_root / "exploratory_candidate_image_v1" / f"{instance_id}.json"
            ),
            execution_receipt_path=(
                package_root / "exploratory_candidate_execution_v1" / f"{instance_id}.json"
            ),
            post=post,
        )
    except RuntimeError as error:
        assert "without retry" in str(error)
    else:
        raise AssertionError("failed provider call must fail closed")

    receipt = json.loads((tmp_path / "public-receipts" / f"{instance_id}.failed.json").read_text())
    assert receipt["review_status"] == "failed_no_retry"
    assert receipt["task_arm_outcomes_generated"] is False
    assert (tmp_path / "private" / f"{instance_id}.failed.json").is_file()
