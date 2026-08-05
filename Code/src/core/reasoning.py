"""Provider capability resolution for provider-neutral reasoning requests."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from core.config import LLMSettings
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
    supported_efforts: tuple[ReasoningEffort, ...] = ()
    default_effort: ReasoningEffort | None = None


GENERIC_PROFILE = ReasoningCapabilityProfile(
    profile_id=ReasoningCapabilityProfileId.GENERIC_OPENAI_COMPATIBLE,
    version="v1",
)


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
    policy is independently justified.
    """

    if complexity == ReasoningDecisionComplexity.ROUTINE:
        return routine_tool_reasoning_policy(settings, routine=True)
    return ReasoningPolicy(
        mode=ReasoningMode.PROVIDER_DEFAULT,
        unsupported_behavior=UnsupportedReasoningBehavior.PROVIDER_DEFAULT,
    )


def select_reasoning_capability_profile(settings: LLMSettings) -> ReasoningCapabilityProfile:
    explicit_value = getattr(settings, "reasoning_capability_profile", None)
    explicit = (
        explicit_value.value
        if isinstance(explicit_value, ReasoningCapabilityProfileId)
        else str(explicit_value or "").strip()
    )
    if explicit:
        return _explicit_profile(explicit, settings.model)
    host = (urlparse(str(getattr(settings, "base_url", "") or "")).hostname or "").lower()
    model = str(getattr(settings, "model", "") or "").lower()
    if host == "api.openai.com" and _is_known_openai_reasoning_model(model):
        return _openai_profile(model)
    if host == "api.deepseek.com" and model in {"deepseek-v4-flash", "deepseek-v4-pro"}:
        return ReasoningCapabilityProfile(
            profile_id=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
            version="v1",
            supports_disabled=True,
            supports_enabled=True,
            supported_efforts=(ReasoningEffort.HIGH, ReasoningEffort.MAX),
            default_effort=ReasoningEffort.HIGH,
        )
    return GENERIC_PROFILE


def resolve_reasoning_policy(
    policy: ReasoningPolicy,
    settings: LLMSettings,
) -> ResolvedReasoningPolicy:
    profile = select_reasoning_capability_profile(settings)
    if policy.token_budget is not None:
        return _unsupported(
            policy,
            profile,
            "explicit reasoning token budget is unsupported",
        )
    if policy.mode == ReasoningMode.PROVIDER_DEFAULT:
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
    if resolved.effective_mode == ReasoningMode.PROVIDER_DEFAULT:
        return {}
    if resolved.profile_id == ReasoningCapabilityProfileId.OPENAI_CHAT_KNOWN:
        if resolved.effective_mode == ReasoningMode.DISABLED:
            return {"reasoning_effort": "none"}
        if resolved.effective_effort is not None:
            return {"reasoning_effort": resolved.effective_effort.value}
        return {}
    if resolved.profile_id == ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN:
        thinking_type = (
            "disabled" if resolved.effective_mode == ReasoningMode.DISABLED else "enabled"
        )
        rendered: dict[str, object] = {
            "extra_body": {"thinking": {"type": thinking_type}}
        }
        if resolved.effective_mode == ReasoningMode.ENABLED and resolved.effective_effort is not None:
            rendered["reasoning_effort"] = resolved.effective_effort.value
        return rendered
    return {}


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


def _explicit_profile(profile_id: str, model: str) -> ReasoningCapabilityProfile:
    normalized = profile_id.lower().removesuffix(":v1")
    if normalized == "generic-openai-compatible":
        return GENERIC_PROFILE
    if normalized == "openai-chat-known":
        if not _is_known_openai_reasoning_model(str(model).lower()):
            raise UnsupportedReasoningPolicyError("explicit OpenAI profile requires a known model")
        return _openai_profile(str(model).lower())
    if normalized == "deepseek-chat-known":
        return ReasoningCapabilityProfile(
            profile_id=ReasoningCapabilityProfileId.DEEPSEEK_CHAT_KNOWN,
            version="v1",
            supports_disabled=True,
            supports_enabled=True,
            supported_efforts=(ReasoningEffort.HIGH, ReasoningEffort.MAX),
            default_effort=ReasoningEffort.HIGH,
        )
    raise UnsupportedReasoningPolicyError(f"unknown reasoning capability profile: {profile_id}")


def _is_known_openai_reasoning_model(model: str) -> bool:
    return model.startswith(("gpt-5", "o1", "o3", "o4", "gpt-oss-"))


def _openai_profile(model: str) -> ReasoningCapabilityProfile:
    if model.startswith("gpt-5.6"):
        efforts = (
            ReasoningEffort.LOW,
            ReasoningEffort.MEDIUM,
            ReasoningEffort.HIGH,
            ReasoningEffort.XHIGH,
            ReasoningEffort.MAX,
        )
        supports_disabled = True
    elif model.startswith(("gpt-5.5", "gpt-5.4", "gpt-5.3", "gpt-5.2")):
        efforts = (
            ReasoningEffort.LOW,
            ReasoningEffort.MEDIUM,
            ReasoningEffort.HIGH,
            ReasoningEffort.XHIGH,
        )
        supports_disabled = True
    else:
        efforts = (
            ReasoningEffort.MINIMAL,
            ReasoningEffort.LOW,
            ReasoningEffort.MEDIUM,
            ReasoningEffort.HIGH,
        )
        supports_disabled = model.startswith("gpt-5.1")
    return ReasoningCapabilityProfile(
        profile_id=ReasoningCapabilityProfileId.OPENAI_CHAT_KNOWN,
        version="v1",
        supports_disabled=supports_disabled,
        supports_enabled=True,
        supported_efforts=efforts,
        default_effort=ReasoningEffort.MEDIUM,
    )
