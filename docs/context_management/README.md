# Unified Context Assembly

This directory owns the staged delivery plan and acceptance evidence for the
project-wide model-facing context assembly boundary.

The work is intentionally staged. Each phase has its own plan, tests, evidence,
and exit gate. A later phase must not start by assuming an earlier phase passed.

## Goal

All production LLM request paths use one typed, deterministic, budgeted,
auditable, and recoverable context assembly boundary while business modules
retain ownership of their instructions and source facts.

## Phases

| Phase | Scope | Gate |
| --- | --- | --- |
| 0 | Inventory and acceptance boundary | Every production request path and existing contract is classified |
| 1 | Typed candidates, policy, and result | No control behavior depends on free-form section dictionaries |
| 2 | Full-request budget envelope | Fixed instructions/tool schemas and context share one explicit input budget |
| 3 | Production entry migration | Every classified production request path uses the assembly boundary or has a documented exclusion |
| 4 | Recovery and end-to-end acceptance | Artifact replay, interruption recovery, real-provider evidence, and full regression pass |
| 5A | Correctness boundaries | Control instructions and structured owner payloads cannot be silently corrupted; budget failure remains typed |
| 5B | Typed memory adapter | Memory, dialog, project, and environment sources receive candidate-level selection evidence without changing source ownership |
| 5C | Typed source governance | Exact duplicate, explicit stale, and explicit conflict evidence is handled deterministically without semantic guessing |
| 5D | Artifact-backed compaction | Budget-omitted older dialog can be replaced by a source-linked checksum artifact while recent dialog stays verbatim |
| 5E | Quality and legacy convergence | Offline typed quality fixtures protect selection behavior and production legacy callers remain at zero |
| 6 | Segmented compaction | Large old observations are deterministically masked behind source-linked artifacts while current control and recovery evidence stays exact |
| 7 | In-session Session Constraint State | Explicit, confirmed conversation constraints survive compaction as bounded required context and are checked at runtime boundaries |

Phases 0-5E are complete. Phase 5B was delivered under
`PHASE_5B_TYPED_MEMORY_ADAPTER_PLAN.md`; its plan was recorded before behavior
or test changes. The current baseline remains 22/22 production purposes
migrated, one executable request constructor, real-provider usage captured,
process recovery verified, and `Code` full suite `680 passed`.

Phase 5C was delivered under `PHASE_5C_SOURCE_GOVERNANCE_PLAN.md`; its plan and
metadata impact note were recorded before test or behavior changes.

Phase 5D was delivered under `PHASE_5D_ARTIFACT_COMPACTION_PLAN.md`; its plan and
metadata impact note were recorded before test or behavior changes.

Phase 5E was delivered under `PHASE_5E_QUALITY_AND_LEGACY_CONVERGENCE_PLAN.md`; its
plan and metadata impact note were recorded before test or behavior changes.

Phase 6 is delivered under `PHASE_6_SEGMENTED_COMPACTION_PLAN.md`. Its offline
long-trajectory gates passed; provider and real-task admission remain a separate
reviewed canary rather than an implied consequence of offline savings.

Phase 7 is delivered under `PHASE_7_SESSION_CONSTRAINT_STATE_PLAN.md`. Its
typed, source-linked state preserves explicit in-session constraints through
compaction, while runtime enforcement remains owned by task/path/verification
contracts. The offline three-arm replay passed; no cross-session memory write
or real-task canary is implied.

## Non-goals

- A universal business Prompt template.
- Moving source ownership out of memory, project, runtime, tool, or agent modules.
- Treating provider usage as pre-request selection evidence.
- Adding summarization, embeddings, or a generalized relationship graph without
  separate production evidence.

## Completion rule

The goal is complete only when all phase plans have recorded passing evidence,
the implementation log is current, no production LLM path silently bypasses the
classified boundary, and remaining exclusions are explicit and tested.
