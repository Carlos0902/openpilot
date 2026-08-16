# Context projection pilot (experiment-only)

This offline pilot prepares the stage 3 treatment in `HARNESS_SLIMMING_PLAN.md`.
It compares a full current-source projection (control) with one single-variable
treatment: retain typed safety/permission/task/acceptance/write-scope/
validation/schema facts, and retain only relevant preferred evidence. Diagnosis,
history, memory, manifest, validation detail, and support bodies are degradable;
they never authorize reads, writes, commands, validation, or completion.

The renderer is deliberately pure and does not import production context
assembly, metadata, permissions, providers, or tools. It is a mechanism canary,
not evidence for a production default. A corrupt or incomplete treatment
artifact fails closed: the renderer restores the current source view, marks the
projection not ready, and the evaluator requires that fallback. Required facts
are never silently omitted. The evaluator also rejects unknown/duplicate IDs,
aggregate-copy duplication, and malformed projection records.

Run the deterministic pilot:

```bash
PYTHONPATH=. python -m experiments.context_projection_pilot.runner
PYTHONPATH=. pytest -q experiments/context_projection_pilot/test_pilot.py
```

The frozen corpus has 12 strata and three repeated rows per arm. No provider
response is consumed, and no production default or runtime behavior changes.
The runner also emits a conservative analysis: it requires all deterministic
gates and a 20% input reduction for `mechanism_canary_passed`, while provider
quality and non-inferiority remain unestimated.
