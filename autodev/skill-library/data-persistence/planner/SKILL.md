---
name: data-persistence-planner
description: Domain guidance for planning data and persistence phases. Loaded when phase category is DATA.
---

# Data/Persistence Planning Guidance

Data bugs are silent — they survive weak test suites and surface late. Anything that could survive "tests pass" needs an explicit pass criterion.

## Decomposition checklist
- Storage shape (DB vs file vs hybrid) and the declared source of truth.
- Schema design (PKs, FKs, UNIQUE, NOT NULL, CHECK) with normalisation rationale.
- Serialization contract and versioning.
- Migration plan (additive-first, destructive gated, backup/rollback story).
- Cache policy (what is cacheable, TTL, invalidation trigger, size bound) — only if caching is in scope.

## Interfaces & contracts to specify
- Persisted record shape per entity, with field types and nullability.
- Serialization format and a `schema_version` field plus forward/backward compatibility stance.
- Time policy: timezone-aware timestamps stored in UTC, with explicit conversion boundaries at the edge.
- File-I/O contract: path policy (absolute vs relative vs workspace-rooted, `~` expansion, cross-platform), encoding policy (assume non-UTF8 exists), atomic write strategy (temp + rename).

## Edge cases — must enumerate, not generalise
Empty store, single record, duplicate insert, concurrent write, corrupted record on read, missing file, partial write recovery, schema version older or newer than current, non-UTF8 encoding, oversized blob.

## Pass criteria patterns
- Round-trip test per persisted structure and per supported schema version.
- Migration dry-run lints clean; no destructive operation without an explicit, named approval step in the plan.
- Corrupt/missing data triggers the deterministic behaviour the plan named (quarantine / rebuild / error) — asserted by test.
- Cache (if any) has an invalidation test and a bounded-growth test.

## Anti-patterns to avoid
- Any schema change without a migration step in `implementation_plan`.
- Any destructive operation (DROP, DELETE without WHERE, file truncation) without a backup/rollback step paired with it.
- "Atomic write" claimed but implemented as plain open-write-close.
- Times stored as local-zone strings.

## TDD test structure
Minimum: round-trip serialization test per entity, one migration dry-run test, one corrupt-input recovery test, one concurrent-access test if writes can race.
