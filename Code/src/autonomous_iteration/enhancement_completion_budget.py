"""Purpose-aware completion reservations for the optional enhancement stage."""

from __future__ import annotations

import hashlib

from metadata import (
    EnhancementCompletionComplexity,
    EnhancementCompletionDecisionValue,
    EnhancementCompletionReconciliation,
    EnhancementCompletionRequest,
    EnhancementCompletionReservation,
    RuntimeBudgetMetadata,
)


class EnhancementCompletionBudgetCoordinator:
    """Reserve and reconcile one shared enhancement completion-token pool."""

    def __init__(self, budget: RuntimeBudgetMetadata) -> None:
        self.budget = budget

    def _commit(self, **updates) -> None:
        """Apply one validator-checked ledger transition without changing owner identity."""

        payload = self.budget.model_dump(mode="python")
        payload.update(updates)
        validated = RuntimeBudgetMetadata.model_validate(payload)
        for field_name in updates:
            object.__setattr__(
                self.budget,
                field_name,
                getattr(validated, field_name),
            )

    def reserve(
        self,
        request: EnhancementCompletionRequest,
    ) -> EnhancementCompletionReservation | None:
        existing = self.budget.enhancement_completion_reservations.get(
            request.logical_key
        )
        if existing is not None:
            if (
                existing.purpose != request.purpose
                or existing.recovery_of != request.recovery_of
            ):
                raise ValueError("logical completion reservation key changed meaning")
            return existing

        policy = self.budget.enhancement_completion_policy
        purpose_limit = policy.purpose_limits[request.purpose]
        remaining = self.budget.enhancement_completion_tokens_remaining
        if remaining < purpose_limit.floor:
            return None

        recovery_limit = None
        if request.recovery_of:
            recovery_limit = self.budget.enhancement_completion_length_recovery_limits.get(
                request.recovery_of
            )
            if (
                recovery_limit is None
                or request.recovery_of in self.budget.enhancement_completion_length_recovery_used
            ):
                return None

        complexity_weight = {
            EnhancementCompletionComplexity.ROUTINE: 0.0,
            EnhancementCompletionComplexity.STANDARD: 0.45,
            EnhancementCompletionComplexity.COMPLEX: 0.8,
        }[request.complexity]
        value_bonus = {
            EnhancementCompletionDecisionValue.LOW: 0.0,
            EnhancementCompletionDecisionValue.NORMAL: 0.1,
            EnhancementCompletionDecisionValue.HIGH: 0.2,
        }[request.remaining_value]
        prompt_bonus = 0.1 if request.prompt_tokens >= 3_000 else 0.0
        weight = min(1.0, complexity_weight + value_bonus + prompt_bonus)
        desired = purpose_limit.floor + int(
            (purpose_limit.ceiling - purpose_limit.floor) * weight
        )
        fair_share = max(purpose_limit.floor, remaining // request.remaining_calls)
        desired = min(desired, fair_share, purpose_limit.ceiling, remaining)

        if recovery_limit is not None:
            desired = max(desired, recovery_limit + policy.recovery_step)
            desired = min(desired, purpose_limit.ceiling, remaining)
            if desired <= recovery_limit:
                return None
            recovery_used = [
                *self.budget.enhancement_completion_length_recovery_used,
                request.recovery_of,
            ]
        else:
            recovery_used = list(
                self.budget.enhancement_completion_length_recovery_used
            )

        if desired < purpose_limit.floor:
            return None
        reservation = EnhancementCompletionReservation(
            reservation_id=(
                "enhancement:"
                + hashlib.sha256(request.logical_key.encode("utf-8")).hexdigest()
            ),
            logical_key=request.logical_key,
            purpose=request.purpose,
            max_tokens=desired,
            recovery_of=request.recovery_of,
        )
        reservations = {
            **self.budget.enhancement_completion_reservations,
            request.logical_key: reservation,
        }
        self._commit(
            enhancement_completion_tokens_reserved=(
                self.budget.enhancement_completion_tokens_reserved + desired
            ),
            enhancement_completion_reservations=reservations,
            enhancement_completion_length_recovery_used=recovery_used,
        )
        return reservation

    def reconcile(
        self,
        reservation: EnhancementCompletionReservation,
        *,
        actual_tokens: int | None,
        finish_reason: str | None,
        response_empty: bool = False,
    ) -> EnhancementCompletionReconciliation:
        existing = self.budget.enhancement_completion_reconciliations.get(
            reservation.reservation_id
        )
        if existing is not None:
            return existing
        del response_empty  # Empty text is not usage evidence by itself.
        usage_known = actual_tokens is not None
        refunded = 0
        reserved_tokens = self.budget.enhancement_completion_tokens_reserved
        used_tokens = self.budget.enhancement_completion_tokens_used
        if usage_known:
            actual = max(0, int(actual_tokens or 0))
            reserved_tokens = max(
                0,
                self.budget.enhancement_completion_tokens_reserved - reservation.max_tokens,
            )
            used_tokens += actual
            refunded = max(0, reservation.max_tokens - actual)
        else:
            actual = None

        normalized_finish = str(finish_reason or "").lower()
        recovery_limits = dict(
            self.budget.enhancement_completion_length_recovery_limits
        )
        if normalized_finish in {"length", "max_tokens"}:
            recovery_limits.setdefault(
                reservation.reservation_id,
                reservation.max_tokens,
            )

        reconciliation = EnhancementCompletionReconciliation(
            reservation_id=reservation.reservation_id,
            reserved_tokens=reservation.max_tokens,
            actual_tokens=actual,
            refunded_tokens=refunded,
            usage_known=usage_known,
            finish_reason=finish_reason,
        )
        reconciliations = {
            **self.budget.enhancement_completion_reconciliations,
            reservation.reservation_id: reconciliation,
        }
        self._commit(
            enhancement_completion_tokens_reserved=reserved_tokens,
            enhancement_completion_tokens_used=used_tokens,
            enhancement_completion_length_recovery_limits=recovery_limits,
            enhancement_completion_reconciliations=reconciliations,
        )
        return reconciliation

    def reconcile_failure(
        self,
        reservation: EnhancementCompletionReservation,
        error: Exception,
    ) -> EnhancementCompletionReconciliation:
        """Settle known failed-attempt usage while retaining unknown reservations."""

        usage = getattr(error, "usage", None)
        actual_tokens = None
        if isinstance(usage, dict):
            actual_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        return self.reconcile(
            reservation,
            actual_tokens=(int(actual_tokens) if actual_tokens is not None else None),
            finish_reason=getattr(error, "finish_reason", None),
            response_empty=not bool(str(getattr(error, "response_text", "") or "")),
        )
