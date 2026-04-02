#!/usr/bin/env bash
# install.sh — AutoDev interactive setup (13 steps)
# Usage: ./install.sh [--force] [--non-interactive]
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
NON_INTERACTIVE=0
for arg in "$@"; do
    [ "$arg" = "--force" ] && FORCE=1
    [ "$arg" = "--non-interactive" ] || [ "$arg" = "--ci" ] && NON_INTERACTIVE=1
done

# Helper: prompt with default answer (skipped in non-interactive mode)
# Usage: prompt_yn "Question" "Y"   → returns 0 for yes, 1 for no
prompt_yn() {
    local question="$1" default="${2:-Y}"
    if [ "$NON_INTERACTIVE" -eq 1 ]; then
        [ "${default}" = "Y" ] && return 0 || return 1
    fi
    read -r -p "  ${question} " ans
    ans="${ans:-$default}"
    case "$ans" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
RECOMMENDED_OC_VERSION="1.2.0"
SETUP_MARKER="$HOME/.autodev_setup_complete"

# ─────────────────────────────────────────────────────────────────────────────
# 1/13  OS CHECK
# ─────────────────────────────────────────────────────────────────────────────
hdr "1/13  OS check"

OS_TYPE=$(uname -s)
OS_STATUS="ok"

if [ "$OS_TYPE" = "Linux" ]; then
    ok "OS: Linux"
elif [ "$OS_TYPE" = "Darwin" ]; then
    echo "${YELLOW}  ⚠${RESET} OS is macOS. fcntl-based pipeline locking is Linux-only."
    echo "    The orchestrator pipeline will not function correctly on macOS."
    echo "    This installation is suitable for UI-only development."
    if [ "$FORCE" -eq 1 ] || [ "$NON_INTERACTIVE" -eq 1 ]; then
        warn "macOS detected — pipeline locking disabled (--force or --non-interactive)"
        OS_STATUS="macOS (warned)"
    else
        if prompt_yn "Continue anyway? [y/N]" "N"; then
            warn "macOS detected — pipeline locking will not function correctly"
            OS_STATUS="macOS (warned)"
        else
            fail "Aborted. Re-run on Linux or with --force to proceed anyway."
        fi
    fi
elif echo "$OS_TYPE" | grep -qiE 'MINGW|CYGWIN|MSYS'; then
    fail "Windows is not supported. AutoDev requires Linux (fcntl locking)."
else
    if [ "$FORCE" -eq 1 ]; then
        warn "Unknown OS: $OS_TYPE — proceeding due to --force"
        OS_STATUS="unknown (forced)"
    else
        fail "Unsupported OS: $OS_TYPE. AutoDev requires Linux."
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2/13  PYTHON VERSION
# ─────────────────────────────────────────────────────────────────────────────
hdr "2/13  Python version"

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

if [ -z "$PYTHON" ]; then
    echo "${RED}  ✗${RESET} Python 3.9+ not found."
    if [ "$OS_TYPE" = "Darwin" ]; then
        echo "    Install via Homebrew:  brew install python@3.11"
        echo "    Or via pyenv:          pyenv install 3.11 && pyenv global 3.11"
    else
        echo "    Install via apt:       sudo apt install python3.11"
        echo "    Or via pyenv:          pyenv install 3.11 && pyenv global 3.11"
    fi
    fail "Python 3.9+ is required. Install it and re-run install.sh."
fi

if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
    fail "pip not available for $PYTHON. Install pip and re-run."
fi
ok "pip available"

if ! command -v git >/dev/null 2>&1; then
    fail "git not found. Install git and re-run."
fi
ok "git available"

# ─────────────────────────────────────────────────────────────────────────────
# 3/13  PYTHON DEPENDENCIES
# ─────────────────────────────────────────────────────────────────────────────
hdr "3/13  Python dependencies"

AUTODEV_REPO_PATH="$(cd "$(dirname "$0")" && pwd)"
REQUIREMENTS="$AUTODEV_REPO_PATH/ui/requirements.txt"
[ -f "$REQUIREMENTS" ] || fail "ui/requirements.txt not found at $REQUIREMENTS"

PIP_INSTALLED="no"

info "Previewing packages to install:"
"$PYTHON" -m pip install --dry-run -r "$REQUIREMENTS" 2>&1 \
    | grep -E "^(Would install|Requirement already)" | head -20 || true

if prompt_yn "Install / confirm these packages? [Y/n]" "Y"; then
    if "$PYTHON" -m pip install -r "$REQUIREMENTS"; then
        ok "pip install succeeded"
        PIP_INSTALLED="yes"
    else
        PIP_EXIT=$?
        echo "${RED}  ✗${RESET} pip install failed (exit $PIP_EXIT). See output above." >&2
        exit "$PIP_EXIT"
    fi
else
    warn "pip install skipped — server may not start without required packages"
    PIP_INSTALLED="skipped"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4/13  OPENCLAW DETECTION
# ─────────────────────────────────────────────────────────────────────────────
hdr "4/13  OpenClaw detection"

AUTODEV_ROOT=""

# Priority 1: $OPENCLAW_ROOT env var
if [ -n "${OPENCLAW_ROOT:-}" ] && [ -d "$OPENCLAW_ROOT" ]; then
    AUTODEV_ROOT="$OPENCLAW_ROOT"
    ok "Using \$OPENCLAW_ROOT: $AUTODEV_ROOT"
fi

# Priority 2: ~/.openclaw default
if [ -z "$AUTODEV_ROOT" ] && [ -d "$HOME/.openclaw" ]; then
    AUTODEV_ROOT="$HOME/.openclaw"
    ok "Found OpenClaw at default path: $AUTODEV_ROOT"
fi

# Priority 3: interactive prompt
if [ -z "$AUTODEV_ROOT" ]; then
    echo "  OpenClaw not found at \$OPENCLAW_ROOT or ~/.openclaw"
    if [ "$NON_INTERACTIVE" -eq 1 ]; then
        fail "OpenClaw not found. Set OPENCLAW_ROOT env var or install OpenClaw at ~/.openclaw."
    fi
    while true; do
        read -r -p "  Enter your OpenClaw directory path: " user_path
        user_path="${user_path/#\~/$HOME}"  # expand leading ~
        if [ -d "$user_path" ] && [ -f "$user_path/openclaw.json" ]; then
            AUTODEV_ROOT="$user_path"
            ok "OpenClaw found at: $AUTODEV_ROOT"
            break
        elif [ -d "$user_path" ]; then
            echo "  ${YELLOW}⚠${RESET} Directory exists but openclaw.json not found inside it."
            echo "    Is OpenClaw installed there? Check the path and try again."
        else
            echo "  ${RED}✗${RESET} Directory not found: $user_path"
            echo "    To install OpenClaw: https://openclaw.dev/install"
            if ! prompt_yn "Try another path? [Y/n]" "Y"; then
                fail "OpenClaw not found. Install OpenClaw and re-run install.sh."
            fi
        fi
    done
fi

if [ ! -f "$AUTODEV_ROOT/openclaw.json" ]; then
    warn "$AUTODEV_ROOT/openclaw.json not found — OpenClaw gateway configuration missing"
fi

if [ -f "$AUTODEV_ROOT/orchestrator.py" ]; then
    warn "Pre-migration orchestrator still present: $AUTODEV_ROOT/orchestrator.py"
    warn "  This file is superseded by $AUTODEV_REPO_PATH/autodev/pipeline/orchestrator.py"
    warn "  Remove it: rm $AUTODEV_ROOT/orchestrator.py"
else
    ok "No stale orchestrator.py in \$AUTODEV_ROOT"
fi

if [ -f "$AUTODEV_ROOT/pipeline.lock" ]; then
    warn "$AUTODEV_ROOT/pipeline.lock exists — a pipeline may already be running"
    warn "  If no pipeline is active, remove it: rm $AUTODEV_ROOT/pipeline.lock"
else
    ok "No stale pipeline.lock"
fi

export AUTODEV_ROOT AUTODEV_REPO_PATH
ok "AUTODEV_REPO_PATH = $AUTODEV_REPO_PATH"
ok "AUTODEV_ROOT      = $AUTODEV_ROOT"

# Write detected path to ui/config.json as autodev_repo_path (atomic)
"$PYTHON" -c "
import json, os, tempfile
p = '$AUTODEV_REPO_PATH/ui/config.json'
cfg = {}
if os.path.exists(p):
    try:
        with open(p) as f:
            cfg = json.load(f)
    except Exception:
        pass
cfg['autodev_repo_path'] = '$AUTODEV_ROOT'
d = os.path.dirname(p)
fd, tmp = tempfile.mkstemp(dir=d, prefix='config_', suffix='.json')
with os.fdopen(fd, 'w') as f:
    json.dump(cfg, f, indent=2)
os.replace(tmp, p)
print('ok')
" > /dev/null && ok "ui/config.json updated with AUTODEV_ROOT" \
  || warn "Could not update ui/config.json — set autodev_repo_path manually"

# ─────────────────────────────────────────────────────────────────────────────
# 5/13  OPENCLAW VERSION CHECK
# ─────────────────────────────────────────────────────────────────────────────
hdr "5/13  OpenClaw version check"

OC_VERSION_STATUS="unknown"
OC_JSON="$AUTODEV_ROOT/openclaw.json"

if [ -f "$OC_JSON" ]; then
    OC_VERSION=$("$PYTHON" -c "
import json, sys
try:
    with open('$OC_JSON') as f:
        d = json.load(f)
    print(d.get('version', ''))
except Exception:
    print('')
" 2>/dev/null)

    if [ -z "$OC_VERSION" ]; then
        warn "openclaw.json does not contain a version field — cannot verify version"
        OC_VERSION_STATUS="missing field"
    else
        # Simple version comparison: split on dot, compare numerically
        IFS='.' read -r rec_maj rec_min rec_pat <<< "$RECOMMENDED_OC_VERSION"
        IFS='.' read -r oc_maj oc_min oc_pat <<< "${OC_VERSION%%[^0-9.]*}"
        oc_maj=${oc_maj:-0}; oc_min=${oc_min:-0}; oc_pat=${oc_pat:-0}

        if [ "$oc_maj" -gt "$rec_maj" ] || \
           ([ "$oc_maj" -eq "$rec_maj" ] && [ "$oc_min" -gt "$rec_min" ]) || \
           ([ "$oc_maj" -eq "$rec_maj" ] && [ "$oc_min" -eq "$rec_min" ] && [ "$oc_pat" -ge "$rec_pat" ]); then
            ok "OpenClaw version $OC_VERSION (recommended ≥ $RECOMMENDED_OC_VERSION)"
            OC_VERSION_STATUS="ok ($OC_VERSION)"
        else
            warn "OpenClaw version $OC_VERSION is below recommended $RECOMMENDED_OC_VERSION"
            warn "  Update OpenClaw for best compatibility with this release"
            OC_VERSION_STATUS="below recommended ($OC_VERSION)"
        fi
    fi
else
    info "Skipping version check (openclaw.json not found)"
    OC_VERSION_STATUS="skipped (no openclaw.json)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6/13  AGENT WORKSPACE PROVISIONING
# ─────────────────────────────────────────────────────────────────────────────
hdr "6/13  Agent workspace provisioning"

TOTAL_DEPLOYED=0
declare -A AGENT_COUNTS
MISSING_FILES=()

for agent in planner executor reviewer escalation prd-creator roadmap-converter; do
    src_dir="$AUTODEV_REPO_PATH/autodev/agents/$agent"
    dst_dir="$AUTODEV_ROOT/workspace-$agent"

    if [ ! -d "$src_dir" ]; then
        warn "Source not found: $src_dir — skipping $agent"
        AGENT_COUNTS[$agent]="skip"
        continue
    fi

    if [ ! -d "$dst_dir" ]; then
        warn "Workspace not found: $dst_dir — skipping $agent (OpenClaw registers these)"
        AGENT_COUNTS[$agent]="skip"
        continue
    fi

    for doc in IDENTITY.md SOUL.md TOOLS.md AGENTS.md USER.md; do
        src="$src_dir/$doc"
        dst="$dst_dir/$doc"
        [ -f "$src" ] || continue
        # cp -u: only copies if source is newer than dest (or dest missing)
        if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
            MISSING_FILES+=("  $agent/$doc")
        fi
    done

    case "$agent" in planner|executor|reviewer)
        src="$src_dir/HEARTBEAT.md"
        dst="$dst_dir/HEARTBEAT.md"
        if [ -f "$src" ] && { [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; }; then
            MISSING_FILES+=("  $agent/HEARTBEAT.md")
        fi
        ;;
    esac

    # Empty skills/ for runtime injection (roadmap-converter is not a pipeline agent)
    if [ "$agent" = "roadmap-converter" ]; then
        mkdir -p "$dst_dir/skills"
    fi
done

if [ "${#MISSING_FILES[@]}" -gt 0 ]; then
    info "The following agent workspace files will be created or updated:"
    for f in "${MISSING_FILES[@]}"; do
        info "$f"
    done
    if prompt_yn "Deploy agent files? [Y/n]" "Y"; then
        for agent in planner executor reviewer escalation prd-creator roadmap-converter; do
            src_dir="$AUTODEV_REPO_PATH/autodev/agents/$agent"
            dst_dir="$AUTODEV_ROOT/workspace-$agent"
            [ -d "$src_dir" ] && [ -d "$dst_dir" ] || continue
            count=0
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
            if [ "$agent" = "roadmap-converter" ]; then
                mkdir -p "$dst_dir/skills"
            fi
            AGENT_COUNTS[$agent]=$count
            TOTAL_DEPLOYED=$((TOTAL_DEPLOYED + count))
            ok "$agent: $count file(s) deployed → $dst_dir"
        done
        info "Total agent files deployed: $TOTAL_DEPLOYED"
    else
        warn "Agent workspace provisioning skipped"
        for agent in planner executor reviewer escalation prd-creator roadmap-converter; do
            AGENT_COUNTS[$agent]="skipped"
        done
    fi
else
    ok "All agent workspace files are current — nothing to deploy"
    for agent in planner executor reviewer escalation prd-creator roadmap-converter; do
        AGENT_COUNTS[$agent]="current"
    done
fi

# ─────────────────────────────────────────────────────────────────────────────
# 7/13  EXEC-APPROVALS VALIDATION
# ─────────────────────────────────────────────────────────────────────────────
hdr "7/13  Exec-approvals validation"

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
# 8/13  CRON/JOBS.JSON HEARTBEAT PATH UPDATE
# ─────────────────────────────────────────────────────────────────────────────
hdr "8/13  Cron/jobs.json heartbeat path"

CRON_FILE="$AUTODEV_ROOT/cron/jobs.json"
CRON_STATUS="not found"

if [ ! -f "$CRON_FILE" ]; then
    warn "cron/jobs.json not found at $CRON_FILE — skipping"
else
    OLD_CRON_SCRIPT="$AUTODEV_ROOT/heartbeat_cron.py"
    NEW_CRON_SCRIPT="$AUTODEV_REPO_PATH/autodev/pipeline/heartbeat_cron.py"

    CRON_NEEDS_UPDATE=$("$PYTHON" -c "
import json, sys
try:
    with open('$CRON_FILE') as f:
        raw = f.read()
    print('yes' if '$OLD_CRON_SCRIPT' in raw else 'no')
except Exception:
    print('no')
")

    if [ "$CRON_NEEDS_UPDATE" = "yes" ]; then
        info "Heartbeat path needs updating:"
        info "  Was: $OLD_CRON_SCRIPT"
        info "  Now: $NEW_CRON_SCRIPT"
        if prompt_yn "Update cron/jobs.json? [Y/n]" "Y"; then
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
                    CRON_STATUS="updated"
                    ;;
                already_correct)
                    ok "cron/jobs.json already has the correct path"
                    CRON_STATUS="already correct"
                    ;;
                *)
                    warn "cron/jobs.json update returned unexpected result: '$CRON_RESULT'"
                    CRON_STATUS="update uncertain"
                    ;;
            esac
        else
            warn "Cron path update skipped — heartbeat watchdog may target old location"
            CRON_STATUS="skipped"
        fi
    else
        # Check if new path already present
        if grep -q "$NEW_CRON_SCRIPT" "$CRON_FILE" 2>/dev/null; then
            ok "cron/jobs.json heartbeat path already points to the new location"
            CRON_STATUS="already correct"
        else
            info "cron/jobs.json: no heartbeat_cron.py entry found — no update needed"
            CRON_STATUS="no entry found"
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 9/13  REGISTER ROADMAP-CONVERTER AGENT
# ─────────────────────────────────────────────────────────────────────────────
hdr "9/13  Register roadmap-converter agent"

REGISTER_STATUS_STEP="not attempted"
REGISTER_AGENT="$AUTODEV_REPO_PATH/autodev/installer/register_agent.py"

if [ ! -f "$REGISTER_AGENT" ]; then
    warn "register_agent.py not found at $REGISTER_AGENT — skipping"
    REGISTER_STATUS_STEP="script missing"
elif [ ! -f "$AUTODEV_ROOT/openclaw.json" ]; then
    warn "openclaw.json not found — cannot register roadmap-converter"
    REGISTER_STATUS_STEP="skipped (no openclaw.json)"
else
    # Run dry-run; it prints JSON block then status token on last line
    REGISTER_DRY_OUTPUT=$("$PYTHON" "$REGISTER_AGENT" \
        "$AUTODEV_ROOT/openclaw.json" "$AUTODEV_ROOT" --dry-run 2>&1)
    REGISTER_DRY_STATUS=$(echo "$REGISTER_DRY_OUTPUT" | tail -1)

    case "$REGISTER_DRY_STATUS" in
        already_registered)
            ok "roadmap-converter already registered in openclaw.json"
            REGISTER_STATUS_STEP="already registered"
            ;;
        dry_run)
            # Print the JSON block (everything except the last line)
            echo "$REGISTER_DRY_OUTPUT" | head -n -1
            info "The above entry would be added to $AUTODEV_ROOT/openclaw.json"
            if prompt_yn "Register roadmap-converter agent? [Y/n]" "Y"; then
                APPLY_RESULT=$("$PYTHON" "$REGISTER_AGENT" \
                    "$AUTODEV_ROOT/openclaw.json" "$AUTODEV_ROOT" --apply 2>&1)
                case "$APPLY_RESULT" in
                    registered)
                        ok "roadmap-converter registered in openclaw.json"
                        REGISTER_STATUS_STEP="registered"
                        ;;
                    already_registered)
                        ok "roadmap-converter already registered (concurrent write?)"
                        REGISTER_STATUS_STEP="already registered"
                        ;;
                    error:*)
                        warn "Registration failed: ${APPLY_RESULT#error:}"
                        REGISTER_STATUS_STEP="error"
                        ;;
                    *)
                        warn "Unexpected result from register_agent.py: $APPLY_RESULT"
                        REGISTER_STATUS_STEP="unexpected result"
                        ;;
                esac
            else
                warn "Skipped — roadmap-converter not registered"
                warn "  Alignment check and adversarial review features will not function"
                warn "  Re-run install.sh and accept the prompt to register later"
                REGISTER_STATUS_STEP="skipped by user"
            fi
            ;;
        missing_prd_creator)
            warn "prd-creator entry not found in openclaw.json — cannot copy model config"
            warn "  Add a prd-creator agent to openclaw.json first, then re-run install.sh"
            REGISTER_STATUS_STEP="missing prd-creator"
            ;;
        error:*)
            warn "Registration error: ${REGISTER_DRY_STATUS#error:}"
            REGISTER_STATUS_STEP="error"
            ;;
        *)
            warn "Unexpected dry-run result: $REGISTER_DRY_STATUS"
            REGISTER_STATUS_STEP="unexpected result"
            ;;
    esac
fi

# ─────────────────────────────────────────────────────────────────────────────
# 10/13  CONVERSION PROMPT VALIDATION
# ─────────────────────────────────────────────────────────────────────────────
hdr "10/13  Conversion prompt validation"

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
# 11/13  WRITE .env
# ─────────────────────────────────────────────────────────────────────────────
hdr "11/13  Writing .env"

ENV_FILE="$AUTODEV_REPO_PATH/.env"
if [ -f "$ENV_FILE" ]; then
    info ".env already exists at $ENV_FILE — not overwriting"
    info "  To regenerate, remove it and re-run install.sh"
else
    cat > "$ENV_FILE" <<EOF
AUTODEV_ROOT=$AUTODEV_ROOT
AUTODEV_REPO_PATH=$AUTODEV_REPO_PATH
EOF
    ok ".env written to $ENV_FILE"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 12/13  MARK SETUP COMPLETE
# ─────────────────────────────────────────────────────────────────────────────
hdr "12/13  Marking setup complete"

date -u +%Y-%m-%dT%H:%M:%SZ > "$SETUP_MARKER"
ok "Setup marker written to $SETUP_MARKER"
info "  The AutoDev UI uses this file for first-run detection"

# ─────────────────────────────────────────────────────────────────────────────
# 13/13  SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
hdr "13/13  Summary"
echo
printf "  %-32s %s\n" "OS:"                      "$OS_TYPE ($OS_STATUS)"
printf "  %-32s %s\n" "Python version:"           "$PYTHON_VERSION"
printf "  %-32s %s\n" "Pip packages installed:"   "$PIP_INSTALLED"
printf "  %-32s %s\n" "AUTODEV_ROOT:"             "$AUTODEV_ROOT"
printf "  %-32s %s\n" "OpenClaw version:"         "$OC_VERSION_STATUS"
printf "  %-32s %s\n" "Conversion prompt:"        "$PROMPT_FOUND"
printf "  %-32s %s\n" "Exec-approvals:"           "$APPROVALS_STATUS"
printf "  %-32s %s\n" "Cron path:"                "$CRON_STATUS"
printf "  %-32s %s\n" "Roadmap-converter agent:"  "$REGISTER_STATUS_STEP"
echo   "  Agent files deployed:"
for agent in planner executor reviewer escalation prd-creator roadmap-converter; do
    printf "    %-24s %s\n" "$agent:" "${AGENT_COUNTS[$agent]:-0}"
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
