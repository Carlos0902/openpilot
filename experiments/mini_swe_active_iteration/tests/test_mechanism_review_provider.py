from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mini_swe_active_iteration.mechanism_review_provider import (
    MechanismReviewProviderProtocol,
    parse_mechanism_review_response,
)


def test_protocol_binds_prompt_and_retains_task_arm_boundary() -> None:
    package_root = Path(__file__).parents[1]
    protocol = MechanismReviewProviderProtocol.load(
        package_root / "CORE_BENEFIT_SCREEN_MECHANISM_REVIEW_PROVIDER_PROTOCOL_V1.json"
    )

    assert protocol.prompt_sha256 == hashlib.sha256(protocol.prompt_path.read_bytes()).hexdigest()
    assert protocol.task_arm_provider_execution_authorized is False


def test_response_parser_rejects_extra_or_empty_scope() -> None:
    assert parse_mechanism_review_response(
        '{"diagnostic_measurement":false,"post_action_validation":true,'
        '"recovery_or_safe_stop":false,"rationale":"Validation distinguishes a full repair from regression."}'
    ) == (False, True, False, "Validation distinguishes a full repair from regression.")

    for content in (
        '{"diagnostic_measurement":false,"post_action_validation":false,'
        '"recovery_or_safe_stop":false,"rationale":"none"}',
        '{"diagnostic_measurement":true,"post_action_validation":false,'
        '"recovery_or_safe_stop":false,"rationale":"x","extra":true}',
    ):
        with pytest.raises(ValueError):
            parse_mechanism_review_response(content)
