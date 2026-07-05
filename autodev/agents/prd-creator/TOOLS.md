# TOOLS.md — PRD Creator Agent

## Available Tools

- **File write** — Write output files to `~/.openclaw/ideas/{id}/` only. Your permitted write targets per turn:
  - `~/.openclaw/ideas/{id}/turns/{n}.md` — your full response (written first)
  - `~/.openclaw/ideas/{id}/prd_draft.md` — current complete PRD state (written second)
  - `~/.openclaw/ideas/{id}/turns/{n}.done` — sentinel file, content: `done` (written last)
  - `~/.openclaw/ideas/{id}/clarity_result.json` — JSON clarity result (when invoked for clarity check)
  - `~/.openclaw/ideas/{id}/clarity_result.done` — clarity sentinel (when invoked for clarity check)
  - `~/.openclaw/ideas/{id}/readiness.json` — readiness assessment (readiness sessions only, written first)
  - `~/.openclaw/ideas/{id}/readiness.done` — readiness sentinel (readiness sessions only, written last)

- **File read** — Read your own prior output for continuity:
  - `~/.openclaw/ideas/{id}/turns/*.md` — prior turn responses
  - `~/.openclaw/ideas/{id}/prd_draft.md` — current PRD state
  - `~/.openclaw/ideas/{id}/conversation_log.md` — server-maintained full turn history (read-only; the server is the sole writer)

## Path Convention

- ✅ CORRECT: `~/.openclaw/ideas/abc123/turns/3.md`
- ❌ WRONG: relative paths or paths outside `~/.openclaw/ideas/` — use full tilde-expanded paths

## Explicitly Denied

- `edit` — cannot apply patches to files
- `apply_patch` — not available
- `exec` — cannot run shell commands or scripts
- `process` — cannot manage processes or services
- `browser` — not available

You read your own idea output and write your idea output files only. You have no access to pipeline project directories, system files, or anything outside `~/.openclaw/ideas/`.
