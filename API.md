# OpenPilot Personal Agent API Notes

本文件用于维护 OpenPilot Personal Agent 的模块接口、工具插件、记忆类型和权限边界。每次修改项目架构、工具调用方式或执行权限时，应同步更新本文档。

## 1. System Modules

### Goal Understanding

- Input: user goal, user constraints, optional files or context.
- Output: structured task card.
- Required fields: `goal`, `task_type`, `priority`, `risk_level`, `required_resources`, `expected_deliverables`.
- Responsibility: identify intent, scope, constraints, permissions, and likely execution path.

### Planner

- Input: structured task card, retrieved memory, available tools.
- Output: ordered execution plan.
- Required fields: `steps`, `dependencies`, `fallbacks`, `confirmation_points`, `success_criteria`.
- Responsibility: decompose goals, choose execution order, define checkpoints, and replan after failures.

### Memory

- Input: task context, user feedback, execution logs, reflections.
- Output: relevant memories and memory update proposals.
- Responsibility: retrieve useful context before execution and store useful lessons after execution.

### Tool Selector

- Input: plan step, available tool registry, permission policy.
- Output: selected tool and invocation schema.
- Responsibility: choose API tools, local tools, browser automation, GUI agent, file system access, or local model execution.
- Current runtime note: the planner no longer receives the full tool registry on every turn. It first sees a compact
  **planning surface** (need catalog + core capability cards + deferred capability cards). The runtime then maps
  `decision_needs` to concrete tools through `ToolRouter`.

### Executor

- Input: approved plan step and selected tool.
- Output: execution result, artifacts, logs, and errors.
- Responsibility: run low-risk steps automatically, pause for required confirmations, and report failures.

### Reflection

- Input: final result, execution logs, errors, user feedback.
- Output: task review and memory updates.
- Responsibility: summarize what worked, what failed, what should be reused, and what should be avoided next time.

## 2. MVP Python Interfaces

The first implementation lives under `Code/` as a Python package and CLI. It plans tasks only; it does not execute tools yet.

### LLM Configuration

OpenAI-compatible providers are configured with environment variables:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `OPENPILOT_LLM_PROVIDER` | No | `openai-compatible` | Provider label used in normalized responses. |
| `OPENPILOT_LLM_BASE_URL` | No | `https://api.openai.com/v1` | OpenAI-compatible endpoint base URL. |
| `OPENPILOT_LLM_API_KEY` | Yes for real calls | None | Secret API key. Do not store it in this file. |
| `OPENPILOT_LLM_MODEL` | No | `gpt-4o-mini` | Chat completion model name. |
| `OPENPILOT_LLM_TIMEOUT_SECONDS` | No | `60` | Provider timeout. |
| `OPENPILOT_LLM_TEMPERATURE` | No | `0.2` | Default sampling temperature. |
| `OPENPILOT_LLM_REASONING_CAPABILITY_PROFILE` | No | unset (generic no-control) | Optional typed opt-in to the versioned `v1` profile `generic-openai-compatible`, `openai-chat-known`, or `deepseek-chat-known`; endpoint and model names never select a profile. |
| `OPENPILOT_TOOL_EVENT_REASONING_MODE` | No | `disabled` | Economical policy for typed routine tool decisions; accepts only `provider_default` or `disabled`. |
| `OPENPILOT_LLM_TOKENIZER_PATH` | No | Local DeepSeek cache | Optional explicit provider tokenizer JSON path. |
| `OPENPILOT_CONTEXT_MAX_PROMPT_TOKENS` | No | `4096` | Exact token budget for the memory-context slice when a provider tokenizer is available. |
| `OPENPILOT_CONTEXT_RESERVED_PROMPT_TOKENS` | No | `128` | Explicit framing/safety reserve deducted from assembled request content budget. |
| `OPENPILOT_PROVIDER_TOOL_EXECUTION_ENABLED` | No | `false` | Explicit opt-in for provider-native execution. |
| `OPENPILOT_PROVIDER_TOOL_EXECUTION_BUDGET_PROFILE` | No | `canary` | Typed static budget profile: `canary`, `real_read_only`, or `real_mutation`. |
| `OPENPILOT_PROVIDER_TOOL_EXECUTION_MAX_ROUNDS` | No | `3` | Provider-native round ceiling, bounded to 1–8. |
| `OPENPILOT_PROVIDER_TOOL_INITIAL_CONTEXT_PROJECTION_ENABLED` | No | `false` | Separate opt-in for initial context projection. |
| `OPENPILOT_PROVIDER_TOOL_INITIAL_CONTEXT_MUTATION_ENABLED` | No | `false` | Higher-risk mutation projection opt-in; never implied by read-only projection. |
| `OPENPILOT_PROVIDER_TOOL_COMPLETION_OUTCOME_FEEDBACK_ENABLED` | No | `false` | Enables bounded typed completion-outcome feedback. |
| `OPENPILOT_EMBEDDING_PROVIDER` | No | `openai-compatible` | Embedding provider label. |
| `OPENPILOT_EMBEDDING_BASE_URL` | No | Inherits `OPENPILOT_LLM_BASE_URL` | OpenAI-compatible embedding endpoint. |
| `OPENPILOT_EMBEDDING_API_KEY` | No | Inherits `OPENPILOT_LLM_API_KEY` | Embedding API key. |
| `OPENPILOT_EMBEDDING_MODEL` | No | `text-embedding-3-small` | Embedding model name. |
| `OPENPILOT_EMBEDDING_TIMEOUT_SECONDS` | No | `30` | Embedding request timeout. |

CLI readiness checks treat blank `OPENPILOT_LLM_BASE_URL` and blank
`OPENPILOT_LLM_API_KEY` as missing. Diagnostics may show whether a value is set, but
must never print the actual API key.
Provider lanes bind endpoint, model, capability profile, tokenizer, credential
environment names, budget profile, and round ceiling as one immutable identity.
Lane-specific settings do not load repository `.env` files.
Settings search the repository-root `.env`, `Code/.env`, and the current
working-directory `.env`, so model and tokenizer binding do not change merely
because the CLI was launched from `Code/` instead of the repository root.

### Standard LLM Request / Response

Provider-native Anthropic Messages and Gemini GenerateContent adapters convert
typed requests and normalize responses without changing reasoning policy
ownership. A native call performs one HTTP attempt, never follows redirects,
and rejects response bodies larger than 2,000,000 bytes before JSON parsing.
Retry, caching, and JSON repair remain `LLMClient` responsibilities and are not
enabled by the transport contract alone.
Tool continuations preserve the assistant call IDs, result order, and optional
provider reasoning state exactly. Validation commands are compared as parsed
argv; shell wrappers, pipes, redirections, substitutions, and appended commands
are not treated as equivalent authority.

`LLMRequest`:

- `messages`: typed chat messages, including provider tool continuation fields.
- `tools`: optional typed function definitions.
- `tool_choice`: optional `auto`, `none`, or `required` provider selection policy.
- `response_format`: `text` or `json_object`.
- `temperature`: optional per-request override.
- `max_tokens`: optional token limit.
- `reasoning`: typed provider-neutral `ReasoningPolicy` caller intent.
- `trace_info`: local tracing annotations that are not part of the strict metadata protocol.

`ReasoningPolicy` separates `mode`, optional `effort`, optional reasoning-token
budget, and unsupported-capability behavior from provider transport fields.
`core/reasoning.py` resolves it through a versioned typed capability profile and
produces `ResolvedReasoningPolicy`; only the LLM transport renders OpenAI- or
DeepSeek-compatible request fields. Official provider endpoints may select a
known profile automatically. Unknown/custom endpoints use the conservative
generic profile unless explicitly configured, and unsupported requests either
reject or resolve to provider default according to the typed policy. Model-name
substring matching is not a capability source.
For structured JSON requests, a provider-default policy is mapped to explicit
reasoning disable only when the selected capability profile declares that
control; otherwise the provider default remains omitted.
Tool definitions, tool choice, and continuation messages participate in cache
identity. Provider tool-call responses are normalized into `LLMResponse` and
are never cached because they require a fresh, ordered tool result continuation.
Profiles that declare a native Anthropic or Gemini transport are routed through
the registered native adapter without constructing an OpenAI client. Each HTTP
attempt remains single-shot; `LLMClient` applies at most five configured retries
and records bounded, credential-redacted attempt evidence. After an
environment-proxy network failure, one additional direct attempt is permitted
and labeled separately. Native
streaming remains rejected until a separate streaming contract exists.
OpenAI-compatible streaming preserves provider `reasoning_content` separately
from visible text; reasoning fragments never become user-visible deltas.
Streaming tool-call fragments are joined by their provider index, validated into
the typed call contract, and finalized in index order. Malformed containers,
negative indexes, or incomplete final shapes fail closed.
Provider call IDs are retained separately from project-owned tool lifecycle IDs;
the external ID correlates wire requests and results but never grants permission,
budget, checkpoint, or execution authority.
Provider-native admission returns one strict `admitted` or `blocked` outcome.
Admitted outcomes require an existing typed `ToolSelection`; blocked outcomes
require an existing typed `ToolErrorMetadata`. Contradictory payloads and
provider/project identity drift reject during validation.
Provider-visible tool definitions are projected only from registered typed
contracts. The projection exposes required, alternative-required, conditional,
and defaulted fields; rejects unknown, duplicate, or untyped tools; forbids
additional properties; and never exposes runtime-only input metadata fields.
One request may expose at most 32 tools and 64 declared fields per tool.
Provider tool arguments accept only JSON objects, with a 200,000-character cap
before parsing. Required, alternative-required, and conditional requirements
are checked against the existing typed input metadata without granting execution.
Provider file reads and writes require non-empty explicit scopes, exact canonical
path matches, project-root containment, and non-symlink paths. Scope and request
sets are independently capped at 64 paths.
Provider tool budget admission uses typed per-call and batch-prior usage for
tool calls, reads, edits, creates, and validation attempts. Decisions expose a
strict admitted/blocked status and typed exhaustion reason; exact remaining
boundaries are allowed and projected overages are blocked.
Provider validation commands admit only once, require exact parsed-argv
equivalence, bind omitted mode to `automatic`, reject unapproved modes and cwd
changes, and expose typed rejection reasons. Shell wrappers, pipes,
redirections, substitutions, appended commands, and malformed quoting do not
match the task-owned command.
Provider permission admission fails closed on unknown or forbidden levels.
Medium/high calls require confirmation; mutations additionally require a
separate literal-boolean mutation opt-in, explicit confirmation, and retain
typed mutation/confirmation evidence even after admission.
A single-call read-only admission boundary now composes registry and executor
identity, bounded argument decoding, typed defaults and contracts, runtime
budget, permission, explicit read scope, and exact validation-command checks in
that order. It creates an executable `ToolSelection` only after every boundary
passes, blocks every mutation, and never executes the selected tool itself.
The separate mutation entry admits only `file_patch_writer`. It requires a
literal mutation opt-in, explicit confirmation, exact write scope, a non-empty
task-owned validation command, and a registered `command_executor`; it returns
a selection without performing either the patch or the validation command.
Provider batches are capped at 32 calls and reject duplicate provider call IDs.
Calls retain response order and receive deterministic project IDs; only admitted
calls accumulate typed call, read, edit, create, and validation usage for later
calls in the same batch, so blocked requests cannot consume authority.
Provider round-trip attempt and evidence observations use frozen runtime
contracts. Attempt success/error facts must be consistent; normalized
signatures, paths, declared windows, page counts, evidence keys, duplicate-only
rounds, and finalization requests have explicit bounds and set invariants. These
contracts do not execute tools, persist checkpoints, or grant authority.
The final round-trip result is a separate frozen envelope over existing LLM
responses/messages, tool-loop results, attempt/evidence values, typed provider
budget diagnostics, and reasoning observations. Success is the inverse of error
presence; attempt lineage and evidence counters cannot exceed `rounds_used`;
messages, attempts, loops, and diagnostic collections are bounded.
Provider responses may expose messages as SDK objects or plain mappings; both
forms use the same content, fallback-field, and diagnostic normalization.
Every normalized response records the selected profile adapter's typed,
body-free reasoning observation, including known token usage, reasoning-content
presence, visible-content emptiness, and finish reason.

Decision routing may supply a typed `ReasoningDecisionComplexity` value
(`routine`, `standard`, or `complex`) to a request owner. It is intentionally
separate from enhancement completion complexity: the former selects
provider-neutral reasoning intent, while the latter reserves completion
tokens. Capability resolution remains in `core/reasoning.py`; versioned
provider adapters own transport rendering and normalized reasoning-usage
observations without inferring capability from model names.

The tool planner selects the economical setting only for typed routine work:
bounded inspection with explicit reads, exact validation with its declared
command, or an implementation with one write target and at most two reads.
General, ambiguous, and multi-write decisions remain `provider_default`.
Filtering a plan that violates the task contract fails with purpose-specific
evidence; it may not fall through to a broader deterministic action.

For `tool_event_decision`, `RuntimeBudgetMetadata` derives `max_tokens` from a
runtime total, a static per-call ceiling/floor, recovery-round decay, and the
current loop's remaining calls. The allowance is reserved before transport and
reconciled to provider completion usage after success. A failed provider attempt
also reconciles when usage is available; an empty invalid response without usage
refunds the reservation, while an unknown transport failure conservatively keeps
it. A `length` / `max_tokens` finish grants one bounded, one-shot recovery bonus
to the next controller call instead of permanently raising the ceiling. JSON
repair is limited to one provider attempt for this purpose.

Post-core enhancement calls use a second, independent completion pool owned by
the same `RuntimeBudgetMetadata`; it is not shared with the controller decision
pool above. `EnhancementCompletionBudgetPolicy` defaults to 12,000 tokens and
governs exactly five purposes: `project_improvement` (500–1,500),
`iteration_goal` (400–1,200), `iteration_task_design` (700–2,200),
improvement-owned `code_generation` (1,000–3,500), and localized `code_edit`
(400–1,600). Allocation combines the
purpose range with typed complexity, remaining decision value, selected prompt
size, remaining calls, and remaining total. Core-task code generation uses a
local allowance and cannot consume this post-core pool. Core-task symbol edits
also remain outside this pool; `code_edit` consumes it only when the
project-improvement runtime explicitly attaches the authoritative budget.

Each enhancement call reserves before transport using a stable semantic
`logical_key`. Checkpointed reservation and reconciliation ledgers make reserve
and settlement apply-once across resume. Known success or failure usage replaces
the reservation with actual completion usage; unknown usage conservatively
keeps it. Only a typed `length`/`max_tokens` failure may request one bounded
expansion of an already narrow JSON contract. Truncated code generation is
rejected instead of written. Audit-only trace/context-selection metadata is
excluded from provider replay identity. `EnhancementCompletionRequirement`
controls one call's fail/fallback behavior; it does not replace the stage-level
`ProjectImprovementPolicy`.

`LLMResponse`:

- `content`: raw text content.
- `parsed_json`: parsed object for JSON responses.
- `model`: provider model name.
- `provider`: configured provider label.
- `usage`: normalized usage object when available.
- `finish_reason`: provider finish reason.
- `provider_details`: safe provider details such as response id and timestamp.

Runtime diagnostics also attach a credential-free provider-bound request hash and
request ordinal to LLM attempt evidence. Successful response details and failed
`provider_attempt` evidence carry the attempt ID, normalized endpoint, provider/model
identity, and transport-attempted flag. These are audit links only; token accounting
still comes from complete provider usage, and unknown/partial usage is never serialized
as zero.

### OpenPilot Metadata Protocol

OpenPilot reserves the word metadata for strict, Pydantic v2 model-harness
contracts in `code/src/metadata`. Free-form diagnostic data must use names such
as `annotations`, `attributes`, `trace_info`, or `provider_details`.

Every metadata payload carries:

- `kind`
- `schema_version`
- `source`
- `correlation`

Each concrete model locks `kind` to one `Literal[MetadataKind.*]`. The common
`source` field is always a `MetadataSource` producer/owner envelope; domain
provenance uses qualified fields such as `path_source` and `signal_source`.
Historical path/problem payloads whose `source` was a string are accepted on
read and migrated to the qualified field, while new serialization always emits
the common structured envelope. The complete ownership and lifecycle inventory
is maintained in `docs/metadata/CONTRACT_CATALOG.md`. Metadata changes must
follow `docs/metadata/DEVELOPMENT_CONVENTIONS.md`: review existing contracts and
real producers/consumers first, prefer reuse or extension, and preserve the
current value-nested architecture unless a separately reviewed change is
justified by concrete evidence. Values that control routing, permission, budget,
retry, recovery, completion, persistence, or audit behavior cannot live only in
free-form strings or diagnostic containers.

Tool execution uses typed metadata:

- `ToolDefinition.input_metadata_type`
- `ToolDefinition.output_metadata_type`
- `ToolSelection.input_metadata`
- `ExecutionResult.output_metadata`
- `TaskExecutionResult.result_metadata`

Result metadata uses `status`. Successful results put data in `result`; failed
or timed-out results put structured error details in `failure`.

Path-sensitive runtime actions additionally emit:

- `PathIntentMetadata`: what raw path the planner/runtime wanted to use, for what operation, and under which project root.
- `PathResolutionMetadata`: how that path was grounded (resolved, corrected, planned, ambiguous, or blocked), including whether `sketch.json` or file indexes were used.

Prompt-context assembly emits `ContextSelectionMetadata`. Source-specific builders
collect and render candidates, then delegate request fingerprinting, budgeting,
deterministic selection, truncation, and selection evidence to
`memory.context_assembly.ContextAssembler`. The assembler owns only the derived
model-facing view; memory, project, and runtime stores retain their source facts.
Prompt-specific adapters may submit strict `ContextCandidate` values with typed
kind, source identity, retention (`required`, `preferred`, or `optional`),
priority, source order, and truncation policy. Typed assembly returns
`ContextAssemblyResult`; a required candidate that cannot fit without violating
its policy produces `assembly_status=budget_insufficient` and names the omitted
required IDs. It must not be submitted to the provider as a ready request.
Candidates may additionally carry typed trust, freshness, and an explicit
`conflict_key`. Before budgeting, the assembler removes normalized exact
duplicates within one kind, omits explicitly stale non-required evidence, and
resolves explicit conflict groups by retention, trust, freshness, priority, and
source order. It does not infer semantic conflicts from prose, tags, embeddings,
or confidence gaps. Required stale evidence or materially different required
candidates in one conflict group produce `assembly_status=governance_blocked`
and an independent `ContextAssemblyGovernanceError` before provider transport.
`ContextRequestBuilder` converts a ready result into role-preserving
`LLMMessage` values and attaches the same selection object to `LLMRequest`.
Owners that already projected typed candidates use
`build_context_candidate_request`; message-shaped legacy owners use
`build_context_llm_request`. Both share the same provider-aware policy and
pre-transport failure boundary.
The ContextLoader compatibility payload also exposes the selected-only typed
candidate list used to build a `DerivedContextProjection`. This is a derived
view, not a second source of truth: raw session turns remain authoritative, and
downstream analyzer/Goal/Task adapters must preserve its request/turn/constraint
hash lineage and fail closed on stale or incomplete projections.
Contextual `code_generator` requests use a purpose-specific projection instead
of serializing the complete `prompt_context` as one message. Tool instruction,
task, target/write boundary, product safety constraints, current target source,
and output contract are required and non-truncatable. Validation and quality
evidence are independently selectable; diagnosis, environment, and product
judgment are bounded summaries. Existing-file replacement without current
source, or with required source that cannot fit, fails before provider transport.
Non-contextual code generation retains the legacy message adapter.
When exact token counting is active, `requested_prompt_tokens` is reduced by the
explicit `reserved_prompt_tokens`; `max_prompt_tokens` is the effective content
allowance and `remaining_prompt_tokens` is recorded after selection. The client
rejects `budget_insufficient` before cache or network transport. The reserve
covers chat framing/safety uncertainty and is not reported as provider usage.
Every migrated request carries a typed `ContextRequestPurpose`; phase-3A
semantic routing, decomposition, tool planning, iteration goal/task design,
project improvement, and runtime-output evaluation paths now use the shared
request adapter. The migration registry accounts for all production purposes
and prevents free-form purpose text from controlling assembly policy.
All phase-0 production request purposes are now migrated, including code/edit/
bugfix, memory/summarization, web research, and agent slot generation. Direct
executable `LLMRequest` construction is centralized in
`memory.context_assembly.request_builder`; transport and diagnostic wrappers
observe or forward the assembled request but do not rebuild it.
Project improvement analysis, iteration goal selection, and iteration task
design use purpose-specific candidates rather than one aggregate message. Their
instruction/schema, selected goal, safety constraints, and compact validation
summary are independently required and non-truncatable. README, per-file code,
diagnosis, and historical memory are source-linked optional candidates. Optional
evidence may be omitted with selection evidence; a required candidate that does
not fit still raises typed `ContextAssemblyBudgetError` before provider
transport. The safety projection reads authoritative product intent from the
validated project state and merges any report-carried constraints with stable
deduplication; a report that omits `prompt_context` therefore cannot silently
erase non-regression constraints, delivery surface, or runtime mode. The memory
records used by these adapters are current-project scoped before prompt assembly.
Project/task/session records without current-project identity fail closed; global
feedback and long-term guidance may remain eligible. The model-facing projection
keeps bounded content and a small allowlist of useful environment/iteration
attributes, never raw PATH, provider payloads, Git snapshots, or full dependency
dumps. Task Designer applies the same defensive projection and a three-record cap
even when invoked with an externally constructed project snapshot. An assembled
`memory_context.prompt_text` is artifact evidence only and is never reinserted
as a downstream improvement candidate. Project identity uses canonical resolved
paths; basename and query similarity cannot establish ownership. The required
validation projection includes a name-only project manifest of at most 40 files,
with excluded trees pruned before traversal; manifest files are not added to
`safe_target_files` and their contents are not loaded. Code-generation prompts
may retain compact current-code evidence, but edit routing and `code_editor`
always read the authoritative target file rather than deriving symbol offsets
from a truncated projection.

Task Designer `evidence_ids` may contain only exact values copied from the
assembled request's `[evidence_id="..."]` candidate headers. Goal IDs,
diagnosis candidate IDs, task IDs, source IDs, and identifiers embedded inside
candidate content are different domains and are discarded by the runtime's
retained-candidate filter. This field links the bounded task delta to request
evidence; it does not grant task, target, or mutation authority.

Project environment import discovery scans the bounded project Python surface,
including test modules outside `written_files`, while excluding `.venv`, Git,
cache, and `node_modules` trees. Before a project-scoped Python validation, the
runtime performs a filesystem-only preflight. `EnvironmentSyncMetadata.operation`
distinguishes legacy sync, preflight, setup, and resync; `readiness` is one of
unknown, setup-required, ready, stale, or blocked. Only a ready observation with
an `environment_id`, interpreter, and command cwd may enter the runtime's
attachment cache. Setup/resync may create `.venv`, install packages, update the
stack preset, and create Git safety state, so it requires the root task's
mutation/command/network authority or explicit approval. A denied or failed
setup blocks the Python task; the runtime does not fall back to host Python.
After project writes, validation preflights again so new manifest/import
dependencies become a controlled resync rather than an implicit host dependency.

Command evidence preserves `ToolInputMetadata.requested_command` separately
from the effective command. `effective_interpreter` and `environment_id` prove
which attached environment executed the request. Completion compares the exact
requested validation intent while execution and audit evidence retain the
rewritten `.venv` command.

The general memory adapter emits one typed candidate
per fixed instruction, dialog message, related file, retrieved memory, and
environment observation. Its fixed instruction is required and
non-truncatable; all source items receive candidate-level keep/partial/omit
evidence with stable source identity.
Local tokenizer evidence covers the canonical selected message-content
projection plus an explicit reserve. Provider-private chat framing is not
claimed as locally exact: `LLMResponse.usage` remains authoritative for the
serialized request and billing. Recovery hashes include the attached selection
record, and durable replay returns the observed response without another
provider call.
With a locally available provider tokenizer, the production memory context builder applies
`OPENPILOT_CONTEXT_MAX_PROMPT_TOKENS` (4,096 by default; tool callers reuse
`max_tokens`) together with the 16,000-character safety ceiling. Without a
tokenizer it explicitly falls back to the existing character boundary; it never
labels `chars/4` as an exact token count. Candidate selection uses typed
retention, priority, and source-order policy; rendering remains system prompt,
recent dialog as a contiguous suffix, related files, related memories, then
environment evidence. Candidate decisions are authoritative. Section-level
keep/partial/omit reasons and the selected dialog start/timestamp boundary remain
as compatibility projections for existing consumers. The record also carries
original/final character counts and exact context-slice token and tokenizer/model
evidence. General memory consumers receive only the rendered bounded context
plus this selection record, rather than a second copy of all selected entries;
project-improvement consumers receive governed granular facts and never reload
that rendered aggregate.

Project-improvement model output is also incremental. The provider may return
only bounded changed signals, proposed actions, one next decision/goal,
must-satisfy constraints, blocking risks, retained evidence IDs, and a typed
stack-preset patch. Runtime maps this once into the existing
`ImprovementAnalysisMetadata` vocabulary and supplies project/goal/iteration
identity from tool input; malformed, oversized, mixed-state, or unknown-field
payloads use the same bounded deterministic fallback. Full prompt context,
diagnosis, project state, and product judgment are not copied into model output.
Task design accepts one identity-free bounded delta. Runtime owns stable goal
and task IDs, canonicalizes targets against `safe_target_files`, retains only
evidence IDs actually selected into that request, and merges authoritative goal
criteria and validated product constraints. Legacy plural task envelopes may
migrate only their first valid item and cannot control identity.
When runtime checkpointing is enabled, the complete selected payload is stored
once as a checksum-addressed `prompt_context` artifact. The checkpoint-owned
`RuntimePromptContextSnapshot` binds the full request hash, rendered Prompt hash,
selection record, and artifact reference at `context_assembled`. The request
fingerprint includes the memory adapter version, so typed/governed-adapter snapshots
cannot collide with legacy section-adapter snapshots. Exact resume replays the
payload only for an identical request; corrupt or mismatched context
evidence blocks recovery instead of rebuilding from changed memory sources.

When checkpointing is enabled and the initial budget omits at least two older
assistant dialog candidates, the memory builder may trial deterministic segmented
compaction. It keeps at least two recent messages verbatim, extracts bounded
decision/error/validation signal lines from the older prefix, replaces verbose
observations with a length and fingerprint marker, replaces only the older
non-required prefix, and adopts the summary only if it fits completely and
is persisted as a checksum `context_compaction` artifact. Each compacted source
decision links to the artifact candidate; `ContextCompactionRecord` binds the
source fingerprint/IDs, algorithm, summary, and before/after size, while
`ContextCompactionBinding` binds that record to `DurableArtifactReference`.
`RuntimePromptContextSnapshot` carries the bindings and recovery validates every
referenced artifact. Source changes produce a new fingerprint; missing/corrupt
compaction artifacts fail closed. The exact `prompt_context` artifact remains the
replay authority, so the compact artifact is evidence rather than a second prompt.
User dialog, required candidates, and system instructions are never
observation-compacted. Historical `deterministic_dialog_extract_v1` records remain
readable; new memory projections use `deterministic_observation_mask_v1`. If a compactor cannot be
selected completely, assembly atomically falls back to the source candidates.
The opt-in `llm_rolling_summary_v1` projection uses a strict nested summary
payload and separate summary-token evidence; it is accepted only when its
source fingerprint, usage/finish evidence, and recent-suffix fit are validated.
The default remains deterministic observation masking, and invalid or
over-budget generated summaries fall back to that current view.
Recoverable tool-planning prompts apply the same boundary ephemerally to explicit
large observation fields while retaining paths, commands, operation kind, symbol,
mode, errors, and the original typed metadata unchanged.
Recovery uses an explicit safe projection: environment secrets, runtime handles,
arbitrary attributes, unknown fields, and duplicate free-form task/context values
are omitted rather than hashed. A resumed prompt snapshot with a different adapter
request hash fails closed; successful exact replay is consumed once.

Offline context quality uses `ContextQualityExpectation` and
`ContextQualityEvaluation`. The evaluator checks explicit expected-present and
expected-absent candidate IDs plus structural invariants: ready status, character
budget, complete decisions, required representation, duplicate leakage,
governance links, recent-dialog suffix, and compaction links. It emits typed issue
codes and never controls runtime routing or claims semantic relevance. A fixture
corpus covers budget, conflict, duplicate, and compaction behavior.

The section-shaped `ContextAssembler.assemble(payload)` and standalone
`ContextCompressor` have zero production callers and emit deprecation warnings.
They remain only for historical compatibility. Production inventory tests prevent
new callers; `ContextSectionDecision` and `priority_then_recency_v1` remain valid
historical readers, while current assembly and request fingerprints use
`retention_priority_order_v1`. The memory adapter fingerprint is versioned as
`typed_memory_candidates_quality_v4`.

`RuntimeStateMetadata.execution_mode` is the root-task permission authority.
It is either `read_only` or `mutation_allowed`, with an explicit
`execution_mode_source` and human-readable `execution_mode_reason`. Internal
subtasks use the typed `Task.kind` vocabulary. `inspect` / `analysis` subtasks
may execute only read or research needs; `validate` subtasks may execute only
their exact non-empty `validation_command`; and `implement` / `repair` subtasks
must list every permitted `write_files` target before mutation. These contracts
may narrow the shared root mode but must not rewrite it. Unknown explicit task
kinds are rejected instead of silently becoming mutation-capable. Historical checkpoints containing the
legacy `runtime_mode:read_only_analysis` assumption are migrated to typed
read-only state when loaded. Tool-routing denials are retained as typed
`GuardDecisionMetadata` entries in `guard_history` and are execution failures
for required decision needs; an empty selection is not success.

Planned and observed file state are separate contracts. `Task.write_files`
describes intent. `TaskExecutionResult.attributes.observed_modified_files` and
`ExecutionStateMetadata.changed_files` describe successful file-tool side
effects only. A write/implement task without observed mutation evidence, or a
validation task without a successful argv-equivalent execution of its declared
`validation_command`, cannot be marked completed. A successful substitute such
as `compileall` therefore cannot satisfy a requested `pytest` task.

`RuntimeStateMetadata.session_constraints` is the bounded, conversation-scoped
ledger for explicit user constraints that must survive dialog compaction. It
contains typed, source-linked entries for write scope, exact validation
commands, API compatibility, goal/acceptance corrections, or a narrowing
execution mode. A proposal is not authority, even after it is marked
confirmed; an explicit reducer transition must create the active entry.
Revoked entries remain as tombstones so an old dialog summary cannot revive a
constraint. The ledger is checkpointed with `runtime_state`, has no long-term
memory side effect, and is not copied into every task. It is projected into
required, non-truncatable `ContextCandidate` values for model visibility, but
the existing typed task/path/guard/verification contracts remain the only
execution authorities. Free-form `statement` text and compact artifacts cannot
grant or expand permissions.

The production ingress contract is separate from the runtime ledger:
`ConversationIdentity` binds a stable conversation to a per-run checkpoint and
project root; `SessionTurn` carries one source turn; and `SessionIngressState`
holds pending proposals until explicit confirmation. Interactive CLI ingress
now owns this state, standard/enhanced planner and decomposer prompts receive
the active projection, and `RuntimeCheckpointMetadata.session_ingress_state`
round-trips the bounded turn/proposal snapshot. Resume rejects a supplied
ingress state whose identity or constraint snapshot differs from the
checkpoint. The main tool-event loop fails closed before Provider transport if
the complete active constraint projection was removed by prompt budgeting.
The interactive ingress routes `/constraints`, `/confirm`, `/reject`, and
`/revoke` through one reducer; newer same-key pending proposals supersede older
ones, and typed `SessionConstraintLimits` bound proposal/entry counts and
serialized values. Active entries retain the confirmation turn and revoked
tombstones retain the revocation turn; quota violations fail closed.
This establishes offline production wiring; it does not authorize a
full-conversation Provider canary or claim a Token/quality gain.

When a ContextLoader or project-improvement request consumes a derived dialog
projection, `ToolInputMetadata.session_turn_source_hash` records the SHA-256
digest of the authoritative ingress turn ledger. It is replay/evidence metadata
only; raw `SessionIngressState` and typed constraints remain the authority.

Post-core project improvement has a separate typed completion policy:
`ProjectImprovementPolicy(requirement, source, target_successes, max_attempts)`.
`requirement` is `disabled`, `optional`, or `required`. Automatic default
improvement is optional; an explicit CLI/interactive iteration selection is
required; zero disables the stage. `required_successful_improvements` remains a
compatibility view of `target_successes`, not the top-level completion authority.
Runtime state and reports preserve `core_success`, the policy, and the observed
improvement status. Optional failure/interruption keeps overall success when the
core task succeeded and is surfaced as a warning; required failure makes overall
success false without rewriting completed core task/tool evidence.
Each enhancement attempt takes a pre-iteration Git safety snapshot. If execution
or evaluation fails after mutation, only its explicit changed-file set is
restored from that snapshot and `IterationResult` records whether rollback was
applied, restored paths, snapshot reference, and any rollback error. Failed
state is not fed into another automatic repair iteration.

Fast and module-owned project-improvement tool paths serialize the same existing
`ToolCallMetadata`, `ToolContextMetadata`, `ToolErrorMetadata`, and
`ToolExecutionEnvelopeMetadata` used by runtime diagnostics. Each logical
invocation has a unique call ID and one durable start/terminal pair; internal
retries do not inflate logical call counts. Diagnostic-hook failure is isolated
from execution. Registry-backed file, README, and bounded bug-fix mutations also
derive targets from one shared mutation descriptor, require explicit
`Task.write_files` authority, pass the standard edit guard, retain the task's
exact validation command, and participate in `prepared -> observed -> applied`
checkpoint reconciliation. A success result with no observed target diff is
persisted and reported as failure. External command and environment setup side
effects retain their separately documented recovery limits; this contract does
not claim that arbitrary external effects are exactly-once.

Task Executor does not implicitly synchronize `README.md` after a code
improvement. README post-processing is a separate mutation and is executed—and
made part of iteration success—only when the current designed task explicitly
lists the resolved README path among its targets. An unrequested README cannot
be added to `Task.write_files`, consume an edit budget, or turn an otherwise
successful scoped code change into failure.

Localized project-improvement edits resolve symbol evidence in authority order:
an explicit symbol, typed iteration goal, acceptance criteria, then descriptive
task/report text. A layer that names multiple project symbols is ambiguous and
cannot authorize choosing the first symbol in source order; the executor uses a
safer non-localized route instead.

Durable runtime recovery uses `RuntimeCheckpointMetadata` as a separate
contract from mutable `RuntimeStateMetadata`. A checkpoint preserves the stable
run/task/session identity, the complete runtime state and consumed budget, a
named safe boundary, the last durable trajectory event, pending tool-action
state, and a strict non-secret project fingerprint. `RuntimeCheckpointStore`
writes immutable generations under the run trajectory directory, verifies a
SHA-256 content checksum, and atomically advances `latest_checkpoint.json`.
Stale generations are rejected and a corrupt latest generation may fall back
to the preceding valid generation with a warning. The current runtime supports
an explicit `IntelligentAutopilot.resume(run_id, checkpoint_id, context)` path.
Completed sessions finalize through typed `runtime_state_completed`,
`runtime_report_persisted`, and `runtime_finalized` boundaries. The checkpoint-owned
`RuntimeFinalizationCursor` records monotonic stage, outcome, report source hash,
report artifact reference, and the unique completion event ID. An interrupted
finalization resumes without invoking the session executor; an already-finalized
checkpoint returns the persisted report. Legacy `controlled_stop` checkpoints
remain readable but do not claim the new exactly-once evidence. Resume preserves the original run/root-task/
session identity and consumed budgets, creates a separate resume-attempt ID,
and records a typed `RuntimeResumeDecisionMetadata` preflight. The current
operational state is `RuntimeStateMetadata.recovery_status`; preflight separately
records recoverability, recovery mode (including `finalize_from_checkpoint`), automation policy, stable reason code,
typed blockers, fallback, evidence, and remaining budget. Human `reason` and
`next_action` text are display-only; legacy `decision`/`resume_status` remain
compatibility projections. A checkpoint also binds the ready project's
interpreter and content-sensitive environment identity in `ProjectFingerprint`.
Resume rebuilds the in-memory attachment through read-only preflight and blocks
on missing, stale, or mismatched environment evidence. Legacy checkpoints remain
readable, but a legacy pending Python verification also requires a fresh ready
attachment. Resume never creates or installs an environment implicitly; an
authorized setup/resync is a separate action. The checkpoint may own a strict session bootstrap or execution cursor. Both
standard and enhanced-UI sessions persist decomposition, plan hash, completed
result prefix, and next subtask index; exact resume executes only the remaining
suffix. LLM responses and local read results use checksum-addressed recovery
artifacts plus request/call ledgers so an observed result is applied once rather
than fetched again. New `provider_bound_v2` LLM request hashes bind provider,
model, credential-free normalized endpoint (including non-default port),
capability-profile version, and effective reasoning semantics. Legacy unbound
LLM artifacts remain readable evidence but are not replayable. Missing or
corrupt recovery artifacts fail closed. File create/replace/delete calls use a
`prepared -> observed -> applied -> verification_applied` protocol with target
hashes and typed tool input. Resume can detect a write that completed before
the process stopped, avoid replaying it, apply its state/budget exactly once,
and continue verification. When the active tool plan already contains a later
required validation command, the mutation checkpoint stores it as typed
`pending_verification`; an ordered plan records per-command cwd/mode/timeout and
a contiguous progress cursor, so resume executes only the remaining commands
instead of synthesizing a weaker replacement. The recorder explicitly attaches
the replacement process to the checkpoint's existing run/task/session identity
before emitting resume events. External target drift blocks recovery without
overwriting the file. Mutating commands receive prepared/result checkpoints but
are not automatically replayed without a tool-specific reconciliation probe.
A corrupt requested generation can offer a previous valid checkpoint only with
explicit confirmation. A missing checkpoint with no valid generation returns
typed `not_recoverable` plus `terminate_preserving_evidence`, rather than an
unclassified exception or silent new run. Each checkpointed runtime holds a
non-blocking per-run writer lease for its execution lifetime. A concurrent
resume returns typed `run_lease_active + retry_later` without executing or
writing the active trajectory. Trajectory event append also uses a per-run file
lock and recomputes the durable maximum sequence under that lock. A retry may
select an older immutable checkpoint; its state source is recorded in
`resume_source_checkpoint_id`, while new generations continue after the run's
current latest generation.

CLI entry points are explicit:

```bash
openpilot run --once "Inspect project" --checkpointing --project-path /path/to/project
openpilot run --resume-run-id RUN_ID --resume-checkpoint-id CHECKPOINT_ID --project-path /path/to/project
```

When `project_path` is available, path governance now covers both explicit tool
path fields and absolute path fragments embedded inside command strings. For
example, `command_executor` grounds `cwd` plus command arguments such as
`python /workspace/openpilot/src/ui/cli.py` before execution, and blocks
absolute paths that escape the declared project boundary.

### Autonomous Planning Types

`PlanningSurfaceCard` (runtime-internal, prompt-facing only):

- `card_id`
- `source_kind` (`tool` today, future-compatible with skill-backed cards)
- `exposure` (`core`, `deferred`, `hidden`)
- `need_types`
- `summary`
- `required_fields_hint`
- `example_need`
- `trigger_terms`
- `backing_refs`

`PlanningSurfaceCatalog`:

- Built from one or more capability-card providers.
- Today includes tool-backed cards.
- Future skill providers can join the same planning surface without changing the `decision_needs -> ToolRouter` protocol.

`ClarificationQuestion`:

- `field`
- `prompt`
- `reason`
- `default_assumption`

`ClarificationAnswer`:

- `field`
- `answer`

`TaskBrief`:

- `goal`
- `constraints`
- `answers`
- `assumptions`
- `missing_fields`
- `ready_for_planning`

Interactive `openpilot run` may ask clarification questions before planning when
the goal lacks a deadline, deliverables, or other key project details. `--once`
mode does not block for answers; it records default assumptions and includes
them in planner constraints and audit logs.

`TaskCard`:

- `goal`
- `task_type`
- `priority`
- `risk_level`
- `required_resources`
- `expected_deliverables`
- `constraints`

`PlanStep`:

- `id`
- `title`
- `description`
- `risk_level`
- `required_resources`
- `expected_output`
- `dependencies`
- `confirmation_required`

`TaskStatus`:

- `planned`
- `in_progress`
- `blocked`
- `done`
- `skipped`

`TaskNode`:

- `id`
- `title`
- `description`
- `status`
- `risk_level`
- `required_resources`
- `expected_output`
- `dependencies`
- `confirmation_required`

`TimelineSlot`:

- `id`
- `title`
- `task_ids`
- `start_label`
- `end_label`
- `status`

`TimelinePlan`:

- `goal`
- `time_horizon`
- `status`
- `task_tree`
- `timeline`
- `reminder_plan`
- `milestones`
- `notes`

`ReminderItem`:

- `id`
- `task_id`
- `title`
- `remind_at`
- `reason`
- `channel`
- `status`
- `reminder_type`

`ReminderPlan`:

- `goal`
- `items`
- `notes`

Reminder plans are local planning data only. The MVP does not create Windows
notifications, calendar events, emails, background jobs, or external reminders.

`ExecutionPlan`:

- `task_card`
- `steps`
- `fallbacks`
- `confirmation_points`
- `success_criteria`
- `timeline`

The MVP derives `timeline` deterministically from validated `steps`. It creates
planning-only task nodes, timeline slots, and reminder-plan data; it does not
write calendar reminders or execute tools.

### CLI

```powershell
openpilot config check
openpilot plan "用户高层目标"
openpilot plan "用户高层目标" --json
openpilot run
openpilot run --once "用户高层目标"
openpilot run --log-file logs/demo.jsonl
openpilot run --ignore-memory  # OP-04: disable preference retrieval
```

### OpenPilot Validation Log

`openpilot run` provides a modern validation REPL for planning-only workflows. The CLI shows status spinners, the current planning phase, and generated planned steps without executing tools. Users can exit with `exit`, `quit`, or `:q`. The legacy `openpilot openpilot` command remains supported as an alias.

On startup, `openpilot run` prints a Rich header panel and API setup guidance when config is incomplete:

- create or edit `Code/.env`;
- set `OPENPILOT_LLM_BASE_URL`;
- set `OPENPILOT_LLM_API_KEY`;
- set `OPENPILOT_LLM_MODEL`;
- never commit real API keys.

If `OPENPILOT_LLM_BASE_URL` or `OPENPILOT_LLM_API_KEY` is blank or missing, the REPL
prints `WARNING: LLM config incomplete: ...` before each prompt. This warning is
non-blocking; planning failures should still be logged as `planner_failed`.

Default log file:

- `Code/logs/openpilot.jsonl`

Each JSONL event includes:

- `timestamp`
- `session_id`
- `turn_id`
- `event_type`
- `payload`

Event types:

- `goal_received`
- `clarification_started`
- `clarification_answered`
- `clarification_completed`
- `memory_retrieved` (OP-04: records retrieved memories and reuse notes)
- `planner_started`
- `planner_succeeded`
- `reminders_planned`
- `planner_failed`

`planner_succeeded` stores the validated task card, planned executable steps,
derived timeline, task brief or assumptions when present, final risk level,
risk-policy marker, reminder plan, confirmation points, fallbacks, success criteria,
and `memory_reuse_notes` (OP-04: explains which preferences were applied).
`reminders_planned` stores the same local reminder plan as its own event.
`memory_retrieved` (OP-04) stores retrieved memories, their confidence scores, and reuse notes.
Logs must not include API keys, environment variables, or secrets.

## 3. Tool Plugin Registration

Each tool should be registered with the following fields:

```yaml
name: example_tool
description: What the tool does.
version: 0.1.0
permission_level: auto | notify | confirm | forbidden
input_metadata_type: ToolInputMetadata
output_metadata_type: ToolResultMetadata
contract_metadata:
  kind: tool_contract
  required_input_fields: []
  input_defaults: {}
failure_modes:
  - timeout
  - auth_required
  - invalid_input
fallbacks:
  - alternative_tool
audit_log: true
```

Permission levels:

- `auto`: may run automatically and must log the action.
- `notify`: may run after notifying the user or according to user-configured rules.
- `confirm`: must ask for explicit user confirmation before execution.
- `forbidden`: must be blocked by default, except in a sandbox or explicit development override.

## 4. Memory Types

### Short-Term Memory

Current task context, active plan, intermediate observations, temporary files, and recent tool outputs.

### Long-Term Memory

Stable user preferences, long-term goals, recurring constraints, preferred output formats, and trusted sources.

**OP-04 Preference Reuse Implementation:**
- Each memory record includes a `confidence` score (0.0-1.0) and `usage_count`.
- High-confidence preferences (≥0.7) are automatically injected as constraints during planning.
- Low-confidence preferences (<0.7) are retrieved but not auto-applied; they require user confirmation.
- Successfully applied preferences increment their `usage_count` and update `last_used` timestamp.
- Use `--ignore-memory` CLI flag to disable preference retrieval for a specific run.

### Task Memory

Historical task plans, execution traces, results, user feedback, failure causes, and recovery strategies.

### Skill Memory

Reusable workflows, scripts, prompt templates, tool chains, GUI operation templates, and verified procedures.

## 5. Permission Policy

| Risk Level | Default Handling | Examples |
| --- | --- | --- |
| Low | Execute automatically and log | Search, summarize, read approved files, draft content |
| Medium | Notify before execution or follow user rule | Search, batch download, consume paid model quota, create local files |
| High | Require explicit confirmation | Send email, delete files, modify calendar, access sensitive accounts |
| Forbidden | Block by default or sandbox only | Payments, system setting changes, unknown code execution, production data mutation |

The MVP planner applies deterministic keyword safeguards after LLM validation so obvious medium, high, or forbidden operations cannot be silently downgraded.

### Experimental SWE-bench execution boundary

The isolated `experiments/mini_swe_active_iteration` package is not an OpenPilot
production tool. Its 12-task core-benefit screen keeps review-plane calls and
task-arm execution separate. A task arm is forbidden unless a hash-bound
`ScreenExecutionProtocol` authorizes it and validates against the frozen Stage A
manifest. When authorized, each arm must use a fresh pinned SWE-bench image with
`network_mode=none`, no host mount, a bounded command path, and an arm-blind,
network-isolated evaluator. Public receipts may contain a model-patch hash, but
not the patch body, hidden evaluator inputs, or review rationale.

## 6. MVP Interface Contract

The first MVP focuses on personal task progress assistance.

Minimum flow:

1. Receive a future project or task goal from the user.
2. Clarify missing deadline, deliverable, priority, availability, dependency, or scope details when needed.
3. Generate a task card, executable steps, task tree, and timeline.
4. Identify deadlines, dependencies, resources, risk, and confirmation points.
5. Produce reminder-plan data and task-log-ready structured output.
6. Ask for confirmation before external sending, account login, bulk file writes, or high-risk GUI actions.
7. Preserve research reports as one supported task type, not the only MVP path.
8. Later phases add real reminders, task logs, daily/weekly reports, and authorized auto-actions.

Minimum deliverables:

- Task tree and timeline.
- Reminder-plan data.
- Execution/planning log.
- Memory update proposal.
- Risk confirmation record when applicable.

## 7. Update Rules

- Update this file when adding a new module, tool type, permission rule, memory category, or external integration.
- Keep this file implementation-facing and concise.
- Do not store secrets, API keys, private credentials, or personal user data in this file.

## 8. Autopilot Tool Execution Updates

Autopilot execution supports local document-summary workflows in addition to
planning. A typical completion-report summary chain is:

1. `directory_lister` lists matching files in a local directory.
2. `multi_file_reader` reads the matched files and combines their text.
3. `llm_summarizer` receives the combined text through typed tool input
   metadata and generates the summary.

Built-in local tools:

- `directory_lister`
  - Capability: `file_read`
  - Permission: `low`
  - Inputs: `directory_path`, optional `pattern`, `recursive`, `max_files`
  - Default report pattern: `*完成报告*.md`
  - Output: `files`, `count`, `total_count`, `truncated`
- `multi_file_reader`
  - Capability: `file_read`
  - Permission: `low`
  - Inputs: `file_paths` or `directory_path` plus optional `pattern`
  - Output: combined `content`, `files`, `count`, `truncated`

Execution input chaining:

- Tool selections declare `input_metadata` and optional dependencies.
- The workflow executor routes upstream outputs by metadata kind and the
  declared input/output metadata types.
- File artifacts can feed file-reading tools.
- Text and code artifacts can feed summarization, review, execution, or writing
  tools when those tools declare compatible input metadata.

Workflow execution logs now include `step_results` for each tool call:

- `step_id`
- `tool`
- `status`
- `success`
- `error`
- `input_keys`
- `input_resolution`
- `output_summary`
- `output_preview`
- `duration_seconds`

Workflow execution logs also include:

- `planned_steps`: the validated planner steps, including titles, descriptions,
  expected output, dependencies, risk level, and confirmation flags.
- `tool_selections`: the orchestration output for each step, including selected
  tool, input metadata keys, compact input preview, dependencies, selection
  reason, confidence, and confirmation flag.

Autopilot writes diagnostic events before the final workflow summary:

- `workflow_plan_generated`
- `tool_orchestration_planned`
- `tool_execution_result`
- `workflow_execution`

Before invoking a built-in tool, the workflow executor validates required
inputs. Missing required inputs produce a failed `ExecutionResult` with
`error.type = "MissingRequiredInput"` instead of allowing a raw `KeyError`.
For `llm_summarizer`, missing `text` records whether a compatible upstream
metadata output was available and whether the input chain was unresolved.

Workflow success must reflect actual execution. Non-dry-run workflows are
successful only when at least one tool ran, every execution result succeeded,
and all available validation results passed.

### Autopilot log routing and final-report behavior

- Autopilot workflow diagnostics use the same log file as the interactive CLI:
  `Code/logs/openpilot.jsonl`.
- `Code/logs/workflow.jsonl` is no longer the primary Autopilot diagnostic log.
- `openpilot run` clears the selected log file once at startup. The default
  selected log is `Code/logs/openpilot.jsonl`; `--log-file` selects and clears a
  different log file.
- `/autopilot` and `/execute` receive the active CLI logger, so their
  `workflow_plan_generated`, `tool_orchestration_planned`,
  `tool_execution_result`, and `workflow_execution` events are written to
  `openpilot.jsonl`.
- Successful `llm_summarizer` output is not printed verbatim in the CLI. The CLI
  shows step status and concise errors; bounded `output_preview` and
  `output_summary` fields are written to the JSONL log for diagnostics.
- Final report generation is an LLM summarization step unless the user explicitly
  requests persistence with a concrete output file path or filename. In that
  explicit-save case, `file_writer` receives content from the latest compatible
  text/code result metadata.

### LLM semantic analysis

- Goal understanding and plan-step tool orchestration use LLM semantic analysis
  instead of keyword-based positive classification.
- `SemanticAnalyzer.analyze_goal(goal, constraints)` returns task type, risk,
  resources, deliverables, intent, confidence, and reason as strict JSON.
- `SemanticAnalyzer.analyze_plan_step(goal, step, available_tools)` returns the
  operation type, capability, preferred tool, write/mutation flags, source kind,
  confidence, and reason as strict JSON.
- Deterministic code is still allowed for path extraction, glob matching, schema
  validation, required-input checks, and safety blocking. It must not be used to
  positively classify the user intent or step semantics.
- Autopilot logs `semantic_goal_analysis`, `semantic_step_analysis`, and
  `semantic_analysis_failed` events to `Code/logs/openpilot.jsonl`.
- If an LLM summary tool returns empty text, the executor logs
  `empty_output_retry`, retries once with a shorter payload, and then fails the
  current step with `EmptyLLMOutput` if the retry is also empty.
- Input-resolution diagnostics include `source_text_empty` so a present-but-empty
  upstream metadata output is distinguishable from a missing compatible source.
