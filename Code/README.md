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
