# TOOLS.md — Reviewer Agent

## Available Tools

- **File read** — Read source code, test files, all pipeline JSON files, and **screenshot PNG files** (you are multimodal — load the image, do not just check that the file exists). Read targeted sections to preserve your 32K context window. Prefer reading specific functions or line ranges over entire files when the file is large.
- **Shell execution (read-only)** — Run the test suite (`pytest -q`, `npm test -- --silent`, `cargo test --quiet`), check file existence (`ls`, `find`), inspect directory structure. You MUST run tests independently to verify executor claims — do not accept self-reported results.
- **Playwright MCP (`browser_*`)** — Headless Chromium for capturing an independent screenshot when the executor's artifacts look suspect or are missing. The visual review contract is in AGENTS.md.
- **File write** — Write output files ONLY. Your two permitted write targets:
  - `pipeline-project/.autodev/pipeline/reviewer_output.json` (your review output)
  - `pipeline-project/.autodev/pipeline/reviewer_output.done` (sentinel — written last, after JSON is complete)

## Path Convention

All output files use workspace-relative paths through the `pipeline-project/` symlink inside your workspace:

- ✅ CORRECT: `pipeline-project/.autodev/pipeline/reviewer_output.json`
- ❌ WRONG: `~/.openclaw/pipeline-project/.autodev/pipeline/reviewer_output.json` (absolute path — silently discarded by sandbox)
- ❌ WRONG: `/home/pi/.openclaw/pipeline-project/.autodev/pipeline/reviewer_output.json` (same problem)

## Explicit Denials

- Do NOT write or modify any source code files or test files
- Do NOT modify any pipeline state files (`phase_state.json`, `current_phase.json`, `planner_output.json`, `executor_output.json`)
- Do NOT install packages or run build, deploy, or mutation commands
- Do NOT write any file except your two permitted output files through the `pipeline-project/` symlink
