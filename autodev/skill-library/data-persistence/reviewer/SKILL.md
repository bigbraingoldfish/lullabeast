---
name: data-persistence-reviewer
description: Domain guidance for reviewing data and persistence phases. Loaded when phase category is DATA.
---

# Data/Persistence Review Guidance

## Core principle
Data work can be "green" and still wrong. Review for invariants, reversibility, and silent corruption.

## Verify beyond tests
- Schema: constraints and indexes exist and match intent; no accidental denormalization.
- Serialization: round-trip tests exist; timezone policy explicit; no naive datetimes.
- Migrations: dry-run/lint present; destructive ops gated; rollback story exists.
- File I/O: explicit encoding, atomic writes for critical paths, cross-platform paths.
- Cache: explicit keying+TTL+invalidation; size bounded.

## Independent checks
- Corrupt/missing file simulation — verify deterministic recovery.
- Cross-timezone or timezone-mocked tests for datetime handling.
- Regression: previously passing behavior remains passing.

## Attribution
- Plan: did not forbid destructive operations or require approval gate.
- Impl: plan forbade it but implementation allows it anyway.
- If tests exist but miss integrity invariants: check whether plan lacked pass criteria (plan) or executor skipped them (impl).
