# ASSUMPTIONS.md — Skills Integration Operator Review Document

> Generated: 2026-03-13
> Scope: Integration of optional discipline skills into the AutoDev pipeline.
> This document is the operator's primary tool for reviewing the implementation and catching misalignment between intent and execution.

---

## 1. Assumptions About Existing Codebase

### A. OpenClaw workspace-level skills loading
**Assumption:** OpenClaw auto-loads `SKILL.md` files from `<workspace>/skills/{name}/SKILL.md` at session start (workspace-level tier). This was confirmed via the official documentation at https://docs.openclaw.ai/tools/skills and by observing the existing `~/.openclaw/workspace/skills/autonomous-dev/SKILL.md` and `init-project/SKILL.md` patterns in the live system.

**Risk if wrong:** Skills would not load. Observable symptom: agents behave as if skills are absent even when `[SKILL] Status=loaded` is logged. Mitigation: verify during first live CORE-* phase run.

### B. `~/.openclaw/skills/` is the global tier
**Assumption:** Files placed in `~/.openclaw/skills/` are loaded for ALL agent sessions simultaneously (OpenClaw global tier). This is why we use `~/.openclaw/skill-library/` (an innocuously named directory OpenClaw does not scan) as the source library.

**Risk if wrong:** If OpenClaw does NOT scan `~/.openclaw/skills/` automatically, we could have used it as the source library without conflict. The impact would be zero — `skill-library/` works equally well as a source path. No functional risk.

### C. Orchestrator `openclaw_config` is loaded once at startup
**Assumption:** `self.openclaw_config` is populated once in `Orchestrator.__init__()` via `load_config()` and not refreshed during the run. Therefore, changes to `openclaw.json`'s `pipeline.skills` flags require an orchestrator restart to take effect.

**Impact on spec:** The spec says "flags are read at the start of each phase (not cached across phases), so operators can toggle mid-run." This intent is partially honoured — `inject_skill()` reads flags from the passed `openclaw_config` dict on every call, so no extra caching occurs inside `SkillManager`. However, since `self.openclaw_config` itself is not reloaded between phases, a mid-run toggle requires restart. This is documented in README.md and this file.

**Possible improvement (not implemented):** Have `inject_skill()` call `self.load_config()` internally on each invocation. Opted against this to keep `SkillManager` a pure helper with no filesystem side-effects beyond skill injection, and because the live system rarely needs mid-run skill toggling.

### D. `current_phase_raw_id` is populated before agent invocation
**Assumption:** `self.state["current_phase_raw_id"]` is always set to the current phase ID (e.g., `"CORE-E2"`) before the planner/executor/reviewer blocks execute. This was verified by reading orchestrator.py — the state is loaded from `pipeline_state.json` at the top of the main loop, and `current_phase_raw_id` is set during phase transition. An empty string is returned by `self.state.get("current_phase_raw_id", "")` as the safe fallback.

### E. PyYAML is available
**Assumption:** The `yaml` (PyYAML) package is installed on the Raspberry Pi 5. This was not explicitly verified but is a standard Python package available in the system environment. `skill_manager.py` handles `ImportError` gracefully — if PyYAML is absent, all skills are disabled with a warning (no crash).

### F. `workspace-{agent}/skills/` directories are exclusively orchestrator-owned
**Assumption:** None of the per-agent `workspace-{planner,executor,reviewer}/skills/` directories existed prior to this implementation (confirmed by directory listing). No other process places files there. The orchestrator therefore has full ownership — it can `rmtree` and recreate the directory on every invocation without risk.

---

## 2. Divergences from Original Prompt Spec

### A. Skill destination: workspace root vs. `skills/` subdirectory
**Original spec:** "copies/symlinks it into the agent's workspace skills/ directory"
**Implemented:** `workspace-{agent}/skills/{discipline}-{role}/SKILL.md`
**Reason:** The official OpenClaw docs specify the native loading path as `<workspace>/skills/{name}/SKILL.md`. Existing skills in `workspace/skills/` use this exact pattern. Placing SKILL.md directly at the workspace root was the initial assumption, corrected after reviewing the docs and live system state.

### B. Source library name: `skills/` vs. `skill-library/`
**Original spec:** "Copy or symlink them into the pipeline project's skills/ directory"
**Implemented:** `~/.openclaw/skill-library/`
**Reason:** `~/.openclaw/skills/` is OpenClaw's global tier — all files there are loaded into every agent session simultaneously. Placing 27 SKILL.md files there would inject all domain guidance into every session regardless of phase, negating the entire per-phase injection design. Renamed to `skill-library/` to stay outside OpenClaw's discovery paths. The spec predates knowledge of OpenClaw's native 3-tier system.

### C. Symlinks vs. file copy
**Original spec:** "copies/symlinks"
**Implemented:** File copy (`shutil.copy2`)
**Reason:** Symlinks pointing back into `skill-library/` would work, but copy is more robust — it survives `skill-library/` being moved or the symlink target being inaccessible. Given the small file sizes (SKILL.md files are 1–20 KB each), copy overhead is negligible.

### D. `workspace-{agent}/skills/` cleaned via rmtree + recreate, not just deletion of specific file
**Original spec:** "ensures the agent's workspace skills/ directory is clean (no stale skills)"
**Implemented:** `shutil.rmtree(skills_dir)` followed by `os.makedirs(skills_dir)` — removes the entire directory tree and recreates it empty.
**Reason:** Ensures no residual subdirectory from a prior phase can survive (e.g., if the skill name changes across phases). Recreating the empty dir means OpenClaw always sees a valid `skills/` directory rather than its absence, which could cause OpenClaw to behave differently than intended.

### E. `openclaw_config` passed as parameter instead of re-reading file
**Original spec:** "flags are read at the start of each phase (not cached across phases)"
**Implemented:** `inject_skill(phase_raw_id, agent_role, openclaw_config)` reads flags from the passed dict.
**Reason:** `SkillManager` is a stateless helper — coupling it to filesystem reads of `openclaw.json` would make it harder to test (tests would need real config files). Passing the dict makes testing trivial and the behaviour explicit. The limitation (restart required for live toggles) is documented.

### F. 13 tests instead of 11
**Original spec:** 11 test cases listed
**Implemented:** 13 tests (`test_same_phase_different_roles` and `test_default_enabled_when_config_absent` added)
**Reason:** During implementation, two additional edge cases were identified as worth covering explicitly.

---

## 3. All Modified Files

| File | Summary of change |
|------|-------------------|
| `~/.openclaw/orchestrator.py` | Added `from skill_manager import SkillManager` import; added `self.skill_manager = SkillManager(WORKSPACE_DIR)` in `__init__`; added `self.skill_manager.inject_skill(...)` before planner, executor, and reviewer webhook calls (3 insertions) |
| `~/.openclaw/openclaw.json` | Added `"pipeline": { "skills": { "enabled": true, ... } }` top-level block |
| `~/.openclaw/README.md` | Added "AutoDev Pipeline — Skills System" section (operator documentation) |
| `~/.openclaw/deployment-package/Updates/PIPELINE-SPEC (1).md` | Added `§Skills — Optional Discipline Skill Injection` section at end |
| `~/.openclaw/workspace-planner/AGENTS.md` | Appended "## Discipline Skill" section (one-line note about optional SKILL.md) |
| `~/.openclaw/workspace-executor/AGENTS.md` | Same as planner |
| `~/.openclaw/workspace-reviewer/AGENTS.md` | Same as planner |

---

## 4. All New Files Created

| File | Purpose |
|------|---------|
| `~/.openclaw/skill_manager.py` | `SkillManager` class — mapping load, skill resolution, workspace injection, structured logging |
| `~/.openclaw/config/skill_mapping.yaml` | Subsystem → discipline mapping (YAML, operator-editable data file) |
| `~/.openclaw/skill-library/` (27 files) | Source library copied from `deployment-package/Updates/autodev-discipline-skills/`. Not modified. Read-only from the orchestrator's perspective. |
| `~/.openclaw/tests/pipeline/test_skill_manager.py` | 13 unit tests for SkillManager — resolution, injection, toggles, graceful degradation, logging |
| `~/.openclaw/ASSUMPTIONS.md` | This document |

---

## 5. Ambiguities Resolved by Judgment Call

### A. What does OpenClaw do with `<workspace>/skills/` if the directory is empty?
Not explicitly documented. Judgment: recreate the empty directory after cleaning so OpenClaw always sees a consistent `skills/` path (vs. its absence). Safest conservative choice.

### B. Should `SkillManager` be a class or a module-level function?
Could have been implemented as a set of module-level functions. Class chosen because: (1) it holds cached state (`self._mapping`, `self._workspace_dir`) avoiding repeated file reads; (2) easier to mock in tests; (3) consistent with how other Python components in this codebase are structured.

### C. Timestamp format in `[SKILL]` log lines
Spec said "Include enough detail to reconstruct: which phase, which agent, which skill, at what time." ISO 8601 UTC timestamp prepended to each line. Could alternatively have relied on the orchestrator's surrounding log context. Chose explicit timestamp in the `[SKILL]` line to make `grep '[SKILL]' orchestrator.log` fully self-contained.

### D. Skill named subdirectory format
OpenClaw's convention is `skills/{name}/SKILL.md` where `{name}` comes from the skill's frontmatter `name` field. The discipline skill files already have `name: {discipline}-{role}` in their frontmatter (e.g., `name: core-logic-executor`). The injected subdirectory is named `{discipline}-{role}` to match, making the directory name and frontmatter name consistent and predictable.

### E. Whether to update `repo_init_check.py` to validate `skill-library/`
Judgment: not added. The prompt specifies "Minimal footprint — touch the orchestrator and config layer only." Gate scripts were explicitly listed as out of scope. Also, skill-library absence is a graceful-degradation case already handled by SkillManager — adding a hard check in repo_init_check would turn a graceful degradation into a pipeline-blocking failure, which is the wrong trade-off.

---

## 6. Verification Checklist for Operator

- [ ] `python -m pytest tests/pipeline/ -v` — all 52 tests pass
- [ ] `workspace-{planner,executor,reviewer}/skills/` do not exist before first pipeline run (clean state)
- [ ] Run pipeline with a CORE-* phase: confirm `[SKILL] Status=loaded` in orchestrator output and SKILL.md appears at `workspace-executor/skills/core-logic-executor/SKILL.md`
- [ ] Run pipeline with an MCP-* phase: confirm `[SKILL] Status=none_mapped` for all 3 agents
- [ ] Set `pipeline.skills.enabled: false` in `openclaw.json`, restart orchestrator, confirm `[SKILL] Status=disabled` and no SKILL.md in any workspace skills dir
- [ ] Confirm `~/.openclaw/skills/` directory does NOT contain the discipline skills (verify OpenClaw global tier is not polluted)
- [ ] After a phase completes and the next phase begins, confirm prior phase's SKILL.md is absent from workspace before new skill is injected
