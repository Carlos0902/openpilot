# Phase 5A Plan: Context Correctness Boundaries

## Status

Completed. This plan was written before phase 5A behavior or test changes.

## Objective

Close the correctness gaps left after the unified request boundary migration:

1. fixed control instructions must never be silently shortened;
2. character truncation must not turn a structured owner payload into an invalid
   request while reporting `assembly_status=ready`;
3. typed context-budget failure must remain distinguishable from provider and
   response-parsing failure at the business-owner boundary;
4. compatibility behavior must remain deterministic and must not weaken the
   existing provider-before-send guard.

This phase does not yet migrate the five-section `MemoryContextBuilder` to
candidate-level assembly. That source-adapter migration is phase 5B and requires
its own plan before implementation.

## Observed failures

- The legacy five-section adapter may partially retain `system_prompt` and return
  a ready result. A 1,400-character instruction was reduced to 239 characters
  under a 256-character budget.
- `ContextRequestBuilder.build_messages()` maps an entire user message to one
  head-truncatable candidate. A JSON semantic payload can therefore be cut into
  invalid JSON while the request remains ready.
- Some business owners catch `Exception` around assembly and provider execution,
  converting `ContextAssemblyBudgetError` into an ordinary empty/fallback result
  with no typed diagnostic distinction.

## Invariants

- A system-role candidate remains `required + truncation=forbidden`.
- A structured payload is either preserved as a complete candidate, projected by
  its owner into independently legal candidates, or rejected before provider
  transport. Raw string slicing must not claim that malformed structured input is
  ready.
- `ContextAssemblyBudgetError` is terminal for the current request shape. It is
  not retried as a provider transport error and is not hidden as a parse failure.
- Existing deterministic business fallbacks may remain, but their diagnostic
  evidence must name context budget insufficiency.
- No permission, runtime budget, checkpoint, or provider-usage authority moves
  into Prompt text.

## Metadata impact note

```text
Fact: a required model-input projection could not legally fit the configured context budget
Authoritative producer: ContextAssembler / ContextRequestBuilder
Consumers: business request owner, LLMClient guard, diagnostics, recovery request hash
Lifecycle: runtime request evidence; optionally trajectory/checkpoint evidence through existing owners
Control impact: request submission and fallback selection
Existing contracts reviewed: ContextCandidate, ContextAssemblyPolicy,
  ContextCandidateDecision, ContextSelectionMetadata, ContextAssemblyResult,
  LLMRequestMetadata, FailureMetadata, RuntimePromptContextSnapshot
Decision: reuse ContextAssemblyBudgetError and existing selection/failure evidence;
  do not add a MetadataKind or a second budget status
Why no duplicate source of truth is created: ContextSelectionMetadata remains the
  assembly authority; owner diagnostics reference that decision instead of copying it
Serialization and migration: no persisted schema change planned in phase 5A
Tests: structured-payload integrity, forbidden control truncation, typed owner
  error handling, provider-before-send guard, full regression
Documentation updates: context README, API if externally visible behavior changes,
  testing guide, implementation log, this plan
```

## TDD sequence

1. Add a failing regression proving that a structured semantic payload is never
   submitted after raw truncation.
2. Add a failing regression proving that legacy fixed instructions cannot be
   returned as a partially retained ready control surface.
3. Add owner-boundary tests that distinguish context budget insufficiency from
   provider failure and malformed response handling.
4. Implement the smallest owner/adapter changes needed to satisfy the tests.
5. Run context, semantic, iteration, diagnostics, checkpoint, and full suites.
6. Update the implementation log with observed failure, evidence, fix, and
   remaining phase-5B limitations.

## Exit gate

- No tested control instruction is silently truncated.
- No tested structured JSON owner payload becomes malformed while marked ready.
- Budget insufficiency remains typed and visible through the affected owner path.
- Provider transport is not called for an illegal request.
- Existing 22-purpose inventory and single executable request constructor remain
  intact.
- Full repository tests pass and phase evidence is recorded before phase 5B
  planning begins.

## Rollback

Revert phase 5A owner/adapter behavior and tests together. Do not weaken
`LLMClient`'s existing assembly-status guard, edit checkpoint payloads, or make
structured truncation legal through free-form exception handling.

## Completion evidence

- Three regressions failed before implementation and passed afterward: legacy
  control overflow, semantic structured-payload overflow, and iteration owner
  budget-error propagation.
- The legacy memory adapter now preflights its fixed instruction through the
  existing typed candidate/status boundary and raises
  `ContextAssemblyBudgetError` when it cannot fit.
- Semantic goal/plan-step and iteration goal/task-design message payloads are
  non-truncatable until their owners split them into independently valid
  candidates.
- Context/semantic/iteration/checkpoint targeted regression: `172 passed`.
- Full repository regression after implementation: `654 passed`; compileall and
  `git diff --check` passed.
- Remaining limitation: memory/project/dialog/environment sections still use the
  legacy section adapter. Their candidate-level migration is phase 5B.
