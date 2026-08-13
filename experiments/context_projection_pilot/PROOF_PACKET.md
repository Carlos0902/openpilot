# Stage 3 context projection pilot proof packet

## Change definition

Experiment-only renderer treatment removes unrelated preferred diagnosis,
history, memory, manifest, validation detail, and support-body projection while
retaining required facts and relevant evidence. Control keeps the full current
source view. No production files are imported or changed.

## Mechanism hypothesis and authority audit

Typed safety, permission, task/goal, acceptance, write scope, exact validation,
and schema facts are hard inputs and must remain present. Optional evidence is
model-facing context only. Read/write admission, validation, receipts, retry,
recovery, and completion remain outside this pilot. Unknown facts are not
inferred. Duplicate aggregate/granular projection is rejected.

## Frozen design and gates

`manifest.json` freezes the treatment variable, corpus, renderer, evaluator,
three repeats, and the claim boundary. The deterministic gates require 100%
required-fact and active-constraint recall, legal evidence IDs, relevant
evidence recall, and correct fallback. Corrupt or incomplete treatment input
returns the current source view and `ready=false`; it cannot become success.

## Current result

Run `PYTHONPATH=. python -m experiments.context_projection_pilot.runner` and
the accompanying tests before any provider run. This is a mechanism canary
only: no provider quality, non-inferiority, holdout, or production-default
claim is made. The next step is to freeze provider/evaluator settings and run a
paired pilot with independent workspaces.
