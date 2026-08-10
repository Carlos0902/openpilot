# Phase H8-R2BA：Default-off production-facing reusable summary binding shadow 结果

## 判定

**PASS FOR PRODUCTION-SHAPED SHADOW EVIDENCE；仍不授权 prompt 使用或 default-on。**

本阶段把 H8-R2AZ 的 reusable summary admission 结果映射到生产 metadata surface：
`ContextSelectionMetadata.compaction_reuse_admissions`。该字段是 body-free、shadow-only 的 owned nested
evidence；validator 强制 `used_in_prompt=false`，因此 admitted shadow 不会创建
`ContextCompactionBinding`，也不会 govern source omissions。

## Metadata impact note

Fact:
Reusable compaction artifact admission/shadow rejection for one context selection pass.

Authoritative producer:
The context assembly/admission boundary that evaluates a previously generated compaction artifact against current
source binding, required/recent guards, session constraints, and artifact integrity.

Consumers:
Context selection diagnostics, checkpoint/replay audit, experiment evidence, future default-off builder shadow.

Lifecycle:
Runtime selection evidence / checkpoint-compatible nested value. It is not durable project state and not a new
artifact authority.

Control impact:
None in this phase. The contract is shadow-only; `used_in_prompt=true` is invalid.

Existing contracts reviewed:
`ContextSelectionMetadata`, `ContextCompactionAttempt`, `ContextCompactionRecord`, `ContextCompactionBinding`,
`DurableArtifactReference`, `RuntimePromptContextSnapshot`, `docs/metadata/CONTRACT_CATALOG.md`,
`docs/metadata/DEVELOPMENT_CONVENTIONS.md`, context builder, context projection, runtime checkpoint tests.

Decision:
Extend existing `ContextSelectionMetadata` with a strict owned nested value,
`ContextCompactionReuseAdmission`; do not add a new `MetadataKind`.

Why no duplicate source of truth is created:
The new value stores only body-free admission evidence and artifact references/checksums. Raw dialog remains source
authority, `ContextCompactionRecord` remains the compact record authority, `ContextCompactionBinding` remains the
prompt-bound artifact authority, and admitted shadow evidence cannot enter the prompt.

Serialization and migration:
The field defaults to `[]`, preserving historical reads. Enums are serialized as values. Invalid states fail closed:
rejected entries require a typed reason, admitted entries cannot carry a reason, duplicate admission IDs are rejected,
and `used_in_prompt=true` is rejected.

Tests:
`Code/tests/test_metadata_models.py`; H8-R2BA experiment tests; context/rolling focused suite.

Documentation updates:
`API.md`, `docs/metadata/CONTRACT_CATALOG.md`, this result doc, evidence index, completion audit, task trajectory
implementation log.

## 实施内容

- 新增 metadata：
  - `ContextCompactionReuseAdmissionStatus`
  - `ContextCompactionReuseRejectionReason`
  - `ContextCompactionReuseAdmission`
  - `ContextSelectionMetadata.compaction_reuse_admissions`
- 更新 exports：
  `Code/src/metadata/__init__.py`
- 更新 docs：
  `API.md`、`docs/metadata/CONTRACT_CATALOG.md`
- 新增 BA harness：
  `experiments/full_architecture_context_observation/stage_h8r2ba_production_binding_shadow.py`
- 新增 BA tests：
  `experiments/full_architecture_context_observation/test_stage_h8r2ba_production_binding_shadow.py`

## 结果

正式 receipt：

`experiments/full_architecture_context_observation/runs/phase_h8r2ba_production_binding_shadow_20260809_v1/aggregate/receipt.json`

Aggregate hash：

`sha256:6efa909ce7d9786f5288db7fe1af858d83f23db3ddb3d0318cee2a1b686fd78c`

Metadata surface：

`ContextSelectionMetadata.compaction_reuse_admissions`

Admission count：8

- 1 admitted shadow；
- 7 rejected drift cases；
- `candidate_decisions=[]`；
- `compaction_attempts=[]`；
- all `used_in_prompt=false`。

Rejected reasons covered：

- `source_fingerprint_mismatch`
- `source_candidate_ids_mismatch`
- `required_candidate_ids_mismatch`
- `recent_suffix_ids_mismatch`
- `session_constraints_hash_mismatch`
- `artifact_kind_mismatch`
- `artifact_integrity_mismatch`

## Gates

- Metadata + AZ/BA focused：
  `PYTHONPATH=Code/src:. python -m pytest -q Code/tests/test_metadata_models.py experiments/full_architecture_context_observation/test_stage_h8r2az_reusable_summary_artifact.py experiments/full_architecture_context_observation/test_stage_h8r2ba_production_binding_shadow.py`
  → **62 passed**
- AV/AX/AY/AZ/BA adjacent focused：
  → **21 passed**
- Context/rolling focused：
  `Code/tests/test_memory_context_rolling_integration.py Code/tests/test_rolling_summary_factory.py Code/tests/test_rolling_compaction.py Code/tests/test_compaction_summary_contract.py Code/tests/test_context_assembly.py Code/tests/test_context_projection.py`
  → **99 passed**
- `compileall` for metadata and BA harness/tests：passed
- body/secret-free scan for BA and nested AZ source receipts：passed
- `git diff --check`：passed
- trailing whitespace scan for changed BA/metadata docs/code：no hits

## 安全边界

- no provider transport；
- no prompt/summary/response body persisted；
- no project/memory/network/writer/command/verification side effects；
- no `ContextCompactionBinding` created from reuse admission；
- no prompt candidate added from reuse admission；
- raw dialog and prompt-context artifact remain authoritative.

## 限制

- Production builder still does not read reusable artifacts by default.
- This phase proves production-shaped shadow metadata and validation, not prompt-use behavior.
- Semantic equivalence, cross-provider behavior, and real-task long-session benefit remain unproven.

## 下一步

H8-R2BB should wire a default-off builder shadow provider that can evaluate candidate reusable artifacts and append
`compaction_reuse_admissions` to the actual context selection metadata while still keeping `used_in_prompt=false`.
Only after that shadow is stable should a separate canary consider prompt use.
