# Development Roadmap

> Source PRD: `docs/prd/autodev-ui-screens.prd.md`

This document is the **project's source of truth** for intent, phase sequencing, and execution state for the AutoDev UI enhancement — Project Idea & Setup Screens.

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

- **Scope boundary**: Greenfield projects only. Enhancement flow for existing projects is explicitly out of scope.
- **Atomic writes**: Every endpoint that writes files uses temp-file + `os.replace()` pattern.
- **Error handling**: All endpoints return sensible defaults for missing files — never unhandled 500s. Match the `_read_json_file()` null-safety pattern in `server.py`.
- **React naming**: Top-level screens use `{Name}Screen`, sub-components use `{Name}Panel`, primitives use descriptive PascalCase. All are `function` declarations inside the single `<script type="text/babel">` block in `index.html`.
- **Design system**: Body bg `#0d0f12`, panel bg `#141618`, element bg `#1a1d21`, border `#1a1d21`, accent `#00b4d8`. Header text via class `header-text` (JetBrains Mono). Body text IBM Plex Sans. Tailwind only — no inline styles.

---

## 2) Milestones

- **M0 — Pre-flight Bug Fix**: `INFRA-B1`
- **M1 — Infrastructure**: `INFRA-E1`
- **M2 — Screen 1 Core**: `UI-E1`, `UI-E2`, `UI-E3`
- **M3 — Screen 1 Upload & Progression**: `UI-E4`, `UI-E5`
- **M4 — Screen 2: Setup & Preflight**: `UI-E6`, `UI-E7`, `UI-E8`, `UI-E9`

### Dependency Order

```
INFRA-B1
  └── INFRA-E1
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

INFRA-B1 is a hard gate — nothing else starts until it is committed. INFRA-E1 must be complete before UI-E8 (preflight workspace check relies on audited workspace structure).

---

## 3) Phase Catalog

### Milestone 0 — Pre-flight Bug Fix

- [x] `INFRA-B1` | HIGH | Fix --project-path CLI argument mismatch in server.py, add prd-creator to OpenClaw config, and add all new config keys to server.py
  > Test: After this phase: (1) `POST /api/resume-orchestrator` spawns the orchestrator with `--project-path` — verify by inspecting the spawned process args via `ps aux` and confirming `pipeline_state.json` contains `project_path` after a mock resume cycle. (2) `~/.openclaw/openclaw.json` `hooks.allowedAgentIds` contains `"prd-creator"`. (3) `~/.openclaw/openclaw.json` `hooks.allowedSessionKeyPrefixes` contains `"ideas:"`. (4) `server.py` DEFAULTS contains `ideas_dir`, `hooks_url`, `hooks_token`, `conversion_prompt_path`. (5) `ui/config.json` contains `ideas_dir`, `hooks_url`, `hooks_token`, `conversion_prompt_path`.
  > Notes: **Fix 1 — CLI arg**: In `ui/server.py` `post_resume_orchestrator()` (currently at line ~950), change `"--project"` to `"--project-path"`. The orchestrator defines this arg at `orchestrator.py` line 2098 with `dest="project_path"`. No other change to the orchestrator.
  >
  > **Fix 2 — openclaw.json edits** (`~/.openclaw/openclaw.json`): (a) Add `"prd-creator"` to the `hooks.allowedAgentIds` array (currently `["planner","executor","reviewer","escalation"]` — append to end). (b) Add `"ideas:"` to the `hooks.allowedSessionKeyPrefixes` array (currently `["pipeline:"]` — append). After editing, restart the OpenClaw gateway for changes to take effect.
  >
  > **Fix 3 — Config keys**: Add to `server.py` DEFAULTS dict:
  > ```python
  > "ideas_dir": "~/.openclaw/ideas",
  > "hooks_url": "http://localhost:18789/hooks/agent",
  > "hooks_token": "pipeline-secret-token",
  > "conversion_prompt_path": "~/.openclaw/deployment-package/Updates/PRD to Roadmap (sonnet 4.5 ideal).txt",
  > ```
  > Add the same four keys to `ui/config.json`. `load_config()` already expands `~` on all string values, so these work automatically. `conversion_prompt_path` is confirmed to exist at that path — no blocker.

---

### Milestone 1 — Infrastructure

- [x] `INFRA-E1` | HIGH | Extend init-project skill to cover all repo_init_check.py requirements and validate end-to-end
  > Test: Use `/tmp/infra-e1-test-a` (Mode A) and `/tmp/infra-e1-test-b` (Mode B) as the fixed test paths — hardcoded, not pytest `tmp_path`. **Do NOT use pytest for any test that involves the symlink or running the skill. All symlink verification must be done with direct shell commands (`readlink -f`, `ls -la`) and direct Python `subprocess` calls — never inside a pytest test function, because pytest teardown cannot be guaranteed to run before the orchestrator's sentinel timeout fires.** Run the extended skill against each path using direct shell calls. After Mode A: (1) `readlink -f ~/.openclaw/pipeline-project` equals `/tmp/infra-e1-test-a`; (2) project root contains `roadmap.md` that passes format validation via `python3 gate_scripts/roadmap_parser.py`; (3) `cat /tmp/infra-e1-test-a/.gitignore` contains all 7 required pipeline entries; (4) `python3 gate_scripts/repo_init_check.py /tmp/infra-e1-test-a` exits 0. After Mode B: same checks pass, no existing files overwritten. **CRITICAL — after both modes pass**: immediately run `ln -sfn /home/pi/projects/autodev-ui ~/.openclaw/pipeline-project` and verify `readlink -f ~/.openclaw/pipeline-project` = `/home/pi/projects/autodev-ui` before writing any output files or committing. Failure to restore will break the orchestrator.
  > Notes: Read `~/.openclaw/workspace/skills/init-project/SKILL.md` in full before making any changes. The skill file is the source of truth for the steps — this phase adds to it, it does not replace it.
  >
  > **Gap 1 — symlink**: Both Mode A and Mode B must add a final step: `ln -sfn {project_dir} ~/.openclaw/pipeline-project`. Place this as the last action before the final verification step in each mode. **After both test modes complete and pass, run `ln -sfn /home/pi/projects/autodev-ui ~/.openclaw/pipeline-project` to restore the production symlink before committing.** Do not commit until this restore is confirmed.
  >
  > **Gap 2 — .gitignore pipeline entries**: Mode A Step 6 writes a `.gitignore` with only Python tooling entries. Extend the heredoc template to append these 7 entries with a section header:
  > ```
  > # Pipeline metadata — orchestrator-managed per-turn state, never committed
  > *.done
  > phase_state.json
  > planner_output.json
  > executor_output.json
  > reviewer_output.json
  > escalation_output.json
  > current_phase.json
  > ```
  > For Mode B Step 3, audit the existing `.gitignore` and append only missing entries from this exact list. Do NOT add `current_phase_????????` or `phase_state_????????` — these are not checked by `repo_init_check.py`.
  >
  > **Gap 3 — workspace docs check**: Add a workspace validation step in both modes (before reporting success) that checks each of the 4 workspace dirs (`~/.openclaw/workspace-{planner,executor,reviewer,escalation}`) for the 5 required files: `AGENTS.md`, `TOOLS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`. If any are missing, report clearly: `[WARN] workspace-{agent}/{file} missing — operator must install this file`. Do NOT create these files. Do NOT fail — report and continue. These are operator responsibility.
  >
  > **Non-gap clarification**: `pipeline.json` (written by skill, lives in project dir) and `pipeline_state.json` (written by orchestrator, lives at `~/.openclaw/pipeline_state.json`) are intentionally different files. The skill writes `pipeline.json` only. Never write `pipeline_state.json` from the skill. Document this distinction in `lessons.md` after this phase.
  >
  > **Validation regex** (Mode A Step 4 and Mode B Step 5 — already in skill, no change needed): Phase line format: `^\- \[.\] \`[A-Z]+-[A-Z]\d+\` \| (LOW|HIGH) \| .+`

---

### Milestone 2 — Screen 1 Core

- [ ] `UI-E1` | LOW | Render the Screen 1 split-panel scaffold with a conversation pane and a static PRD document pane
  > Test: Navigating to the Ideas screen (`currentScreen === 'ideas'`) displays two side-by-side panels filling the available content area. Left panel shows an empty message list area and a text input pinned to the bottom. Right panel shows a placeholder PRD skeleton with all 12 canonical section headers rendered as formatted markdown `##` headings with dim placeholder text under each. Both panels scroll independently. No agent session is wired — all content is static. No console errors.
  > Notes: Replace the existing `IdeasScreen` function (lines 1150–1164 in `ui/index.html`) entirely. Keep the component name `IdeasScreen`.
  >
  > **Layout**: Use `flex h-full` to fill the parent container. Left pane: `w-[38%] flex-shrink-0`. Right pane: `flex-1`. Separator: `border-r border-[#1a1d21]`. Each pane: `flex flex-col bg-[#141618] overflow-hidden`. Conversation pane inner: `flex-1 overflow-y-auto p-4` (messages area) + `border-t border-[#1a1d21] p-3` (input area). Document pane inner: `flex-1 overflow-y-auto p-4`.
  >
  > **PRD skeleton section headers** (hardcoded — `prd_template.txt` does not exist on disk; source of truth is `~/.openclaw/workspace/skills/prd-creator/skill.md`): `## Problem Statement`, `## Goals & Success Metrics`, `## User Stories`, `## Functional Requirements`, `## Edge Cases`, `## Non-Functional Requirements`, `## Dependencies & Integrations`, `## Milestones & Timeline`, `## Risks & Mitigations`, `## Open Questions`, `## Glossary & Domain Terms`, `## Revision History`. Render each header followed by a dim placeholder line: `*Empty — start a conversation to populate this section.*` in `text-slate-600 italic text-sm`.
  >
  > **No agent wiring in this phase.** Agent wiring is UI-E2. Text input submit does nothing. Message list is empty.

- [ ] `UI-E2` | HIGH | Wire the prd-creator agent session to Screen 1 with sentinel polling, live document updates, and persisted conversation history
  > Test: Sending a message in the conversation pane posts to `POST /api/ideas/{id}/message`. The server calls the OpenClaw webhook, then polls for the agent's turn-completion sentinel (max 120s, 2s intervals). While polling is active, the document pane shows a visible loading state. The agent's response appears in the conversation pane after polling completes. The right-panel document updates with the new PRD content. Refreshing the page and reopening the document restores both conversation history and document state without data loss. A unique session key is used per idea document per turn: `ideas:{id}:session-{n}` where n increments each turn.
  > Notes: **Architecture — sentinel polling (not streaming)**: The OpenClaw webhook (`POST /hooks/agent`) returns immediately after queuing the agent. The agent runs asynchronously. The server polls for output. Workflow: send webhook → poll `~/.openclaw/ideas/{id}/turns/{turn_n}.done` every 2 seconds up to 120 seconds → on sentinel found, read `~/.openclaw/ideas/{id}/turns/{turn_n}.md` as the agent's response.
  >
  > **Loading animation**: While polling is active (webhook sent, sentinel not yet found), apply a pulsing opacity animation to the document pane to signal the document is being updated. Use the existing `status-pulse` keyframe already defined in `index.html` CSS, applied as an overlay or wrapper class on the document pane content. Remove the animation immediately when the sentinel is found and `prd_draft.md` is read. This prevents the user from reading partially-updated content as final.
  >
  > **Webhook call spec** — POST to `config.hooks_url` (`http://localhost:18789/hooks/agent`):
  > ```
  > Headers: Authorization: Bearer {config.hooks_token}, Content-Type: application/json
  > Body:
  > {
  >   "agentId": "prd-creator",
  >   "sessionKey": "ideas:{id}:session-{n}",
  >   "wakeMode": "now",
  >   "message": "{user_message_text}\n\n[SYSTEM] Write your full turn response to ~/.openclaw/ideas/{id}/turns/{turn_n}.md. When done, create the file ~/.openclaw/ideas/{id}/turns/{turn_n}.done. Also write the current complete PRD document (all sections populated so far) to ~/.openclaw/ideas/{id}/prd_draft.md after every turn."
  > }
  > ```
  > `{id}` and `{turn_n}` are filled by the server. `turn_n` starts at 1 and increments per message. Read `hooks_token` from `load_config()`.
  >
  > **Session persistence** — `~/.openclaw/ideas/{id}/session.json` schema:
  > ```json
  > {
  >   "messages": [{"role": "user|assistant", "content": "...", "ts": "ISO8601"}],
  >   "prd_content": "full PRD markdown string or empty string",
  >   "created": "ISO8601",
  >   "updated": "ISO8601"
  > }
  > ```
  > Write atomically: `session.json.tmp` → `os.replace()` → `session.json`. After each agent turn: append user and assistant messages, update `prd_content` from `prd_draft.md`, update `updated` timestamp.
  >
  > **New server endpoints**:
  > - `POST /api/ideas/{id}/message` — body: `{"content": str, "turn": int}`. Sends webhook, polls for sentinel, reads agent response, persists to `session.json`, returns `{"response": str, "prd_content": str}`. Returns 408 on timeout.
  > - `GET /api/ideas/{id}/session` — returns full `session.json` contents or `{"messages": [], "prd_content": "", "created": null, "updated": null}` if not found.
  >
  > **prd-creator skill behavior**: Conversational, no sentinel files on its own. Signals PRD completion by appending `> ✅ PRD CONVERSION-READY` to `prd_draft.md`. Server detects readiness by checking `prd_content` for this string.

- [ ] `UI-E3` | LOW | Add document management — list, create, resume, and delete idea documents
  > Test: The Ideas screen shows a document list. Creating a new document generates a UUID, creates `~/.openclaw/ideas/{id}/` and an empty `session.json`, adds it to the list, and opens a fresh session. Selecting an existing document restores conversation history and document state. Deleting a document shows a confirmation prompt, then removes the directory and its entry from the list. Download button produces a valid `.md` file named `{project-name}-prd.md`. Summary line shows the first sentence after `## Problem Statement`, or is blank if unpopulated.
  > Notes: **Storage**: `~/.openclaw/ideas/` (from `config.ideas_dir`). Each idea is a subdirectory `{uuid}/` containing `session.json` and optionally `prd_draft.md`, `roadmap_draft.md`, turn files.
  >
  > **Summary extraction**: Parse `session.json` → `prd_content`. Find the first non-blank line after `## Problem Statement`. Take text up to the first `.` or end of line. Return empty string if section absent or blank.
  >
  > **New server endpoints**:
  > - `GET /api/ideas` — lists all subdirectories under `ideas_dir`. For each: `{id, name, summary, updated}`. Returns `[]` if directory absent or empty.
  > - `POST /api/ideas` — creates `{ideas_dir}/{uuid}/`, writes empty `session.json`, returns `{"id": uuid}`.
  > - `DELETE /api/ideas/{id}` — deletes `{ideas_dir}/{id}/` recursively via `shutil.rmtree`. Returns 404 if not found.
  > - `GET /api/ideas/{id}/download` — returns `prd_content` from `session.json` as file response with `Content-Disposition: attachment; filename="{name}-prd.md"`. Name derived from first `#` heading in `prd_content` or fallback to id.

---

### Milestone 3 — Screen 1 Upload & Progression

- [ ] `UI-E4` | HIGH | Add PRD upload flow with agent clarity check and format validation gate
  > Test: A file upload input on the Ideas screen accepts `.md` files only. Uploading a file containing `## Problem Statement`, `## Goals & Success Metrics`, and `## Functional Requirements` headers triggers a clarity check agent call and shows "ready to convert" on pass. A file missing any of those three headers is rejected server-side with a message naming each missing header — the agent is never called for a rejected file. A non-`.md` file is rejected client-side. No non-conforming file silently passes. The clarity check uses the sentinel polling pattern (max 60s, 2s intervals).
  > Notes: **Upload endpoint**: `POST /api/ideas/{id}/upload` — body: multipart form with `file` field. Server validates `.md` extension and presence of the 3 required headers before any agent call. On format pass: atomic-write uploaded content to `session.json.prd_content` and trigger clarity check.
  >
  > **Clarity check endpoint**: `POST /api/ideas/{id}/clarity-check` — no body (reads current `prd_content` from `session.json`). Sends webhook:
  > ```
  > {
  >   "agentId": "prd-creator",
  >   "sessionKey": "ideas:{id}:clarity-{timestamp_ms}",
  >   "wakeMode": "now",
  >   "message": "Review the following PRD for clarity and completeness. Do not write or modify any files other than clarity_result.json and clarity_result.done listed below. Analyze whether all essential sections are present and well-formed. Write a JSON object to ~/.openclaw/ideas/{id}/clarity_result.json with schema {\"pass\": bool, \"missing_sections\": [str], \"issues\": [str]}, then create ~/.openclaw/ideas/{id}/clarity_result.done.\n\nPRD CONTENT:\n{prd_content}"
  > }
  > ```
  > Server polls for `clarity_result.done` (2s interval, 60s timeout). Reads `clarity_result.json`. Returns `{"pass": bool, "missing_sections": [], "issues": []}`.
  >
  > **Tool restriction**: Enforced via message instruction only — the OpenClaw webhook has no per-call tool restriction field. The message explicitly prohibits file writes except the result files.

- [ ] `UI-E5` | HIGH | Add progression flow — trigger PRD-to-roadmap conversion, surface outputs, offer navigation to Screen 2
  > Test: A "Generate Roadmap" button appears when `prd_content` contains `> ✅ PRD CONVERSION-READY` OR when the user explicitly clicks it. Clicking triggers conversion. On success: roadmap content is shown in the UI and downloadable as `{name}-roadmap.md`. A "Proceed to Setup" button navigates to Screen 2 with the roadmap pre-populated. If conversion fails (timeout or missing result file), the raw error is displayed and the user can retry. "Generate Roadmap" is disabled if `prd_content` is empty.
  > Notes: **Readiness detection**: `GET /api/ideas/{id}/readiness` — returns `{"ready": bool, "reason": str}`. Ready if `prd_content` contains `> ✅ PRD CONVERSION-READY` OR all 10 required sections are present with non-empty content: `## Problem Statement`, `## Goals & Success Metrics`, `## User Stories`, `## Functional Requirements`, `## Edge Cases`, `## Non-Functional Requirements`, `## Dependencies & Integrations`, `## Risks & Mitigations`, `## Open Questions`, `## Glossary & Domain Terms`. A section is non-empty if content between its `##` header and the next `##` header contains at least one non-blank, non-header line.
  >
  > **Conversion prompt**: Located at `config.conversion_prompt_path` — resolves to `~/.openclaw/deployment-package/Updates/PRD to Roadmap (sonnet 4.5 ideal).txt`. Confirmed to exist on disk. Server reads this file at request time. If missing, return 503 immediately.
  >
  > **Conversion endpoint**: `POST /api/ideas/{id}/convert`. Reads conversion prompt from `config.conversion_prompt_path`. Sends webhook:
  > ```
  > {
  >   "agentId": "prd-creator",
  >   "sessionKey": "ideas:{id}:convert-{timestamp_ms}",
  >   "wakeMode": "now",
  >   "message": "{conversion_prompt_content}\n\n---\n\n{prd_content}\n\nWrite the resulting roadmap.md content to ~/.openclaw/ideas/{id}/roadmap_draft.md, then create ~/.openclaw/ideas/{id}/roadmap_draft.done."
  > }
  > ```
  > Server polls for `roadmap_draft.done` (2s interval, 180s timeout). Reads `roadmap_draft.md`. Stores content atomically in `session.json` as `roadmap_content`. Returns `{"roadmap_content": str}`.
  >
  > **Navigation**: The `App` component holds `seedRoadmap` state alongside `currentScreen`. When navigating to `preflight` after conversion, set `seedRoadmap` to the roadmap content. `PreflightScreen` receives it as a prop.
  >
  > **Additional endpoint**: `GET /api/ideas/{id}/download-roadmap` — returns `roadmap_content` from `session.json` as file response `{name}-roadmap.md`.

---

### Milestone 4 — Screen 2: Setup & Preflight

- [ ] `UI-E6` | LOW | Render Screen 2 with repo path input and roadmap seed input, both with lock/confirm behavior
  > Test: Screen 2 (`preflight` screen) displays a repo path text input and a roadmap seed input. Each has a lock/unlock toggle. Locking freezes the field as a read-only display with an unlock option. Unlocking restores editability. Fields are independent. If navigated from Screen 1 after conversion, the roadmap seed field is pre-populated with the generated content and pre-locked. No validation runs in this phase. No console errors.
  > Notes: Replace the existing `PreflightScreen` function (lines 1129–1148 in `ui/index.html`). Keep component name `PreflightScreen` and screen key `'preflight'` — no routing changes needed.
  >
  > **State**: `repoPath: string`, `repoPathLocked: bool`, `roadmapSeed: string`, `roadmapSeedLocked: bool`. Update the `App` component to hold `seedRoadmap` state (set by UI-E5). Pass it as a prop to `PreflightScreen`, which initializes `roadmapSeed` from it if present.
  >
  > **Repo path**: Plain text input only. No native directory picker — browser-only stack confirmed (no `package.json`, no Electron).
  >
  > **Roadmap seed**: `<input type="file" accept=".md">` reads file content into `roadmapSeed`. If pre-populated from Screen 1, show a text indicator "From Project Ideas — {first 40 chars...}" and suppress the upload button. User can unlock the field to upload a different file.
  >
  > **New server endpoint**: `POST /api/setup/roadmap-seed` — body: `{"content": str}`. Stores roadmap content atomically to `~/.openclaw/setup_session.json` for use by UI-E7 validation. Returns `{"ok": true}`.

- [ ] `UI-E7` | HIGH | Add roadmap seed format validation with line-specific errors
  > Test: Triggering validation on a locked roadmap seed calls `POST /api/setup/validate-roadmap`. A valid seed returns `{"valid": true, "errors": []}`. A malformed phase line returns an error with the exact line number, the offending content, and the expected format. A phase missing `> Test:` returns an error naming the phase ID. Duplicate IDs are listed by name. No malformed seed silently passes. The validate button is re-runnable.
  > Notes: **Python implementation**: Add `_validate_roadmap_content(content: str) -> dict` to `server.py`. This is a Python reimplementation of the bash checks in `~/.openclaw/workspace/skills/init-project/SKILL.md` Step 4. Do NOT call the skill as a subprocess.
  >
  > **Phase line regex** (Python `re`, `MULTILINE` flag): `r'^- \[.\] `[A-Z]+-[A-Z]\d+` \| (?:LOW|HIGH) \| .+'`
  >
  > **Test-line check**: For each matched phase line at line N, scan the next 10 lines for `r'^\s*> Test:'`. If not found, record error: `"Phase {id} (line {N}) is missing a '> Test:' line"`.
  >
  > **Uniqueness check**: `re.findall(r'\`([A-Z]+-[A-Z]\d+)\`', content)` — report any duplicates.
  >
  > **Return schema**: `{"valid": bool, "errors": [{"line": int, "content": str, "message": str}]}`
  >
  > **After implementation**: Record in `lessons.md`: "The roadmap validation regex in `server.py _validate_roadmap_content()` must be kept in sync with `init-project/SKILL.md` Step 4 manually — no automated sync."
  >
  > **New server endpoint**: `POST /api/setup/validate-roadmap` — body: `{"content": str}`. Returns validation result. No file writes.

- [ ] `UI-E8` | HIGH | Add orchestrator preflight validation with per-check status display and .gitignore auto-inject
  > Test: "Run Preflight" calls `POST /api/setup/preflight` with `{"repo_path": str}`. Response contains a `checks` array, each with `check`, `status` (`pass`/`fail`/`warn`), and `message`. Checks shown: symlink, .gitignore presence, .gitignore entries (with inject report), git repo with main/master branch, per-workspace directory + required docs, git remote (warn-only), roadmap file (warn-only). Missing .gitignore entries are auto-injected and reported. All failures include specific actionable messages. Launch button disabled until no `fail` status in any check.
  > Notes: **Implementation**: Add `_run_preflight_checks(repo_path: str) -> list[dict]` to `server.py`. All checks are Python — do NOT call `repo_init_check.py` as a subprocess (it exits 0/1 with human-readable stdout, no per-check JSON). Expand `~` in `repo_path` via `os.path.expanduser()` before any checks.
  >
  > **Check list** (in order):
  >
  > 1. **Symlink**: `~/.openclaw/pipeline-project` exists and `os.path.realpath()` equals `repo_path`. FAIL if absent or pointing elsewhere. Message on fail: `"Symlink missing or wrong — run: ln -sfn {repo_path} ~/.openclaw/pipeline-project"`. Note: symlink is SET during launch (UI-E9).
  >
  > 2. **`.gitignore` presence**: File exists at `{repo_path}/.gitignore`. FAIL if absent.
  >
  > 3. **`.gitignore` entries**: Check for the 7 required entries: `*.done`, `phase_state.json`, `planner_output.json`, `executor_output.json`, `reviewer_output.json`, `escalation_output.json`, `current_phase.json`. Auto-inject any missing by appending `\n# Pipeline metadata — orchestrator-managed per-turn state, never committed\n{missing entries}`. Report PASS with message `"Added N entries: {list}"` if injected, or `"All required entries present"` if already complete.
  >
  > 4. **Git repo**: `{repo_path}/.git` exists. FAIL if absent. Check for `main` or `master` branch: `subprocess.run(["git", "-C", repo_path, "branch", "--list", "main", "master"], capture_output=True, text=True)`. FAIL if output is empty (neither branch exists).
  >
  > 5. **Workspace directories and docs**: For each of `workspace-planner`, `workspace-executor`, `workspace-reviewer`, `workspace-escalation` under `~/.openclaw/`: FAIL if directory absent. If present, check for `AGENTS.md`, `TOOLS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`. FAIL for each missing doc. Message: `"workspace-{agent}/{doc} missing — operator must install this file."`
  >
  > 6. **Git remote** (non-blocking): `subprocess.run(["git", "-C", repo_path, "remote", "get-url", "origin"], capture_output=True)`. WARN if command fails. Message: `"No git remote configured — pushes will produce warnings."`
  >
  > 7. **Roadmap file** (non-blocking): `glob.glob(f"{repo_path}/*oadmap*.md")`. WARN if empty. Message: `"No roadmap file found — launch step will write roadmap.md from seed."`
  >
  > **New server endpoint**: `POST /api/setup/preflight` — body: `{"repo_path": str}`. Returns `{"checks": [{"check": str, "status": str, "message": str}]}`.

- [ ] `UI-E9` | HIGH | Add launch sequence — initialize project directory, set symlink, navigate to pipeline monitor
  > Test: Use `/tmp/ui-e9-test-launch` as the fixed test repo path for all executor testing. Launch button is disabled until repo path is locked, roadmap seed is locked and valid (`valid: true` from UI-E7), and preflight passes (no `fail` in any check). Clicking launch calls `POST /api/setup/launch`. On success: `~/.openclaw/pipeline-project` symlink resolves to the test repo path — verify via `readlink -f`. Pipeline monitor shows the new project's roadmap on next poll (within 3s). On failure: verbatim error output is displayed, user stays on Screen 2. **CRITICAL — after all tests pass**: immediately restore the production symlink `ln -sfn /home/pi/projects/autodev-ui ~/.openclaw/pipeline-project` and verify `readlink -f ~/.openclaw/pipeline-project` = `/home/pi/projects/autodev-ui` before committing. The launch endpoint intentionally changes the symlink — failing to restore it will break all orchestrator git operations for the remainder of the pipeline run.
  > Notes: **Execution model**: The server implements init-project logic directly in Python as `_run_init_project(repo_path: str, roadmap_seed: str) -> dict`. This is NOT an OpenClaw agent call — the init-project skill is bash-based and cannot be invoked as a subprocess. The Python reimplementation mirrors `~/.openclaw/workspace/skills/init-project/SKILL.md` steps exactly. No LLM calls are made — only filesystem operations and git shell commands. No API keys are needed for this step. All LLM calls in the UI (prd-creator conversations, clarity checks, conversion) route through the OpenClaw gateway using its configured keys — users need no additional key setup.
  >
  > **Mode detection**: `mode = "A"` if `{repo_path}/.git` does not exist. `mode = "B"` if `.git` exists.
  >
  > **Mode A steps** (mirroring SKILL.md):
  > 1. `os.makedirs` for `{repo_path}/phases`, `{repo_path}/tests`, `{repo_path}/src/{name}` (where name = last path segment). Touch `{repo_path}/src/{name}/__init__.py`.
  > 2. Write `{repo_path}/pipeline.json`: `{"project": name, "created": ISO8601, "current_phase": null, "current_plan": null, "phase_start_time": null, "completed_count": 0, "status": "idle"}`. Atomic write.
  > 3. Write `{repo_path}/roadmap.md` from `roadmap_seed`. Atomic write.
  > 4. Validate roadmap via `_validate_roadmap_content()` — if invalid, delete created files and return error immediately.
  > 5. Write `{repo_path}/prd.md` placeholder, `{repo_path}/lessons.md` skeleton, `{repo_path}/metrics.jsonl` (empty).
  > 6. Write `{repo_path}/.gitignore` with Python tooling entries (`__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.egg-info/`, `dist/`, `build/`, `.venv/`, `.ruff_cache/`) plus the 7 pipeline metadata entries with the `# Pipeline metadata` header comment.
  > 7. `subprocess.run(["git", "init", repo_path])`, `git checkout -b main`, `git -C {repo_path} add -A`, `git -C {repo_path} commit -m "init: project structure with roadmap"`. If any subprocess call fails, raise and return error.
  > 8. Set symlink: if `~/.openclaw/pipeline-project` exists (symlink or otherwise), remove it with `os.remove()`, then `os.symlink(repo_path, expanduser("~/.openclaw/pipeline-project"))`.
  >
  > **Mode B steps**: Check existing structure. Create only missing files using same templates. Never overwrite existing files. Append missing pipeline gitignore entries only. Run `git add` + commit of new files only. Set symlink last (same as Mode A step 8).
  >
  > **On failure**: Catch `subprocess.CalledProcessError` and `OSError`. Return `{"ok": false, "error": str(e)}`. In Mode A, attempt cleanup of created directory on failure (`shutil.rmtree(repo_path, ignore_errors=True)`).
  >
  > **New server endpoint**: `POST /api/setup/launch` — body: `{"repo_path": str, "roadmap_seed": str}`. Synchronous/blocking (completes in under 5 seconds). Returns `{"ok": bool, "error": str|null}`.
  >
  > **Post-launch**: After symlink is set, `/api/state` and `/api/roadmap` read through `config.project_dir_path` → `~/.openclaw/pipeline-project` → new project. Monitor reflects new project on next 3-second poll. No additional server changes needed.

---

## 4) Change Control

- The agent never modifies phase goals or test intent. It only changes checkboxes (`- [ ]` → `- [x]`).
- Any change to a phase's Goal or Test Intent requires human approval.
- If cloud reviewer issues REJECTED three times on the same phase, the agent marks it `- [!]` and notifies the human.
- If during execution the agent discovers the goal is impossible or wrong, it must not silently modify the goal. Instead: mark `- [!]`, note in `lessons.md`, notify human.

---

## 6) Appendix: Project Metadata

```
Project:     autodev-ui-screens
Created:     2026-03-19
Models:      openrouter/minimax/minimax-m2.7 (execute) + claude-sonnet-4-6 (plan + review)
Repository:  /home/pi/projects/autodev-ui
PRD:         docs/prd/autodev-ui-screens.prd.md
```

---

## 7) Appendix: Glossary

- **PRD skill**: `/home/pi/.openclaw/workspace/skills/prd-creator/skill.md` — the OpenClaw agent skill for PRD development. Distinct from the init-project skill.
- **Roadmap seed**: `roadmap.md` content provided to Screen 2. From Screen 1 conversion or user-supplied.
- **Preflight**: Environment checks in Screen 2 before launch: symlink, .gitignore, git branch, workspace dirs + docs.
- **Init-project logic**: The sequence of steps from `init-project/SKILL.md` reimplemented in Python in `server.py` as `_run_init_project()`. Makes no LLM calls — pure filesystem and git operations.
- **Conversion prompt**: File at `~/.openclaw/deployment-package/Updates/PRD to Roadmap (sonnet 4.5 ideal).txt`, referenced by `config.conversion_prompt_path`.
- **Sentinel polling**: Server sends webhook → polls for `{name}.done` file → reads `{name}.md` for agent output. This is the only mechanism for capturing agent output from OpenClaw. Streaming does not exist for webhook-invoked agents.
- **ideas_dir**: `~/.openclaw/ideas` — root directory for all idea documents. One subdirectory per idea UUID.
- **SUBSYSTEM**: `INFRA`, `UI` — logical boundaries in this project.
- **Phase**: One atomic unit of work with one goal, one commit, one review cycle.
