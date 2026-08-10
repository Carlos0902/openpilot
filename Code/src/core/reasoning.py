"""Provider capability resolution for provider-neutral reasoning requests."""

from __future__ import annotations

from dataclasses import dataclass

from core.config import LLMSettings
from core.reasoning_adapters import (
    get_reasoning_transport_adapter,
    observe_reasoning_response,
)
from metadata import (
    ReasoningCapabilityProfileId,
    ReasoningDecisionComplexity,
    ReasoningEffort,
    ReasoningMode,
    ReasoningPolicy,
    ReasoningResolution,
    ReasoningTransportFamily,
    ResolvedReasoningPolicy,
    UnsupportedReasoningBehavior,
)


class UnsupportedReasoningPolicyError(ValueError):
    """Raised when an explicit policy is unsupported and must not be guessed."""


@dataclass(frozen=True)
class ReasoningCapabilityProfile:
    profile_id: ReasoningCapabilityProfileId
    version: str
    transport_family: ReasoningTransportFamily = (
        ReasoningTransportFamily.OPENAI_CHAT_COMPLETIONS
    )
    supports_disabled: bool = False
    supports_enabled: bool = False
    supports_token_budget: bool = False
    supported_efforts: tuple[ReasoningEffort, ...] = ()
    default_effort: ReasoningEffort | None = None


GENERIC_PROFILE = ReasoningCapabilityProfile(
    profile_id=ReasoningCapabilityProfileId.GENERIC_OPENAI_COMPATIBLE,
    version="v1",
)

# Capability is an explicit, versioned configuration value.  In particular,
# this registry must not be selected from a provider hostname or model name:
# those values identify a request, but do not prove which controls a proxy or
# deployed model accepts.
REASONING_CAPABILITY_PROFILES: dict[str, ReasoningCapabilityProfile] = {
    GENERIC_PROFILE.profile_id.value: GENERIC_PROFILE,
    ReasoningCapabilityProfileId.OPENAI_CHAT_KNOWN.value: ReasoningCapabilityProfile(
        profile_id=ReasoningCapabilityProfileId.OPENAI_CHAT_KNOWN,
        version="v1",
        supports_disabled=True,
        supports_enabled=True,
        supported_efforts=(
            ReasoningEffort.LOW,
            ReasoningEffort.MEDIUM,
            ReasoningEffort.HIGH,
            ReasoningEffort.XHIGH,
            ReasoningEffort.MAX,
        ),
        default_effort=ReasoningEffort.MEDIUM,
    ),
    ReasoningCapabilityProfileId.OPENAI_CHAT_NO_REASONING_KNOWN.value: ReasoningCapabilityProfile(
        profile_id=ReasoningCapabilityProfileId.OPENAI_CHAT_NO_REASONING_KNOWN,
        version="v1",
        supports_disabled=True,
        supports_enabled=False,
        supported_efforts=(),
        default_effort=None,
    ),
    ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN.value: ReasoningCapabilityProfile(
        profile_id=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
        version="v1",
        supports_disabled=True,
        supports_enabled=True,
        supported_efforts=(ReasoningEffort.HIGH, ReasoningEffort.MAX),
        default_effort=ReasoningEffort.HIGH,
    ),
    ReasoningCapabilityProfileId.ANTHROPIC_MESSAGES_KNOWN.value: ReasoningCapabilityProfile(
        profile_id=ReasoningCapabilityProfileId.ANTHROPIC_MESSAGES_KNOWN,
        version="v1",
        transport_family=ReasoningTransportFamily.ANTHROPIC_MESSAGES,
        supports_disabled=True,
        supports_enabled=True,
        supports_token_budget=True,
        supported_efforts=(
            ReasoningEffort.LOW,
            ReasoningEffort.MEDIUM,
            ReasoningEffort.HIGH,
            ReasoningEffort.MAX,
        ),
        default_effort=ReasoningEffort.MEDIUM,
    ),
    ReasoningCapabilityProfileId.GEMINI_GENERATE_CONTENT_KNOWN.value: ReasoningCapabilityProfile(
        profile_id=ReasoningCapabilityProfileId.GEMINI_GENERATE_CONTENT_KNOWN,
        version="v1",
        transport_family=ReasoningTransportFamily.GOOGLE_GENERATE_CONTENT,
        supports_disabled=True,
        supports_enabled=True,
        supports_token_budget=True,
        supported_efforts=(
            ReasoningEffort.MINIMAL,
            ReasoningEffort.LOW,
            ReasoningEffort.MEDIUM,
            ReasoningEffort.HIGH,
        ),
        default_effort=ReasoningEffort.MEDIUM,
    ),
}


def routine_tool_reasoning_policy(
    settings: object | None,
    *,
    routine: bool = True,
) -> ReasoningPolicy:
    """Return the bounded routine policy, with an explicit A/B baseline override."""

    mode = ReasoningMode(
        getattr(settings, "tool_event_reasoning_mode", ReasoningMode.DISABLED)
    )
    if mode == ReasoningMode.DISABLED and not routine:
        mode = ReasoningMode.PROVIDER_DEFAULT
    return ReasoningPolicy(
        mode=mode,
        unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
    )


def reasoning_policy_for_decision(
    settings: object | None,
    complexity: ReasoningDecisionComplexity,
) -> ReasoningPolicy:
    """Map typed decision complexity to provider-neutral request intent.

    Capability resolution remains in :func:`resolve_reasoning_policy`; this
    helper never inspects a model name or emits provider transport fields.
    Standard and complex decisions begin at provider default until an enabled
    policy is independently justified.  The caller must pass complexity as a
    typed value; this helper never inspects task prose or provider identity.
    """

    if complexity == ReasoningDecisionComplexity.ROUTINE:
        return routine_tool_reasoning_policy(settings, routine=True)
    return ReasoningPolicy(
        mode=ReasoningMode.PROVIDER_DEFAULT,
        unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
    )


def select_reasoning_capability_profile(settings: LLMSettings) -> ReasoningCapabilityProfile:
    """Select a configured capability profile, defaulting to no controls.

    Provider and model settings are intentionally not capability evidence.  A
    compatible endpoint can proxy a different model, and model names are not a
    stable API contract.  Callers that want provider controls must opt in to a
    known profile through the typed settings field.
    """

    explicit_value = getattr(settings, "reasoning_capability_profile", None)
    explicit = (
        explicit_value.value
        if isinstance(explicit_value, ReasoningCapabilityProfileId)
        else str(explicit_value or "").strip()
    )
    if not explicit:
        return GENERIC_PROFILE
    return _explicit_profile(explicit)


def resolve_reasoning_policy(
    policy: ReasoningPolicy,
    settings: LLMSettings,
    *,
    structured_output: bool = False,
) -> ResolvedReasoningPolicy:
    profile = select_reasoning_capability_profile(settings)
    if policy.token_budget is not None and not profile.supports_token_budget:
        return _unsupported(
            policy,
            profile,
            "explicit reasoning token budget is unsupported",
        )
    if policy.mode == ReasoningMode.PROVIDER_DEFAULT:
        if structured_output and profile.supports_disabled:
            return _resolved(
                policy,
                profile,
                mode=ReasoningMode.DISABLED,
                resolution=ReasoningResolution.MAPPED,
            )
        return _resolved(
            policy,
            profile,
            mode=ReasoningMode.PROVIDER_DEFAULT,
            resolution=ReasoningResolution.OMITTED,
        )
    if policy.mode == ReasoningMode.DISABLED:
        if profile.supports_disabled:
            return _resolved(
                policy,
                profile,
                mode=ReasoningMode.DISABLED,
                resolution=ReasoningResolution.EXACT,
            )
        return _unsupported(policy, profile, "reasoning disable is unsupported")
    if not profile.supports_enabled:
        return _unsupported(policy, profile, "explicit reasoning is unsupported")

    requested_effort = policy.effort
    if policy.mode == ReasoningMode.ADAPTIVE and requested_effort is None:
        return _resolved(
            policy,
            profile,
            mode=ReasoningMode.ENABLED,
            effort=profile.default_effort,
            token_budget=policy.token_budget,
            resolution=ReasoningResolution.MAPPED,
        )
    effective_effort = requested_effort or profile.default_effort
    if effective_effort in profile.supported_efforts:
        return _resolved(
            policy,
            profile,
            mode=ReasoningMode.ENABLED,
            effort=effective_effort,
            token_budget=policy.token_budget,
            resolution=(
                ReasoningResolution.EXACT
                if policy.mode == ReasoningMode.ENABLED
                else ReasoningResolution.MAPPED
            ),
        )
    if policy.unsupported_behavior == UnsupportedReasoningBehavior.CLAMP:
        mapped = _mapped_effort(profile, effective_effort)
        if mapped is not None:
            resolution = (
                ReasoningResolution.MAPPED
                if profile.profile_id == ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN
                else ReasoningResolution.CLAMPED
            )
            return _resolved(
                policy,
                profile,
                mode=ReasoningMode.ENABLED,
                effort=mapped,
                token_budget=policy.token_budget,
                resolution=resolution,
            )
    return _unsupported(policy, profile, f"reasoning effort {effective_effort!s} is unsupported")


def render_reasoning_transport(resolved: ResolvedReasoningPolicy) -> dict[str, object]:
    return get_reasoning_transport_adapter(resolved.profile_id).render(resolved)


def _resolved(
    policy: ReasoningPolicy,
    profile: ReasoningCapabilityProfile,
    *,
    mode: ReasoningMode,
    resolution: ReasoningResolution,
    effort: ReasoningEffort | None = None,
    token_budget: int | None = None,
) -> ResolvedReasoningPolicy:
    return ResolvedReasoningPolicy(
        requested=policy,
        effective_mode=mode,
        effective_effort=effort,
        effective_token_budget=token_budget,
        resolution=resolution,
        profile_id=profile.profile_id,
        profile_version=profile.version,
        transport_family=profile.transport_family,
    )


def _unsupported(
    policy: ReasoningPolicy,
    profile: ReasoningCapabilityProfile,
    message: str,
) -> ResolvedReasoningPolicy:
    if policy.unsupported_behavior in {
        UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
        UnsupportedReasoningBehavior.CLAMP,
    }:
        return _resolved(
            policy,
            profile,
            mode=ReasoningMode.PROVIDER_DEFAULT,
            resolution=ReasoningResolution.OMITTED,
        )
    raise UnsupportedReasoningPolicyError(
        f"{message} for capability profile {profile.profile_id}:{profile.version}"
    )


def _mapped_effort(
    profile: ReasoningCapabilityProfile,
    requested: ReasoningEffort | None,
) -> ReasoningEffort | None:
    if not profile.supported_efforts:
        return None
    if profile.profile_id == ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN:
        if requested in {ReasoningEffort.MINIMAL, ReasoningEffort.LOW, ReasoningEffort.MEDIUM}:
            return ReasoningEffort.HIGH
        if requested == ReasoningEffort.XHIGH:
            return ReasoningEffort.MAX
    order = list(ReasoningEffort)
    requested_index = order.index(requested) if requested in order else 0
    return min(
        profile.supported_efforts,
        key=lambda effort: abs(order.index(effort) - requested_index),
    )


def _explicit_profile(profile_id: str) -> ReasoningCapabilityProfile:
    """Resolve one registry entry without inspecting model or endpoint text."""

    normalized = profile_id.lower().strip()
    if ":" in normalized:
        base_id, version = normalized.rsplit(":", 1)
        if version != "v1":
            raise UnsupportedReasoningPolicyError(
                f"unknown reasoning capability profile version: {profile_id}"
            )
        normalized = base_id
    profile = REASONING_CAPABILITY_PROFILES.get(normalized)
    if profile is None:
        raise UnsupportedReasoningPolicyError(
            f"unknown reasoning capability profile: {profile_id}"
        )
    return profile
