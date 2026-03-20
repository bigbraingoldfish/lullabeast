# UI-E6 — Render Screen 2 with repo path input and roadmap seed input, both with lock/confirm behavior
**Completed:** 2026-03-19T19:00:00Z
**Executor attempts:** 1
**Reviewer passes:** 1 (direct implementation)

## Changes
- `ui/index.html`: Replaced placeholder PreflightScreen with full Screen 2 implementation
  - App: lifted `repoPath`, `repoPathLocked`, `roadmapSeed`, `roadmapSeedLocked` state
  - `navigateToPreflight` sets `roadmapSeed` and `roadmapSeedLocked(true)` when called with content
  - PreflightScreen: repo path text input + lock/unlock toggle (disabled when locked)
  - Roadmap seed: file input (`.md`) when empty, content preview + optional re-upload when populated
  - Pre-populated from Screen 1: shows "From Project Ideas — {preview}", upload suppressed when locked, unlock reveals re-upload
  - Props: seedRoadmap, repoPath, repoPathLocked, roadmapSeed, roadmapSeedLocked, onRepoPathChange, onRepoPathLockToggle, onRoadmapSeedChange, onRoadmapSeedLockToggle, onBack
- `ui/server.py`: Added `POST /api/setup/roadmap-seed` — atomic write to `~/.openclaw/setup_session.json`
- `tests/test_ui_preflight_screen.py`: 11 tests covering rendering, lock behavior, pre-population, FileReader usage
- `tests/test_api_setup_roadmap_seed.py`: 7 tests covering 200/422, atomic write, overwrite, parent dir creation
