---
name: completion-reviewer
description: Guides the reviewer through generating completion documentation after a successful pipeline run. Loaded when phase category is COMPLETE.
---

# Completion Review Guidance

## Purpose

This is a **documentation-only** pass. The pipeline has finished. Your sole task is to survey what was built and produce human-readable documentation at the project root. Do not add features, fix bugs, refactor code, or change any implementation file.

## Step 1 — Survey the build

Determine the base branch (check `git log --oneline -20` and `git branch -a`; the base is typically `main` or `master`). Then run:

```bash
git diff --name-only <base-branch>...HEAD
```

Read the diff summary. Group changed files by type: source code, tests, config, docs. Note entry points (executables, API mounts, CLI commands, exported functions) that appear in the diff.

## Step 2 — Update or create README.md

Check whether `README.md` exists at the project root.

- **If absent:** Create a minimal `README.md` covering: what the project does (one paragraph), how to install/configure it, how to run it, and how to run the tests. Derive content from the roadmap, PRD, and code structure — do not invent claims.
- **If present:** Add or update a "What's new" section summarising the changes from this pipeline run. Keep the existing structure intact. Do not rewrite sections that are still accurate.

## Step 3 — Append to CHANGELOG.md

If `CHANGELOG.md` does not exist, create it with a standard Keep-a-Changelog header. Append an entry for today's date:

```markdown
## [Unreleased] — YYYY-MM-DD
### Added / Changed / Fixed
- <brief bullet per meaningful change, derived from git diff>
```

Use plain, present-tense language. One bullet per logical change group. Do not list individual files — describe what changed for the user.

## Step 4 — Write completion_report.md

Write `completion_report.md` at the project root. This is the primary artifact. Structure:

```markdown
# Completion Report

**Generated:** YYYY-MM-DD  
**Pipeline run:** <current_phase_raw_id or "all phases">

## What was built
<2–4 sentences. What does the system do now that it didn't do before? What problem does it solve?>

## Entry points
<List executables, API endpoints, CLI commands, or importable modules the caller cares about. Include how to invoke each.>

## How to run
<Exact commands: install, configure, start, test. Copy from README if already accurate.>

## Files changed
<Compact grouped list: source, tests, config, docs. Derive from git diff.>

## Suggested next steps
<3–5 concrete suggestions. What would a developer naturally tackle next? Base on roadmap phases not yet complete, TODOs in the code, or gaps visible in the diff.>
```

Keep the total file under 600 words. Precision beats verbosity.

## Constraints

- Write only to: `README.md`, `CHANGELOG.md`, `completion_report.md` at the project root.
- Do not touch source code, tests, configuration, or any file under `.autodev/`.
- Do not invent features or capabilities that are not visible in the git diff.
- If a file cannot be read or a command fails, skip that step and continue. Do not abort.
- Write the sentinel file (`completion_reviewer.done` or as instructed) only after all three documentation files have been written.

## Attribution

This is a documentation pass, not a code review. There is nothing to attribute to plan/impl/infra. If you find a genuine bug while reading, note it in "Suggested next steps" — do not fix it here.
