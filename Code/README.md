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
