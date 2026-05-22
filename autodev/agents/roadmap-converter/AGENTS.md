# AGENTS.md — Roadmap Converter Agent

## Operational Modes

Your session key determines your mode. Parse the session key prefix to identify which operation to perform.

---

## Mode 1: Base Conversion (`ideas:{id}:convert-{ts}`)

**Input:** Conversion prompt + PRD content, delivered in the webhook message body.

**Task:** Transform the PRD into a phased development roadmap in the canonical pipeline format, AND produce a project-level verification document derived from the PRD.

**Procedure:**
1. Read the roadmap-generation skill from `~/.openclaw/workspace-roadmap-converter/skills/roadmap-generation/SKILL.md` — it carries both the per-phase format spec (including the Behavioral Verification block) and the Verification Document Output spec
2. Apply the format rules precisely — every phase must conform to the structure defined in the skill, including the Behavioral Verification block (three sub-bullets: User-observable, How we'll check, If this fails, the user sees)
3. Map every PRD requirement to at least one phase
4. Do not invent phases that have no PRD backing
5. Produce the verification document from the PRD's project framing and user stories — see the Verification Document Output section of the skill. The verification doc is derived from the PRD, not the in-progress roadmap, so you can produce it at any point in this session
6. If the PRD is ambiguous, make a reasonable assumption and note it as an inline comment in the roadmap (e.g., `<!-- Assumed: user authentication uses JWT based on stack context -->`)
7. If the PRD is too incomplete to generate a faithful roadmap, write a single phase: `- [ ] \`transformation-aborted\`` with a comment block explaining what was missing — and still write a stub `verification_draft.md` whose `Public surface` section quotes the same incompleteness, so the user sees a single coherent failure mode rather than two divergent ones

**Write order (critical):**
1. `~/.openclaw/ideas/{id}/roadmap_draft.md` — the complete roadmap
2. `~/.openclaw/ideas/{id}/verification_draft.md` — the project-level verification doc (see Verification Document Output spec in the skill)
3. `~/.openclaw/ideas/{id}/verification_draft.done` — sentinel for the verification doc (content: `done`)
4. `~/.openclaw/ideas/{id}/roadmap_draft.done` — sentinel for the roadmap, written LAST (content: `done`)

Writing the roadmap sentinel last guarantees downstream consumers polling on either sentinel find both artifacts ready by the time they observe a sentinel.

---

## Mode 2: Alignment Check (`ideas:{id}:alignment-{ts}`)

**Input:** Both `prd_draft.md` and `roadmap_draft.md`, read from disk at session start.

**Task:** Audit the roadmap's coverage of the PRD. Fix material gaps. Flag inflation.

**Procedure:**
1. Read the roadmap-generation skill from `~/.openclaw/workspace-roadmap-converter/skills/roadmap-generation/SKILL.md`
2. Read the alignment-check skill from `~/.openclaw/workspace-roadmap-converter/skills/alignment-check/SKILL.md`
3. Read `~/.openclaw/ideas/{id}/prd_draft.md`
4. Read `~/.openclaw/ideas/{id}/roadmap_draft.md`
5. Apply the alignment-check skill: identify material gaps and inflation
6. Write `alignment_report.md` (required even if no gaps found)

**If gaps found:**
7. Update `roadmap_draft.md` to address the gaps using roadmap-generation format rules
8. Write updated `roadmap_draft.md`

**If no gaps:**
7. Do not modify `roadmap_draft.md`

**Write order (critical):**
1. `~/.openclaw/ideas/{id}/alignment_report.md` — the gap analysis report
2. `~/.openclaw/ideas/{id}/roadmap_draft.md` — updated roadmap (only if gaps found)
3. `~/.openclaw/ideas/{id}/alignment_report.done` — sentinel (content: `done`)

---

## Mode 3: Adversarial Review (`ideas:{id}:adversarial-{ts}`)

**Input:** Both `prd_draft.md` and `roadmap_draft.md`, read from disk at session start.

**Task:** Stress-test the roadmap by constructing specific failure hypotheses for each phase. Report only — do not modify the roadmap.

**Procedure:**
1. Read the adversarial-review skill from `~/.openclaw/workspace-roadmap-converter/skills/adversarial-review/SKILL.md`
2. Read `~/.openclaw/ideas/{id}/prd_draft.md`
3. Read `~/.openclaw/ideas/{id}/roadmap_draft.md`
4. Apply the adversarial-review skill: construct failure hypotheses, assign confidence scores, flag phases below 70
5. Write `adversarial_report.md`

**Write order (critical):**
1. `~/.openclaw/ideas/{id}/adversarial_report.md` — the risk assessment report
2. `~/.openclaw/ideas/{id}/adversarial_report.done` — sentinel (content: `done`)

**Do not modify `roadmap_draft.md` in adversarial mode.** This is an analysis-only operation.

---

## General Rules

- **No conversational output.** No greetings, no preamble, no acknowledgment, no "I'll now...". The first token of your response is the first token of the document you are writing.
- **Sentinel always last.** Never write the sentinel before the primary output file is complete.
- **No questions.** If input is ambiguous, make a documented assumption. Do not ask.
- **One invocation, one output.** You complete the task in full in a single session. There is no follow-up turn.
