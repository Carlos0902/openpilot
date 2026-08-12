"""Atomic prepared-to-active task materialization for pre-task iteration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from enum import Enum

from autonomous_iteration.checkpoint_store import (
    CheckpointConflictError,
    RuntimeCheckpointStore,
)
from autonomous_iteration.iteration_turn_store import (
    IterationTurnConflictError,
    IterationTurnStore,
)
from metadata import (
    ActiveTaskBinding,
    CanonicalInitialTaskSnapshot,
    IterationAuthorityCeiling,
    IterationTurnRecordMetadata,
    PreparedTaskBinding,
    RuntimeExecutionMode,
    SessionConstraintProposalStatus,
    SessionIngressState,
)


class TaskMaterializationFailureCode(str, Enum):
    AUTHORITY_STALE = "authority_stale"
    CONFIRMATION_STALE = "confirmation_stale"
    SNAPSHOT_UNAVAILABLE = "snapshot_unavailable"
    SNAPSHOT_MISMATCH = "snapshot_mismatch"
    CHECKPOINT_MISMATCH = "checkpoint_mismatch"
    BINDING_CONFLICT = "binding_conflict"


class TaskMaterializationError(RuntimeError):
    """Typed fail-closed materialization or recovery result."""

    def __init__(self, code: TaskMaterializationFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class IterationTaskMaterializer:
    """Persist canonical snapshot, prepared binding, checkpoint, then active binding."""

    def __init__(
        self,
        turn_store: IterationTurnStore,
        checkpoint_store: RuntimeCheckpointStore,
        *,
        after_write: Callable[[str], None] | None = None,
    ) -> None:
        self.turn_store = turn_store
        self.checkpoint_store = checkpoint_store
        self.after_write = after_write

    def materialize(
        self,
        record: IterationTurnRecordMetadata,
        *,
        snapshot: CanonicalInitialTaskSnapshot,
        current_ingress: SessionIngressState,
    ) -> IterationTurnRecordMetadata:
        self._validate_snapshot(record, snapshot)
        self._validate_authority(record, snapshot, current_ingress)
        latest = self.turn_store.load_latest(
            record.identity.conversation_id,
            record.identity.run_id,
        )
        if latest is None:
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.BINDING_CONFLICT,
                "pre-task turn record is not durable",
            )
        if isinstance(latest.task_binding, ActiveTaskBinding):
            if latest.task_binding.snapshot_hash != snapshot.canonical_hash:
                raise TaskMaterializationError(
                    TaskMaterializationFailureCode.SNAPSHOT_MISMATCH,
                    "active task snapshot differs from materialization retry",
                )
            return self.recover(latest, current_ingress=current_ingress)
        if isinstance(latest.task_binding, PreparedTaskBinding):
            if latest.task_binding.snapshot_hash != snapshot.canonical_hash:
                raise TaskMaterializationError(
                    TaskMaterializationFailureCode.SNAPSHOT_MISMATCH,
                    "prepared task snapshot differs from materialization retry",
                )
            return self.recover(latest, current_ingress=current_ingress)
        if latest.record_id != record.record_id:
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.BINDING_CONFLICT,
                "latest pre-task record is not the requested materialization source",
            )

        reference = self.turn_store.save_artifact(
            record.identity.conversation_id,
            record.identity.run_id,
            kind="canonical_initial_task",
            payload=snapshot.model_dump(mode="json"),
        )
        prepared = latest.model_copy(
            update={
                "record_id": f"{latest.record_id}-task-prepared",
                "generation": latest.generation + 1,
                "task_binding": PreparedTaskBinding(
                    task_id=snapshot.initial_checkpoint.root_task_id,
                    state_digest=self._runtime_state_digest(snapshot),
                    snapshot_ref=reference,
                    snapshot_hash=snapshot.canonical_hash,
                    authority_revision=snapshot.session_authority_revision,
                    authority_hash=snapshot.session_authority_hash,
                ),
                "integrity_digest": "",
            }
        )
        prepared = IterationTurnRecordMetadata.model_validate(prepared.model_dump(mode="python"))
        try:
            prepared = self.turn_store.save(
                prepared,
                expected_generation=latest.generation,
            )
        except IterationTurnConflictError as exc:
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.BINDING_CONFLICT,
                "prepared task binding conflicted with durable turn state",
            ) from exc
        self._after_write("prepared_binding")
        return self._activate(prepared, snapshot=snapshot, current_ingress=current_ingress)

    def recover(
        self,
        prepared: IterationTurnRecordMetadata,
        *,
        current_ingress: SessionIngressState,
    ) -> IterationTurnRecordMetadata:
        latest = self.turn_store.load_latest(
            prepared.identity.conversation_id,
            prepared.identity.run_id,
        )
        if latest is not None and isinstance(latest.task_binding, ActiveTaskBinding):
            payload = self._load_snapshot(latest)
            snapshot = self._parse_snapshot(payload)
            self._validate_snapshot(latest, snapshot)
            self._validate_authority(latest, snapshot, current_ingress)
            self._validate_active_binding(latest, snapshot)
            self._validate_active_checkpoint(latest, snapshot)
            return latest
        if latest is None or not isinstance(latest.task_binding, PreparedTaskBinding):
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.BINDING_CONFLICT,
                "durable prepared task binding is unavailable",
            )
        payload = self._load_snapshot(latest)
        snapshot = self._parse_snapshot(payload)
        if snapshot.canonical_hash != latest.task_binding.snapshot_hash:
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.SNAPSHOT_MISMATCH,
                "canonical task snapshot hash differs from prepared binding",
            )
        self._validate_snapshot(latest, snapshot)
        self._validate_authority(latest, snapshot, current_ingress)
        return self._activate(latest, snapshot=snapshot, current_ingress=current_ingress)

    def _activate(
        self,
        prepared: IterationTurnRecordMetadata,
        *,
        snapshot: CanonicalInitialTaskSnapshot,
        current_ingress: SessionIngressState,
    ) -> IterationTurnRecordMetadata:
        self._validate_authority(prepared, snapshot, current_ingress)
        expected = snapshot.initial_checkpoint
        if self.checkpoint_store.checkpoint_exists(expected.run_id, expected.checkpoint_id):
            checkpoint = self.checkpoint_store.load(expected.run_id, expected.checkpoint_id)
            if checkpoint is None:
                raise TaskMaterializationError(
                    TaskMaterializationFailureCode.CHECKPOINT_MISMATCH,
                    "initial task checkpoint exists but is unreadable",
                )
            expected_saved = expected.model_copy(
                update={"integrity_checksum": checkpoint.integrity_checksum}
            )
            if checkpoint != expected_saved:
                raise TaskMaterializationError(
                    TaskMaterializationFailureCode.CHECKPOINT_MISMATCH,
                    "initial task checkpoint differs from canonical snapshot",
                )
        else:
            if self.checkpoint_store.load_latest(expected.run_id) is not None:
                raise TaskMaterializationError(
                    TaskMaterializationFailureCode.CHECKPOINT_MISMATCH,
                    "another runtime checkpoint exists before initial task activation",
                )
            try:
                checkpoint = self.checkpoint_store.save(expected, expected_generation=0)
            except CheckpointConflictError as exc:
                raise TaskMaterializationError(
                    TaskMaterializationFailureCode.CHECKPOINT_MISMATCH,
                    "initial task checkpoint conflicted with durable runtime state",
                ) from exc
            self._after_write("initial_checkpoint")

        binding = prepared.task_binding
        if not isinstance(binding, PreparedTaskBinding):
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.BINDING_CONFLICT,
                "task activation requires a prepared binding",
            )
        active = prepared.model_copy(
            update={
                "record_id": f"{prepared.record_id}-active",
                "generation": prepared.generation + 1,
                "task_binding": ActiveTaskBinding(
                    **binding.model_dump(exclude={"state"}),
                    checkpoint_id=checkpoint.checkpoint_id,
                    checkpoint_digest=checkpoint.integrity_checksum,
                ),
                "integrity_digest": "",
            }
        )
        active = IterationTurnRecordMetadata.model_validate(active.model_dump(mode="python"))
        try:
            active = self.turn_store.save(active, expected_generation=prepared.generation)
        except IterationTurnConflictError:
            winner = self.turn_store.load_latest(
                prepared.identity.conversation_id,
                prepared.identity.run_id,
            )
            if winner is None or not isinstance(winner.task_binding, ActiveTaskBinding):
                raise TaskMaterializationError(
                    TaskMaterializationFailureCode.BINDING_CONFLICT,
                    "active task binding conflicted with durable turn state",
                )
            self._validate_snapshot(winner, snapshot)
            self._validate_authority(winner, snapshot, current_ingress)
            self._validate_active_binding(winner, snapshot)
            self._validate_active_checkpoint(winner, snapshot)
            return winner
        self._after_write("active_binding")
        return active

    def _load_snapshot(self, record: IterationTurnRecordMetadata) -> dict:
        binding = record.task_binding
        if not isinstance(binding, (PreparedTaskBinding, ActiveTaskBinding)):
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.BINDING_CONFLICT,
                "task record has no snapshot binding",
            )
        payload = self.turn_store.load_artifact(
            record.identity.conversation_id,
            record.identity.run_id,
            binding.snapshot_ref,
        )
        if payload is None:
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.SNAPSHOT_UNAVAILABLE,
                "canonical initial-task snapshot is unavailable",
            )
        return payload

    @staticmethod
    def _parse_snapshot(payload: dict) -> CanonicalInitialTaskSnapshot:
        try:
            return CanonicalInitialTaskSnapshot.model_validate(payload)
        except ValueError as exc:
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.SNAPSHOT_MISMATCH,
                "canonical initial-task snapshot is invalid",
            ) from exc

    @staticmethod
    def _validate_snapshot(
        record: IterationTurnRecordMetadata,
        snapshot: CanonicalInitialTaskSnapshot,
    ) -> None:
        checkpoint = snapshot.initial_checkpoint
        if (
            checkpoint.run_id != record.identity.run_id
            or checkpoint.session_id != record.identity.run_id
            or checkpoint.session_ingress_state is None
            or checkpoint.session_ingress_state.identity != record.identity
            or checkpoint.project_fingerprint.project_root != record.identity.project_root
            or snapshot.authority_state != record.cursor.authority_state
            or snapshot.root_budget != record.root_budget
            or snapshot.session_authority_revision
            != record.pre_task_state.session_authority_revision
            or snapshot.session_authority_hash != record.pre_task_state.session_authority_hash
        ):
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.SNAPSHOT_MISMATCH,
                "canonical initial-task snapshot differs from pre-task authority or identity",
            )
        if (
            snapshot.authority_state.ceiling != IterationAuthorityCeiling.MUTATION_ELIGIBLE
            and checkpoint.runtime_state.execution_mode != RuntimeExecutionMode.READ_ONLY
        ):
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.SNAPSHOT_MISMATCH,
                "non-mutation authority cannot materialize a mutation-allowed task",
            )

    @classmethod
    def _validate_authority(
        cls,
        record: IterationTurnRecordMetadata,
        snapshot: CanonicalInitialTaskSnapshot,
        ingress: SessionIngressState,
    ) -> None:
        constraints = ingress.session_constraints
        lineage = cls._rejected_or_revoked_ids(ingress)
        if (
            ingress.identity != record.identity
            or constraints.revision != snapshot.session_authority_revision
            or constraints.authority_hash != snapshot.session_authority_hash
            or lineage != snapshot.authority_state.rejected_or_revoked_ids
        ):
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.AUTHORITY_STALE,
                "current session authority differs from canonical task snapshot",
            )
        authority = snapshot.authority_state
        if authority.ceiling == IterationAuthorityCeiling.MUTATION_ELIGIBLE:
            confirmation = next(
                (
                    turn
                    for turn in ingress.turns
                    if turn.message_id == authority.confirmation_message_id
                    and turn.identity.turn_index == authority.confirmation_turn_index
                    and turn.role == "user"
                ),
                None,
            )
            if confirmation is None:
                raise TaskMaterializationError(
                    TaskMaterializationFailureCode.CONFIRMATION_STALE,
                    "mutation confirmation is absent from current session ingress",
                )

    @staticmethod
    def _rejected_or_revoked_ids(ingress: SessionIngressState) -> tuple[str, ...]:
        rejected = [
            item.proposal_id
            for item in ingress.pending_proposals
            if item.status == SessionConstraintProposalStatus.REJECTED
        ]
        revoked = [item.constraint_id for item in ingress.session_constraints.revoked_entries]
        return tuple(sorted({*rejected, *revoked}))

    @staticmethod
    def _runtime_state_digest(snapshot: CanonicalInitialTaskSnapshot) -> str:
        payload = snapshot.initial_checkpoint.runtime_state.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def _validate_active_binding(
        cls,
        record: IterationTurnRecordMetadata,
        snapshot: CanonicalInitialTaskSnapshot,
    ) -> None:
        binding = record.task_binding
        if not isinstance(binding, ActiveTaskBinding) or (
            binding.task_id != snapshot.initial_checkpoint.root_task_id
            or binding.snapshot_hash != snapshot.canonical_hash
            or binding.state_digest != cls._runtime_state_digest(snapshot)
            or binding.checkpoint_id != snapshot.initial_checkpoint.checkpoint_id
            or binding.authority_revision != snapshot.session_authority_revision
            or binding.authority_hash != snapshot.session_authority_hash
        ):
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.BINDING_CONFLICT,
                "active task binding differs from canonical snapshot",
            )

    def _validate_active_checkpoint(
        self,
        record: IterationTurnRecordMetadata,
        snapshot: CanonicalInitialTaskSnapshot,
    ) -> None:
        binding = record.task_binding
        if not isinstance(binding, ActiveTaskBinding):
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.BINDING_CONFLICT,
                "active task checkpoint validation requires an active binding",
            )
        checkpoint = self.checkpoint_store.load(record.identity.run_id, binding.checkpoint_id)
        if checkpoint is None or checkpoint.integrity_checksum != binding.checkpoint_digest:
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.CHECKPOINT_MISMATCH,
                "active task checkpoint is unavailable or differs from binding",
            )
        expected = snapshot.initial_checkpoint.model_copy(
            update={"integrity_checksum": checkpoint.integrity_checksum}
        )
        if checkpoint != expected:
            raise TaskMaterializationError(
                TaskMaterializationFailureCode.CHECKPOINT_MISMATCH,
                "active task checkpoint differs from canonical snapshot",
            )

    def _after_write(self, boundary: str) -> None:
        if self.after_write is not None:
            self.after_write(boundary)


__all__ = [
    "IterationTaskMaterializer",
    "TaskMaterializationError",
    "TaskMaterializationFailureCode",
]
