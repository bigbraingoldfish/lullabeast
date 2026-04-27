# TOOLS.md — Planner Agent

## Available Tools

- **File read** — Read existing source files, configuration, and pipeline JSON. Use to understand codebase structure before writing your plan. Always read `pipeline-project/.autodev/pipeline/current_phase.json` and `pipeline-project/.autodev/pipeline/phase_state.json` at the start of every invocation.
- **File write** — Write output files ONLY. Your two permitted write targets:
  - `pipeline-project/.autodev/pipeline/planner_output.json` (your plan output)
  - `pipeline-project/.autodev/pipeline/planner_output.done` (sentinel — written last, after JSON is complete)
- **Shell execution** — Run read-only shell commands: `ls`, `find`, `grep`, `head`, `cat` to explore project structure and understand existing code. Do NOT run destructive commands, install packages, or execute build/test scripts.

## Path Convention

All output files use workspace-relative paths through the `pipeline-project/` symlink inside your workspace. The symlink is transparent — write to it as if it were a regular directory.

- ✅ CORRECT: `pipeline-project/.autodev/pipeline/planner_output.json`
- ❌ WRONG: `~/.openclaw/pipeline-project/.autodev/pipeline/planner_output.json` (absolute path — silently discarded by sandbox)
- ❌ WRONG: `/home/pi/.openclaw/pipeline-project/.autodev/pipeline/planner_output.json` (same problem)

## Explicit Denials

- Do NOT write or modify any source code files or test files
- Do NOT modify `current_phase.json`, `phase_state.json`, or any pipeline orchestration state file
- Do NOT install packages or execute build, test, or deploy commands
- Do NOT write any file outside your two permitted write targets
- Do NOT send messages via Signal or any other messaging channel — you do not have messaging rights. All Signal/SMS communication is exclusively handled by the escalation agent. If you receive a Signal message (which should not happen), do not reply to it.
