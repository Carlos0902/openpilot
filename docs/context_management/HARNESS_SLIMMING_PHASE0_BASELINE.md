# Harness slimming phase 0: clean-checkout baseline

Status: inventory only; no runtime, metadata, or test change.

This document records a reproducible baseline from a clean detached worktree at
`bb3fdba7ef91c2d9e7eebfb70e4598d21fb343d8` (the `HEAD` used for this phase).
The main checkout was intentionally not used because it contained unrelated
dirty edits and deleted/added experiment files.

## Reproduction

From the repository root, create a clean worktree from the recorded commit and
run the following read-only scan. `utf-8-sig` is intentional: one source file
contains a BOM. The scan parses every Python file under `Code/src`, reports all
attribute calls named `complete`, and reports both bare and qualified
`LLMRequest(...)` constructions.

```sh
git worktree add --detach /tmp/openpilot-phase0-baseline HEAD
cd /tmp/openpilot-phase0-baseline
python - <<'PY'
import ast
from pathlib import Path

root = Path("Code/src")
complete = []
requests = []
for path in sorted(root.rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr == "complete":
            complete.append((path, node.lineno, ast.unparse(function)))
        if (isinstance(function, ast.Name) and function.id == "LLMRequest") or (
            isinstance(function, ast.Attribute) and function.attr == "LLMRequest"
        ):
            requests.append((path, node.lineno, ast.unparse(function)))
print(f"complete_calls={len(complete)}")
for row in complete:
    print("COMPLETE", *row, sep=" | ")
print(f"llm_request_constructors={len(requests)}")
for row in requests:
    print("REQUEST", *row, sep=" | ")
PY
```

The same result can be spot-checked without Python with:

```sh
rg -n --glob '*.py' 'LLMRequest\s*\(|\.complete\s*\(' Code/src
```

At the recorded commit the AST output is `complete_calls=26` and
`llm_request_constructors=1`. The constructor is the canonical assembly point
in `Code/src/memory/context_assembly/request_builder.py:172`; the other
production modules construct requests through that builder or receive a
request from an upstream owner.

## Request-call accounting

The 26 direct `.complete(...)` calls are not 26 independent prompt owners.
They partition as follows:

| Classification | Calls | Locations / reason |
| --- | ---: | --- |
| Assembly/owner paths | 22 | The 22 request purposes listed in `PHASE_0_INVENTORY_PLAN.md`; several purposes share a source owner, and the two length/empty-response retries are included as their owning purpose. |
| Instrumentation wrapper | 2 | `core/instrumented_llm.py:34,66`; delegates to `super().complete` and only adds progress/telemetry. |
| Provider-native transport roundtrip | 1 | `core/provider_tool_roundtrip.py:1151`; sends the already-built request after admission and state checks; it is not a second assembly owner. |
| Compatibility summary path | 1 | `memory/rolling_summary_factory.py:100`; invokes the request built by `request_builder` with bounded retry and records summary evidence. |
| **Total** | **26** | Every AST match is assigned above. |

`core/tool_event_loop.py:1027` is a compatibility helper that calls a supplied
`complete` callable, but it is not an AST `.complete(...)` match and does not
construct a request. `ProjectEvaluatorAgent`'s `generate`/`chat`/callable
branches are likewise compatibility adapters, not additional provider request
owners.

## Canonical 22-purpose inventory

The following is the purpose-level inventory used by phase 0. It is copied as a
stable baseline from `PHASE_0_INVENTORY_PLAN.md`; the source locations and
dynamic inputs are the migration boundary for later phases.

| Family | Owner / purpose | Dynamic input summary | Budget/recovery risk |
| --- | --- | --- | --- |
| Agent definition | `slot_generator._generate_slot_payload` | user task | no input budget/checkpoint |
| Agent definition | `slot_generator._repair_slot_language` | task, language, generated slots | no input budget/checkpoint |
| Semantic routing | `SemanticAnalyzer.analyze_goal` | goal, constraints | timeout; deterministic fallback |
| Semantic routing | `SemanticAnalyzer.analyze_plan_step` | goal, tools, step | timeout; deterministic fallback |
| Tool planning | `ToolEventLoopRunner.run` | accumulated decision context | bounded rounds, unbounded input |
| Tool planning | `ToolPlanningExecutor._retry_empty_decision_plan` | plan and typed failure evidence | output cap; one retry |
| Task design | `ExecutionTaskDecomposer._estimate_complexity` | task description | output cap 10; fallback 0.5 |
| Task design | `ExecutionTaskDecomposer._generate_decomposition` | task and context dict | output cap 2,000 |
| Iteration | `AutonomousIterationAgent` goal maker | project/evaluation/improvement state | memory projection; outer request unbounded |
| Iteration | `AutonomousIterationAgent` task designer | goal/project/report | memory projection; outer request unbounded |
| Iteration | `project_improvement_tool_executor` | previews, rubric, diagnosis | local preview truncation |
| Evaluation | `ProjectEvaluatorAgent._call_llm_text` | runtime output judgment | tail truncation; output cap 300 |
| Artifact generation | `CodeGenerator._call_llm` | task, constraints, context | timeout; compatibility client |
| Artifact generation | `TaskExecutor._generate_text_file_content` | goal/evaluation/report/current file | no input budget |
| Artifact generation | `code_unit_generator._call_llm` | task, symbol, surrounding context | no input budget; compatibility client |
| Artifact generation | `code_editor._call_llm` | task, scope, surrounding context | no input budget; compatibility client |
| Repair | `bug_fix_tool._request_fix` | command evidence, attempts, allowed files | output cap 6,000; input-overflow risk |
| Memory transform | `ContextCompressor._generate_summary` | complete conversation | heuristic target/output cap |
| Tool transform | `llm_summarizer._complete_summary` | user text/instruction | output retry accounting |
| Research | `web_searcher._llm_search_query_variants` | user query | output cap 220 |
| Research | `web_searcher._select_redirect_links_with_llm` | results/pages/candidates | output cap 300; local caps |
| Research | `web_searcher._clean_with_llm` | result/page excerpts | output cap 900; local caps |

The 22 purposes occupy 18 owner/source locations after grouping retries and
compatibility branches under the request that owns their prompt. This is the
`22 purposes / 18 source-call locations` figure in the phase plan; it must not
be read as 22 constructors or as permission to delete wrappers.

## Boundary and metadata review

The inventory checked the request builder, `LLMRequest`, runtime pending-request
checkpoint fields, context assembly, provider roundtrip admission, and
instrumentation. The closest existing contracts remain:

* `ContextSelectionMetadata` for a derived selection and budget decision;
* `ContextSectionDecision` for the existing five-section aggregate;
* `RuntimePromptContextSnapshot` for checkpoint/replay of a selected payload;
* `LLMRequestMetadata` / `LLMResponseMetadata` for request trajectory facts;
* `DurableArtifactReference` for persisted artifact identity.

No new metadata owner is justified by this inventory. A later phase may extend
the existing selection contract with strictly typed nested candidate decisions,
but must first add contract tests and a metadata impact note. Prompt text is a
projection and must not become a second authority for scope, budget, validation,
recovery, identity, or completion.

## Evidence, limits, and next gate

Evidence for this baseline is the clean-worktree AST output above plus the
purpose matrix in the phase plan. It covers production source only; tests,
health checks, provider fakes, and intentionally unassembled probes are not
counted as production owners. Static AST cannot prove runtime reachability or
provider call frequency, so later paired experiments must use request telemetry
and preserve failures.

The pre-behavior baseline recorded by the phase plan is `613 passed` for the
Code suite. This documentation-only change does not rerun or alter that suite.
Phase 1 may proceed only with a separately frozen treatment, contract tests,
provider/evaluator configuration, and a clean baseline checkout.
