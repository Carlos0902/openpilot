# Phase 12 Result: Reasoning Strategy Experiment

## Decision

The failure cause is confirmed: on this endpoint, provider-default or
explicitly enabled reasoning consumes the entire completion ceiling. Explicit
DeepSeek-known `disabled` reasoning produced a valid rolling summary at the
same 128-token ceiling.

This is a reasoning/completion allocation result, not a Compact-schema quality
result. The summary was still shadow-only and was not inserted into a task
Prompt.

## Real-provider matrix

Endpoint identity was `https://api.deepseek.com`; model was
`deepseek-v4-flash`; all four calls shared the same source, IDs, schema,
temperature, tokenizer, and read-only boundary. Profiles were explicit and
selected by typed strategy spec, never inferred from the model name.

| strategy | profile / intent | cap | input | output | reasoning | finish | outcome |
|---|---|---:|---:|---:|---:|---|---|
| `default_128` | generic v1 / provider default | 128 | 495 | 128 | 128 | `length` | reasoning exhausted completion |
| `disabled_128` | DeepSeek-known v1 / disabled | 128 | 416 | 68 | — | `stop` | valid summary |
| `default_256` | generic v1 / provider default | 256 | 495 | 256 | 256 | `length` | reasoning exhausted completion |
| `enabled_high_128` | DeepSeek-known v1 / enabled high | 128 | 495 | 128 | 128 | `length` | reasoning exhausted completion |

Totals reconcile for every attempt. The three failed attempts were
`InvalidLLMResponseError`, validation category, with no retry recommendation.
The successful disabled attempt returned a source-linked summary of 65 local
tokens and finish `stop`; its summary was accepted by the existing rolling
adapter but remained `used_in_prompt=false`.

## Root cause

Increasing the completion ceiling from 128 to 256 did not help: the provider
simply used 256 reasoning tokens and stopped again. Enabling high reasoning at
128 behaved the same as provider default. Disabling reasoning at 128 left 68
tokens for the structured summary and completed successfully.

Therefore the previous failure was not caused by summary schema size,
source-lineage validation, or the deterministic Compact boundary. The causal
signal is the provider's reasoning allocation policy relative to the
completion ceiling.

## Safety boundary

- Four bounded Provider calls; no project/memory mutation.
- No Treatment Prompt or task execution; all responses were shadow-only.
- Current deterministic context and global defaults were unchanged.
- The successful response is eligible for recorded replay, but no paired task
  canary was admitted in this experiment.

## Next step

Run a new Compact experiment using an explicit, versioned provider profile and
disabled reasoning only for the supported endpoint. Keep reasoning and Compact
as separate factors, then measure required-state retention, provenance,
verification, task quality, and token/call reduction. Do not make disabled
reasoning the global default from this single four-call campaign.
