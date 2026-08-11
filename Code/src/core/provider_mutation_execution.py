"""Execute prepared provider mutations through the shared tool lifecycle."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.provider_code_artifact_ledger import ProviderCodeArtifactLedger
from core.provider_generated_unit_redaction import (
    redact_provider_generated_units,
)
from core.provider_mutation_execution_batch import (
    prepare_provider_mutation_execution_batch,
)
from core.provider_readonly_execution import (
    execute_prepared_provider_admissions,
)
from core.provider_tool_admission import ProviderToolAdmission


def execute_provider_mutation_admissions(
    runner: Any,
    task: Any,
    admissions: list[ProviderToolAdmission] | tuple[ProviderToolAdmission, ...],
    *,
    round_index: int = 1,
    code_artifact_ledger: ProviderCodeArtifactLedger | None = None,
    authorized_post_processing_write_scope: Sequence[str] | None = None,
):
    """Prepare and execute one bounded provider mutation batch."""

    task_id = str(getattr(task, "id", "unknown"))
    session_id = runner.owner._session_id()
    prepared = prepare_provider_mutation_execution_batch(
        admissions,
        task_id=task_id,
        session_id=session_id,
        round_index=round_index,
        code_artifact_ledger=code_artifact_ledger,
        authorized_post_processing_write_scope=(
            authorized_post_processing_write_scope
        ),
    )
    loop_result = execute_prepared_provider_admissions(
        runner,
        task,
        prepared,
        round_index=round_index,
        mutation_mode=True,
    )
    return redact_provider_generated_units(loop_result)


__all__ = ["execute_provider_mutation_admissions"]
