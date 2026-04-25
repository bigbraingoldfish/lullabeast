# Development Roadmap — Chat UI Enhancement

This document is the **source of truth** for the Chat UI Enhancement effort. It covers sidebar unification, input ergonomics, project list intelligence, and conversation readability improvements targeted for MVP release polish.

- Process and workflow follow the `autonomous-dev` skill (SKILL.md).
- Phase archives live in `phases/{phase_id}.md` after completion.
- Execution metrics live in `metrics.jsonl`.
- Hard-won insights live in `lessons.md`.

---

## 1) Conventions and Definitions

### Phase ID

Format: `UI-{N}`

All phases in this roadmap belong to the `UI` subsystem. N is monotonic across all milestones in document order.

### Frontend-Only Phases — TDD Note

Several phases modify only `ui/index.html` (the single-file React frontend). There is no compiled build step. For these phases the TDD contract is:

1. Write a `tests/test_ui_{topic}.py` that asserts the **behavioral logic** (state transitions, filter logic, class application conditions) as pure Python assertions — mirroring the pattern in `tests/test_ui_escalation_command_panel.py`.
2. Confirm the test **fails** before touching `index.html`.
3. Implement the change in `index.html`.
4. Confirm the test **passes**.
5. Perform a manual browser verification pass and document the result in the phase archive.

### Done Criteria

A phase is complete only when ALL of the following are true:

1. All tests pass (`pytest tests/ -q` from repo root).
2. Linting passes (`ruff check ui/server.py` for backend phases; no new JS console errors for frontend phases).
3. Cloud review returns **APPROVED**.
4. Changes committed with message `phase(UI-N): {goal summary}`.
5. Roadmap checkbox updated `- [ ]` → `- [x]`.
6. Phase archive written to `phases/UI-N.md`.
7. Metric appended to `metrics.jsonl`.

### Cross-Cutting Constraints

- **TDD**: Tests written before implementation. A test passing before any code changes is invalid.
- **Scope**: Never touch pipeline, orchestrator, or non-UI server routes unless the phase explicitly requires it.
- **Single-file discipline**: `ui/index.html` is intentionally monolithic — do not split it. All frontend changes are in-place edits.
- **Color tokens**: The accent color `#00b4d8` is canonical. Do not introduce new accent colors. Tailwind arbitrary values (`text-[#00b4d8]`) are acceptable — the project uses CDN Tailwind with no build step.
- **Backend additive-only**: API changes in M3 are additive (new fields on existing responses). No existing field is renamed or removed.

---

## 2) Milestones

- **M1 — Quick Wins**: `UI-1`, `UI-2`, `UI-3` — Pure frontend CSS and state changes, zero backend dependency, zero regression risk.
- **M2 — Layout Restructure**: `UI-4`, `UI-5` — Structural HTML/React layout changes that unify the sidebar and fix the input bar.
- **M3 — Backend Enrichment**: `UI-6`, `UI-7` — Additive API fields that power the project list intelligence features.
- **M4 — Hover Card System**: `UI-8`, `UI-9` — Floating hover card with project context; header cleanup that depends on M4 being live.

---

## 3) Phase Catalog

---

### Milestone 1 — Quick Wins

*All three phases touch only `ui/index.html`. No backend changes. Execute in order — each is independent.*

---

- [x] `UI-1` | LOW | Improve chat message bubble readability with updated border-radius and line-height
  > Test: Write `tests/test_ui_chat_bubbles.py`. Assert that the rendering logic for `msg.role === "user"` produces a class string containing `rounded-[12px_12px_4px_12px]` and `leading-[1.6]`. Assert that `msg.role === "assistant"` produces a class string containing `rounded-[12px_12px_12px_4px]`, `border-l-2`, `border-[#00b4d8]`, and `leading-[1.6]`. Both assertions must fail before the `index.html` edit. Manual browser check: open a conversation with ≥ 5 message pairs and confirm the chat feels less dense.
  > Notes: User bubble class target — line 3788 in `index.html`. Assistant bubble class target — line 3848. Change `rounded` → Tailwind arbitrary `rounded-[12px_12px_4px_12px]` / `rounded-[12px_12px_12px_4px]`. Add `leading-[1.6]` to both. Add `border-l-2 border-[#00b4d8]` to assistant bubble only. The left accent border is in scope but is explicitly noted as a preference — if during browser review it looks visually noisy, remove the border and note the decision in the phase archive without requiring a re-review. Line-height change is non-negotiable.

---

- [x] `UI-2` | LOW | Add a title search/filter input above the project list in the chats rail
  > Test: Write `tests/test_ui_project_filter.py`. Assert that given `ideasList = [{id:"a", name:"LLN Lab"}, {id:"b", name:"Chaos Timer"}, {id:"c", name:"Vehicle City"}]` and `filterText = "lln"`, the filtered result contains exactly `[{id:"a", name:"LLN Lab"}]`. Assert that `filterText = ""` returns all three items unchanged. Assert that `filterText = "xyz"` returns an empty list. All assertions must fail before `index.html` edit.
  > Notes: Scope is title-only, case-insensitive substring match (`it.name.toLowerCase().includes(filterText.toLowerCase())`). Add `useState("")` for `filterText`. Render a controlled `<input>` above the `ideasList.map(...)` block (inside the `!chatsRailCollapsed` branch). Show a `×` clear button only when `filterText.length > 0`. No semantic search, no tag filtering, no score filtering — strictly title string match. Input should clear when the user switches to a different nav screen.

---

- [x] `UI-3` | LOW | Raise the auto-grow textarea ceiling from 5 lines to 8 lines (~160 px)
  > Test: Write `tests/test_ui_textarea_autogrow.py`. Assert that the `adjustTa` height calculation `Math.min(el.scrollHeight, maxH)` with `maxH = 160` correctly caps at 160 when scrollHeight exceeds 160, and passes scrollHeight through when scrollHeight ≤ 160. Assert the previous ceiling of 110 is no longer present anywhere in the file. Both assertions must fail before edit.
  > Notes: `adjustTa()` is at line 2987 in `index.html`. Change `const maxH = lh * 5` to `const maxH = 160`. Change the Tailwind class on the `<textarea>` from `max-h-[110px]` to `max-h-[160px]`. Two-line change. The line-height reference variable `lh = 22` stays unchanged — only the ceiling multiplier changes.

---

### Milestone 2 — Layout Restructure

*M2 phases are structural HTML/React changes. Execute UI-4 before UI-5 — the sidebar change establishes the new component prop boundary that UI-5 does not depend on but should not conflict with.*

---

- [x] `UI-4` | LOW | Unify the two independent sidebar collapse controls into a single state and a single chevron toggle in the brand header
  > Test: Write `tests/test_ui_sidebar_collapse.py`. Assert that with `sidebarCollapsed = true`, both the nav rail width resolves to `w-14` and the chats rail renders no project list rows (width resolves to zero / hidden). Assert that with `sidebarCollapsed = false`, the nav rail renders at `w-44` and the chats rail renders at `w-56`. Assert that toggling `sidebarCollapsed` inverts both conditions simultaneously. All assertions must fail before edit.
  > Notes: **Current broken state (reference):** two independent `›` chevrons sit side-by-side at the bottom of the divider border; the chats rail collapses to a black void with no content. This is the primary design deficit being fixed. **Implementation path:** (1) Remove `navCollapsed` state from `NavBar` component and `chatsRailCollapsed` state from `ProjectIdeas`. (2) Lift a single `sidebarCollapsed` state to `App` (or the nearest common parent of both). (3) Pass `sidebarCollapsed` + `setSidebarCollapsed` as props to `NavBar` and thread the value into the chats rail branch. (4) Remove both `side-divider-btn` floating pill toggles. (5) Add a single `‹` / `›` chevron button inside the brand header row (`div` at line 2411), right-aligned, that calls `setSidebarCollapsed`. (6) In collapsed state, the chats rail hides entirely (zero width, `overflow-hidden`) — do NOT render an empty black panel. The nav icons-only view (already working) is sufficient collapsed affordance. Initials avatars in the collapsed chats rail are explicitly **out of scope** for this phase — they can be added later.
  > Reference images: collapsed current state and expanded current state are documented in the session design review (2026-04-24).

![Current collapsed sidebar — two independent chevrons on a black void](../../.cursor/projects/home-pi-projects-autodev-ui/assets/c__Users_Z_AppData_Roaming_Cursor_User_workspaceStorage_31bc52038a997657524af27787110255_images_Screenshot_2026-04-24_152911-d764604e-92da-41d9-b0a7-8458a7424e32.png)

![Current expanded sidebar — two separate panels](../../.cursor/projects/home-pi-projects-autodev-ui/assets/c__Users_Z_AppData_Roaming_Cursor_User_workspaceStorage_31bc52038a997657524af27787110255_images_Screenshot_2026-04-24_152917-bb60d267-1aad-4cbe-9082-e9cfc11a76c4.png)

---

- [x] `UI-5` | LOW | Restructure the chat input bar so Attach and Send are pinned in a dedicated bottom toolbar row below the textarea
  > Test: Write `tests/test_ui_input_bar.py`. Assert that the layout model places the textarea above the action row (column direction, not row). Assert that Send button disabled state is `true` when `inputText.trim() === ""` and `false` when `inputText.trim().length > 0`. Assert that the Send button class includes `bg-[#00b4d8]` when enabled and `bg-[#2a2d31]` when disabled. Assert that Attach button disabled state reflects `!currentIdeaId || isLoading`. All assertions must fail before edit.
  > Notes: Current layout at line 3912 is `flex items-stretch gap-2` with `[Attach] [textarea] [Send]` in a single horizontal row — the Attach and Send buttons stretch vertically with the textarea as it grows, which looks broken on tall inputs. **Target layout:** outer wrapper becomes `flex flex-col`, textarea in the top slot, then a `flex items-center justify-between` row below containing Attach (left, icon + "Attach" text label) and Send (right). The Send button already has correct disabled/enabled CSS — keep it. Verify that the staged attachment pill and annotation pill rows (lines 3895–3910) remain above the outer wrapper and are unaffected. Do not change any submission logic, only the layout structure.

![Target input bar layout — pinned Attach + Send row below textarea](../../.cursor/projects/home-pi-projects-autodev-ui/assets/c__Users_Z_AppData_Roaming_Cursor_User_workspaceStorage_31bc52038a997657524af27787110255_images_Screenshot_2026-04-24_145322-4a9af92d-05cc-49e4-ad9f-468b231fe760.png)

---

### Milestone 3 — Backend Enrichment

*M3 phases modify `ui/server.py` only. Both phases touch the same `get_ideas()` function — execute UI-6 then UI-7 in order so each has a clean, reviewable diff. M4 depends on both being complete.*

---

- [x] `UI-6` | LOW | Add `readiness_score` field to each item in the `GET /api/ideas` response
  > Test: Extend `tests/test_api_ideas_list.py` with a new test class `TestGetIdeasListReadinessScore`. Write tests that: (1) an idea with a `readiness.json` containing `{"score": 7}` returns `{"readiness_score": 7}` in the list item; (2) an idea with no `readiness.json` returns `{"readiness_score": null}` in the list item; (3) an idea with a `readiness.json` that is malformed (invalid JSON) returns `{"readiness_score": null}` without raising an exception. All three tests must fail before the `server.py` edit.
  > Notes: The readiness assessment result is stored per-idea. Confirm the exact storage path by checking `_trigger_readiness_assessment()` at line 3317 of `server.py` — it likely writes to `{ideas_dir}/{idea_id}/readiness.json`. Read this file in `get_ideas()` for each subdirectory, extract `score` (integer or null), and append `readiness_score` to the ideas dict at line 3906. Use a try/except around the file read so a missing or corrupt file always produces `null` — never raises a 500. The field name must be `readiness_score` (snake_case, consistent with existing API style). Do not change any other response fields.

---

- [x] `UI-7` | LOW | Add `has_prd` and `has_roadmap` boolean fields to each item in the `GET /api/ideas` response
  > Test: Extend `tests/test_api_ideas_list.py` with `TestGetIdeasListDocFlags`. Write tests that: (1) an idea with non-empty `prd_content` in `session.json` returns `has_prd: true`; (2) an idea with empty or absent `prd_content` returns `has_prd: false`; (3) an idea with non-empty `roadmap_content` in `session.json` returns `has_roadmap: true`; (4) an idea with empty or absent `roadmap_content` returns `has_roadmap: false`. All four tests must fail before the `server.py` edit.
  > Notes: Both values are already present in `session_data` within `get_ideas()` — `prd_content` at line 3892 and `roadmap_content` can be retrieved the same way. Compute `has_prd = bool((session_data.get("prd_content") or "").strip())` and `has_roadmap = bool((session_data.get("roadmap_content") or "").strip())`. Append both to the dict at line 3906 alongside `readiness_score` from UI-6. The updated response shape for each list item is `{id, name, summary, updated, readiness_score, has_prd, has_roadmap}`. No existing field changes.

---

### Milestone 4 — Hover Card System

*M4 depends on M3 being complete and deployed. Do not start UI-8 until `GET /api/ideas` returns `readiness_score`, `has_prd`, and `has_roadmap`.*

---

- [x] `UI-8` | LOW | Show a floating hover detail card to the right of the sidebar on project row hover (320 ms delay, mouse-leave dismiss)
  > Test: Write `tests/test_ui_hover_card.py`. Assert that hover card visibility logic: `showCard = true` only when `hoveredIdeaId !== null` AND `hoverDelayFired === true`; `showCard = false` immediately when `hoveredIdeaId` is set to `null` regardless of `hoverDelayFired`. Assert score color logic: score ≥ 8 → `text-emerald-400`; 5–7 → `text-amber-400`; < 5 → `text-red-400`; `null` → `text-slate-500` with text "—". Assert doc badge logic: `has_prd: true` → green indicator; `has_prd: false` → gray indicator (same for `has_roadmap`). Assert that the card does not dismiss while the mouse is over the card itself (`mouseLeave` on the row fires the dismiss timer, which cancels if the mouse enters the card before it fires). All assertions must fail before edit.
  > Notes: **Data sourcing:** all required fields (`name`, `summary`, `readiness_score`, `has_prd`, `has_roadmap`) are now in `ideasList` from M3 — no additional fetch required on hover. **Positioning:** use `React.createPortal(cardJSX, document.body)` to escape the sidebar's `overflow-hidden`. Position the card using `getBoundingClientRect()` on the hovered row element — `top` aligns to the row top, `left` is `sidebarWidth + 8px`. **Delay mechanism:** `onMouseEnter` on each project row sets `hoveredIdeaId = it.id` and starts a 320ms `setTimeout` ref. If `onMouseLeave` fires before 320ms, clear the timer and set `hoveredIdeaId = null`. **Mouse-leave edge case:** when the cursor moves from the project row onto the card, the card's own `onMouseEnter` cancels any pending dismiss timer. The card's `onMouseLeave` dismisses immediately. **Card content:** project name (bold), `summary` (2-line clamp, gray), a mini progress bar (`readiness_score / 10` fill, color-coded), score label (`N/10`), two doc rows (`PRD` and `Roadmap`) each with a green check or gray dash. **Empty state:** if `summary` is empty or null, render "No description yet." in slate-500 italic. If `readiness_score` is null, hide the progress bar row and show "—" for score.
  > Inline score badge (sidebar row): as part of this phase, also add the `readiness_score` badge inline to each project row in the chats rail (right-aligned, `text-[10px]`, color-coded: green ≥ 8, amber 5–7, red < 5, hidden if null). This is the same data, zero additional fetch — bundle it here rather than a separate phase.

![Hover card target design — score, description, PRD/Roadmap doc status](../../.cursor/projects/home-pi-projects-autodev-ui/assets/c__Users_Z_AppData_Roaming_Cursor_User_workspaceStorage_31bc52038a997657524af27787110255_images_Screenshot_2026-04-24_145447-c5068604-0395-402f-9433-ee4c56a348e4.png)

![Project list with inline score badges and hover card](../../.cursor/projects/home-pi-projects-autodev-ui/assets/c__Users_Z_AppData_Roaming_Cursor_User_workspaceStorage_31bc52038a997657524af27787110255_images_Screenshot_2026-04-24_145729-5fd85d15-197d-40d1-ba78-9f178a6e78d6.png)

![Hover card empty state — new project with no documentation](../../.cursor/projects/home-pi-projects-autodev-ui/assets/c__Users_Z_AppData_Roaming_Cursor_User_workspaceStorage_31bc52038a997657524af27787110255_images_Screenshot_2026-04-24_151312-690c2c10-efbe-403c-9c9a-9ebb31142ecb.png)

---

- [x] `UI-9` | LOW | Remove the inline readiness score display from the chat panel header bar
  > Test: Write `tests/test_ui_chat_header_score.py`. Assert that the chat panel header render logic does NOT include a readiness score span when `activeDocTab === "prd"` — i.e., the score display condition (`readinessStatus === "ready" && readinessData`) no longer produces a visible score element in the header. The score must still exist in `readinessData` state (the fetch still runs — only the header display element is removed). Test must fail before edit.
  > Notes: Target is lines 3762–3769 in `index.html`. The `"Assessing…"` pulse span (line 3763) and the score span (lines 3765–3769) are both removed. The header bar retains only `currentIdeaName` (line 3761). **Do not touch** the readiness polling logic, the readiness state variables, or the readiness display in the PRD panel itself (line 4454 onward) — only the header-bar display is removed. **Prerequisite**: UI-8 must be live so the score remains discoverable via the hover card. Do not commit this phase until UI-8 is confirmed working in a browser.

---

### Post-M1 — Assistant thread polish

*Depends on M1 chat bubble work. Frontend-only `ui/index.html` + tests.*

---

- [x] `UI-10` | LOW | Assistant thread: unified `rounded-xl` user/assistant bubbles; assistant reply card (prose first, collapsible neutral assumptions); `QuestionFlow` as separate sibling card; remove cyan left accent on assistant prose; shared outer shell for default/pending/error prose
  > Test: Update `tests/test_ui_chat_bubbles.py` for uniform radius + no cyan border; add `tests/test_ui_assistant_assumptions_shell.py` for disclosure copy and neutral expanded panel. Assert `QuestionFlow` root uses `rounded-xl` in `index.html`.
  > Notes: `AssistantAssumptionsDisclosure` with `useState` per message; no “tap to review” helper. Sender identity via color, not asymmetric corners.

---

## 4) Reference: Deferred Items

These were evaluated and explicitly excluded from MVP scope.

| Item | Reason Deferred |
|------|----------------|
| Styled nav tooltips (custom CSS) | Native `title` attribute tooltips already fire on hover in collapsed state — functional need is covered. Cosmetic upgrade deferred post-MVP. |

---

## 5) Appendix: Project Metadata

```
Project:        Chat UI Enhancement — AutoDev UI
Created:        2026-04-24
Scope:          ui/index.html (frontend), ui/server.py (backend M3 only)
Models:         qwen3-coder-next (execute) + claude-sonnet-4-5 (plan + review)
Repository:     /home/pi/projects/autodev-ui
Test runner:    pytest tests/ -q (from repo root, with source .env)
Phase count:    10 (UI-1 through UI-10)
```

---

## 6) Appendix: Glossary

- **Chats rail**: The second sidebar panel (right of the nav rail) listing project ideas/chats.
- **Nav rail**: The leftmost sidebar panel containing Pipeline Monitor, Project Queue, Setup & Preflight, Project Ideas navigation items.
- **Hover card**: The `React.createPortal`-rendered floating card that appears to the right of the sidebar on project row hover.
- **Readiness score**: Integer 0–10 produced by the PRD readiness assessment agent. Stored per-idea in `readiness.json`.
- **Side-divider-btn**: The existing floating pill collapse toggle on the border edge — replaced by the unified chevron in UI-4.
- **Accent color**: `#00b4d8` — the canonical cyan used for active states, borders, and interactive highlights throughout the UI.
