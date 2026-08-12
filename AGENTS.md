# AGENTS.md

## Purpose
This file defines the development rules for OpenPilot Personal Agent.
When architecture, tool routing, metadata contracts, or permission boundaries change,
update this file together with `API.md`.

## Development principles
- Find the problem first; solve it second.
- Add a module, abstraction, or dependency only when there is a real need.
- Prefer the smallest change that fixes the current bug, gap, or boundary issue.
- Keep the project restrained; avoid speculative generalization.
- Preserve the existing style: explicit fields, strict contracts, narrow scope, and clear state transitions.

## Design priorities
- The project’s main strength is its LLM-oriented data modeling.
- Protect metadata shapes, field semantics, and state boundaries.
- Prefer extending existing contracts over inventing new layers.
- For cross-module communication, use the strict models under `Code/src/metadata/`.

## Metadata-first development
- Treat metadata as the project skeleton: define authoritative facts, ownership,
  lifecycle, legal states, and evidence links before implementing controller,
  prompt, persistence, supervisor, or UI behavior.
- Before adding or changing a field or model, review
  `docs/metadata/CONTRACT_CATALOG.md`, the concrete models, public exports, and
  actual producers/consumers. Treat `docs/metadata/VALUE_NESTING_RESEARCH.md`
  as non-normative supporting research only.
- Choose explicitly among reuse, extension, owned nested value, an existing
  reference/view mechanism, and a new contract. A new contract is the last
  option; a new generalized relationship layer requires separate architecture
  evidence and approval.
- Values that control routing, permissions, budgets, retry, recovery,
  completion, identity, persistence, or audit aggregation must be typed. Free
  text may explain a decision but must never control it.
- Context compaction may replace only non-required model-facing projections. A
  replacement and its governed source decisions are atomic; if the complete
  replacement cannot be selected or verified, fall back to the source view.
- Do not create a second authoritative copy of an existing fact. Preserve the
  current value-nested architecture unless a concrete problem justifies a
  separately reviewed structural change.
- Follow the mandatory impact note, tests, migration, and documentation gates
  in `docs/metadata/DEVELOPMENT_CONVENTIONS.md`.

## Module boundaries
- `ui/`: user interaction, display, and CLI entry points only.
- `autonomous_iteration/`: orchestration, state machine, task execution, and iteration control only.
- `tools/`: executable capabilities, tool contracts, and tool implementations only.
- `core/`: configuration, LLM access, risk policy, logging, and shared infrastructure only.
- `memory/`: context assembly, memory storage, project indexing, and persistence only.
- `metadata/`: typed contracts only; no business execution logic.
- `utils/`: stateless helper functions only.

## Tooling and permissions
- Tool use must remain controlled; do not open up command execution, file writes, or network access by default.
- Any action that mutates files, runs commands, or changes project state must be justified by a concrete need.
- High-risk actions need clear inputs, scope, and a verification or rollback path.
- Do not bypass existing approval, routing, or verification mechanisms for convenience.
- Post-processing mutations such as README synchronization are separate writes:
  run them only when the current typed task explicitly includes their target in
  `write_files`; do not widen a code task's scope implicitly.
- Localized symbol routing must prefer typed iteration-goal and acceptance
  evidence over free-form task prose; ambiguous symbol matches must fall back
  to a safer route instead of selecting the first source symbol.
- Project-environment preflight must remain read-only. Setup/resync inherits the
  root task's write, command, and network authority and must expose its side
  effects; a project-scoped Python validation must fail closed until a ready
  environment is attached, never silently fall back to the host interpreter.
- Provider mutation receipts are retained evidence, not mutation authority.
  Project only the first successful file mutation from a bounded result list;
  preserve the exact bounded validation command, strict artifact reference,
  byte count, and at most eight line-only changed ranges. Generated bodies,
  replacement text, and malformed or unbounded evidence must never enter the
  receipt.
- Post-mutation provider context must be rebuilt from the first system and user
  messages in canonical role order, one observed bounded mutation receipt, and
  bounded body-free assistant/tool wire evidence only. Never truncate the exact
  validation command or carry stale tool history into that continuation.
- Exact provider validation is one typed observation, never independent success
  and failure booleans. The task-owned command may match by normalized argv only,
  must execute at most once, and requires literal result success plus an integer
  zero exit code before the observation can become successful.
- One admitted provider round must preflight response/admission/duplicate/window
  identity, literal mutation mode, result budget, round identity, and artifact
  ledger type before execution. The preflight result is immutable and grants no
  new authority; it must snapshot mutable response/admission inputs so later
  execution cannot reinterpret caller-mutated raw collections.
- Single-round execution must consume that preflight bundle, invoke exactly one
  existing execution bridge, then reuse bounded result and wire projection. A
  post-execution projection failure must retain completed loop evidence (and
  projected results when available) without exposing provider exception text.
- Mutation/validation/finalization routing must be derived from one typed
  transition. Validation cannot succeed or fail without an available mutation
  receipt; a successful validation requires a remaining round for finalization;
  free-form error text must not control these branches.
- Provider responses must be classified before tool execution. A finalization
  response may not contain tool calls or an empty answer; reasoning-only token
  exhaustion is a distinct typed failure based only on strict usage evidence.
- Read-only finalization must require complete scoped reads plus either the page
  cap or a bounded no-progress projection. Both routes must reserve a remaining
  provider round before setting finalization pending.
- Duplicate-only and no-progress routing must be one typed transition. Mutation
  guidance is sent at most once, duplicate read-only evidence may request
  finalization only with remaining budget, and no-progress failure uses a stable
  code plus a separate bounded count.
- Failure recovery classification must require typed recoverability and
  capability evidence. Only recoverable admission failures or read-only tool
  failures may continue; protocol repair is a separate action, while mutation,
  shell, checkpoint, and indeterminate-side-effect failures remain terminal.
- Model-visible protocol repair must use one bounded transition: disabled or
  non-repairable failures do nothing, one eligible repair may be requested, and
  repeated/last-round cases fail with stable typed codes.
- Provider completion outcome must be classified once from strict response
  facts. Truncation takes precedence over tool progress; empty content is not
  normal completion; free-form finish-reason text cannot control other states.
- Historical provider tool-message compaction may rewrite only tool results
  before the latest assistant tool-call. It must deep-copy the message list,
  preserve the latest round byte-for-byte, bound the compacted payload, and
  convert malformed JSON into a bounded failure projection.
- Per-round provider budget calculation must be a pure bounded policy. It must
  validate positive prompt/call facts, preserve the 640-character result floor,
  cap calls at four, reserve 512 prompt tokens per tool result when remaining
  prompt capacity is known, and never infer a zero/negative call budget.
- Provider completion-token usage is observation evidence, not a coercion target.
  Accept only bounded nonnegative literal integers from `completion_tokens` or
  `output_tokens` (with the former taking precedence); booleans, strings,
  floats, negatives, and over-cap values remain unknown.
- Provider-visible tool surface must be derived by one pure phase policy:
  finalization exposes none, post-mutation exposes only exact validation,
  completed declared reads hide reader/command exploration, and pre-mutation
  preserves the declared surface. Tool names are bounded and unique.
- Mutation permission must be a separate pure boundary: no exposed mutation
  surface means no opt-in is required; an exposed surface requires literal
  code-level opt-in and literal user confirmation before any provider request.
- Reasoning intent is a typed request policy resolved by `core/reasoning.py`
  against a versioned provider capability profile. Business modules may select
  intent from typed task facts, but must not emit provider-specific payloads or
  infer capabilities from model-name substrings.

## Testing policy
- Follow test-driven development: add or update tests before changing behavior.
- Prefer deterministic, offline, repeatable tests.
- Every behavior change should have a corresponding test update.
- Focus coverage on:
  - metadata and serialization contracts
  - tool side effects
  - runtime state machine and routing
  - memory/context assembly
  - boundary conditions and failure paths

## Change discipline
- Make small, localized changes.
- Do not refactor multiple core modules in one pass.
- Do not introduce new frameworks, layers, or abstractions unrelated to the current problem.
- If you change API fields, tool behavior, or permission boundaries, update the documentation too.

## Documentation sync
If any of the following changes, review and update them:
- `AGENTS.md`
- `API.md`
- `README.md`
- `Code/README.md`
- `Code/.env.example`
- `AGENT_LOOP_PROTOCOL.md`
- `AGENT_LOOP_SUPERVISOR.md`
- `AGENT_LOOP_GOAL.md`
- `AGENT_LOOP_TUI_OUTPUT_GUIDE.md`

For task-trajectory / real-task-diagnostics work, also review:
- `docs/task_trajectory/README.md`
- `docs/task_trajectory/TASK_TRAJECTORY_EVIDENCE.md`
- `docs/task_trajectory/TASK_TRAJECTORY_EVIDENCE_ARCHITECTURE.md`
- `docs/task_trajectory/TASK_TRAJECTORY_EVIDENCE_PLAN.md`
- `docs/task_trajectory/TASK_TRAJECTORY_EVENT_ALIGNMENT.md`
- `docs/task_trajectory/TASK_TRAJECTORY_ID_STRATIFICATION.md`
- `docs/task_trajectory/IMPLEMENTATION_LOG.md`

When a diagnosed problem is fully handled, update
`docs/task_trajectory/IMPLEMENTATION_LOG.md` in the same change set with:
- observed failure
- validation evidence
- implemented fix
- remaining limitations

Do not wait for the user to ask for this log update separately.

## Default workflow
1. Read the relevant code and tests first.
2. Identify the current design and the actual problem.
3. For metadata-affecting work, complete the inventory/duplication review and
   metadata impact note before changing behavior.
4. Fix it with the smallest viable change.
5. Add or adjust tests.
6. Only then consider refactoring or adding new abstractions.

## Loop coordination
- Codex-facing iteration rules live in `AGENT_LOOP_PROTOCOL.md`.
- External scheduling, checkpointing, retry, and resume semantics live in `AGENT_LOOP_SUPERVISOR.md`.
- The loop’s acceptance criteria and stop conditions live in `AGENT_LOOP_GOAL.md`.
- Session recovery rules live in `AGENT_LOOP_SESSION_RESUME.md`.
- If these documents change, review whether `docs/testing/TEST_DESIGN_GUIDE.md` needs a matching update.
