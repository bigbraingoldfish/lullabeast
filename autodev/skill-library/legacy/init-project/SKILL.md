---
name: init-project
description: >
  Initializes a new project or connects an existing repo for use with
  the autonomous-dev pipeline. Creates required directory structure,
  validates roadmap format, stores PRD, sets up git with optional remote.
metadata:
  openclaw:
    emoji: "🏗️"
    requires:
      bins: [git, jq, python3]
---

# Project Initialization Skill

You initialize projects for the autonomous-dev pipeline. You create the
required file structure, validate inputs, and ensure everything is ready
for the pipeline to begin working.

You need TWO inputs from the user:
1. **Project name** — used for directory name and pipeline.json
2. **Roadmap** — either a file path or content for roadmap.md

You accept TWO optional inputs:
3. **PRD** — file path or content for the project's PRD (stored as prd.md)
4. **Git remote URL** — for pushing to a remote repository

---

## Mode Detection

Ask the user or infer from context:

**Mode A — New Project:**
User provides a project name and wants a fresh project created.
The project directory does NOT exist yet.

**Mode B — Connect Existing Project:**
User provides a path to an existing git repo.
The project directory ALREADY exists with source code.

---

## Mode A: New Project

### Step 1: Create Project Structure

```bash
PROJECT_NAME="{project_name}"
PROJECT_DIR="/home/pi/projects/${PROJECT_NAME}"

mkdir -p ${PROJECT_DIR}/{phases,tests,src/${PROJECT_NAME}}
touch ${PROJECT_DIR}/src/${PROJECT_NAME}/__init__.py
touch ${PROJECT_DIR}/metrics.jsonl
```

### Step 2: Write Pipeline State

```bash
cat > ${PROJECT_DIR}/pipeline.json << EOF
{
  "project": "${PROJECT_NAME}",
  "created": "$(date -Iseconds)",
  "current_phase": null,
  "current_plan": null,
  "phase_start_time": null,
  "completed_count": 0,
  "status": "idle"
}
EOF
```

### Step 3: Write Roadmap

If the user provided roadmap content directly, write it to
`${PROJECT_DIR}/roadmap.md`.

If the user provided a file path, copy it:
```bash
cp {roadmap_path} ${PROJECT_DIR}/roadmap.md
```

### Step 4: Validate Roadmap Format

Run these checks on `${PROJECT_DIR}/roadmap.md`:

```bash
# At least one phase exists
grep -c '^\- \[.\] `' ${PROJECT_DIR}/roadmap.md
# Must return > 0

# Every phase has the correct format: - [ ] `ID` | RISK | Goal
grep '^\- \[.\] `' ${PROJECT_DIR}/roadmap.md | while read -r line; do
  echo "$line" | grep -qP '^\- \[.\] `[A-Z]+-[A-Z]\d+` \| (LOW|HIGH) \| .+' || echo "MALFORMED: $line"
done

# Every phase has a Test line
PHASE_COUNT=$(grep -c '^\- \[.\] `' ${PROJECT_DIR}/roadmap.md)
TEST_COUNT=$(grep -c '^\s*> Test:' ${PROJECT_DIR}/roadmap.md)
if [ "$PHASE_COUNT" -ne "$TEST_COUNT" ]; then
  echo "WARNING: ${PHASE_COUNT} phases but ${TEST_COUNT} test lines. Every phase needs a > Test: line."
fi

# Phase IDs are unique
grep -oP '`[A-Z]+-[A-Z]\d+`' ${PROJECT_DIR}/roadmap.md | sort | uniq -d
# Should return nothing (no duplicates)
```

If validation fails, report EXACTLY which lines are malformed and what
the expected format is:
```
Expected: - [ ] `PHASE-ID` | LOW or HIGH | Goal text
  > Test: What must be verifiable
  > Notes: Optional clarifications

Your line: {the malformed line}
Problem: {what's wrong — missing backticks, wrong risk value, no pipe delimiter, etc.}
```

Do NOT silently fix the roadmap. The roadmap is user input. Report
problems and wait for the user to correct them.

### Step 5: Store PRD (If Provided)

If the user provided PRD content or a file path:

```bash
# If content provided directly, write it
cat > ${PROJECT_DIR}/prd.md << 'EOF'
{prd_content}
EOF

# If file path provided, copy it
cp {prd_path} ${PROJECT_DIR}/prd.md
```

If no PRD provided, create a placeholder:
```bash
cat > ${PROJECT_DIR}/prd.md << 'EOF'
# PRD — {project_name}

> No PRD provided at project initialization.
> Add the PRD content here for alignment checks during planning and review.
EOF
```

### Step 6: Create Supporting Files

```bash
# Lessons file
cat > ${PROJECT_DIR}/lessons.md << 'EOF'
# Lessons Learned

_One-line entries added by the pipeline after each phase._
EOF

# Python project config
cat > ${PROJECT_DIR}/pyproject.toml << EOF
[project]
name = "${PROJECT_NAME}"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
target-version = "py311"
line-length = 100
EOF

# Gitignore (including pipeline metadata — orchestrator-managed per-turn state)
cat > ${PROJECT_DIR}/.gitignore << 'EOF'
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
dist/
build/
.venv/
.ruff_cache/
diagnosis.md
review.md

# Pipeline metadata — orchestrator-managed per-turn state, never committed
*.done
phase_state.json
planner_output.json
executor_output.json
reviewer_output.json
escalation_output.json
current_phase.json
EOF
```

### Step 7: Initialize Git

```bash
cd ${PROJECT_DIR}
git init
git checkout -b main
git add -A
git commit -m "init: project structure with roadmap"
```

### Step 8: Configure Remote (If Provided)

If the user provided a git remote URL:

```bash
cd ${PROJECT_DIR}
git remote add origin {remote_url}
git push -u origin main 2>/dev/null || echo "WARNING: git push failed. Check remote URL and auth. Work continues locally."
```

If no remote URL provided:
```
NOTE: No git remote configured. The autonomous-dev pipeline will work
locally. Pushes will produce non-blocking warnings.

To add a remote later:
  git -C ${PROJECT_DIR} remote add origin <url>
  git -C ${PROJECT_DIR} push -u origin main
```

### Step 8b: Create Pipeline Symlink

After git commit, create a symlink so the project is accessible via the standard path:

```bash
ln -sfn ${PROJECT_DIR} ~/.openclaw/pipeline-project
```

### Step 8c: Workspace-Docs Check

Verify all 4 agent workspace directories contain their required support files.
WARN for each missing file, but never fail the skill execution.
These files are operator responsibility to install:

```bash
echo "=== Workspace-Docs Check ==="
for AGENT in planner executor reviewer escalation; do
  for DOC in AGENTS.md TOOLS.md SOUL.md USER.md IDENTITY.md; do
    DOC_PATH="/home/pi/.openclaw/workspace-${AGENT}/${DOC}"
    if [ ! -f "${DOC_PATH}" ]; then
      echo "[WARN] workspace-${AGENT}/${DOC} missing — operator must install this file"
    fi
  done
done
```

### Step 9: Final Verification

```bash
echo "=== Project Structure ==="
find ${PROJECT_DIR} -maxdepth 2 -not -path '*/.git/*' | sort

echo ""
echo "=== Roadmap Phases ==="
grep '^\- \[.\] `' ${PROJECT_DIR}/roadmap.md | wc -l
echo "phases found"

echo ""
echo "=== Pipeline State ==="
cat ${PROJECT_DIR}/pipeline.json | jq '{project, status, completed_count}'

echo ""
echo "=== Git Status ==="
git -C ${PROJECT_DIR} log --oneline
git -C ${PROJECT_DIR} remote -v 2>/dev/null || echo "No remote configured"

echo ""
echo "=== PRD ==="
if [ -s ${PROJECT_DIR}/prd.md ]; then
  head -3 ${PROJECT_DIR}/prd.md
else
  echo "No PRD"
fi
```

Report to user:
```
Project "{project_name}" initialized at ${PROJECT_DIR}

  Phases: {count} (all unchecked)
  PRD: {present / placeholder}
  Git: initialized on main, {remote configured / no remote}
  Status: idle — ready for autonomous-dev pipeline

To start development:
  Use the autonomous-dev skill and point it at ${PROJECT_DIR}
```

**Append to lessons.md:**
```bash
echo "- INFRA-E1: \`pipeline.json\` (skill-written, project dir) vs \`pipeline_state.json\` (orchestrator-written, \`~/.openclaw/\`) are intentionally different files — never confuse them" >> ${PROJECT_DIR}/lessons.md
```

---

## Mode B: Connect Existing Project

### Step 1: Verify Project Exists

```bash
PROJECT_DIR="{provided_path}"

# Must be a git repo
test -d ${PROJECT_DIR}/.git || echo "ERROR: ${PROJECT_DIR} is not a git repo."

# Must have clean working tree
cd ${PROJECT_DIR} && git status --porcelain
# If not clean, warn but continue
```

### Step 2: Audit Existing Structure

Check what already exists and what needs to be created:

```bash
echo "=== Existing Structure Audit ==="
[ -f ${PROJECT_DIR}/roadmap.md ]    && echo "roadmap.md: EXISTS"    || echo "roadmap.md: MISSING"
[ -f ${PROJECT_DIR}/pipeline.json ] && echo "pipeline.json: EXISTS" || echo "pipeline.json: MISSING"
[ -f ${PROJECT_DIR}/metrics.jsonl ] && echo "metrics.jsonl: EXISTS" || echo "metrics.jsonl: MISSING"
[ -f ${PROJECT_DIR}/lessons.md ]    && echo "lessons.md: EXISTS"    || echo "lessons.md: MISSING"
[ -d ${PROJECT_DIR}/phases ]        && echo "phases/: EXISTS"       || echo "phases/: MISSING"
[ -d ${PROJECT_DIR}/tests ]         && echo "tests/: EXISTS"        || echo "tests/: MISSING"
[ -f ${PROJECT_DIR}/prd.md ]        && echo "prd.md: EXISTS"        || echo "prd.md: MISSING"
```

### Step 3: Create Missing Files Only

For each MISSING item, create it using the same templates from Mode A.
Do NOT overwrite existing files. Do NOT modify existing source code,
git history, or configuration.

**Gitignore handling (append-only, never overwrite):**
```bash
REQUIRED_PIPELINE_ENTRIES="*.done
phase_state.json
planner_output.json
executor_output.json
reviewer_output.json
escalation_output.json
current_phase.json"

if [ -f ${PROJECT_DIR}/.gitignore ]; then
  GITIGNORE_CONTENT=$(cat ${PROJECT_DIR}/.gitignore)
  MISSING_ENTRIES=""
  for ENTRY in ${REQUIRED_PIPELINE_ENTRIES}; do
    if ! echo "${GITIGNORE_CONTENT}" | grep -q "^${ENTRY}$"; then
      MISSING_ENTRIES="${MISSING_ENTRIES}
${ENTRY}"
    fi
  done
  if [ -n "${MISSING_ENTRIES}" ]; then
    echo "" >> ${PROJECT_DIR}/.gitignore
    echo "# Pipeline metadata — orchestrator-managed per-turn state, never committed" >> ${PROJECT_DIR}/.gitignore
    echo "${MISSING_ENTRIES}" >> ${PROJECT_DIR}/.gitignore
    echo "NOTE: Appended missing pipeline entries to existing .gitignore"
  fi
else
  # No existing .gitignore — create one with standard entries plus pipeline entries
  cat > ${PROJECT_DIR}/.gitignore << 'EOF'
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
dist/
build/
.venv/
.ruff_cache/
diagnosis.md
review.md

# Pipeline metadata — orchestrator-managed per-turn state, never committed
*.done
phase_state.json
planner_output.json
executor_output.json
reviewer_output.json
escalation_output.json
current_phase.json
EOF
fi
```

### Step 4: Write Roadmap (If Missing)

Same as Mode A Step 3 — write from user-provided content or file path.

### Step 5: Validate Roadmap Format

Same as Mode A Step 4 — run all format checks.

### Step 6: Store PRD (If Provided and Missing)

Same as Mode A Step 5 — write PRD if provided and prd.md doesn't exist.
If prd.md already exists, do NOT overwrite. Report that it exists.

### Step 7: Configure Remote (If Provided and Not Already Set)

```bash
cd ${PROJECT_DIR}
EXISTING_REMOTE=$(git remote get-url origin 2>/dev/null)

if [ -n "$EXISTING_REMOTE" ]; then
  echo "Remote already configured: $EXISTING_REMOTE"
elif [ -n "{remote_url}" ]; then
  git remote add origin {remote_url}
  git push -u origin main 2>/dev/null || echo "WARNING: git push failed."
else
  echo "NOTE: No git remote configured."
fi
```

### Step 8: Commit Pipeline Files

```bash
cd ${PROJECT_DIR}
git add pipeline.json metrics.jsonl lessons.md phases/ prd.md .gitignore 2>/dev/null
git diff --cached --quiet || git commit -m "init: add autonomous-dev pipeline files"
git push origin main 2>/dev/null || true
```

### Step 8b: Create Pipeline Symlink

```bash
ln -sfn ${PROJECT_DIR} ~/.openclaw/pipeline-project
```

### Step 8c: Workspace-Docs Check

Same as Mode A Step 8c — WARN for each missing workspace doc, never fail:

```bash
echo "=== Workspace-Docs Check ==="
for AGENT in planner executor reviewer escalation; do
  for DOC in AGENTS.md TOOLS.md SOUL.md USER.md IDENTITY.md; do
    DOC_PATH="/home/pi/.openclaw/workspace-${AGENT}/${DOC}"
    if [ ! -f "${DOC_PATH}" ]; then
      echo "[WARN] workspace-${AGENT}/${DOC} missing — operator must install this file"
    fi
  done
done
```

### Step 9: Final Verification

Same as Mode A Step 9, plus:

```
Report:
  Files created: {list of newly created files}
  Files existing: {list of files that already existed}
  Files skipped: {list of files that existed and were not modified}
```

**Append to lessons.md (if it exists):**
```bash
if [ -f ${PROJECT_DIR}/lessons.md ]; then
  echo "- INFRA-E1: \`pipeline.json\` (skill-written, project dir) vs \`pipeline_state.json\` (orchestrator-written, \`~/.openclaw/\`) are intentionally different files — never confuse them" >> ${PROJECT_DIR}/lessons.md
fi
```

---

## Rules

- Never overwrite existing files (Mode B)
- Never modify source code in existing projects
- Never silently fix roadmap format — report and wait
- Never create pipeline.json with a non-standard schema
- If roadmap validation fails, do NOT proceed to git init/commit
- The PRD is a reference document — store it, don't parse or modify it
- Git remote is optional — warn if missing, don't block
- Phase IDs must be unique across the entire roadmap
- All file paths must be absolute
