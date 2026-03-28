# Task 02 — Roadmap Converter Agent + Alignment & Adversarial Checks

*AutoDev MVP pre-tester work. Claude Code should read this document in full before planning.*

---

## Background

The current roadmap generation flow sends the PRD content to the `prd-creator`
agent via a throw-away single-shot session. This agent is optimized for
conversational PRD elicitation — its IDENTITY, SOUL, TOOLS, and AGENTS documents
are all written for interactive question-asking with a user. Asking it to perform
a one-shot analytical document transformation while ignoring that guidance produces
lower quality output and is harder to tune independently.

This task creates a dedicated `roadmap-converter` agent with its own workspace,
identity documents, and skills — all pointing at single-shot document transformation
rather than conversation. It also introduces two optional quality check passes
(alignment check and adversarial review) that run through this same agent with
different skill injections, and a post-roadmap popup that offers these checks
to the user immediately after generation.

The `roadmap-converter` agent registration in `openclaw.json` is handled by the
installer (Task 01 Step 9). This task assumes that registration exists.

---

## Architecture Summary

All three operations (base conversion, alignment check, adversarial check) share
the same execution pattern:

1. Server injects the appropriate skill into `workspace-roadmap-converter/skills/`
2. Server POSTs to OpenClaw webhook with `agentId: "roadmap-converter"` and a
   unique session key
3. Agent reads input documents, applies its skill guidance, writes output file(s)
   plus a sentinel `.done` file
4. Server polls for sentinel, reads output, stores result
5. For alignment and adversarial: server injects a brief notification into the
   active PRD conversational session so the prd-creator agent has visibility

The prd-creator agent is an informed observer for checks — it receives a
notification of what changed but is not asked to do anything unless it identifies
a genuine PRD-level misalignment that warrants a question to the user.

---

## New Files to Create

### Agent workspace documents
All live in `autodev/agents/roadmap-converter/` and are deployed to
`~/.openclaw/workspace-roadmap-converter/` by the installer.

**IDENTITY.md**
- Role: single-shot document transformation agent
- Receives structured input documents (PRD, roadmap, or both)
- Produces a single structured output document per invocation
- No conversational interaction — no questions, no clarifying asks
- Emphasis: format precision, completeness, consistency with pipeline
  gate script expectations

**SOUL.md**
- Precise, analytical, format-disciplined
- When in doubt about scope, do less and be explicit about what was omitted
- Never invent requirements not present in source documents
- Output quality is measured by whether the pipeline can execute the roadmap
  without ambiguity

**TOOLS.md**
- Read access: `~/.openclaw/ideas/{id}/prd_draft.md`,
  `~/.openclaw/ideas/{id}/roadmap_draft.md`,
  `~/.openclaw/ideas/{id}/session.json`
- Write access: `~/.openclaw/ideas/{id}/roadmap_draft.md`,
  `~/.openclaw/ideas/{id}/roadmap_draft.done`,
  `~/.openclaw/ideas/{id}/alignment_report.md`,
  `~/.openclaw/ideas/{id}/alignment_report.done`,
  `~/.openclaw/ideas/{id}/adversarial_report.md`,
  `~/.openclaw/ideas/{id}/adversarial_report.done`
- No edit tool, no browser, no shell
- Always write the sentinel `.done` file last, after the primary output is complete
- Explicit denial: do not write to any pipeline project directories,
  do not write to prd_draft.md

**AGENTS.md**
Full behavioral contract covering all three operation modes:

*Base conversion mode* (session key: `ideas:{id}:convert-{ts}`):
- Input: conversion prompt + PRD content (provided in webhook message)
- Output: roadmap_draft.md in canonical pipeline format, then roadmap_draft.done
- Format requirements: see roadmap-generation skill (injected before session)
- Never ask questions — if PRD is ambiguous, make a reasonable assumption and
  note it as a comment in the roadmap

*Alignment check mode* (session key: `ideas:{id}:alignment-{ts}`):
- Input: prd_draft.md + roadmap_draft.md (read from disk at session start)
- Output: alignment_report.md containing gap analysis, then updated
  roadmap_draft.md if gaps found, then roadmap_draft.done, then
  alignment_report.done
- Write order: report first, then roadmap update, then sentinels
- If no gaps found: write alignment_report.md noting all clear,
  do not touch roadmap_draft.md, write alignment_report.done only

*Adversarial review mode* (session key: `ideas:{id}:adversarial-{ts}`):
- Input: prd_draft.md + roadmap_draft.md (read from disk at session start)
- Output: adversarial_report.md containing phase-by-phase risk assessment,
  then adversarial_report.done
- Does not modify roadmap_draft.md — analysis only
- For each phase: confidence score (0–100), specific failure hypothesis,
  recommended mitigation if confidence < 70

**USER.md**
- Audience: automated system invocation, not a human
- NO_REPLY prohibition: never produce conversational output
- No greetings, no acknowledgments, no "I'll now..." preamble
- First token of output should be the first token of the document being written

---

## New Skills to Create

All live in `autodev/skill-library/roadmap-converter/` and are injected into
`~/.openclaw/workspace-roadmap-converter/skills/` before each session.

### `roadmap-generation/SKILL.md`
Injected for: base conversion sessions and alignment check sessions
(when roadmap update is needed)

Content must cover:
- Canonical phase ID format: `{DISCIPLINE}-{PHASE_NUMBER}` (e.g. `INFRA-E1`)
- Checkbox syntax: `- [ ]` pending, `- [x]` complete, `- [-]` skipped
- Required fields per phase: phase ID, description, entry criteria,
  exit criteria, TDD requirement (must include test file names and
  what each test validates), done criteria checklist
- Phase granularity rules: no phase should require more than one
  agent context window to complete; if a feature is complex, split it
- Git convention: each phase gets its own branch named after the phase ID
- Roadmap file must start with `# {Project Name} Roadmap` header
- No markdown beyond the defined checkbox and heading structure

### `alignment-check/SKILL.md`
Injected for: alignment check sessions only

Content must cover:
- Read prd_draft.md first, extract all stated requirements and goals
- Read roadmap_draft.md, map each phase to the PRD requirements it addresses
- Gap identification: requirements in PRD with no corresponding phase
- Inflation identification: phases in roadmap with no PRD backing
  (scope creep introduced during conversion)
- Output format for alignment_report.md:
  ```
  # Alignment Report
  ## Gaps (PRD requirements not covered by roadmap)
  - {requirement}: {recommended fix}
  ## Inflation (roadmap phases not backed by PRD)
  - {phase ID}: {assessment}
  ## Overall Assessment
  {one paragraph}
  ```
- If gaps exist: update roadmap_draft.md to address them using the
  roadmap-generation skill format rules
- Be conservative — only add phases where the gap is clear and material

### `adversarial-review/SKILL.md`
Injected for: adversarial review sessions only

Content must cover:
- Read both documents with the goal of finding failure points, not
  validating the plan
- For each phase, construct a specific failure hypothesis — not generic
  risk statements but concrete scenarios: "this phase will fail because
  the executor will hit context window limits attempting X"
- Confidence score 0–100 where 100 = certain to succeed, 0 = certain to fail
- Flag any phase below 70 as high-risk with a recommended mitigation
- Output format for adversarial_report.md:
  ```
  # Adversarial Review
  ## Phase Risk Assessment
  | Phase ID | Confidence | Failure Hypothesis | Mitigation |
  |----------|-----------|-------------------|------------|
  ...
  ## Highest Risk Phases
  {top 3 phases most likely to fail, with detailed reasoning}
  ## Overall Pipeline Confidence
  {score}/100 — {one paragraph assessment}
  ```
- Do not modify any files other than adversarial_report.md

---

## Server Changes — `ui/server.py`

### Skill injection helper
Add a `_inject_converter_skill(skill_name: str)` function that:
- Resolves skill source from `AUTODEV_REPO_PATH/autodev/skill-library/roadmap-converter/{skill_name}/SKILL.md`
- Copies to `AUTODEV_ROOT/workspace-roadmap-converter/skills/{skill_name}/SKILL.md`
- Creates directory if not present
- Uses atomic write (mkstemp + os.replace)
- Called before each converter session POST

### Update existing `/api/ideas/{id}/convert` endpoint
- Change `agentId` from `WEBHOOK_AGENT_ID` (`prd-creator`) to
  `ROADMAP_CONVERTER_AGENT_ID = "roadmap-converter"`
- Inject `roadmap-generation` skill before POSTing
- Session key pattern: `ideas:{id}:convert-{timestamp_ms}` — unchanged
- All other behavior unchanged

### New endpoint: `POST /api/ideas/{id}/alignment-check`
- Inject `roadmap-generation` and `alignment-check` skills
- Build webhook payload: instruct agent to read prd_draft.md and
  roadmap_draft.md, produce alignment_report.md, update roadmap if
  needed, write sentinels
- Session key: `ideas:{id}:alignment-{timestamp_ms}`
- Poll for `alignment_report.done` (timeout: 180s, interval: 2s)
- On completion: read alignment_report.md, read updated roadmap_draft.md,
  store both in session.json
- Inject PRD agent notification (see below)
- Return: `{"alignment_report": str, "roadmap_updated": bool,
  "roadmap_content": str | null}`

### New endpoint: `POST /api/ideas/{id}/adversarial-check`
- Inject `adversarial-review` skill only
- Build webhook payload: instruct agent to read both documents,
  produce adversarial_report.md, write sentinel
- Session key: `ideas:{id}:adversarial-{timestamp_ms}`
- Poll for `adversarial_report.done` (timeout: 180s, interval: 2s)
- On completion: read adversarial_report.md, store in session.json
- Inject PRD agent notification (see below)
- Return: `{"adversarial_report": str}`

### PRD agent notification injection
After alignment or adversarial check completes, POST a system notification
to the active PRD conversational session:

For alignment check:
```
[SYSTEM] Alignment check complete. {N} gaps found.
{alignment_report summary — first 3 bullet points only}
Roadmap has been updated. Review before proceeding to setup.
```

For adversarial check:
```
[SYSTEM] Adversarial review complete. Pipeline confidence: {score}/100.
{top 3 high-risk phases from report}
Full report available. No roadmap changes were made.
```

Session key for notification: the most recent `ideas:{id}:session-{n}` key
(highest n). Find by scanning session.json turn history for the latest
session key. POST as a user-role message to the existing session so the
prd-creator agent has visibility without being asked to act.

### New constants
```python
ROADMAP_CONVERTER_AGENT_ID = "roadmap-converter"
ALIGNMENT_CHECK_TIMEOUT = 180
ADVERSARIAL_CHECK_TIMEOUT = 180
```

---

## UI Changes — `ui/index.html`

### Post-roadmap generation popup
After `doConvert()` succeeds and `roadmapContent` is set, display a modal:

```
✓ Roadmap generated

Would you like to run optional quality checks before setup?
These passes are experimental and will update your roadmap if issues are found.

[Run Alignment Check]  [Run Adversarial Review]  [Skip — Continue to Setup]
```

- Modal appears automatically after successful roadmap generation
- Dismissible via Skip or clicking outside
- Each button triggers the respective check and closes the modal
- If dismissed, both checks remain available in the kabob menu

### Kabob menu additions
Add two new items to the existing ⋮ menu (after Download PRD, Download Roadmap):
- `Run Alignment Check` — calls `POST /api/ideas/{id}/alignment-check`
- `Run Adversarial Review` — calls `POST /api/ideas/{id}/adversarial-check`
- Both items only visible when `roadmapContent` is non-empty

### Check result display
After either check completes, display the report in a scrollable modal with:
- Report title and overall assessment prominently at top
- Full report content in a markdown renderer (reuse existing `msg-md` styling)
- A close button
- For alignment check: if roadmap was updated, show
  "Roadmap updated — {N} changes made" badge before the close button

### Loading states
During check execution show a loading indicator in place of the modal content:
- "Running alignment check..." or "Running adversarial review..."
- Animated pulse, same style as existing drafting indicators

---

## Config Changes

### `ui/config.json` additions
```json
"roadmap_converter_workspace": "~/.openclaw/workspace-roadmap-converter",
"alignment_check_prompt_path": "~/.openclaw/workspace-roadmap-converter/skills/alignment-check/SKILL.md",
"adversarial_check_prompt_path": "~/.openclaw/workspace-roadmap-converter/skills/adversarial-review/SKILL.md"
```

### `autodev/config/prompts/` — new directory
Move the conversion prompt reference here for discoverability:
- `autodev/config/prompts/roadmap-generation.txt` — symlink or copy of
  existing conversion prompt file
- `autodev/config/prompts/` is the canonical home for all agent prompt files
  going forward

---

## Testing Requirements

**Philosophy**: TDD. Write tests first, implement to pass them.
Mock all external dependencies — no real OpenClaw webhook calls,
no real filesystem writes to `~/.openclaw/`.

### What to mock
- `aiohttp` POST to webhook (return 200 OK)
- Filesystem polling for `.done` sentinel files — use temp directory fixture
- `session.json` reads and writes
- `_inject_converter_skill` — verify it's called with correct skill name
  before each webhook POST

### Test coverage required

**`/api/ideas/{id}/convert` (updated)**
- Skill injection called with `roadmap-generation` before POST
- agentId in payload is `roadmap-converter` not `prd-creator`
- Existing behavior preserved: polls for done, reads roadmap, returns content

**`/api/ideas/{id}/alignment-check`**
- Returns 404 if idea not found
- Returns 400 if roadmap_draft.md does not exist (can't align without roadmap)
- Injects both `roadmap-generation` and `alignment-check` skills before POST
- Session key matches `ideas:{id}:alignment-{ts}` pattern
- On sentinel detection: reads report, reads roadmap, returns both
- PRD agent notification is POSTed after completion
- Timeout returns 408 with clear message

**`/api/ideas/{id}/adversarial-check`**
- Returns 400 if roadmap_draft.md does not exist
- Injects `adversarial-review` skill before POST
- Session key matches `ideas:{id}:adversarial-{ts}` pattern
- Does not update roadmap_draft.md (verify no write occurs)
- PRD agent notification POSTed after completion
- Timeout returns 408

**`_inject_converter_skill`**
- Creates skill directory if missing
- Copies SKILL.md atomically
- Source path not found: raises clear error
- Idempotent: calling twice does not corrupt the file

---

## Implementation Constraints

- `prd-creator` agentId must not appear in any converter session payload —
  all converter sessions use `roadmap-converter`
- Skill injection must always happen before the webhook POST, never after
- Sentinel write order in agent sessions is enforced via AGENTS.md guidance:
  primary output file first, then sentinel — never reverse this
- PRD agent notification must not block the check response — fire and
  forget if the notification POST fails (log warning, do not 500)
- All new path construction uses `AUTODEV_ROOT` and `AUTODEV_REPO_PATH`
- No hardcoded model names in server.py — model selection is OpenClaw's
  responsibility via openclaw.json

---

## Claude Code Instructions

**Before any changes:**
```
git add -A && git commit -m "pre-roadmap-converter: checkpoint"
```
Confirm hash before proceeding.

**Process:**
1. Planning phase first. Read in full:
   - ui/server.py
   - ui/index.html
   - autodev/agents/prd-creator/ (all files — reference for agent doc style)
   - autodev/skill-library/ (reference for skill doc style)
   - autodev/config/skill_mapping.yaml (reference for skill structure)
   Wait for plan approval before writing anything.

2. Write tests first (TDD). All tests should fail initially.

3. Create agent workspace documents in autodev/agents/roadmap-converter/.

4. Create skill documents in autodev/skill-library/roadmap-converter/.

5. Implement server.py changes.

6. Implement ui/index.html changes.

7. Manual verification checklist:
   - Click Generate Roadmap — post-generation popup appears
   - Click Run Alignment Check — loading state shows, report modal appears
   - Click Run Adversarial Review — loading state shows, report modal appears
   - Both checks appear in kabob menu when roadmap exists
   - PRD chat receives notification message after each check
   - All new tests pass: `pytest tests/ -q`
   - agentId in all converter webhook payloads is "roadmap-converter"
   - Skill files are present in workspace-roadmap-converter/skills/ after each run

8. After verification:
```
git add -A
git commit -m "roadmap-converter: dedicated agent, alignment check, adversarial review"
git push origin main
```
