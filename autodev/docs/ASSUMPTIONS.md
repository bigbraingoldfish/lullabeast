# ASSUMPTIONS.md: Resolved Spec Ambiguities and Judgment Calls

> Scope: design decisions and resolved ambiguities for the Lullabeast pipeline. This file is the canonical rationale for several choices that PIPELINE-SPEC.md, reviewer_gate.py, and the reviewer tests cite by section letter (for example, "see ASSUMPTIONS.md §J").
>
> History: the original 2026-03-13 skills-integration review (former sections 1 through 4 and the operator checklist) was trimmed once that work migrated out of ~/.openclaw/ into the repo, leaving its file-path snapshots superseded by CLAUDE.md and the code itself. The lettered decision log below is retained because parts of it are still referenced by name.

---

## Resolved Ambiguities and Judgment Calls

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

### K. v0.1.1 — reviewer recovery: honest hard-error labeling over fast-fail; operator RESET_REVIEWER restores the budget

Two reviewer-recovery changes shipped in v0.1.1, prompted by a live no-progress loop (a text-only reviewer model 500-ing on behavioral-smoke screenshots — fixed separately by adding an `mmproj` vision projector — which exposed two latent recovery defects):

1. **`reset_reviewer()` now zeros `reviewer_contract_retries` and `reviewer_unverified_retries`** (it previously zeroed only `reviewer_retries`). An operator `RESET_REVIEWER` is a deliberate "give the reviewer a clean shot" action, so it restores a fresh budget — matching the established `reset_execution('escalation')` precedent for the executor. Without this the pooled counters survived the reset and an already-maxed counter re-escalated on the next failure (the "fast fail, no retries" symptom). The only design question was whether an operator reset *should* clear these; the executor precedent settled it (yes). These two counters stay per-phase otherwise (preserved by `reset_execution`, zeroed by `reset_phase`).

2. **A CONTRACT_FAILURE caused by a reviewer model hard-error is labeled honestly** (`ERR_REVIEWER_MODEL_ERROR` + the real inference error) rather than the generic "reviewer gave up." **Judgment call (operator-confirmed):** keep the existing self-healing soft-retry + backoff rather than fast-failing on the first hard-error. Rationale: the deterministic capability error (images on a text-only model) was fixed at the infra layer (`mmproj`), so the remaining hard-error class is mostly *transient* GPU contention / model eviction, which a retry clears — fast-failing would escalate on a blip a retry would have absorbed. Only the *terminal* escalation label changed; the retry loop is untouched.
