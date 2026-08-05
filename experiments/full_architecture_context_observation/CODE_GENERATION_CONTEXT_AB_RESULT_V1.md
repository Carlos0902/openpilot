# Code-generation context A/B result V1

## Deterministic replay

The replay reconstructs the improvement execution input from hash-locked run
`20260803T183221Z` (events 29, 56, and 58) under the unchanged DeepSeek 4,096
requested / 128 reserved prompt policy.

| Variant | Original chars | Original tokens | Final tokens | Status |
|---|---:|---:|---:|---|
| Legacy contextual message | 49,781 | 13,324 | 0 | `budget_insufficient` |
| Purpose-specific candidates | 8,430 | 2,076 | 2,076 | `ready` |

The new projection reduced original prompt tokens by 84.4% without budget
expansion. All required candidates were kept: instruction, task, target/write
boundary, product safety, complete current source, and output contract. The
required representation retained `calculator.py`, `file_replace`, the imported
API non-regression criterion, `project_native`, and the `divide` implementation.

An intermediate implementation still filled all 3,968 effective tokens because
the optional diagnosis was unbounded. The hard gate rejected that result. The
final adapter projects bounded diagnosis, environment, and product-judgment
fields, leaving 1,892 prompt tokens unused in the replay.

## Real fixed-decomposition arm

Run `20260803T200118Z` used the frozen four-task fixture and optional improvement
policy. Core execution completed 4/4 tasks. The downstream improvement
code-generation request reached the provider with 1,942 assembled tokens,
2,025 provider input tokens, and 342 output tokens. All six required candidates
were kept; 2,026 effective prompt tokens remained.

The improvement changed only `calculator.py`. `test_calculator.py` retained
SHA-256 `c91400d8fcd3fc2557b4d4db8f4b559b08973ea7630b88946c2cc0156084abbe`.
Independent exact-command validation reported three pytest tests passed,
`python -m compileall -q calculator.py` succeeded, and `python calculator.py`
printed the new usage text. Trajectory events record
`completed_successful_iteration=true` and `completed_improvements=1`.

Post-run analysis uses the four-purpose observation window. This run covers
`project_improvement`, `iteration_task_design`, and `code_generation`; its
`iteration_goal` was a deterministic bypass. The three observed requests have
100% usage coverage and total 7,075 input, 3,276 output, and 10,351 provider
tokens. The sample remains `incomplete` for four-purpose cost comparison.

## Claim boundary

This establishes that the downstream Code Generator input owner no longer fails
before transport and can produce one verified improvement while preserving its
required context. It is one provider sample, not a distribution-wide causal
Token estimate. The same run still shows separate pressure in Task Designer
(3,968 selected tokens), controller reasoning/output, and host-versus-venv
interpreter consistency.
