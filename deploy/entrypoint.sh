#!/usr/bin/env bash
# deploy/entrypoint.sh - Lullabeast single-container boot.
#
# Boot sequence:
#   1. Validate the env contract. A provider API key can arrive three ways:
#      deploy/.env (headless path), the persisted /data/secrets/provider.env
#      (written by the dashboard setup screen), or not at all. Neither present
#      (and not OFFLINE=1) is no longer fatal: the container boots into SETUP
#      MODE, provisions everything, and waits for the dashboard to supply the
#      key. OFFLINE=1 remains the keyless CI/smoke mode.
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
#      gateway start), then run the doctor: --live on first boot only, and
#      deferred entirely in setup mode (like OFFLINE=1) until a key exists.
#   5. Print the dashboard URL + token, then run the lifetime watch loop
#      over four processes: gateway, UI server, heartbeat loop,
#      session-cleanup loop. The orchestrator is NOT supervised; the UI
#      server spawns it per run. Any supervised process dying tears the
#      container down (compose's restart policy recovers it). The same loop
#      applies configuration changes: in setup mode it waits for the
#      dashboard to drop the key file (restart gateway, deferred live
#      doctor, clear the setup marker), and for the container's lifetime it
#      consumes the apply-request marker the dashboard touches after editing
#      provider.env (re-render config, restart gateway, advisory doctor).
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
# Host-published gateway port. The gateway always listens on 18789 inside the
# container; the dev compose file publishes it on a different host port and
# sets GATEWAY_PUBLISHED_PORT so the boot banner and the dashboard's Settings
# link point where the host can actually reach it.
GATEWAY_LINK_PORT="${GATEWAY_PUBLISHED_PORT:-$GATEWAY_PORT}"
# DEV_MODE=1 (set by deploy/docker-compose.dev.yml): the repo at /app is a
# bind-mounted working tree, the UI server hot-reloads, and test dependencies
# are installed at boot. Everything else (provisioning, install, doctor,
# supervision) is identical to a user deploy on purpose.
DEV_MODE="${DEV_MODE:-0}"

say() { echo "[lullabeast] $*"; }
die() { echo "[lullabeast] FATAL: $*" >&2; exit 1; }

cd "$APP"

if [ "$DEV_MODE" = "1" ]; then
    say "DEV MODE: /app is a bind-mounted working tree. The gitignored .env and"
    say "ui/config.json in it are container-owned from here on (path and token"
    say "keys are rewritten every boot); see deploy/README.md, Development container."
fi

# ── 1. Env contract ──────────────────────────────────────────────────────────
# A provider can arrive several ways, with per-variable precedence:
#   1. deploy/.env (env vars already set): the headless / power-user path.
#      This is a cloud key (OPENROUTER_API_KEY / ANTHROPIC_API_KEY) OR a local
#      model server (LOCAL_MODEL_URL, e.g. http://host.docker.internal:11434).
#      A variable set here is pinned for the container's lifetime.
#   2. /data/secrets/provider.env: written by the dashboard (the setup screen,
#      and settings saves after setup). Loaded per variable: pinned variables
#      are skipped, every other assignment applies. The watch loop re-reads
#      the file on an apply request, so dashboard edits land without a
#      container restart.
#   3. Neither: SETUP MODE. The container provisions fully and the dashboard
#      collects a key or a local-model URL. Agents cannot run until it does.
# ANTHROPIC_API_KEY is still accepted here (B1-enforcement) but never documented.
# OFFLINE=1 (CI/smoke only) waives the provider requirement entirely and skips
# the billable probes so CI can build+doctor the image on every deploy-file
# change.
PROVIDER_KEY_FILE="$DATA/secrets/provider.env"
# The variables the dashboard may write into provider.env. Parsing is
# allowlisted to this set: the file is read line by line, never executed.
PROVIDER_ENV_VARS="ANTHROPIC_API_KEY OPENROUTER_API_KEY LOCAL_MODEL_URL \
LOCAL_MODEL_MAX_TOKENS LOCAL_MODEL_REASONING LOCAL_MODEL_CONTEXT_WINDOW \
LOCAL_MODEL_VISION PLANNER_MODEL EXECUTOR_MODEL REVIEWER_MODEL PRD_MODEL \
ROADMAP_MODEL ESCALATION_MODEL PROVIDER_SETUP_SKIPPED"
ENV_PINNED_VARS=""
for _v in $PROVIDER_ENV_VARS; do
    if [ -n "${!_v:-}" ]; then ENV_PINNED_VARS="$ENV_PINNED_VARS $_v"; fi
done
unset _v

source_provider_env() {
    # Values are never echoed: the file holds the provider key with
    # restrictive perms.
    [ -s "$PROVIDER_KEY_FILE" ] || return 0
    local line key val
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        key="${line%%=*}"
        val="${line#*=}"
        # A CRLF file would export an invisible trailing \r inside the value
        # (an API key that "looks right" but fails auth).
        val="${val%$'\r'}"
        case " $PROVIDER_ENV_VARS " in
            *" $key "*) : ;;
            *) continue ;;
        esac
        case " $ENV_PINNED_VARS " in
            *" $key "*) continue ;;
        esac
        export "$key=$val"
    done < "$PROVIDER_KEY_FILE"
}
source_provider_env

OFFLINE="${OFFLINE:-0}"
SETUP_MODE=0
if [ "$OFFLINE" = "1" ]; then
    echo "=================================================================="
    echo "  OFFLINE=1: CI/smoke mode."
    echo "  The provider requirement and the one-time --live doctor probe are"
    echo "  skipped. Agents CANNOT run without a provider; this mode is for"
    echo "  CI image smoke tests only, never for a real deployment."
    echo "=================================================================="
elif [ -n "${PROVIDER_SETUP_SKIPPED:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] \
    && [ -z "${OPENROUTER_API_KEY:-}" ] && [ -z "${LOCAL_MODEL_URL:-}" ]; then
    # The operator explicitly skipped provider setup from the welcome screen
    # (persisted in provider.env, sourced above): models and providers are
    # managed by hand in OpenClaw. Not setup mode (the welcome screen must
    # never reappear), but agents cannot run until OpenClaw carries a provider.
    echo "[lullabeast] provider setup was skipped: models are managed manually"
    echo "[lullabeast] in OpenClaw. Agents cannot run until a provider is"
    echo "[lullabeast] configured there (gateway UI, linked from Settings)."
elif [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${OPENROUTER_API_KEY:-}" ] \
    && [ -z "${LOCAL_MODEL_URL:-}" ]; then
    SETUP_MODE=1
    echo "=================================================================="
    echo "  SETUP MODE: no provider API key found yet."
    echo "  The container is provisioning everything except agent capability."
    echo "  Open the dashboard (URL printed at the end of boot) and enter your"
    echo "  provider key in the setup screen. The container wires it and"
    echo "  validates it automatically; agents CANNOT run until you do."
    echo "  A local model server is an alternative to a cloud key: wire one"
    echo "  from the dashboard setup screen, or set LOCAL_MODEL_URL (e.g."
    echo "  http://host.docker.internal:11434) in deploy/.env before boot."
    echo "=================================================================="
fi

# ── 2. /data layout, secrets, first-boot config render ──────────────────────
mkdir -p "$OPENCLAW_ROOT" "$AUTODEV_PIPELINE_ROOT" "$DATA/projects"
mkdir -p "$DATA/secrets"
chmod 700 "$DATA/secrets"
ln -sfn "$OPENCLAW_ROOT" "$HOME/.openclaw"

# Config-apply marker: the dashboard touches this file after editing
# provider.env; the watch loop consumes it, re-renders config, and restarts
# the gateway. A stale marker from a previous run is cleared here: boot
# already applies everything fresh.
APPLY_REQUEST_FILE="$DATA/secrets/apply.request"
rm -f "$APPLY_REQUEST_FILE"

# Setup-mode marker: a file the dashboard and doctor read to know a key is
# still owed. Written here (after the /data mkdirs) when we entered setup mode,
# removed when a key is present so a later keyed boot clears a stale marker.
SETUP_MARKER="$DATA/.setup-mode"
if [ "$SETUP_MODE" = "1" ]; then
    touch "$SETUP_MARKER"
else
    rm -f "$SETUP_MARKER"
fi

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
#
# Wrapped in a function so the setup-watch loop can re-run it after the
# dashboard supplies a key: a dashboard-set *_MODEL value (or a newly sourced
# provider) must reach openclaw.json before the gateway restart. Idempotent.
render_reconcile_config() {
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

# A remapped host gateway port (dev stack) needs its origin in the Control UI
# allow-list or the browser session hits an origin prompt. Additive and
# reconcile-safe: the template matches scalar lists by membership, so extra
# origins survive every later boot.
published = (os.environ.get("GATEWAY_PUBLISHED_PORT") or "").strip()
if published.isdigit() and published != "18789":
    with open(target, encoding="utf-8") as f:
        cfg = json.load(f)
    origins = (
        cfg.setdefault("gateway", {})
        .setdefault("controlUi", {})
        .setdefault("allowedOrigins", [])
    )
    wanted = [f"http://127.0.0.1:{published}", f"http://localhost:{published}"]
    if any(o not in origins for o in wanted):
        origins.extend(o for o in wanted if o not in origins)
        write_json_atomic(target, cfg)
        print(f"[lullabeast] Control UI origins extended for published gateway port {published}")
PY
}

# Local-model wiring / detection. Two behaviors, both idempotent and both safe
# to re-run in the setup-watch loop:
#   (a) LOCAL_MODEL_URL set: normalize it, best-effort probe its /v1/models plus
#       the server's family metadata endpoint (context window, reasoning,
#       vision), build an enriched models.providers.local entry, and merge it
#       into openclaw.json (via the shared local_models + atomic_io helpers).
#       The merge preserves same-id fields a prior boot or a hand-edit already
#       set; LOCAL_MODEL_MAX_TOKENS / LOCAL_MODEL_REASONING (applied to the
#       *_MODEL-referenced local models, or all when no knob is local) have the
#       last word. We print each discovered model in the exact "local/<id>"
#       form the *_MODEL knobs need, with the values it was wired with: every
#       assumption is loud, because a silently under-specified entry truncates
#       real pipeline turns. A failed probe still writes the provider entry
#       (empty models) with a loud bind-hint warning; a malformed URL is a loud
#       warning and a skip, never a boot crash.
#   (b) LOCAL_MODEL_URL unset and setup mode: probe the known local-server ports
#       on the docker bridge and, for anything that answers, print the exact
#       LOCAL_MODEL_URL line to add (or note the dashboard's one-click wiring).
#       Detection NEVER auto-writes config: wiring requires the explicit
#       LOCAL_MODEL_URL, because role assignment via the *_MODEL knobs is the
#       user's call and port 8080 is a common false positive. If nothing
#       answers we print nothing extra (the bind hint lives in the docs and the
#       setup screen; a keyless boot is not spammed).
wire_or_probe_local_models() {
    SETUP_MODE="$SETUP_MODE" python3 - <<'PY'
import json
import os

from autodev.installer import local_models
from autodev.pipeline.atomic_io import write_json_atomic

url = (os.environ.get("LOCAL_MODEL_URL") or "").strip()
target = os.path.join(os.environ["OPENCLAW_ROOT"], "openclaw.json")


def _load_config():
    if not os.path.exists(target):
        return {}
    try:
        with open(target, encoding="utf-8") as f:
            return json.load(f)
    except ValueError:
        return {}


if url:
    try:
        base = local_models.normalize_local_base_url(url)
    except ValueError as exc:
        print(f"[lullabeast] LOCAL_MODEL_URL is malformed ({exc}); skipping local-model wiring")
    else:
        models = local_models.probe_openai_models(base)
        metadata = local_models.probe_model_metadata(base, models or [])
        entry = local_models.build_local_provider_entry(base, models or [], metadata)
        merged = local_models.merge_local_provider(_load_config(), entry)
        # Explicit overrides win over probes and prior edits; scope them to the
        # role-referenced local models when any *_MODEL knob points at local/.
        from autodev.installer.openclaw_template import TEMPLATE_MODEL_DEFAULTS
        max_tokens = local_models.parse_positive_int(os.environ.get("LOCAL_MODEL_MAX_TOKENS"))
        reasoning = local_models.parse_bool_flag(os.environ.get("LOCAL_MODEL_REASONING"))
        context_window = local_models.parse_positive_int(os.environ.get("LOCAL_MODEL_CONTEXT_WINDOW"))
        vision = local_models.parse_bool_flag(os.environ.get("LOCAL_MODEL_VISION"))
        targets = [
            v[len("local/"):]
            for v in ((os.environ.get(k) or "").strip() for k in TEMPLATE_MODEL_DEFAULTS)
            if v.startswith("local/")
        ]
        if any(o is not None for o in (max_tokens, reasoning, context_window, vision)):
            prov = merged["models"]["providers"][local_models.LOCAL_PROVIDER_ID]
            merged["models"]["providers"][local_models.LOCAL_PROVIDER_ID] = (
                local_models.apply_local_model_overrides(
                    prov,
                    max_tokens=max_tokens,
                    reasoning=reasoning,
                    context_window=context_window,
                    vision=vision,
                    target_ids=targets,
                )
            )
        write_json_atomic(target, merged)
        if models:
            final = merged["models"]["providers"][local_models.LOCAL_PROVIDER_ID]["models"]
            print(f"[lullabeast] local model provider wired from LOCAL_MODEL_URL ({base}):")
            unconfirmed = []
            for m in final:
                print(f"[lullabeast]   local/{m['id']}  ({local_models.summarize_model_entry(m)})")
                if "reasoning" not in m:
                    unconfirmed.append(m["id"])
            print("[lullabeast] point a role at one with its *_MODEL knob, e.g. "
                  f"ESCALATION_MODEL=local/{models[0]}")
            if unconfirmed:
                print("[lullabeast] reasoning could not be detected for: "
                      + ", ".join(unconfirmed))
                print("[lullabeast] if one of these is a reasoning model, set "
                      "LOCAL_MODEL_REASONING=1 in deploy/.env (or confirm it in the "
                      "dashboard setup screen); leaving it unset degrades its output.")
        else:
            print(f"[lullabeast] local model provider wired from LOCAL_MODEL_URL ({base}), "
                  "but its /v1/models probe returned no models.")
            print(f"[lullabeast] {local_models.BIND_HINT}")
            print('[lullabeast] see deploy/README.md "Local models on the host" for the full contract.')
elif os.environ.get("SETUP_MODE") == "1":
    found = local_models.discover_local_servers()
    for hit in found:
        models = hit["models"]
        summary = ", ".join(f"local/{m}" for m in models) if models else "(no models loaded yet)"
        print(f"[lullabeast] detected {hit['name']} at {hit['url']} with models: {summary}")
        print(f"[lullabeast]   to wire it, add LOCAL_MODEL_URL={hit['url']} to deploy/.env "
              "and reboot, or use the dashboard setup screen's one-click wiring.")
PY
}

render_reconcile_config
wire_or_probe_local_models

# Assert the container-owned keys in /app/.env before install.sh runs, so its
# merge (which never overwrites an existing key) adopts the container paths
# and the persisted tokens. Forced, not merged: on a dev bind mount the
# working tree's .env carries bare-metal paths and stale tokens that would
# otherwise poison config. Every other line (tuning knobs) is preserved.
python3 - <<'PY'
import os
from autodev.installer.setup_helpers import force_dotenv_keys
result = force_dotenv_keys(
    os.path.join(os.environ["AUTODEV_REPO_PATH"], ".env"),
    {
        "OPENCLAW_ROOT": os.environ["OPENCLAW_ROOT"],
        "AUTODEV_REPO_PATH": os.environ["AUTODEV_REPO_PATH"],
        "AUTODEV_PIPELINE_ROOT": os.environ["AUTODEV_PIPELINE_ROOT"],
        "AUTODEV_HOOKS_TOKEN": os.environ["AUTODEV_HOOKS_TOKEN"],
        "AUTODEV_UI_TOKEN": os.environ["AUTODEV_UI_TOKEN"],
    },
)
if result.startswith("error:"):
    # Unwritable /app (dev bind mount with a mismatched uid): the working
    # tree's bare-metal .env would poison every derived path. Fail the boot
    # here, where the cause is legible, not downstream.
    raise SystemExit(f"[lullabeast] FATAL: cannot write /app/.env ({result}); "
                     "on a dev bind mount rebuild with --build-arg LULLABEAST_UID=$(id -u)")
PY

# Seed the UI port and the container-only setup paths into ui/config.json.
# The port lets the doctor's ports check probe the port the server actually
# binds. The setup keys tell the server surfaces (provider-status,
# provider-key, import-demo, local-models) and the doctor's provider_key check
# where the key file, the setup marker, and the projects dir live, and which
# host to probe for a local model server; bare-metal installs leave them unset
# and those surfaces degrade to "unsupported". install.sh preserves every key
# it does not own, so all of this survives the owned-mode run below.
# The container-structural keys (repo path, roots, hooks_url) are asserted
# too: on a dev bind mount the working tree's ui/config.json carries
# bare-metal values that would otherwise poison every path the server derives.
UI_PORT="$UI_PORT" \
PROVIDER_KEY_FILE="$PROVIDER_KEY_FILE" \
SETUP_MARKER="$SETUP_MARKER" \
APPLY_REQUEST_FILE="$APPLY_REQUEST_FILE" \
DATA_PROJECTS="$DATA/projects" \
python3 - <<'PY'
import json, os
from autodev.installer.local_models import DEFAULT_BRIDGE_HOST
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
cfg["provider_key_path"] = os.environ["PROVIDER_KEY_FILE"]
cfg["setup_marker_path"] = os.environ["SETUP_MARKER"]
cfg["apply_request_path"] = os.environ["APPLY_REQUEST_FILE"]
cfg["projects_dir"] = os.environ["DATA_PROJECTS"]
cfg["autodev_repo_path"] = os.environ["AUTODEV_REPO_PATH"]
cfg["openclaw_root"] = os.environ["OPENCLAW_ROOT"]
cfg["autodev_pipeline_root"] = os.environ["AUTODEV_PIPELINE_ROOT"]
# The gateway always listens on 18789 inside the container.
cfg["hooks_url"] = "http://localhost:18789/hooks/agent"
# Host-published gateway port, when remapped (dev stack): the Settings
# screen's gateway link must point where the host can reach it.
published = (os.environ.get("GATEWAY_PUBLISHED_PORT") or "").strip()
if published.isdigit():
    cfg["gateway_published_port"] = int(published)
else:
    cfg.pop("gateway_published_port", None)
# The docker bridge host the UI server's local-model setup surface probes; its
# presence is what gates that surface (bare metal leaves it unset).
cfg["local_model_probe_host"] = DEFAULT_BRIDGE_HOST
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

# Heal permissions on any previously-deployed extensions before the gateway
# scans them: copies installed from a bind-mounted repo (Windows/macOS
# Docker) arrived world-writable and OpenClaw blocks such plugins, which
# fails owned-mode validation and crash-loops the boot. install.sh now
# stages installs with sane permissions; this covers trees deployed by
# earlier boots.
if [ -d "$OPENCLAW_ROOT/extensions" ]; then
    chmod -R go-w "$OPENCLAW_ROOT/extensions" 2>/dev/null \
        || say "WARNING: could not normalize permissions under $OPENCLAW_ROOT/extensions"
fi

say "starting OpenClaw gateway (bootstrap)"
start_gateway
wait_for_gateway

# Owned mode: overwrite-everything, zero prompts, any warning is fatal.
# --skip-playwright because the image already bakes the exact Chromium build
# @playwright/mcp drives and the golden template registers the MCP server;
# step 12 would only re-download the browser on every boot.
say "running install.sh --owned-openclaw (every boot; the image repo is the source of truth)"
bash "$APP/install.sh" --owned-openclaw --skip-playwright

# Dev stacks run the test suites in-container; the user image stays lean.
if [ "$DEV_MODE" = "1" ]; then
    say "DEV MODE: installing test dependencies (requirements-dev.txt)"
    pip install -q -r "$APP/requirements-dev.txt" \
        || say "WARNING: requirements-dev.txt install failed; pytest may be unavailable"
fi

# ── 4. Gateway restart + doctor ──────────────────────────────────────────────
# The gateway reads the plugin bundle and agent registrations at start, so the
# bundle install.sh just deployed needs a restart to actually load.
say "restarting gateway to load the deployed plugin bundle"
stop_gateway
start_gateway
wait_for_gateway

FIRST_BOOT_MARKER="$DATA/.first-boot-doctor-done"
DOCTOR_FLAGS=()
if [ "$OFFLINE" = "1" ] || [ "$SETUP_MODE" = "1" ]; then
    # CI/smoke mode and setup mode both defer the live (billable) probe and
    # leave the first-boot marker unwritten, so the one --live ping still fires
    # later: for OFFLINE, on the next real boot; for setup mode, in the
    # setup-watch loop right after the dashboard supplies the key. The non-live
    # doctor below still gates the boot.
    :
elif [ ! -f "$FIRST_BOOT_MARKER" ]; then
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
UVICORN_ARGS=(--host 0.0.0.0 --port "$UI_PORT")
if [ "$DEV_MODE" = "1" ]; then
    # Hot-reload the UI server on working-tree edits. Only the server needs
    # it: the orchestrator and gate scripts are spawned fresh per run, agent
    # files redeploy on the next boot's install.
    UVICORN_ARGS+=(--reload --reload-dir "$APP/ui" --reload-dir "$APP/autodev"
        --reload-exclude "*/node_modules/*")
fi
python3 -m uvicorn ui.server:app "${UVICORN_ARGS[@]}" &
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

dashboard_url() { echo "http://127.0.0.1:${UI_PORT}/?token=${AUTODEV_UI_TOKEN}"; }

# Print the Dashboard line in bold green so it is the unmistakable last thing
# on screen. The reset (\033[0m) at end of line keeps gateway logs that follow
# uncolored. dashboard_url() is not a secret name, so this passes the
# echo/say/die secret-echo lint (printf is not matched anyway).
banner_dashboard_line() {
    printf '\033[1;32m  Dashboard:  %s\033[0m\n' "$(dashboard_url)"
    echo "  OpenClaw gateway (model management): http://127.0.0.1:${GATEWAY_LINK_PORT}"
    if [ "$DEV_MODE" = "1" ]; then
        echo "  DEV MODE: /app is your working tree; the UI server hot-reloads."
    fi
}

banner_up() {
    echo
    echo "=================================================================="
    echo "  Lullabeast is up."
    echo
    echo "  (Open the dashboard URL below on the machine running docker; the"
    echo "  port is published to the host's loopback only. Spend warning:"
    echo "  agent pipelines are token-hungry and bills are real; watch the"
    echo "  Monitor's cost strip on your first runs.)"
    echo "=================================================================="
    echo
    banner_dashboard_line
    echo
}

banner_setup_mode() {
    echo
    echo "=================================================================="
    echo "  SETUP MODE: Lullabeast is up but has no provider key yet."
    echo
    echo "  Open the dashboard and enter your provider key in the setup"
    echo "  screen. Agents CANNOT run until the key is entered there; once"
    echo "  it is, the container wires and validates it automatically."
    echo "=================================================================="
    echo
    banner_dashboard_line
    echo
}

banner_unlocked() {
    echo
    echo "=================================================================="
    echo "  UNLOCKED: provider key accepted and validated. Agents can run."
    echo "=================================================================="
    echo
    banner_dashboard_line
    echo
}

if [ "$SETUP_MODE" = "1" ]; then
    banner_setup_mode
    say "setup mode: waiting for the dashboard to supply a provider key at $PROVIDER_KEY_FILE"
else
    banner_up
fi

# ── 5b. Watch loop (lifetime supervision + config apply) ────────────────────
# One loop supervises the four processes and applies configuration changes
# for the container's lifetime. Two triggers:
#   * setup unlock: in setup mode, the dashboard writes the provider key to
#     $PROVIDER_KEY_FILE; detected by content (the pre-marker contract the
#     setup screen already speaks).
#   * apply request: the dashboard touches $APPLY_REQUEST_FILE after editing
#     provider.env (settings saves after setup). The marker is consumed
#     before work starts, so a request landing mid-apply coalesces into one
#     more idempotent pass instead of being lost.
# Both paths re-read provider.env (per-variable, deploy/.env pinned), re-run
# the config wiring, and restart the gateway: OpenClaw reads provider keys
# from its process environment and agent models from openclaw.json at start,
# so the restart is the whole apply mechanism. New sessions bind the new
# config; running sessions keep the model they were created with.
# This runs in the MAIN script, not a supervised background child: the
# gateway must be restarted by the process that owns its pid (a watcher's
# gateway would be the watcher's child, outside supervision). The liveness
# check re-reads GATEWAY_PID every iteration because restarts change it.
while :; do
    for pid in "$UI_PID" "$HB_PID" "$SC_PID" "$GATEWAY_PID"; do
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            rc=0
            wait "$pid" 2>/dev/null || rc=$?
            say "a supervised process (pid $pid) exited (rc=$rc); stopping the container"
            shutdown
            exit "$rc"
        fi
    done
    APPLY=0
    if [ "$SETUP_MODE" = "1" ] && [ -s "$PROVIDER_KEY_FILE" ]; then
        say "provider key received from the dashboard; applying configuration"
        APPLY=1
    elif [ -f "$APPLY_REQUEST_FILE" ]; then
        say "configuration apply requested by the dashboard"
        APPLY=1
    fi
    if [ "$APPLY" = "1" ]; then
        rm -f "$APPLY_REQUEST_FILE"
        source_provider_env
        # Re-run the config wiring before the restart so anything the
        # dashboard wrote lands in openclaw.json: a *_MODEL value takes
        # effect via render_reconcile_config, and a LOCAL_MODEL_URL lands as
        # the models.providers.local entry via wire_or_probe_local_models.
        # SETUP_MODE=0 keeps the pass from re-running local-server discovery.
        render_reconcile_config
        SETUP_MODE=0 wire_or_probe_local_models
        stop_gateway
        start_gateway
        wait_for_gateway
        if [ "$SETUP_MODE" = "1" ]; then
            if [ -n "${PROVIDER_SETUP_SKIPPED:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] \
                && [ -z "${OPENROUTER_API_KEY:-}" ] && [ -z "${LOCAL_MODEL_URL:-}" ]; then
                # Skip-provider unlock: no key to validate, so no --live doctor
                # and the first-boot marker stays unwritten, so a later real key
                # still gets its one billable ping (same contract as OFFLINE).
                say "provider setup skipped from the dashboard; models are managed manually in OpenClaw"
            else
                # Deferred first-boot --live doctor: the key exists now, so run
                # the one billable webhook ping. Mark it spent BEFORE running
                # (same crash-loop guard as the first-boot block above): a
                # doctor die must not re-fire the paid ping on the next boot.
                touch "$FIRST_BOOT_MARKER"
                say "running deferred first-boot doctor --live to validate the key"
                DOCTOR_EXIT=0
                (cd "$APP" && OWNED_OPENCLAW=1 python3 -m autodev.installer.doctor --live) || DOCTOR_EXIT=$?
                case "$DOCTOR_EXIT" in
                    0|2) : ;;
                    *) die "deferred live doctor reports failing checks (exit $DOCTOR_EXIT); see the FAIL lines above" ;;
                esac
            fi
            rm -f "$SETUP_MARKER"
            SETUP_MODE=0
            banner_unlocked
        else
            # Post-setup apply: advisory doctor only. A bad save must not tear
            # down a container that may have queued work; the dashboard's
            # Health card surfaces the same report.
            DOCTOR_EXIT=0
            (cd "$APP" && OWNED_OPENCLAW=1 python3 -m autodev.installer.doctor) || DOCTOR_EXIT=$?
            case "$DOCTOR_EXIT" in
                0|2) : ;;
                *) say "WARNING: doctor reports failing checks after config apply (exit $DOCTOR_EXIT); see the FAIL lines above" ;;
            esac
            say "configuration applied; gateway restarted"
        fi
    fi
    sleep 2
done
