#!/usr/bin/env bash
# install.sh — AutoDev pipeline setup script
# Usage: ./install.sh [--force]
set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && tput colors >/dev/null 2>&1 \
   && [ "$(tput colors)" -ge 8 ]; then
    GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3)
    RED=$(tput setaf 1);   BOLD=$(tput bold); RESET=$(tput sgr0)
else
    GREEN=''; YELLOW=''; RED=''; BOLD=''; RESET=''
fi

WARNINGS=()
ok()   { echo "${GREEN}  ✓${RESET} $*"; }
warn() { echo "${YELLOW}  ⚠${RESET} $*"; WARNINGS+=("$*"); }
fail() { echo "${RED}  ✗ FATAL:${RESET} $*" >&2; exit 1; }
info() { echo "  · $*"; }
hdr()  { echo; echo "${BOLD}$*${RESET}"; }

FORCE=0
for arg in "$@"; do
    [ "$arg" = "--force" ] && FORCE=1
done

# ─────────────────────────────────────────────────────────────────────────────
# 1. PREFLIGHT CHECKS
# ─────────────────────────────────────────────────────────────────────────────
hdr "1/10  Preflight checks"

PYTHON=""
PYTHON_VERSION=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver=$("$candidate" -c \
            'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON="$candidate"
            PYTHON_VERSION="$ver"
            ok "Python $ver found ($candidate)"
            break
        fi
    fi
done
[ -z "$PYTHON" ] && \
    fail "Python 3.9+ not found. Install Python 3.9 or later and re-run."

if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
    fail "pip not available for $PYTHON. Install pip and re-run."
fi
ok "pip available"

if ! command -v git >/dev/null 2>&1; then
    fail "git not found. Install git and re-run."
fi
ok "git available"

OS_TYPE=$(uname -s)
if [ "$OS_TYPE" = "Linux" ]; then
    ok "OS: Linux"
elif [ "$FORCE" -eq 1 ]; then
    warn "OS is $OS_TYPE, not Linux. fcntl-based locking will not function. Continuing due to --force."
else
    echo "${RED}  ✗${RESET} OS is $OS_TYPE, not Linux."
    echo "    AutoDev uses fcntl advisory locking which is Linux-only."
    echo "    The pipeline lock and orchestrator will not function correctly on this platform."
    echo "    Re-run with --force to proceed anyway (e.g. for UI-only development)."
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. DERIVE PATHS
# ─────────────────────────────────────────────────────────────────────────────
hdr "2/10  Deriving paths"

AUTODEV_REPO_PATH="$(cd "$(dirname "$0")" && pwd)"
AUTODEV_ROOT="${AUTODEV_ROOT:-$HOME/.openclaw}"
export AUTODEV_REPO_PATH AUTODEV_ROOT

ok "AUTODEV_REPO_PATH = $AUTODEV_REPO_PATH"
ok "AUTODEV_ROOT      = $AUTODEV_ROOT"

# ─────────────────────────────────────────────────────────────────────────────
# 3. OPENCLAW VALIDATION
# ─────────────────────────────────────────────────────────────────────────────
hdr "3/10  OpenClaw validation"

OC_FOUND="no"
if [ -d "$AUTODEV_ROOT" ]; then
    ok "$AUTODEV_ROOT exists"
    OC_FOUND="yes"
else
    warn "$AUTODEV_ROOT not found — OpenClaw may not be installed"
fi

if [ -f "$AUTODEV_ROOT/orchestrator.py" ]; then
    warn "Pre-migration orchestrator still present: $AUTODEV_ROOT/orchestrator.py"
    warn "  This file is superseded by $AUTODEV_REPO_PATH/autodev/pipeline/orchestrator.py"
    warn "  Remove it: rm $AUTODEV_ROOT/orchestrator.py"
else
    ok "No stale orchestrator.py in \$AUTODEV_ROOT"
fi

if [ -f "$AUTODEV_ROOT/openclaw.json" ]; then
    ok "$AUTODEV_ROOT/openclaw.json exists"
else
    warn "$AUTODEV_ROOT/openclaw.json not found — OpenClaw gateway configuration missing"
fi

if [ -f "$AUTODEV_ROOT/pipeline.lock" ]; then
    warn "$AUTODEV_ROOT/pipeline.lock exists — a pipeline may already be running"
    warn "  If no pipeline is active, remove it: rm $AUTODEV_ROOT/pipeline.lock"
else
    ok "No stale pipeline.lock"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. PYTHON DEPENDENCIES
# ─────────────────────────────────────────────────────────────────────────────
hdr "4/10  Installing Python dependencies"

REQUIREMENTS="$AUTODEV_REPO_PATH/ui/requirements.txt"
[ -f "$REQUIREMENTS" ] || fail "ui/requirements.txt not found at $REQUIREMENTS"

PIP_INSTALLED="no"
if "$PYTHON" -m pip install -r "$REQUIREMENTS"; then
    ok "pip install succeeded"
    PIP_INSTALLED="yes"
else
    PIP_EXIT=$?
    echo "${RED}  ✗${RESET} pip install failed (exit $PIP_EXIT). See output above." >&2
    exit "$PIP_EXIT"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. DEPLOY AGENT WORKSPACE FILES
# ─────────────────────────────────────────────────────────────────────────────
hdr "5/10  Deploying agent workspace files"

TOTAL_DEPLOYED=0
declare -A AGENT_COUNTS

for agent in planner executor reviewer escalation prd-creator; do
    src_dir="$AUTODEV_REPO_PATH/autodev/agents/$agent"
    dst_dir="$AUTODEV_ROOT/workspace-$agent"
    count=0

    if [ ! -d "$src_dir" ]; then
        warn "Source not found: $src_dir — skipping $agent"
        AGENT_COUNTS[$agent]=0
        continue
    fi

    if [ ! -d "$dst_dir" ]; then
        warn "Workspace not found: $dst_dir — skipping $agent (OpenClaw registers these)"
        AGENT_COUNTS[$agent]=0
        continue
    fi

    for doc in IDENTITY.md SOUL.md TOOLS.md AGENTS.md USER.md; do
        src="$src_dir/$doc"
        [ -f "$src" ] || continue
        cp -u "$src" "$dst_dir/$doc"
        count=$((count + 1))
    done

    case "$agent" in planner|executor|reviewer)
        src="$src_dir/HEARTBEAT.md"
        if [ -f "$src" ]; then
            cp -u "$src" "$dst_dir/HEARTBEAT.md"
            count=$((count + 1))
        fi
        ;;
    esac

    AGENT_COUNTS[$agent]=$count
    TOTAL_DEPLOYED=$((TOTAL_DEPLOYED + count))
    ok "$agent: $count file(s) deployed → $dst_dir"
done

info "Total agent files deployed: $TOTAL_DEPLOYED"

# ─────────────────────────────────────────────────────────────────────────────
# 6. VALIDATE CONVERSION PROMPT
# ─────────────────────────────────────────────────────────────────────────────
hdr "6/10  Validating conversion prompt"

PROMPT_DIR="$AUTODEV_ROOT/deployment-package/Updates"
PROMPT_FOUND="no"

if [ -d "$PROMPT_DIR" ]; then
    PROMPT_FILE=$(find "$PROMPT_DIR" -maxdepth 1 \
        -name "PRD to Roadmap*.txt" 2>/dev/null | head -1)
    if [ -n "$PROMPT_FILE" ]; then
        ok "Conversion prompt found: $(basename "$PROMPT_FILE")"
        PROMPT_FOUND="yes"
    else
        warn "No 'PRD to Roadmap*.txt' found in $PROMPT_DIR"
        warn "  /api/ideas/{id}/convert will return 500 until this file exists"
        warn "  Expected pattern: $PROMPT_DIR/PRD to Roadmap*.txt"
    fi
else
    warn "Conversion prompt directory not found: $PROMPT_DIR"
    warn "  /api/ideas/{id}/convert will return 500 until this file exists"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 7. VALIDATE EXEC-APPROVALS
# ─────────────────────────────────────────────────────────────────────────────
hdr "7/10  Validating exec-approvals"

EXEC_APPROVALS="$AUTODEV_ROOT/exec-approvals.json"
APPROVALS_STATUS="missing"

if [ ! -f "$EXEC_APPROVALS" ]; then
    warn "exec-approvals.json not found at $EXEC_APPROVALS"
    warn "  Gate scripts will not execute until approved via the OpenClaw UI"
else
    NEW_GATE_DIR="$AUTODEV_REPO_PATH/autodev/pipeline/gate_scripts"
    STALE=$(grep -o '"[^"]*gate_scripts[^"]*"' "$EXEC_APPROVALS" 2>/dev/null \
        | grep -v "$AUTODEV_REPO_PATH" || true)

    if [ -n "$STALE" ]; then
        warn "Stale gate_scripts paths in $EXEC_APPROVALS:"
        while IFS= read -r line; do
            warn "    $line"
        done <<< "$STALE"
        warn "  Re-approve gate scripts via the OpenClaw UI"
        warn "  Correct paths are under: $NEW_GATE_DIR/"
        APPROVALS_STATUS="stale entries found"
    else
        ok "exec-approvals.json paths look current"
        APPROVALS_STATUS="ok"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 8. UPDATE CRON/JOBS.JSON HEARTBEAT PATH
# ─────────────────────────────────────────────────────────────────────────────
hdr "8/10  Updating cron/jobs.json heartbeat path"

CRON_FILE="$AUTODEV_ROOT/cron/jobs.json"
CRON_STATUS="not found"

if [ ! -f "$CRON_FILE" ]; then
    warn "cron/jobs.json not found at $CRON_FILE — skipping"
else
    OLD_CRON_SCRIPT="$AUTODEV_ROOT/heartbeat_cron.py"
    NEW_CRON_SCRIPT="$AUTODEV_REPO_PATH/autodev/pipeline/heartbeat_cron.py"

    CRON_RESULT=$("$PYTHON" - \
        "$CRON_FILE" "$OLD_CRON_SCRIPT" "$NEW_CRON_SCRIPT" <<'PYEOF'
import json, os, sys, tempfile

cron_file  = sys.argv[1]
old_script = sys.argv[2]
new_script = sys.argv[3]

with open(cron_file) as f:
    raw = f.read()

if old_script not in raw:
    print("already_correct" if new_script in raw else "no_match")
    sys.exit(0)

updated = raw.replace(old_script, new_script)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(cron_file), prefix="jobs_")
with os.fdopen(fd, "w") as f:
    f.write(updated)
os.replace(tmp, cron_file)
print("updated")
PYEOF
    )

    case "$CRON_RESULT" in
        updated)
            ok "cron/jobs.json updated — heartbeat_cron.py path corrected"
            info "  Was: $OLD_CRON_SCRIPT"
            info "  Now: $NEW_CRON_SCRIPT"
            CRON_STATUS="updated"
            ;;
        already_correct)
            ok "cron/jobs.json heartbeat path already points to the new location"
            CRON_STATUS="already correct"
            ;;
        no_match)
            info "cron/jobs.json: no heartbeat_cron.py entry found — no update needed"
            CRON_STATUS="no entry found"
            ;;
        *)
            warn "cron/jobs.json update returned unexpected result: '$CRON_RESULT'"
            CRON_STATUS="update uncertain"
            ;;
    esac
fi

# ─────────────────────────────────────────────────────────────────────────────
# 9. WRITE .env FILE
# ─────────────────────────────────────────────────────────────────────────────
hdr "9/10  Writing .env"

ENV_FILE="$AUTODEV_REPO_PATH/.env"
if [ -f "$ENV_FILE" ]; then
    info ".env already exists at $ENV_FILE — not overwriting"
    info "  To regenerate, remove it and re-run install.sh"
else
    cat > "$ENV_FILE" <<EOF
AUTODEV_ROOT=$HOME/.openclaw
AUTODEV_REPO_PATH=$AUTODEV_REPO_PATH
EOF
    ok ".env written to $ENV_FILE"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 10. FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
hdr "10/10  Summary"
echo
printf "  %-28s %s\n" "Python version:"        "$PYTHON_VERSION"
printf "  %-28s %s\n" "Pip packages installed:" "$PIP_INSTALLED"
printf "  %-28s %s\n" "OpenClaw root found:"    "$OC_FOUND"
printf "  %-28s %s\n" "Conversion prompt:"      "$PROMPT_FOUND"
printf "  %-28s %s\n" "Exec-approvals:"         "$APPROVALS_STATUS"
printf "  %-28s %s\n" "Cron path:"              "$CRON_STATUS"
echo   "  Agent files deployed:"
for agent in planner executor reviewer escalation prd-creator; do
    printf "    %-20s %s\n" "$agent:" "${AGENT_COUNTS[$agent]:-0}"
done

if [ "${#WARNINGS[@]}" -gt 0 ]; then
    echo
    echo "${YELLOW}${BOLD}  Warnings requiring manual action:${RESET}"
    for w in "${WARNINGS[@]}"; do
        echo "  ${YELLOW}⚠${RESET} $w"
    done
    echo
    echo "${YELLOW}${BOLD}⚠ Setup complete with warnings. Review items above before starting.${RESET}"
else
    echo
    echo "${GREEN}${BOLD}✓ Setup complete.${RESET}"
fi

echo
echo "  Start with: uvicorn ui.server:app --host 0.0.0.0 --port 18790"
echo
