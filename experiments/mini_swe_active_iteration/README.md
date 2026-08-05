# mini-SWE-agent Active Iteration Experiment

This is an isolated development package for the staged experiment defined in
[`docs/active_iteration/MINI_SWE_ACTIVE_ITERATION_EXPERIMENT_PROTOCOL.md`](../../docs/active_iteration/MINI_SWE_ACTIVE_ITERATION_EXPERIMENT_PROTOCOL.md).

Current status:

- phase: `0`
- evidence eligibility: `false`
- production authorization: `false`
- mini-SWE-agent: `2.4.6`
- upstream tag commit:
  `a83fcae82d2a08f0ee0c688f9d137b3566c097f8`

Core-benefit screen preparation (separate from Phase 0 and the frozen V4
acquisition route):

- `core_benefit_screen.py` and `generate_core_benefit_screen.py` only construct
  redacted, outcome-free pool and 12-task Stage A drafts from independently
  receipted, resolved reviews plus a separate outcome-blind mechanism review;
  disagreements remain excluded until an independent adjudication receipt is
  present;
- `CORE_BENEFIT_SCREEN_ADJUDICATION_PROTOCOL_V1.json` and
  `CORE_BENEFIT_SCREEN_MECHANISM_REVIEW_PROTOCOL_V1.json` freeze the public
  receipt fields and the outcome-blind boundary for those two preparation gates;
- `screen_execution_protocol.py` validates the eventual 12-task execution
  protocol against the frozen Stage A manifest, including common budgets,
  provider identity, one-attempt failure accounting, and balanced 6/6 arm order;
- `core_benefit_screen_v1/SCREEN_EXECUTION_PROTOCOL_V1.json` freezes the selected
  task-arm provider and common budget but retains `task_arm_provider_execution_authorized=false`
  until the no-model Docker bridge smoke passes;
- `swebench_agent_environment.py`, `swebench_task_runner.py`, and the model-patch
  path in `swebench_network_runner.py` provide the tested execution bridge for a
  future frozen protocol: each arm receives a fresh pinned instance image with
  no network or host mount, exports only a patch, and reaches an arm-blind,
  network-isolated official evaluator. These modules do not authorize or start
  task-arm provider execution by themselves;
- `CORE_BENEFIT_SCREEN_REVIEW_PROVIDER_PROTOCOL_V1.json` freezes an independent,
  stateless DeepSeek V4 Flash second-review plane: one JSON-only request per
  candidate, with raw inputs/outputs private and no task-arm execution authority;
- `core_benefit_screen_v1/AIR_SERIAL_PREFLIGHT_V2.json` binds the existing Air
  host preflight to a serial-only (`maximum_parallel_task_pairs=1`) screen
  capacity; it does not authorize task-arm provider execution;
- the current readiness audit is
  [`docs/active_iteration/MINI_SWE_CORE_BENEFIT_SCREEN_READINESS_AUDIT_V1.md`](../../docs/active_iteration/MINI_SWE_CORE_BENEFIT_SCREEN_READINESS_AUDIT_V1.md).

The package deliberately does not modify the OpenPilot production controller,
convert mini-SWE trajectories into OpenPilot trajectory records, or expose
hidden evaluator inputs to an agent.

Implemented phase-zero slice:

- complete minimal in-memory E2-to-E3 evidence state, including
  `last_action_effect`;
- conservative evidence invalidation after mutation;
- arm-neutral controller and agent usage accounting with provider, token, tool,
  iteration and wall-time limits;
- `BudgetedDefaultAgent(DefaultAgent)` ordinary-arm boundary that retains the
  stock query/execute loop while applying the common budget before calls and
  tool execution;
- `ActiveIterationAgent(DefaultAgent)` with native DELEGATE plus explicit
  MEASURE/ACT/VERIFY/RECOVER command paths;
- a strict controller response boundary that only receives stable-ID native
  messages and lightweight active state;
- namespaced, sequence-numbered active-decision and budget sidecar while retaining
  `trajectory_format=mini-swe-agent-1.1`;
- deterministic hidden-evaluator, malformed-response, tool-failure,
  budget-exhaustion and fresh-agent conformance tests;
- a one-task exposed development suite whose hidden tests are materialized only
  in a host-side evaluator copy;
- a macOS Seatbelt environment that denies repository reads, network access,
  outside-sandbox writes, escaped working directories and inherited host
  secrets;
- a paired development runner that creates fresh model/controller/sandbox
  instances per arm, preserves native trajectories and final files, evaluates
  without an arm label and verifies cleanup;
- one secret-free `SharedProviderSpec` used to construct both the stock mini
  model and active controller model, with billable controller failure receipts;
- hash-bound `PHASE0_DEVELOPMENT_PROTOCOL_V1.json`.
- an outcome-free `EXPLORATORY_TASK_ACQUISITION_RULES_V1.json` that pins
  SWE-bench Verified revision
  `7f1793642f5ab809c0bce2e343b902247954170e`, fixed-seed 20-task stratified
  sampling, a 60-task minimum eligible pool, reproducibility checks and hidden
  data boundaries;
- a non-overwriting `EXPLORATORY_TASK_ACQUISITION_RULES_V2.json` successor
  that preserves the V1 acquisition design while also pinning the official
  SWE-bench harness at
  `f7bbbb2ccdf479001d6467c9e34af59e44a840f9`;
- `EXPLORATORY_TASK_ACQUISITION_RULES_V3.json`, which preserves the pinned
  source, harness, sampling and hidden-data policy but freezes a 6 GiB Docker
  memory floor after V2 produced repeated environment-build exit 137 across
  four ranked candidates, three repositories and three environment-image
  identities. No task-arm outcome informed this resource correction;
- `EXPLORATORY_ACQUISITION_PREFLIGHT_V1.json`, which verifies the pinned source
  has 500 unique instances across 12 repositories but blocks pool acquisition
  because this arm64 host has no running Docker daemon and only about 3.9 GiB
  free versus the frozen 120 GiB requirement;
- preserved V2 and V3 Air-host preflights that respectively expose a virtualenv
  symlink-resolution defect and verify its repair, followed by
  `EXPLORATORY_ACQUISITION_PREFLIGHT_V4.json`, which binds the pinned dataset,
  clean harness checkout, Docker `27.4.0`, Docker smoke image, runtime
  fingerprint and Docker virtual-disk capacity. The Air passes the frozen host
  gate with about 180 GiB available, while retaining explicit arm64, 8 GiB
  physical-memory and 4 GiB Docker-memory warnings;
- `EXPLORATORY_ACQUISITION_PREFLIGHT_V5.json`, which binds V3 rules to the
  current Air runtime and fails closed with the sole blocker
  `docker_memory_below_frozen_minimum`. The inventory generator rejects this
  receipt before reading source data or creating a new output;
- `EXPLORATORY_TASK_ACQUISITION_RULES_V4.json`, which preserves the source,
  harness, sampling and hidden-data design but replaces local image builds with
  official `swebench` instance images resolved and pinned by immutable manifest
  digest. `EXPLORATORY_ACQUISITION_PREFLIGHT_V6.json` passes on the same 4 GiB
  Air because evaluator execution no longer depends on a local environment
  build; V3/V5 remain preserved as the blocked local-build branch;
- a deterministic, redacted
  `EXPLORATORY_CANDIDATE_INVENTORY_V4.json` containing only task identity,
  source hashes, fixed selection rank and pending status for all 500 candidates;
  problem statements, hints, gold/test patches and test identities are not
  persisted. Its separate receipt binds the V4-rules/V6-host inventory;
- a private-materialization and network-isolated official-harness runner.
  Ranks 1–4, spanning matplotlib, Django and pytest plus three distinct
  environment-image hashes, each independently failed the environment build
  twice with exit code 137 before an evaluator container existed. Public
  non-overwriting exclusion receipts store only hashes, frozen runtime identity
  and the preregistered `build_or_install_failure` reason under
  `exploratory_candidate_preflight_v1/`. This consecutive cross-repository
  failure is treated as a 4 GiB Docker-memory host-boundary failure; acquisition
  was paused instead of manufacturing further resource exclusions;
- pinned-image acquisition, execution-preflight and nonexecution-evidence
  receipts for selection ranks 1–7, 9–22 and 24–33. Each official linux/amd64
  instance image is bound to its manifest digest and local image ID before
  evaluation.
  Two clean base runs agree on at least one designated failure and two clean
  gold runs agree on resolution for every included candidate, with the exact
  pinned image and evaluator network disabled in all one hundred twenty-four
  successful-chain attempts. The redacted nonexecution receipts store checkout size, exact
  license-text hashes and only production/test file counts plus path-set hashes.
  No receipt persists patch paths or hidden test identities. All thirty-one
  candidates remain `pending_stratum_reviews`: one outcome-blind review is
  recorded for each candidate, but the second independent review is absent. Private
  rationales remain outside the repository and public review decisions contain
  only a rationale hash. No eligible pool or agent-arm outcome has been
  generated.
- selection rank 8 is preserved as a preregistered exclusion rather than being
  forced through the successful-candidate schema. Its two network-disabled base
  runs both resolve the only designated FAIL_TO_PASS test while preserving all
  59 PASS_TO_PASS tests, so the candidate fails
  `base_failure_not_reproduced_twice`. Gold execution was stopped before a
  result existed; the redacted exclusion receipt records
  `gold_evaluation_started=false` and no hidden test identities.
- selection rank 23 is preserved as a second preregistered exclusion. Its two
  base runs both reproduce the designated failure, but only 2 of 5
  PASS_TO_PASS tests pass. Both gold runs resolve the designated failure while
  retaining the same three PASS_TO_PASS failures, so neither passes the
  official evaluator. A dedicated redacted gold-verification exclusion
  contract binds all four ordered attempts, both repeat agreements, the pinned
  image and `gold_evaluation_started=true` under the frozen
  `gold_patch_not_verified_twice` reason. No execution, nonexecution or review
  success receipt is generated for this candidate.
- ranks 9–22 and 24–33 complete the same pinned-image evidence chain. Their first
  outcome-blind reviews propose, in rank order, `test_regression`,
  `multi_file_interface`, `test_regression`, `single_file`,
  `multi_file_interface`, `single_file`, `localization`, `single_file`, and
  `multi_file_interface`, `single_file`, `localization`, `test_regression`, and
  `test_regression`, `single_file`, `multi_file_interface`,
  `multi_file_interface`, `single_file`, `localization`, `test_regression`,
  `single_file`, `single_file`, `single_file`, `single_file`, and `single_file`.
  Rank 14
  `astropy__astropy-14508` preserves 174 PASS_TO_PASS tests across all four
  attempts; rank 15 `sympy__sympy-21612` preserves 98, and rank 16
  `django__django-14434` preserves 133; rank 17 `sympy__sympy-16597`
  preserves 74; rank 18 `astropy__astropy-14598` preserves 175; rank 19
  `sympy__sympy-20428` preserves 154; and rank 20
  `sphinx-doc__sphinx-11510` preserves 7; rank 21 `django__django-14140`
  preserves 199; rank 22 `django__django-11141` preserves 24; rank 24
  `django__django-13195` preserves 382; and rank 25 `mwaskom__seaborn-3187`
  preserves 248; rank 26 `sympy__sympy-11618` preserves 4; and rank 27
  `django__django-16901` preserves 6; rank 28
  `scikit-learn__scikit-learn-26194` preserves 186; and rank 29
  `sympy__sympy-12481` preserves 7; and rank 30 `django__django-11066`
  preserves 3; rank 31 `sympy__sympy-12489` preserves 8; and rank 32
  `sympy__sympy-19495` preserves 8; and rank 33 `astropy__astropy-14309`
  preserves 141. Across ranks 1–33 there are 130 evaluator attempts: 124 in
  technically passing candidate chains, two
  supporting rank 8's exclusion and four supporting rank 23's exclusion.

The explicit active STOP remains fail-closed and cannot submit success. No
provider execution is authorized by the Phase 0 protocol. Provider execution
requires a separate versioned smoke protocol that freezes one exposed task,
common budget, model/endpoint, Prompt/tool/config fingerprints, single-attempt
failure policy and a non-overwriting artifact directory. V1 and its original
outputs are retained as an invalid infrastructure trial: both arms failed
before provider execution because the sandbox omitted stock mini template
variables, and the zero-call completeness gate was under-specified. V2 binds
the corrected environment and nonzero-call/explicit-terminal gate, but its
top-level active usage exported zero iterations even though the trajectory
retained five controller rounds. V2 remains a replayable diagnostic regression
(`ordinary=true`, `active=false`) but is invalid for clean conformance. V3 bound
both prior invalid trials and repaired iteration accounting plus explicit
noncompensating safety-gate reporting, then exposed another invalid path: an
empty controller response failed closed before its billable provider call was
receipted. V4 binds all prior trials and requires empty or malformed responses
to remain billed and receipted.

Development commands:

```bash
uv sync --extra dev
uv run pytest
```
