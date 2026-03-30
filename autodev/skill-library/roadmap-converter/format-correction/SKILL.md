# SKILL: Roadmap Format Correction
# Agent: roadmap-converter
# Injected for: ideas:{id}:format-correction-{ts} sessions

---

## Purpose

You are fixing the structural formatting of an existing roadmap. The roadmap content
is provided in the message. It failed automated validation.

**Your only job is structural correction — do not change any phase content.**

---

## Constraints (Non-Negotiable)

- Do NOT add phases
- Do NOT remove phases
- Do NOT reorder phases
- Do NOT change phase descriptions, Entry Criteria, Exit Criteria, TDD Requirements,
  or Done Criteria wording
- Do NOT change phase IDs (only fix their format if they use wrong syntax)
- ONLY fix structural issues: checkbox syntax, phase ID format, missing required
  fields that have no content, header structure, file header

If the input is so malformed that you cannot correct it without inventing content
(e.g. all phases are missing TDD Requirements with no inferrable tests), write a
single line to `roadmap_draft.md` explaining why it cannot be corrected, then write
`roadmap_draft.done`. Do not invent phase content.

---

## Output

Write in this order:
1. `~/.openclaw/ideas/{id}/roadmap_draft.md` — the corrected roadmap
2. `~/.openclaw/ideas/{id}/roadmap_draft.done` — empty sentinel file, written LAST

Do not write any other files. Do not produce conversational output.

---

## Target Format Specification

The corrected roadmap must conform exactly to the following specification. This is
the canonical format the pipeline requires.

---

### Roadmap File Header

The roadmap must begin with:

```
# {Project Name} Roadmap
```

Where `{Project Name}` is derived from the PRD's project name or title section.
Do not add any other content before the first phase entry.

---

### Phase ID Format

Every phase must have a unique phase ID in the format:

```
{DISCIPLINE}-{TYPE}{NUMBER}
```

- `DISCIPLINE` is an uppercase prefix indicating the system area (e.g. `CORE`, `UI`, `API`, `AUTH`, `DATA`, `INFRA`, `TEST`, `INT`)
- `TYPE` is a single uppercase letter: `E` for executor phases, `P` for planner phases (most phases are `E`)
- `NUMBER` is a sequential integer starting at 1 within each discipline

Examples: `CORE-E1`, `CORE-E2`, `UI-E1`, `API-E1`, `AUTH-E1`

Phase IDs must be unique across the entire roadmap. Do not reuse a phase ID.

---

### Checkbox Syntax

Each phase entry begins with a checkbox:

```
- [ ] `{PHASE-ID}` | {PRIORITY} | {Description}
```

- `- [ ]` — pending (not started)
- `- [x]` — complete
- `- [-]` — skipped

Do not use any other checkbox syntax. Do not use `- [~]`, `- [/]`, or any variant.

---

### Priority Values

Priority must be one of: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`

---

### Required Fields Per Phase

Each phase entry must include all of the following. Do not omit any field.

```markdown
- [ ] `{PHASE-ID}` | {PRIORITY} | {Description}

  **Entry Criteria:**
  {Comma-separated list of conditions that must be true before this phase begins.
  Must reference specific files, states, or outputs — not vague states.}

  **Exit Criteria:**
  {Comma-separated list of conditions that must be true for this phase to be
  considered complete by the reviewer agent.}

  **TDD Requirements:**
  {List of test files and what each test validates. Format:}
  - `{test_file_name.py}`: {What this test validates}
  - `{test_file_name.py}`: {What this test validates}
  {Minimum 1 test file per phase. Tests must be deterministic and runnable
  without external services.}

  **Done Criteria:**
  - [ ] {Specific verifiable condition 1}
  - [ ] {Specific verifiable condition 2}
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  > Test: {One-sentence description of how to manually verify this phase works
  end-to-end. Must be specific enough to execute without ambiguity.}
```

---

### Git Branch Convention

Each phase gets its own git branch named after its phase ID in lowercase kebab-case:

```
phase/{phase-id-lowercase}
```

Examples: `phase/core-e1`, `phase/ui-e1`, `phase/api-e1`

---

### What Not to Include

- Do not add prose, section headers, or explanatory text outside of phase entries
- Do not add any markdown formatting beyond the defined structure (no horizontal rules,
  no callout blocks, no numbered lists outside of phase entries)
