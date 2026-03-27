---
name: data-persistence-executor
description: Domain guidance for implementing data and persistence phases. Loaded when phase category is DATA.
---

# Data/Persistence Implementation Guidance

## Default stance
Be conservative: validate aggressively, fail safely, never use destructive shortcuts.

## File I/O discipline
- Always set explicit encoding for text files (`encoding='utf-8'`).
- Normalize/resolve paths via one helper; never hardcode absolute paths.
- Use atomic writes for important artifacts (temp file → fsync if needed → rename).
- Handle missing files/dirs explicitly (create with clear policy or fail with clear error).
- Close file handles deterministically (context managers).

## Serialization safety
- Implement explicit encoders/decoders for datetime/Decimal/bytes.
- Use envelopes: schema_version + payload + metadata.
- On parse failure: quarantine and surface structured error — do not guess.

## Migrations
- Never run reset/downgrade-to-base as "verification."
- Add "destructive detected → stop" guardrails.
- Prefer deterministic schema diff + lint tools over hand-written migration SQL.

## Required tests
- Constraint tests (unique/FK/not-null/check violations rejected).
- Round-trip: serialize → deserialize → equal for representative + edge inputs.
- Backward compat: old payload versions still load correctly.
- Cache: invalidation correctness + bounded size + stale detection.
