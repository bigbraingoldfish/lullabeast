---
name: data-persistence-planner
description: Domain guidance for planning data and persistence phases. Loaded when phase category is DATA.
---

# Data/Persistence Planning Guidance

## Must-specify decisions (never leave implicit)
- Storage shape: DB vs file vs hybrid; declare source of truth.
- Schema: PKs, FKs, UNIQUE, NOT NULL, CHECK constraints; normalization rationale.
- Serialization contract: format, schema_version field, forward/backward compat stance.
- Time policy: timezone-aware timestamps (UTC) + conversion boundaries.
- Migration policy: additive-first; destructive ops gated; rollback/backup story.
- Cache policy: what is cacheable, TTL, invalidation trigger, size bounds.

## File I/O edge cases to call out
- Encoding: assume non-UTF8 exists; define behavior and error handling.
- Path policy: absolute vs relative vs workspace-rooted; expand ~; cross-platform.
- Atomicity: critical writes must be atomic (temp+rename strategy).
- Corrupt/missing data: define deterministic recovery behavior (quarantine/rebuild/error).

## Pass criteria (hard, verifiable)
- Round-trip tests for each persisted structure and version.
- Migration dry-run + lint passes; no destructive ops without explicit approval.
- Corrupt/missing data behavior is deterministic and tested.
- Cache has explicit invalidation + bounded growth tests.

## Critical rule
If a persistence defect could survive "tests pass," it MUST have an explicit pass criterion. Data bugs are silent — they survive weak test suites and surface late.
