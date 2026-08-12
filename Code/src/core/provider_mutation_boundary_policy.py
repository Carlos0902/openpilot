"""Derive the explicit permission boundary for exposed mutation tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProviderMutationBoundaryAction(str, Enum):
    """Next action for a provider mutation surface."""

    ALLOW = "allow"
    FAIL = "fail"


class ProviderMutationBoundaryError(ValueError):
    """Raised when mutation permission facts are malformed."""


@dataclass(frozen=True)
class ProviderMutationBoundaryDecision:
    """Immutable mutation permission result with stable failure code."""

    action: ProviderMutationBoundaryAction
    error_code: str | None


def provider_mutation_boundary(
    *,
    mutation_tools_exposed: bool,
    allow_mutations: bool,
    user_confirmed: bool,
) -> ProviderMutationBoundaryDecision:
    """Require both literal opt-in and confirmation only when mutation is exposed."""

    for label, value in (
        ("mutation_tools_exposed", mutation_tools_exposed),
        ("allow_mutations", allow_mutations),
        ("user_confirmed", user_confirmed),
    ):
        if type(value) is not bool:
            raise ProviderMutationBoundaryError(
                f"{label} must be a literal boolean"
            )
    if not mutation_tools_exposed:
        return ProviderMutationBoundaryDecision(
            action=ProviderMutationBoundaryAction.ALLOW,
            error_code=None,
        )
    if not allow_mutations:
        return ProviderMutationBoundaryDecision(
            action=ProviderMutationBoundaryAction.FAIL,
            error_code="ProviderToolMutationOptInRequired",
        )
    if not user_confirmed:
        return ProviderMutationBoundaryDecision(
            action=ProviderMutationBoundaryAction.FAIL,
            error_code="ProviderToolMutationConfirmationRequired",
        )
    return ProviderMutationBoundaryDecision(
        action=ProviderMutationBoundaryAction.ALLOW,
        error_code=None,
    )


__all__ = [
    "ProviderMutationBoundaryAction",
    "ProviderMutationBoundaryDecision",
    "ProviderMutationBoundaryError",
    "provider_mutation_boundary",
]
