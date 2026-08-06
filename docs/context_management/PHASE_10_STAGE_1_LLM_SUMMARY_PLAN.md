# Phase 10 Stage 1 Plan: LLM Summary Contract and Offline Quality Gate

## Status

Plan and implementation complete. This stage did not enable a production
summary caller or change the Compact feature flag.

## Problem

The repository already has deterministic segmented compaction and an artifact
source-of-truth boundary. The legacy `ContextCompressor` is not safe to wire
into that path: it accepts a whole conversation, uses approximate limits,
returns free text, and has no source fingerprint, quality contract, or atomic
selection evidence.

This stage defines the smallest safe contract for a future LLM-assisted rolling
summary and builds an offline quality corpus. It does not yet add a live
Provider call to production runtime.

## Scope

### In scope

- Reuse and minimally extend `ContextCompactionRecord`/
  `ContextCompactionBinding` only if the existing fields cannot express the
  summary evidence.
- Define an explicit summary algorithm/schema version distinct from the
  existing deterministic algorithm literals.
- Define eligible and forbidden source candidate kinds/roles.
- Define a narrow structured summary schema with no authority-bearing fields.
- Define static and dynamic summary token ceilings.
- Define validation outcomes, provider-attempt evidence, and deterministic
  source fallback reasons.
- Add an offline/fake-provider fixture matrix and structural quality evaluator.

### Out of scope

- production Provider transport;
- changing the Compact default or Current fallback;
- summarizing user constraints, task permissions, validation commands, or
  security instructions;
- generalized embeddings or a memory graph;
- changing reasoning policy or completion reservation;
- semantic quality claims beyond explicit fixture expectations.

## Proposed summary shape

The summary is a derived, bounded projection. Its allowed fields are:

```json
{
  "goal_delta": "short statement of what changed",
  "verified_facts": ["fact IDs or bounded fact strings"],
  "decisions": ["decision IDs or bounded decision strings"],
  "open_issues": ["unresolved issue IDs or bounded issue strings"],
  "evidence_ids": ["source artifact/candidate IDs only"],
  "next_action": "one bounded next action"
}
```

The schema must reject fields that could become execution authority, including
write scope, permissions, validation commands, execution mode, or a new task
contract. Those facts remain owned by their existing typed contracts.

## Budget contract

The summary request receives its own bounded budget:

```text
summary_budget = min(
    static_summary_cap,
    remaining_prompt_budget
      - required_context_reserve
      - recent_suffix_reserve
      - response/schema_reserve
)
```

The summary output is generated with a hard `max_summary_tokens` ceiling and is
counted with the configured provider tokenizer when available. A summary that
is empty, invalid, overlong, lacks required lineage, or cannot be selected
atomically is rejected. The assembler then restores the governed source view;
it does not partially truncate the summary into an unverified replacement.

## Eligible source policy

The first schema may summarize only old, non-required assistant/tool
observations already selected for compaction. It must exclude:

- active session constraints and all user-origin authority candidates;
- current task/goal, instructions, schemas, permissions, and verification
  plans;
- recent dialog suffix retained verbatim;
- secrets, runtime handles, unknown attributes, and untrusted recovery fields.

No eligible segment or no positive budget gain is a deterministic no-op.

## Metadata impact note

```text
Fact: validated rolling summary for selected old non-required observations
Authoritative producer: summary pipeline after schema, budget, lineage, and
  quality validation
Consumers: ContextAssembler, ContextProjection, replay, trajectory audit
Lifecycle: artifact plus request-scoped derived candidate
Control impact: context budget, recovery, and evidence only
Existing contracts reviewed: ContextCandidate, ContextCompactionRecord,
  ContextCompactionBinding, DurableArtifactReference, ContextSelectionMetadata,
  LLMRequestMetadata, LLMResponseMetadata
Decision: reuse/extend existing compaction contracts; no new MetadataKind
Why no duplicate source of truth is created: raw candidates and exact artifacts
  remain authoritative; the summary links source IDs and fingerprints
Serialization and migration: preserve deterministic v1-v5 algorithms; add
  optional versioned summary evidence with historical-read defaults
Tests: schema strictness, forbidden authority fields, budget boundaries,
  source mutation, missing/unknown usage, invalid/overlong/empty output,
  atomic binding, replay hash, quality corpus, zero side effects
Documentation updates: this plan, Phase 10 master plan, metadata catalog/API
  only if public fields change, context README, implementation log
```

## Offline fixture matrix

The corpus must include the Cartesian minimum:

| Dimension | Values |
| --- | --- |
| History length | short, medium, long |
| Old-segment relevance | strong, partial, irrelevant |
| Purpose | iteration goal, task design, code generation, bugfix |
| Summary outcome | valid, empty, malformed, overlong, unknown usage |
| Source state | unchanged, changed, artifact missing |

Each fixture must assert:

- required constraint recall is complete;
- summary contains only allowed fields and source IDs;
- recent dialog and exact task/verification facts are unchanged;
- omitted source decisions link to the selected summary atomically;
- source changes invalidate the summary;
- invalid summary falls back to deterministic source selection;
- prompt and summary stay within token/character budgets;
- no Provider, network, project, memory, or file mutation occurs.

## Exit gate

Stage 1 is complete only when:

1. the strict summary contract and source policy are documented;
2. the fixture corpus and evaluator pass offline;
3. all failure paths are typed and fail closed;
4. the legacy `ContextCompressor` remains without a production caller;
5. no Compact/reasoning/completion production defaults changed;
6. evidence is recorded in `IMPLEMENTATION_LOG.md`.
