# Phase 3 Plan: Production Entry Migration — Completed

## Objective

Migrate every phase-0 production request purpose to the typed provider-ready
assembly boundary while preserving each business module's instructions, output
format, retry semantics, and fallback behavior.

## Batch gates

| Batch | Scope | Independent plan |
| --- | --- | --- |
| 3A | Semantic routing, task decomposition, iteration design, tool planning, evaluation | `PHASE_3A_ORCHESTRATION_PLAN.md` |
| 3B | Code/text generation, scoped editing, bug repair | written before batch 3B code changes |
| 3C | Memory/tool transforms, research, agent slot generation | written before batch 3C code changes |

Each batch must pass its targeted tests and full regression before the next
batch begins.

## Shared migration mechanism

- Add a typed `ContextRequestPurpose` enum covering the phase-0 inventory.
- Add a message adapter that converts existing `LLMMessage` values into typed
  candidates and uses provider settings for tokenizer/budget binding.
- Preserve complete messages when they fit. System instructions are required
  and non-truncatable by default; user/assistant messages are required but have
  explicit head/tail policies selected by the owner.
- Attach one `ContextSelectionMetadata` to each resulting `LLMRequest`.
- Compatibility `generate`, `chat`, or callable clients receive already
  assembled messages/text; they do not bypass selection.

## Coverage enforcement

The phase-0 purpose inventory is authoritative. Completion requires a testable
migration registry mapping every purpose to its owner and batch. Direct
production `LLMRequest` construction is allowed only inside the shared request
builder or an explicitly documented provider/health probe exclusion.

## Exit gate

- All 22 request purposes are migrated or have a documented, tested exclusion.
- Prompt/output/fallback contract tests remain green.
- No mutating tool gains broader permissions or file scope.
- Full suite passes after each batch.

## Completion evidence

- 3A orchestration/control full suite: `637 passed`.
- 3B mutation-adjacent full suite: `643 passed`.
- 3C transform/research full suite: `651 passed`.
- All 22 typed purposes are registered and migrated; no undocumented production
  exclusion remains.
