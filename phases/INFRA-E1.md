# INFRA-E1 — Phase INFRA-E1: Extend init-project skill to cover all repo_init_check.py requirements and validate end-to-end
**Completed:** 2026-03-19T17:51:00Z
**Duration:** unknown
**Executor attempts:** 7
**Reviewer passes:** 1

## What was built
Extended the init-project skill with Gap 1 (symlink step in both modes), Gap 2 (7 pipeline .gitignore entries in Mode A heredoc + append-only in Mode B), and Gap 3 (workspace-docs WARN check in both modes). Validated Mode A (new project at /tmp/infra-e1-test-a) and Mode B (connect existing at /tmp/infra-e1-test-b) end-to-end using direct shell commands matching skill steps, with pytest test files verifying results. Symlink restored to autodev-ui before output files written.

## Tests
- `tests/test_skill_mode_a_symlink_and_validation.py`: verifies symlink, repo_init_check.py exit 0, .gitignore pipeline entries, roadmap format validation, and roadmap_parser.py exit 0 for Mode A project.
- `tests/test_skill_mode_b_symlink_and_validation.py`: verifies same checks for Mode B project plus no-overwrite of existing placeholder file and git history integrity.

## Files changed
- `tests/test_skill_mode_a_symlink_and_validation.py`
- `tests/test_skill_mode_b_symlink_and_validation.py`

## Files deleted
None.

## Lessons
pipeline.json (skill-written, project dir) vs pipeline_state.json (orchestrator-written, ~/.openclaw/) are intentionally different files — never confuse them. repo_init_check.py auto-injects missing .gitignore entries rather than hard-failing, self-healing existing projects. The symlink must be restored to /home/pi/projects/autodev-ui after each test run before writing any output files.
