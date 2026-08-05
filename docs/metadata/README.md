# Metadata Documentation

This directory is the single documentation entry point for OpenPilot metadata.

## Document status

| Document | Status | Purpose |
| --- | --- | --- |
| `DEVELOPMENT_CONVENTIONS.md` | Normative | Mandatory metadata-first workflow, reuse/duplication review, migration, and test gates. |
| `CONTRACT_CATALOG.md` | Authoritative inventory | Complete public contract list, ownership, lifecycle, and known pressure points. Executable field truth remains in `Code/src/metadata/`. |
| `VALUE_NESTING_RESEARCH.md` | Research / non-normative | Audit of current value nesting and possible future structural pressure. It does not authorize a three-layer or graph migration. |

## Required reading order for metadata changes

1. Read `DEVELOPMENT_CONVENTIONS.md`.
2. Search `CONTRACT_CATALOG.md` and the concrete models for existing semantics.
3. Inspect real producers, consumers, persistence, and tests.
4. Consult `VALUE_NESTING_RESEARCH.md` only as supporting design evidence.

The current implemented architecture remains strict contracts composed mostly by value
into local trees. Broader cross-tree reference or graph designs require separate evidence,
planning, migration, and acceptance; they are not default development policy.
