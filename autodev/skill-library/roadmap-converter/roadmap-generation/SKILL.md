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
```

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
