# TOOLS.md — Roadmap Converter Agent

## Read Access

- `~/.openclaw/ideas/{id}/prd_draft.md` — the PRD to convert or audit
- `~/.openclaw/ideas/{id}/roadmap_draft.md` — the roadmap to audit (alignment and adversarial modes)
- `~/.openclaw/workspace-roadmap-converter/skills/{skill-name}/SKILL.md` — injected skill guidance

## Write Access

All writes are scoped to `~/.openclaw/ideas/{id}/` only.

**Base conversion mode:**
- `~/.openclaw/ideas/{id}/roadmap_draft.md` — the generated roadmap (written first)
- `~/.openclaw/ideas/{id}/roadmap_draft.done` — sentinel (written last)

**Alignment check mode:**
- `~/.openclaw/ideas/{id}/alignment_report.md` — the gap analysis report (written first)
- `~/.openclaw/ideas/{id}/roadmap_draft.md` — updated roadmap, only if gaps were found (written second)
- `~/.openclaw/ideas/{id}/alignment_report.done` — sentinel (written last)

**Adversarial review mode:**
- `~/.openclaw/ideas/{id}/adversarial_report.md` — the risk assessment report (written first)
- `~/.openclaw/ideas/{id}/adversarial_report.done` — sentinel (written last)

## Sentinel Rule

The sentinel `.done` file is always written **last**, after all primary output files are complete. The server polls for the sentinel and immediately reads the output file — writing the sentinel before the output file is complete causes data corruption.

## Explicitly Denied

- `edit` — cannot apply patches to files
- `apply_patch` — not available
- `exec` — cannot run shell commands or scripts
- `browser` — not available
- Writing to `prd_draft.md` — this file is read-only input
- Writing to any path outside `~/.openclaw/ideas/{id}/`

## Path Convention

- ✅ CORRECT: `~/.openclaw/ideas/abc123/roadmap_draft.md`
- ❌ WRONG: relative paths, `/home/pi/...`, or any path outside `~/.openclaw/ideas/`
