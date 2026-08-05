# Phase 0 Plan: Inventory and Acceptance Boundary — Completed

## Objective

Establish the complete current-state map before changing runtime behavior.
Classify every production LLM request path, context source, budget owner,
Metadata producer/consumer, and checkpoint relationship.

## Inputs to inspect

- `Code/src/metadata/`, public exports, and `docs/metadata/CONTRACT_CATALOG.md`;
- every production construction of `LLMRequest` and call to `complete`;
- `MemoryContextBuilder`, `ContextAssembler`, runtime controller, diagnostics,
  checkpoint artifacts, and provider usage accounting;
- tests and documentation that assert Prompt shape or request identity.

## Work items

1. Build an entrypoint matrix containing owner, request purpose, input shape,
   fixed instructions, dynamic context, tool schema, budget behavior, recovery
   behavior, and migration risk.
2. Record the closest existing Metadata for candidate identity, budget,
   selection, artifact lineage, and provider usage.
3. Separate production request paths from test fakes, health checks, and
   intentionally unassembled provider probes.
4. Define measurable phase 1–4 exit criteria from the observed inventory.
5. Write the Metadata impact decision before adding any field or model.

## Tests and evidence

- Static inventory queries must be reproducible with repository search.
- The matrix must account for every production `LLMRequest` construction and
  direct `complete` call, including wrappers and retries.
- Existing baseline remains `613 passed` before behavioral changes.

## Exit gate

- No unexplained production LLM request path remains.
- Reuse/extend/new-contract decisions name existing alternatives.
- The next phase has a bounded input/output contract and migration sequence.

## Rollback

This phase changes documentation only. Remove the incomplete inventory if it
cannot be reconciled; do not encode uncertain classifications in runtime code.

## Completed inventory

The repository contains 22 production request purposes across 18 source-call
locations. `InstrumentedLLMClient.complete` and `TrajectoryLLMClientProxy.complete`
are transport/observation wrappers and are not separate assembly owners.

| Family | Owner / purpose | Dynamic inputs | Current budget/recovery classification |
| --- | --- | --- | --- |
| Agent definition | `slot_generator._generate_slot_payload` | user task | no input budget; no checkpoint binding |
| Agent definition | `slot_generator._repair_slot_language` | task, language, generated slots | no input budget; no checkpoint binding |
| Semantic routing | `SemanticAnalyzer.analyze_goal` | goal, constraints | timeout only; deterministic fallback |
| Semantic routing | `SemanticAnalyzer.analyze_plan_step` | goal, tools, step | timeout only; deterministic fallback |
| Tool planning | `ToolEventLoopRunner.run` | accumulated decision Prompt | bounded rounds, not bounded input |
| Tool planning | `ToolPlanningExecutor._retry_empty_decision_plan` | plan and typed failure evidence | output cap only |
| Task design | `ExecutionTaskDecomposer._estimate_complexity` | task description | output cap 10; no input budget |
| Task design | `ExecutionTaskDecomposer._generate_decomposition` | task and arbitrary context dict | output cap 2,000; no input budget |
| Iteration | `AutonomousIterationAgent._complete_json` / goal maker | project state, evaluation, improvement report | bounded memory projection may be nested; outer request unbounded |
| Iteration | `AutonomousIterationAgent._complete_json` / task designer | goal, project state, report | bounded memory projection may be nested; outer request unbounded |
| Iteration | `project_improvement_tool_executor` | project previews, rubric, diagnosis | local preview truncation only |
| Evaluation | `ProjectEvaluatorAgent._call_llm_text` | runtime stdout/stderr judgment | manual tail limit plus output cap |
| Artifact generation | `CodeGenerator._call_llm` | task, constraints, additional context | timeout only; compatibility clients supported |
| Artifact generation | `TaskExecutor._generate_text_file_content` | goal, evaluation, report, complete current file | no input budget |
| Artifact generation | `code_unit_generator._call_llm` | task, symbol, surrounding context | no input budget; compatibility clients supported |
| Artifact generation | `code_editor._call_llm` | task, current scope, surrounding context | no input budget; compatibility clients supported |
| Repair | `bug_fix_tool._request_fix` | command evidence, attempts, complete allowed files | output cap 6,000; high input-overflow risk |
| Memory transform | `ContextCompressor._generate_summary` | complete conversation | heuristic target and output cap; no exact input budget |
| Tool transform | `llm_summarizer._complete_summary` | user text/instruction | output retry accounting; no input budget |
| Research | `web_searcher._llm_search_query_variants` | user query | output cap 220 |
| Research | `web_searcher._select_redirect_links_with_llm` | results/pages/candidates | output cap 300; local candidate caps |
| Research | `web_searcher._clean_with_llm` | result/page excerpts | output cap 900; local excerpt caps |

### Existing contract decision

Closest contracts reviewed:

- `ContextSelectionMetadata` owns one derived selection decision and is the
  correct existing parent for selection/budget evidence.
- `ContextSectionDecision` aggregates the five current memory sections but
  cannot identify cross-module candidates or their typed retention policy.
- `RuntimePromptContextSnapshot` owns checkpoint replay of one selected payload;
  it must remain the recovery owner rather than being copied into a new model.
- `LLMRequestMetadata` and `LLMResponseMetadata` own request/response trajectory
  facts; provider `usage` remains the complete-request post-call authority.
- `DurableArtifactReference` already represents persisted content identity.
- `LLMRequest` is the provider-neutral executable request and currently carries
  no assembly evidence.

Phase 1 may extend `ContextSelectionMetadata` with strict nested candidate
decisions and define strict nested request-policy/candidate values. It must not
create another public Metadata owner unless contract tests demonstrate that an
independent cross-boundary lifecycle is required. Candidate source identity
must reuse an existing ID or artifact reference where one exists; content is a
model-facing projection, not a second authoritative copy.

### Migration batches

1. Orchestration/control paths: semantic routing, decomposition, iteration goal
   and task design, tool planning.
2. Artifact mutation paths: code generation/editing, text replacement, bug fix.
3. Transform/research paths: compressor, summarizer, web query/link/cleanup,
   agent slot generation.
4. Transport enforcement: production `LLMRequest` calls carry assembly evidence;
   wrappers observe it but do not reassemble it.

Compatibility `generate`, `chat`, and callable-client branches are not separate
Prompt owners. They must receive the already assembled rendered input or be
documented as unsupported when exact request evidence cannot be preserved.

## Phase 0 evidence and result

- Reproducible AST inventory accounts for every `LLMRequest(...)` construction
  and `.complete(...)` call under `Code/src`.
- Baseline before phase 1: `Code` full suite `613 passed`.
- Result: phase 0 exit gate passed; no production request path remains
  unclassified. Phase 1 must begin with its own plan and contract tests.
