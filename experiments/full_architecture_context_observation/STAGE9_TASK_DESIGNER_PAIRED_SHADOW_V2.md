# Stage 9 Task Designer paired-shadow V2

This protocol changes the experiment topology without changing the product
projection, permission model, metadata contracts, or provider abstraction. It
exists because Stage 9 V1 correctly stopped when two independent full-
architecture runs reached Task Designer with different live `calculator.py`
summaries. The difference was valid runtime evidence, so weakening the V1 hash
would have produced a false causal comparison.

V2 instead makes the comparison inside one full-architecture run. At the first
and only post-core, pre-enhancement Task Designer candidate-builder entry, the
experiment takes one deep value snapshot of the live `ProjectStateSnapshot`,
selected `ImprovementGoal`, improvement report, and completed iteration. The
scenario diagnosis and memory overlay is applied once. The production
candidate builder then produces both `current` and `compact` candidates from
that same frozen source value.

One request is production and one is shadow. Production is always transported
first and is the only output allowed to reach Task Decomposer, Task Executor,
or any tool. Shadow is transported second, parsed, schema-checked, and passed
through deterministic task coercion, but its output is discarded. The shadow
provider has no tools. It may write experiment diagnostics outside the sample
project, but it must not change project files, isolated memory, runtime budget,
or enhancement-completion budget. Hashes and snapshots immediately before and
after the shadow call prove those boundaries remained unchanged.

## Six-run counterbalanced schedule

Each scenario has two full-architecture runs and swaps the projection assigned
to production:

| Run | Scenario | Production | Shadow | Request order |
| ---: | --- | --- | --- | --- |
| 1 | strongly related diagnosis | current | compact | production, shadow |
| 2 | strongly related diagnosis | compact | current | production, shadow |
| 3 | partial shared criterion | compact | current | production, shadow |
| 4 | partial shared criterion | current | compact | production, shadow |
| 5 | relevant iteration memory | current | compact | production, shadow |
| 6 | relevant iteration memory | compact | current | production, shadow |

This gives every scenario one current-as-production and one compact-as-
production run, while alternating the first production role across scenarios.
Live core results may differ across runs. They are not paired across runs and
cross-run runtime-contract equality is not required. The causal prompt
comparison is the current/compact request pair constructed from the one shared
snapshot inside each run.

The provider identity is frozen to the currently configured
`openai-compatible/deepseek-v4-flash` runtime so the paid campaign does not
silently change provider. This is execution identity, not a DeepSeek-specific
product design. Projection policies, typed reasoning intent, mirrored
completion limit, evidence, and stop semantics remain provider-neutral.

## Request and shadow boundaries

Both Task Designer requests must use the same provider identity, response
format, temperature, admitted maximum completion tokens, and typed reasoning
policy. Only the candidate projection and experiment role trace may differ.
Events are attributed through the typed trace role and execution ID; call
position is not accepted as evidence.

The production request uses the normal enhancement-completion reservation and
reconciliation path. Shadow does not create a second product budget
reservation. It mirrors the production request's admitted `max_tokens`, while
the experiment Guard remains authoritative for all actual provider spend. This
distinction must be recorded explicitly; shadow spend must never be presented
as production enhancement-budget spend.

Every run must contain exactly two Task Designer requests, one current and one
compact, with one production and one shadow role. Cache is disabled. Transport
retry, JSON repair, length recovery, failed attempt, omitted required
candidate, and partially retained required candidate counts must all be zero.
Both outputs must be non-empty, non-truncated JSON objects that coerce to one
authorized task and preserve the goal criterion and request-local evidence
IDs.

The production output alone continues through the full architecture. It must
pass core, enhancement, verification, command, permission, and mutation gates.
The only user-owned mutation is `calculator.py`; pytest and compileall must
pass; and the final `divide` docstring must contain the exact sentence
`Raises ValueError when denominator is zero.` Existing V1 mutation ownership
and producer-validation rules remain the safety baseline for the runner; this
V2 protocol does not relax them.

## Token accounting

Stage 9 has already consumed 51,391 observed provider tokens:

- 13,167 in the V1 retry1 diagnostic;
- 13,373 in the V1 retry2 diagnostic;
- 24,851 in the stopped V1 retry3 formal pair.

Those records remain excluded from the V2 formal sample, but their spend is
immutable and counts against the Stage 9 lifetime ceiling. V2 therefore opens
with 128,609 tokens remaining under the 180,000-token lifetime hard limit.

The additional hard limits are 30,000 tokens per V2 full-architecture run and
60,000 across the two V2 runs for one scenario. The per-scenario limit applies
to V2 paired-shadow runs; the Stage 9 lifetime limit additionally includes all
51,391 historical V1 tokens. Before every transport, settled or held usage plus
the exact current-request input, framing reserve, and maximum completion must
fit the current run and remaining lifetime caps. Actual complete usage from
core, production Task Designer, shadow Task Designer, and later production
stages counts. A reservation that does not fit is blocked before transport.

Missing or partial usage does not become zero. Failed-attempt usage is retained
when the provider supplies it; unknown failed usage censors the run, keeps its
reservation held, persists the evidence, and stops the campaign. Hitting a
nominal cap after a completed response also prevents the next provider call.

## Evidence and stopping

The run descriptor must retain the shared snapshot and runtime-contract hashes
and, for each request, its role, projection, candidate fingerprint, execution
ID, final prompt tokens, provider input/output/total usage, completion limit,
reasoning policy, finish reason, response hash, candidate decisions, parse and
coercion results, and whether the output was consumed. Shadow additionally
retains before/after project, memory, runtime-budget, and enhancement-budget
evidence.

Each run record is written atomically before its gates are evaluated. Campaign
state is updated after every run and before an exception is raised. The first
failure stops every later run. Stop conditions include:

- a frozen source, goal, provider identity, or shared-snapshot mismatch;
- more or fewer than one builder entry or two Task Designer requests;
- candidate sentinel, request membership, role, ordering, or parameter drift;
- provider failure, retry, recovery, truncation, empty or invalid output,
  unauthorized task, or incomplete usage;
- any observation of shadow output by downstream execution;
- any shadow change to project files, memory, or product budget state;
- any production quality, permission, command, mutation, or evidence failure;
- any per-run, per-scenario, or Stage 9 lifetime budget violation.

The V1 protocol and its diagnostic directories remain immutable. V2 failure
does not authorize another topology change or a selective rerun inside this
frozen campaign.

## Production-default decision

Compact must not become the production default before V2 passes. Passing
requires all six runs, all twelve Task Designer requests, all six shadow
side-effect proofs, complete usage, and every hard gate. In particular, the
three compact-as-production runs must pass the full architecture; shadow-only
success is insufficient.

Even a complete V2 pass does not mutate the default automatically. It only
makes compact eligible for a separately reviewed production-default decision.
Any V2 failure retains the current default and leaves completed V2 records as
diagnostic evidence only.

## First-arm sentinel observation

The first successful same-source paired transport used current as production
and compact as shadow for `strongly_related_diagnosis`. The immutable original
run stopped on `paired_max_completion_mismatch`, but the two actual requests
both used `max_tokens=1000`. The observer had read the production-only product
reservation field, which is intentionally absent from shadow, instead of the
request diagnostics field. Reanalysis after correcting that measurement
produces no arm stop reasons and makes no provider call.

Current versus compact provider input was 2,838 versus 1,659 tokens (-41.54%).
Both outputs selected only `calculator.py`, preserved all four acceptance
criteria, and were semantically equivalent; the production path passed all
nine full-architecture quality checks. Compact remained shadow-only, so this
is one output-quality and mechanism sentinel, not yet proof of downstream
equivalence or a production-default decision. The bounded evidence is recorded
in `STAGE9_TASK_DESIGNER_PAIRED_SHADOW_V2_FIRST_ARM_REANALYSIS.json`.

## Completed campaign result

Stage 9 V2 completed all six scheduled paired-shadow arms with no stop
reason. The final independent safety audit returned **conditional GO**: the
`iteration_task_design` compact context projection is eligible for a
separately reviewed, controlled production default. This result does not
authorize a global context-policy switch, removal of the current fallback, or
simultaneous changes to reasoning or completion policy.

The three context-relevance scenarios used balanced crossover order. Each
policy ran three times as production and three times as shadow. Across six
Current and six Compact Task Designer requests, provider input fell from
17,262 to 9,702 tokens (-43.80%), provider output fell from 1,086 to 1,054
(-2.95%), and provider total fell from 18,348 to 10,756 (-41.38%). Every one
of the six pairs used fewer total tokens under Compact. Per-scenario input
reductions were 41.65% for strongly related diagnosis, 41.72% for partial
shared criterion, and 47.72% for relevant iteration memory. The main effect
is therefore context projection, not reduced model reasoning or output.

All twelve Task Designer requests completed without retry, truncation,
unknown usage, or duplicate execution. All six full-architecture quality
gates passed. The three Compact-as-production responses were consumed
downstream and passed the same mutation and validation gates as the three
Current-as-production responses. Acceptance criteria and target semantics
were preserved in every pair; user-owned mutation remained limited to
`calculator.py`.

The independent audit recomputed empty stop reasons for all six arms,
confirmed complete usage and 60 unique lifecycle execution IDs, and verified
that Shadow responses were not consumed and did not alter project, memory, or
budget state. Arms 2--6 added 74,929 observed lifecycle tokens to the 79,131
token opening ledger. Final Stage 9 usage was 154,060 / 180,000, leaving
25,940 tokens with no held unknown usage or hard-limit failure. The resumed
first-arm prefix remained part of schedule and scenario accounting without
being charged to the lifetime ledger twice.

Raw evidence-link quality remains incomplete. All six production outputs
emitted goal-domain IDs rather than retained context-candidate IDs, so their
raw citations were rejected by the fail-closed provenance filter. This did
not widen authority or affect observed execution quality, and both policies
showed the ID-type confusion, but the campaign does not prove that provenance
quality is solved.

The authorized next boundary is a feature-flagged canary that changes only
the `iteration_task_design` projection default. It must retain required-
candidate gates, usage and quality telemetry, Current fallback, kill switch,
and existing mutation and verification controls. Compact must not become the
global context default, and this result must not be generalized across
providers, repositories, or task families. Evidence-contract repair and
cross-provider expansion are separate follow-up work; reasoning routing and
completion-budget experiments must remain separate so their effects stay
attributable.
