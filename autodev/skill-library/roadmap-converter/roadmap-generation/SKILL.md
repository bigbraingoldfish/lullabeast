# SKILL: Roadmap Generation
# Agent: roadmap-converter
# Injected for: ideas:{id}:convert-{ts} and ideas:{id}:alignment-{ts} sessions

---

## Purpose

You are generating or updating a phased development roadmap from a PRD. The roadmap
must be machine-readable and executable by an autonomous coding pipeline. Every
structural choice you make directly determines whether the pipeline succeeds or fails.

---

## Roadmap File Header

The roadmap must begin with:

```
# {Project Name} Roadmap
```

Where `{Project Name}` is derived from the PRD's project name or title section.
Do not add any other content before the first phase entry.

---

## Phase ID Format

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

## Checkbox Syntax

Each phase entry begins with a checkbox:

```
- [ ] `{PHASE-ID}` | {PRIORITY} | {Description}
```

- `- [ ]` — pending (not started)
- `- [x]` — complete
- `- [-]` — skipped

Do not use any other checkbox syntax. Do not use `- [~]`, `- [/]`, or any variant.

---

## Priority Values

Priority must be one of: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`

Assign based on whether the feature is foundational for other phases or is a
core deliverable of the PRD.

---

## Required Fields Per Phase

Each phase entry must include all of the following. Do not omit any field.

```markdown
- [ ] `{PHASE-ID}` | {PRIORITY} | {Description}

  > Test: {One-sentence description of how to manually verify this phase works
  end-to-end. Must be specific enough to execute without ambiguity.}

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

  **Behavioral Verification:**
  - **User-observable:** {One sentence in plain English describing what a
    human can do and see after this phase. No code-language terms — the
    sentence should make sense to the person who wrote the PRD.}
  - **How we'll check:** {Concrete procedure the reviewer follows to exercise
    the artifact. Be specific enough to execute without ambiguity — name the
    command, route, file, or interaction. Examples:
    "navigate to /tasks, POST body {title:'x'}, assert response 201 and
    body.id is non-empty"; "run `mycli add 'buy milk'`, expect exit 0 and
    `mycli list` to show 'buy milk'".}
  - **If this fails, the user sees:** {One sentence in plain English that
    the executor's retry feedback and the escalation advisory surface when
    this verification cannot be completed. Phrased as the user would read
    it on the dashboard — no stack traces, no jargon.}
```

The Behavioral Verification block is project-type-agnostic: the *content*
differs across web-app / API / CLI / data-pipeline / game / library /
automation, but the *shape* is identical across all phases of all project
types. Every phase needs all three sub-bullets — none are optional.

Anchor every claim:
- The **User-observable** sentence must trace to a user story or functional
  requirement in the PRD. If the PRD does not state what the user should see
  after this phase, the phase scope is wrong, not the verification.
- The **How we'll check** procedure must be runnable from the project root
  with no manual setup beyond what prior phases produced. If the procedure
  requires a database to be pre-seeded, the seed step belongs in a prior
  phase or in this phase's Entry Criteria.
- The **If this fails** sentence must describe the user-facing symptom, not
  the technical fault. "The task list page does not load and shows a blank
  area where the table should be" is correct; "GET /api/tasks returns 500"
  is not — the user does not see HTTP status codes.

---

## Phase Granularity Rules

- No phase should require more than one agent context window to complete
- If a feature area is complex (multiple subsystems, large file counts, or multi-step
  integrations), split it into multiple phases with clear handoff points between them
- Each phase should produce a discrete, testable artifact: a working module, a
  passing test suite, a deployed configuration — not "partially complete" work
- A phase that says "implement the entire X system" is too large. Break it into:
  data model, business logic, API layer, integration, each as a separate phase

---

## Git Branch Convention

Each phase gets its own git branch named after its phase ID in lowercase kebab-case:

```
phase/{phase-id-lowercase}
```

Examples: `phase/core-e1`, `phase/ui-e1`, `phase/api-e1`

Include this in the phase description where relevant to the executor's workflow.

---

## Ordering Rules

Phases must be ordered by dependency:
1. Infrastructure and configuration phases first
2. Data layer and persistence phases next
3. Business logic and core functionality
4. API and integration layers
5. UI and presentation layers
6. Testing and quality phases last (if separate)

A phase must not appear before a phase it depends on. The executor runs phases
in the order they appear in the roadmap.

---

## Failure Handling

If the PRD is too ambiguous to produce a faithful roadmap, write a single phase:

```markdown
- [ ] `transformation-aborted` | HIGH | Roadmap generation aborted

  <!-- Reason: {Specific explanation of what was missing or too ambiguous
  to generate a phase for. Quote the problematic PRD section if possible.} -->

  **Entry Criteria:** N/A
  **Exit Criteria:** N/A
  **TDD Requirements:** N/A
  **Done Criteria:**
  - [ ] Human reviews this phase and updates the PRD before retrying
```

Do not produce a partial roadmap with some real phases and a transformation-aborted
phase. Either the roadmap is complete or it is aborted.

---

## What Not to Include

- Do not add phases for "deployment" or "go-live" unless the PRD explicitly requires them
- Do not add phases for "monitoring" or "observability" unless explicitly in the PRD
- Do not add a "project setup" phase unless the PRD specifies initializing a new codebase
- Do not add prose, section headers, or explanatory text outside of phase entries
- Do not add any markdown formatting beyond the defined structure (no horizontal rules,
  no callout blocks, no numbered lists outside of phase entries)

---

## Verification Document Output

In addition to the roadmap, you produce a project-level `verification.md`
document in the same session. This document is derived from the PRD, not
from the roadmap, so it can be written at any point during the conversion.
The pipeline gates and the dashboard read this document to know what kind
of artifact the project will produce and how to exercise it.

The verification doc has exactly the structure below. Every section is
required. Do not add, remove, or rename sections. Keep the entire document
under 80 lines.

```markdown
# Verification

## Project type
{One of: web-app, http-api, cli, library, data-pipeline, game, automation,
desktop-app, mobile-app. Pick the closest match from this list. Do not
invent new project types.}

## Entry point
- Command: `{the single command that starts the artifact for verification}`
- Ready signal: {a concrete, observable signal that the artifact is ready
  to be exercised. Examples:
  "HTTP 200 from http://localhost:5173"; "stdout contains 'Listening on'";
  "process exits with code 0"; "log line 'cli ready' printed to stderr".}

## Public surface
{Distilled from the PRD's user stories — the bullet list of what the
artifact must let the user do. One numbered item per user-observable
capability. These are the things every Behavioral Verification block in
the roadmap will be exercising; if you cannot list them, the PRD itself
is not specific enough and you should write a `transformation-aborted`
phase.}
1. {Capability 1 — plain English}
2. {Capability 2 — plain English}
3. ...

## Verification stack
- Acceptance tool: {The single tool the reviewer uses to exercise the
  Public surface. Examples: playwright, requests + jq, subprocess +
  assertions, sqlite cli, etc. Pick exactly one — the reviewer needs to
  know what to install.}
- Notes: {One or two sentences if the project type has a non-obvious
  verification need. Example for web-app:
  "jsdom-only verification is insufficient; the reviewer launches the
  dev server and inspects the rendered DOM directly." For projects with
  no special needs, omit this field entirely rather than padding it.}
```

**Write order (mandatory):**
1. `~/.openclaw/ideas/{id}/roadmap_draft.md` — full roadmap
2. `~/.openclaw/ideas/{id}/verification_draft.md` — verification doc
3. `~/.openclaw/ideas/{id}/verification_draft.done` — sentinel for the doc
4. `~/.openclaw/ideas/{id}/roadmap_draft.done` — sentinel for the roadmap, last

Writing the roadmap sentinel last guarantees that any consumer who polls
on the roadmap sentinel (the existing convention) finds both artifacts
ready when it observes the file.

**The user never edits `verification.md` directly.** If the doc is wrong,
the fix is to edit the PRD and regenerate. Do not introduce hand-edit
fields. Do not produce per-phase verification docs — there is exactly
one verification.md per project.
