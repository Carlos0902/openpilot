# Provider paired pilot readiness (experiment-only)

Before any real provider call, freeze one manifest containing the model name,
provider endpoint/profile and capability profile, reasoning policy, completion
and token budgets, retry/stop/recovery policy, prompt/corpus/evaluator versions,
runtime/dependency/environment identifiers, and the non-inferiority contract.
`PROVIDER_PAIRED_MANIFEST.template.json` is the starting shape and
`provider_readiness.validate_manifest()` is a fail-closed check for this
contract. Credentials, bearer tokens, and secrets must never appear in the
manifest; use symbolic profile names and an external credential store.

Each task/arm/repeat runs from the same immutable source snapshot in an
isolated workspace. The workspace must not share mutable files, memory, or
provider conversation state with the other arm. The response filename is
`<task-id>.<arm>.<repeat>.json`; `validate_response_inventory()` checks the
declared inventory and reports missing recordings without synthesizing rows.

Every outcome is retained, including empty/malformed/length/timeout/provider
errors, tool-admission failures, validation failures, recovery exhaustion,
stopped/user-input-required, and unknown usage or finish reason. Missing usage
is `null`, never zero. Failed or incomplete pairs remain unknown in analysis.
The evaluator and exact validation command are frozen before treatment results
are inspected. No response may promote a production default; the existing
control path remains the rollback/kill switch.
