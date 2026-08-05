"""Strict, outcome-free rules for the exploratory task acquisition stage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatasetSource(_StrictModel):
    dataset_name: str
    split: str
    revision: str
    harness_revision: str | None = None
    expected_row_count: int
    dataset_url: str
    dataset_documentation_url: str
    harness_repository_url: str

    @model_validator(mode="after")
    def validate_source(self) -> "DatasetSource":
        if (
            not self.dataset_name.strip()
            or not self.split.strip()
            or self.expected_row_count <= 0
        ):
            raise ValueError("dataset source fields must be complete")
        if len(self.revision) != 40 or any(
            character not in "0123456789abcdef"
            for character in self.revision
        ):
            raise ValueError("dataset revision must be a full lowercase SHA-1")
        if self.harness_revision is not None and (
            len(self.harness_revision) != 40
            or any(
                character not in "0123456789abcdef"
                for character in self.harness_revision
            )
        ):
            raise ValueError(
                "harness revision must be a full lowercase SHA-1"
            )
        for url in (
            self.dataset_url,
            self.dataset_documentation_url,
            self.harness_repository_url,
        ):
            if not url.startswith("https://"):
                raise ValueError("source URLs must use HTTPS")
        return self


class SamplingRules(_StrictModel):
    algorithm: str
    selection_seed: int
    target_task_count: int
    minimum_eligible_pool_size: int
    maximum_tasks_per_repository: int
    canonical_sort_key: str
    without_replacement: bool
    stratum_precedence: tuple[str, ...]
    stratum_quotas: dict[str, int]

    @model_validator(mode="after")
    def validate_sampling(self) -> "SamplingRules":
        if self.algorithm != "sha256-ranked-stratified-v1":
            raise ValueError("unsupported sampling algorithm")
        if self.canonical_sort_key != "instance_id":
            raise ValueError("canonical_sort_key must be instance_id")
        if not self.without_replacement:
            raise ValueError("sampling must be without replacement")
        if (
            self.target_task_count <= 0
            or self.minimum_eligible_pool_size < self.target_task_count
            or self.maximum_tasks_per_repository <= 0
        ):
            raise ValueError("sampling counts are inconsistent")
        if tuple(self.stratum_quotas) != self.stratum_precedence:
            raise ValueError("stratum quota order must match precedence")
        if any(quota <= 0 for quota in self.stratum_quotas.values()):
            raise ValueError("stratum quotas must be positive")
        if sum(self.stratum_quotas.values()) != self.target_task_count:
            raise ValueError("stratum quotas must equal target_task_count")
        return self


class PreflightRules(_StrictModel):
    clean_snapshot_required: bool
    pinned_base_commit_required: bool
    pinned_harness_commit_required: bool
    docker_required: bool
    gold_pass_repetitions: int
    base_failure_repetitions: int
    identical_repeat_outcomes_required: bool
    maximum_setup_seconds: int
    maximum_evaluator_seconds: int
    maximum_checkout_bytes: int
    minimum_docker_memory_bytes: int | None = None
    instance_image_source: str | None = None
    agent_network_allowed: bool
    evaluator_network_allowed: bool
    credentials_allowed: bool
    gpu_allowed: bool

    @model_validator(mode="after")
    def validate_preflight(self) -> "PreflightRules":
        required = (
            self.clean_snapshot_required,
            self.pinned_base_commit_required,
            self.pinned_harness_commit_required,
            self.docker_required,
            self.identical_repeat_outcomes_required,
        )
        if not all(required):
            raise ValueError("reproducibility preflight must fail closed")
        if self.gold_pass_repetitions < 2 or self.base_failure_repetitions < 2:
            raise ValueError("base and gold checks require two repetitions")
        if min(
            self.maximum_setup_seconds,
            self.maximum_evaluator_seconds,
            self.maximum_checkout_bytes,
        ) <= 0:
            raise ValueError("preflight resource limits must be positive")
        if (
            self.minimum_docker_memory_bytes is not None
            and self.minimum_docker_memory_bytes <= 0
        ):
            raise ValueError("minimum Docker memory must be positive")
        if self.instance_image_source not in {
            None,
            "official_swebench_registry_pinned_digest",
        }:
            raise ValueError("unsupported instance image source")
        if (
            self.agent_network_allowed
            or self.evaluator_network_allowed
            or self.credentials_allowed
            or self.gpu_allowed
        ):
            raise ValueError("task execution must not depend on network or secrets")
        return self


class HiddenDataPolicy(_StrictModel):
    gold_patch_visible_to_agent: bool
    test_patch_visible_to_agent: bool
    fail_to_pass_visible_to_agent: bool
    pass_to_pass_visible_to_agent: bool
    stratum_visible_to_agent: bool
    arm_label_visible_to_evaluator: bool
    acquisition_review_may_use_gold_and_test_patch: bool
    hidden_materialization_boundary: str

    @model_validator(mode="after")
    def validate_hidden_boundary(self) -> "HiddenDataPolicy":
        exposed = (
            self.gold_patch_visible_to_agent,
            self.test_patch_visible_to_agent,
            self.fail_to_pass_visible_to_agent,
            self.pass_to_pass_visible_to_agent,
            self.stratum_visible_to_agent,
            self.arm_label_visible_to_evaluator,
        )
        if any(exposed) or not self.acquisition_review_may_use_gold_and_test_patch:
            raise ValueError("hidden evaluator material must stay outside the agent")
        if self.hidden_materialization_boundary != "evaluator_container_only":
            raise ValueError("hidden evaluator boundary must be container-only")
        return self


class ReviewPolicy(_StrictModel):
    independent_reviewer_count: int
    reviewers_blind_to_future_arm_outcomes: bool
    disagreements_require_adjudication: bool
    review_records_required: bool
    exposure_registry_required: bool
    post_outcome_relabeling_allowed: bool

    @model_validator(mode="after")
    def validate_review(self) -> "ReviewPolicy":
        if self.independent_reviewer_count < 2:
            raise ValueError("stratification requires at least two reviewers")
        if not all(
            (
                self.reviewers_blind_to_future_arm_outcomes,
                self.disagreements_require_adjudication,
                self.review_records_required,
                self.exposure_registry_required,
            )
        ):
            raise ValueError("review provenance must be complete")
        if self.post_outcome_relabeling_allowed:
            raise ValueError("post-outcome relabeling is forbidden")
        return self


class ExploratoryAcquisitionRules(_StrictModel):
    schema_version: str
    rules_id: str
    lifecycle: str
    source: DatasetSource
    source_rationale: str
    known_source_limitations: tuple[str, ...]
    inclusion_requirements: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    exposure_registry_paths: tuple[str, ...]
    required_candidate_manifest_fields: tuple[str, ...]
    stratum_definitions: dict[str, str]
    sampling: SamplingRules
    preflight: PreflightRules
    hidden_data_policy: HiddenDataPolicy
    review_policy: ReviewPolicy
    task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool
    hypothesis_evidence_eligible: bool

    @model_validator(mode="after")
    def validate_rules(self) -> "ExploratoryAcquisitionRules":
        if self.schema_version != "1.0":
            raise ValueError("unsupported acquisition schema")
        if self.lifecycle != "exploratory":
            raise ValueError("acquisition lifecycle must be exploratory")
        if not self.rules_id.strip() or not self.source_rationale.strip():
            raise ValueError("rules identity and rationale are required")
        if not all(
            (
                self.known_source_limitations,
                self.inclusion_requirements,
                self.exclusion_reasons,
                self.exposure_registry_paths,
                self.required_candidate_manifest_fields,
            )
        ):
            raise ValueError("acquisition rules must be complete")
        if set(self.stratum_definitions) != set(
            self.sampling.stratum_quotas
        ):
            raise ValueError("stratum definitions must match sampling quotas")
        if (
            self.task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
            or self.hypothesis_evidence_eligible
        ):
            raise ValueError("acquisition rules cannot authorize task outcomes")
        return self

    def canonical_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def rank_instance_ids(self, instance_ids: Any) -> tuple[str, ...]:
        values = tuple(instance_ids)
        if not values or any(
            not isinstance(value, str) or not value.strip()
            for value in values
        ):
            raise ValueError("instance IDs must be non-empty strings")
        if len(values) != len(set(values)):
            raise ValueError("instance IDs must be unique")
        return tuple(
            sorted(
                values,
                key=lambda value: (
                    hashlib.sha256(
                        f"{self.sampling.selection_seed}:{value}".encode()
                    ).hexdigest(),
                    value,
                ),
            )
        )


class AcquisitionPreflightReceipt(_StrictModel):
    schema_version: str
    receipt_id: str
    rules_canonical_sha256: str
    source_revision: str
    source_parquet_sha256: str
    source_parquet_bytes: int
    source_row_count: int
    unique_instance_id_count: int
    repository_count: int
    source_columns: tuple[str, ...]
    source_gate_passed: bool
    host_architecture: str
    available_storage_bytes: int
    required_storage_bytes: int
    docker_client_version: str
    docker_daemon_available: bool
    blockers: tuple[str, ...]
    host_resource_gate_passed: bool
    eligible_pool_generated: bool
    exploratory_protocol_frozen: bool
    task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_receipt(self) -> "AcquisitionPreflightReceipt":
        if self.schema_version != "1.0" or not self.receipt_id.strip():
            raise ValueError("unsupported or unidentified preflight receipt")
        for digest in (
            self.rules_canonical_sha256,
            self.source_parquet_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError("preflight hashes must be lowercase SHA-256")
        if min(
            self.source_parquet_bytes,
            self.source_row_count,
            self.unique_instance_id_count,
            self.repository_count,
            self.required_storage_bytes,
        ) <= 0:
            raise ValueError("preflight counts must be positive")
        if (
            not self.source_columns
            or not self.source_gate_passed
            or not self.blockers
        ):
            raise ValueError("source evidence and blockers must be explicit")
        if self.host_resource_gate_passed:
            raise ValueError("a blocked receipt cannot pass the host gate")
        if (
            self.eligible_pool_generated
            or self.exploratory_protocol_frozen
            or self.task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError("blocked preflight cannot authorize later stages")
        return self


class AcquisitionHostFacts(_StrictModel):
    host_architecture: str
    os_name: str
    os_version: str
    physical_memory_bytes: int
    available_storage_bytes: int
    docker_client_version: str
    docker_server_version: str
    docker_server_architecture: str
    docker_memory_bytes: int
    docker_virtual_disk_bytes: int | None = None
    docker_virtual_disk_allocated_bytes: int | None = None
    docker_daemon_available: bool
    docker_smoke_image: str
    docker_smoke_image_id: str
    docker_smoke_passed: bool
    harness_commit: str
    harness_clean: bool
    harness_python_version: str
    harness_environment_fingerprint: str
    harness_import_passed: bool
    harness_cli_passed: bool

    @model_validator(mode="after")
    def validate_host_facts(self) -> "AcquisitionHostFacts":
        required_text = (
            self.host_architecture,
            self.os_name,
            self.os_version,
            self.docker_client_version,
            self.docker_server_version,
            self.docker_server_architecture,
            self.docker_smoke_image,
            self.docker_smoke_image_id,
            self.harness_python_version,
        )
        if any(not value.strip() for value in required_text):
            raise ValueError("host facts must be complete")
        if min(
            self.physical_memory_bytes,
            self.available_storage_bytes,
            self.docker_memory_bytes,
        ) <= 0:
            raise ValueError("host resource counts must be positive")
        if (self.docker_virtual_disk_bytes is None) != (
            self.docker_virtual_disk_allocated_bytes is None
        ):
            raise ValueError(
                "Docker virtual disk size and allocation must appear together"
            )
        if self.docker_virtual_disk_bytes is not None and (
            self.docker_virtual_disk_bytes <= 0
            or self.docker_virtual_disk_allocated_bytes is None
            or self.docker_virtual_disk_allocated_bytes < 0
            or self.docker_virtual_disk_allocated_bytes
            > self.docker_virtual_disk_bytes
        ):
            raise ValueError("Docker virtual disk counts are inconsistent")
        if len(self.harness_commit) != 40 or any(
            character not in "0123456789abcdef"
            for character in self.harness_commit
        ):
            raise ValueError("harness commit must be a full lowercase SHA-1")
        if len(self.harness_environment_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.harness_environment_fingerprint
        ):
            raise ValueError(
                "harness environment fingerprint must be lowercase SHA-256"
            )
        if not self.docker_smoke_image_id.startswith("sha256:") or len(
            self.docker_smoke_image_id.removeprefix("sha256:")
        ) != 64:
            raise ValueError("Docker smoke image must use a full image ID")
        return self


class AcquisitionHostPreflightReceiptV2(_StrictModel):
    schema_version: str
    receipt_id: str
    rules_file_sha256: str
    rules_canonical_sha256: str
    source_revision: str
    source_parquet_sha256: str
    source_parquet_bytes: int
    source_row_count: int
    unique_instance_id_count: int
    repository_count: int
    source_columns: tuple[str, ...]
    source_gate_passed: bool
    required_storage_bytes: int
    host_facts: AcquisitionHostFacts
    docker_runtime_fingerprint: str
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]
    host_resource_gate_passed: bool
    eligible_pool_generated: bool
    exploratory_protocol_frozen: bool
    task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_receipt(self) -> "AcquisitionHostPreflightReceiptV2":
        if self.schema_version != "2.0" or not self.receipt_id.strip():
            raise ValueError("unsupported or unidentified V2 preflight receipt")
        for digest in (
            self.rules_file_sha256,
            self.rules_canonical_sha256,
            self.source_parquet_sha256,
            self.docker_runtime_fingerprint,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError("V2 preflight hashes must be lowercase SHA-256")
        if min(
            self.source_parquet_bytes,
            self.source_row_count,
            self.unique_instance_id_count,
            self.repository_count,
            self.required_storage_bytes,
        ) <= 0:
            raise ValueError("V2 preflight counts must be positive")
        if not self.source_columns:
            raise ValueError("V2 source columns must be explicit")
        if self.host_resource_gate_passed != (
            self.source_gate_passed and not self.blockers
        ):
            raise ValueError("V2 host gate must agree with source and blockers")
        if (
            self.eligible_pool_generated
            or self.exploratory_protocol_frozen
            or self.task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError(
                "host preflight cannot authorize task or provider outcomes"
            )
        return self


class CandidateRecord(_StrictModel):
    instance_id: str
    repo: str
    base_commit: str
    environment_setup_commit: str
    difficulty: str
    created_at: str
    problem_statement_sha256: str
    dataset_row_sha256: str
    selection_rank: int
    previously_exposed: bool
    exposure_paths: tuple[str, ...]
    preflight_status: str
    eligibility: str

    @model_validator(mode="after")
    def validate_candidate(self) -> "CandidateRecord":
        if not all(
            value.strip()
            for value in (
                self.instance_id,
                self.repo,
                self.base_commit,
                self.problem_statement_sha256,
                self.dataset_row_sha256,
            )
        ):
            raise ValueError("candidate identity must be complete")
        if self.selection_rank <= 0:
            raise ValueError("selection_rank must be positive")
        for digest in (
            self.problem_statement_sha256,
            self.dataset_row_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError("candidate hashes must be lowercase SHA-256")
        if self.previously_exposed != bool(self.exposure_paths):
            raise ValueError("exposure status must match exposure paths")
        if self.preflight_status != "pending" or self.eligibility != "pending":
            raise ValueError("inventory cannot contain preflight outcomes")
        return self


class CandidateInventory(_StrictModel):
    schema_version: str
    inventory_id: str
    lifecycle: str
    rules_canonical_sha256: str
    source_revision: str
    source_parquet_sha256: str
    exposure_registry_sha256: str
    candidate_count: int
    candidates: tuple[CandidateRecord, ...]
    eligible_pool_generated: bool
    task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool
    hypothesis_evidence_eligible: bool

    @model_validator(mode="after")
    def validate_inventory(self) -> "CandidateInventory":
        if (
            self.schema_version != "1.0"
            or self.lifecycle != "exploratory_candidate_inventory"
        ):
            raise ValueError("unsupported candidate inventory schema")
        if self.candidate_count != len(self.candidates):
            raise ValueError("candidate_count does not match candidates")
        identifiers = [candidate.instance_id for candidate in self.candidates]
        ranks = [candidate.selection_rank for candidate in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("candidate instance IDs must be unique")
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("candidate ranks must be contiguous and ordered")
        for digest in (
            self.rules_canonical_sha256,
            self.source_parquet_sha256,
            self.exposure_registry_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError("inventory hashes must be lowercase SHA-256")
        if (
            self.eligible_pool_generated
            or self.task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
            or self.hypothesis_evidence_eligible
        ):
            raise ValueError("candidate inventory cannot authorize outcomes")
        return self


class CandidateInventoryReceipt(_StrictModel):
    schema_version: str
    receipt_id: str
    rules_file_sha256: str
    rules_canonical_sha256: str
    preflight_file_sha256: str
    inventory_file_sha256: str
    source_parquet_sha256: str
    candidate_count: int
    repository_count: int
    previously_exposed_count: int
    hidden_fields_persisted: bool
    eligible_pool_generated: bool
    task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_inventory_receipt(self) -> "CandidateInventoryReceipt":
        if self.schema_version != "1.0" or not self.receipt_id.strip():
            raise ValueError("unsupported inventory receipt")
        for digest in (
            self.rules_file_sha256,
            self.rules_canonical_sha256,
            self.preflight_file_sha256,
            self.inventory_file_sha256,
            self.source_parquet_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError("receipt hashes must be lowercase SHA-256")
        if (
            self.candidate_count <= 0
            or self.repository_count <= 0
            or self.previously_exposed_count < 0
        ):
            raise ValueError("receipt counts are inconsistent")
        if (
            self.hidden_fields_persisted
            or self.eligible_pool_generated
            or self.task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError("candidate receipt cannot authorize outcomes")
        return self


class CandidateExclusionAttempt(_StrictModel):
    attempt_id: str
    mode: str
    runner_version: str
    result_sha256: str
    report_sha256: str
    build_log_sha256: str
    build_exit_code: int
    network_boundary_reached: bool
    network_disabled: bool
    completed: bool
    resolved: bool

    @model_validator(mode="after")
    def validate_attempt(self) -> "CandidateExclusionAttempt":
        if (
            not self.attempt_id.strip()
            or self.mode not in {"base", "gold"}
            or not self.runner_version.strip()
        ):
            raise ValueError("candidate exclusion attempt identity is invalid")
        for digest in (
            self.result_sha256,
            self.report_sha256,
            self.build_log_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError("attempt hashes must be lowercase SHA-256")
        if self.build_exit_code <= 0:
            raise ValueError("excluded build attempt must have a failure code")
        if self.network_disabled and not self.network_boundary_reached:
            raise ValueError(
                "network cannot be disabled before the evaluator boundary"
            )
        if self.completed or self.resolved:
            raise ValueError("build-failed attempt cannot complete or resolve")
        return self


class CandidateExclusionReceipt(_StrictModel):
    schema_version: str
    receipt_id: str
    rules_file_sha256: str
    rules_canonical_sha256: str
    host_preflight_file_sha256: str
    candidate_inventory_file_sha256: str
    instance_id: str
    repo: str
    base_commit: str
    problem_statement_sha256: str
    dataset_row_sha256: str
    harness_commit: str
    docker_runtime_fingerprint: str
    attempts: tuple[CandidateExclusionAttempt, ...]
    repeat_failure_agreement: bool
    eligibility: str
    exclusion_reason: str
    hidden_fields_persisted: bool
    task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_exclusion(self) -> "CandidateExclusionReceipt":
        if self.schema_version != "1.0" or not self.receipt_id.strip():
            raise ValueError("unsupported candidate exclusion receipt")
        for digest in (
            self.rules_file_sha256,
            self.rules_canonical_sha256,
            self.host_preflight_file_sha256,
            self.candidate_inventory_file_sha256,
            self.problem_statement_sha256,
            self.dataset_row_sha256,
            self.docker_runtime_fingerprint,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError(
                    "candidate exclusion hashes must be lowercase SHA-256"
                )
        if len(self.harness_commit) != 40 or any(
            character not in "0123456789abcdef"
            for character in self.harness_commit
        ):
            raise ValueError("candidate exclusion harness commit is invalid")
        if len(self.attempts) < 2 or not self.repeat_failure_agreement:
            raise ValueError("candidate exclusion requires repeated failure")
        if self.eligibility != "excluded":
            raise ValueError("candidate exclusion eligibility must be excluded")
        if (
            self.hidden_fields_persisted
            or self.task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError("candidate exclusion cannot expose or authorize")
        return self


class CandidateImageAcquisitionReceipt(_StrictModel):
    schema_version: str
    receipt_id: str
    rules_file_sha256: str
    rules_canonical_sha256: str
    host_preflight_file_sha256: str
    candidate_inventory_file_sha256: str
    instance_id: str
    selection_rank: int
    docker_runtime_fingerprint: str
    source_image_ref: str
    manifest_digest: str
    manifest_descriptor_bytes: int
    compressed_layer_bytes: int
    pinned_image_ref: str
    local_image_id: str
    repo_digests: tuple[str, ...]
    image_architecture: str
    image_os: str
    image_size_bytes: int
    acquisition_network_allowed: bool
    evaluator_network_disabled_required: bool
    evaluator_started: bool
    task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_image_acquisition(
        self,
    ) -> "CandidateImageAcquisitionReceipt":
        if self.schema_version != "1.0" or not self.receipt_id.strip():
            raise ValueError("unsupported candidate image acquisition receipt")
        for digest in (
            self.rules_file_sha256,
            self.rules_canonical_sha256,
            self.host_preflight_file_sha256,
            self.candidate_inventory_file_sha256,
            self.docker_runtime_fingerprint,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError(
                    "candidate image receipt hashes must be lowercase SHA-256"
                )
        for digest in (self.manifest_digest, self.local_image_id):
            if (
                not digest.startswith("sha256:")
                or len(digest) != 71
                or any(
                    character not in "0123456789abcdef"
                    for character in digest.removeprefix("sha256:")
                )
            ):
                raise ValueError("image identities must be full SHA-256")
        if min(
            self.selection_rank,
            self.manifest_descriptor_bytes,
            self.compressed_layer_bytes,
            self.image_size_bytes,
        ) <= 0:
            raise ValueError("candidate image receipt counts must be positive")
        if (
            not self.source_image_ref.startswith("swebench/")
            or not self.source_image_ref.endswith(":latest")
            or not self.pinned_image_ref.startswith("swebench/")
            or self.pinned_image_ref.endswith(":latest")
        ):
            raise ValueError("candidate image refs must be official and pinned")
        source_repository = self.source_image_ref.rsplit(":", 1)[0]
        pinned_repository = self.pinned_image_ref.rsplit(":", 1)[0]
        if source_repository != pinned_repository:
            raise ValueError("source and pinned image repositories must match")
        if f"{source_repository}@{self.manifest_digest}" not in self.repo_digests:
            raise ValueError("image repo digests must contain frozen manifest")
        if self.image_architecture != "amd64" or self.image_os != "linux":
            raise ValueError("official instance image platform must be linux/amd64")
        if (
            not self.acquisition_network_allowed
            or not self.evaluator_network_disabled_required
            or self.evaluator_started
            or self.task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError(
                "image acquisition cannot start or authorize evaluation"
            )
        return self


class CandidateExecutionAttempt(_StrictModel):
    attempt_id: str
    mode: str
    repetition: int
    runner_version: str
    result_sha256: str
    summary_report_sha256: str
    detailed_report_sha256: str
    test_output_sha256: str
    evaluator_seconds: float
    network_disabled: bool
    instance_image_ref: str
    instance_image_manifest_digest: str
    instance_image_id: str
    completed: bool
    resolved: bool
    fail_to_pass_success_count: int
    fail_to_pass_failure_count: int
    pass_to_pass_success_count: int
    pass_to_pass_failure_count: int

    @model_validator(mode="after")
    def validate_execution_attempt(self) -> "CandidateExecutionAttempt":
        if (
            not self.attempt_id.strip()
            or self.mode not in {"base", "gold"}
            or self.repetition not in {1, 2}
            or self.runner_version != "network-isolated-swebench-v4"
        ):
            raise ValueError("candidate execution attempt identity is invalid")
        for digest in (
            self.result_sha256,
            self.summary_report_sha256,
            self.detailed_report_sha256,
            self.test_output_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError("execution attempt hashes must be SHA-256")
        for digest in (
            self.instance_image_manifest_digest,
            self.instance_image_id,
        ):
            if (
                not digest.startswith("sha256:")
                or len(digest) != 71
                or any(
                    character not in "0123456789abcdef"
                    for character in digest.removeprefix("sha256:")
                )
            ):
                raise ValueError("execution image identities must be SHA-256")
        if (
            self.evaluator_seconds <= 0
            or not self.network_disabled
            or not self.completed
            or self.pass_to_pass_failure_count != 0
            or min(
                self.fail_to_pass_success_count,
                self.fail_to_pass_failure_count,
                self.pass_to_pass_success_count,
                self.pass_to_pass_failure_count,
            )
            < 0
        ):
            raise ValueError("candidate execution safety evidence is invalid")
        if self.mode == "base" and (
            self.resolved
            or self.fail_to_pass_failure_count <= 0
        ):
            raise ValueError("base attempt must reproduce designated failure")
        if self.mode == "gold" and (
            not self.resolved
            or self.fail_to_pass_success_count <= 0
            or self.fail_to_pass_failure_count != 0
        ):
            raise ValueError("gold attempt must resolve designated failure")
        return self


class CandidateExecutionPreflightReceipt(_StrictModel):
    schema_version: str
    receipt_id: str
    rules_file_sha256: str
    rules_canonical_sha256: str
    host_preflight_file_sha256: str
    candidate_inventory_file_sha256: str
    image_acquisition_receipt_sha256: str
    instance_id: str
    selection_rank: int
    attempts: tuple[CandidateExecutionAttempt, ...]
    base_repeat_agreement: bool
    gold_repeat_agreement: bool
    execution_preflight_passed: bool
    eligibility: str
    hidden_test_identities_persisted: bool
    agent_task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_execution_preflight(
        self,
    ) -> "CandidateExecutionPreflightReceipt":
        if self.schema_version != "1.0" or not self.receipt_id.strip():
            raise ValueError("unsupported candidate execution preflight")
        for digest in (
            self.rules_file_sha256,
            self.rules_canonical_sha256,
            self.host_preflight_file_sha256,
            self.candidate_inventory_file_sha256,
            self.image_acquisition_receipt_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError(
                    "execution preflight hashes must be lowercase SHA-256"
                )
        identities = tuple(
            (attempt.mode, attempt.repetition) for attempt in self.attempts
        )
        if identities != (
            ("base", 1),
            ("base", 2),
            ("gold", 1),
            ("gold", 2),
        ):
            raise ValueError("execution preflight requires ordered base/gold x2")
        if (
            self.selection_rank <= 0
            or not self.base_repeat_agreement
            or not self.gold_repeat_agreement
            or not self.execution_preflight_passed
            or self.eligibility != "pending_nonexecution_reviews"
            or self.hidden_test_identities_persisted
            or self.agent_task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError(
                "execution preflight cannot skip reviews or authorize outcomes"
            )
        return self


class CandidateBaseReproductionAttempt(_StrictModel):
    attempt_id: str
    repetition: int
    runner_version: str
    result_sha256: str
    summary_report_sha256: str
    detailed_report_sha256: str
    test_output_sha256: str
    evaluator_seconds: float
    network_disabled: bool
    instance_image_ref: str
    instance_image_manifest_digest: str
    instance_image_id: str
    completed: bool
    resolved: bool
    fail_to_pass_success_count: int
    fail_to_pass_failure_count: int
    pass_to_pass_success_count: int
    pass_to_pass_failure_count: int

    @model_validator(mode="after")
    def validate_base_reproduction_attempt(
        self,
    ) -> "CandidateBaseReproductionAttempt":
        if (
            not self.attempt_id.strip()
            or self.repetition not in {1, 2}
            or self.runner_version != "network-isolated-swebench-v4"
        ):
            raise ValueError("base reproduction attempt identity is invalid")
        for digest in (
            self.result_sha256,
            self.summary_report_sha256,
            self.detailed_report_sha256,
            self.test_output_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError("base reproduction hashes must be SHA-256")
        for digest in (
            self.instance_image_manifest_digest,
            self.instance_image_id,
        ):
            if (
                not digest.startswith("sha256:")
                or len(digest) != 71
                or any(
                    character not in "0123456789abcdef"
                    for character in digest.removeprefix("sha256:")
                )
            ):
                raise ValueError(
                    "base reproduction image identities must be SHA-256"
                )
        if (
            self.evaluator_seconds <= 0
            or not self.network_disabled
            or not self.completed
            or not self.resolved
            or self.fail_to_pass_success_count <= 0
            or self.fail_to_pass_failure_count != 0
            or self.pass_to_pass_failure_count != 0
            or self.pass_to_pass_success_count < 0
        ):
            raise ValueError(
                "resolved base attempt cannot reproduce designated failure"
            )
        return self


class CandidateBaseReproductionExclusionReceipt(_StrictModel):
    schema_version: str
    receipt_id: str
    rules_file_sha256: str
    rules_canonical_sha256: str
    host_preflight_file_sha256: str
    candidate_inventory_file_sha256: str
    image_acquisition_receipt_sha256: str
    instance_id: str
    selection_rank: int
    attempts: tuple[CandidateBaseReproductionAttempt, ...]
    repeat_outcome_agreement: bool
    exclusion_reason: str
    gold_evaluation_started: bool
    eligibility: str
    hidden_test_identities_persisted: bool
    agent_task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_base_reproduction_exclusion(
        self,
    ) -> "CandidateBaseReproductionExclusionReceipt":
        if self.schema_version != "1.0" or not self.receipt_id.strip():
            raise ValueError("unsupported base reproduction exclusion")
        for digest in (
            self.rules_file_sha256,
            self.rules_canonical_sha256,
            self.host_preflight_file_sha256,
            self.candidate_inventory_file_sha256,
            self.image_acquisition_receipt_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError(
                    "base reproduction exclusion hashes must be SHA-256"
                )
        if tuple(attempt.repetition for attempt in self.attempts) != (1, 2):
            raise ValueError("base reproduction exclusion requires two runs")
        if (
            self.selection_rank <= 0
            or not self.repeat_outcome_agreement
            or self.exclusion_reason
            != "base_failure_not_reproduced_twice"
            or self.gold_evaluation_started
            or self.eligibility != "excluded"
            or self.hidden_test_identities_persisted
            or self.agent_task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError(
                "base reproduction exclusion cannot authorize outcomes"
            )
        return self


class CandidateGoldVerificationAttempt(_StrictModel):
    attempt_id: str
    mode: str
    repetition: int
    runner_version: str
    result_sha256: str
    summary_report_sha256: str
    detailed_report_sha256: str
    test_output_sha256: str
    evaluator_seconds: float
    network_disabled: bool
    instance_image_ref: str
    instance_image_manifest_digest: str
    instance_image_id: str
    completed: bool
    resolved: bool
    fail_to_pass_success_count: int
    fail_to_pass_failure_count: int
    pass_to_pass_success_count: int
    pass_to_pass_failure_count: int

    @model_validator(mode="after")
    def validate_gold_verification_attempt(
        self,
    ) -> "CandidateGoldVerificationAttempt":
        if (
            not self.attempt_id.strip()
            or self.mode not in {"base", "gold"}
            or self.repetition not in {1, 2}
            or self.runner_version != "network-isolated-swebench-v4"
        ):
            raise ValueError("gold verification attempt identity is invalid")
        for digest in (
            self.result_sha256,
            self.summary_report_sha256,
            self.detailed_report_sha256,
            self.test_output_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError(
                    "gold verification hashes must be SHA-256"
                )
        for digest in (
            self.instance_image_manifest_digest,
            self.instance_image_id,
        ):
            if (
                not digest.startswith("sha256:")
                or len(digest) != 71
                or any(
                    character not in "0123456789abcdef"
                    for character in digest.removeprefix("sha256:")
                )
            ):
                raise ValueError(
                    "gold verification image identities must be SHA-256"
                )
        if (
            self.evaluator_seconds <= 0
            or not self.network_disabled
            or not self.completed
            or self.resolved
            or min(
                self.fail_to_pass_success_count,
                self.fail_to_pass_failure_count,
                self.pass_to_pass_success_count,
                self.pass_to_pass_failure_count,
            )
            < 0
        ):
            raise ValueError("gold verification safety evidence is invalid")
        if (
            self.mode == "base"
            and self.fail_to_pass_failure_count <= 0
        ):
            raise ValueError("base attempt must reproduce designated failure")
        if self.mode == "gold" and (
            self.fail_to_pass_failure_count == 0
            and self.pass_to_pass_failure_count == 0
        ):
            raise ValueError("gold attempt must retain an evaluator failure")
        return self


class CandidateGoldVerificationExclusionReceipt(_StrictModel):
    schema_version: str
    receipt_id: str
    rules_file_sha256: str
    rules_canonical_sha256: str
    host_preflight_file_sha256: str
    candidate_inventory_file_sha256: str
    image_acquisition_receipt_sha256: str
    instance_id: str
    selection_rank: int
    attempts: tuple[CandidateGoldVerificationAttempt, ...]
    repeat_outcome_agreement: bool
    exclusion_reason: str
    gold_evaluation_started: bool
    eligibility: str
    hidden_test_identities_persisted: bool
    agent_task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_gold_verification_exclusion(
        self,
    ) -> "CandidateGoldVerificationExclusionReceipt":
        if self.schema_version != "1.0" or not self.receipt_id.strip():
            raise ValueError("unsupported gold verification exclusion")
        for digest in (
            self.rules_file_sha256,
            self.rules_canonical_sha256,
            self.host_preflight_file_sha256,
            self.candidate_inventory_file_sha256,
            self.image_acquisition_receipt_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError(
                    "gold verification exclusion hashes must be SHA-256"
                )
        identities = tuple(
            (attempt.mode, attempt.repetition) for attempt in self.attempts
        )
        if identities != (
            ("base", 1),
            ("base", 2),
            ("gold", 1),
            ("gold", 2),
        ):
            raise ValueError(
                "gold verification exclusion requires ordered base/gold x2"
            )
        if (
            self.selection_rank <= 0
            or not self.repeat_outcome_agreement
            or self.exclusion_reason != "gold_patch_not_verified_twice"
            or not self.gold_evaluation_started
            or self.eligibility != "excluded"
            or self.hidden_test_identities_persisted
            or self.agent_task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError(
                "gold verification exclusion cannot authorize outcomes"
            )
        return self


class CandidateNonexecutionEvidenceReceipt(_StrictModel):
    schema_version: str
    receipt_id: str
    rules_file_sha256: str
    rules_canonical_sha256: str
    host_preflight_file_sha256: str
    candidate_inventory_file_sha256: str
    image_acquisition_receipt_sha256: str
    execution_preflight_receipt_sha256: str
    instance_id: str
    selection_rank: int
    repository_license_spdx: str
    license_evidence_sha256: str
    license_source_url: str
    license_local_research_evaluation_permitted: bool
    checkout_bytes: int
    maximum_checkout_bytes: int
    checkout_within_limit: bool
    production_files_changed: int
    test_files_changed: int
    production_path_set_sha256: str
    test_path_set_sha256: str
    previously_exposed: bool
    nonexecution_evidence_complete: bool
    required_independent_reviewer_count: int
    completed_independent_reviewer_count: int
    review_status: str
    eligibility: str
    hidden_patch_paths_persisted: bool
    hidden_test_identities_persisted: bool
    agent_task_outcomes_generated: bool
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_nonexecution_evidence(
        self,
    ) -> "CandidateNonexecutionEvidenceReceipt":
        if self.schema_version != "1.0" or not self.receipt_id.strip():
            raise ValueError("unsupported candidate nonexecution evidence")
        for digest in (
            self.rules_file_sha256,
            self.rules_canonical_sha256,
            self.host_preflight_file_sha256,
            self.candidate_inventory_file_sha256,
            self.image_acquisition_receipt_sha256,
            self.execution_preflight_receipt_sha256,
            self.license_evidence_sha256,
            self.production_path_set_sha256,
            self.test_path_set_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError(
                    "nonexecution evidence hashes must be lowercase SHA-256"
                )
        if (
            self.selection_rank <= 0
            or not self.repository_license_spdx.strip()
            or not self.license_source_url.startswith("https://")
            or not self.license_local_research_evaluation_permitted
        ):
            raise ValueError("nonexecution license permission is invalid")
        if (
            self.checkout_bytes <= 0
            or self.maximum_checkout_bytes <= 0
            or self.checkout_within_limit
            != (self.checkout_bytes <= self.maximum_checkout_bytes)
            or not self.checkout_within_limit
        ):
            raise ValueError("nonexecution checkout evidence is invalid")
        if self.production_files_changed <= 0 or self.test_files_changed <= 0:
            raise ValueError("nonexecution patch counts must be positive")
        if (
            self.previously_exposed
            or not self.nonexecution_evidence_complete
            or self.required_independent_reviewer_count != 2
            or self.completed_independent_reviewer_count != 0
            or self.review_status
            != "pending_independent_stratum_reviews"
            or self.eligibility != "pending_stratum_reviews"
            or self.hidden_patch_paths_persisted
            or self.hidden_test_identities_persisted
            or self.agent_task_outcomes_generated
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError(
                "nonexecution evidence cannot skip review or authorize outcomes"
            )
        return self


class CandidateStratumReviewDecision(_StrictModel):
    schema_version: str
    decision_id: str
    rules_file_sha256: str
    rules_canonical_sha256: str
    nonexecution_receipt_sha256: str
    instance_id: str
    selection_rank: int
    reviewer_identity_sha256: str
    proposed_stratum: str
    private_rationale_sha256: str
    review_count_contributed: int
    required_independent_reviewer_count: int
    blind_to_future_arm_outcomes: bool
    agent_arm_outcomes_available: bool
    hidden_review_details_persisted: bool
    candidate_eligibility_unchanged: bool
    eligibility: str
    provider_execution_authorized: bool
    production_execution_authorized: bool

    @model_validator(mode="after")
    def validate_stratum_review(self) -> "CandidateStratumReviewDecision":
        if self.schema_version != "1.0" or not self.decision_id.strip():
            raise ValueError("unsupported candidate stratum review")
        for digest in (
            self.rules_file_sha256,
            self.rules_canonical_sha256,
            self.nonexecution_receipt_sha256,
            self.reviewer_identity_sha256,
            self.private_rationale_sha256,
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef"
                for character in digest
            ):
                raise ValueError("stratum review hashes must be SHA-256")
        if (
            self.selection_rank <= 0
            or not self.instance_id.strip()
            or not self.proposed_stratum.strip()
            or self.review_count_contributed != 1
            or self.required_independent_reviewer_count != 2
            or not self.blind_to_future_arm_outcomes
            or self.agent_arm_outcomes_available
            or self.hidden_review_details_persisted
            or not self.candidate_eligibility_unchanged
            or self.eligibility != "pending_stratum_reviews"
            or self.provider_execution_authorized
            or self.production_execution_authorized
        ):
            raise ValueError(
                "one stratum review cannot alter candidate eligibility"
            )
        return self


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def build_acquisition_host_preflight_receipt_v2(
    *,
    rules: ExploratoryAcquisitionRules,
    rules_file_sha256: str,
    source_parquet_sha256: str,
    source_parquet_bytes: int,
    source_row_count: int,
    unique_instance_id_count: int,
    repository_count: int,
    source_columns: tuple[str, ...],
    host_facts: AcquisitionHostFacts,
    receipt_id: str = "mini-swe-exploratory-host-preflight-v2",
) -> AcquisitionHostPreflightReceiptV2:
    if rules.source.harness_revision is None:
        raise ValueError("V2 host preflight requires a pinned harness revision")
    required_source_columns = {
        "repo",
        "instance_id",
        "base_commit",
        "problem_statement",
        "patch",
        "test_patch",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
    }
    source_gate_passed = (
        source_row_count == rules.source.expected_row_count
        and unique_instance_id_count == source_row_count
        and repository_count > 0
        and required_source_columns.issubset(source_columns)
    )
    required_storage_bytes = 120 * 1024**3
    blockers: list[str] = []
    if not source_gate_passed:
        blockers.append("source_audit_failed")
    if host_facts.available_storage_bytes < required_storage_bytes:
        blockers.append("available_storage_below_120_gib")
    if (
        host_facts.docker_virtual_disk_bytes is not None
        and host_facts.docker_virtual_disk_bytes < required_storage_bytes
    ):
        blockers.append("docker_virtual_disk_below_120_gib")
    if not host_facts.docker_daemon_available:
        blockers.append("docker_daemon_unavailable")
    if not host_facts.docker_smoke_passed:
        blockers.append("docker_smoke_failed")
    if (
        rules.preflight.minimum_docker_memory_bytes is not None
        and host_facts.docker_memory_bytes
        < rules.preflight.minimum_docker_memory_bytes
    ):
        blockers.append("docker_memory_below_frozen_minimum")
    if host_facts.harness_commit != rules.source.harness_revision:
        blockers.append("harness_commit_mismatch")
    if not host_facts.harness_clean:
        blockers.append("harness_worktree_not_clean")
    if not host_facts.harness_import_passed:
        blockers.append("harness_import_failed")
    if not host_facts.harness_cli_passed:
        blockers.append("harness_cli_failed")

    warnings: list[str] = []
    if host_facts.host_architecture in {"arm64", "aarch64"}:
        warnings.append("arm64_harness_support_experimental")
    if host_facts.physical_memory_bytes < 16 * 1024**3:
        warnings.append("physical_memory_below_16_gib_recommendation")
    if host_facts.docker_memory_bytes < 8 * 1024**3:
        warnings.append("docker_memory_below_8_gib_recommendation")

    return AcquisitionHostPreflightReceiptV2(
        schema_version="2.0",
        receipt_id=receipt_id,
        rules_file_sha256=rules_file_sha256,
        rules_canonical_sha256=rules.canonical_sha256(),
        source_revision=rules.source.revision,
        source_parquet_sha256=source_parquet_sha256,
        source_parquet_bytes=source_parquet_bytes,
        source_row_count=source_row_count,
        unique_instance_id_count=unique_instance_id_count,
        repository_count=repository_count,
        source_columns=source_columns,
        source_gate_passed=source_gate_passed,
        required_storage_bytes=required_storage_bytes,
        host_facts=host_facts,
        docker_runtime_fingerprint=_canonical_sha256(
            host_facts.model_dump(mode="json")
        ),
        warnings=tuple(warnings),
        blockers=tuple(blockers),
        host_resource_gate_passed=source_gate_passed and not blockers,
        eligible_pool_generated=False,
        exploratory_protocol_frozen=False,
        task_outcomes_generated=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )


def build_candidate_inventory(
    *,
    records: Any,
    rules: ExploratoryAcquisitionRules,
    source_parquet_sha256: str,
    exposure_documents: dict[str, bytes],
    inventory_id: str = "mini-swe-exploratory-candidate-inventory-v1",
) -> CandidateInventory:
    rows = tuple(dict(record) for record in records)
    if not rows:
        raise ValueError("candidate source records must not be empty")
    identifiers = tuple(row.get("instance_id") for row in rows)
    ranked_identifiers = rules.rank_instance_ids(identifiers)
    rank_by_identifier = {
        instance_id: rank
        for rank, instance_id in enumerate(ranked_identifiers, start=1)
    }
    exposure_hashes = {
        path: hashlib.sha256(content).hexdigest()
        for path, content in sorted(exposure_documents.items())
    }
    exposure_registry_sha256 = _canonical_sha256(exposure_hashes)
    candidates: list[CandidateRecord] = []
    for row in rows:
        required = (
            "instance_id",
            "repo",
            "base_commit",
            "problem_statement",
        )
        if any(key not in row for key in required):
            raise ValueError("candidate row is missing required source fields")
        instance_id = str(row["instance_id"])
        matching_paths = tuple(
            path
            for path, content in sorted(exposure_documents.items())
            if instance_id.encode() in content
        )
        candidates.append(
            CandidateRecord(
                instance_id=instance_id,
                repo=str(row["repo"]),
                base_commit=str(row["base_commit"]),
                environment_setup_commit=str(
                    row.get("environment_setup_commit") or ""
                ),
                difficulty=str(row.get("difficulty") or ""),
                created_at=str(row.get("created_at") or ""),
                problem_statement_sha256=hashlib.sha256(
                    str(row["problem_statement"]).encode()
                ).hexdigest(),
                dataset_row_sha256=_canonical_sha256(row),
                selection_rank=rank_by_identifier[instance_id],
                previously_exposed=bool(matching_paths),
                exposure_paths=matching_paths,
                preflight_status="pending",
                eligibility="pending",
            )
        )
    candidates.sort(key=lambda candidate: candidate.selection_rank)
    return CandidateInventory(
        schema_version="1.0",
        inventory_id=inventory_id,
        lifecycle="exploratory_candidate_inventory",
        rules_canonical_sha256=rules.canonical_sha256(),
        source_revision=rules.source.revision,
        source_parquet_sha256=source_parquet_sha256,
        exposure_registry_sha256=exposure_registry_sha256,
        candidate_count=len(candidates),
        candidates=tuple(candidates),
        eligible_pool_generated=False,
        task_outcomes_generated=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
        hypothesis_evidence_eligible=False,
    )


def build_candidate_inventory_receipt(
    *,
    receipt_id: str,
    rules_path: Path,
    preflight_path: Path,
    inventory_path: Path,
) -> CandidateInventoryReceipt:
    rules = load_exploratory_acquisition_rules(rules_path)
    preflight_payload = json.loads(
        preflight_path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if preflight_payload.get("schema_version") == "2.0":
        preflight = load_acquisition_host_preflight_receipt_v2(
            preflight_path,
            rules_path=rules_path,
        )
        if not preflight.host_resource_gate_passed:
            raise ValueError("candidate inventory requires a passed host gate")
        source_parquet_sha256 = preflight.source_parquet_sha256
    else:
        preflight = load_acquisition_preflight_receipt(
            preflight_path,
            rules=rules,
        )
        source_parquet_sha256 = preflight.source_parquet_sha256
    inventory = load_candidate_inventory(
        inventory_path,
        rules=rules,
        source_parquet_sha256=source_parquet_sha256,
    )
    return CandidateInventoryReceipt(
        schema_version="1.0",
        receipt_id=receipt_id,
        rules_file_sha256=hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        rules_canonical_sha256=rules.canonical_sha256(),
        preflight_file_sha256=hashlib.sha256(
            preflight_path.read_bytes()
        ).hexdigest(),
        inventory_file_sha256=hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        source_parquet_sha256=source_parquet_sha256,
        candidate_count=inventory.candidate_count,
        repository_count=len(
            {candidate.repo for candidate in inventory.candidates}
        ),
        previously_exposed_count=sum(
            candidate.previously_exposed
            for candidate in inventory.candidates
        ),
        hidden_fields_persisted=False,
        eligible_pool_generated=False,
        task_outcomes_generated=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )


def build_candidate_exclusion_receipt(
    *,
    receipt_id: str,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    instance_id: str,
    attempts: tuple[CandidateExclusionAttempt, ...],
    exclusion_reason: str,
) -> CandidateExclusionReceipt:
    rules = load_exploratory_acquisition_rules(rules_path)
    if exclusion_reason not in rules.exclusion_reasons:
        raise ValueError("candidate exclusion reason is not preregistered")
    preflight = load_acquisition_host_preflight_receipt_v2(
        host_preflight_path,
        rules_path=rules_path,
    )
    if not preflight.host_resource_gate_passed:
        raise ValueError("candidate exclusion requires a passed host gate")
    inventory = load_candidate_inventory(
        inventory_path,
        rules=rules,
        source_parquet_sha256=preflight.source_parquet_sha256,
    )
    matching = tuple(
        candidate
        for candidate in inventory.candidates
        if candidate.instance_id == instance_id
    )
    if len(matching) != 1:
        raise ValueError("candidate exclusion identity is not unique")
    candidate = matching[0]
    repeat_failure_agreement = (
        len(attempts) >= 2
        and len({attempt.mode for attempt in attempts}) == 1
        and len({attempt.build_exit_code for attempt in attempts}) == 1
        and len({attempt.report_sha256 for attempt in attempts}) == 1
        and all(not attempt.completed for attempt in attempts)
    )
    return CandidateExclusionReceipt(
        schema_version="1.0",
        receipt_id=receipt_id,
        rules_file_sha256=hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        rules_canonical_sha256=rules.canonical_sha256(),
        host_preflight_file_sha256=hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        candidate_inventory_file_sha256=hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        instance_id=candidate.instance_id,
        repo=candidate.repo,
        base_commit=candidate.base_commit,
        problem_statement_sha256=candidate.problem_statement_sha256,
        dataset_row_sha256=candidate.dataset_row_sha256,
        harness_commit=preflight.host_facts.harness_commit,
        docker_runtime_fingerprint=preflight.docker_runtime_fingerprint,
        attempts=attempts,
        repeat_failure_agreement=repeat_failure_agreement,
        eligibility="excluded",
        exclusion_reason=exclusion_reason,
        hidden_fields_persisted=False,
        task_outcomes_generated=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )


def build_candidate_image_acquisition_receipt(
    *,
    receipt_id: str,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    instance_id: str,
    manifest_digest: str,
    manifest_descriptor_bytes: int,
    compressed_layer_bytes: int,
    local_image_id: str,
    repo_digests: tuple[str, ...],
    image_architecture: str,
    image_os: str,
    image_size_bytes: int,
) -> CandidateImageAcquisitionReceipt:
    rules = load_exploratory_acquisition_rules(rules_path)
    if (
        rules.preflight.instance_image_source
        != "official_swebench_registry_pinned_digest"
    ):
        raise ValueError(
            "candidate image acquisition requires official image rules"
        )
    preflight = load_acquisition_host_preflight_receipt_v2(
        host_preflight_path,
        rules_path=rules_path,
    )
    if not preflight.host_resource_gate_passed:
        raise ValueError(
            "candidate image acquisition requires a passed host gate"
        )
    inventory = load_candidate_inventory(
        inventory_path,
        rules=rules,
        source_parquet_sha256=preflight.source_parquet_sha256,
    )
    matching = tuple(
        candidate
        for candidate in inventory.candidates
        if candidate.instance_id == instance_id
    )
    if len(matching) != 1:
        raise ValueError("candidate image identity is not unique")
    candidate = matching[0]
    repository = (
        "swebench/sweb.eval.x86_64."
        f"{instance_id.lower().replace('__', '_1776_')}"
    )
    source_image_ref = f"{repository}:latest"
    pinned_image_ref = (
        f"{repository}:acq-v4-"
        f"{manifest_digest.removeprefix('sha256:')[:12]}"
    )
    return CandidateImageAcquisitionReceipt(
        schema_version="1.0",
        receipt_id=receipt_id,
        rules_file_sha256=hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        rules_canonical_sha256=rules.canonical_sha256(),
        host_preflight_file_sha256=hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        candidate_inventory_file_sha256=hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        instance_id=candidate.instance_id,
        selection_rank=candidate.selection_rank,
        docker_runtime_fingerprint=preflight.docker_runtime_fingerprint,
        source_image_ref=source_image_ref,
        manifest_digest=manifest_digest,
        manifest_descriptor_bytes=manifest_descriptor_bytes,
        compressed_layer_bytes=compressed_layer_bytes,
        pinned_image_ref=pinned_image_ref,
        local_image_id=local_image_id,
        repo_digests=repo_digests,
        image_architecture=image_architecture,
        image_os=image_os,
        image_size_bytes=image_size_bytes,
        acquisition_network_allowed=True,
        evaluator_network_disabled_required=True,
        evaluator_started=False,
        task_outcomes_generated=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )


def build_candidate_execution_preflight_receipt(
    *,
    receipt_id: str,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    instance_id: str,
    attempts: tuple[CandidateExecutionAttempt, ...],
) -> CandidateExecutionPreflightReceipt:
    rules = load_exploratory_acquisition_rules(rules_path)
    preflight = load_acquisition_host_preflight_receipt_v2(
        host_preflight_path,
        rules_path=rules_path,
    )
    if not preflight.host_resource_gate_passed:
        raise ValueError("execution preflight requires a passed host gate")
    inventory = load_candidate_inventory(
        inventory_path,
        rules=rules,
        source_parquet_sha256=preflight.source_parquet_sha256,
    )
    matching = tuple(
        candidate
        for candidate in inventory.candidates
        if candidate.instance_id == instance_id
    )
    if len(matching) != 1:
        raise ValueError("execution preflight candidate identity is not unique")
    candidate = matching[0]
    image_receipt = load_candidate_image_acquisition_receipt(
        image_receipt_path,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
    )
    if image_receipt.instance_id != instance_id:
        raise ValueError("execution preflight image candidate drifted")
    if any(
        attempt.evaluator_seconds
        > rules.preflight.maximum_evaluator_seconds
        for attempt in attempts
    ):
        raise ValueError("candidate evaluator exceeded frozen timeout")

    def repeat_agreement(mode: str) -> bool:
        selected = tuple(
            attempt for attempt in attempts if attempt.mode == mode
        )
        fields = (
            "summary_report_sha256",
            "detailed_report_sha256",
            "network_disabled",
            "instance_image_ref",
            "instance_image_manifest_digest",
            "instance_image_id",
            "completed",
            "resolved",
            "fail_to_pass_success_count",
            "fail_to_pass_failure_count",
            "pass_to_pass_success_count",
            "pass_to_pass_failure_count",
        )
        return len(selected) == 2 and all(
            len({getattr(attempt, field) for attempt in selected}) == 1
            for field in fields
        )

    if any(
        (
            attempt.instance_image_ref != image_receipt.pinned_image_ref
            or attempt.instance_image_manifest_digest
            != image_receipt.manifest_digest
            or attempt.instance_image_id != image_receipt.local_image_id
        )
        for attempt in attempts
    ):
        raise ValueError("execution attempt image binding drifted")
    base_repeat_agreement = repeat_agreement("base")
    gold_repeat_agreement = repeat_agreement("gold")
    return CandidateExecutionPreflightReceipt(
        schema_version="1.0",
        receipt_id=receipt_id,
        rules_file_sha256=hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        rules_canonical_sha256=rules.canonical_sha256(),
        host_preflight_file_sha256=hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        candidate_inventory_file_sha256=hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        image_acquisition_receipt_sha256=hashlib.sha256(
            image_receipt_path.read_bytes()
        ).hexdigest(),
        instance_id=candidate.instance_id,
        selection_rank=candidate.selection_rank,
        attempts=attempts,
        base_repeat_agreement=base_repeat_agreement,
        gold_repeat_agreement=gold_repeat_agreement,
        execution_preflight_passed=(
            base_repeat_agreement and gold_repeat_agreement
        ),
        eligibility="pending_nonexecution_reviews",
        hidden_test_identities_persisted=False,
        agent_task_outcomes_generated=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )


def build_candidate_base_reproduction_exclusion_receipt(
    *,
    receipt_id: str,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    instance_id: str,
    attempts: tuple[CandidateBaseReproductionAttempt, ...],
) -> CandidateBaseReproductionExclusionReceipt:
    rules = load_exploratory_acquisition_rules(rules_path)
    if "base_failure_not_reproduced_twice" not in rules.exclusion_reasons:
        raise ValueError("base reproduction exclusion is not preregistered")
    preflight = load_acquisition_host_preflight_receipt_v2(
        host_preflight_path,
        rules_path=rules_path,
    )
    if not preflight.host_resource_gate_passed:
        raise ValueError(
            "base reproduction exclusion requires a passed host gate"
        )
    inventory = load_candidate_inventory(
        inventory_path,
        rules=rules,
        source_parquet_sha256=preflight.source_parquet_sha256,
    )
    matching = tuple(
        candidate
        for candidate in inventory.candidates
        if candidate.instance_id == instance_id
    )
    if len(matching) != 1:
        raise ValueError(
            "base reproduction candidate identity is not unique"
        )
    candidate = matching[0]
    image_receipt = load_candidate_image_acquisition_receipt(
        image_receipt_path,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
    )
    if image_receipt.instance_id != instance_id:
        raise ValueError("base reproduction image candidate drifted")
    if any(
        (
            attempt.evaluator_seconds
            > rules.preflight.maximum_evaluator_seconds
            or attempt.instance_image_ref != image_receipt.pinned_image_ref
            or attempt.instance_image_manifest_digest
            != image_receipt.manifest_digest
            or attempt.instance_image_id != image_receipt.local_image_id
        )
        for attempt in attempts
    ):
        raise ValueError("base reproduction attempt binding drifted")
    agreement_fields = (
        "summary_report_sha256",
        "detailed_report_sha256",
        "network_disabled",
        "instance_image_ref",
        "instance_image_manifest_digest",
        "instance_image_id",
        "completed",
        "resolved",
        "fail_to_pass_success_count",
        "fail_to_pass_failure_count",
        "pass_to_pass_success_count",
        "pass_to_pass_failure_count",
    )
    repeat_outcome_agreement = len(attempts) == 2 and all(
        len({getattr(attempt, field) for attempt in attempts}) == 1
        for field in agreement_fields
    )
    return CandidateBaseReproductionExclusionReceipt(
        schema_version="1.0",
        receipt_id=receipt_id,
        rules_file_sha256=hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        rules_canonical_sha256=rules.canonical_sha256(),
        host_preflight_file_sha256=hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        candidate_inventory_file_sha256=hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        image_acquisition_receipt_sha256=hashlib.sha256(
            image_receipt_path.read_bytes()
        ).hexdigest(),
        instance_id=candidate.instance_id,
        selection_rank=candidate.selection_rank,
        attempts=attempts,
        repeat_outcome_agreement=repeat_outcome_agreement,
        exclusion_reason="base_failure_not_reproduced_twice",
        gold_evaluation_started=False,
        eligibility="excluded",
        hidden_test_identities_persisted=False,
        agent_task_outcomes_generated=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )


def build_candidate_gold_verification_exclusion_receipt(
    *,
    receipt_id: str,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    instance_id: str,
    attempts: tuple[CandidateGoldVerificationAttempt, ...],
) -> CandidateGoldVerificationExclusionReceipt:
    rules = load_exploratory_acquisition_rules(rules_path)
    if "gold_patch_not_verified_twice" not in rules.exclusion_reasons:
        raise ValueError("gold verification exclusion is not preregistered")
    preflight = load_acquisition_host_preflight_receipt_v2(
        host_preflight_path,
        rules_path=rules_path,
    )
    if not preflight.host_resource_gate_passed:
        raise ValueError(
            "gold verification exclusion requires a passed host gate"
        )
    inventory = load_candidate_inventory(
        inventory_path,
        rules=rules,
        source_parquet_sha256=preflight.source_parquet_sha256,
    )
    matching = tuple(
        candidate
        for candidate in inventory.candidates
        if candidate.instance_id == instance_id
    )
    if len(matching) != 1:
        raise ValueError("gold verification candidate identity is not unique")
    candidate = matching[0]
    image_receipt = load_candidate_image_acquisition_receipt(
        image_receipt_path,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
    )
    if image_receipt.instance_id != instance_id:
        raise ValueError("gold verification image candidate drifted")
    if any(
        (
            attempt.evaluator_seconds
            > rules.preflight.maximum_evaluator_seconds
            or attempt.instance_image_ref != image_receipt.pinned_image_ref
            or attempt.instance_image_manifest_digest
            != image_receipt.manifest_digest
            or attempt.instance_image_id != image_receipt.local_image_id
        )
        for attempt in attempts
    ):
        raise ValueError("gold verification attempt binding drifted")
    agreement_fields = (
        "summary_report_sha256",
        "detailed_report_sha256",
        "network_disabled",
        "instance_image_ref",
        "instance_image_manifest_digest",
        "instance_image_id",
        "completed",
        "resolved",
        "fail_to_pass_success_count",
        "fail_to_pass_failure_count",
        "pass_to_pass_success_count",
        "pass_to_pass_failure_count",
    )
    pairs = (attempts[:2], attempts[2:])
    repeat_outcome_agreement = len(attempts) == 4 and all(
        len(pair) == 2
        and all(
            len({getattr(attempt, field) for attempt in pair}) == 1
            for field in agreement_fields
        )
        for pair in pairs
    )
    return CandidateGoldVerificationExclusionReceipt(
        schema_version="1.0",
        receipt_id=receipt_id,
        rules_file_sha256=hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        rules_canonical_sha256=rules.canonical_sha256(),
        host_preflight_file_sha256=hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        candidate_inventory_file_sha256=hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        image_acquisition_receipt_sha256=hashlib.sha256(
            image_receipt_path.read_bytes()
        ).hexdigest(),
        instance_id=candidate.instance_id,
        selection_rank=candidate.selection_rank,
        attempts=attempts,
        repeat_outcome_agreement=repeat_outcome_agreement,
        exclusion_reason="gold_patch_not_verified_twice",
        gold_evaluation_started=True,
        eligibility="excluded",
        hidden_test_identities_persisted=False,
        agent_task_outcomes_generated=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )


def build_candidate_nonexecution_evidence_receipt(
    *,
    receipt_id: str,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    execution_receipt_path: Path,
    instance_id: str,
    repository_license_spdx: str,
    license_evidence_sha256: str,
    license_source_url: str,
    license_local_research_evaluation_permitted: bool,
    checkout_bytes: int,
    production_files_changed: int,
    test_files_changed: int,
    production_path_set_sha256: str,
    test_path_set_sha256: str,
) -> CandidateNonexecutionEvidenceReceipt:
    rules = load_exploratory_acquisition_rules(rules_path)
    preflight = load_acquisition_host_preflight_receipt_v2(
        host_preflight_path,
        rules_path=rules_path,
    )
    if not preflight.host_resource_gate_passed:
        raise ValueError("nonexecution evidence requires a passed host gate")
    inventory = load_candidate_inventory(
        inventory_path,
        rules=rules,
        source_parquet_sha256=preflight.source_parquet_sha256,
    )
    matching = tuple(
        candidate
        for candidate in inventory.candidates
        if candidate.instance_id == instance_id
    )
    if len(matching) != 1:
        raise ValueError(
            "nonexecution evidence candidate identity is not unique"
        )
    candidate = matching[0]
    image_receipt = load_candidate_image_acquisition_receipt(
        image_receipt_path,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
    )
    execution_receipt = load_candidate_execution_preflight_receipt(
        execution_receipt_path,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        image_receipt_path=image_receipt_path,
    )
    if (
        image_receipt.instance_id != instance_id
        or execution_receipt.instance_id != instance_id
        or not execution_receipt.execution_preflight_passed
    ):
        raise ValueError("nonexecution evidence candidate binding drifted")
    if not license_local_research_evaluation_permitted:
        raise ValueError("nonexecution license permission is required")
    if checkout_bytes > rules.preflight.maximum_checkout_bytes:
        raise ValueError("candidate checkout exceeded frozen limit")
    return CandidateNonexecutionEvidenceReceipt(
        schema_version="1.0",
        receipt_id=receipt_id,
        rules_file_sha256=hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        rules_canonical_sha256=rules.canonical_sha256(),
        host_preflight_file_sha256=hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        candidate_inventory_file_sha256=hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        image_acquisition_receipt_sha256=hashlib.sha256(
            image_receipt_path.read_bytes()
        ).hexdigest(),
        execution_preflight_receipt_sha256=hashlib.sha256(
            execution_receipt_path.read_bytes()
        ).hexdigest(),
        instance_id=candidate.instance_id,
        selection_rank=candidate.selection_rank,
        repository_license_spdx=repository_license_spdx,
        license_evidence_sha256=license_evidence_sha256,
        license_source_url=license_source_url,
        license_local_research_evaluation_permitted=(
            license_local_research_evaluation_permitted
        ),
        checkout_bytes=checkout_bytes,
        maximum_checkout_bytes=rules.preflight.maximum_checkout_bytes,
        checkout_within_limit=(
            checkout_bytes <= rules.preflight.maximum_checkout_bytes
        ),
        production_files_changed=production_files_changed,
        test_files_changed=test_files_changed,
        production_path_set_sha256=production_path_set_sha256,
        test_path_set_sha256=test_path_set_sha256,
        previously_exposed=candidate.previously_exposed,
        nonexecution_evidence_complete=True,
        required_independent_reviewer_count=(
            rules.review_policy.independent_reviewer_count
        ),
        completed_independent_reviewer_count=0,
        review_status="pending_independent_stratum_reviews",
        eligibility="pending_stratum_reviews",
        hidden_patch_paths_persisted=False,
        hidden_test_identities_persisted=False,
        agent_task_outcomes_generated=False,
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )


def build_candidate_stratum_review_decision(
    *,
    decision_id: str,
    rules_path: Path,
    nonexecution_receipt_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    execution_receipt_path: Path,
    instance_id: str,
    reviewer_identity_sha256: str,
    proposed_stratum: str,
    private_rationale_path: Path,
) -> CandidateStratumReviewDecision:
    rules = load_exploratory_acquisition_rules(rules_path)
    nonexecution = load_candidate_nonexecution_evidence_receipt(
        nonexecution_receipt_path,
        rules_path=rules_path,
        host_preflight_path=host_preflight_path,
        inventory_path=inventory_path,
        image_receipt_path=image_receipt_path,
        execution_receipt_path=execution_receipt_path,
    )
    if (
        nonexecution.instance_id != instance_id
        or nonexecution.eligibility != "pending_stratum_reviews"
    ):
        raise ValueError("stratum review candidate binding drifted")
    if proposed_stratum not in rules.stratum_definitions:
        raise ValueError("stratum review proposed an unknown stratum")
    if (
        private_rationale_path.is_symlink()
        or not private_rationale_path.is_file()
        or not private_rationale_path.read_text().strip()
    ):
        raise ValueError("private stratum rationale must be a nonempty file")
    return CandidateStratumReviewDecision(
        schema_version="1.0",
        decision_id=decision_id,
        rules_file_sha256=hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        rules_canonical_sha256=rules.canonical_sha256(),
        nonexecution_receipt_sha256=hashlib.sha256(
            nonexecution_receipt_path.read_bytes()
        ).hexdigest(),
        instance_id=instance_id,
        selection_rank=nonexecution.selection_rank,
        reviewer_identity_sha256=reviewer_identity_sha256,
        proposed_stratum=proposed_stratum,
        private_rationale_sha256=hashlib.sha256(
            private_rationale_path.read_bytes()
        ).hexdigest(),
        review_count_contributed=1,
        required_independent_reviewer_count=(
            rules.review_policy.independent_reviewer_count
        ),
        blind_to_future_arm_outcomes=True,
        agent_arm_outcomes_available=False,
        hidden_review_details_persisted=False,
        candidate_eligibility_unchanged=True,
        eligibility="pending_stratum_reviews",
        provider_execution_authorized=False,
        production_execution_authorized=False,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_exploratory_acquisition_rules(
    path: Path,
) -> ExploratoryAcquisitionRules:
    if path.is_symlink() or not path.is_file():
        raise ValueError("acquisition rules must be a regular non-symlink file")
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    return ExploratoryAcquisitionRules.model_validate(payload)


def load_acquisition_preflight_receipt(
    path: Path,
    *,
    rules: ExploratoryAcquisitionRules,
) -> AcquisitionPreflightReceipt:
    if path.is_symlink() or not path.is_file():
        raise ValueError("preflight receipt must be a regular non-symlink file")
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    receipt = AcquisitionPreflightReceipt.model_validate(payload)
    if receipt.rules_canonical_sha256 != rules.canonical_sha256():
        raise ValueError("preflight rules binding drifted")
    if receipt.source_revision != rules.source.revision:
        raise ValueError("preflight source revision drifted")
    if receipt.source_row_count != rules.source.expected_row_count:
        raise ValueError("preflight source row count drifted")
    return receipt


def write_candidate_inventory(
    *,
    inventory: CandidateInventory,
    output_path: Path,
) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite candidate inventory: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                inventory.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
        )
        stream.write("\n")


def write_candidate_inventory_receipt(
    *,
    receipt: CandidateInventoryReceipt,
    output_path: Path,
) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite candidate receipt: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                receipt.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
        )
        stream.write("\n")


def write_candidate_exclusion_receipt(
    *,
    receipt: CandidateExclusionReceipt,
    output_path: Path,
) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite candidate exclusion: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                receipt.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
        )
        stream.write("\n")


def write_candidate_image_acquisition_receipt(
    *,
    receipt: CandidateImageAcquisitionReceipt,
    output_path: Path,
) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite candidate image receipt: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                receipt.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
        )
        stream.write("\n")


def write_candidate_execution_preflight_receipt(
    *,
    receipt: CandidateExecutionPreflightReceipt,
    output_path: Path,
) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite execution preflight: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                receipt.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
        )
        stream.write("\n")


def write_candidate_base_reproduction_exclusion_receipt(
    *,
    receipt: CandidateBaseReproductionExclusionReceipt,
    output_path: Path,
) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite base reproduction exclusion: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                receipt.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
        )
        stream.write("\n")


def write_candidate_gold_verification_exclusion_receipt(
    *,
    receipt: CandidateGoldVerificationExclusionReceipt,
    output_path: Path,
) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite gold verification exclusion: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                receipt.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
        )
        stream.write("\n")


def write_candidate_nonexecution_evidence_receipt(
    *,
    receipt: CandidateNonexecutionEvidenceReceipt,
    output_path: Path,
) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite nonexecution evidence: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                receipt.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
        )
        stream.write("\n")


def write_candidate_stratum_review_decision(
    *,
    decision: CandidateStratumReviewDecision,
    output_path: Path,
) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite stratum review: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                decision.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
        )
        stream.write("\n")


def load_candidate_inventory(
    path: Path,
    *,
    rules: ExploratoryAcquisitionRules,
    source_parquet_sha256: str,
) -> CandidateInventory:
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "candidate inventory must be a regular non-symlink file"
        )
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    inventory = CandidateInventory.model_validate(payload)
    if inventory.rules_canonical_sha256 != rules.canonical_sha256():
        raise ValueError("candidate inventory rules binding drifted")
    if inventory.source_revision != rules.source.revision:
        raise ValueError("candidate inventory source revision drifted")
    if inventory.source_parquet_sha256 != source_parquet_sha256:
        raise ValueError("candidate inventory source payload drifted")
    return inventory


def load_candidate_inventory_receipt(
    path: Path,
    *,
    rules_path: Path,
    preflight_path: Path,
    inventory_path: Path,
) -> CandidateInventoryReceipt:
    for artifact_path in (
        path,
        rules_path,
        preflight_path,
        inventory_path,
    ):
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ValueError(
                "inventory receipt artifacts must be regular non-symlink files"
            )
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    receipt = CandidateInventoryReceipt.model_validate(payload)
    rules = load_exploratory_acquisition_rules(rules_path)
    expected_hashes = {
        "rules_file_sha256": hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        "rules_canonical_sha256": rules.canonical_sha256(),
        "preflight_file_sha256": hashlib.sha256(
            preflight_path.read_bytes()
        ).hexdigest(),
        "inventory_file_sha256": hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
    }
    drift = tuple(
        name
        for name, expected in expected_hashes.items()
        if getattr(receipt, name) != expected
    )
    if drift:
        raise ValueError(f"candidate inventory receipt drifted: {drift}")
    return receipt


def load_acquisition_host_preflight_receipt_v2(
    path: Path,
    *,
    rules_path: Path,
) -> AcquisitionHostPreflightReceiptV2:
    for artifact_path in (path, rules_path):
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ValueError(
                "V2 preflight artifacts must be regular non-symlink files"
            )
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    receipt = AcquisitionHostPreflightReceiptV2.model_validate(payload)
    rules = load_exploratory_acquisition_rules(rules_path)
    expected_rules_file_sha256 = hashlib.sha256(
        rules_path.read_bytes()
    ).hexdigest()
    if receipt.rules_file_sha256 != expected_rules_file_sha256:
        raise ValueError("V2 preflight rules file binding drifted")
    if receipt.rules_canonical_sha256 != rules.canonical_sha256():
        raise ValueError("V2 preflight canonical rules binding drifted")
    if receipt.source_revision != rules.source.revision:
        raise ValueError("V2 preflight source revision drifted")
    if receipt.source_row_count != rules.source.expected_row_count:
        raise ValueError("V2 preflight source row count drifted")
    if receipt.host_facts.harness_commit != rules.source.harness_revision:
        raise ValueError("V2 preflight harness revision drifted")
    return receipt


def load_candidate_exclusion_receipt(
    path: Path,
    *,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
) -> CandidateExclusionReceipt:
    for artifact_path in (
        path,
        rules_path,
        host_preflight_path,
        inventory_path,
    ):
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ValueError(
                "candidate exclusion artifacts must be regular files"
            )
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    receipt = CandidateExclusionReceipt.model_validate(payload)
    expected = {
        "rules_file_sha256": hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        "host_preflight_file_sha256": hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        "candidate_inventory_file_sha256": hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
    }
    drift = tuple(
        field
        for field, digest in expected.items()
        if getattr(receipt, field) != digest
    )
    if drift:
        raise ValueError(f"candidate exclusion receipt drifted: {drift}")
    return receipt


def load_candidate_image_acquisition_receipt(
    path: Path,
    *,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
) -> CandidateImageAcquisitionReceipt:
    for artifact_path in (
        path,
        rules_path,
        host_preflight_path,
        inventory_path,
    ):
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ValueError(
                "candidate image acquisition artifacts must be regular files"
            )
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    receipt = CandidateImageAcquisitionReceipt.model_validate(payload)
    rules = load_exploratory_acquisition_rules(rules_path)
    expected = {
        "rules_file_sha256": hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        "rules_canonical_sha256": rules.canonical_sha256(),
        "host_preflight_file_sha256": hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        "candidate_inventory_file_sha256": hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
    }
    drift = tuple(
        field
        for field, digest in expected.items()
        if getattr(receipt, field) != digest
    )
    if drift:
        raise ValueError(f"candidate image receipt drifted: {drift}")
    return receipt


def load_candidate_execution_preflight_receipt(
    path: Path,
    *,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
) -> CandidateExecutionPreflightReceipt:
    for artifact_path in (
        path,
        rules_path,
        host_preflight_path,
        inventory_path,
        image_receipt_path,
    ):
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ValueError(
                "candidate execution artifacts must be regular files"
            )
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    receipt = CandidateExecutionPreflightReceipt.model_validate(payload)
    rules = load_exploratory_acquisition_rules(rules_path)
    expected = {
        "rules_file_sha256": hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        "rules_canonical_sha256": rules.canonical_sha256(),
        "host_preflight_file_sha256": hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        "candidate_inventory_file_sha256": hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        "image_acquisition_receipt_sha256": hashlib.sha256(
            image_receipt_path.read_bytes()
        ).hexdigest(),
    }
    drift = tuple(
        field
        for field, digest in expected.items()
        if getattr(receipt, field) != digest
    )
    if drift:
        raise ValueError(f"candidate execution receipt drifted: {drift}")
    return receipt


def load_candidate_base_reproduction_exclusion_receipt(
    path: Path,
    *,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
) -> CandidateBaseReproductionExclusionReceipt:
    for artifact_path in (
        path,
        rules_path,
        host_preflight_path,
        inventory_path,
        image_receipt_path,
    ):
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ValueError(
                "base reproduction exclusion artifacts must be regular files"
            )
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    receipt = CandidateBaseReproductionExclusionReceipt.model_validate(
        payload
    )
    rules = load_exploratory_acquisition_rules(rules_path)
    expected = {
        "rules_file_sha256": hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        "rules_canonical_sha256": rules.canonical_sha256(),
        "host_preflight_file_sha256": hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        "candidate_inventory_file_sha256": hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        "image_acquisition_receipt_sha256": hashlib.sha256(
            image_receipt_path.read_bytes()
        ).hexdigest(),
    }
    drift = tuple(
        field
        for field, digest in expected.items()
        if getattr(receipt, field) != digest
    )
    if drift:
        raise ValueError(
            f"base reproduction exclusion receipt drifted: {drift}"
        )
    if (
        receipt.exclusion_reason not in rules.exclusion_reasons
        or receipt.instance_id
        != CandidateImageAcquisitionReceipt.model_validate(
            json.loads(
                image_receipt_path.read_text(),
                object_pairs_hook=_reject_duplicate_keys,
            )
        ).instance_id
    ):
        raise ValueError("base reproduction exclusion identity drifted")
    return receipt


def load_candidate_gold_verification_exclusion_receipt(
    path: Path,
    *,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
) -> CandidateGoldVerificationExclusionReceipt:
    for artifact_path in (
        path,
        rules_path,
        host_preflight_path,
        inventory_path,
        image_receipt_path,
    ):
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ValueError(
                "gold verification exclusion artifacts must be regular files"
            )
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    receipt = CandidateGoldVerificationExclusionReceipt.model_validate(
        payload
    )
    rules = load_exploratory_acquisition_rules(rules_path)
    expected = {
        "rules_file_sha256": hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        "rules_canonical_sha256": rules.canonical_sha256(),
        "host_preflight_file_sha256": hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        "candidate_inventory_file_sha256": hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        "image_acquisition_receipt_sha256": hashlib.sha256(
            image_receipt_path.read_bytes()
        ).hexdigest(),
    }
    drift = tuple(
        field
        for field, digest in expected.items()
        if getattr(receipt, field) != digest
    )
    if drift:
        raise ValueError(
            f"gold verification exclusion receipt drifted: {drift}"
        )
    if (
        receipt.exclusion_reason not in rules.exclusion_reasons
        or receipt.instance_id
        != CandidateImageAcquisitionReceipt.model_validate(
            json.loads(
                image_receipt_path.read_text(),
                object_pairs_hook=_reject_duplicate_keys,
            )
        ).instance_id
    ):
        raise ValueError("gold verification exclusion identity drifted")
    return receipt


def load_candidate_nonexecution_evidence_receipt(
    path: Path,
    *,
    rules_path: Path,
    host_preflight_path: Path,
    inventory_path: Path,
    image_receipt_path: Path,
    execution_receipt_path: Path,
) -> CandidateNonexecutionEvidenceReceipt:
    for artifact_path in (
        path,
        rules_path,
        host_preflight_path,
        inventory_path,
        image_receipt_path,
        execution_receipt_path,
    ):
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ValueError(
                "candidate nonexecution artifacts must be regular files"
            )
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    receipt = CandidateNonexecutionEvidenceReceipt.model_validate(payload)
    rules = load_exploratory_acquisition_rules(rules_path)
    expected = {
        "rules_file_sha256": hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        "rules_canonical_sha256": rules.canonical_sha256(),
        "host_preflight_file_sha256": hashlib.sha256(
            host_preflight_path.read_bytes()
        ).hexdigest(),
        "candidate_inventory_file_sha256": hashlib.sha256(
            inventory_path.read_bytes()
        ).hexdigest(),
        "image_acquisition_receipt_sha256": hashlib.sha256(
            image_receipt_path.read_bytes()
        ).hexdigest(),
        "execution_preflight_receipt_sha256": hashlib.sha256(
            execution_receipt_path.read_bytes()
        ).hexdigest(),
    }
    drift = tuple(
        field
        for field, digest in expected.items()
        if getattr(receipt, field) != digest
    )
    if drift:
        raise ValueError(f"candidate nonexecution receipt drifted: {drift}")
    return receipt


def load_candidate_stratum_review_decision(
    path: Path,
    *,
    rules_path: Path,
    nonexecution_receipt_path: Path,
) -> CandidateStratumReviewDecision:
    for artifact_path in (
        path,
        rules_path,
        nonexecution_receipt_path,
    ):
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ValueError(
                "candidate stratum review artifacts must be regular files"
            )
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    decision = CandidateStratumReviewDecision.model_validate(payload)
    rules = load_exploratory_acquisition_rules(rules_path)
    nonexecution_payload = json.loads(
        nonexecution_receipt_path.read_text(),
        object_pairs_hook=_reject_duplicate_keys,
    )
    nonexecution = CandidateNonexecutionEvidenceReceipt.model_validate(
        nonexecution_payload
    )
    expected = {
        "rules_file_sha256": hashlib.sha256(
            rules_path.read_bytes()
        ).hexdigest(),
        "rules_canonical_sha256": rules.canonical_sha256(),
        "nonexecution_receipt_sha256": hashlib.sha256(
            nonexecution_receipt_path.read_bytes()
        ).hexdigest(),
    }
    drift = tuple(
        field
        for field, digest in expected.items()
        if getattr(decision, field) != digest
    )
    if drift:
        raise ValueError(f"candidate stratum review drifted: {drift}")
    if (
        decision.instance_id != nonexecution.instance_id
        or decision.selection_rank != nonexecution.selection_rank
        or decision.proposed_stratum not in rules.stratum_definitions
    ):
        raise ValueError("candidate stratum review identity drifted")
    return decision
