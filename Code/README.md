# OpenPilot (package)

This directory is the `openpilot` Python package. For the full project overview,
architecture, setup, and handover notes, see the **[top-level README](../README.md)**.

```bash
pip install -r requirements.txt
pip install -e .
openpilot run
```

- `src/` — source code (`ui`, `autonomous_iteration`, `agent_generator`, `tools`,
  `metadata`, `core`, `memory`, `utils`).
- `tests/` — pytest suite (`pytest`).
- `pyproject.toml` — package metadata and the `openpilot` CLI entry point.

Durable recovery is implemented by strict contracts in `src/metadata/`, the
atomic `src/autonomous_iteration/checkpoint_store.py`, and explicit controller
preflight/reconciliation. Enable it with `openpilot run --checkpointing`; resume
requires explicit run ID, checkpoint ID, and project path. File mutations are
hash-reconciled, while commands without a registered probe fail closed.

Project-scoped Python validation is gated by a read-only `.venv` preflight.
Existing ready environments attach without install/network writes; setup or
resync follows the root permission policy. Failure blocks validation instead of
falling back to host Python, and resume reattaches and verifies the checkpointed
environment identity before continuing.

Project improvement runs after a verified core project result. The automatic
default is an optional enhancement; `--improvement-iterations N` with `N > 0`
is an explicit required quality gate, while `0` disables improvement. Optional
failure is reported as a warning without changing completed core task evidence.
If an optional improvement mutates files and then fails, its explicit changed
files are restored from the pre-iteration Git safety snapshot before the run
returns; rollback failure remains a visible enhancement failure.

LLM reasoning is controlled through a provider-neutral typed policy. A
versioned capability resolver renders provider payloads at the transport
boundary. Only typed routine tool decisions may use the configured economical
mode (disabled by default); ambiguous, general, and multi-file decisions retain
the provider default. Recovery-cache identity binds provider, model, sanitized
endpoint (including a non-default port), capability profile, and resolved
reasoning semantics.
Structured JSON requests disable provider-default reasoning only for capability
profiles that explicitly support disabling it; generic endpoints remain
provider-controlled.
Provider tool definitions and continuation messages are rendered explicitly at
the LLM boundary. Tool-call responses retain call identity and reasoning state,
fail closed on malformed shapes, and bypass the response cache.
Known native Anthropic and Gemini profiles route through their registered
single-attempt transports. `LLMClient` owns finite retry and bounded redacted
attempt evidence, including one labeled direct attempt after an environment
proxy failure, while native streaming remains explicitly unsupported.
OpenAI-compatible streams retain reasoning fragments as separate response state
without emitting them as visible text.
Streamed tool-call fragments are aggregated by index and validated before the
normalized response is returned.
Provider message mappings and SDK objects share one response normalization path.
Normalized responses attach typed reasoning-usage observations without storing
provider reasoning bodies in diagnostics.
Provider tool admission outcomes reuse project call, selection, failure, and
error contracts; wire identity alone never authorizes execution.
Event-loop result maps preserve a non-null provider call ID for wire correlation
without adding a null field to ordinary local tool results.
Provider tool schemas are a narrow read-only projection of registered typed
contracts, not a copy of the broad internal input model.
Provider argument decoding and contract validation are bounded pure checks;
successful validation is not an execution permission.
Provider path-scope checks require exact project-contained, non-symlink targets;
an empty explicit scope grants no file authority.
Provider resource usage and budget outcomes are typed before full admission;
free-form error text does not control budget branching.
Validation command admission preserves the exact task-owned argv and cwd and
permits only one automatic execution.
Provider permission decisions require literal booleans and keep mutation opt-in
separate from user confirmation.
The single-call read-only admission path composes those boundaries in one
fail-closed sequence and returns a selection only after registry, contract,
budget, permission, scope, and exact-validation checks pass; it never executes
the tool and never admits mutation.
Mutation admission is a separate patch-only entry. It requires literal opt-in,
confirmation, exact write scope, a task-owned validation command, and a
registered validation executor before returning a non-executed selection.
Batch admission lives in a separate module, caps one response at 32 unique
provider calls, and accumulates typed resource use only after a call is
admitted. It preserves provider order and never executes returned selections.
Round-trip attempt and evidence-coverage values are strict frozen core
contracts. They preserve provider correlation and bounded read/page evidence
without creating a new persisted metadata owner.
The final result envelope separately composes existing LLM, tool-loop, budget,
attempt, evidence, and reasoning values and rejects contradictory completion or
lineage states.
Provider tool-call signatures use bounded canonical arguments, project-root path
normalization, and SHA-256 identity so provider call IDs cannot control replay or
duplicate detection.
The attempt ledger separately enforces unique provider IDs, first-signature
ownership, explicit duplicate lineage, and the existing 1,024-attempt cap.
Cross-round duplicate partitioning emits ordered new calls plus typed duplicate
blocks and performs all input, signature, ID, and capacity checks before
mutating the ledger.
The evidence state is the runtime owner for completed reads, declared windows,
bounded projections, page counts, evidence keys, and round observations; it
projects the existing frozen coverage contract without performing file I/O.
Wire exchange converts typed duplicate blocks and exact matching tool results
into one assistant message followed by provider-call-ordered tool messages,
with a 32-call and 1,600-character-per-result limit.
The shared result-payload fitter is the sole character-limit authority. It
returns valid deterministic JSON, preserves typed completion/error facts, and
compacts preview/artifact data without mutating the source payload.
Result artifact projection is separately pure and bounded. It converts text,
file, and code artifacts into at most 480-character model-facing previews plus
hash-based project/provider lineage, without storing bodies or performing I/O.
Result batching correlates those projections with at most 32 provider response
calls, typed recoverable errors, and duplicate blocks. It emits one bounded
`LLMToolResult` per call in response order and treats missing execution as a
fixed retryable batch abort without consuming ordinary local result maps.
Provider code-artifact references are parsed as a strict frozen
`ToolInputMetadata.artifact_ref` value. They never fall through to the generic
attributes escape hatch and contain no generated-code body.
The code-artifact ledger separately owns bounded generated-code bodies for one
runtime. Its frozen reference binds source and provider call IDs, checksum,
sizes, and language; exact re-registration is idempotent, while lineage rebinds
and mismatched or stale references fail closed without file I/O.
