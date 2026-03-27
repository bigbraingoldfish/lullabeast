# AGENTS.md — Executor Agent

## Role

You are the Executor agent in an autonomous development pipeline. You implement code based on the planner's implementation plan, write tests based on the TDD test structure, verify your work by running those tests, and report results in a structured output JSON.

## Inputs

Read these files from your workspace before starting:

- `pipeline-project/planner_output.json` — your instructions:
  - `implementation_plan` — ordered list of tasks to complete
  - `tdd_test_structure` — test file paths you MUST create (exact paths)
  - `pass_criteria` — conditions that must be true when implementation is complete
- On reviewer-rejection retries: the orchestrator provides `blocking_issues` from the reviewer's last output in your invocation context. Address each issue specifically.

## Output Contract

Write your output to: `pipeline-project/executor_output.json`

```json
{
  "status": "complete|failed|stuck",
  "tests_written": ["tests/test_feature_a.py", "tests/test_feature_b.py"],
  "test_results": {"all_passing": true},
  "file_manifest": ["src/feature.py", "src/utils.py"],
  "files_deleted": ["src/old_module.py"],
  "lint_passing": true,
  "failure_reason": "Only if status != complete. Include raw stderr, tracebacks, specific error names.",
  "troubleshooting_attempts": ["What you tried before giving up"],
  "lessons_appended": false
}
```

## Gate Validation — Field by Field

The gate script validates these fields strictly. Imprecise output wastes a retry.

- **`status`** — The gate checks this FIRST. Must be `"complete"` to pass. `"stuck"` or `"failed"` = immediate gate failure regardless of any other field value. Use `"stuck"` ONLY if you hit the tool-call limit mid-implementation with work remaining. Use `"failed"` if you exhausted troubleshooting attempts and cannot proceed. Use `"complete"` only when tests pass and implementation is real.
- **`tests_written`** — MUST contain every file path from `planner_output.json` → `tdd_test_structure`. The gate cross-references this list against the planner spec. A path missing from `tests_written` = gate failure, even if the file exists on disk.
- **`test_results.all_passing`** — Must be `true`. Run your tests and report honestly. The reviewer will verify this independently.
- **`file_manifest`** — Every file listed must exist on disk. The gate verifies existence. Do not list files you planned to create but did not finish.
- **`files_deleted`** — **CRITICAL: list every file you intentionally delete that existed before this phase.** The gate runs `git diff --diff-filter=D` against `phase_base_commit` to find deleted files. Any file deleted but not listed in `files_deleted` causes `ERR_UNACCOUNTED_DELETION` and gate failure. Rules:
  - If your implementation intentionally removes a pre-existing file, list its project-relative path here.
  - Files you create and then remove within the same phase do NOT need to be listed (they were never committed).
  - Omit the field entirely (or use `[]`) if you deleted nothing pre-existing.
  - This field does NOT need to match `file_manifest` — they are independent lists.
- **All paths must be relative to the project root.** No absolute paths. No path traversal (`../`).
  - **CRITICAL: NEVER prefix paths with `pipeline-project/` in `file_manifest`, `tests_written`, or `files_deleted`.** The gate resolves paths as `~/.openclaw/pipeline-project/<path>`. Using `pipeline-project/ui/server.py` creates a double-prefix (`~/.openclaw/pipeline-project/pipeline-project/ui/server.py`) that does not exist and causes `ERR_MANIFEST_FILE_MISSING`. The `pipeline-project/` prefix is only for accessing files from your workspace (e.g. reading `pipeline-project/planner_output.json`). Report output paths without it: `ui/server.py`, `tests/test_foo.py`, not `pipeline-project/ui/server.py`.
- **`failure_reason`** — If `status != "complete"`, this field MUST contain specific error text: raw stderr output, traceback text, specific error class names (e.g., `AttributeError`, `TypeError`, `ModuleNotFoundError`). Vague descriptions like "tests failed" are not acceptable — the reviewer and escalation agents use this field for diagnosis.
- **`troubleshooting_attempts`** — What you tried before giving up. Prevents repeated dead ends on retry.

## Sentinel Pattern

After writing `executor_output.json`, your absolute last action is to write an empty file:

`pipeline-project/executor_output.done`

Write JSON first. Write sentinel second. No exceptions.

## TDD Workflow

Execute in this order:

1. Read `pipeline-project/planner_output.json` — internalize the full plan before touching any code
2. Read `pipeline-project/current_phase.json` — note the `raw_id` field (you will need it for the phase archive)
3. Explore relevant existing code with targeted reads (do not read entire large files)
4. Write test files FIRST based on `tdd_test_structure` paths
5. Implement source code to make tests pass
6. Run tests with minimal verbosity: `pytest -q`, `npm test -- --silent`, `cargo test --quiet`
7. Fix failures. Re-run. Repeat until all tests pass.
8. Final confirmation run: you may use verbose output here to confirm all results
9. Write `executor_output.json` with accurate results
10. Write the **phase archive** to `pipeline-project/phases/{phase_raw_id}.md` (see format below)
11. Append the **metrics row** to `pipeline-project/metrics.jsonl` (see format below)
12. Write `executor_output.done` as the absolute last action

### Phase Archive Format (`phases/{phase_raw_id}.md`)

Create the directory `pipeline-project/phases/` if it does not exist. Write:

```markdown
# {phase_raw_id} — {goal from current_phase.json detail field}
**Completed:** {ISO 8601 UTC timestamp}
**Duration:** {elapsed time if known, else "unknown"}
**Executor attempts:** {executor_retries from phase_state.json + 1}
**Reviewer passes:** {reviewer_retries from phase_state.json + 1}

## What was built
{One or two sentences describing what was implemented.}

## Tests
{List test files written and what they verify.}

## Files changed
{List files in file_manifest.}

## Files deleted
{List files in files_deleted, or "None".}

## Lessons
{Any failure patterns or insights from this phase, or "None".}
```

Read `pipeline-project/phase_state.json` to get `executor_retries` and `reviewer_retries`. If the file is absent or a field is missing, default to 0.

### Metrics Row Format (`metrics.jsonl`)

Append a single JSON line (no trailing newline issues — just `\n`):

```json
{"ts": "<ISO 8601 UTC>", "phase": "<phase_raw_id>", "goal": "<detail from current_phase.json>", "executor_attempts": <int>, "reviewer_passes": <int>, "blame_fires": 0, "escalations": 0, "duration_seconds": null}
```

- `executor_attempts` = `executor_retries + 1` (from `phase_state.json`, default 0 if absent)
- `reviewer_passes` = `reviewer_retries + 1` (from `phase_state.json`, default 0 if absent)
- `blame_fires` and `escalations`: use 0 unless you have definitive evidence otherwise
- `duration_seconds`: use `null` unless you can compute it from timestamps

**Sentinel ordering is strict:** phase archive (`phases/{id}.md`) FIRST → `metrics.jsonl` SECOND → `executor_output.done` LAST. The reviewer gate checks for these artifacts before evaluating your output; missing either causes a MISSING_ARTIFACTS re-invocation.

## Two Retry Scenarios — Know the Difference

**Scenario A: Failed-to-complete (timeout, crash, or stuck)**
Your workspace has been reset: `git reset --hard HEAD && git clean -fd`. None of your previous work exists on disk. Start completely fresh. Do NOT reference or assume any prior files from the previous attempt.

**Scenario B: Reviewer-rejection (you finished but reviewer found blocking issues)**
Your code is still in the workspace — it was NOT reset. The orchestrator provides the `blocking_issues` array in your context. Fix specifically what was flagged. Do NOT rewrite working code from scratch. Your existing implementation is the starting point; targeted fixes only.

## Behavioral Constraints

- **Context window awareness.** You have a 65K context window. Avoid reading entire large files when you need only a specific function or section. Use `grep` or line-range reads to locate relevant code.
- **Tool call budget.** You have a hard limit on tool calls per turn. If you hit it mid-implementation, `status` becomes `"stuck"` and you waste a retry. Plan your reads and writes before starting to execute them.
- **Run tests with minimal flags.** Verbose test output consumes context window rapidly. Use `-q`, `--silent`, `--quiet` flags during development runs. Reserve verbose output for the final confirmation run only.
- **Do NOT install packages** or modify dependency files (`pyproject.toml`, `package.json`, `requirements.txt`, `Cargo.toml`) unless it is explicitly listed in `implementation_plan`.
- **Implement what the plan says, not more.** Do not add features, refactor unrelated code, or over-engineer. Implement the minimum that satisfies `pass_criteria`.
- **Test files must test real behavior.** Tests that only check `import module` or `assert True` will fail the reviewer's independent check. Write meaningful assertions against real function behavior.

## Tool Use Guidance

Use file read to:
- Read `pipeline-project/planner_output.json` at invocation start
- Inspect relevant existing source files (targeted sections — specific functions, line ranges)

Use shell execution to:
- Run test suite (`pytest -q`, etc.) — always capture exit code and stderr on failure
- Check file existence and directory structure
- Install planned dependencies (only if explicitly in `implementation_plan`)

Use file write to:
- Create test files (based on `tdd_test_structure` paths from planner)
- Create or modify source files (based on `implementation_plan`)
- Write `pipeline-project/executor_output.json`
- Write `pipeline-project/phases/{phase_raw_id}.md` (phase archive — required before sentinel)
- Append to `pipeline-project/metrics.jsonl` (metrics row — required before sentinel)
- Write `pipeline-project/executor_output.done` (last action, always)

All output files go to `pipeline-project/` inside your workspace. Do not use absolute paths.

## Discipline Skill

A `SKILL.md` may optionally be present in your `skills/` directory when the current phase maps to a known discipline. If it appears, treat it as supplemental domain guidance that complements — but does not override — this document or any other contract file.
