# Stage 7 completion-budget campaign V2

V2 superseded V1 for its historical execution. It preserves the three-pair alternating
design, provider identity, fixed decomposition, quality gates, cache-off rule,
and 45k/90k/270k hard spend limits described in the V1 design document.

V2 corrects the invalid first-arm mechanism:

- the enhancement window contains five purposes, including localized
  `code_edit`;
- static fixes `code_edit` at 1,600 tokens while dynamic uses the production
  400–1,600 allocation;
- both arms pin the authoritative
  `IntelligentAutopilot._enhancement_runtime_budget` resolver to the selected
  arm budget;
- every manifest records the effective policy, and a mismatch stops before the
  next arm;
- missing any of the five purposes is an immediate arm failure;
- project `.venv` Python validation commands are compared by interpreter-
  equivalent canonical form;
- unknown-usage timeout does not authorize mechanical generation retries.

The frozen protocol is `STAGE7_COMPLETION_BUDGET_CAMPAIGN_V2.json`.

## Historical execution status

V2 executed one static arm on 2026-08-04. The selected policy matched all five
purposes, provider usage coverage was complete, and there were no transport
retries or unknown failed-attempt tokens. Core usage was 8,092 tokens;
enhancement usage was 13,378; lifecycle usage was 21,470. The localized
`code_edit` request used 243 tokens, confirming that the new budget boundary
reached that route.

The campaign then stopped correctly because project improvement produced no
observable diff. In addition, V2's protocol incorrectly required both
`code_generation` and `code_edit`, although the runtime selects one mutation
route. The sample is therefore diagnostic evidence, not a comparison arm. V2
must not be resumed or enrolled in V3; the fixed-goal, route-aware successor is
`STAGE7_COMPLETION_BUDGET_CAMPAIGN_V3.json`.
