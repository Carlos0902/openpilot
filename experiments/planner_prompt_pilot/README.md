# Planner prompt pilot (experiment-only)

This frozen pilot compares the current instructional **control** prompt with an economical **treatment** prompt. It deliberately does not change the production executor, metadata, permissions, tools, budgets, or validation authority.

`corpus.json` contains 30 task fixtures spanning the plan's §6.2 shapes. Before any provider run, freeze this corpus, evaluator, prompt files, model/profile, budget, retry policy, and environment in a manifest. Pilot runs require at least three independent runs per task and arm; keep malformed responses, provider failures, and stopped runs in the record.

The offline runner evaluates recorded responses and is safe to run without credentials:

```bash
python -m experiments.planner_prompt_pilot.runner
python -m experiments.planner_prompt_pilot.runner --responses path/to/frozen-responses --output pilot-results.json
```

Missing recordings are reported as malformed rather than silently treated as success. This harness is a mechanism canary only; it cannot establish non-inferiority or authorize a production default.
