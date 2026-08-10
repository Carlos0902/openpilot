# Context-management experiment completion audit

## Requirement-by-requirement evidence

| Requirement | Current evidence | Status |
|---|---|---|
| Complete staged roadmap and gates | `CONTEXT_MANAGEMENT_EXPERIMENT_ROADMAP.md`, `EXPERIMENT_EVIDENCE_INDEX.md`, Phase 28–46 plan/result files | Proven for the active route; historical pre-route plans are explicitly superseded |
| Segmented context projection and Compact | Phase40/41 read-only gates; Phase43–45 fresh mutation matrices; H8-R2AI-2F raw/segmented control (`16,150→7,867` prompt, `17,146→8,775` total) | Proven for tested DeepSeek strata; H8-R2AI-2F is limited to one calculator mutation fixture and remains non-default |
| Conversation-scoped constraint persistence | Phase32: recall 1.0 at 10/20/50 turns, stable noise hash, revocation/authority checks; Phase32D 25→50 checkpoint/resume and post-resume Compact; Phase32E/F Controller and IntelligentAutopilot resume lineage; H8-R2AJ-0 50-turn SessionIngress/confirmed-state raw/Compact gate with stable `authority_hash` and complete snapshot hash separation; H8-R2AM three real task pairs retained the required constraint, Compact lineage=50, typed answer coverage and exact source grounding | Proven for the tested DeepSeek read-only task strata; independent semantic answer equivalence and broader provider coverage remain open |
| Provider/tool schema and permissions | Conditional schema/admission tests, scope/write/validation/receipt oracles, Phase42–45 6/6 gates | Proven for tested native lane |
| Reasoning/provider adaptation | Phase46A 94 tests, Phase46B DeepSeek canary, Phase46C-R structured JSON repair, H8-R2AN-AN-A/B explicit OpenAI no-reasoning profile and synthetic wire pair | Proven for explicit profiles and offline OpenAI wire omission; high effort and real OpenAI transport are not proven |
| Real final efficiency benefit | Phase43/44/45 paired prompt/total reductions with quality and mutation gates; H8-R2AI-2F verifier-hardened paired control; H8-R2AM candidate-projection-hardened three-task real pair (`22,010→12,552` prompt, `23,785→14,342` total); H8-R2AN-C/H8-R2AQ fresh DeepSeek pairs with typed grounding envelope; latest H8-R2AQ (`7,092→3,932` prompt, `7,820→4,460` total) | Descriptive DeepSeek results, not universal/default; calls stayed `9→9` in H8-R2AM and `3→3` in both latest single-task pairs, and the envelope is typed evidence linkage rather than full-answer semantic equivalence |
| Cross-provider confirmation | H8-R2AL readiness plus H8-R2AO-1/H8-R2AP remote readiness | Incomplete: OpenAI identity/profile, exact `tiktoken:o200k_base` tokenizer and provider-neutral runner are ready; only `missing_credentials` remains; zero transport; no real OpenAI quality/token/Compact result |

The earlier Phase38 factorial was correctly stopped as `CONDITIONAL/NO-GO` on
an incomplete cross-file quality denominator; later Phase40–45 strata were run
with repaired quality, scope, wire, and mutation gates and must not be merged
with the stopped Phase38 denominator.

## Current decision

The system has sufficient evidence for a feature-flagged, provider/task-scoped
Compact treatment in the tested DeepSeek lanes. It does not yet have evidence
for default-on mutation projection, a universal call-count benefit, a global
reasoning effort mapping, or a cross-provider rollout.

H8-R2AM strengthens the evidence contract: all three final DeepSeek task pairs now
have independently verified typed answer facts and exact source grounding, while
the two initial matcher failures are sealed and excluded rather than silently
treated as success. This improves evidence quality; it does not broaden the
semantic or provider claim.

H8-R2AN-C additionally reproduced one DeepSeek raw/Compact pair after rotating the
 credential and rebinding a task-specific candidate selection. The first attempt
 was correctly stopped before transport because an old selection disagreed with the
 current fixture; the repaired runner now routes multi-task candidates from typed
 `task_id` and the fresh R1/K1 pair passed all gates. This is a scoped regression and
efficiency confirmation, not cross-provider evidence.

H8-R2AQ then reran the current provider-neutral runner after repairing automatic
`quality_contract.answer_facts` admission. The fresh DeepSeek pair passed all gates and
reduced prompt/total tokens by 44.55%/42.97%; this is a regression and efficiency signal,
not a call-count or cross-provider claim.

H8-R2AN-AN-A/B closes the offline OpenAI adapter gap: `gpt-4o-mini` now uses an
explicit no-reasoning profile that omits unsupported controls, and the generic
tool-call continuation contract is exercised by a synthetic raw/Compact pair.
This is a transport/contract gate only; it does not authenticate the credential,
prove OpenAI model behavior, or authorize a real canary.

H8-R2AR closes the static-summary-cap gap: the opt-in rolling-summary path now
uses a static ceiling plus exact-tokenizer dynamic remaining-budget calculation,
and skips the summary provider when no safe slot remains. This is an offline
budget/fallback result; it does not make generated summaries default-on.

H8-R2AS closes the provider-factory wiring gap: the provider-neutral summary
request is now feature-flagged, no-tool, JSON-only, reasoning-disabled and
source-fingerprint bound; the existing adapter and deterministic source view
remain authoritative on failure. H8-R2AT then exercised that path once on
`openpilot-air`: DeepSeek returned a complete accepted summary, but the builder
retained the deterministic record at its atomic/recent-suffix selection boundary.
The result is a safety pass with no observed efficiency benefit, not a reason to
enable the flag by default.

## Remaining gates

### H8-R2AU Compact summary selection safety gate

The AU-3R source-binding repair is now implemented and independently reviewed. The shadow
harness uses an inert read-only memory dependency, a pre-arm revalidated
`ValidatedSourceSnapshot`, fixture turn-ledger/candidate-contract binding, current Compact code
manifest binding, and a canonical receipt verifier with nested body/secret and side-effect checks.
Local source/provider focused tests pass **14**, the experiment suite passes **92**, and the same
wrapper/source focused preflight passes **20** on `openpilot-air`.

The current remote source/target admission passed zero-transport DeepSeek readiness, followed by a
fresh credentialed R0/S1 builder shadow. S1 made one request with usage `2522/127/2649` and
`finish=stop`; the provider summary was accepted but not selected because it displaced a recent
suffix. Independent receipt verification passed with no prompt/artifact or mutation side effects.
This is a scoped DeepSeek safety/selection result, not semantic equivalence, token/call-count
benefit, cross-provider evidence, or default-on authorization.

1. Supply an OpenAI credential, then rerun the provider-neutral readiness and a
   reasoning-disabled native read-only canary using the explicit
   `openai-chat-no-reasoning-known:v1` profile; exact `tiktoken:o200k_base` readiness
   is already present for `gpt-4o-mini`. The latest readiness rerun is
   `phase46b_provider_readiness_v5/readiness.json` and the latest
   `phase46d_openai_cross_file_mutation_v3/result.json`; both still report only
   `missing_credentials` and `transport_attempted=false`.
2. If that canary passes, run the frozen 46D raw/compact mutation canary and
   three-pair matrix separately for OpenAI.
3. If explicit high effort is required, add a provider-specific reasoning-token
   control or independently budgeted visible-completion reserve before testing
   it again.
4. Expand the accepted envelope to a larger task matrix or a mutation-safe shadow only
   after deciding how to measure semantic equivalence beyond lexical fact coverage. Keep
   Compact feature-flagged because calls did not decrease and completion increased in AJ-4.
5. Before any generated-summary rollout, keep the provider-summary flag disabled
   and deterministic Compact authoritative. H8-R2AY shows a narrow schema can
   stabilize balanced-history at cap 256/320, but single-use net cost remains
   positive. H8-R2AZ closes the experiment-only reuse-admission gap: two
   same-source reuses amortize the H8-R2AY generation cost and source/required/
   recent/session/artifact drift all fail closed. H8-R2BA closes the
   production-shaped metadata gap by adding body-free, shadow-only
   `ContextSelectionMetadata.compaction_reuse_admissions`; `used_in_prompt=true`
   is invalid and admitted shadow evidence creates no compaction binding.
   H8-R2BB closes the default-off builder injection gap: `MemoryContextBuilder`
   can append validated reuse admissions from a body-free shadow provider while
   preserving request hash, prompt hash, selected candidates and compaction
   bindings. H8-R2BC closes the artifact source adapter gap: explicit body-free
   reusable compaction candidates, including reduced `ContextCompactionBinding`
   facts, can produce shadow admissions without retaining summary text or
   changing prompt/request identity. H8-R2BD closes the first checkpoint
   discovery shadow slice: prompt-context snapshot bindings can be inspected and
   admitted only when an external body-free source-binding hash index is
   supplied; missing index, stale hash and artifact checksum drift all fail
   closed. H8-R2BE then persists the source-binding hash on newly produced
   `ContextCompactionBinding` values, lets checkpoint discovery admit from that
   persisted hash without an external index, keeps explicit compatibility only
   for historical bindings, and rejects persisted/external conflicts. The
   remaining gap was semantic quality checks and a default-off prompt-use
   preflight before any reusable artifact can replace source candidates. H8-R2BF
   adds that dry-run gate: a reusable binding must have admitted shadow evidence,
   matching source binding, artifact integrity, required/recent retention,
   deterministic semantic-fact coverage, and a successful trial assembly before
   any future prompt-use transition. H8-R2BG then adds the default-off simulation
   layer: the same typed candidates are assembled raw and with an in-memory
   reusable projection, the source candidates must be omitted as
   `reason="compacted"` governed by the summary candidate, required/recent IDs
   must remain kept, and a positive prompt-character delta is required. H8-R2BH
   then verifies the same chain on `MemoryContextBuilder.build()` output:
   builder-selected typed candidates can feed preflight + simulation, builder
   prompt/context compactions remain unchanged, selected non-required dialog
   sources are governed-replaced, and required/recent IDs remain kept. H8-R2BI
   adds explicit token accounting to that default-off path: with an opt-in token
   policy and offline token counter, the same builder-sourced projection records
   raw/reusable prompt token counts and a positive token delta while preserving
   the existing governance and retention invariants. H8-R2BJ then replaces the
   harness-constructed binding with a real builder compact/sink binding
   rediscovered from checkpoint lineage; the discovered admission remains
   shadow-only and the same opt-in preflight/simulation path records positive
   character and token deltas. H8-R2BK then runs the first real-provider
   read-only paired canary on top of that exact chain: DeepSeek prompt tokens
   fell `4,424→1,176`, total tokens fell `4,474→1,233`, both arms finished
   `stop`, usage was complete and deterministic quality facts were covered.
   H8-R2BL then expands this to a 3-case read-only matrix and exposes/fixes a
   real Compact quality gap: deterministic summaries now preserve bounded
   `key=value` markers instead of wasting the summary budget on line prefixes.
   The repaired v3 matrix reduces aggregate prompt tokens `28,623→2,054` and
   total tokens `28,719→2,150`, with complete usage and fact coverage in every
   raw/reusable arm. H8-R2BM then moves the same discovered-binding/preflight/
   simulation chain into a safe isolated mutation/tool-task shadow: raw and
   reusable DeepSeek arms both use the production provider-tool entry on a
   temporary calculator fixture, observe scoped writer evidence, run the exact
   pytest command through `command_executor`, pass an independent exact pytest
   recheck, and avoid suspicious success. Prompt tokens fall `17,433→7,127`
   and total tokens fall `17,936→7,651`; provider calls stay `4/4`, and the
   reusable arm spends 21 more completion tokens. H8-R2BN then expands the
   same isolated mutation lane to a 3-case confirmation matrix and exposes a
   shared provider-tool contract gap: `tools` alone did not force native tool
   calls because `LLMRequest` lacked a typed `tool_choice` surface. After adding
   provider-neutral `tool_choice` propagation and omitting tools/tool choice for
   finalization, all 6 DeepSeek arms complete
   `tool_calls → tool_calls → tool_calls → stop`, pass scoped writer, exact
   validation, independent validation and suspicious-success gates, and reduce
   aggregate prompt tokens `41,288→14,223`, total tokens `42,633→15,251`, and
   completion tokens `1,345→1,028`. H8-R2BO then replays that same
   `tool_choice`/finalization contract against a real-project-shaped mutation
   task in a temporary source-isolated workspace using the production
   provider-tool entry: three tool-phase requests are required, finalization
   exposes no tools/tool choice, scoped writer/exact validation/independent
   validation gates pass, and provider transport remains zero. H8-R2BP then
   runs one credentialed DeepSeek raw/Compact pair on the same real-project
   task shape in independent temporary workspaces. The first attempt exposed
   that provider-default DeepSeek thinking rejects `tool_choice=required`; the
   experiment lane then explicitly routed the frozen task to routine/disabled
   reasoning without changing production policy. The passing v2 pair completes
   2/2 arms with scoped writer, exact validation, independent validation,
   complete usage and no suspicious success; prompt tokens fall
   `19,673→11,127` and total tokens `20,091→11,592`, while completion rises
   `418→465` and calls stay `4/4`. H8-R2BQ then confirms the same lane across
   two real-project sentinel mutation cases / four arms. The matrix passes
   scoped writer, exact validation, independent validation, complete usage,
   request-shape and suspicious-success gates; aggregate prompt tokens fall
   `51,138→24,797` and total tokens `52,050→25,722`, while completion rises
   `912→925`. Provider calls are 22 because some arms spend extra read rounds,
   so this is explicitly not a call-count benefit. This still does not mutate
   the production builder prompt, authorize production prompt-use, cover
   OpenAI/cross-provider behavior, prove call-count benefit, or justify
   default-on rollout. H8-R2BR then probes that call-count limitation by keeping
   the same two-case / four-arm DeepSeek matrix but narrowing `Task.read_files`
   to the target test file and representing support files as required body-free
   support-context metadata. The official v3 run passes scoped writer, exact
   provider validation, independent validation, request-shape, response-shape
   (`stop`) and suspicious-success gates; provider calls fall `22→16`, prompt
   tokens fall `31,951→15,347`, and total tokens fall `32,522→15,850`. This
   supports a future required-read vs support-context contract split, but still
   does not mutate the production builder prompt, authorize production prompt-use,
   change production provider routing, cover OpenAI/cross-provider behavior, or
   justify default-on rollout. H8-R2BS then makes that split production-facing:
   `Task.support_context_files` and `TaskGraphNodeMetadata.support_context_files`
   now preserve model-facing support identities, and provider-native execution
   projects them as required body-free `ContextCandidate` metadata under the
   existing explicit projection flags. Focused/adjacent tests prove this field
   does not widen `read_scope` or `write_scope`, does not serialize support file
   bodies, and support-only file reads are still rejected. BS is a contract
   hardening step. H8-R2BT then completes the production-route check by rerunning
   the BR-style two-case / four-arm DeepSeek matrix with `Task.support_context_files`
   instead of harness-local support candidates. Every arm selects production
   `provider-support-context:` IDs and no `h8r2br-support-context:` IDs, keeps
   the ideal 16-call shape, and passes scoped writer, exact validation,
   independent validation, request/response shape and suspicious-success gates.
   Prompt tokens fall `31,230→18,364` and total tokens fall `32,000→18,857`.
   This proves the production provider-entry support-context path for these
   sentinel mutations; it still does not authorize production prompt-use,
   default-on Compact, OpenAI/cross-provider behavior, source-body support
   inclusion, or broad real-task claims. H8-R2BU then moves one step beyond
   sentinel test edits by running a small production-code refactor of
   `Code/src/core/validation_command.py` through the same production
   `Task.support_context_files` path in independent temporary workspaces. Both
   DeepSeek arms complete the ideal 4-call provider-tool shape, select
   production `provider-support-context:` IDs, change only the target file,
   pass exact provider validation, independent validation, request/response
   shape, explicit refactor and suspicious-success gates. Prompt tokens fall
   `11,843→7,769` and total tokens fall `12,217→8,145`, while completion
   tokens rise by 2. This supports one production-code mutation stratum only;
   it still does not authorize production prompt-use/default-on Compact,
   source-body support inclusion, OpenAI/cross-provider behavior, production
   reasoning-policy changes, broad real-task claims, or accepted commit status.
   H8-R2BV then stops adding Provider benefit experiments and instead runs a
   deterministic support-context consolidation audit. The audit validates typed
   model fields, shared runtime/decomposer/provider projection markers, docs,
   test markers, and the BT/BU real-provider receipts through their own stage
   validators. It records zero Provider calls, zero project mutations, zero
   memory mutations and no commit; focused BV tests pass **4**, adjacent
   support-context/runtime/provider regression passes **280**, and the audit
   receipt hash is
   `sha256:bd19fb4f91c4091654eab95997941c1c8c31f298e26d8074a300125cc5a2453b`.
   This makes the support-context shared surface ready for a separate
   consolidation/review commit candidate, but still does not claim an accepted
   commit, dirty-tree-wide acceptance, default-on Compact, source-body support
   inclusion, OpenAI/cross-provider behavior, or broad real-task收益. H8-R2BW
   then prepares that candidate boundary as a deterministic commit-candidate
   manifest without staging or committing. The manifest identifies 32 candidate
   paths: 9 production shared files, 3 regression tests and 20 experiment
   evidence files. At generation time it partitions the dirty tree into 32
   candidate dirty paths, 997 explicitly excluded dirty paths and 558
   non-candidate dirty paths. It validates the BV audit receipt while marking
   run artifacts as excluded from the commit candidate, records zero
   stage/commit/push/provider/project/memory side effects, and keeps
   `commit_ready=false`. Focused BW tests pass **5**, adjacent
   support-context/runtime/provider regression passes **285**, and the manifest
   receipt hash is
   `sha256:b7926e9790b6d8c540d648d2a9f463e23da4711b1cbd24bb287bea51fff3dc5f`.
   This prepares exact staging/review; it still does not create or accept a
   commit, accept the wider dirty tree, or authorize default-on/cross-provider/
   broad real-task claims. H8-R2BX then validates the fixed BW 32-path
   candidate set through exact staging/review simulation without adding BX
   artifacts to that set. It validates the BW receipt, confirms all 32
   candidate paths exist and are not excluded, passes exact `git diff --check`,
   scans candidate files with zero secret-shaped and trailing-whitespace hits,
   and records review evidence tables for state/effects, resource bounds and
   boundary behavior. Body-free diff stats are 32 files, 15,498 added lines and
   39 deleted lines. Focused BX tests pass **5**, adjacent
   support-context/runtime/provider regression passes **290**, and the
   simulation receipt hash is
   `sha256:4939a02e4063524ea5f0e0957a29a187ebfb9ab7bae7840cb41c622a9e60e9e9`.
   This makes the package ready for independent review or explicit
   user-authorized staging, but still does not stage files, create or accept a
   commit, accept the wider dirty tree, or authorize default-on/cross-provider/
   broad real-task claims. H8-R2BY then applies the code-review sizing gate and
   rejects the fixed 32-path package as a single commit: the split audit records
   15,537 changed lines, classifies the single package as `huge_must_split`,
   and emits a required finding to split before commit. It proposes
   package-specific review for production/shared runtime files (9 paths, 1,036
   changed lines), regression tests (3 paths, 5,565 changed lines), and
   experiment evidence (20 paths, 8,936 changed lines), covering all 32 paths
   exactly once with zero excluded paths. Focused BY tests pass **5**, adjacent
   support-context/runtime/provider regression passes **295**, and the split
   receipt hash is
   `sha256:bb17048198714d3cc1aa0f70dca3bc29332a8db4b2f063670d4810ab5959784d`.
   This moves the work from review-ready to split-strategy-ready; it still does
   not stage files, approve any package, create or accept a commit, accept the
   wider dirty tree, or authorize default-on/cross-provider/broad real-task
   claims. H8-R2BZ then applies package-specific review to the actual
   production/shared runtime package. It rejects `support_context_contract_runtime`
   as a single commit because the package is 9 paths / 1,036 changed lines, and
   proposes three smaller slices: typed contract/docs (5 paths, 438 changed
   lines), runtime propagation (3 paths, 51 changed lines) and provider
   projection (1 path, 547 changed lines). Marker audit passes across typed
   contract/docs, runtime propagation and provider projection with zero missing
   markers. Focused BZ tests pass **5**, adjacent support-context/runtime/provider
   regression passes **300**, and the review receipt hash is
   `sha256:ae636fed2e5505212f0a5f928836d452bed177d957fefa4aada7ebda4320f562`.
   This makes the runtime package ready for slice-specific review; it still
   does not approve any slice, stage files, create or accept a commit, accept
   the wider dirty tree, or authorize default-on/cross-provider/broad real-task
   claims. H8-R2CA then reviews the smallest runtime propagation slice and
   catches a hunk-level contamination that path-level slicing hid: two of the
   three files include unrelated changes. `intelligent_autopilot.py` carries
   rolling summary initialization, `runtime_controller.py` carries checkpoint
   ingress/session binding, and `execution_task_decomposer.py` is pure
   support-context propagation. The audit rejects the path-level slice as a
   support-context commit, requires hunk-level selective staging, and confirms
   support-context propagation markers are present. Focused CA tests pass **5**,
   adjacent support-context/runtime/provider regression passes **305**, and the
   review receipt hash is
   `sha256:b2eee4c86239c6cba5c32c860c74b744c69236053114fed57391563f5056a90b`.
   This improves the commit-preparation fidelity: next work must separate
   support-context hunks from rolling-summary and checkpoint-ingress hunks
   before staging. It still does not approve any hunk, stage files, create or
   accept a commit, accept the wider dirty tree, or authorize default-on/
   cross-provider/broad real-task claims. H8-R2CB then converts that
   hunk-contamination finding into a deterministic body-free hunk candidate
   manifest. It identifies 8 support-context propagation candidate hunks and
   6 excluded unrelated hunks, with candidate support-context coverage matching
   CA at 9/9 changed lines and unrelated exclusion coverage matching CA at
   42/42 changed lines. Candidate unrelated lines and excluded support-context
   lines are both zero; excluded classes remain checkpoint ingress/session
   binding, rolling summary initialization and unclassified unrelated runtime
   changes. Focused CB tests pass **6**, adjacent support-context/runtime/
   provider regression passes **311**, and the manifest receipt hash is
   `sha256:6e5c579af9a37bec4c3fb4a80245297f3e5273270e6a3f0cac2146525f8c610e`.
   This makes the support-context runtime propagation candidate ready for
   independent hunk-level review. It still does not approve any hunk, claim
   selective-staging readiness, stage files, create or accept a commit, accept
   the wider dirty tree, or authorize default-on/cross-provider/broad real-task
   claims. H8-R2CC then performs that independent hunk review and approves the
   8 support-context propagation hunks as one hunk-level slice. The role review
   covers decomposer parse/serialize/prompt hint, autopilot task graph
   propagation and runtime task/node propagation with zero missing, duplicate
   or unknown roles. Authority review records model-facing context only, no
   read/write/validation/completion authority widening and zero authority-control
   hits; the 6 unrelated hunks remain excluded. Focused CC tests pass **6**,
   adjacent support-context/runtime/provider regression passes **317**, and the
   review receipt hash is
   `sha256:aa1a597371a0a5463570426b18090ea6bd8b53322170b7eddf36582826da5d9d`.
   This makes the hunk-level runtime propagation slice ready for a later
   selective-staging simulation. It still does not stage files, create or accept
   a commit, accept the wider dirty tree, or authorize default-on/cross-provider/
   broad real-task claims. H8-R2CD then runs that selective-staging simulation
   without writing the git index. The in-memory staged tree applies exactly the
   8 review-approved hunks to HEAD, selects 0/6 excluded hunks, produces 3
   simulated files and staged-tree hash
   `sha256:1ecfdc3611ef8fb788d2e342c6b2be02980bd66df8fefd13d0d2e27d51edd32f`.
   Selected changes are 9 support-context additions, 0 deletions and 0 unrelated
   lines. The cached-diff name hash is unchanged before/after, proving no index
   mutation. Focused CD tests pass **6**, adjacent support-context/runtime/
   provider regression passes **323**, and the simulation receipt hash is
   `sha256:dcc18f16ca387ac773ed82f49f5c6c803b1aa7fd450e104819ffd80b6ddb46bd`.
   This makes the slice ready only for user-authorized selective staging. It
   still does not stage files, create or accept a commit, accept the wider dirty
   tree, or authorize default-on/cross-provider/broad real-task claims. H8-R2CE
   then reviews the remaining `support_context_provider_projection` path-level
   slice and rejects it as a support-context commit. The support-context
   provider projection markers are present, but only 23/547 changed lines are
   support-context projection; 524 lines are unrelated provider/runtime/
   reasoning/budget/telemetry/import/unclassified behavior. Focused CE tests
   pass **6**, adjacent support-context/runtime/provider regression passes
   **329**, and the review receipt hash is
   `sha256:989ae079a08985311355e1863b0388a306fcc007e1a53123bed7a96711022658`.
   This means provider projection still requires hunk/feature-level split before
   any support-context commit. It does not approve any hunk, stage files, create
   or accept a commit, accept the wider dirty tree, or authorize default-on/
   cross-provider/broad real-task claims. H8-R2CF then attempts the hunk/feature
   candidate manifest and proves that ordinary hunk-level staging is still not
   viable: there are 0 pure support-context hunks, while all 23 support-context
   projection lines live inside 2 mixed hunks that also contain 492 unrelated
   changed lines; 9 unrelated-only hunks contain the remaining 32 unrelated
   lines. Focused CF tests pass **6**, adjacent support-context/runtime/provider
   regression passes **335**, and the manifest receipt hash is
   `sha256:1bd9e12a2ed2dcdd755f90635725bb62b104526296bfff6ca1ef346b94fdefba`.
   This means provider projection needs line/feature extraction before any
   staging simulation. It does not approve any hunk, stage files, create or
   accept a commit, accept the wider dirty tree, or authorize default-on/
   cross-provider/broad real-task claims. H8-R2CG then converts that diagnosis
   into a stacked feature extraction plan. The support-context helper bundle is
   identifiable as 4 functions / 69 lines, the generic initial-context helper is
   1 function / 34 lines, and the provider-native execution base is 1 function /
   402 lines that must be reviewed as a non-support-context prerequisite before
   support-context call-site wiring can proceed. Focused CG tests pass **6**,
   adjacent support-context/runtime/provider regression passes **341**, and the
   extraction receipt hash is
   `sha256:7e5959d6003cf465d7fc9ed1ec89f15b3573bc550b67a0e784983b49c6b42c24`.
   This makes the provider projection split route explicit, but still does not
   implement production extraction, approve any hunk, stage files, create or
   accept a commit, accept the wider dirty tree, or authorize default-on/
   cross-provider/broad real-task claims. H8-R2CH then reviews that
   provider-native execution base as its own non-support-context prerequisite
   candidate. The entrypoint is 402 lines and covers all 8 reviewed behavior
   domains: provider admission, budget policy, mutation boundary, runtime state
   wiring, prompt policy, roundtrip invocation, result telemetry and
   support-context projection. The review records 35 top-level statements, 10
   mixed-domain statements and 2 large mixed-domain statements, so it rejects
   the base as a single accepted prerequisite and requires a stacked split:
   admission/budget, mutation scope boundary, runtime/prompt setup, roundtrip
   invocation, result telemetry/completion mapping, and only then
   support-context call-site wiring. Focused CH tests pass **6**, adjacent
   support-context/runtime/provider regression passes **347**, and the review
   receipt hash is
   `sha256:23d05164c83c99e7e6dc42abd3a5c1a89e2760520638bf917882b588ceb92a16`.
   This keeps support-context provider call-site staging blocked and still does
   not accept any provider base package, implement production extraction,
   approve any hunk, stage files, create or accept a commit, accept the wider
   dirty tree, or authorize default-on/cross-provider/broad real-task claims.
   H8-R2CI then reviews the first provider base slice,
   `provider_admission_budget_policy`, and rejects it as a single accepted
   package. From CH's body-free statement classification, CI records 16
   admission/budget target statements / 286 lines, including 10 pure statements
   / 48 lines, 5 mutation-mixed statements and 3 forbidden-domain mixed
   statements. The next smaller candidate is
   `provider_entry_flag_and_budget_profile_core`; mutation profile guards, tool
   registry allowlist and mutation capability detection remain separate.
   Focused CI tests pass **6**, adjacent support-context/runtime/provider
   regression passes **353**, and the review receipt hash is
   `sha256:3512c2de9ab92b8420c0001676701ba4eb51169822f5957076e656b98d5a0bf8`.
   This keeps support-context provider call-site staging blocked and still does
   not accept any provider base package, implement production extraction,
   approve any hunk, stage files, create or accept a commit, accept the wider
   dirty tree, or authorize default-on/cross-provider/broad real-task claims.
   H8-R2CJ then reviews the smaller
   `provider_entry_flag_and_budget_profile_core`. CJ selects 8 pure
   admission/budget statements / 44 lines and verifies that the selected core
   contains only `provider_admission` and `budget_policy` domains with zero
   forbidden-domain hits. It still rejects the package because the error
   branches depend on unresolved `stmt-06`, a `provider_admission` +
   `result_telemetry` failure-result helper. The next candidate is therefore
   `provider_failure_result_helper_contract`. Focused CJ tests pass **6**,
   adjacent support-context/runtime/provider regression passes **359**, and
   the review receipt hash is
   `sha256:c173d9d86241b241f285b340cbb84f1c59c108aac4fb8fce6851e81715b42dc8`.
   This keeps support-context provider call-site staging blocked and still does
   not accept any provider base package, implement production extraction,
   approve any hunk, stage files, create or accept a commit, accept the wider
   dirty tree, or authorize default-on/cross-provider/broad real-task claims.
   H8-R2CK then reviews the unresolved
   `provider_failure_result_helper_contract` from CJ. The local
   `failure_result(...)` helper is 21 lines, returns typed
   `TaskExecutionResult`, constructs `FailureMetadata` and
   `TaskResultMetadata`, maps failures to `TaskStatus.FAILED` and
   `ResultStatus.FAIL`, and carries only body-free provider-execution
   attributes. Focused CK tests pass **6**, adjacent support-context/runtime/
   provider regression passes **365**, and the review receipt hash is
   `sha256:6f7bbb93d3b4886691b6824bb27d7a0fff25aa20e743b84d70a78d10d674b161`.
   This review-approves only the helper prerequisite and resolves CJ's helper
   dependency; it still does not accept the entry/budget core, implement
   production extraction, approve support-context call-site staging, approve any
   hunk, stage files, create or accept a commit, accept the wider dirty tree, or
   authorize default-on/cross-provider/broad real-task claims. H8-R2CL then
   joins the sealed CJ and CK receipts and review-approves
   `provider_entry_flag_and_budget_profile_core` itself. The selected 8
   statements / 44 lines remain body-free and limited to `provider_admission`
   and `budget_policy`; CK resolves the helper dependency; the next candidate is
   `provider_mutation_scope_boundary`. Focused CL tests pass **7**, adjacent
   support-context/runtime/provider regression passes **372**, and the review
   receipt hash is
   `sha256:327fde6670a1771461552db8b3621825d9e2c8d36ddefffb9c3911da735f3fda`.
   This still does not implement production extraction, approve support-context
   call-site staging, approve any hunk, stage files, create or accept a commit,
   accept the wider dirty tree, or authorize default-on/cross-provider/broad
   real-task claims. H8-R2CM then reviews the next
   `provider_mutation_scope_boundary` candidate and rejects it as a single
   package. The CH classification contains 8 mutation-boundary statements / 235
   lines, but only 3 pure mutation statements / 18 lines; 5 statements are
   dependency-mixed, 2 are forbidden-mixed, and 2 are large mixed statements
   carrying runtime/roundtrip/result/support-context or other provider-base
   domains. Focused CM tests pass **7**, adjacent support-context/runtime/
   provider regression passes **379**, and the review receipt hash is
   `sha256:2efaf32c356ff1d8dc520f2ac87152540c19518f0c30ed596b4deb28fafe0afd`.
   The next candidate is `provider_budget_mutation_profile_guards`. This still
   does not approve the mutation boundary package, implement production
   extraction, approve support-context call-site staging, approve any hunk, stage
   files, create or accept a commit, accept the wider dirty tree, or authorize
   default-on/cross-provider/broad real-task claims. H8-R2CN then review-approves
   `provider_budget_mutation_profile_guards` as the first smaller mutation
   prerequisite. The selected `stmt-09` and `stmt-10` cover 2 statements / 10
   lines, carry only `budget_policy` and `mutation_boundary`, have zero
   forbidden/unexpected domain hits, and depend on the already review-approved
   entry/budget core and failure helper. Focused CN tests pass **7**, adjacent
   support-context/runtime/provider regression passes **386**, and the review
   receipt hash is
   `sha256:1dd3c7ddb4ee14ddd9372bb72c6d441cc0c0bd99db3d7fa89786bc29344706ef`.
   The next candidate is `provider_initial_context_mutation_projection_guard`.
   This still does not approve the broader mutation boundary package, implement
   production extraction, approve support-context call-site staging, approve any
   hunk, stage files, create or accept a commit, accept the wider dirty tree, or
   authorize default-on/cross-provider/broad real-task claims. H8-R2CO then
   reviews `provider_initial_context_mutation_projection_guard` and rejects it
   as a single package. The target `stmt-19` is a pure `mutation_boundary`
   statement / 11 lines, but AST/source-boundary inspection shows it depends on
   unreviewed setup statements `stmt-15` through `stmt-18`; `stmt-17` and
   `stmt-18` include support-context / roundtrip-adjacent request detection.
   Focused CO tests pass **8**, adjacent support-context/runtime/provider
   regression passes **394**, and the review receipt hash is
   `sha256:3e866422d1226c675ea73c17ce41a2b0296b5b4475f95afbd99dea0d423ac507`.
   The next candidate is `provider_initial_context_projection_guard_dependencies`.
   This still does not approve the projection guard package, implement
   production extraction, approve support-context call-site staging, approve any
   hunk, stage files, create or accept a commit, accept the wider dirty tree, or
   authorize default-on/cross-provider/broad real-task claims. H8-R2CP then
   reviews `provider_initial_context_projection_guard_dependencies` and rejects
   it as a single package. The selected `stmt-15` through `stmt-18` cover 4
   statements / 10 lines; `stmt-15` and `stmt-16` are neutral projection flag
   lookup, while `stmt-17` and `stmt-18` are support-context request detection,
   and `stmt-18` is roundtrip-adjacent. Focused CP tests pass **7**, adjacent
   support-context/runtime/provider regression passes **401**, and the review
   receipt hash is
   `sha256:ee5c68620516c967564e919293b663217e48c97b15c8a1867e26351d5687885d`.
   The next candidate is `provider_initial_context_projection_flag_lookup_core`.
   This still does not approve the projection dependencies package, implement
   production extraction, approve support-context call-site staging, approve any
   hunk, stage files, create or accept a commit, accept the wider dirty tree, or
   authorize default-on/cross-provider/broad real-task claims. H8-R2CQ then
   review-approves that smaller `provider_initial_context_projection_flag_lookup_core`
   candidate. The selected `stmt-15` and `stmt-16` cover 2 statements / 8 lines,
   carry no domains, and assign only `projection_flag` and
   `mutation_projection_flag`. Focused CQ tests pass **7**, adjacent
   support-context/runtime/provider regression passes **408**, and the review
   receipt hash is
   `sha256:d8f101f7fe9c78f2a597458e89170e36d31ff9e6fcd1bd46f469650df2872fcd`.
   The next candidate is `provider_support_context_request_detection`. This
   still does not approve support-context request detection, approve the
   initial-context mutation projection guard, implement production extraction,
   approve support-context call-site staging, approve any hunk, stage files,
   create or accept a commit, accept the wider dirty tree, or authorize
   default-on/cross-provider/broad real-task claims. H8-R2CR then review-approves
   `provider_support_context_request_detection` as the next small prerequisite.
   The selected `stmt-17` and `stmt-18` cover 2 statements / 2 lines; `stmt-17`
   carries `support_context_projection`, while `stmt-18` carries
   `support_context_projection` plus `roundtrip_invocation` only as
   request-detection adjacency. Assigned names are limited to
   `task_support_context_files` and `projected_context_requested`; direct calls
   are limited to `self._task_support_context_files` and `bool`. Focused CR
   tests pass **7**, adjacent support-context/runtime/provider regression
   passes **415**, and the review receipt hash is
   `sha256:efd48d9ac46d012ca678570c164b81f686962c0d56cb93f3aacf2d7fc7209c5a`.
   The next candidate returns to `provider_initial_context_mutation_projection_guard`.
   This still does not approve the mutation projection guard, support-context
   candidate construction, provider roundtrip invocation, production extraction,
   support-context call-site staging, any hunk, stage files, accepted commit,
   wider dirty tree, or default-on/cross-provider/broad real-task claims.
   H8-R2CS then joins the CO blocker evidence with CQ/CR's resolved setup
   dependencies and review-approves `provider_initial_context_mutation_projection_guard`.
   The selected `stmt-19` covers 1 statement / 11 lines, carries only
   `mutation_boundary`, assigns no names, loads `allow_mutations`,
   `failure_result`, `mutation_projection_flag`, `projected_context_requested`
   and `projection_flag`, and calls only `failure_result`. Focused CS tests pass
   **7**, adjacent support-context/runtime/provider regression passes **422**,
   and the review receipt hash is
   `sha256:d8e64236686d45929f355172768715afb92c6c1b3ffcbbefd20c35c96610c575`.
   The next candidate is `provider_mutation_tool_capability_detection`. This
   still does not approve mutation capability detection, mutation confirmation,
   support-context candidate construction, provider roundtrip invocation, result
   telemetry mapping, production extraction, support-context call-site staging,
   any hunk, stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims. H8-R2CT then reviews
   `provider_mutation_tool_capability_detection` and rejects it as an accepted
   package because `stmt-23` loads `registry` from unresolved `stmt-20` /
   `stmt-21`. The target shape `stmt-22` / `stmt-23` is valid for future
   capability detection (2 statements / 6 lines), but CI had listed
   `provider_tool_allowlist_registry_core` separately and CL did not accept
   those registry statements. Focused CT tests pass **8**, adjacent
   support-context/runtime/provider regression passes **430**, and the review
   receipt hash is
   `sha256:4fbb61e3a80e4a9f9bf993e0e9e7740a8624337926448ed14b3145ef72eb9a83`.
   The next candidate is `provider_tool_allowlist_registry_core`. This still
   does not approve capability detection, registry core, mutation confirmation,
   provider roundtrip invocation, result telemetry mapping, production
   extraction, support-context call-site staging, any hunk, stage files,
   accepted commit, wider dirty tree, or default-on/cross-provider/broad
   real-task claims. H8-R2CU then review-approves
   `provider_tool_allowlist_registry_core`. The selected `stmt-20` and
   `stmt-21` cover 2 statements / 3 lines; `stmt-20` carries
   `provider_admission` and assigns `registry`, while `stmt-21` is a neutral
   missing-registry fail-closed guard that calls the CK-approved
   `failure_result` helper. Focused CU tests pass **7**, adjacent
   support-context/runtime/provider regression passes **437**, and the review
   receipt hash is
   `sha256:732d309e5a49ca9723563005978a487d872bd12c636b116ce2f1dbde27e32cf8`.
   The next candidate returns to `provider_mutation_tool_capability_detection`.
   This still does not approve capability detection, mutation confirmation,
   provider roundtrip invocation, result telemetry mapping, production
   extraction, support-context call-site staging, any hunk, stage files,
   accepted commit, wider dirty tree, or default-on/cross-provider/broad
   real-task claims. H8-R2CV then returns to
   `provider_mutation_tool_capability_detection` after CU resolved the registry
   dependency and CL's normalized-tools dependency remained approved. The
   selected `stmt-22` and `stmt-23` cover 2 statements / 6 lines; domains are
   limited to `mutation_boundary` and `provider_admission`; assignments are
   limited to `mutation_tools`, `capabilities`, `definition` and `tool_name`;
   direct calls are limited to `getattr`, `hasattr`, `mutation_tools.append`,
   `registry.get` and `set`. Focused CV tests pass **7**, adjacent
   support-context/runtime/provider regression passes **444**, and the review
   receipt hash is
   `sha256:39839bbee500e8592adeea353385577a1db10e58e07ec9878b1ac48ff7fb9a93`.
   The next candidate is `provider_mutation_confirmation_gate`. This still does
   not approve mutation confirmation, provider roundtrip invocation, result
   telemetry mapping, production extraction, support-context call-site staging,
   any hunk, stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims. H8-R2CW then
   review-approves `provider_mutation_confirmation_gate`. The selected
   `stmt-24` covers 1 statement / 6 lines, carries only `mutation_boundary`,
   assigns no names, loads `allow_mutations`, `failure_result`,
   `mutation_tools` and `user_confirmed`, and calls only the CK-approved
   `failure_result` helper. Focused CW tests pass **7**, adjacent
   support-context/runtime/provider regression passes **451**, and the review
   receipt hash is
   `sha256:e1699345af8861556746baa4aadc8e3968bf52503f6a7f8505711a8948bb1157`.
   The next candidate is `provider_runtime_state_and_prompt_setup`. This still
   does not approve provider roundtrip invocation, result telemetry mapping,
   production extraction, support-context call-site staging, any hunk, stage
   files, accepted commit, wider dirty tree, or default-on/cross-provider/broad
   real-task claims. H8-R2CX then reviews
   `provider_runtime_state_and_prompt_setup` and rejects it as a single
   accepted package. The selected top-level `stmt-25` is 141 lines and carries
   8 domains: `provider_admission`, `budget_policy`, `mutation_boundary`,
   `runtime_state_wiring`, `prompt_policy`, `roundtrip_invocation`,
   `result_telemetry` and `support_context_projection`. The first-level split
   covers 27 try-body statements plus 1 exception handler, with 140 inner
   covered lines, one structural `try:` line gap, and zero unassigned/unknown/
   duplicate IDs. Focused CX tests pass **7**, adjacent support-context/runtime/
   provider regression passes **458**, and the review receipt hash is
   `sha256:f17b4a41aedc4201ce3c78e3bed2c98469efa89f687b85b3e384b12cc402b9d2`.
   The next candidate is `provider_tool_definition_construction_core`. This
   still does not approve runtime/prompt setup, tool definition construction,
   provider roundtrip invocation, result telemetry mapping, production
   extraction, support-context call-site staging, any hunk, stage files,
   accepted commit, wider dirty tree, or default-on/cross-provider/broad
   real-task claims. H8-R2CY then review-approves
   `provider_tool_definition_construction_core`. The selected inner statement
   is exactly `stmt-25.try-01` / 1 line: it assigns `tools`, loads
   `build_provider_tool_definitions`, `normalized_tools` and `registry`, and
   directly calls only `build_provider_tool_definitions` with `registry` and
   `normalized_tools`. Focused CY tests pass **7**, adjacent
   support-context/runtime/provider regression passes **465**, and the review
   receipt hash is
   `sha256:203fa317f86c39279bf7a94615e1803fdb6364f7cf0c5eee7b76f92e0d35a9e3`.
   The next candidate is `provider_context_goal_project_scope_setup`. This
   still does not approve context/goal/project/scope setup, prompt construction,
   runtime controller setup, support-context call-site staging, provider
   roundtrip invocation, result telemetry mapping, production extraction, any
   hunk, stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims. H8-R2CZ then
   review-approves `provider_context_goal_project_scope_setup`. The selected
   inner statements are exactly `stmt-25.try-02` through `stmt-25.try-05`, 8
   lines total, assigning `goal`, `project_path`, `read_scope` and
   `write_scope`. They contain no `failure_result`, runtime-controller,
   prompt-message, support-context or provider-roundtrip calls. Focused CZ
   tests pass **7**, adjacent support-context/runtime/provider regression
   passes **472**, and the review receipt hash is
   `sha256:014453ff62a4d0626794a9cbfb6bb03dfb9f4309bbf481fd2a5d6c03bbe671a1`.
   The next candidate is `provider_mutation_scope_runtime_guards`. This still
   does not approve mutation scope guard, prompt construction, runtime
   controller setup, support-context call-site staging, provider roundtrip
   invocation, result telemetry mapping, production extraction, any hunk, stage
   files, accepted commit, wider dirty tree, or default-on/cross-provider/broad
   real-task claims. H8-R2DA then review-approves
   `provider_mutation_scope_runtime_guards`. The selected inner statements are
   exactly `stmt-25.try-06` and `stmt-25.try-07`, 10 lines total, carrying only
   fail-closed mutation scope/task-kind guards. They return typed
   `ProviderTaskScopeMissing` and `ProviderMutationTaskKindInvalid` failures,
   assign no names, and do not construct prompts, runtime controller state,
   support-context candidates, provider roundtrip runners or telemetry mapping.
   Focused DA tests pass **7**, adjacent support-context/runtime/provider
   regression passes **479**, and the review receipt hash is
   `sha256:011ade9852945f83da7b48d9292b96db00f08cfeb7bc360fbbb33b8df9f6dac0`.
   The next candidate is `provider_runtime_controller_state_setup`. This still
   does not approve runtime controller setup, prompt construction,
   support-context call-site staging, provider roundtrip invocation, result
   telemetry mapping, production extraction, any hunk, stage files, accepted
   commit, wider dirty tree, or default-on/cross-provider/broad real-task
   claims. H8-R2DB then review-approves
   `provider_runtime_controller_state_setup`. The selected inner statements
   are exactly `stmt-25.try-08` through `stmt-25.try-18`, 43 lines total,
   covering session constraint extraction/text projection, runtime controller
   lookup, `RuntimeStateMetadata` initialization, budget updates, session
   constraint copy, read-only runtime mode application and active task binding.
   It contains no `ProviderToolRoundTripRunner`, `LLMMessage`, support-context
   candidate construction, prompt instruction setup or telemetry mapping.
   Focused DB tests pass **7**, adjacent support-context/runtime/provider
   regression passes **486**, and the review receipt hash is
   `sha256:209bca473ddf6c345ea502960a02b27541ef3335cf1f2621455648853758f948`.
   The next candidate is `provider_prompt_instruction_setup`. This still does
   not approve prompt instruction setup, support-context call-site staging,
   provider roundtrip invocation, result telemetry mapping, production
   extraction, any hunk, stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims. H8-R2DC then
   review-approves `provider_prompt_instruction_setup`. The selected inner
   statements are exactly `stmt-25.try-19` through `stmt-25.try-23`, 30 lines
   total, assigning `system_prompt` and `user_prompt`, with `json.dumps` as the
   only direct call. The receipt records only string literal counts and lengths;
   prompt literal bodies are not serialized. Focused DC tests pass **7**,
   adjacent support-context/runtime/provider regression passes **493**, and the
   review receipt hash is
   `sha256:03d2d27115f0b90a97c0ac094a8a630e7b9e225927c3ebd57a2df6baad90f645`.
   The next candidate is `support_context_candidate_setup`. This still does not
   approve support-context candidate setup, support-context call-site staging,
   provider roundtrip invocation, result telemetry mapping, production
   extraction, any hunk, stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims. H8-R2DD then
   review-approves `support_context_candidate_setup`. The selected inner
   statements are exactly `stmt-25.try-24` through `stmt-25.try-26`, 24 lines
   total, initializing effective initial context, constructing support-context
   candidates, failing closed with `ProviderSupportContextInvalid`, and composing
   provider task prompt candidates with support-context and prior initial
   context. Candidate bodies are not serialized. Focused DD tests pass **7**,
   adjacent support-context/runtime/provider regression passes **500**, and the
   review receipt hash is
   `sha256:5792cb70953cede38082601921163ab66dff14a1497dfdb09a9d3cec5168434d`.
   The next candidate is `provider_roundtrip_invocation_core`. This still does
   not approve provider roundtrip invocation, result telemetry mapping,
   support-context call-site staging, production extraction, any hunk, stage
   files, accepted commit, wider dirty tree, or default-on/cross-provider/broad
   real-task claims. H8-R2DE then review-approves
   `provider_roundtrip_invocation_core` at static review level. The selected
   inner statement is exactly `stmt-25.try-27`, 22 lines total, constructing
   `ProviderToolRoundTripRunner`, invoking `.run(...)`, and passing system/user
   `LLMMessage` entries by role and payload reference. Message bodies are not
   serialized, and runtime provider transport is not executed by the review
   harness. Focused DE tests pass **7**, adjacent support-context/runtime/
   provider regression passes **507**, and the review receipt hash is
   `sha256:4356bc02ef50b156f1c83bf8eeafc2a76e78584b3dda145f4c855681d58c6ce0`.
   The next candidate is `provider_setup_exception_mapping`. This still does
   not approve setup exception mapping, result telemetry mapping,
   support-context call-site staging, production extraction, any hunk, stage
   files, accepted commit, wider dirty tree, or default-on/cross-provider/broad
   real-task claims. H8-R2DF then review-approves
   `provider_setup_exception_mapping`. The selected handler is exactly
   `stmt-25.handler-01`, 2 lines total, catching `Exception as exc` and
   returning `failure_result` with typed failure ID
   `ProviderToolTaskSetupFailed`. Raw exception text is not serialized, runtime
   provider transport is not executed, and result telemetry mapping remains
   blocked. Focused DF tests pass **7**, adjacent support-context/runtime/
   provider regression passes **514**, and the review receipt hash is
   `sha256:9d88ec18eaf2ad54ae400f1e05c536e4d22d9f3955c0b99f9746f5c52326b05e`.
   The next candidate is `provider_result_telemetry_mapping`. This still does
   not approve result telemetry/completion mapping, support-context call-site
   staging, production extraction, any hunk, stage files, accepted commit,
   wider dirty tree, or default-on/cross-provider/broad real-task claims.
   H8-R2DG then review-approves only the telemetry prelude slice under
   `provider_result_telemetry_mapping`. The selected top-level statements are
   exactly `stmt-26` through `stmt-29`, 14 lines total, assigning `duration`,
   `loop_payload`, `feedback_enabled` and `reasoning_complexity`. The full
   telemetry package is not accepted because later statements still contain
   output payload construction, budget contract hashing and failure/success
   result mapping. Focused DG tests pass **7**, adjacent support-context/
   runtime/provider regression passes **521**, and the review receipt hash is
   `sha256:6abe951c6f779578e0098057aa9f033618b8cf87f9bb4c21b1c5096f231de343`.
   The next candidate is `provider_result_output_payload_split_review`. This
   still does not approve output payload mapping, budget contract mapping,
   completion result mapping, support-context call-site staging, production
   extraction, any hunk, stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims. H8-R2DH then completes
   output payload split review and rejects `provider_result_output_payload` as
   a single accepted package. `stmt-30` is a 1-line `reasoning_mode`
   assignment, while `stmt-31` is a 61-line mixed `output` assignment with 23
   top-level keys, 10 budget-limit keys, 7 attempt keys and CH domains spanning
   budget policy, mutation boundary, roundtrip invocation, result telemetry and
   support-context projection. Focused DH tests pass **7**, adjacent
   support-context/runtime/provider regression passes **528**, and the review
   receipt hash is
   `sha256:17f4ecd5ad3addd76d5b3da9b1edd9e9e9ddeed3176d9625084856c467b2be10`.
   The next candidate is `provider_result_reasoning_mode_prelude`. This still
   does not approve output payload mapping, budget contract mapping,
   completion result mapping, support-context output payload or call-site
   staging, production extraction, any hunk, stage files, accepted commit,
   wider dirty tree, or default-on/cross-provider/broad real-task claims.
   H8-R2DI then review-approves `provider_result_reasoning_mode_prelude`.
   The selected top-level statement is exactly `stmt-30`, 1 line total,
   assigning `reasoning_mode` via a single `getattr(roundtrip, "reasoning_mode",
   None)` call. It contains no dict/list payload construction and does not
   accept `stmt-31`. Focused DI tests pass **7**, adjacent support-context/
   runtime/provider regression passes **535**, and the review receipt hash is
   `sha256:57a3487675d7976c1ef841a78b6dc92a1ef98a4ff802096668750224adea3f1e`.
   The next candidate is `provider_result_output_payload_core_fields`. This
   still does not approve output payload mapping, budget contract mapping,
   completion result mapping, support-context output payload or call-site
   staging, production extraction, any hunk, stage files, accepted commit,
   wider dirty tree, or default-on/cross-provider/broad real-task claims.
   H8-R2DJ then review-approves only the body-free key manifest for
   `provider_result_output_payload_core_fields`. The selected statement is
   exactly `stmt-31`; the approved manifest covers provider identity,
   roundtrip telemetry, diagnostics, reasoning and attempts key groups, while
   actual values remain blocked. It explicitly does not accept `final_response`
   content values, diagnostic payload values, budget/mutation output fields,
   support-context output payload, full output payload mapping or completion
   mapping. Focused DJ tests pass **7**, adjacent support-context/runtime/
   provider regression passes **542**, and the review receipt hash is
   `sha256:97d86ff105b8437bf2daba709c3f5d34e329cfa4db2f378d4254dade6f4d8d83`.
   The next candidate is `provider_result_output_payload_core_value_bounds`.
   This still does not approve output value mapping, budget contract mapping,
   completion result mapping, support-context output payload or call-site
   staging, production extraction, any hunk, stage files, accepted commit,
   wider dirty tree, or default-on/cross-provider/broad real-task claims.
   H8-R2DK then review-approves only the value-bound policy for those core
   output fields. It classifies provider/result/reasoning scalar fields as
   later scalar-mapping candidates, requires bounded structured projection for
   `tool_loops`, `evidence_coverage` and `attempts`, and keeps
   `final_response` plus request/budget/handoff diagnostics actual values
   blocked. Focused DK tests pass **7**, adjacent support-context/runtime/
   provider regression passes **549**, and the review receipt hash is
   `sha256:716c874974a221554b18372ce3f831666f61cb23f7d0587f840464ae96f249ca`.
   The next candidate is `provider_result_output_payload_core_scalar_mapping`.
   This still does not approve actual output value mapping, full output
   payload mapping, budget contract mapping, completion result mapping,
   support-context output payload or call-site staging, production extraction,
   any hunk, stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims.
   H8-R2DL then review-approves only the scalar mapping shape for the DK scalar
   keys. It records body-free AST/value-expression shapes for
   `provider_tool_execution`, `rounds_used`, `provider`, `model`,
   `budget_profile`, `outcome_feedback_enabled`, `reasoning_complexity` and
   `reasoning_mode`, while explicitly keeping actual scalar runtime values
   un-serialized. Focused DL tests pass **7**, adjacent support-context/runtime/
   provider regression passes **556**, and the review receipt hash is
   `sha256:09903a7d43da374ed0567b23008b45f13013161d269853169ada7b004b2e1851`.
   The next candidate is
   `provider_result_output_payload_bounded_structured_projection`. This still
   does not approve bounded structured projection mapping, actual output value
   mapping, full output payload mapping, budget contract mapping, completion
   result mapping, support-context output payload or call-site staging,
   production extraction, any hunk, stage files, accepted commit, wider dirty
   tree, or default-on/cross-provider/broad real-task claims.
   H8-R2DM then review-approves only the bounded structured projection shape for
   `tool_loops`, `evidence_coverage` and `attempts`. It records that
   `tool_loops` still comes from preprojected loop metadata, that `attempts`
   still uses a fixed 7-field projection, and that actual structured runtime
   values are not serialized in the receipt. Focused DM tests pass **7**,
   adjacent support-context/runtime/provider regression passes **563**, and the
   review receipt hash is
   `sha256:81d39d89468d5f7e838ace545981a8aa04d11af789639432ddb543a5e6919618`.
   The next candidate is
   `provider_result_output_payload_budget_mutation_support_context_split_review`.
   This still does not approve actual output value mapping, full output payload
   mapping, final-response content mapping, diagnostics payload mapping, budget
   contract mapping, completion result mapping, support-context output payload
   or call-site staging, production extraction, any hunk, stage files, accepted
   commit, wider dirty tree, or default-on/cross-provider/broad real-task
   claims.
   H8-R2DN then completes that split review without accepting the fields as a
   package. It classifies the remaining non-core output fields into mutation
   boundary, support-context output and budget/round-limit groups, records the
   fixed `budget_limits` key manifest, and identifies `budget_contract_sha256`
   as a separate dependency on `output["budget_limits"]` while keeping budget
   contract mapping blocked. Focused DN tests pass **7**, adjacent support-
   context/runtime/provider regression passes **570**, and the review receipt
   hash is
   `sha256:37179b863fef3cb354278791befcf5dce6fc65f109e71484da0cdb56cc42f797`.
   The next candidate is `provider_result_output_payload_mutation_boundary_fields`.
   This still does not approve mutation field mapping, support-context output
   payload mapping, budget limit mapping, budget contract mapping, full output
   payload mapping, completion result mapping, support-context output payload
   or call-site staging, production extraction, any hunk, stage files, accepted
   commit, wider dirty tree, or default-on/cross-provider/broad real-task
   claims.
   H8-R2DO then review-approves only the mutation boundary field mapping shape
   for `execution_mode`, `allow_mutations` and `user_confirmed`. It keeps actual
   mutation-boundary runtime values un-serialized and explicitly does not claim
   mutation permission decision completion. Focused DO tests pass **7**,
   adjacent support-context/runtime/provider regression passes **577**, and the
   review receipt hash is
   `sha256:cba4e55ad0539a6a4a252cc839d008b88b25ecb084093947609bf606a8104f45`.
   The next candidate is `provider_result_output_payload_support_context_fields`.
   This still does not approve support-context output payload mapping, budget
   limit mapping, budget contract mapping, full output payload mapping,
   completion result mapping, support-context call-site staging, production
   extraction, any hunk, stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims.
   H8-R2DP then review-approves only the support-context field mapping shape for
   `support_context_files` and `support_context_candidate_count`. It keeps
   actual support-context runtime values un-serialized and explicitly keeps
   support-context output payload and call-site staging blocked. Focused DP
   tests pass **7**, adjacent support-context/runtime/provider regression
   passes **584**, and the review receipt hash is
   `sha256:fdb1436af52875803cf08126bc101d18aebd944a23d90998bde8033549e67b8f`.
   The next candidate is `provider_result_output_payload_budget_round_limit_fields`.
   This still does not approve budget limit mapping, budget contract mapping,
   full output payload mapping, completion result mapping, support-context
   call-site staging, production extraction, any hunk, stage files, accepted
   commit, wider dirty tree, or default-on/cross-provider/broad real-task
   claims.
   H8-R2DQ then review-approves only the budget/round field mapping shape for
   `requested_max_rounds`, `effective_max_rounds` and `budget_limits`. It
   records the fixed nested `budget_limits` key/source shape while keeping
   actual budget values and budget contract mapping blocked. Focused DQ tests
   pass **7**, adjacent support-context/runtime/provider regression passes
   **591**, and the review receipt hash is
   `sha256:3ff69001ce5a1a510943e8a6891a2b0404fd74da3a3acf2673dcf3ac8fc5aad3`.
   The next candidate is `provider_result_output_payload_budget_contract_mapping`.
   This still does not approve budget contract mapping, full output payload
   mapping, completion result mapping, support-context call-site staging,
   production extraction, any hunk, stage files, accepted commit, wider dirty
   tree, or default-on/cross-provider/broad real-task claims.
   H8-R2DR then review-approves only the budget contract mapping
   algorithm/dependency shape for `budget_contract_sha256`. It verifies the
   dependency on `budget_limits` and the fixed canonical hash shape while
   keeping actual budget values, actual contract hash values, completion mapping
   and full output payload mapping blocked. Focused DR tests pass **7**,
   adjacent support-context/runtime/provider regression passes **598**, and the
   review receipt hash is
   `sha256:101eb6d5d6f6207cded8f6da75b92c14528cbd2ee859b6b0a84e516500da790f`.
   The next candidate is `provider_result_completion_mapping`. This still does
   not approve actual contract hash serialization, full output payload mapping,
   completion result mapping, support-context call-site staging, production
   extraction, any hunk, stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims.
   H8-R2DS then review-approves only the provider result completion mapping
   construction shape for the failure and success `TaskExecutionResult` paths.
   It verifies fixed failure metadata, success artifact and bounded summary
   shapes while keeping actual final text, result-summary text, error text, raw
   provider response/error and full output payload values blocked. Focused DS
   tests pass **7**, adjacent support-context/runtime/provider regression
   passes **605**, and the review receipt hash is
   `sha256:ca7e07a2d45e0ef2e0a999458d18fe73bd8ba109c88be591a45252b38393bb54`.
   The next candidate is `provider_result_mapping_integration_review`. This
   still does not approve runtime provider transport, support-context call-site
   staging, production extraction, any hunk, stage files, accepted commit, wider
   dirty tree, or default-on/cross-provider/broad real-task claims.
   H8-R2DT then review-approves only the composition of DG→DS provider-result
   mapping receipts. It verifies that all source receipts validate, the
   slice-level package chain is continuous, DH is treated as
   `provider_result_output_payload_split_review` rather than broad output-payload
   acceptance, and all combined blocked claims remain blocked. Focused DT tests
   pass **7**, adjacent support-context/runtime/provider regression passes
   **612**, and the review receipt hash is
   `sha256:0beb7c8a41465d27c58182eb16f3196018f850e8c22742fd52885e7d1b94d76f`.
   The next candidate is `provider_result_extraction_candidate_review`. This
   still does not approve provider-result production extraction, runtime
   provider transport, support-context call-site staging, production staging,
   any hunk, stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims.
   H8-R2DU then review-approves only the provider-result extraction candidate
   boundary. It verifies that `stmt-26`..`stmt-35` start after provider
   roundtrip invocation, exclude provider transport calls, and contain both
   failure/success completion return paths. Focused DU tests pass **7**,
   adjacent support-context/runtime/provider regression passes **619**, and the
   review receipt hash is
   `sha256:86a1416813dc4c43fb82aca696d47190dbfe2a1848cce5158096bad1405b7741`.
   The next candidate is `provider_result_extraction_hunk_candidate`. This
   still does not approve production extraction, hunk acceptance, runtime
   provider transport, support-context call-site staging, production staging,
   stage files, accepted commit, wider dirty tree, or
   default-on/cross-provider/broad real-task claims.
   H8-R2DV then review-approves only the provider-result extraction hunk candidate.
   It verifies that the exact contiguous `stmt-26`..`stmt-35` / lines 862–984 interval
   starts after the provider roundtrip, retains both completion return paths, and
   excludes provider transport calls. Focused DV tests pass **6**, adjacent
   support-context/runtime/provider regression passes **625**, and the review receipt
   hash is `sha256:25ddcc1f4be4864a2262e11e0029c9f2a3561a72266eebe5df29a76d1d94b885`.
   This still does not prove production extraction, accepted hunk, runtime provider
   transport, support-context call-site staging, output/error body serialization,
   staging, any accepted commit, wider dirty-tree acceptance, default-on Compact,
   cross-provider behavior or broad real-task benefit.
   H8-R2DW then independently reviews that hunk candidate against the current AST and
   confirms the exact post-roundtrip boundary, zero provider/authority-control calls,
   and a wide helper interface (27 input names, 13 output names). The review passes as
   diagnosis only; `production_helper_accepted=false`,
   `provider_result_extraction_hunk_accepted=false`, and
   `ready_for_selective_staging_simulation=false`. Focused DW tests pass **7**, adjacent
   support-context/runtime/provider regression passes **632**, and the receipt hash is
   `sha256:ec2f996c977c7c2af6da20888c524f21bfaa953f5b8c4561606d162fc8c3c187`.
   The next candidate is `provider_result_extraction_selective_staging_simulation`,
   which must first define a narrower split; no production refactor, staging, commit,
   provider transport or dirty-tree acceptance is implied.
   H8-R2DX then performs a no-index selective-staging simulation. It splits the current
   AST into telemetry (`stmt-26`..`stmt-30`), monolithic payload (`stmt-31`..`stmt-32`)
   and completion (`stmt-33`..`stmt-35`) segments, selects only telemetry and completion,
   and keeps payload blocked at 20 inputs / 3 outputs. Focused DX tests pass **7**,
   adjacent support-context/runtime/provider regression passes **639**, and the receipt
   hash is `sha256:d46a007b6b743c099b229815380d0b16af2a8f001f831ebca5bbe194a4862a6f`.
   The simulation writes no git index and does not approve production extraction,
   selective staging, a commit, provider transport or dirty-tree acceptance. The next
   candidate is `provider_result_completion_helper_candidate`; payload remains a separate
   `provider_result_output_payload_statement_split` follow-up.
   H8-R2DY then reviews the completion helper candidate at `stmt-33`..`stmt-35` / lines
   941–984. It confirms 11 inputs, 3 output names, one failure return and one success
   return, with zero provider/authority-control calls; the payload follow-up remains
   excluded. Focused DY tests pass **7**, adjacent support-context/runtime/provider
   regression passes **646**, and the receipt hash is
   `sha256:918330a7391e950b3d3f732523aaa4bfc06f2dc2bacc1b2f273fd77a4a104461`.
   This still does not accept a production helper/refactor, selective staging, runtime
   provider transport, a commit, or dirty-tree-wide behavior. The next candidate is
   `provider_result_completion_helper_contract_review`.
   H8-R2DZ then reviews the proposed `_build_provider_task_result` contract without
   implementing it. The contract fixes five runtime parameters, six typed dependencies,
   a `TaskExecutionResult` return type, failure/success status branches and a 500-character
   result-summary bound; actual final/error/artifact values remain absent. Focused DZ tests
   pass **7**, adjacent support-context/runtime/provider regression passes **653**, and the
   receipt hash is `sha256:abd18ed7a666f1846ac9f7584119601cf943adc24261f64a9a73103d112a85a9`.
   No helper implementation, provider transport, staging, commit or dirty-tree acceptance
   is implied. The next candidate is `provider_result_completion_helper_implementation_candidate`;
   payload remains `provider_result_output_payload_statement_split`.

The latest post-adapter regression passed **178 tests** across token counting,
context assembly, reasoning policy/adapters, native transport, provider/tool
admission and round-trip, and mutation gates. This is an offline/in-process
regression result; it does not substitute for the still-blocked OpenAI request.

The Phase46D isolated runner and its zero-transport credential/endpoint gates
are additionally covered by the latest **183-test** regression. Its current
`result.json` is typed-blocked only on missing credentials.

The runner now also rejects `--repetitions 3` unless the same run root contains
a passed two-arm canary with matching OpenAI identity, exact validation, bounded
diff, sealed receipt, and zero replay.

The repository-wide project-environment regression also passed **1163 tests**
with one non-failing pytest deprecation warning. `compileall -q
Code/src experiments/full_architecture_context_observation` and `git diff
--check` both passed. This validates the broader offline contract surface, but
does not replace the missing real OpenAI transport evidence.

The complete `experiments/full_architecture_context_observation` suite then
passed **469 tests**. Its Stage9 frozen offline report and paired-shadow source
gate now match the current deterministic projection output; this remains an
offline/provider-free result from the isolated execution host. The original
moved harness is at
`openpilot-air:/Users/abaaba/work/openpilot-worker/experiments/full_architecture_context_observation`;
that worker is an older, user-dirty tree and is not a valid execution root.
The revalidated H0/H1-R copy is at
`openpilot-air:/Users/abaaba/work/openpilot-context-experiment-20260808-h0/experiments/full_architecture_context_observation`.

Phase32D closes the direct `RuntimeCheckpointStore` ingress round-trip and
post-resume Compact boundary. Phase32E additionally passes the production
`AgentRuntimeController.resume` cursor/context canary after binding checkpoint
`session_id` to ingress execution identity. Phase32E-A then passes a real
process-replacement bootstrap lane: the child exits after persisting
`TASK_NORMALIZED`, the replacement receives exactly one goal-bound bootstrap
marker, and malformed goal hashes are rejected before executor entry. The
Phase32D/E/A claims are limited to direct/store and Controller
process-replacement boundaries; they do not claim Provider semantic quality,
token usage, or mutation benefit. Deliberately malformed checkpoint identity
is rejected by the repaired contract.

Phase32F extends that evidence through the public
`IntelligentAutopilot.resume` entry itself. The production outer object reaches
the Controller, preserves cursor/ingress/context lineage, blocks a conflicting
project before executor entry, and performs zero Provider/network/project
side effects. This is still an offline routing/integrity result; it does not
replace real Provider semantic or mutation evidence.

Until an OpenAI-scoped credential is supplied and the external Provider gates
pass, the cross-provider claim remains typed-blocked and all DeepSeek results
must remain labeled as scoped strata. This is an external credential
prerequisite, not evidence that context management is unsafe or incomplete.

Audit note: the H0 receipt hash is valid and is retained as sealed evidence, but
the file ends with a literal `\\n` suffix rather than a JSON whitespace newline.
The audit treated that as a serialization discrepancy, not as a new experiment
result; no receipt was rewritten.

The credentialed DeepSeek H3-C canary subsequently passed its native read-only
wire/scope/mutation gates: three requests, two tool-call rounds plus a final
text response, provider usage captured, and zero sentinel drift. This admits
the DeepSeek lane to the next reasoning/Compact experiments but does not add a
semantic-quality or token-benefit claim.
