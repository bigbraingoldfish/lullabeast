# Task 01 — Interactive Installer & First-Run Experience

*AutoDev MVP pre-tester work. Claude Code should read this document in full before planning.*

---

## Background

AutoDev currently ships with a static `install.sh` and a `SETUP.md` that requires
semi-technical users to manually edit config files, validate prerequisites, and
register gate scripts in OpenClaw before the app will work. This is too much
friction for testers who are technical but not CLI-comfortable.

This task replaces that experience with an interactive CLI installer that guides
the user through setup step by step, detects what it can automatically, prompts
for what it cannot, and validates everything before the server starts. It also adds
a first-run detection in the UI so that a user who skips the installer lands on a
clear "run this first" screen instead of a broken preflight.

The target user: comfortable reading terminal output and running commands when
told exactly what to run, but not comfortable debugging a failed pip install or
editing YAML by hand.

---

## Scope (today)

### In scope
- Interactive `install.sh` rewrite (see spec below)
- First-run detection in `ui/server.py` + a first-run screen in `ui/index.html`
- TDD with mocked dependencies (see testing requirements)
- All changes additive only — nothing destructive to existing OpenClaw config

### Explicitly out of scope
- Automatic OpenClaw installation or version upgrade
- Fully in-browser guided wizard
- Network calls to download dependencies
- Signal/messaging configuration (separate task — see known follow-up note below)

---

## Known Follow-Up (do not implement today)

The escalation agent guidance is currently Signal-specific. It should be updated
to reference whatever messaging integration is enabled in the user's OpenClaw
config rather than hardcoding Signal. This is out of scope today because the
current setup uses a Docker/Signal configuration on a specific port that would
be broken by a generalised guidance change. Flag this in CLAUDE.md as a pending
task with this context preserved.

---

## Installer Spec — `install.sh`

### Interaction model
Homebrew-style terminal output. Each step prints clearly:
- What it is about to check or do
- The result (✓ pass, ✗ fail, ⚠ warning)
- On failure: exact command to run to fix it, then a prompt to re-check or exit

The script must be re-runnable. Running it a second time on an already-configured
system should produce all green checkmarks and exit cleanly without touching
anything.

### Step sequence

**Step 1 — OS check**
- Detect Linux vs macOS vs Windows
- Linux: proceed
- macOS: warn that fcntl locking is not supported, pipeline liveness detection
  will not work correctly. Prompt: "Continue anyway? [y/N]"
- Windows: hard stop with message explaining Linux requirement

**Step 2 — Python version**
- Require Python 3.9+
- On fail: print exact pyenv or system package manager command for their OS,
  hard stop

**Step 3 — pip dependencies**
- Run `pip install -r ui/requirements.txt --dry-run` first to preview
- Print the packages that will be installed
- Prompt: "Install these packages? [Y/n]"
- On confirm: run actual install, capture and display any errors
- On fail: print error, hard stop

**Step 4 — OpenClaw detection**
- Check common paths in order:
  1. `$OPENCLAW_ROOT` env var if set
  2. `~/.openclaw/`
  3. Prompt user: "OpenClaw not found at default path. Enter your OpenClaw
     directory path:"
- Validate the path contains `openclaw.json`
- If not found after prompt: print OpenClaw install instructions, hard stop
- On success: write detected path to `ui/config.json` as `autodev_repo_path`
  and export as `AUTODEV_ROOT` for remainder of script

**Step 5 — OpenClaw version check**
- Read version from `openclaw.json` if present
- If version field missing or below recommended: print warning with recommended
  version and update instructions, do not hard stop
- If version meets requirement: ✓

**Step 6 — Agent workspace provisioning**
- For each of the five agents (planner, executor, reviewer, escalation,
  prd-creator):
  - Check if `~/.openclaw/workspace-{agent}/` exists
  - Check if identity files exist (IDENTITY.md, SOUL.md, TOOLS.md, AGENTS.md,
    USER.md)
  - Check HEARTBEAT.md for planner, executor, reviewer
- If any files are missing, print a summary:
  "The following agent workspace files are missing and will be created:
   [list of files]"
- Prompt: "Set up missing agent workspace files? [Y/n]"
- On confirm: copy from `autodev/agents/{agent}/` to
  `~/.openclaw/workspace-{agent}/` using `cp -u` (additive, never overwrites
  newer files)
- Never touch `.openclaw/workspace-state.json` or any OpenClaw-managed files

**Step 7 — Exec-approvals validation**
- Read `~/.openclaw/exec-approvals.json`
- Check each gate script entry — does the path point to the current
  `autodev/pipeline/gate_scripts/` location?
- If stale paths found: print each stale entry and instruct user to
  re-approve via OpenClaw UI. Do not attempt to auto-fix exec-approvals.
- If file missing: warn that gate scripts will not run until approved

**Step 8 — Cron path update**
- Read `~/.openclaw/cron/jobs.json`
- Find heartbeat_cron.py entry
- If path points to old location: update to current
  `autodev/pipeline/heartbeat_cron.py` path (this is the one auto-fix
  the script performs — it is safe and additive)
- Confirm update to user before writing

**Step 9 — Register roadmap-converter agent in openclaw.json**
- Read `~/.openclaw/openclaw.json`
- Check if an agent entry with `id: "roadmap-converter"` already exists
- If it already exists: ✓ skip, print "roadmap-converter already registered"
- If missing: this is the one intentional write to openclaw.json in the
  entire installer. Add a new agent entry using the same model config as
  prd-creator (copy model.primary, model.fallback values from the
  prd-creator entry). Set workspace to `~/.openclaw/workspace-roadmap-converter/`.
  Use `mkstemp` + `os.replace` for atomic write — never write directly to
  openclaw.json.
- Print summary of what was added before writing
- Prompt: "Register roadmap-converter agent in openclaw.json? [Y/n]"
- On confirm: write atomically
- On decline: warn that alignment check and adversarial check will not
  function until this agent is registered. Do not hard stop.
- This is the ONLY permitted write to openclaw.json. All other keys and
  entries must be preserved exactly.

**Step 10 — Conversion prompt validation**
- Check that the PRD-to-roadmap prompt file exists at configured path
- If missing: print warning with exact expected path, explain which feature
  breaks without it, do not hard stop

**Step 11 — Write .env**
- If `.env` does not exist: write it with AUTODEV_ROOT and AUTODEV_REPO_PATH
- If `.env` exists: print "Found existing .env — not overwritten" and skip

**Step 12 — Mark setup complete**
- Write `~/.autodev_setup_complete` sentinel file with timestamp
- This is what the UI uses for first-run detection

**Step 13 — Summary**
Print a summary block showing pass/warn/fail for each step.
Collect all warnings into a "Manual steps required" section at the end.
End with either:
- "✓ Setup complete. Start AutoDev with: uvicorn ui.server:app --host 0.0.0.0 --port 18790"
- "⚠ Setup complete with warnings. Review manual steps above before starting."

---

## First-Run Detection — `ui/server.py` + `ui/index.html`

### server.py
- On startup, check for `~/.autodev_setup_complete` sentinel file
- Expose as a field in `GET /api/state`: `"setup_complete": true/false`
- Add `GET /api/setup/status` endpoint that returns:
  - `setup_complete`: bool
  - `missing_items`: list of what's not configured (config paths, missing
    workspaces, etc.) — same checks as install.sh steps 4–9, read-only

### ui/index.html
- On app load, if `setup_complete` is false, show a FirstRunScreen instead
  of routing to the normal app
- FirstRunScreen content:
  - Friendly message: "Welcome to AutoDev — let's get you set up"
  - A checklist showing which items are configured and which aren't
    (populated from `/api/setup/status`)
  - A single prominent instruction: the exact command to run install.sh
  - A "I've run the installer — check again" button that re-polls
    `/api/setup/status`
  - Once all items pass: "Setup complete — continue to AutoDev →" button
    that routes to the normal app and never shows this screen again

---

## Testing Requirements

**Philosophy**: TDD. Write tests first, implement to pass them.
Mock all external dependencies — no actual filesystem writes to `~/.openclaw/`,
no real pip installs, no real OpenClaw config reads.

### What to mock
- Filesystem operations (`os.path.exists`, `open`, `shutil.copy`, file writes)
- `subprocess` calls (pip install, git)
- `~/.openclaw/` directory structure — use a temp directory fixture
- `~/.autodev_setup_complete` sentinel file

### Test coverage required

**install.sh logic** (test via a Python test harness or bash test framework):
- Each step passes when prerequisites are met
- Each step fails correctly when prerequisites are missing
- Re-run on configured system produces all-pass with no writes
- Additive-only: existing files are not overwritten (cp -u behavior)
- Stale exec-approvals are detected and reported but not auto-fixed
- Cron path update writes correctly and only when stale
- openclaw.json agent registration: adds entry when missing, skips when
  present, preserves all existing keys and entries, uses atomic write,
  skips gracefully on user decline

**server.py endpoints**:
- `GET /api/state` includes `setup_complete` field
- `GET /api/setup/status` returns correct missing items when sentinel absent
- `GET /api/setup/status` returns all-clear when sentinel present and config valid

**ui/index.html**:
- FirstRunScreen renders when `setup_complete` is false
- Normal app renders when `setup_complete` is true
- "Check again" button re-polls and updates checklist
- Transition to normal app on all-pass

---

## Implementation Constraints

- Additive only — never overwrite files newer than source (`cp -u`)
- `openclaw.json` may only be written to add the `roadmap-converter` agent
  entry. All other keys and agent entries must be preserved exactly.
  Use atomic write (mkstemp + os.replace). This is the only permitted
  exception to the read-only rule for OpenClaw config.
- Never touch `exec-approvals.json` — report only
- Never touch `workspace-state.json` or any `.pi` files
- install.sh must be re-runnable safely
- All path construction must use `AUTODEV_ROOT` and `AUTODEV_REPO_PATH` constants
- No hardcoded usernames or home directory paths

---

## Claude Code Instructions

**Before any changes:**
```
git add -A && git commit -m "pre-installer: checkpoint"
```
Confirm hash before proceeding.

**Process:**
1. Planning phase first — output a detailed plan covering all components
   (install.sh, server.py endpoints, FirstRunScreen, test suite).
   Wait for approval before writing any code.
2. Write tests first (TDD). All tests should fail initially.
3. Implement to pass tests.
4. Manual verification checklist:
   - Run install.sh on a clean config — all steps execute correctly
   - Run install.sh again — all green, no writes
   - Start server without sentinel — FirstRunScreen appears
   - Run installer, restart server — normal app appears
   - All new tests pass: `pytest tests/ -q`
5. After verification:
```
git add -A
git commit -m "installer: interactive setup wizard, first-run detection, TDD"
git push origin main
```
