# Stage 9 Task Designer provider sentinel V1

This campaign is a six-arm sentinel, not a distribution-wide experiment. It
tests three positive relevance branches with one current/compact pair each:

1. exact selected-candidate identity, current then compact;
2. a different candidate sharing one full acceptance criterion, compact then
   current;
3. independently relevant autonomous-iteration memory, current then compact.

All three scenarios use the same implementable enhancement: add a `divide`
docstring containing the normalized exact sentence `Raises ValueError when
denominator is zero.`, modify only `calculator.py`, and pass pytest and
compileall. The relationship between the goal and optional evidence is the
scenario variable.

The preflight also revalidates the frozen Stage 8 post-core/pre-enhancement
`ProjectStateSnapshot` (pipeline sequence 47, normalized source fingerprint
`sha256:f158...59e3`). Its `calculator.py` preview has the repaired ValueError
behavior but does not contain the exact sentence, so the enhancement criterion
was observably unsatisfied at that boundary in the source campaign.

## Source and runtime isolation

The provider scope retains live project path, safe targets, file summaries,
validation facts, product intent, and prompt-context safety. It overlays only
the frozen scenario diagnosis and memory records, then invokes the production
Task Designer candidate builder with the selected projection policy. The same
scope fixes the runtime iteration goal to the scenario goal so prompt facts and
task coercion cannot use different goal identities.

The manifest descriptor records three independent fingerprints:

- the exact diagnosis/memory overlay source;
- the frozen goal;
- the normalized live runtime contract.

The two arms in a pair must have the same runtime-contract fingerprint.
That fingerprint intentionally includes normalized live file summaries. Core
provider wording can therefore make a pair fail closed even when paths and
permissions match. This is accepted as a sentinel limitation; the gate must not
be weakened merely to retain a pair.

## Hard gates

Each arm requires one Task Designer request, complete usage, disabled reasoning,
zero transport retries, zero failed attempts, zero length recovery, complete
required candidates, and the scenario-specific sentinel candidate contract.
Full-architecture quality must show core success, successful enhancement,
ordered pytest/compileall validation, calculator-only mutation, no symlink or
project-root escape, and a parsed `divide` docstring containing that complete
normalized sentence.

Mutation scope is derived from a bounded canonical snapshot captured on the
first entry to the production Task Designer candidate builder. This is the
post-core, pre-enhancement boundary and is captured before candidate building
or provider transport. The final snapshot is diffed against that boundary.
The original fixture-to-final whole-run diff remains in the manifest as
descriptive evidence, but it does not gate the enhancement mutation scope.
Repeated builder entry may observe the same frozen boundary; a changed repeated
snapshot fails closed. A missing boundary, a capture count other than one,
truncation, or any symlink also fails closed.

`.openpilot` is intentionally included in both snapshots. It is not silently
excluded: any unverified `.openpilot` mutation after the Task Designer boundary
fails the enhancement gate, while the narrow runtime-owned forms below require
content and path validation. In the
retry1 diagnostic, `.openpilot` indexes and `project_stack.json` had the same
pre-Task-Designer write time as the core/environment safety snapshots; the old
whole-run gate therefore conflated core setup with enhancement mutations.

The phase-aligned retry2 diagnostic then showed a narrower normal runtime
effect: OpenPilot refreshes its file-content indexes and root directory sketch
after the calculator mutation. The record therefore retains the complete raw
enhancement diff and derives two explicit views: `runtime_owned_mutations` and
`user_owned_mutations`. Runtime ownership is content-verified, not inferred
from a directory prefix. The only accepted runtime files are:

- root `sketch.json`, parseable JSON with `kind=directory_sketch`, exact
  `system/openpilot` source, and canonical `project_root` and `directory` equal
  to the project root;
- `.openpilot/file_indexes/<relative-file>.index.json`, parseable JSON with
  `kind=file_content_index`, exact `system/openpilot` source, canonical project
  root, a traversal-free relative path naming an existing non-symlink project
  file, and matching canonical `file_path`, `index_file`, and sidecar location.

Unknown `.openpilot` paths, malformed JSON, deleted or symlinked artifacts,
path traversal, and kind/source/root/path mismatches fail closed. After this
classification, the complete user-owned changed-path set must be exactly
`calculator.py`.

The mutation descriptor itself is also treated as untrusted evidence. Its
`all_changed_paths` must equal the union of added, deleted, and modified paths;
the three categories must be pairwise disjoint and duplicate-free; every path
must already be canonical and contained by the canonical project root. Any
descriptor inconsistency fails before ownership classification.

Runtime ownership cannot be claimed by final JSON content alone. At the first
production Task Designer builder entry, the scope derives and freezes an
`expected_runtime_artifact_manifest` from paths already present in the bounded
enhancement-start snapshot. A runtime-owned path must occur in that manifest,
and runtime-owned additions or deletions are forbidden. Final JSON is parsed by
the production typed metadata models with unknown fields rejected. The index
validator then derives content SHA-256, byte size, line count, language, and
sections from the final target file. The sketch validator enumerates the final
root files through the production index policy, validates every corresponding
index, rebuilds each sketch item, and requires the complete `files` mapping to
match exactly. Only `created_at` and correlation identity are ignored during
derived section comparison; field sets remain strict. Each record persists the
full `producer_validation` result, and the arm gate independently recomputes it
instead of trusting the record's classification.

Spend limits are 30,000 Token per arm, 60,000 per pair, and 180,000 cumulative
provider-sentinel spend. The retry1 phase-alignment diagnostic consumed 13,167
complete observed tokens; retry2 consumed another 13,373 before exposing the
missing runtime-ownership classification. Both are excluded from formal arm
analysis but frozen as `prior_paid_diagnostics` with campaign-state and
arm-record hashes, validated during preflight, and counted against pair 1 and
the overall ceiling. Prior paid spend is therefore 26,540 tokens, leaving pair
1 with 33,460 and the campaign with 153,460. State files report prior-paid,
formal, and cumulative totals separately. Any hard-gate failure stops before
the next arm. The later confirmatory six arms are not launched automatically.

Every built arm record is atomically persisted to
`<run_dir>/campaign_record.json` before any gate is evaluated. The campaign
root atomically updates `campaign_state.json` after every arm with all records,
observed spend, status, and stop reasons. A failed arm is therefore durable
before the runner raises, including failures caused by Guard uncertainty.

The arm gate reads the hardened Guard observation copied from the run manifest.
Censored usage, unsettled reservation, reservation overrun, blocked requests,
or reservation-admission failure all stop the campaign; complete aggregate
usage alone cannot override those signals.

The parent protocol path is part of execution identity. The subprocess command
receives the exact resolved path used by the parent; programmatic execution
rejects a protocol object whose content differs from that file. A custom parent
protocol can therefore no longer validate one object while the arm loads the
default protocol.

## Current execution state

Running the module without flags performs a read-only preflight with zero
provider calls. The separately hardened `run_observation.py` Stage 9 arm
adapter is installed and verified offline. Paid execution still requires the
explicit `--execute` flag and an output directory. Tests use both the injected
`run_arm(schedule_item, run_dir, protocol)` interface and a stubbed subprocess
adapter to verify stopping and accounting without contacting a provider.

Three preserved diagnostic directories are not formal samples:

- `runs/stage9_provider_sentinel_v1_20260804` stopped before transport and used
  zero provider tokens;
- `runs/stage9_provider_sentinel_v1_20260804_retry1` completed one provider arm
  with 13,167 tokens, then stopped because the obsolete whole-run mutation gate
  included core-stage README, `.openpilot`, `sketch.json`, and `.gitignore`.
- `runs/stage9_provider_sentinel_v1_20260804_retry2` completed one provider arm
  with 13,373 tokens, then stopped because the phase-aligned gate had not yet
  distinguished verified OpenPilot indexes/sketch from user-owned mutation.

No diagnostic directory is deleted or rewritten. The retry1 and retry2 spend
remain part of the cumulative paid audit even though their records are not
mixed into the six formal records.

## V1 terminal result

The retry3 campaign completed the first current/compact pair and then stopped,
as required, on `runtime_contract_hash_mismatch`. Both arms passed their
individual quality, permission, mutation, command, candidate, usage, and
reasoning gates. The Task Designer observations were:

- current: 2,805 provider input tokens, 171 output tokens, and 2,780 assembled
  prompt tokens;
- compact: 1,644 provider input tokens, 140 output tokens, and 1,619 assembled
  prompt tokens.

This is a descriptive 41.39% provider-input reduction and 41.76% assembled-
prompt reduction. It is not a causal A/B result. The normalized runtime
contracts differed only in the live `calculator.py` summary produced by the
independent pre-Task-Designer core runs: one raised
`ValueError("denominator cannot be zero")`, while the other included the
denominator in the exception message. Goal, project path, safe targets,
validation context, and prompt-context safety were identical. Both code forms
passed validation, but the file summary is direct Task Designer input, so the
exact-hash gate must not be weakened after observing the result.

Retry3 used 24,851 formal tokens. Together with retry1 and retry2, Stage 9 V1
used 51,391 observed tokens, leaving 128,609 under the original 180,000-token
ceiling. The stopped campaign state and two immutable arm records are anchored
by SHA-256 values `080dc4ac...f696`, `9a8af670...9f5a`, and
`c8c4b5d8...d56c`, respectively. No later V1 arm is eligible to run under the
frozen stop-on-first-failure protocol.

The follow-up must therefore change the experiment topology, not the product
projection or safety contract: freeze one live post-core Task Designer input,
construct both projections from that same value, execute one as production and
the other as a no-downstream-effect shadow request, and swap production roles
across runs. V1 remains immutable and compact is not promoted from this result.
