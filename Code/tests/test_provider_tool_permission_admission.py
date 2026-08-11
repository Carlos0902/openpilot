from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.provider_tool_admission import (
    ProviderToolPermissionDecision,
    provider_tool_permission_decision,
)
from core.tool_contracts import PermissionLevel, ToolCapability


def _definition(*, permission=PermissionLevel.LOW, capabilities=()):
    return SimpleNamespace(
        permission_level=permission,
        capabilities=list(capabilities),
    )


def test_low_risk_read_only_tool_is_admitted_without_confirmation() -> None:
    decision = provider_tool_permission_decision(
        "file_reader",
        _definition(capabilities=[ToolCapability.FILE_READ]),
        user_confirmed=False,
        allow_mutations=False,
    )

    assert decision == ProviderToolPermissionDecision(
        status="admitted",
        reason_code="allowed",
        requires_confirmation=False,
        mutating=False,
    )


@pytest.mark.parametrize("permission", [PermissionLevel.MEDIUM, PermissionLevel.HIGH])
def test_elevated_permission_requires_confirmation(permission) -> None:
    blocked = provider_tool_permission_decision(
        "reviewer",
        _definition(permission=permission),
        user_confirmed=False,
        allow_mutations=False,
    )
    admitted = provider_tool_permission_decision(
        "reviewer",
        _definition(permission=permission),
        user_confirmed=True,
        allow_mutations=False,
    )

    assert blocked.reason_code == "confirmation_required"
    assert blocked.requires_confirmation is True
    assert admitted.reason_code == "allowed"


def test_mutation_requires_separate_opt_in_and_confirmation() -> None:
    definition = _definition(capabilities=[ToolCapability.FILE_WRITE])

    no_opt_in = provider_tool_permission_decision(
        "file_patch_writer",
        definition,
        user_confirmed=True,
        allow_mutations=False,
    )
    no_confirmation = provider_tool_permission_decision(
        "file_patch_writer",
        definition,
        user_confirmed=False,
        allow_mutations=True,
    )
    admitted = provider_tool_permission_decision(
        "file_patch_writer",
        definition,
        user_confirmed=True,
        allow_mutations=True,
    )

    assert no_opt_in.reason_code == "mutation_not_allowed"
    assert no_confirmation.reason_code == "confirmation_required"
    assert admitted == ProviderToolPermissionDecision(
        status="admitted",
        reason_code="allowed",
        requires_confirmation=True,
        mutating=True,
    )


def test_named_mutation_tool_is_mutating_even_without_capability() -> None:
    decision = provider_tool_permission_decision(
        "file_writer",
        _definition(permission=PermissionLevel.LOW),
        user_confirmed=False,
        allow_mutations=False,
    )

    assert decision.mutating is True
    assert decision.reason_code == "mutation_not_allowed"


@pytest.mark.parametrize(
    ("permission", "reason_code"),
    [(PermissionLevel.FORBIDDEN, "forbidden"), ("invented", "unknown_permission")],
)
def test_forbidden_or_unknown_permission_fails_closed(permission, reason_code) -> None:
    decision = provider_tool_permission_decision(
        "tool",
        _definition(permission=permission),
        user_confirmed=True,
        allow_mutations=True,
    )

    assert decision.status == "blocked"
    assert decision.reason_code == reason_code


@pytest.mark.parametrize(
    ("user_confirmed", "allow_mutations"),
    [(1, False), (False, "yes")],
)
def test_permission_inputs_require_literal_booleans(user_confirmed, allow_mutations) -> None:
    with pytest.raises(ValueError, match="literal booleans"):
        provider_tool_permission_decision(
            "file_reader",
            _definition(),
            user_confirmed=user_confirmed,
            allow_mutations=allow_mutations,
        )


def test_permission_decision_rejects_contradictory_state() -> None:
    with pytest.raises(ValidationError):
        ProviderToolPermissionDecision(
            status="admitted",
            reason_code="forbidden",
            requires_confirmation=False,
            mutating=False,
        )
