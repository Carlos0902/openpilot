# OpenPilot Documentation Index

This directory contains topic-specific documentation that used to be spread
across the repository root.

## Root-level documents kept in place

These stay at the repository root because they are entry points or project-wide
contracts:

- `../README.md`
- `../INSTALL.md`
- `../API.md`
- `../AGENTS.md`
- `../THOUGHT_ARCHITECTURE.md`
- `../Thought.md`

## Topic documents

### Task trajectory / real-task diagnostics

- `./task_trajectory/README.md`

This is the active index for:

- task trajectory evidence design;
- trajectory architecture and implementation plan;
- event and id alignment;
- implementation log;
- real-task failure analyses;
- legacy diagnostics pointers.

### Testing

- `./testing/TEST_DESIGN_GUIDE.md`

Testing guidance aligned with the trajectory-evidence workflow.

### Metadata architecture

- `./metadata/README.md`

This is the single index for the mandatory development convention, authoritative
contract catalog, and non-normative value-nesting research.

### Runtime checkpoint and recovery

- `./runtime_recovery/README.md`

This is the single index for the active comprehensive recovery-boundary roadmap,
checkpoint/storage design, and typed status/fallback design.

## Where the `experiments/` tree lives

Documents in this directory reference paths under `experiments/`. That tree is
**not part of the main branch**. It is one-shot research tooling and evidence
with no product dependency — nothing under `Code/` imports it, and packaging
only collects `Code/src`. It is archived on the `codex/collaboration` branch.

To read or re-run an experiment referenced below, restore the tree locally:

```bash
git checkout origin/codex/collaboration -- experiments/
```

The local copy is ignored by `.gitignore`, so it will not be re-committed to
main by accident.

## Documentation maintenance rule

If a completed implementation changes task trajectory, real-task diagnostics,
tool planning, path grounding, timeout/retry behavior, or read-only guardrails,
update:

- `./task_trajectory/IMPLEMENTATION_LOG.md`

in the same change set.
