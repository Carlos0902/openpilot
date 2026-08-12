# Development Release 0.1.0.dev11

## Release identity

- Package version: `0.1.0.dev11`
- Previous development snapshot: `0.1.0.dev10`
- Release date: 2026-08-11

This is a cumulative local development snapshot. The functional changes are
already delivered in the stacked runtime PRs; this PR keeps only the package
identity, dependency metadata, version-consistency test, and a release note.

## Included behavior

The snapshot corresponds to the current development branch after:

- bounded response and evidence routing;
- project-scope and bounded-discovery safeguards;
- authorized generated-code persistence and bounded code-generation recovery;
- typed validation handoff and side-effect-free interactive validation;
- confirmed delivery of a completed interactive project through its typed run
  command;
- exact OpenAI tokenizer support for configured known capability profiles;
- concrete CLI delivery evidence for accepted actions, changed files, and
  validation status.

Those behaviors remain implemented and reviewed in their respective stacked
PRs. This release-only PR does not duplicate their source changes.

## Validation

- `PYTHONPATH=Code/src python -m pytest -q Code/tests/test_release_version.py`
- `python -m compileall -q Code/src/utils/__init__.py`
- `git diff --check`

The version test reads `Code/pyproject.toml` and fails if the importable source
version drifts from package metadata.

## Boundary

This note does not claim a production release or change default provider,
network, mutation, command, or launch permissions. It only identifies the
development snapshot that combines the already reviewed stacked PRs.
