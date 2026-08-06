# Phase 4 Plan: Recovery and End-to-End Acceptance — Completed

## Objective

Prove that the completed assembly boundary remains deterministic and auditable
through durable checkpoint/replay, real process interruption, real provider
submission, and provider usage reconciliation.

## Metadata impact note

```text
Fact: one assembled request was recovered/replayed with identical selection identity and observed provider evidence
Authoritative producer: ContextAssembler owns pre-request selection; runtime checkpoint owns durable snapshot;
  provider response owns actual usage
Consumers: runtime resume, diagnostics, final acceptance report
Lifecycle: checkpoint, response artifact, trajectory evidence
Control impact: recovery and completion
Existing contracts reviewed: ContextSelectionMetadata, RuntimePromptContextSnapshot,
  PendingLLMRequest, LLMReplayEntry, RuntimeCheckpointMetadata, LLMResponseMetadata
Decision: reuse existing contracts and add acceptance tests/evidence only
Why no duplicate source of truth is created: acceptance records compare existing owners; they do not persist another state model
Serialization and migration: no schema change planned
Tests: typed request replay hash, response replay without second call, corrupt/mismatched evidence failure,
  process interruption context replay, real provider request and usage, full regression
Documentation updates: context README, API, catalog, implementation log, phase plan
```

## Acceptance sequence

1. Extend LLM replay tests so an assembled request with candidate decisions is
   prepared, observed, persisted, and replayed without a second provider call.
2. Re-run prompt-context artifact checksum/hash mismatch and real process-exit
   recovery tests from the existing recovery suite.
3. Run one bounded real-provider request through `ContextRequestBuilder` using
   the configured model; record purpose, local content tokens, reserve, provider
   prompt/output usage, and finish status without exposing credentials/content.
4. Run the complete context/metadata/recovery targeted suite.
5. Run full repository tests, compile, diff checks, static entry inventory, and
   documentation consistency checks.

## Real-provider boundary

- One small deterministic request; no tool or file mutation.
- Output budget is intentionally small.
- Local counts are pre-request content evidence; provider usage is the final
  serialized-request authority. A difference is expected and recorded, not
  rewritten into local selection Metadata.
- Missing credentials/provider availability is a documented external blocker,
  not replaced with fake success.

## Exit gate

- Assembled request replay performs zero additional provider calls.
- Selection/request hashes and artifact checks fail closed on mismatch.
- Real provider returns a valid response with usage evidence and typed purpose.
- Static inventory still has 22/22 migrated purposes and one request constructor.
- Full suite and documentation checks pass.
- Remaining limitations are explicit; only then may the Goal be completed.

## Completion evidence

- Typed assembled request replay, selection-aware request hash, prompt-context
  artifact replay, corrupt-artifact blocking, and real process exit recovery:
  `5 passed`.
- Complete context/Metadata/recovery acceptance group: `197 passed`.
- Real configured provider (`deepseek-v4-flash`, `openai-compatible`) returned
  the exact requested response with `finish_reason=stop`.
- Local official tokenizer evidence: 14 selected message-content tokens, 128
  reserved tokens, 3,968 effective content allowance.
- Provider authority: 97 prompt tokens, 25 completion tokens, 122 total tokens.
  The difference is retained as the documented provider framing/billing
  boundary; it is not copied back into selection Metadata.
- Static inventory: 22/22 typed purposes, one executable request constructor.
- `Code` full suite: `651 passed`; compile and scoped diff checks passed.
- Phase 4 exit gate passed.

## Remaining limitations

- Provider-private message framing cannot be reproduced exactly offline; the
  explicit reserve remains a configured safety boundary.
- Most migrated owners currently expose candidates at message granularity.
  Owners may later split large messages into finer typed projections, but must
  preserve the same policy and recovery contracts.
- Provider usage is observed and audited but does not automatically retune
  future budgets.
