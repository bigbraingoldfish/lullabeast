#!/usr/bin/env bash
# install.sh — AutoDev interactive setup (14 steps)
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
IS_WSL2=0

if [ "$OS_TYPE" = "Linux" ]; then
    if [ -r /proc/version ] && grep -qi microsoft /proc/version 2>/dev/null; then
        IS_WSL2=1
        ok "OS: Linux (WSL2)"
        OS_STATUS="WSL2"
    else
        ok "OS: Linux"
    fi
elif [ "$OS_TYPE" = "Darwin" ]; then
    ok "OS: macOS (Darwin)"
    OS_STATUS="macOS"
elif echo "$OS_TYPE" | grep -qiE 'MINGW|CYGWIN|MSYS'; then
    fail "Windows native is not supported. Use WSL2."
else
    if [ "$FORCE" -eq 1 ]; then
        warn "Unknown OS: $OS_TYPE — proceeding due to --force"
        OS_STATUS="unknown (forced)"
    else
        fail "Unsupported OS: $OS_TYPE. AutoDev requires Linux, macOS, or WSL2."
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

# Repo root: if AUTODEV_REPO_PATH is already set in the environment, keep it
# (canonical path); otherwise use the directory containing this install.sh.
_AUTODEV_INSTALL_ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ -n "${AUTODEV_REPO_PATH:-}" ]; then
    if [ ! -d "$AUTODEV_REPO_PATH" ]; then
        fail "AUTODEV_REPO_PATH is set but is not a directory: $AUTODEV_REPO_PATH"
    fi
    AUTODEV_REPO_PATH="$(cd "$AUTODEV_REPO_PATH" && pwd)"
    ok "Using AUTODEV_REPO_PATH from environment: $AUTODEV_REPO_PATH"
else
    AUTODEV_REPO_PATH="$_AUTODEV_INSTALL_ROOT"
fi
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

# Capture the operator-provided OPENCLAW_ROOT before we reassign.
_OC_ENV_INPUT="${OPENCLAW_ROOT:-}"
OPENCLAW_ROOT=""

# Priority 1: $OPENCLAW_ROOT env var (as provided by the operator)
if [ -n "$_OC_ENV_INPUT" ] && [ -d "$_OC_ENV_INPUT" ]; then
    OPENCLAW_ROOT="$_OC_ENV_INPUT"
    ok "Using \$OPENCLAW_ROOT: $OPENCLAW_ROOT"
fi

# Priority 2: ~/.openclaw default
if [ -z "$OPENCLAW_ROOT" ] && [ -d "$HOME/.openclaw" ]; then
    OPENCLAW_ROOT="$HOME/.openclaw"
    ok "Found OpenClaw at default path: $OPENCLAW_ROOT"
fi

# Priority 3: interactive prompt
if [ -z "$OPENCLAW_ROOT" ]; then
    echo "  OpenClaw not found at \$OPENCLAW_ROOT or ~/.openclaw"
    if [ "$NON_INTERACTIVE" -eq 1 ]; then
        fail "OpenClaw not found. Set OPENCLAW_ROOT env var or install OpenClaw at ~/.openclaw."
    fi
    while true; do
        read -r -p "  Enter your OpenClaw directory path: " user_path
        user_path="${user_path/#\~/$HOME}"  # expand leading ~
        if [ -d "$user_path" ] && [ -f "$user_path/openclaw.json" ]; then
            OPENCLAW_ROOT="$user_path"
            ok "OpenClaw found at: $OPENCLAW_ROOT"
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
unset _OC_ENV_INPUT

# openclaw.json must already exist — we never create it.
# Its absence means OpenClaw is not installed or is broken beyond what AutoDev
# can repair (gateway process, auth-profiles, and agent session management all
# depend on it).  Fail fast here rather than generating a stub.
if [ ! -f "$OPENCLAW_ROOT/openclaw.json" ]; then
    fail "openclaw.json not found at $OPENCLAW_ROOT/openclaw.json. Install and start OpenClaw, then re-run install.sh."
fi
ok "openclaw.json present"

mkdir -p "$AUTODEV_REPO_PATH/.autodev/ideas"
ok "Repo-local runtime dir: $AUTODEV_REPO_PATH/.autodev"

if [ -f "$OPENCLAW_ROOT/orchestrator.py" ]; then
    warn "Pre-migration orchestrator still present: $OPENCLAW_ROOT/orchestrator.py"
    warn "  This file is superseded by $AUTODEV_REPO_PATH/autodev/pipeline/orchestrator.py"
    warn "  Remove it: rm $OPENCLAW_ROOT/orchestrator.py"
else
    ok "No stale orchestrator.py in \$OPENCLAW_ROOT"
fi

REPO_RT="$AUTODEV_REPO_PATH/.autodev"
if [ -f "$REPO_RT/pipeline.lock" ]; then
    warn "$REPO_RT/pipeline.lock exists — a pipeline may already be running"
    warn "  If no pipeline is active, remove it: rm $REPO_RT/pipeline.lock"
elif [ -f "$OPENCLAW_ROOT/pipeline.lock" ]; then
    warn "Legacy lock at $OPENCLAW_ROOT/pipeline.lock (pre repo-local runtime)"
    warn "  If unused, remove it; see docs/RUNTIME-MIGRATION.md"
else
    ok "No stale pipeline.lock under .autodev or \$OPENCLAW_ROOT"
fi

export OPENCLAW_ROOT AUTODEV_REPO_PATH
ok "AUTODEV_REPO_PATH = $AUTODEV_REPO_PATH"
ok "OPENCLAW_ROOT     = $OPENCLAW_ROOT"

# Write detected paths to ui/config.json (atomic). Seeds from config.example.json when missing.
export INSTALL_OPENCLAW_ROOT="$OPENCLAW_ROOT"
"$PYTHON" -c "
import json, os, tempfile
repo = os.environ['AUTODEV_REPO_PATH']
oc = os.environ.get('INSTALL_OPENCLAW_ROOT', '')
p = os.path.join(repo, 'ui', 'config.json')
example = os.path.join(repo, 'ui', 'config.example.json')
cfg = {}
if os.path.exists(p):
    try:
        with open(p) as f:
            cfg = json.load(f)
    except Exception:
        pass
elif os.path.exists(example):
    with open(example) as f:
        cfg = json.load(f)
cfg['autodev_repo_path'] = repo
if oc:
    cfg['openclaw_root'] = oc
d = os.path.dirname(p)
fd, tmp = tempfile.mkstemp(dir=d, prefix='config_', suffix='.json')
with os.fdopen(fd, 'w') as f:
    json.dump(cfg, f, indent=2)
os.replace(tmp, p)
print('ok')
" > /dev/null && ok "ui/config.json updated (autodev_repo_path, openclaw_root)" \
  || warn "Could not update ui/config.json — copy ui/config.example.json and set paths"

# ─────────────────────────────────────────────────────────────────────────────
# 5/13  OPENCLAW VERSION CHECK
# ─────────────────────────────────────────────────────────────────────────────
hdr "5/13  OpenClaw version check"

OC_VERSION_STATUS="unknown"
OC_JSON="$OPENCLAW_ROOT/openclaw.json"

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

# Each pipeline agent workspace needs pipeline-project → $OPENCLAW_ROOT/pipeline-project
# (the hub the UI/orchestrator update). OpenClaw sandboxes writes to the workspace root.
ensure_workspace_pipeline_project_symlinks() {
    local hub="$OPENCLAW_ROOT/pipeline-project"
    local ws_dir link any=0
    for pipeline_agent in planner executor reviewer escalation; do
        ws_dir="$OPENCLAW_ROOT/workspace-$pipeline_agent"
        [ -d "$ws_dir" ] || continue
        any=1
        link="$ws_dir/pipeline-project"
        if [ -e "$link" ] && [ ! -L "$link" ]; then
            warn "$link exists but is not a symlink — skipping (remove or convert manually)"
            continue
        fi
        if ! ln -sfn "$hub" "$link"; then
            warn "Could not create symlink $link → $hub"
        fi
    done
    if [ "$any" -eq 1 ]; then
        ok "workspace pipeline-project symlinks → $hub"
    fi
}

TOTAL_DEPLOYED=0
# Parallel indexed array of "agent=count" pairs (bash 3.2-compatible — macOS
# default bash lacks `declare -A`). Use _set_count / _get_count helpers below.
AGENT_COUNTS=()
MISSING_FILES=()

_set_count() {
    # Replace existing entry if present, else append.
    local key="$1" val="$2" prefix="$1=" i
    for i in "${!AGENT_COUNTS[@]}"; do
        case "${AGENT_COUNTS[$i]}" in
            "$prefix"*) AGENT_COUNTS[$i]="$key=$val"; return ;;
        esac
    done
    AGENT_COUNTS+=("$key=$val")
}

_get_count() {
    local prefix="$1=" item
    for item in "${AGENT_COUNTS[@]}"; do
        case "$item" in
            "$prefix"*) echo "${item#"$prefix"}"; return ;;
        esac
    done
    echo "0"
}

for agent in planner executor reviewer escalation prd-creator roadmap-converter; do
    src_dir="$AUTODEV_REPO_PATH/autodev/agents/$agent"
    dst_dir="$OPENCLAW_ROOT/workspace-$agent"

    if [ ! -d "$src_dir" ]; then
        warn "Source not found: $src_dir — skipping $agent"
        _set_count "$agent" "skip"
        continue
    fi

    if [ ! -d "$dst_dir" ]; then
        mkdir -p "$dst_dir"
        ok "Created workspace: $dst_dir"
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

    # Ensure expected skills directories exist for non-pipeline agents that
    # consume static skills from workspace paths.
    case "$agent" in
        prd-creator|roadmap-converter|escalation)
            mkdir -p "$dst_dir/skills"
            ;;
    esac

    if [ "$agent" = "prd-creator" ]; then
        src="$AUTODEV_REPO_PATH/autodev/skill-library/prd-creator/readiness-reviewer/SKILL.md"
        dst="$dst_dir/skills/readiness-reviewer/SKILL.md"
        if [ -f "$src" ]; then
            if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
                MISSING_FILES+=("  $agent/skills/readiness-reviewer/SKILL.md")
            fi
        else
            MISSING_FILES+=("  MISSING SOURCE: autodev/skill-library/prd-creator/readiness-reviewer/SKILL.md")
        fi
    fi

    if [ "$agent" = "escalation" ]; then
        src="$AUTODEV_REPO_PATH/autodev/agents/escalation/skills/escalation-summary/SKILL.md"
        dst="$dst_dir/skills/escalation-summary/SKILL.md"
        if [ -f "$src" ]; then
            if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
                MISSING_FILES+=("  $agent/skills/escalation-summary/SKILL.md")
            fi
        else
            MISSING_FILES+=("  MISSING SOURCE: autodev/agents/escalation/skills/escalation-summary/SKILL.md")
        fi
    fi

    if [ "$agent" = "roadmap-converter" ]; then
        for skill in roadmap-generation alignment-check adversarial-review; do
            src="$AUTODEV_REPO_PATH/autodev/skill-library/roadmap-converter/$skill/SKILL.md"
            dst="$dst_dir/skills/$skill/SKILL.md"
            if [ -f "$src" ]; then
                if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
                    MISSING_FILES+=("  $agent/skills/$skill/SKILL.md")
                fi
            else
                MISSING_FILES+=("  MISSING SOURCE: autodev/skill-library/roadmap-converter/$skill/SKILL.md")
            fi
        done
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
            dst_dir="$OPENCLAW_ROOT/workspace-$agent"
            [ -d "$src_dir" ] || continue
            mkdir -p "$dst_dir"
            count=0
            for doc in IDENTITY.md SOUL.md TOOLS.md AGENTS.md USER.md; do
                src="$src_dir/$doc"
                dst="$dst_dir/$doc"
                [ -f "$src" ] || continue
                # Same "newer-than" predicate the dry-run preview block uses
                # (above, ~line 371). BSD `cp` lacks `-u`; this is portable.
                if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
                    cp "$src" "$dst"
                    count=$((count + 1))
                fi
            done
            case "$agent" in planner|executor|reviewer)
                src="$src_dir/HEARTBEAT.md"
                dst="$dst_dir/HEARTBEAT.md"
                if [ -f "$src" ] && { [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; }; then
                    cp "$src" "$dst"
                    count=$((count + 1))
                fi
                ;;
            esac
            case "$agent" in
                prd-creator|roadmap-converter|escalation)
                    mkdir -p "$dst_dir/skills"
                    ;;
            esac

            if [ "$agent" = "prd-creator" ]; then
                src="$AUTODEV_REPO_PATH/autodev/skill-library/prd-creator/readiness-reviewer/SKILL.md"
                dst="$dst_dir/skills/readiness-reviewer/SKILL.md"
                if [ -f "$src" ]; then
                    mkdir -p "$dst_dir/skills/readiness-reviewer"
                    if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
                        cp "$src" "$dst"
                        count=$((count + 1))
                    fi
                else
                    warn "Missing skill source for prd-creator: $src"
                fi
            fi

            if [ "$agent" = "escalation" ]; then
                src="$AUTODEV_REPO_PATH/autodev/agents/escalation/skills/escalation-summary/SKILL.md"
                dst="$dst_dir/skills/escalation-summary/SKILL.md"
                if [ -f "$src" ]; then
                    mkdir -p "$dst_dir/skills/escalation-summary"
                    if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
                        cp "$src" "$dst"
                        count=$((count + 1))
                    fi
                else
                    warn "Missing skill source for escalation: $src"
                fi
            fi

            if [ "$agent" = "roadmap-converter" ]; then
                for skill in roadmap-generation alignment-check adversarial-review; do
                    src="$AUTODEV_REPO_PATH/autodev/skill-library/roadmap-converter/$skill/SKILL.md"
                    dst="$dst_dir/skills/$skill/SKILL.md"
                    if [ -f "$src" ]; then
                        mkdir -p "$dst_dir/skills/$skill"
                        if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
                            cp "$src" "$dst"
                            count=$((count + 1))
                        fi
                    else
                        warn "Missing skill source for roadmap-converter ($skill): $src"
                    fi
                done
            fi
            _set_count "$agent" "$count"
            TOTAL_DEPLOYED=$((TOTAL_DEPLOYED + count))
            ok "$agent: $count file(s) deployed → $dst_dir"
        done
        info "Total agent files deployed: $TOTAL_DEPLOYED"
    else
        warn "Agent workspace provisioning skipped"
        for agent in planner executor reviewer escalation prd-creator roadmap-converter; do
            _set_count "$agent" "skipped"
        done
    fi
else
    ok "All agent workspace files are current — nothing to deploy"
    for agent in planner executor reviewer escalation prd-creator roadmap-converter; do
        _set_count "$agent" "current"
    done
fi

ensure_workspace_pipeline_project_symlinks

# ─────────────────────────────────────────────────────────────────────────────
# 7/13  EXEC-APPROVALS VALIDATION
# ─────────────────────────────────────────────────────────────────────────────
hdr "7/13  Exec-approvals validation"

EXEC_APPROVALS="$OPENCLAW_ROOT/exec-approvals.json"
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

CRON_FILE="$OPENCLAW_ROOT/cron/jobs.json"
CRON_STATUS="not found"

if [ ! -f "$CRON_FILE" ]; then
    warn "cron/jobs.json not found at $CRON_FILE — skipping"
else
    OLD_CRON_SCRIPT="$OPENCLAW_ROOT/heartbeat_cron.py"
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
# 9/13  REGISTER AUTODEV AGENTS (OPENCLAW)
# ─────────────────────────────────────────────────────────────────────────────
hdr "9/13  Register AutoDev agents in openclaw.json"

REGISTER_STATUS_STEP="not attempted"
TOOLS_PROFILE_STEP="skipped"
HOOKS_STEP="not attempted"
WEBHOOK_SYNC_STEP="not checked"
REGISTER_AGENT="$AUTODEV_REPO_PATH/autodev/installer/register_agent.py"
UI_CONFIG_PATH="$AUTODEV_REPO_PATH/ui/config.json"
ENV_FILE="$AUTODEV_REPO_PATH/.env"

if [ ! -f "$REGISTER_AGENT" ]; then
    warn "register_agent.py not found at $REGISTER_AGENT — skipping"
    REGISTER_STATUS_STEP="script missing"
elif [ ! -f "$OPENCLAW_ROOT/openclaw.json" ]; then
    warn "openclaw.json not found — cannot register pipeline agents"
    REGISTER_STATUS_STEP="skipped (no openclaw.json)"
else
    # Webhook hooks baseline (orchestrator/UI → gateway) before agent registration
    HOOK_ISSUES=$(
        cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import openclaw_hooks_issues
import sys
print(','.join(openclaw_hooks_issues(sys.argv[1])))
" "$OPENCLAW_ROOT/openclaw.json" 2>/dev/null || echo "audit_error"
    )
    if [ "$HOOK_ISSUES" = "audit_error" ]; then
        warn "Could not audit hooks block in openclaw.json — fix JSON syntax and re-run install.sh"
        HOOKS_STEP="audit_error"
    elif [ -z "$HOOK_ISSUES" ]; then
        ok "openclaw.json hooks baseline OK (webhook Bearer + session-key prefixes for AutoDev)"
        HOOKS_STEP="ok"
    else
        warn "openclaw.json hooks need changes for AutoDev webhook calls:"
        case ",$HOOK_ISSUES," in
            *,no_hooks_object,*|*,hooks_not_object,*) warn "  · hooks must be a JSON object" ;;
        esac
        case ",$HOOK_ISSUES," in *,enabled,*) warn "  · hooks.enabled should be true" ;; esac
        case ",$HOOK_ISSUES," in *,token,*) warn "  · hooks.token is required (Bearer secret for POST /hooks/agent — not the Control UI token)" ;; esac
        case ",$HOOK_ISSUES," in *,allowRequestSessionKey,*) warn "  · hooks.allowRequestSessionKey should be true" ;; esac
        case ",$HOOK_ISSUES," in *,allowedSessionKeyPrefixes,*) warn "  · hooks.allowedSessionKeyPrefixes should be a list" ;; esac
        case ",$HOOK_ISSUES," in *,prefix_pipeline,*) warn "  · allowedSessionKeyPrefixes must include pipeline:" ;; esac
        case ",$HOOK_ISSUES," in *,prefix_ideas,*) warn "  · allowedSessionKeyPrefixes must include ideas:" ;; esac
        case ",$HOOK_ISSUES," in *,invalid_json,*) warn "  · openclaw.json is not valid JSON" ;; esac
        case ",$HOOK_ISSUES," in *,invalid_root,*) warn "  · openclaw.json root must be an object" ;; esac
        case ",$HOOK_ISSUES," in *,no_file,*) warn "  · openclaw.json not found (unexpected)" ;; esac
        HOOKS_STEP="issues: ${HOOK_ISSUES}"
        if prompt_yn "Patch hooks now (atomic write; keeps your existing hooks.token if set)? [Y/n]" "Y"; then
            HP=$(
                cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import patch_openclaw_hooks_baseline
import sys
print(patch_openclaw_hooks_baseline(sys.argv[1]))
" "$OPENCLAW_ROOT/openclaw.json" 2>/dev/null || echo "error:patch"
            )
            case "$HP" in
                updated) ok "openclaw.json hooks block updated (enabled, session-key flags, prefixes)" ;;
                unchanged) ok "openclaw.json hooks block already matched baseline (no file change)" ;;
                *) warn "hooks patch failed: $HP" ;;
            esac
        else
            warn "hooks patch skipped — pipeline webhooks may be rejected until hooks are fixed"
        fi
        HOOK_ISSUES_AFTER=$(
            cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import openclaw_hooks_issues
import sys
print(','.join(openclaw_hooks_issues(sys.argv[1])))
" "$OPENCLAW_ROOT/openclaw.json" 2>/dev/null || echo "audit_error"
        )
        if [ "$HOOK_ISSUES_AFTER" = "audit_error" ]; then
            warn "Could not re-audit hooks after patch"
        elif echo ",$HOOK_ISSUES_AFTER," | grep -q ",token,"; then
            if prompt_yn "hooks.token is still empty. Generate a random token and set it (atomic)? [Y/n]" "Y"; then
                GEN_TOKEN=$("$PYTHON" -c "import secrets; print(secrets.token_urlsafe(32))")
                HT=$(
                    cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import patch_openclaw_hooks_baseline
import sys
print(patch_openclaw_hooks_baseline(sys.argv[1], token_if_missing=sys.argv[2]))
" "$OPENCLAW_ROOT/openclaw.json" "$GEN_TOKEN" 2>/dev/null || echo "error:token"
                )
                case "$HT" in
                    updated)
                        ok "hooks.token generated and written to openclaw.json"
                        info "Use the same value for AUTODEV_HOOKS_TOKEN (or ui/config.json hooks_token) so the UI can call the gateway"
                        TE=$(
                            cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import merge_dotenv_missing_keys
import os, sys
print(merge_dotenv_missing_keys(os.path.join(sys.argv[1], '.env'), {'AUTODEV_HOOKS_TOKEN': sys.argv[2]}))
" "$AUTODEV_REPO_PATH" "$GEN_TOKEN" 2>/dev/null || echo "error:env"
                        )
                        case "$TE" in
                            created|updated) ok "AUTODEV_HOOKS_TOKEN appended to .env (existing value preserved if already set)" ;;
                            unchanged) info ".env already had AUTODEV_HOOKS_TOKEN — update it manually if it does not match hooks.token" ;;
                            *) warn "Could not merge AUTODEV_HOOKS_TOKEN into .env: $TE" ;;
                        esac
                        ;;
                    *)
                        warn "Could not set hooks.token: $HT"
                        ;;
                esac
            else
                warn "Without hooks.token, POST /hooks/agent returns 401 — set it manually to match the UI/orchestrator Bearer secret"
            fi
        fi
        HOOK_FINAL=$(
            cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import openclaw_hooks_issues
import sys
print(','.join(openclaw_hooks_issues(sys.argv[1])))
" "$OPENCLAW_ROOT/openclaw.json" 2>/dev/null || echo "audit_error"
        )
        if [ "$HOOK_FINAL" = "audit_error" ]; then
            HOOKS_STEP="audit_error (after patch attempt)"
        elif [ -z "$HOOK_FINAL" ]; then
            HOOKS_STEP="ok (patched)"
        else
            HOOKS_STEP="pending: ${HOOK_FINAL}"
        fi
    fi

    # Webhook secret sync checks:
    #   hooks.token  <->  ui/config.json hooks_token  <->  .env AUTODEV_HOOKS_TOKEN
    read_sync_state() {
        "$PYTHON" -c "
from autodev.installer.setup_helpers import webhook_secret_sync_assess
import sys
r = webhook_secret_sync_assess(sys.argv[1], sys.argv[2], sys.argv[3])
print('|'.join([
  r.summary_code(),
  '1' if r.expected_token else '0',
  '1' if r.ui_needs_sync else '0',
  '1' if r.env_key_missing_or_empty else '0',
  '1' if r.env_wrong else '0',
  '1' if r.ui_config_exists else '0',
]))
" "$OPENCLAW_ROOT/openclaw.json" "$UI_CONFIG_PATH" "$ENV_FILE" 2>/dev/null || echo "error|0|0|0|0|0"
    }

    SYNC_STATE=$(read_sync_state)
    IFS='|' read -r SYNC_CODE SYNC_HAS_TOKEN SYNC_UI_NEEDS SYNC_ENV_MISSING SYNC_ENV_WRONG SYNC_UI_EXISTS <<< "$SYNC_STATE"

    if [ "$SYNC_HAS_TOKEN" = "1" ]; then
        if [ "$SYNC_ENV_MISSING" = "1" ]; then
            CURRENT_HOOK_TOKEN=$(
                cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import read_openclaw_hooks_token
import sys
print(read_openclaw_hooks_token(sys.argv[1]) or '')
" "$OPENCLAW_ROOT/openclaw.json" 2>/dev/null || echo ""
            )
            if [ -n "$CURRENT_HOOK_TOKEN" ]; then
                ADD_ENV_RESULT=$(
                    cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import merge_dotenv_missing_keys
import os, sys
print(merge_dotenv_missing_keys(sys.argv[1], {'AUTODEV_HOOKS_TOKEN': sys.argv[2]}))
" "$ENV_FILE" "$CURRENT_HOOK_TOKEN" 2>/dev/null || echo "error:env"
                )
                case "$ADD_ENV_RESULT" in
                    created|updated|unchanged) ok ".env ensures AUTODEV_HOOKS_TOKEN is present ($ADD_ENV_RESULT)" ;;
                    *) warn "Could not ensure AUTODEV_HOOKS_TOKEN in .env: $ADD_ENV_RESULT" ;;
                esac
            fi
        fi

        # Re-read status after adding any missing .env key.
        SYNC_STATE=$(read_sync_state)
        IFS='|' read -r SYNC_CODE SYNC_HAS_TOKEN SYNC_UI_NEEDS SYNC_ENV_MISSING SYNC_ENV_WRONG SYNC_UI_EXISTS <<< "$SYNC_STATE"

        if [ "$SYNC_UI_NEEDS" = "1" ]; then
            if [ "$NON_INTERACTIVE" -eq 1 ]; then
                warn "ui/config.json hooks_token is empty or does not match hooks.token (non-interactive mode: no overwrite)"
            elif [ "$SYNC_UI_EXISTS" = "1" ]; then
                if prompt_yn "Sync ui/config.json hooks_token to match openclaw.json hooks.token? [Y/n]" "Y"; then
                    UI_SYNC_RESULT=$(
                        cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import read_openclaw_hooks_token, set_ui_config_hooks_token
import sys
tok = read_openclaw_hooks_token(sys.argv[1]) or ''
print(set_ui_config_hooks_token(sys.argv[2], tok) if tok else 'error:empty token')
" "$OPENCLAW_ROOT/openclaw.json" "$UI_CONFIG_PATH" 2>/dev/null || echo "error:ui"
                    )
                    case "$UI_SYNC_RESULT" in
                        updated|unchanged) ok "ui/config.json hooks_token synced to hooks.token ($UI_SYNC_RESULT)" ;;
                        *) warn "Could not sync ui/config.json hooks_token: $UI_SYNC_RESULT" ;;
                    esac
                else
                    warn "ui/config.json hooks_token sync skipped by user"
                fi
            else
                warn "ui/config.json missing — cannot sync hooks_token automatically"
            fi
        fi

        if [ "$SYNC_ENV_WRONG" = "1" ]; then
            if [ "$NON_INTERACTIVE" -eq 1 ]; then
                warn ".env AUTODEV_HOOKS_TOKEN does not match hooks.token (non-interactive mode: no overwrite)"
            else
                if prompt_yn "Update .env AUTODEV_HOOKS_TOKEN to match openclaw.json hooks.token? [Y/n]" "Y"; then
                    ENV_SYNC_RESULT=$(
                        cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import read_openclaw_hooks_token, set_dotenv_key
import sys
tok = read_openclaw_hooks_token(sys.argv[1]) or ''
print(set_dotenv_key(sys.argv[2], 'AUTODEV_HOOKS_TOKEN', tok) if tok else 'error:empty token')
" "$OPENCLAW_ROOT/openclaw.json" "$ENV_FILE" 2>/dev/null || echo "error:env"
                    )
                    case "$ENV_SYNC_RESULT" in
                        created|updated|unchanged) ok ".env AUTODEV_HOOKS_TOKEN synced ($ENV_SYNC_RESULT)" ;;
                        *) warn "Could not sync .env AUTODEV_HOOKS_TOKEN: $ENV_SYNC_RESULT" ;;
                    esac
                else
                    warn ".env AUTODEV_HOOKS_TOKEN sync skipped by user"
                fi
            fi
        fi

        SYNC_STATE=$(read_sync_state)
        IFS='|' read -r SYNC_CODE SYNC_HAS_TOKEN SYNC_UI_NEEDS SYNC_ENV_MISSING SYNC_ENV_WRONG SYNC_UI_EXISTS <<< "$SYNC_STATE"
        if [ "$SYNC_CODE" = "ok" ]; then
            WEBHOOK_SYNC_STEP="ok"
            ok "Webhook secret sync OK (hooks.token, ui/config.json, .env)"
        else
            WEBHOOK_SYNC_STEP="ACTION REQUIRED ($SYNC_CODE)"
            warn "Webhook secret sync is incomplete: $SYNC_CODE"
            REM_TEXT=$(
                cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import webhook_secret_remediation_text
print(webhook_secret_remediation_text())
" 2>/dev/null || true
            )
            if [ -n "$REM_TEXT" ]; then
                echo "$REM_TEXT"
            fi
        fi
    else
        WEBHOOK_SYNC_STEP="pending: hooks.token missing"
        warn "hooks.token missing in openclaw.json — cannot sync UI/.env webhook secret"
    fi

    TOOLS_PROFILE=$("$PYTHON" -c "
import json, sys
with open(sys.argv[1], encoding='utf-8') as f:
    d = json.load(f)
t = d.get('tools') or {}
print((t.get('profile') or '').strip())
" "$OPENCLAW_ROOT/openclaw.json" 2>/dev/null || echo "__invalid__")

    case "$TOOLS_PROFILE" in
        coding|full|"")
            ok "tools.profile OK for pipeline coding agents (${TOOLS_PROFILE:-unset = gateway default})"
            TOOLS_PROFILE_STEP="ok (${TOOLS_PROFILE:-default})"
            ;;
        *)
            warn "openclaw.json tools.profile is '${TOOLS_PROFILE}' — not 'coding' or 'full'."
            warn "  Planner/executor/reviewer need Coding-profile tools (fs, exec, web, sessions, memory)."
            warn "  Reference: https://docs.openclaw.ai/tools"
            TOOLS_PROFILE_STEP="warn ($TOOLS_PROFILE)"
            if prompt_yn "Set global tools.profile to \"coding\" now? (atomic write; no other keys changed) [y/N]" "N"; then
                TP_R=$(
                    cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import set_openclaw_global_tools_profile
import sys
print(set_openclaw_global_tools_profile(sys.argv[1], 'coding'))
" "$OPENCLAW_ROOT/openclaw.json" 2>/dev/null || echo "error:helper"
                )
                case "$TP_R" in
                    updated)
                        ok "tools.profile set to coding in $OPENCLAW_ROOT/openclaw.json"
                        TOOLS_PROFILE_STEP="updated to coding"
                        ;;
                    unchanged)
                        ok "tools.profile already coding"
                        TOOLS_PROFILE_STEP="ok (coding)"
                        ;;
                    *)
                        warn "Could not update tools.profile: $TP_R"
                        ;;
                esac
            fi
            ;;
    esac

    # Dry-run: status token is the last line of stdout (warnings go to stderr)
    REGISTER_DRY_OUTPUT=$("$PYTHON" "$REGISTER_AGENT" \
        "$OPENCLAW_ROOT/openclaw.json" "$OPENCLAW_ROOT" --dry-run)
    REGISTER_DRY_STATUS=$(echo "$REGISTER_DRY_OUTPUT" | tail -1)

    case "$REGISTER_DRY_STATUS" in
        already_registered)
            ok "All AutoDev agents already registered in openclaw.json"
            REGISTER_STATUS_STEP="already registered"
            ;;
        dry_run)
            # Print the preview (everything except the last line)
            # `head -n -1` is GNU-only; `sed '$d'` (delete last line) is BSD/GNU portable.
            echo "$REGISTER_DRY_OUTPUT" | sed '$d'
            info "The above changes would be applied to $OPENCLAW_ROOT/openclaw.json"
            if prompt_yn "Register missing AutoDev agents (planner, executor, reviewer, escalation, prd-creator, roadmap-converter)? [Y/n]" "Y"; then
                APPLY_RESULT=$("$PYTHON" "$REGISTER_AGENT" \
                    "$OPENCLAW_ROOT/openclaw.json" "$OPENCLAW_ROOT" --apply)
                case "$APPLY_RESULT" in
                    registered)
                        ok "AutoDev agents registered in openclaw.json"
                        REGISTER_STATUS_STEP="registered"
                        ;;
                    already_registered)
                        ok "AutoDev agents already registered (concurrent write?)"
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
                warn "Skipped — pipeline agents not registered in openclaw.json"
                warn "  Webhook invocations for planner/executor/reviewer/escalation/prd-creator/roadmap-converter may be denied"
                warn "  Re-run install.sh and accept the prompt to register later"
                REGISTER_STATUS_STEP="skipped by user"
            fi
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
# 10/13  CONVERSION PROMPT (repo-bundled)
# ─────────────────────────────────────────────────────────────────────────────
hdr "10/13  Conversion prompt"

BUNDLED_PROMPT="$AUTODEV_REPO_PATH/autodev/prompts/prd-to-roadmap-conversion.txt"
if [ -f "$BUNDLED_PROMPT" ]; then
    ok "PRD→roadmap conversion instructions: $BUNDLED_PROMPT"
    PROMPT_FOUND="yes (bundled)"
else
    warn "Bundled conversion prompt missing: $BUNDLED_PROMPT"
    PROMPT_FOUND="no"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 11/13  WRITE .env
# ─────────────────────────────────────────────────────────────────────────────
hdr "11/13  Writing .env"

ENV_FILE="$AUTODEV_REPO_PATH/.env"
ENV_MERGE=$(
    cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import merge_dotenv_missing_keys
import os
repo_path = os.environ['AUTODEV_REPO_PATH']
rt = os.path.join(repo_path, '.autodev')
oc = os.environ['OPENCLAW_ROOT']
# Canonical names only. The legacy aliases AUTODEV_ROOT and
# AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME were removed.
pairs = {
    'OPENCLAW_ROOT': oc,
    'AUTODEV_REPO_PATH': repo_path,
    'AUTODEV_PIPELINE_ROOT': rt,
}
print(merge_dotenv_missing_keys(os.path.join(repo_path, '.env'), pairs))
" 2>/dev/null || echo "error:helper"
)
case "$ENV_MERGE" in
    created|updated) ok ".env merged ($ENV_MERGE): $ENV_FILE" ;;
    unchanged) info ".env unchanged (keys already present)" ;;
    *) warn ".env merge: $ENV_MERGE" ;;
esac

ENV_STALL_HINTS=$(
    cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import ensure_dotenv_stall_timeout_hints
import os
print(ensure_dotenv_stall_timeout_hints(os.path.join(os.environ['AUTODEV_REPO_PATH'], '.env')))
" 2>/dev/null || echo "error:helper"
)
case "$ENV_STALL_HINTS" in
    appended) ok "Added commented AUTODEV_STALL_TIMEOUT_* placeholders to .env (optional overrides; see SETUP.md)" ;;
    unchanged) : ;;
    *) warn ".env stall-timeout hint block: $ENV_STALL_HINTS" ;;
esac

ENV_IDEAS_HINTS=$(
    cd "$AUTODEV_REPO_PATH" && PYTHONPATH="$AUTODEV_REPO_PATH" "$PYTHON" -c "
from autodev.installer.setup_helpers import ensure_dotenv_ideas_idle_hints
import os
print(ensure_dotenv_ideas_idle_hints(os.path.join(os.environ['AUTODEV_REPO_PATH'], '.env')))
" 2>/dev/null || echo "error:helper"
)
case "$ENV_IDEAS_HINTS" in
    appended) ok "Added commented AUTODEV_IDEAS_* placeholders to .env (Ideas UI poll; see SETUP.md)" ;;
    unchanged) : ;;
    *) warn ".env Ideas idle hint block: $ENV_IDEAS_HINTS" ;;
esac

# ─────────────────────────────────────────────────────────────────────────────
# 12/14  INSTALL PIPELINE SIGNALS PLUGIN
# ─────────────────────────────────────────────────────────────────────────────
hdr "12/14  Installing autodev-pipeline-signals plugin"

PLUGIN_DIR="$AUTODEV_REPO_PATH/autodev/plugin"
PLUGIN_INSTALL_STEP="not attempted"
if command -v openclaw >/dev/null 2>&1; then
    if openclaw plugins install "$PLUGIN_DIR" >/dev/null 2>&1; then
        ok "Plugin installed: autodev-pipeline-signals"
        info "  Restart the OpenClaw gateway to load the plugin."
        # Ensure allowConversationAccess is set in the installed plugin entry.
        OC_CFG="$OPENCLAW_ROOT/openclaw.json"
        if [ -f "$OC_CFG" ]; then
            # Use python to add allowConversationAccess without disturbing other keys.
            python3 - "$OC_CFG" <<'PYEOF' 2>/dev/null && ok "openclaw.json: allowConversationAccess set for autodev-pipeline-signals" || warn "Could not patch openclaw.json — set hooks.allowConversationAccess manually for autodev-pipeline-signals"
import json, sys, os, tempfile
cfg_path = sys.argv[1]
with open(cfg_path) as f:
    cfg = json.load(f)
plugins = cfg.setdefault("plugins", {}).setdefault("entries", {})
entry = plugins.setdefault("autodev-pipeline-signals", {})
entry["hooks"] = entry.get("hooks", {})
entry["hooks"]["allowConversationAccess"] = True
tmp = cfg_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
os.replace(tmp, cfg_path)
PYEOF
        fi
        PLUGIN_INSTALL_STEP="ok"
    else
        warn "Plugin install failed — run manually: openclaw plugins install \"$PLUGIN_DIR\""
        PLUGIN_INSTALL_STEP="warn (see above)"
    fi
else
    warn "openclaw CLI not found — plugin not installed. Run manually after gateway is available: openclaw plugins install \"$PLUGIN_DIR\""
    PLUGIN_INSTALL_STEP="warn (openclaw not in PATH)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 13/14  MARK SETUP COMPLETE
# ─────────────────────────────────────────────────────────────────────────────
hdr "13/14  Marking setup complete"

date -u +%Y-%m-%dT%H:%M:%SZ > "$SETUP_MARKER"
ok "Setup marker written to $SETUP_MARKER"
info "  The AutoDev UI uses this file for first-run detection"

# ─────────────────────────────────────────────────────────────────────────────
# 14/14  SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
hdr "14/14  Summary"
echo
printf "  %-32s %s\n" "OS:"                      "$OS_TYPE ($OS_STATUS)"
printf "  %-32s %s\n" "Python version:"           "$PYTHON_VERSION"
printf "  %-32s %s\n" "Pip packages installed:"   "$PIP_INSTALLED"
printf "  %-32s %s\n" "OPENCLAW_ROOT:"            "$OPENCLAW_ROOT"
printf "  %-32s %s\n" "OpenClaw version:"         "$OC_VERSION_STATUS"
printf "  %-32s %s\n" "Conversion prompt:"        "$PROMPT_FOUND"
printf "  %-32s %s\n" "Exec-approvals:"           "$APPROVALS_STATUS"
printf "  %-32s %s\n" "Cron path:"                "$CRON_STATUS"
printf "  %-32s %s\n" "OpenClaw hooks (webhook):"   "$HOOKS_STEP"
printf "  %-32s %s\n" "Webhook secret sync:"      "$WEBHOOK_SYNC_STEP"
printf "  %-32s %s\n" "OpenClaw tools.profile:"   "$TOOLS_PROFILE_STEP"
printf "  %-32s %s\n" "OpenClaw agents (register):" "$REGISTER_STATUS_STEP"
printf "  %-32s %s\n" "Pipeline signals plugin:"    "$PLUGIN_INSTALL_STEP"
echo   "  Agent files deployed:"
for agent in planner executor reviewer escalation prd-creator roadmap-converter; do
    printf "    %-24s %s\n" "$agent:" "$(_get_count "$agent")"
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
echo "  Start with: cd \"$AUTODEV_REPO_PATH\" && source .env && uvicorn ui.server:app --host 127.0.0.1 --port 18790 (change to 0.0.0.0 only if serving trusted LAN)"
echo "  Verify hooks with POST (expect HTTP 200):"
echo "    curl -sS -o /dev/null -w \"HTTP %{http_code}\\n\" -X POST http://127.0.0.1:18789/hooks/agent \\"
echo "      -H \"Authorization: Bearer <hooks.token>\" -H \"Content-Type: application/json\" \\"
echo "      -d '{\"agentId\":\"prd-creator\",\"sessionKey\":\"ideas:install-check:0\",\"wakeMode\":\"now\",\"message\":\"ping\"}'"
echo
echo "  To run as a background service:"
if [ "$OS_TYPE" = "Darwin" ]; then
    echo "    macOS — install the bundled LaunchAgent:"
    echo "      1. Edit WorkingDirectory and ProgramArguments in ui/com.autodev.ui.plist"
    echo "      2. cp \"$AUTODEV_REPO_PATH/ui/com.autodev.ui.plist\" ~/Library/LaunchAgents/"
    echo "      3. launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.autodev.ui.plist"
    echo "      4. launchctl enable gui/\$(id -u)/com.autodev.ui"
    echo "    Tail logs: tail -f /tmp/autodev-ui.log /tmp/autodev-ui.err"
elif [ "$IS_WSL2" -eq 1 ]; then
    echo "    WSL2 — enable systemd first (if not already enabled):"
    echo "      echo '[boot]' | sudo tee -a /etc/wsl.conf && echo 'systemd=true' | sudo tee -a /etc/wsl.conf"
    echo "      (Restart the WSL instance for systemd to take effect.)"
    echo "    Then install the bundled systemd unit:"
    echo "      sudo cp \"$AUTODEV_REPO_PATH/ui/autodev-ui.service\" /etc/systemd/system/"
    echo "      sudo systemctl daemon-reload && sudo systemctl enable --now autodev-ui"
else
    echo "    Linux — install the bundled systemd unit:"
    echo "      sudo cp \"$AUTODEV_REPO_PATH/ui/autodev-ui.service\" /etc/systemd/system/"
    echo "      sudo systemctl daemon-reload && sudo systemctl enable --now autodev-ui"
fi
echo
