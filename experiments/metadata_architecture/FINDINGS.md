# Findings

## Status

Phase 0 establishes the isolated harness and one executable case. Conclusions are provisional and
non-normative.

## Initial conclusions

- The fact/relationship/projection split can represent project diagnosis selection without
  storing the full selected candidate in every aggregate and consumer view.
- Immutable revision references make “which version was selected” explicit.
- A prompt projection can be rebuilt from pinned sources and can expose source references without
  becoming authoritative metadata.
- Relationship and identity envelopes add overhead. Small cases may serialize to more bytes than
  value nesting, so payload size must be measured rather than assumed.
- Recovery checkpoints remain a strong counterexample to normalization: their core runtime state
  should remain self-contained snapshot-by-value unless external resolution can meet recovery
  guarantees.
- Repeated field names are not sufficient evidence of duplicated facts. Producer, owner,
  lifecycle, and persistence analysis remains mandatory.

## Open questions

- Should fact identity be global, scoped by project/run, or expressed as a typed composite key?
- Which references must pin revisions, and where is explicit `latest` safe?
- Does relationship persistence require atomic writes with endpoint facts?
- How should deleted, compacted, or migrated fact revisions behave?
- Is a relationship store useful beyond evidence lineage and candidate selection?
- Can projections remain plain functions, or do audit requirements justify projection records?

Run the case study and AST audit using the commands in `README.md`. Record dated measurements here
only when the input revision and scenario are identified.

## Recorded runs

### 2026-08-02 — Phase 0 diagnosis-selection case

- Repository base revision: `76b910b` (the working tree also contained unrelated in-progress
  changes, so this run is evidence for the checked-out source shape rather than a clean release).
- Baseline authoritative candidate copies: 3.
- Three-layer authoritative candidate copies: 1.
- Baseline serialized size: 858 bytes.
- Three-layer fact/relationship/projection recipe size: 1,149 bytes.
- Materialized prompt view size: 679 bytes.
- Interpretation: identity removed competing value copies, but its envelopes cost 291 bytes in
  this small case. Consistency improved; compactness did not.

The AST audit inspected 12 files under `Code/src/metadata`. Leading discovery signals included
`attributes=14`, `project_path=13`, `reason=13`, `confidence=12`, `tool_name=12`, `evidence=11`,
and `selected_candidate=3`. These counts require semantic ownership/lifecycle review before they
can be classified as duplication.
