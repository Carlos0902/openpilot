# Agent Loop Supervisor

## Purpose

This document defines responsibilities outside one in-process runtime attempt.
It does not grant permission to replay tools or change budgets.

## Responsibilities

The supervisor may:

- start a new task run;
- request resume using an explicit `run_id` and `checkpoint_id`;
- retain checkpoint and trajectory directories;
- report a process exit, timeout, or unavailable dependency;
- apply a user-authorized budget extension as a separate recorded decision.

The supervisor must not:

- infer that the latest directory belongs to the current user request;
- reset consumed runtime budget;
- refund or discard unknown enhancement reservations;
- replay a mutating or indeterminate tool call;
- edit checkpoint JSON to force compatibility;
- treat terminal output as the recovery source of truth.

## Restart policy

Automatic restart is allowed only when resume preflight returns
`recoverability=recoverable_now` and
`automation_policy=automatic_allowed`. The supervisor executes only the typed
`recovery_mode`; it must not interpret `reason`, terminal output, or the legacy
`decision` field.

`recoverable_after_action` stops scheduling until the recorded user/external
action occurs. `manual_only` displays the typed fallback but does not execute
it. `not_recoverable` terminates the current run while preserving evidence;
`offer_new_linked_run` still requires explicit authorization and cannot be used
to bypass an indeterminate side effect. Repeated process failure remains
bounded by the restored recovery budget.

Resume restores enhancement reservation/reconciliation ledgers with their
aggregate counters. Rebuilding the same logical model call must reuse its
reservation and provider replay identity; the supervisor must not reset the
ledger, manufacture a new call key, or settle an observed response twice.

`run_lease_active` is not evidence that the original process failed. The
supervisor must wait and re-assess; it must not launch a concurrent writer or
append recovery events to that run while the lease is held.

When a user explicitly selects an older immutable checkpoint, the runtime may
use it as the state source, but new checkpoints must append after the run's
current latest generation and record that source ID. The supervisor must never
rewind `latest_checkpoint.json` or delete the intervening failed attempt.
