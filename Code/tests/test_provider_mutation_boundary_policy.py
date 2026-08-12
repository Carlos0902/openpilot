from __future__ import annotations

import pytest

from core.provider_mutation_boundary_policy import (
    ProviderMutationBoundaryAction,
    ProviderMutationBoundaryError,
    provider_mutation_boundary,
)


def test_no_mutation_surface_is_allowed_without_opt_in_or_confirmation() -> None:
    decision = provider_mutation_boundary(
        mutation_tools_exposed=False,
        allow_mutations=False,
        user_confirmed=False,
    )

    assert decision.action is ProviderMutationBoundaryAction.ALLOW
    assert decision.error_code is None


def test_mutation_surface_requires_code_level_opt_in() -> None:
    decision = provider_mutation_boundary(
        mutation_tools_exposed=True,
        allow_mutations=False,
        user_confirmed=True,
    )

    assert decision.action is ProviderMutationBoundaryAction.FAIL
    assert decision.error_code == "ProviderToolMutationOptInRequired"


def test_mutation_surface_requires_user_confirmation() -> None:
    decision = provider_mutation_boundary(
        mutation_tools_exposed=True,
        allow_mutations=True,
        user_confirmed=False,
    )

    assert decision.action is ProviderMutationBoundaryAction.FAIL
    assert decision.error_code == "ProviderToolMutationConfirmationRequired"


def test_mutation_surface_is_allowed_when_both_facts_are_true() -> None:
    decision = provider_mutation_boundary(
        mutation_tools_exposed=True,
        allow_mutations=True,
        user_confirmed=True,
    )

    assert decision.action is ProviderMutationBoundaryAction.ALLOW
    assert decision.error_code is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"mutation_tools_exposed": 1},
        {"allow_mutations": "yes"},
        {"user_confirmed": None},
    ],
)
def test_boundary_rejects_non_literal_permission_facts(overrides) -> None:
    values = {
        "mutation_tools_exposed": False,
        "allow_mutations": False,
        "user_confirmed": False,
    }
    values.update(overrides)
    with pytest.raises(ProviderMutationBoundaryError):
        provider_mutation_boundary(**values)
