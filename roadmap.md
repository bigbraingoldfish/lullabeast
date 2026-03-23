# Development Roadmap

> Source PRD: `docs/prd/autodev-ui-screens.prd.md`

This document is the **project's source of truth** for phase sequencing and execution state for the AutoDev UI new screens: **Project Ideas** and **Setup & Preflight**.

- Process and workflow live in the agent's SKILL.md.
- Phase archives live in `phases/{phase_id}.md` (one per completed phase).
- Execution metrics live in `metrics.jsonl`.
- Hard-won insights live in `lessons.md`.

---

## 1) Conventions and Definitions

### Phase ID

Format: `{SUBSYSTEM}-{Type}{N}` where Type is one letter (`B` = bugfix, `E` = enhancement) and N is a monotonic integer within that type.
Examples: `INFRA-B1`, `INFRA-E1`, `UI-E1`, `UI-E9`

### Goal

One sentence stating the observable outcome. Must be measurable. Must not contain "and then" — split if it does.

### Risk Level

- **LOW**: Straightforward UI or config work. Review is mandatory but rejection is unlikely.
- **HIGH**: Touches orchestrator state, filesystem, external service contracts, OpenClaw config, or webhook calls.

### Checkbox States

```
- [ ]  = pending
- [x]  = complete
- [-]  = skipped (reason stated after —)
- [!]  = blocked (blocker stated after —)
```

### Done Criteria

A phase is complete only when ALL of:
1. All tests pass (full suite).
2. Linting passes (`ruff check .`).
3. Cloud review returns APPROVED.
4. Changes committed to main with message `phase({phase_id}): {goal summary}`.
5. Roadmap checkbox updated from `- [ ]` to `- [x]`.
6. Phase archive written to `phases/{phase_id}.md`.
7. Metric appended to `metrics.jsonl`.

### Cross-Cutting Constraints

These apply to every phase. No exceptions.

- **Pipeline monitor must never break.** After every single change to `index.html` or `server.py`, confirm `http://localhost:18790` loads the pipeline monitor correctly before committing. This is the most important screen and must remain functional throughout.
- **Backup before every index.html change.** Run `cp ui/index.html ui/index.html.bak` before touching `index.html`. Never skip this.
- **Script tag order is sacred.** React, ReactDOM, and Babel CDN script tags must remain in `<head>` before the Tailwind CDN script tag. Never move, reorder, or duplicate these. Changing this order breaks the entire UI.
- **Atomic writes.** Every endpoint that writes files uses temp-file + `os.replace()` pattern. Never write sentinel before payload.
- **Error handling.** All endpoints return sensible defaults for missing files — never unhandled 500s. Match the `_read_json_file()` null-safety pattern in `server.py`.
- **React naming.** Top-level screens: `{Name}Screen`. Sub-components: `{Name}Panel`. Primitives: descriptive PascalCase. All are `function` declarations inside the single `<script type="text/babel">` block in `index.html`.
- **Design system.** Body bg `#0d0f12`, panel bg `#141618`, element bg `#1a1d21`, border `#1a1d21`, accent `#00b4d8`. Header text via class `header-text` (JetBrains Mono). Body text IBM Plex Sans. Tailwind only — no inline styles. New screens must look like they belong to the same product as the pipeline monitor.
- **Scope boundary.** Greenfield projects only. Enhancement flow for existing projects is explicitly out of scope for MVP.
- **Commit every phase.** Every completed phase must be committed with `phase({id}): {goal}` before the next phase begins. Never leave changes uncommitted.

---

## 2) Milestones

- **M0 — Pre-flight Bug Fix**: `INFRA-B1` ✅ complete
- **M1 — Infrastructure**: `INFRA-E1` ✅ complete
- **M2 — Screen 1 Core**: `UI-E1`, `UI-E2`, `UI-E3`
- **M3 — Screen 1 Upload & Progression**: `UI-E4`, `UI-E5`
- **M4 — Screen 2: Setup & Preflight**: `UI-E6`, `UI-E7`, `UI-E8`, `UI-E9`

### Dependency Order

```
INFRA-B1 ✅
  └── INFRA-E1 ✅
        └── UI-E1
              └── UI-E2
                    └── UI-E3
                          ├── UI-E4
                          │     └── UI-E5
                          └── UI-E6 (parallel with UI-E4)
                                └── UI-E7
                                      └── UI-E8
                                            └── UI-E9
```

INFRA-B1 and INFRA-E1 are complete — confirmed infrastructure.

**Implementation status (2026-03-22):** The attached plan’s **server.py items 1–8** and **UI-E1–E9** are implemented in `ui/server.py` and `ui/index.html` (spliced via `ui/_build_screens.py`). Full test suite green with **11 skipped** (manual symlink fixture tests under `/tmp/infra-e1-test-*`). **`tests/test_infra2_root.py`** now restores `ui/index.html` after its temporary stub — that test previously left `<html></html>` on disk and broke UI tests.

**Follow-up (2026-03-22):** **Preflight** “Invalid path” on good paths was a **frontend bug**: `fetch` handlers ignored HTTP status and treated FastAPI `{detail: ...}` bodies as `{valid: false}` → generic error. Fixed with `if (!r.ok)` + `detail` parsing; path is trimmed on confirm. **Ideas UX (later same day):** **collapsible main `Sidebar`**; **vertical Chats rail** (list of ideas, no dropdown) **collapses** when opening a chat; conversation + PRD share remaining width.

---

## 3) Phase Catalog

### Milestone 0 — Pre-flight Bug Fix

- [x] `INFRA-B1` | HIGH | Fix --project-path CLI argument mismatch in server.py, add prd-creator to OpenClaw config, and add all new config keys to server.py
  > Test: `POST /api/resume-orchestrator` spawns orchestrator with `--project-path`. `openclaw.json` has `"prd-creator"` in `allowedAgentIds` and `"ideas:"` in `allowedSessionKeyPrefixes`. `server.py` DEFAULTS and `ui/config.json` contain `ideas_dir`, `hooks_url`, `hooks_token`, `conversion_prompt_path`.

---

### Milestone 1 — Infrastructure

- [x] `INFRA-E1` | HIGH | Extend init-project skill to cover all repo_init_check.py requirements and validate end-to-end
  > Test: Extended skill against fresh test dirs in Mode A and Mode B. After each: symlink resolves correctly, `.gitignore` contains all 7 pipeline entries, `repo_init_check.py` exits 0. No existing files overwritten in Mode B. Production symlink restored to `/home/pi/projects/autodev-ui` before commit.

---

### Milestone 2 — Screen 1 Core

- [ ] `UI-E1` | LOW | Replace the IdeasScreen placeholder with a split-panel scaffold: conversation pane left, PRD document pane right, both scrollable and static
  > Test: Navigating to the Ideas screen (`currentScreen === 'ideas'`) renders two side-by-side panels filling the content area. Left pane (38% width): empty message list area at top, `<textarea>` input pinned to bottom. Right pane (flex-1): 12 PRD section headers rendered as `##` markdown with dim placeholder text under each. Both panes scroll independently. No agent wiring. No console errors. Pipeline monitor still loads correctly after this change.
  > Notes: Replace the existing `IdeasScreen` function in `index.html` entirely. Keep the component name `IdeasScreen`.
  >
  > **CRITICAL — message input must be `<textarea>` not `<input type="text">`**. The `<textarea>` must: auto-resize vertically as the user types (max 5 lines then scroll), submit on `Enter` (no modifier), insert newline on `Shift+Enter`, match design system styling (bg `#1a1d21`, same border and padding as other inputs). Using `<input>` is wrong and will fail review.
  >
  > **Layout**: `flex h-full` on the container. Left pane: `w-[38%] flex-shrink-0 flex flex-col bg-[#141618] overflow-hidden border-r border-[#1a1d21]`. Right pane: `flex-1 flex flex-col bg-[#141618] overflow-hidden`. Conversation message list: `flex-1 overflow-y-auto p-4`. Input area: `border-t border-[#1a1d21] p-3`. Document pane: `flex-1 overflow-y-auto p-4`.
  >
  > **PRD skeleton section headers** (source: `~/.openclaw/workspace/skills/prd-creator/skill.md`): `## Problem Statement`, `## Goals & Success Metrics`, `## User Stories`, `## Functional Requirements`, `## Edge Cases`, `## Non-Functional Requirements`, `## Dependencies & Integrations`, `## Milestones & Timeline`, `## Risks & Mitigations`, `## Open Questions`, `## Glossary & Domain Terms`, `## Revision History`. Each followed by `*Empty — start a conversation to populate this section.*` in `text-slate-600 italic text-sm`.
  >
  > **Left panel also needs**: a top strip showing the ideas list (rendered above the conversation area) and a "New Idea" button. For this phase, the list is static empty state ("No ideas yet") and the button does nothing. The list and its behavior are wired in UI-E3.
  >
  > **No agent wiring in this phase.** Textarea submit does nothing. Message list is empty.

- [ ] `UI-E2` | HIGH | Wire the prd-creator agent session with sentinel polling, live document updates, persisted history, and correct loading state
  > Test: Sending a message posts to `POST /api/ideas/{id}/message`. Server sends webhook, polls for `turns/{n}.done` (2s interval, 120s timeout), reads `turns/{n}.md` as response. While polling, document pane shows a subtle opacity pulse (no color change — NOT yellow). Agent response appears in conversation pane after sentinel found. Document pane updates with `prd_draft.md` content. Refreshing the page and reopening the idea restores full conversation history and document state from disk. Session key format: `ideas:{id}:session-{n}` where n increments per turn.
  > Notes: **Sentinel polling architecture**: OpenClaw webhook returns immediately. Agent runs async. Server polls `~/.openclaw/ideas/{id}/turns/{turn_n}.done`. On sentinel found, read `~/.openclaw/ideas/{id}/turns/{turn_n}.md` for response and `~/.openclaw/ideas/{id}/prd_draft.md` for document content.
  >
  > **CRITICAL — loading state must NOT be yellow.** Any yellow applied during polling was a regression that failed review. The correct loading state: wrap document pane content with the `status-pulse` CSS keyframe already defined in `index.html` (check the pipeline monitor's status pill implementation for the class name). Apply as reduced opacity on the document pane content div. Background stays `#141618`. No color change. Remove animation immediately when sentinel found.
  >
  > **Webhook body**:
  > ```json
  > {
  >   "agentId": "prd-creator",
  >   "sessionKey": "ideas:{id}:session-{n}",
  >   "wakeMode": "now",
  >   "message": "[SESSION] ideas:{id}:session-{n}\n\n{user_message_text}"
  > }
  > ```
  > The `[SESSION]` prefix is required — the agent parses its id and turn number from this line. `Authorization: Bearer {config.hooks_token}`. Read token from `load_config()`.
  >
  > **Session persistence** — `session.json` schema:
  > ```json
  > {
  >   "name": "New Idea",
  >   "messages": [{"role": "user|assistant", "content": "...", "ts": "ISO8601"}],
  >   "prd_content": "",
  >   "roadmap_content": "",
  >   "created": "ISO8601",
  >   "updated": "ISO8601"
  > }
  > ```
  > Write atomically after every agent turn. On mount when an idea is selected, call `GET /api/ideas/{id}/session` and populate `messages` and `prd_content` from the response. This is the session restore on refresh mechanism — it must be in a `useEffect` that fires when `selectedIdeaId` changes.
  >
  > **New server endpoints**:
  > - `POST /api/ideas/{id}/message` — body: `{"content": str, "turn": int}`. Sends webhook, polls for sentinel, reads response, persists to `session.json`, returns `{"response": str, "prd_content": str}`. Returns 408 on timeout.
  > - `GET /api/ideas/{id}/session` — returns full `session.json` or empty defaults if not found.

- [ ] `UI-E3` | LOW | Add idea document management with auto-naming: list, create (deferred until first turn), resume, delete, and download
  > Test: Clicking "New Idea" opens a blank conversation pane but does NOT add anything to the idea list yet. After the first agent turn completes, the idea appears in the list with the agent-proposed project name (extracted from the first `# ` heading in `prd_draft.md`). Selecting an existing idea from the list restores its conversation and document state. Deleting an idea shows a confirmation prompt then removes it from the list. Download PRD button appears in the document pane only when `prd_content` is non-empty — never in the idea list. The list shows project name and one-line summary only.
  > Notes: **Auto-naming contract**: After every successful agent turn in `POST /api/ideas/{id}/message`, the server reads `prd_draft.md`, extracts the first line starting with `# `, strips the `# `, and writes it to `session.json` `name` field atomically — but only if the current name is still `"New Idea"` or empty string. Fallback if no `# ` heading: use the first 40 characters of the user's first message, title-cased.
  >
  > **`GET /api/ideas` filtering**: Returns only ideas where `~/.openclaw/ideas/{id}/turns/1.done` exists. Ideas with no completed agent turn are not shown. This means clicking "New Idea" creates the document in the backend but nothing appears in the list until the user sends a message and gets a response.
  >
  > **No manual rename, no double-click rename.** The agent names ideas. Users do not.
  >
  > **Download PRD**: A "Download PRD" button inside the document pane, visible only when `prd_content.trim().length > 0`. Calls `GET /api/ideas/{id}/download`. Never in the idea list.
  >
  > **Summary extraction**: First sentence after `## Problem Statement` in `prd_content`. Empty string if section absent.
  >
  > **New server endpoints**:
  > - `GET /api/ideas` — list ideas where `turns/1.done` exists. Returns `[{id, name, summary, updated}]`.
  > - `POST /api/ideas` — creates `{ideas_dir}/{uuid}/`, writes empty `session.json` with `name: "New Idea"`, returns `{"id": uuid}`.
  > - `DELETE /api/ideas/{id}` — deletes `{ideas_dir}/{id}/` via `shutil.rmtree`. Returns 404 if not found.
  > - `GET /api/ideas/{id}/download` — returns `prd_content` from `session.json` as file attachment `{name}-prd.md`.

---

### Milestone 3 — Screen 1 Upload & Progression

- [ ] `UI-E4` | HIGH | Add PRD upload flow that synthesizes any uploaded markdown into PRD template structure instead of rejecting it
  > Test: A file upload input accepts `.md` files. Uploading any `.md` file (regardless of format) sends the content to the prd-creator agent with a synthesis instruction. The agent restructures the content into the canonical PRD template, preserving the user's intent, and writes its output via the standard sentinel pattern. The synthesized PRD appears in the document pane. If the uploaded file has no recognizable project content (e.g., random text with no concept, goals, or users), the agent responds with clarifying questions rather than fabricating a PRD — this is correct behavior and must not be prevented. Non-`.md` files are rejected client-side only.
  > Notes: **No rejection for format non-compliance.** Previous implementation rejected uploads that didn't match the template. This was wrong. The correct behavior: synthesize whatever the user has into the template structure. The agent is the quality gate, not a format validator.
  >
  > **Upload endpoint**: `POST /api/ideas/{id}/upload` — body: multipart form with `file` field. Server checks only: `.md` extension (reject otherwise with 400), file is non-empty. On pass: write uploaded content to `session.json` `prd_content`, then trigger synthesis.
  >
  > **Synthesis webhook**:
  > ```json
  > {
  >   "agentId": "prd-creator",
  >   "sessionKey": "ideas:{id}:upload-{timestamp_ms}",
  >   "wakeMode": "now",
  >   "message": "[SESSION] ideas:{id}:upload-1\n\nI uploaded a file. Please read ~/.openclaw/ideas/{id}/uploaded_seed.md and synthesize its content into the canonical PRD template (all sections), preserving my intent. Explain briefly what you structured in your reply."
  > }
  > ```
  > Before sending the webhook, write the uploaded content to `~/.openclaw/ideas/{id}/uploaded_seed.md` atomically. Server polls for `turns/1.done` (if this is the first turn) or the appropriate turn sentinel. On completion, `prd_draft.md` is updated and the document pane reflects the synthesized PRD.

- [ ] `UI-E5` | HIGH | Add progression flow — readiness detection, PRD-to-roadmap conversion, downloadable outputs, and navigation to Screen 2
  > Test: A "Generate Roadmap" button is visible in the document pane header when `prd_content` is non-empty. It is disabled when `prd_content` is empty. Clicking it triggers conversion via `POST /api/ideas/{id}/convert`. On success, the generated roadmap content appears below the PRD document (or in a toggle view) and is downloadable as `{name}-roadmap.md`. A "Continue to Setup →" button appears after successful conversion and navigates to Screen 2 with the roadmap pre-populated and pre-locked. On conversion failure (timeout or missing result file), the raw error is displayed with a retry option.
  > Notes: **Readiness**: `GET /api/ideas/{id}/readiness` returns `{"ready": bool, "reason": str}`. Ready if `prd_content` contains `> ✅ PRD CONVERSION-READY` OR all 10 required sections have at least one non-blank, non-header content line. Required sections: `## Problem Statement`, `## Goals & Success Metrics`, `## User Stories`, `## Functional Requirements`, `## Edge Cases`, `## Non-Functional Requirements`, `## Dependencies & Integrations`, `## Risks & Mitigations`, `## Open Questions`, `## Glossary & Domain Terms`. Button is enabled regardless of readiness — user can always trigger conversion. Readiness is a signal, not a gate.
  >
  > **Conversion prompt**: At `config.conversion_prompt_path` → `~/.openclaw/deployment-package/Updates/PRD to Roadmap (sonnet 4.5 ideal).txt`. Server reads at request time. Returns 503 if file missing.
  >
  > **Conversion endpoint**: `POST /api/ideas/{id}/convert`. Sends webhook:
  > ```json
  > {
  >   "agentId": "prd-creator",
  >   "sessionKey": "ideas:{id}:convert-{timestamp_ms}",
  >   "wakeMode": "now",
  >   "message": "[SESSION] ideas:{id}:convert-1\n\n{conversion_prompt_content}\n\n---\n\n{prd_content}\n\nWrite the resulting roadmap.md content to ~/.openclaw/ideas/{id}/roadmap_draft.md, then create ~/.openclaw/ideas/{id}/roadmap_draft.done."
  > }
  > ```
  > Poll for `roadmap_draft.done` (2s, 180s timeout). Read `roadmap_draft.md`. Store in `session.json` as `roadmap_content` atomically. Return `{"roadmap_content": str}`.
  >
  > **Navigation**: `App` component holds `seedRoadmap` state. On "Continue to Setup →", set `seedRoadmap` to roadmap content and set `currentScreen` to `'preflight'`. `PreflightScreen` receives `seedRoadmap` as a prop.
  >
  > **Additional endpoint**: `GET /api/ideas/{id}/download-roadmap` — returns `roadmap_content` as file attachment `{name}-roadmap.md`.

---

### Milestone 4 — Screen 2: Setup & Preflight

- [ ] `UI-E6` | LOW | Replace the PreflightScreen placeholder with repo path input and roadmap seed input, both with Confirm/Edit lock behavior
  > Test: Screen 2 renders two input fields. Repo path: plain text input with placeholder "Enter the full path to your project directory (e.g. /home/pi/projects/my-project)". Roadmap seed: file upload or pre-populated from Screen 1. Each field has a "Confirm" button when unlocked and an "Edit" button when locked. Locked state: field is read-only with slightly darker background (`#0d0f12`), green checkmark icon, "Edit" button in muted text. Unlocked state: field is editable, "Confirm" button in accent color (`#00b4d8`). Fields are independent — locking one does not affect the other. If navigated from Screen 1, roadmap seed is pre-populated and pre-locked with a label "From Project Ideas". Pipeline monitor still loads correctly after this change.
  > Notes: Keep component name `PreflightScreen` and screen key `'preflight'` — no routing changes needed.
  >
  > State in `PreflightScreen`: `repoPath: string`, `repoPathLocked: bool`, `roadmapSeed: string`, `roadmapSeedLocked: bool`. Props: `seedRoadmap: string` (from App, may be empty).
  >
  > On mount: if `seedRoadmap` prop is non-empty, initialize `roadmapSeed` from it and set `roadmapSeedLocked: true`.
  >
  > Repo path is plain text only — browser-only stack, no native directory picker available. Roadmap seed: `<input type="file" accept=".md">` reads file content into `roadmapSeed`. If pre-populated from Screen 1, suppress the file upload button and show the label instead.
  >
  > No validation logic in this phase — that is UI-E7. Confirm button allows locking without validation here. Validation is added in UI-E7 and gates the Confirm button then.

- [ ] `UI-E7` | HIGH | Add validation to Confirm buttons: repo path non-empty check and full roadmap template validation with line-specific errors
  > Test: Clicking "Confirm" on empty repo path shows inline error "Enter a directory path to continue" and does not lock. Clicking "Confirm" on a valid path string locks the field. Clicking "Confirm" on the roadmap seed triggers `POST /api/setup/validate-roadmap`. A valid roadmap (all phases match format, every phase has `> Test:`, unique IDs, correct ID format) shows "Valid ✓" and locks. A malformed phase line shows an error with line number, offending content, and expected format. A phase missing `> Test:` names the phase ID. Duplicate IDs are listed. No malformed seed silently passes. Validation is re-runnable after unlocking.
  > Notes: **Roadmap validation must be thorough.** The agent must read the canonical template at `/home/pi/.openclaw/deployment-package/Updates/Dev_Roadmap_template v3 (updated for oc-auto-dev).md` before implementing `_validate_roadmap_content()`. Understand which fields the pipeline actually reads at runtime (phase IDs, risk levels, goals, test lines, checkbox states) and validate all of them.
  >
  > **Python implementation** — add `_validate_roadmap_content(content: str) -> dict` to `server.py`:
  >
  > Check 1 — Phase line format (Python `re.MULTILINE`): `r'^- \[.\] `[A-Z]+-[A-Z]\d+` \| (?:LOW|HIGH) \| .+'`. Every phase line must match. Record malformed lines with line number.
  >
  > Check 2 — Test line presence: for each matched phase line at line N, scan lines N+1 through N+10 for `r'^\s*> Test:'`. If not found, record error: `"Phase {id} (line {N}) is missing a '> Test:' line"`.
  >
  > Check 3 — Phase ID format: `re.findall(r'\`([A-Z]+-[A-Z]\d+)\`', content)`. IDs must match `[A-Z]+-[A-Z]\d+` exactly.
  >
  > Check 4 — Unique IDs: report any duplicates by name.
  >
  > Check 5 — At least one phase exists. Return error if zero phases found.
  >
  > Return: `{"valid": bool, "errors": [{"line": int, "content": str, "message": str}]}`.
  >
  > **Repo path validation**: `POST /api/setup/validate-repo-path` — body `{"path": str}`. Check: non-empty, no null bytes, length < 512. Return `{"valid": bool, "error": str|null}`. Do NOT check filesystem existence — that happens in preflight.
  >
  > After implementing, add to `lessons.md`: "The roadmap validation regex in `server.py _validate_roadmap_content()` must be kept in sync with `init-project/SKILL.md` Step 4 manually — no automated sync."

- [ ] `UI-E8` | HIGH | Add active preflight validation that auto-resolves what it can and clearly communicates what requires operator action
  > Test: "Run Preflight" calls `POST /api/setup/preflight`. Response contains `checks` array. Each check has `check`, `status` (`pass`/`fail`/`warn`/`fixed`), and `message`. Checks run in order: symlink, .gitignore presence, .gitignore entries, git repo + branch, workspace directories + docs, git remote. Auto-resolvable items (symlink, gitignore) are fixed immediately and reported as `fixed`. Items requiring user action (missing git repo, missing workspace docs) show `fail` with the exact command or instruction needed. Git remote shows `warn` with clear setup instructions. Launch button disabled until no `fail` status remains.
  > Notes: **Active preflight — fix what you can, clearly communicate the rest.** This screen's job is to get the project ready to launch. Passive reporting is not enough.
  >
  > Add `_run_preflight_checks(repo_path: str) -> list[dict]` to `server.py`. All checks in Python. Expand `~` via `os.path.expanduser()`. `status` values: `pass`, `fail`, `warn`, `fixed`.
  >
  > **Check 1 — Symlink** (`~/.openclaw/pipeline-project`): If missing or pointing to wrong path, **create it**: `os.remove(symlink_path)` if exists, then `os.symlink(os.path.realpath(repo_path), symlink_path)`. Report `fixed` with message "Symlink created → {repo_path}". If creation fails (permissions), report `fail` with exact command: `ln -sfn {repo_path} ~/.openclaw/pipeline-project`.
  >
  > **Check 2 — `.gitignore` presence**: If missing, **create it** with the 7 pipeline metadata entries plus the `# Pipeline metadata` header comment. Report `fixed` with "Created .gitignore with pipeline entries."
  >
  > **Check 3 — `.gitignore` entries**: Required entries: `*.done`, `phase_state.json`, `planner_output.json`, `executor_output.json`, `reviewer_output.json`, `escalation_output.json`, `current_phase.json`. If any missing, **append them** with the header comment. Report `fixed` with "Added N missing entries: {list}". If all present, report `pass`.
  >
  > **Check 4 — Git repo and branch**: If `{repo_path}/.git` absent, report `fail`: "Not a git repo. Run: `git -C {repo_path} init && git -C {repo_path} checkout -b main`". If `.git` exists, check for `main` or `master` branch via `subprocess.run(["git", "-C", repo_path, "branch", "--list", "main", "master"])`. If neither exists, report `fail`: "No main/master branch. Run: `git -C {repo_path} checkout -b main`".
  >
  > **Check 5 — Workspace directories and docs**: For each of `workspace-{planner,executor,reviewer,escalation}` under `~/.openclaw/`: if dir absent, report `fail`: "~/.openclaw/workspace-{agent}/ missing — operator must create this directory with required files." If present, check for `AGENTS.md`, `TOOLS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`. For each missing file, report `fail`: "workspace-{agent}/{file} missing — operator must install this file." Cannot auto-create — operator responsibility.
  >
  > **Check 6 — Git remote** (non-blocking): `subprocess.run(["git", "-C", repo_path, "remote", "get-url", "origin"])`. If fails: report `warn` with "No git remote configured. Before pushing: `git -C {repo_path} remote add origin {url}`". Does not block launch.
  >
  > After all checks, re-run checks 1-3 (the auto-fixable ones) to confirm fixes landed. Return the post-fix state.
  >
  > **New endpoint**: `POST /api/setup/preflight` — body: `{"repo_path": str}`. Returns `{"checks": [{"check": str, "status": str, "message": str}]}`.

- [ ] `UI-E9` | HIGH | Add launch sequence — initialize project via Python init logic, set symlink, navigate to pipeline monitor
  > Test: Use `/tmp/ui-e9-test-launch` as the fixed test path. Launch button is disabled until: repo path locked (valid), roadmap seed locked (valid), preflight shows no `fail` status. Clicking launch calls `POST /api/setup/launch`. On success: symlink resolves to test path (verify `readlink -f`), pipeline monitor reflects new project's roadmap on next poll (within 3s). On failure: verbatim error shown, user stays on Screen 2. **CRITICAL after tests pass**: immediately restore production symlink `ln -sfn /home/pi/projects/autodev-ui ~/.openclaw/pipeline-project` and confirm before committing. The launch endpoint intentionally changes the symlink — failing to restore it breaks the pipeline run.
  > Notes: **Execution model**: `_run_init_project(repo_path: str, roadmap_seed: str) -> dict` in `server.py`. Pure Python + git subprocess calls. No LLM calls. No OpenClaw agent. No API keys needed here.
  >
  > **Mode detection**: Mode A if `{repo_path}/.git` absent. Mode B if `.git` exists.
  >
  > **Mode A steps**:
  > 1. `os.makedirs` for `phases/`, `tests/`, `src/{name}/` (name = last path segment). Touch `src/{name}/__init__.py`.
  > 2. Write `pipeline.json`: `{"project": name, "created": ISO8601, "current_phase": null, "current_plan": null, "phase_start_time": null, "completed_count": 0, "status": "idle"}`. Atomic.
  > 3. Write `roadmap.md` from `roadmap_seed`. Atomic.
  > 4. Validate roadmap via `_validate_roadmap_content()`. If invalid, delete created files and return error.
  > 5. Write `prd.md` placeholder, `lessons.md` skeleton, `metrics.jsonl` (empty).
  > 6. Write `.gitignore` with Python tooling entries (`__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.egg-info/`, `dist/`, `build/`, `.venv/`, `.ruff_cache/`) plus 7 pipeline metadata entries with `# Pipeline metadata` header.
  > 7. `git init`, `git checkout -b main`, `git add -A`, `git commit -m "init: project structure with roadmap"`. Raise on any subprocess failure.
  > 8. Set symlink: remove existing (`os.remove()`), create new (`os.symlink(realpath, symlink_path)`).
  >
  > **Mode B steps**: Audit existing structure. Create only missing files using same templates. Never overwrite existing. Append only missing gitignore entries. `git add` + commit new files only. Set symlink last.
  >
  > **On failure**: Catch `subprocess.CalledProcessError` and `OSError`. Return `{"ok": false, "error": str(e)}`. Mode A only: attempt `shutil.rmtree(repo_path, ignore_errors=True)` on failure.
  >
  > **New endpoint**: `POST /api/setup/launch` — body: `{"repo_path": str, "roadmap_seed": str}`. Synchronous, blocking (< 5s). Returns `{"ok": bool, "error": str|null}`.
  >
  > After symlink set, `/api/state` and `/api/roadmap` reflect new project on next 3s poll — no additional server changes needed.

---

## 4) Change Control

- The agent never modifies phase goals or test intent. It only changes checkboxes.
- Any change to a phase Goal or Test Intent requires human approval.
- If cloud reviewer issues REJECTED three times on the same phase, mark `- [!]` and notify human.
- If the goal is discovered impossible or wrong, mark `- [!]`, note in `lessons.md`, notify human.

---

## 5) Appendix: Project Metadata

```
Project:     autodev-ui-screens
Created:     2026-03-19 (rebuilt 2026-03-20 with lessons from first execution)
Models:      openrouter/minimax/minimax-m2.7 (execute) + claude-sonnet-4-6 (plan + review)
Repository:  /home/pi/projects/autodev-ui
PRD:         docs/prd/autodev-ui-screens.prd.md
```

---

## 6) Appendix: Glossary

- **prd-creator agent**: Lives at `~/.openclaw/workspace-prd-creator/`. Conversational, question-driven. Uses sentinel polling — writes `turns/{n}.md`, `prd_draft.md`, then `turns/{n}.done` as final act. Session key format: `ideas:{id}:session-{n}`.
- **Sentinel polling**: Server sends webhook → polls for `{name}.done` every 2s → reads response file when found. No streaming. This is the only mechanism for capturing agent output from OpenClaw webhook invocations.
- **Auto-naming**: After each agent turn, server extracts first `# ` heading from `prd_draft.md` and writes to `session.json` `name` field. Ideas appear in the list only after `turns/1.done` exists.
- **Active preflight**: Preflight checks auto-fix what they can (symlink, gitignore). Items requiring operator action get exact commands. Nothing is just reported passively.
- **ideas_dir**: `~/.openclaw/ideas` — root for all idea documents. One UUID subdirectory per idea.
- **Roadmap seed**: `roadmap.md` content for Screen 2. From Screen 1 conversion or user-supplied file. Must pass full template validation before locking.
- **Init-project logic**: `_run_init_project()` in `server.py` — Python reimplementation of `init-project/SKILL.md`. Pure filesystem + git. No LLM calls.
- **Conversion prompt**: `~/.openclaw/deployment-package/Updates/PRD to Roadmap (sonnet 4.5 ideal).txt` — confirmed on disk.
- **Readiness (FIX-PASS-1)**: `GET /api/ideas/{id}/readiness` returns `{"status","data"}` from `readiness.json` / `readiness.done` (agent assessment via `readiness-reviewer` skill), not Python heuristics. `GET /api/ideas/{id}/readiness/poll` returns `{"done": bool}`.
- **Setup QOL (FIX-PASS-2)**: `POST /api/setup/validate-repo-path` debounced in UI for format ✓/✗. `POST /api/setup/check-repo-path` returns `{exists, parent_exists, is_git_repo, path}` for create-folder flow. `POST /api/setup/create-repo-dir` creates a single directory when parent exists. Roadmap seed uses **Paste** vs **Upload** toggle; upload uses a styled button (hidden file input). Preflight button becomes **Re-run Preflight** after first run with last-run time; pulses on any `fail`. Ideas list shows relative `updated` timestamps; delete uses inline confirmation; conversation auto-scrolls to latest message.