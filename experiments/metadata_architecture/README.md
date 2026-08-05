# OpenPilot Metadata Architecture Experiment

This directory is an isolated research package for testing alternatives to OpenPilot's
current value-nested metadata trees. It is not a production metadata package and does not
authorize changes under `Code/src/metadata/`.

## Boundary

- Production code must not import `openpilot_metadata_experiment`.
- Experimental code must not import `metadata` or modules under `Code/src/`.
- The package has its own `pyproject.toml`, source tree, tests, and commands.
- Experimental facts, relationships, projections, and generated results are not production
  protocol or persistence formats.
- A result can influence production only through a separately reviewed metadata impact note,
  migration design, and end-to-end tests required by
  `docs/metadata/DEVELOPMENT_CONVENTIONS.md`.

## Working hypothesis

OpenPilot likely needs a hybrid structure rather than a universal graph:

1. **Fact layer** — immutable revisions owned by one authoritative producer.
2. **Relationship layer** — typed, independently owned links between fact revisions.
3. **Projection layer** — disposable prompt, report, UI, and guard views rebuilt from pinned
   source revisions.

Complete point-in-time state such as a recovery checkpoint remains a value-owned snapshot.
References are candidates only when the referenced value has independent identity or lifecycle
and is reused across boundaries.

## First case study

The first executable case models the current
`ProjectDiagnosisMetadata -> ImprovementAnalysisMetadata -> prompt_context` pressure point.
It compares repeated selected-candidate values with one candidate fact, one `selects`
relationship, and a rebuildable prompt projection. The comparison reports duplication and
serialized sizes; it deliberately does not declare the three-layer model better from one case.

## Run

From this directory:

```bash
python -m pytest -q
PYTHONPATH=src python -m openpilot_metadata_experiment diagnosis-case
PYTHONPATH=src python -m openpilot_metadata_experiment audit --metadata-dir ../../Code/src/metadata
```

From the repository root, set the experiment source path explicitly:

```bash
PYTHONPATH=experiments/metadata_architecture/src \
  python -m openpilot_metadata_experiment diagnosis-case
```

## Layout

```text
metadata_architecture/
├── README.md
├── RESEARCH_PROTOCOL.md
├── FINDINGS.md
├── pyproject.toml
├── src/openpilot_metadata_experiment/
└── tests/
```

`RESEARCH_PROTOCOL.md` defines evidence and promotion gates. `FINDINGS.md` records results and
limitations without changing production policy.
