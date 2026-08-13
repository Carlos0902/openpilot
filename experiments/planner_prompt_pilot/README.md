# Planner prompt pilot (experiment-only)

This frozen pilot compares the current instructional **control** prompt with an economical **treatment** prompt. It deliberately does not change the production executor, metadata, permissions, tools, budgets, or validation authority.

`corpus.json` contains 30 task fixtures spanning the plan's §6.2 shapes and freezes three repeats per arm. Its `acceptance` entries are checked against the typed, experiment-only registry in `acceptance.py`; exact-validation fixtures also freeze the command string. Before any provider run, freeze this corpus, evaluator, prompt files, model/profile, budget, retry policy, and environment in a manifest. Each pilot row records its arm, repeat, prompt size, global acceptance dimensions, per-fixture acceptance results, and deterministic evaluator outcome. If a recorded response supplies them, the row also preserves fabricated-path, Router/Guard, final-validation, retry, input/total-token, and latency fields; absent values remain `null`. Malformed responses, provider failures, and stopped runs remain in the record; they are never silently converted to success.

The offline runner evaluates recorded responses and is safe to run without credentials:

```bash
python -m experiments.planner_prompt_pilot.runner
python -m experiments.planner_prompt_pilot.runner --responses path/to/frozen-responses --output pilot-results.json
```

Response files use `<task-id>.<arm>.<repeat>.json` (for example `t01.control.1.json`). Missing or invalid JSON recordings are reported as malformed rows with a bounded error kind rather than aborting the run or silently treating them as success. This harness is a mechanism canary only; it cannot establish non-inferiority or authorize a production default.

The frozen manifest and evidence boundary are documented in [`manifest.json`](manifest.json) and [`PROOF_PACKET.md`](PROOF_PACKET.md).
