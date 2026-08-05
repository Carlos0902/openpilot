# Phase 3B Plan: Generation, Editing, and Repair Entries — Completed

## Scope

Migrate five request purposes:

1. full code generation;
2. autonomous text-file replacement generation;
3. code-unit generation;
4. scoped code editing;
5. runtime bug fixing.

## Safety rule

Model output from these paths may later mutate files. A complete edit scope,
replacement source, declared target list, and required output contract must not
be silently truncated. Until an owner splits a large input into independently
safe projections, its existing complete message is `required` with truncation
`forbidden`. Budget insufficiency follows the existing tool failure/fallback
path before any write.

## Metadata impact note

```text
Fact: a mutating workflow generated output from a complete bounded request or stopped before generation
Authoritative producer: each tool owns task/scope/file evidence; ContextRequestBuilder owns selection evidence
Consumers: code parser/validator, file writer, bug-fix verifier, diagnostics and replay
Lifecycle: request evidence followed by existing tool result and side-effect evidence
Control impact: permission, budget, recovery
Existing contracts reviewed: ContextCandidate, ContextSelectionMetadata, EditPlanMetadata,
  ToolInputMetadata, CodeArtifactMetadata, BugFixAttemptMetadata, ObservedFileMutationResult
Decision: reuse typed request assembly and existing tool/domain Metadata
Why no duplicate source of truth is created: candidate messages project existing tool inputs;
  file/tool Metadata remain the authoritative mutation scope
Serialization and migration: no new persisted contract; optional request selection remains backward compatible
Tests: entry coverage, full-scope preservation, insufficient-before-write, existing parser/permission/verification tests
Documentation updates: API, test guide, implementation log, this plan
```

## TDD order

1. Add static migration coverage for all five owner functions.
2. Add runtime assertions that generated requests carry the correct purpose and
   forbidden truncation behavior stops before provider/write.
3. Migrate complete/generate/chat/callable compatibility paths using already
   assembled content.
4. Run code generation/editor/bugfix/tool IO and recovery tests.
5. Run full suite.

## Exit gate

- No direct `LLMRequest` construction remains in the five owners.
- Complete scope is preserved or the typed request is rejected.
- Existing file target validation, approvals, snapshots, writes, and
  verification are unchanged.
- Targeted and full suites pass.

## Completion evidence

- Migrated all five mutation-adjacent request purposes with typed purpose and
  `forbidden` truncation for complete existing scopes.
- Compatibility complete/generate/chat/callable clients receive already
  assembled content.
- Static and runtime entry coverage: `16 passed`.
- Tool IO, generation, bugfix, iteration, and permission regressions:
  `169 passed`.
- `Code` full suite: `643 passed`.
- Phase 3B exit gate passed.
