# TOOLS.md — Executor Agent

## Available Tools

- **File read** — Read planner output, existing source code, test files, and directory structure. Use targeted reads (specific line ranges, `grep` for function names) rather than reading entire large files. Your 96K context window can fill quickly on large codebases.
- **File write** — Create and modify source files, test files, and your output JSON. All pipeline output files go through the `pipeline-project/` symlink directory. Project source files go to their paths within the project root (e.g., `pipeline-project/src/feature.py`).
- **Shell execution** — Run test suites, check file existence, inspect structure, install planned dependencies. Always capture exit codes and stderr on failure. Use minimal verbosity flags (`pytest -q`, `npm test -- --silent`, `cargo test --quiet`) to preserve context window.

## Path Convention

All pipeline output files use workspace-relative paths through the `pipeline-project/` symlink:

- ✅ CORRECT: `pipeline-project/.autodev/pipeline/executor_output.json`
- ✅ CORRECT: `pipeline-project/.autodev/pipeline/executor_output.done`
- ❌ WRONG: `~/.openclaw/pipeline-project/.autodev/pipeline/executor_output.json` (absolute path — silently discarded by sandbox)
- ❌ WRONG: `/home/pi/.openclaw/pipeline-project/.autodev/pipeline/executor_output.json` (same problem)

Source files within the project are also accessed via the symlink, e.g., `pipeline-project/src/feature.py`.

## Explicit Denials

- Do NOT modify `current_phase.json`, `phase_state.json`, `planner_output.json`, or any pipeline orchestration file
- Do NOT modify dependency manifests (`pyproject.toml`, `package.json`, `requirements.txt`, `Cargo.toml`) unless explicitly specified in `implementation_plan`
- Do NOT write files outside the project directory or the `pipeline-project/` output path
