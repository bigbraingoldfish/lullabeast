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

### F. P0 Stage C — `## Project type` strictness in `_validate_verification_content`

The roadmap-generation skill instructs the converter LLM to pick from a canonical 9-item project-type list (`web-app | http-api | cli | library | data-pipeline | game | automation | desktop-app | mobile-app`). Open question at design time: should the validator soft-warn or hard-fail when the converter writes a value outside that list?

Operator decision: **hard-fail.** Downstream gates and adapters (planned for P1) will branch on this value; admitting unknown types now would create silent fallbacks that mask malformed verification docs. Operators may extend the canonical list later by editing `_VERIFICATION_CANONICAL_TYPES` in `ui/server.py`; no preflight-level "allow custom type" knob exists. Strictness is paired with a shape check (single line, ≤40 chars, no markdown formatting characters) so the field cannot accidentally absorb a paragraph.

### G. P0 Stage C — `verification.md` enforcement at every staging entry point

Three endpoints can stage a project: `/api/setup/preflight`, `/api/setup/launch`, and `/api/setup/switch-project`. The original Stage C scope in the P0 plan named only `preflight`. Operator decision during plan review: include all three. Rationale: §2.9's strict-from-day-one promise has a hole if any path can bypass the gate. Implementation: each endpoint now accepts a `verification_content` body field; `_run_init_project` refuses to initialize a project when neither `verification_content` nor an existing `verification.md` is available. The error message points the operator at the Ideas-screen regenerate flow.

### H. P0 Stage D — keep `exit_criteria` list, add `exit_criteria_block`

`phase_resolver.parse_roadmap` historically returned `exit_criteria` as a list built from `>`-prefixed lines under each phase header. Reviewer-gate consumers rely on that shape today. The Stage D additions parse a separate `**Exit Criteria:**` markdown block as `exit_criteria_block: str` rather than replacing the list. Decision: additive only; no churn for existing consumers. Future cleanup (e.g. P2) can deprecate the list once all consumers are migrated.

### I. P0 Stage F — content-driven behavioural check + hard removal of `phase_intent_validated`

Two coupled decisions made during Stage F implementation:

**Content-driven, not prefix-driven.** The reviewer gate's existing visual check (`_is_visual_phase`) keys on the phase's raw-id prefix (`UI-*`, `INT-*`, plus an env-var allowlist). The new behavioural check (`_requires_behavioral_verification`) is keyed on the *content* of `current_phase.json` — specifically, presence of a non-null `behavioral_verification` dict with all three required sub-fields. Under P0 every roadmap phase carries the block (preflight enforces it), so the check is effectively universal; the content-driven shape makes the gate forward-compatible with future project types without an allowlist edit, and preserves the §2.9 transitional contract (legacy in-flight phases queued before P0 land with `behavioral_verification: None` and are exempt).

**Hard removal of `phase_intent_validated`, not dual-write.** The legacy `phase_intent_validated: boolean` was self-attested and unverifiable — a single `true` value gated phase merge with no anchor to evidence. P0 Stage F removes the field from all three surfaces simultaneously: the reviewer AGENTS.md no longer instructs writing it, the reviewer gate no longer reads it (`reviewer_gate.py:153` triggered ERR_VALIDATION_FAILED on `not data.get("phase_intent_validated")` — replaced by `behavioral_rejection` keying on `behavioral_verification.verdict ∈ {fail, cannot_verify}`), and the OpenClaw `before_agent_finalize` plugin no longer requires it (the plugin's structural pre-check now enforces the new `behavioral_verification` object shape). A transitional dual-write window was considered and rejected: the new field is the structured replacement; keeping the boolean would invite the reviewer to falsely set `true` even when behavioural evidence was weak, undermining the very signal Stage F was designed to strengthen.

**Plugin enforces shape, gate enforces semantics.** The `autodev/plugin/src/before-finalize-handler.ts` `checkReviewer` validates that `behavioral_verification` is an object with `verdict` (string), `evidence` (array), and `how_to_check_followed` (boolean) — purely structural checks the plugin's "no filesystem traversal" boundary allows. The hard `reviewer_gate.py` enforces the semantic rules: minimum 3 anchors on `verdict='pass'`, workspace-bound path safety on `file_or_screenshot_or_log`, and on-disk existence of every listed path. This split mirrors the existing visual contract and keeps the soft pre-gate cheap.

### J. P0 Stage G — gate-side synthesis as the canonical write-back for behavioural blocking issues

When the reviewer returns `behavioral_verification.verdict ∈ {fail, cannot_verify}` and `blocking_issues` is empty, the reviewer gate (`reviewer_gate.py:_synthesize_behavioral_blocking_issues`) synthesises one blocking issue per evidence entry and **persists the augmented `reviewer_output.json` back to disk** before returning the routing verdict. Three alternatives were considered:

1. Have `apply_reviewer_routing` do the write-back — rejected: collapses pure routing with disk I/O.
2. Have the orchestrator (`_write_reviewer_failure_context`) do the synthesis — rejected: would make `reviewer_output.json` on disk diverge from `failure_context.json`, confusing for any post-mortem reader who tries to reconcile the two files.
3. Add `_synthesize_behavioral_blocking_issues` in the gate, called from `evaluate_reviewer` ahead of routing, write back to `reviewer_output.json` atomically (`mkstemp` + `os.replace`), then route on the same `data` dict — chosen. `apply_reviewer_routing` stays pure routing; the on-disk file always reflects the canonical list.

The reviewer AGENTS.md was also updated to instruct the reviewer agent to populate `criterion_source` and `criterion_id` directly. The gate's synthesis is the **defensive fallback** for the case where the agent's structured output omits them — same "raise the floor" symmetry as executor `behavioral_smoke_artifacts` (AGENTS.md says do it; gate enforces it).

**`criterion_id` format: `"behavioral_evidence[<N>]"`** (zero-based index into `behavioral_verification.evidence`). Distinct from the planner's `pass_criteria[].traces_to` anchor by design — `traces_to` is a planning-time link (`behavior:user_observable`, `behavior:how_to_check`, etc.); `criterion_id` is a *runtime-evidence pointer* into the reviewer's specific evidence array. The two should never be conflated; an operator who finds `criterion_id == "behavioral_evidence[2]"` in `failure_context.blocking_issues` can `jq '.behavioral_verification.evidence[2]'` the reviewer_output.json to retrieve the original claim verbatim.

**`failure_language` sourcing.** `_generate_escalation_advisory` reads `current_phase_behavioral_verification.failure_language` *from* `failure_context.json` (the materialised snapshot the executor sees on its self-heal pass), not from a fresh read of `current_phase.json`. Single source of truth — advisory and executor read the same materialised view.

**`reviewer_retries >= 2` gating is data-level, not prompt-level.** The advisory user-message `behavioral_verification` block is `None` when `reviewer_retries < 2`; the system prompt also names the rule for clarity, but the data gating is the load-bearing mechanism — the LLM cannot quote `failure_language` when the data is absent. This is what makes escalation a true fallback consumer (executor self-heal goes first) and keeps the `failure_language` verbatim quote out of summaries that fire before self-heal has been attempted.

**`criterion_source: "free"` defaulting.** `_write_reviewer_failure_context` applies the explicit `"free"` label to any blocking issue arriving without a `criterion_source`. The explicit-enum approach (rather than leaving the field absent and forcing downstream None-checks) closes the bug pattern Stage G is meant to eliminate — every downstream consumer can branch on a complete four-value enum without truthiness defensiveness. `criterion_id` is omitted on `"free"` source — there is no anchor to point at.

---

## 6. Verification Checklist for Operator

- [ ] `python -m pytest tests/pipeline/ -v` — all 52 tests pass
- [ ] `workspace-{planner,executor,reviewer}/skills/` do not exist before first pipeline run (clean state)
- [ ] Run pipeline with a CORE-* phase: confirm `[SKILL] Status=loaded` in orchestrator output and SKILL.md appears at `workspace-executor/skills/core-logic-executor/SKILL.md`
- [ ] Run pipeline with an MCP-* phase: confirm `[SKILL] Status=none_mapped` for all 3 agents
- [ ] Set `pipeline.skills.enabled: false` in `openclaw.json`, restart orchestrator, confirm `[SKILL] Status=disabled` and no SKILL.md in any workspace skills dir
- [ ] Confirm `~/.openclaw/skills/` directory does NOT contain the discipline skills (verify OpenClaw global tier is not polluted)
- [ ] After a phase completes and the next phase begins, confirm prior phase's SKILL.md is absent from workspace before new skill is injected
