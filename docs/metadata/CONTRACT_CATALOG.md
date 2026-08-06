# Metadata Contract Catalog

## Purpose

This catalog makes OpenPilot's cross-module metadata surface reviewable without
requiring contributors to remember every model. The executable sources of truth
remain:

- `Code/src/metadata/base.py::MetadataKind` for canonical kinds;
- `Code/src/metadata/__init__.py::__all__` for the public contract surface;
- `Code/tests/test_metadata_models.py` for one-kind-per-model, envelope, and
  catalog-completeness invariants.

The catalog describes ownership and lifecycle boundaries. Field-level truth
comes from the Pydantic models themselves.

All additions and changes must follow
`docs/metadata/DEVELOPMENT_CONVENTIONS.md`. That document defines the mandatory
reuse/duplication review while preserving the currently implemented value-nested
architecture. This catalog answers “what exists”; the convention answers
“whether and how it may change.”

## Protocol invariants

Every concrete metadata model must:

- inherit `MetadataBase`;
- own exactly one `Literal[MetadataKind.*]` kind;
- preserve the common `source: MetadataSource` envelope field;
- serialize through `to_json_dict()` when crossing a module or persistence
  boundary;
- keep runtime-only objects outside the serialized protocol;
- be exported from `metadata.__all__` and listed in this catalog.

`source` is reserved for the producer/owner envelope. Domain provenance must
use a qualified name such as `path_source` or `signal_source`. Free-form
diagnostic extensions belong in `annotations`, `attributes`, `trace_info`, or
another explicitly scoped escape hatch; they must not silently replace an
existing typed field.

## Current inventory

There are 79 public concrete contracts, one for each `MetadataKind`.

| Family / owner | Contracts | Boundary and lifecycle |
| --- | --- | --- |
| Artifacts (`metadata/artifacts.py`) | `TextArtifactMetadata`, `CodeArtifactMetadata`, `FileArtifactMetadata`, `CommandArtifactMetadata`, `SearchArtifactMetadata`, `EmbeddingArtifactMetadata` | Typed payloads produced by tools and routed to downstream tools, agents, UI, or persistence. |
| Result envelopes (`metadata/results.py`) | `FailureMetadata`, `ToolResultMetadata`, `TaskResultMetadata` | Success/failure envelopes for tool and task boundaries. `status`, `result`, and `failure` must remain mutually consistent. |
| Tool orchestration (`metadata/tooling.py`) | `ToolInputMetadata`, `ToolSelectionMetadata`, `ToolContractMetadata`, `ToolChainMetadata`, `ToolContextMetadata`, `ToolCallMetadata`, `ToolErrorMetadata`, `ToolEventMetadata`, `ToolLoopMetadata` | Planner-to-router-to-executor contracts and the typed tool-loop trace. Runtime handles are deliberately excluded from serialization. |
| Execution runtime (`metadata/runtime.py`) | `LLMRequestMetadata`, `LLMResponseMetadata`, `ExecutionContextMetadata`, `LogEventMetadata`, `ToolExecutionEnvelopeMetadata`, `AgentExecutionMetadata`, `ModuleExecutionMetadata` | LLM, execution, logging, and aggregate runtime envelopes. `ReasoningPolicy` and `ResolvedReasoningPolicy` are strict owned values inside LLM envelopes, not new public metadata kinds. These are execution observations, not autonomous state-machine ownership. |
| Agent runtime (`metadata/agent_runtime.py`) | `RuntimeBudgetMetadata`, `ContextSelectionMetadata`, `EditPlanMetadata`, `VerificationPlanMetadata`, `PathIntentMetadata`, `PathResolutionMetadata`, `DecisionNeedMetadata`, `ToolDecisionMetadata`, `GuardDecisionMetadata`, `RuntimeStateMetadata`, `RuntimeCheckpointMetadata`, `RuntimeResumeDecisionMetadata`, `RuntimeReportMetadata` | Mutable phase-driven task state, prompt-context selection, bounded tool/recovery and tool-event completion budgets, evidence-backed decisions, path grounding, durable recovery checkpoints and finalization cursor, explainable resume preflight, and the final derived report. |
| Entry routing (`metadata/routing.py`) | `TaskRouteMetadata` | UI/entry-point decision between supported execution routes. |
| Project and autonomy (`metadata/project.py`) | `ProjectStateMetadata`, `ProblemSignalMetadata`, `ProblemJudgmentMetadata`, `DifficultyAssessmentMetadata`, `ResolutionPlanMetadata`, `TaskGraphNodeMetadata`, `TaskGraphEdgeMetadata`, `ExecutionStateMetadata`, `ProductIntentMetadata`, `ProjectObjectiveMetadata`, `SuccessMetricMetadata`, `ProjectDimensionAssessmentMetadata`, `ReferenceInsightMetadata`, `ProjectDependencyMetadata`, `DependencyStrategyMetadata`, `ProjectStackPresetMetadata`, `FileContentSectionMetadata`, `FileContentIndexMetadata`, `DirectorySketchMetadata`, `TaskFileResolutionRequestMetadata`, `RelatedProjectFileMetadata`, `TaskFileResolutionMetadata`, `GitRepositoryMetadata`, `GitSnapshotMetadata`, `GitDiffContextMetadata`, `ImprovementCandidateMetadata`, `ProjectDiagnosisMetadata`, `ValidationIssueMetadata`, `ImprovementAnalysisMetadata`, `EnvironmentSyncMetadata`, `AutonomyDecisionMetadata` | Project facts, diagnosis, planning graph, file indexes, environment state, and improvement decisions shared across autonomy modules. Persist only the models required by the owning store or trajectory. |
| Bug and environment repair (`metadata/bugfix.py`) | `BugFixAttemptMetadata`, `BugFixResultMetadata`, `EnvironmentFailureMetadata`, `EnvironmentFixResultMetadata` | Bounded repair attempts and their final evidence. |
| Warning analysis (`metadata/warnings.py`) | `WarningItemMetadata`, `WarningCheckResultMetadata` | Structured warning classification and the aggregate warning-check result. |
| Generated-agent data flow (`metadata/data.py`) | `CollectedDataMetadata`, `ProcessedDataMetadata`, `PresentationMetadata` | Collection, processing, and presentation-stage exchange for generated agents. |

## Similar-looking contracts that are not currently duplicates

- `ReasoningDecisionComplexity` is a typed request-owner routing input and is
  not a provider capability or a completion-budget authority. It separates
  reasoning complexity from `EnhancementCompletionComplexity`.
- `ReasoningPolicy` owns provider-neutral caller intent;
  `ResolvedReasoningPolicy` owns the capability-profile decision and transport
  semantics actually used. They are not two authorities: resolution is a
  deterministic derived value. `ReasoningCapabilityProfileId` and
  `ReasoningTransportFamily` are typed routing identities, while provider
  payload keys remain transport implementation details.

- `EnvironmentSyncMetadata` is the sole project-environment lifecycle owner.
  Its typed operation/readiness fields control attachment and setup admission;
  `operations` and warnings are diagnostic only. Old payloads default to
  `legacy_sync + unknown`, never ready. `ProjectFingerprint.interpreter` and
  `environment_id` bind an already-ready observation to a checkpoint for resume
  comparison; they are not a second mutable environment state. Requested and
  effective command identities remain owned by `ToolInputMetadata`.

- `RuntimeStateMetadata` is mutable controller state;
  `RuntimeCheckpointMetadata` is an immutable persisted snapshot at a named
  recovery boundary;
  `RuntimeResumeDecisionMetadata` is the preflight decision for one explicit
  resume attempt;
  `RuntimeReportMetadata` is its final auditable projection;
  `ProjectStateMetadata` describes the target project rather than a task run.
- `RuntimeStateMetadata.session_constraints` is the single owner of the
  source-linked, in-session constraint ledger. `SessionConstraintProposal`,
  `SessionConstraintEntry`, and `SessionConstraintState` are strict nested
  values, not public Metadata kinds. Proposals never control execution;
  active/revoked entries preserve a per-key snapshot and revoked tombstones.
  `SessionConstraintLimits` is an owned typed bound for pending proposals,
  active/revoked entries, serialized values, and variant item counts. Newer
  same-key user proposals source-link and supersede older pending proposals;
  superseded proposals cannot be activated. Missing limits in historical
  checkpoints migrate to bounded defaults.
  `TaskGraphNodeMetadata.write_files` and `validation_command`,
  `RuntimeExecutionMode`, and existing guards remain the execution authorities;
  the session ledger can only narrow or project those facts. Assistant text,
  summaries, compact artifacts, and long-term memory cannot create authority.
- `ConversationIdentity`, `SessionTurn`, and `SessionIngressState` are strict
  ingress values for the production conversation owner. They keep
  conversation identity distinct from a per-run checkpoint identity and hold
  pending proposals outside long-term memory; they do not add a public
  `MetadataKind` or replace the runtime ledger. `RuntimeCheckpointMetadata`
  nests a bounded `session_ingress_state` snapshot and rejects a checkpoint
  whose nested constraint snapshot diverges from `RuntimeStateMetadata`.
- Recovery extends that existing runtime family rather than adding parallel
  public kinds. `RuntimeStateMetadata.recovery_status` is operational state;
  `RuntimeResumeDecisionMetadata` owns one attempt's recoverability, mode,
  automation policy, reason code, strict nested blockers, and fallback;
  `RuntimeReportMetadata` is a projection. `RecoveryBlocker` and
  `RecoveryFallback` are owned nested values, not independent contracts.
- `RuntimeCheckpointMetadata.session_cursor` owns the durable monolithic-session
  position. It reuses `TaskGraphNodeMetadata` for the decomposed plan and nests
  strict semantic/task-result cursor values; the plan hash and contiguous result
  rule prevent a controller from guessing the next subtask. It does not create a
  second public session metadata kind.
- `SessionBootstrapCursor`, `PendingLLMRequest`, `LLMReplayEntry`,
  `ReadToolReplayEntry`, and `DurableArtifactReference` are strict values owned
  by the checkpoint. They identify hashes, ordinals, calls, and integrity-checked
  artifacts needed for apply-once recovery; provider payloads and read contents
  live in the artifact store, not in a parallel state tree.
- `RuntimeBudgetMetadata` remains the single owner of both completion-accounting
  pools. The controller-decision pool and post-core enhancement pool are
  independent 12,000-token limits, not one global allowance. Enhancement
  policy/limit/request/reservation/reconciliation values and their complexity,
  decision-value, and requirement enums are strict owned nested values; they
  add no `MetadataKind`. Reservation ledger keys are stable logical call keys,
  reconciliation keys are reservation IDs, and aggregate counters must match
  those ledgers after checkpoint restoration. Provider usage and finish reason
  remain execution evidence; reconciliation is only the budget projection, not
  a second provider fact source. The pool governs planning, full-file generation,
  and localized `code_edit`; historical four-purpose policy values migrate by
  adding the bounded `code_edit` limit. `EnhancementCompletionRequirement` applies to
  one model call, while `ProjectImprovementPolicy` remains the stage/top-level
  success authority.
- `RuntimePromptContextSnapshot` is a strict value owned by
  `RuntimeCheckpointMetadata`. It binds the complete context request hash and
  rendered Prompt hash to the existing `ContextSelectionMetadata` and one
  checksum-verified `prompt_context` artifact. Raw memory/project/dialog stores
  remain authoritative for candidates; the artifact is the immutable selected
  input for that model call, not a second memory store.
- `VerificationStatus` and `CheckpointStatus` are strict control enums rather
  than public metadata kinds. Human recovery explanations remain strings only
  because they do not control branching.
- `CheckpointBoundary` is the single typed registry for durable checkpoint
  names. `CheckpointFaultPoint` types runtime-only deterministic write-before
  and write-after test hooks; the hook itself is never serialized.
- `RuntimeCheckpointMetadata.finalization_cursor` owns the monotonic
  `state_completed -> report_persisted -> run_finalized` progress. It reuses
  `DurableArtifactReference` for the checksum-verified report and records the
  completion event ID as projection evidence. `RuntimeReportMetadata.state_hash`
  binds the derived report to completed task state; neither value duplicates
  authoritative runtime state. `FinalizationFaultPoint` is test-only.
- `RuntimeStateMetadata.execution_mode` is root-task permission state. Its
  source and reason explain who established the boundary; subtask tags are not
  authoritative producers. `guard_history` stores typed routing decisions for
  audit and failure propagation, not an alternative permission source.
- `ProjectImprovementPolicy` and its requirement/source enums are strict owned
  runtime values, not new Metadata kinds. Configuration produces this policy
  once; legacy enable/count/attempt fields are compatibility views. Runtime
  state/report preserve the same value with `core_success` and the typed
  improvement status so optional enhancement failure cannot overwrite core
  execution facts.
- `Task.write_files` remains planned intent. Observed file mutations flow from
  successful mutation tool results into runtime state and
  `ExecutionStateMetadata.changed_files`; these fields are deliberately not
  interchangeable.
- `ImprovementAnalysisMetadata.evidence_ids` links a bounded improvement delta
  to retained context candidates from the exact provider request. The model may
  propose IDs, but runtime filtering is authoritative; unknown or omitted
  candidate IDs are discarded. `DesignedImprovementTask.evidence_ids` is the
  local autonomous-iteration equivalent and is not a second evidence store.
  Its provider-facing contract accepts only exact `[evidence_id="..."]` header
  values; goal, diagnosis-candidate, task, source, and content-embedded IDs are
  distinct identity domains and are never interpreted as context evidence.
- `RuntimeCheckpointMetadata.pending_verification` is the typed, still-required
  validation plan known before a file mutation. `VerificationCommandSpec`,
  `next_command_index`, and the strict `completed_commands` prefix extend that
  existing owner for bounded ordered validation; completed commands are not
  reconstructed from path evidence or free-form summaries.
- `resume_attempt_id` identifies one execution attempt, while
  `resume_source_checkpoint_id` identifies the immutable checkpoint selected as
  its state source. Retrying an older generation appends after the run's current
  latest generation and never overwrites the intervening history.
- `ToolCallMetadata` is a requested invocation, `ToolErrorMetadata` is a
  protocol/execution failure, and `ToolEventMetadata` is the lifecycle event
  that may embed either. Their repeated correlation fields are intentional for
  standalone trajectory records.
- `AgentExecutionMetadata` and `ModuleExecutionMetadata` share an execution
  envelope but identify different ownership levels. Consolidation should wait
  until producers and consumers demonstrate identical lifecycle semantics.
- Artifact subclasses share text fields because code and files can flow through
  text-oriented consumers while retaining a discriminated kind.

## Known pressure points

- `ToolInputMetadata` is a broad compatibility contract with many optional
  fields. Do not add another field until the target tool contract, producer,
  and consumer are identified. Prefer a dedicated input model only when a real
  boundary can adopt it end to end.
- `ToolInputMetadata.session_turn_source_hash` is the identified typed
  evidence field for a model-facing dialog projection. It is a derived replay
  digest only; raw `SessionIngressState` remains the authority and is never
  replaced by this hash.
- `attributes`, `annotations`, `raw_payload`, `details`, `trace_info`, and other
  `JsonValue` containers weaken schema guarantees. They are appropriate for
  diagnostics or provider-specific data, but not for facts that control routing,
  permissions, recovery, or completion.
- Permission branching now uses the typed `execution_mode` and
  `execution_mode_source` enums. The legacy read-only assumption marker is a
  historical-read migration input only and must not be produced by new
  subtask planning.
- Several project-analysis models contain nested free-form dictionaries. Type
  them incrementally when more than one module depends on the same shape; avoid
  speculative mass conversion.
- Schema version `1.0` is currently global. A breaking persisted-shape change
  requires an explicit read migration or a version change before old data is
  written back.

## Change checklist

Before adding or changing a metadata field, record:

1. The fact being represented and why an existing field cannot represent it.
2. The authoritative producer and every known consumer.
3. Whether it is runtime-only, trajectory evidence, or durable project state.
4. Whether it controls branching, permissions, recovery, or completion.
5. Default and optionality semantics; avoid using empty strings and `None` for
   different undocumented meanings.
6. Serialization, historical-read, and deprecation behavior.
7. Tests for round-trip serialization, invalid input, boundary conditions, and
   affected state transitions.
8. Required updates to `API.md`, this catalog, and task-trajectory documentation.

Deprecation proceeds in order: stop new producers, migrate consumers, preserve
historical reads, remove the old field, and finally remove its compatibility
path. Do not merge similar models solely because their field names overlap.

## Context-management alignment

Context selection should reuse these contracts rather than copying entire raw
payloads. A context decision is explainable only when the trajectory can recover:

- the applicable budget and usage before/after selection;
- the source contract or artifact considered;
- the keep, summarize, truncate, or discard decision;
- the reason and resulting size;
- the checkpoint used for recovery after interruption.

`MemoryContextBuilder` adapts each fixed instruction, dialog message, project
file, memory record, and environment observation into an individual
`ContextCandidate` with stable candidate/source identity, while
`memory.context_assembly.ContextAssembler` produces the bounded view and emits
`ContextSelectionMetadata` because this decision controls model-facing input and crosses
the memory, autonomous-iteration, and trajectory boundaries. The assembler is not a
second source store and introduces no additional Metadata contract. The contract records
whether character or exact provider
token budgeting controlled selection, tokenizer/model identity, original/final
character and token sizes, candidate decisions, and the contiguous recent-dialog
selection boundary. Historical section decisions are a compatibility projection
derived from candidate decisions, not a second selection authority. The selected
source entries remain available to non-model
  consumers. Project improvement analysis, Goal Maker, and Task Designer now
  project required control candidates separately from optional file/history
  evidence instead of copying a complete ProjectState/report message. Contextual
  code generation follows the same ownership rule: `Task` / `ToolInputMetadata`,
  `CodeGenerationRequest`, and `ProductIntentMetadata` retain their authoritative
  facts while a stateless adapter derives `ContextCandidate` values. Required
  current source is never truncated for an existing-file replacement; optional
  diagnosis/environment/judgment are bounded summaries. This is a derived view,
  not a new Metadata kind or second permission/task owner.

Candidate governance reuses these owned nested contracts. `ContextCandidate`
may classify projection trust and freshness and may name an explicit
`conflict_key`; `ContextAssemblyPolicy` controls exact-duplicate, stale, and
explicit-conflict handling; `ContextCandidateDecision` records the governed
reason and winning candidate. `ContextSelectionMetadata` uses
`governance_blocked` only when required candidates cannot be governed safely.
Defaults keep historical serialized values readable. No semantic contradiction
is inferred from free text, embeddings, tags, or confidence, and no source memory
is mutated by this derived selection pass.

Dialog compaction is represented by the narrow owned nested
`ContextCompactionRecord` / `ContextCompactionBinding` values. The record names
the exact non-required dialog candidate IDs, source fingerprint, deterministic
algorithm, summary, and before/after sizes; the binding requires a
`context_compaction` `DurableArtifactReference`. An `artifact` candidate alone
may name `compacted_candidate_ids`, and each compacted source decision links back
to it. `RuntimePromptContextSnapshot.compaction_bindings` defaults empty for
historical checkpoints and makes checksum validation part of recovery preflight.
Raw dialog remains authoritative; the exact prompt-context artifact remains the
replay authority.
The record accepts historical `deterministic_dialog_extract_v1` and current
`deterministic_observation_mask_v1` algorithms. Algorithm changes do not change
source ownership. A compactor that cannot be kept completely must not govern its
sources out of the assembled request.
Compaction candidates must forbid truncation and cannot be nested or cyclic.
Current observation compaction is limited to assistant dialog projections; user
dialog and required candidates remain outside this algorithm's legal source set.
The optional `ContextCompactionSummary` is a strict derived value for the
feature-flagged `llm_rolling_summary_v1` algorithm. It is source-evidence linked,
bounded by a separate summary token ceiling, and carries no task, permission,
write-scope, or verification authority. Unknown usage, incomplete finish
evidence, stale source fingerprints, or a summary that displaces the recent
suffix restore the deterministic source view. Raw dialog and checkpoint
artifacts remain authoritative.

`ContextQualityExpectation` and `ContextQualityEvaluation` are strict offline
owned values, not runtime Metadata owners. Fixture authors explicitly name
expected selected/omitted candidate IDs; the stateless evaluator reports typed
structural issue codes and references selection evidence. It does not mutate the
assembly, control routing, copy source facts, or infer semantic relevance. These
values therefore do not justify a new `MetadataKind` or persisted runtime field.

The current typed policy identity is `retention_priority_order_v1`. Historical
`priority_then_recency_v1` and section decisions remain readable for compatibility,
but production builders have zero calls to legacy section assembly or the
standalone compressor.

The selected payload is persisted as a checksum artifact at the typed
`context_assembled` boundary. `RuntimePromptContextSnapshot` carries its request
hash, Prompt hash, selection record, and artifact reference with the session
bootstrap/cursor. Resume replays it only for the identical request and fails
closed on artifact/hash mismatch. Omitted candidates still belong to their
memory/project stores and are not copied into checkpoint state.

`ContextSelectionMetadata` does not copy `LLMResponse.usage`: the former is the
pre-request count of the bounded context slice, while provider usage is the
post-request authority for the complete serialized request and billing. If the
configured tokenizer is unavailable, token fields remain absent and
`token_count_method=unavailable`; no heuristic count controls the token budget.

Typed cross-module adapters use owned strict values rather than new Metadata
kinds: `ContextCandidate` describes one source projection and its typed control
policy; `ContextAssemblyPolicy` owns one selection budget; `ContextCandidateDecision`
records the derived per-candidate outcome; and `ContextAssemblyResult` returns
the selected projections with the existing `ContextSelectionMetadata` owner.
`DerivedContextProjection` is an owned nested view of one ready assembly for
downstream purpose adapters. It carries only selected dialog/artifact candidates
plus request, session-turn, constraint, and projection hashes. Raw ingress and
source stores remain authoritative; a downstream adapter must reject stale or
incomplete selected candidates and must not reintroduce compacted source IDs
into a second assembler.
`assembly_status=budget_insufficient` is required when a `required` candidate is
omitted. These values do not own memory, file, artifact, task, or runtime source
facts and must preserve available source IDs instead of becoming a second store.

For provider-ready requests, `ContextSelectionMetadata` distinguishes the
requested prompt budget, explicit framing/safety reserve, effective content
allowance (`max_prompt_tokens`), exact selected content tokens, and remaining
allowance. `LLMRequest.context_selection` and `LLMRequestMetadata.context_selection`
carry this same owned record through transport diagnostics and replay hashing;
they do not duplicate its fields into `trace_info`. Provider response `usage`
remains the post-request authority.

`ContextRequestPurpose` is the typed audit/routing vocabulary for production
assembly adapters. The migration registry covers 22 purposes and assigns each
to its business owner and migration batch. The purpose identifies a call but
does not replace goal, task, tool, artifact, or domain-result Metadata.
