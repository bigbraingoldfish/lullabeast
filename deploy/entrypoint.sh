#!/usr/bin/env bash
# deploy/entrypoint.sh - Lullabeast single-container boot (DS-3).
#
# Boot sequence:
#   1. Validate the env contract (a provider API key is mandatory).
#   2. Provision /data: directories, persisted secrets, and the openclaw.json
#      render (first boot) or reconcile toward the current template (every
#      later boot, so an image upgrade that changes the template self-heals).
#   3. Start the OpenClaw gateway, wait for it, then run
#      ./install.sh --owned-openclaw. install.sh is the single provisioning
#      brain; this entrypoint never reimplements any of its steps. Every boot
#      re-runs it: the repo baked into the image is the source of truth for
#      agent files, skills, and the plugin bundle, so an image upgrade
#      re-deploys them over /data automatically.
#   4. Restart the gateway (plugin bundle + agent registrations are read at
#      gateway start), then run the doctor: --live on first boot only.
#   5. Print the dashboard URL + token, then supervise four processes:
#      gateway, UI server, heartbeat loop, session-cleanup loop. The
#      orchestrator is NOT supervised; the UI server spawns it per run. Any
#      supervised process dying tears the container down (compose's restart
#      policy recovers it).
set -euo pipefail

APP=/app
DATA=/data
export AUTODEV_REPO_PATH="$APP"
export OPENCLAW_ROOT="$DATA/openclaw"
export AUTODEV_PIPELINE_ROOT="$DATA/pipeline-state"
# The openclaw CLI does not read Lullabeast's OPENCLAW_ROOT; it acts on its
# own state dir (default ~/.openclaw). Point both levers at /data/openclaw:
# OpenClaw's OPENCLAW_STATE_DIR env, plus a $HOME symlink (created below)
# covering config values written as literal "~/.openclaw/..." paths (the
# agent workspaces in the golden template).
export OPENCLAW_STATE_DIR="$DATA/openclaw"
UI_PORT="${UI_PORT:-18790}"
GATEWAY_PORT=18789

say() { echo "[lullabeast] $*"; }
die() { echo "[lullabeast] FATAL: $*" >&2; exit 1; }

cd "$APP"

# ── 1. Env contract ──────────────────────────────────────────────────────────
# Fail before starting anything: a keyless container boots into a pipeline
# that stalls silently at the first agent invocation.
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${OPENROUTER_API_KEY:-}" ]; then
    die "no provider API key set. Put ANTHROPIC_API_KEY or OPENROUTER_API_KEY in deploy/.env (the shipped model defaults use OpenRouter, so OPENROUTER_API_KEY is the golden path; with an Anthropic key, also set the *_MODEL variables)."
fi

# ── 2. /data layout, secrets, first-boot config render ──────────────────────
mkdir -p "$OPENCLAW_ROOT" "$AUTODEV_PIPELINE_ROOT" "$DATA/projects"
mkdir -p "$DATA/secrets"
chmod 700 "$DATA/secrets"
ln -sfn "$OPENCLAW_ROOT" "$HOME/.openclaw"

# Secrets are generated once and persisted in /data so they survive container
# recreation and image upgrades. Unique temp file + rename is the
# autodev/pipeline/atomic_io.py pattern replicated in bash (this runs before
# we can rely on anything Python-side being importable).
secret() {
    local f="$DATA/secrets/$1" tmp
    if [ ! -s "$f" ]; then
        tmp=$(mktemp "$f.XXXXXX")
        python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$tmp"
        chmod 600 "$tmp"
        mv "$tmp" "$f"
    fi
    cat "$f"
}
HOOKS_TOKEN=$(secret hooks_token)
GATEWAY_TOKEN=$(secret gateway_token)
AUTODEV_UI_TOKEN=$(secret ui_token)
export AUTODEV_HOOKS_TOKEN="$HOOKS_TOKEN"
export AUTODEV_UI_TOKEN

# OpenClaw binary: baked at image build when the OPENCLAW_VERSION build arg
# was set (the default; MIT license permits redistribution, see
# deploy/README.md). The no-bake image variant installs the same pin into the
# /data volume on first boot.
export PATH="$DATA/openclaw-npm/bin:$PATH"
if ! command -v openclaw >/dev/null 2>&1; then
    [ -n "${OPENCLAW_VERSION:-}" ] || die "openclaw is not baked into this image and OPENCLAW_VERSION is unset; rebuild with the default build args or set OPENCLAW_VERSION"
    say "first boot (no-bake image): installing openclaw@$OPENCLAW_VERSION into $DATA/openclaw-npm"
    npm install -g --prefix "$DATA/openclaw-npm" "openclaw@$OPENCLAW_VERSION"
    command -v openclaw >/dev/null 2>&1 || die "openclaw install failed"
fi

# Config render/reconcile. First boot renders the golden config fresh; every
# later boot reconciles the persisted config toward the current image's
# template. Reconciling (not just first-boot rendering) is what lets an image
# upgrade that changes the template self-heal: without it, a persisted config
# that lacks a newly-required key dead-ends at install.sh's owned-mode
# validation and the doctor's template_conformance check, crash-looping the
# container. Reconcile forces every key the template pins and preserves every
# key it does not (operator-added providers, OpenClaw's runtime bookkeeping);
# it also re-applies changed *_MODEL env values, which first-boot-only
# rendering ignored. Both paths go through openclaw_template (never a
# hand-rolled sed) so the substitution/matching contract lives in one place;
# writes go through atomic_io.
say "rendering/reconciling $OPENCLAW_ROOT/openclaw.json against the golden template"
HOOKS_TOKEN="$HOOKS_TOKEN" GATEWAY_TOKEN="$GATEWAY_TOKEN" python3 - <<'PY'
import json
import os
from autodev.installer.openclaw_template import (
    reconcile_config_to_template,
    render_template_text,
    template_path,
)
from autodev.pipeline.atomic_io import write_json_atomic, write_text_atomic

target = os.path.join(os.environ["OPENCLAW_ROOT"], "openclaw.json")
with open(template_path(os.environ["AUTODEV_REPO_PATH"]), encoding="utf-8") as f:
    rendered = render_template_text(f.read(), dict(os.environ))

existing = None
if os.path.exists(target):
    try:
        with open(target, encoding="utf-8") as f:
            existing = json.load(f)
    except ValueError:
        existing = None  # corrupt persisted config: re-render fresh, do not crash-loop

if existing is None:
    write_text_atomic(target, rendered)
    print("[lullabeast] openclaw.json rendered fresh")
else:
    reconciled = reconcile_config_to_template(json.loads(rendered), existing)
    if reconciled == existing:
        print("[lullabeast] openclaw.json already conformant with the template (no change)")
    else:
        write_json_atomic(target, reconciled)
        print("[lullabeast] openclaw.json reconciled toward the current template")
PY

# Pre-seed /app/.env before install.sh runs so its merge (which never
# overwrites an existing key) adopts the container paths and the persisted
# tokens instead of generating bare-metal defaults.
python3 - <<'PY'
import os
from autodev.installer.setup_helpers import merge_dotenv_missing_keys
merge_dotenv_missing_keys(
    os.path.join(os.environ["AUTODEV_REPO_PATH"], ".env"),
    {
        "OPENCLAW_ROOT": os.environ["OPENCLAW_ROOT"],
        "AUTODEV_REPO_PATH": os.environ["AUTODEV_REPO_PATH"],
        "AUTODEV_PIPELINE_ROOT": os.environ["AUTODEV_PIPELINE_ROOT"],
        "AUTODEV_HOOKS_TOKEN": os.environ["AUTODEV_HOOKS_TOKEN"],
        "AUTODEV_UI_TOKEN": os.environ["AUTODEV_UI_TOKEN"],
    },
)
PY

# Seed the UI port into ui/config.json so the doctor's ports check probes the
# port the server actually binds. install.sh preserves every key it does not
# own, so this survives the owned-mode run below.
UI_PORT="$UI_PORT" python3 - <<'PY'
import json, os
from autodev.pipeline.atomic_io import write_json_atomic
path = os.path.join(os.environ["AUTODEV_REPO_PATH"], "ui", "config.json")
cfg = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except ValueError:
        cfg = {}
cfg["port"] = int(os.environ["UI_PORT"])
write_json_atomic(path, cfg)
PY

# Git identity: install.sh fails fast without one (the pipeline commits in
# project repos). Container-scoped default; override via GIT_USER_NAME /
# GIT_USER_EMAIL in deploy/.env.
if [ -z "$(git config --global user.name 2>/dev/null || true)" ]; then
    git config --global user.name "${GIT_USER_NAME:-Lullabeast}"
fi
if [ -z "$(git config --global user.email 2>/dev/null || true)" ]; then
    git config --global user.email "${GIT_USER_EMAIL:-lullabeast@container.invalid}"
fi

# ── 3. Gateway up, then install.sh (the provisioning brain) ─────────────────
GATEWAY_PID=""
start_gateway() {
    openclaw gateway run &
    GATEWAY_PID=$!
}
stop_gateway() {
    [ -n "$GATEWAY_PID" ] || return 0
    kill "$GATEWAY_PID" 2>/dev/null || true
    wait "$GATEWAY_PID" 2>/dev/null || true
    GATEWAY_PID=""
}
wait_for_gateway() {
    local i
    for i in $(seq 1 120); do
        if python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(0.5)
try:
    s.connect(('127.0.0.1', int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
finally:
    s.close()
" "$GATEWAY_PORT" 2>/dev/null; then
            return 0
        fi
        sleep 0.5
    done
    die "gateway did not listen on port $GATEWAY_PORT within 60s"
}

say "starting OpenClaw gateway (bootstrap)"
start_gateway
wait_for_gateway

# Owned mode: overwrite-everything, zero prompts, any warning is fatal.
# --skip-playwright because the image already bakes the exact Chromium build
# @playwright/mcp drives and the golden template registers the MCP server;
# step 12 would only re-download the browser on every boot.
say "running install.sh --owned-openclaw (every boot; the image repo is the source of truth)"
bash "$APP/install.sh" --owned-openclaw --skip-playwright

# ── 4. Gateway restart + doctor ──────────────────────────────────────────────
# The gateway reads the plugin bundle and agent registrations at start, so the
# bundle install.sh just deployed needs a restart to actually load.
say "restarting gateway to load the deployed plugin bundle"
stop_gateway
start_gateway
wait_for_gateway

FIRST_BOOT_MARKER="$DATA/.first-boot-doctor-done"
DOCTOR_FLAGS=()
if [ ! -f "$FIRST_BOOT_MARKER" ]; then
    # --live adds the webhook ping, which creates one real (tiny, billable)
    # agent session; that validates the provider API key end to end exactly
    # once. Mark the ping spent BEFORE running the doctor: otherwise a doctor
    # FAIL (die below) leaves the marker unwritten, and every container restart
    # re-fires the billable --live ping in a crash loop. The non-live doctor
    # still gates every subsequent boot, so a real problem still fails loudly.
    DOCTOR_FLAGS+=(--live)
    touch "$FIRST_BOOT_MARKER"
fi
say "running doctor${DOCTOR_FLAGS[0]:+ ${DOCTOR_FLAGS[*]}}"
DOCTOR_EXIT=0
(cd "$APP" && OWNED_OPENCLAW=1 python3 -m autodev.installer.doctor "${DOCTOR_FLAGS[@]}") || DOCTOR_EXIT=$?
case "$DOCTOR_EXIT" in
    0|2) : ;;
    *) die "doctor reports failing checks (exit $DOCTOR_EXIT); see the FAIL lines above" ;;
esac

# ── 5. Supervise ─────────────────────────────────────────────────────────────
UI_PID=""
HB_PID=""
SC_PID=""
shutdown() {
    trap - TERM INT
    say "shutting down supervised processes"
    local pid
    for pid in "$UI_PID" "$HB_PID" "$SC_PID" "$GATEWAY_PID"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap 'shutdown; exit 0' TERM INT

# The UI server binds 0.0.0.0 INSIDE the container: compose publishes the
# port to the host's loopback only, and connections arriving through docker's
# proxy carry a non-loopback source address, which the server refuses unless
# AUTODEV_UI_TOKEN is set (it always is here; exported above).
python3 -m uvicorn ui.server:app --host 0.0.0.0 --port "$UI_PORT" &
UI_PID=$!

# Host installs run these from cron; a container has no crontab, so they run
# as supervised sleep loops instead (same scripts, unchanged).
(
    while :; do
        python3 "$APP/autodev/pipeline/heartbeat_cron.py" || say "heartbeat_cron exited nonzero (next pass in 30m)"
        sleep 1800
    done
) &
HB_PID=$!
(
    while :; do
        python3 "$APP/autodev/pipeline/session_cleanup.py" || say "session_cleanup exited nonzero (next pass in 24h)"
        sleep 86400
    done
) &
SC_PID=$!

echo
echo "=================================================================="
echo "  Lullabeast is up."
echo
echo "  Dashboard:  http://127.0.0.1:${UI_PORT}/?token=${AUTODEV_UI_TOKEN}"
echo
echo "  (Open it on the machine running docker; the port is published to"
echo "  the host's loopback only. Spend warning: agent pipelines are"
echo "  token-hungry and bills are real; watch the Monitor's cost strip"
echo "  on your first runs.)"
echo "=================================================================="
echo

rc=0
wait -n || rc=$?
say "a supervised process exited (rc=$rc); stopping the container"
shutdown
exit "$rc"
