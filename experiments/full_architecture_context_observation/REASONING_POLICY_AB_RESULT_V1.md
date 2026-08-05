# Reasoning Policy Experiment Result V1

## Decision

Adopt capability-resolved `disabled` reasoning only for typed routine tool
decisions: bounded inspection with explicit read targets, exact validation with
a typed command, and a single-file implementation task. Keep ambiguous,
general, and multi-write decisions at `provider_default`. Do not automatically
route any task to `high`; the complex positive sample did not show a quality
benefit.

Do not lower the production completion ceiling to 800. The 1,200 arm passed all
four fixed samples, while the 800 arm passed three and over-planned one read-only
sample. The 800 failure ended with `stop`, not `length`, so this is a conservative
quality rejection rather than evidence that the cap caused truncation. The
current 2,000 ceiling remains a provider-neutral guard; actual routine usage is
reduced by the reasoning policy, while broader repeated evidence is collected.

## Fixed-trajectory screening

Provider identity was `openai-compatible`, `deepseek-v4-flash`, official
`https://api.deepseek.com`, capability profile `deepseek-chat-known:v1`, and
cache was disabled.

- Offline system-quality reanalysis of the first routine run: baseline 1/4,
  economical 4/4, zero applied critical violations. Completion-token median
  fell from 1,954 to 145, a 92.58% reduction. The immutable source predates
  persisted `system_quality`; the derived result is hash-bound in
  `REASONING_POLICY_AB_REANALYSIS_V1.json`.
- Budget screening: 1,200 passed 4/4 with 148.5 median completion tokens; 800
  passed 3/4 and is rejected for now.
- Complex sample: economical and high both passed. Economical used 42 completion
  tokens and 215 total tokens; high used 774 completion tokens, including 677
  reasoning tokens, and 1,025 total tokens. This sample provides no basis for
  automatic high-reasoning escalation.

Source result SHA-256 values:

- routine source: `3066f0bc163b42937a769cf05088340e3512110128e7211dc7161e0526f5bcef`
- complex source: `8e66ca5fc916d86d2948fa18468817e9e03cadb70862cc49f936d63feb46d304`
- budget source: `cac600f0af1b6247554ac7a3b252613f9a020471f95d3458bf361e5306f15698`

## Final full-architecture pilot

The final paired pilot used one code state, the frozen four-task decomposition,
the same provider/model/profile, cache disabled, and identical completion
limits. The typed arm was the only intentional reasoning intervention. This is
an end-to-end mechanism pilot, not a statistical causal estimate, because
non-target LLM calls remain stochastic and there was one paired repetition.

Both arms completed the four core subtasks, preserved `test_calculator.py`, ran
the exact requested pytest then compileall commands through the project `.venv`,
and passed independent final pytest and compileall checks.

| Metric | Provider default | Routine disabled | Change |
| --- | ---: | ---: | ---: |
| Tool-event calls/outcomes | 4 | 4 | same |
| Tool-event input tokens | 6,415 | 6,118 | -4.63% |
| Tool-event output tokens | 5,276 | 787 | -85.08% |
| Tool-event total tokens | 11,691 | 6,905 | -40.94% |
| Tool-event duration | 27,863 ms | 8,739 ms | -68.64% |
| Tool-event length failures | 1 | 0 | removed in this pair |
| Full observed lifecycle tokens | 38,789 | 27,975 | -27.88% |
| Mission wall time | 237.6 s | 151.0 s | -36.45% |

Baseline requested/resolved `provider_default/omitted`; treatment requested and
resolved `disabled/exact` for all four routine tool decisions. Usage coverage was
100% for logical requests. The baseline failed attempt preserved 1,634 input,
2,000 output, 1,822 reasoning tokens, `finish_reason=length`, and partial output.

Final source hashes:

- baseline analysis: `83c37a6c86765aef80b22dbbbd1775f0888e9696fe62bee983a75404fe3f45f4`
- baseline manifest: `fbe58e0aa961ce2b3ece08b3c658c13c62b687d150ec71f0a404ab5dadc3ae14`
- treatment analysis: `40ac579e0613554c14eac9e8c294201685b28c919cf1fb7196551d82d6fbb31a`
- treatment manifest: `ecfda150c442f93bbc975aa47f96b72f7e72d52e0d150a6200e4683b2fc5e380`

## Remaining limits

Input-side late-run amplification remained in both arms, and the optional
project-improvement lifecycle still dominates later output. Reasoning control
therefore solves Controller output bloat, not all context growth. Failed optional
improvements now restore explicit changed files to their pre-iteration safety
snapshot and stop instead of repairing stale failed state, but fast-tool actions
outside `ToolEventLoop` still lack the same first-class `tool_called` trajectory
events. A broader experiment needs at least three paired repetitions with
alternating arm order and separate core-versus-enhancement windows.
