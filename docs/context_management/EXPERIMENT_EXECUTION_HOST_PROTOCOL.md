# Experiment execution host protocol

The experiment harness was moved off this checkout to the `openpilot-air`
machine. The active matched workspace is
`/Users/abaaba/work/openpilot-context-experiment-20260808-h0`; the older
`/Users/abaaba/work/openpilot-worker`/`worke` location is archival and must not
be used as an execution root. This checkout is therefore the production
code/review host and must not silently recreate or restore the moved harness.

## Host responsibilities

### Active H0 workspace

- Run the full-architecture experiment runners and their focused tests from
  `/Users/abaaba/work/openpilot-context-experiment-20260808-h0`.
- Keep provider credentials local to that host; never copy secrets into this
  repository or receipts.
- Emit the typed result/receipt artifacts from the active route.

### This checkout

- Review production code, metadata contracts, plans, and result artifacts.
- Run Code tests that do not depend on the moved harness.
- Treat absent experiment files as an execution-host boundary, not as a failed
  experiment and not as permission to restore deleted files.

## Required artifact envelope

Every result copied back from the active H0 workspace must include:

- campaign ID and stage;
- source commit SHA and experiment-host identifier;
- provider/model/profile/endpoint identity without secrets;
- request/response usage and finish reason, or typed unknown;
- project, memory, and network side-effect counters;
- quality, scope, verification, and receipt-integrity decisions;
- explicit `passed`, `failed`, or `blocked` status and claim boundary.

The current checkout may cite a transferred artifact only after validating its
schema, source SHA, receipt hash, and claim boundary. A historical result from
the previous checkout is not silently relabeled as a run from this host.
