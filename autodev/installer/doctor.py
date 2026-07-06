#!/usr/bin/env python3
"""Lullabeast doctor: one command that checks every documented silent-failure mode.

Read-only by design. The doctor never mutates anything; each red item carries a
``fix_hint`` telling the human (or the installer) what to run. Three consumers
share this module (DS-1):

  * CLI:      ``python -m autodev.installer.doctor [--json] [--live] [--quiet]``
  * Server:   ``GET /api/doctor`` in ``ui/server.py`` (``run_doctor(load_config())``)
  * Installer: install.sh runs the CLI as its final gate (all modes).

Exit codes (CLI): 0 all ok (warns allowed with ``--quiet``), 1 any fail,
2 warns only.

Every network/subprocess probe is bounded by ``DOCTOR_PROBE_TIMEOUT`` seconds
(default 5; feature-scoped env name per repo convention, no AUTODEV_ prefix).

The ``--live`` flag additionally performs the webhook POST ping. It is opt-in
because it creates a real OpenClaw agent session (the documented curl check
from the README quickstart, automated).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

from autodev.installer import setup_helpers
from autodev.installer.openclaw_template import (
    load_template,
    template_conformance_issues,
    template_path,
)
from autodev.installer.register_agent import AUTODEV_AGENT_IDS
from autodev.pipeline.env_resolvers import resolve_openclaw_root, resolve_pipeline_root

# ---------------------------------------------------------------------------
# OpenClaw version-compatibility table.
#
# The repo has a documented floor and documented known-bad releases; this used
# to live only in docs (SETUP.md "Known Compatible OpenClaw Version") and in
# session memory. It is now a checked table. A drift guard in
# tests/test_doctor_checks.py asserts MIN_OPENCLAW_VERSION agrees with the
# version string in SETUP.md; change them together.
# ---------------------------------------------------------------------------
MIN_OPENCLAW_VERSION = "2026.5.18"
KNOWN_BAD_OPENCLAW_VERSIONS: dict[str, str] = {
    # 2026.6.8 stripped the planner's exec tool and introduced a symlink-write
    # sandbox regression; pipelines wheelspin. Verified live 2026-06.
    "2026.6.8": "strips planner exec; pipeline agents cannot run gate scripts",
}

# Content-level marker proving the deployed plugin bundle was built from
# current source: the Ideas production-form session-key matcher. If this
# marker ever changes, update it here AND in install.sh step 11 AND in
# tests/test_install_sh_plugin_deploy.py (all three sites change together).
PLUGIN_BUNDLE_MARKER = "agent:[a-z0-9_-]+:ideas:"

REQUIRED_PLUGIN_HOOKS = {
    "agent_end",
    "before_agent_finalize",
    "model_call_started",
    "model_call_ended",
    "after_tool_call",
}

# PyYAML missing silently disables skill injection (SkillManager degrades with
# no crash), which is exactly the class of failure the doctor exists to catch.
PYTHON_DEPS = ("fastapi", "uvicorn", "aiohttp", "requests", "websocket", "yaml")

_STATUS_ORDER = ("ok", "warn", "fail", "skipped")

# Repo root: autodev/installer/doctor.py is 3 dirname calls from repo root
# (the depth rule in CLAUDE.md).
_REPO_ROOT_DEFAULT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def probe_timeout() -> float:
    """Bounded-probe timeout in seconds. Env DOCTOR_PROBE_TIMEOUT, default 5."""
    raw = (os.environ.get("DOCTOR_PROBE_TIMEOUT") or "").strip()
    try:
        val = float(raw)
    except ValueError:
        return 5.0
    if val <= 0:
        return 5.0
    return max(0.5, val)


@dataclass
class CheckResult:
    id: str
    title: str
    status: str  # "ok" | "warn" | "fail" | "skipped"
    detail: str = ""
    fix_hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in _STATUS_ORDER}
        for c in self.checks:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    @property
    def has_fail(self) -> bool:
        return any(c.status == "fail" for c in self.checks)

    @property
    def has_warn(self) -> bool:
        return any(c.status == "warn" for c in self.checks)

    def overall(self) -> str:
        if self.has_fail:
            return "fail"
        if self.has_warn:
            return "warn"
        return "ok"

    def exit_code(self, warns_ok: bool = False) -> int:
        if self.has_fail:
            return 1
        if self.has_warn:
            return 0 if warns_ok else 2
        return 0

    def to_dict(self) -> dict:
        return {
            "status": self.overall(),
            "counts": self.counts(),
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Shared probe helpers (all read-only, all bounded)
# ---------------------------------------------------------------------------

def _load_openclaw_json(path: str):
    """Return (data, error_code). data is a dict on success, else None."""
    if not os.path.isfile(path):
        return None, "no_file"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, "invalid_json"
    if not isinstance(data, dict):
        return None, "invalid_root"
    return data, ""


def _parse_version(text: str):
    m = re.search(r"(\d{4})\.(\d+)\.(\d+)", text or "")
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def _version_str(v) -> str:
    return ".".join(str(p) for p in v)


def _openclaw_cli(*args: str):
    """Run the openclaw CLI (bounded).

    Returns (rc, stdout, stderr), or (None, reason, "") when the CLI could not
    run at all. stderr rides along because some CLI builds print their banner
    (or the version line itself) there; a check that only reads stdout would
    silently degrade to "skipped" and defeat its guard.
    """
    exe = shutil.which("openclaw")
    if not exe:
        return None, "openclaw CLI not on PATH", ""
    try:
        proc = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=probe_timeout() * 4,  # node CLI cold start is slow; still bounded
        )
    except subprocess.TimeoutExpired:
        return None, "openclaw CLI timed out", ""
    except OSError as e:
        return None, f"openclaw CLI failed to start: {e}", ""
    return proc.returncode, proc.stdout, proc.stderr


def _git_config_value(key: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "config", "--global", key],
            capture_output=True,
            text=True,
            timeout=probe_timeout(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip()


def _tcp_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=probe_timeout()):
            return True
    except OSError:
        return False


def _hooks_host_port(hooks_url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(hooks_url or "http://localhost:18789/hooks/agent")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 18789)
    return host, port


def _http_get_json(url: str):
    """GET url, return (status_code, parsed_json_or_None). Raises nothing."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=probe_timeout()) as resp:
            body = resp.read(65536)
            code = resp.status
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, None
    try:
        return code, json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return code, None


# ---------------------------------------------------------------------------
# The check catalogue. Each check maps to a documented failure mode; see the
# DS-1 table in plans/Active/Cosidered-fable-tasks/deploy-simplification-roadmap.md.
# ---------------------------------------------------------------------------

def check_env_paths(config: dict) -> CheckResult:
    oc = config.get("openclaw_root") or ""
    repo = config.get("autodev_repo_path") or ""
    proot = config.get("autodev_pipeline_root") or ""
    problems: list[str] = []
    if not os.path.isdir(oc):
        problems.append(f"OPENCLAW_ROOT is not a directory: {oc or '(unset)'}")
    orch = os.path.join(repo, "autodev", "pipeline", "orchestrator.py")
    if not os.path.isfile(orch):
        problems.append(f"AUTODEV_REPO_PATH does not contain autodev/pipeline/orchestrator.py: {repo or '(unset)'}")
    if os.path.isdir(proot):
        if not os.access(proot, os.W_OK):
            problems.append(f"AUTODEV_PIPELINE_ROOT is not writable: {proot}")
    else:
        parent = os.path.dirname(proot.rstrip(os.sep)) or "/"
        if not (os.path.isdir(parent) and os.access(parent, os.W_OK)):
            problems.append(
                f"AUTODEV_PIPELINE_ROOT missing and parent not writable: {proot or '(unset)'}"
            )
    if problems:
        return CheckResult(
            "env_paths", "Core paths resolve", "fail", "; ".join(problems),
            "source .env (written by install.sh) or set OPENCLAW_ROOT / AUTODEV_REPO_PATH / AUTODEV_PIPELINE_ROOT",
        )
    return CheckResult(
        "env_paths", "Core paths resolve", "ok",
        f"openclaw_root={oc} repo={repo} pipeline_root={proot}",
    )


def check_python_deps(config: dict) -> CheckResult:
    import importlib.util

    missing = [m for m in PYTHON_DEPS if importlib.util.find_spec(m) is None]
    if missing:
        detail = "missing importable modules: " + ", ".join(missing)
        if "yaml" in missing:
            detail += " (PyYAML missing silently disables per-phase skill injection)"
        return CheckResult(
            "python_deps", "Python dependencies importable", "fail", detail,
            "pip install -r ui/requirements.txt (and PyYAML)",
        )
    return CheckResult(
        "python_deps", "Python dependencies importable", "ok",
        ", ".join(PYTHON_DEPS),
    )


def check_git_identity(config: dict) -> CheckResult:
    if not shutil.which("git"):
        return CheckResult(
            "git_identity", "Git present with identity", "fail",
            "git not found on PATH",
            "install git, then: git config --global user.name / user.email",
        )
    name = _git_config_value("user.name")
    email = _git_config_value("user.email")
    if not name or not email:
        return CheckResult(
            "git_identity", "Git present with identity", "fail",
            "git user.name / user.email not configured (the pipeline commits in project repos)",
            'git config --global user.name "Your Name" && git config --global user.email "you@example.com"',
        )
    return CheckResult("git_identity", "Git present with identity", "ok", f"{name} <{email}>")


def check_openclaw_json(config: dict) -> CheckResult:
    path = os.path.join(config.get("openclaw_root") or "", "openclaw.json")
    data, err = _load_openclaw_json(path)
    if data is None:
        detail = {
            "no_file": f"openclaw.json not found at {path}",
            "invalid_json": f"openclaw.json is not valid JSON: {path}",
            "invalid_root": "openclaw.json root is not a JSON object",
        }[err]
        return CheckResult(
            "openclaw_json", "openclaw.json parses", "fail", detail,
            "install/start OpenClaw; Lullabeast never creates openclaw.json",
        )
    return CheckResult("openclaw_json", "openclaw.json parses", "ok", path)


def check_openclaw_version(config: dict) -> CheckResult:
    # The gateway exposes no version endpoint over plain HTTP (verified on
    # 2026.6.11: / and /v1/models serve the Control SPA, /health carries no
    # version), so `openclaw --version` is the primary source.
    rc, out, err = _openclaw_cli("--version")
    if rc is None:
        return CheckResult(
            "openclaw_version", "OpenClaw version compatible", "skipped",
            f"could not determine version ({out})",
            "run `openclaw --version` manually and compare against SETUP.md's floor",
        )
    # Some CLI builds print the version (or banner noise) on stderr; falling
    # back keeps the floor/known-bad guard alive instead of skipping.
    ver = _parse_version(out or "") or _parse_version(err or "")
    if rc != 0 or ver is None:
        return CheckResult(
            "openclaw_version", "OpenClaw version compatible", "skipped",
            f"could not parse `openclaw --version` output (rc={rc}): {(out or err or '').strip()[:120]}",
            "run `openclaw --version` manually and compare against SETUP.md's floor",
        )
    ver_s = _version_str(ver)
    if ver_s in KNOWN_BAD_OPENCLAW_VERSIONS:
        return CheckResult(
            "openclaw_version", "OpenClaw version compatible", "fail",
            f"OpenClaw {ver_s} is a known-bad release: {KNOWN_BAD_OPENCLAW_VERSIONS[ver_s]}",
            "upgrade OpenClaw past the known-bad release (see the pin in the deploy roadmap)",
        )
    floor = _parse_version(MIN_OPENCLAW_VERSION)
    if ver < floor:
        return CheckResult(
            "openclaw_version", "OpenClaw version compatible", "fail",
            f"OpenClaw {ver_s} is older than the supported floor {MIN_OPENCLAW_VERSION}",
            f"upgrade OpenClaw to {MIN_OPENCLAW_VERSION} or newer",
        )
    return CheckResult("openclaw_version", "OpenClaw version compatible", "ok", f"OpenClaw {ver_s}")


def check_hooks_baseline(config: dict) -> CheckResult:
    path = os.path.join(config.get("openclaw_root") or "", "openclaw.json")
    issues = setup_helpers.openclaw_hooks_issues(path)
    if issues:
        return CheckResult(
            "hooks_baseline", "openclaw.json hooks baseline", "fail",
            "issues: " + ", ".join(issues),
            "./install.sh (step 8 patches hooks: enabled, token, allowRequestSessionKey, pipeline:/ideas: prefixes)",
        )
    return CheckResult("hooks_baseline", "openclaw.json hooks baseline", "ok")


def check_secret_sync(config: dict) -> CheckResult:
    oc_json = os.path.join(config.get("openclaw_root") or "", "openclaw.json")
    repo = config.get("autodev_repo_path") or ""
    ui_cfg = os.path.join(repo, "ui", "config.json")
    env_file = os.path.join(repo, ".env")
    r = setup_helpers.webhook_secret_sync_assess(oc_json, ui_cfg, env_file)
    code = r.summary_code()
    if code == "ok":
        return CheckResult("secret_sync", "Webhook Bearer secret in sync", "ok")
    if code == "no_hooks_token":
        return CheckResult(
            "secret_sync", "Webhook Bearer secret in sync", "fail",
            "hooks.token missing/empty in openclaw.json",
            "./install.sh (step 8 can generate hooks.token and sync .env / ui/config.json)",
        )
    return CheckResult(
        "secret_sync", "Webhook Bearer secret in sync", "fail",
        f"hooks.token vs ui/config.json vs .env: {code}",
        "sync AUTODEV_HOOKS_TOKEN in .env and hooks_token in ui/config.json to openclaw.json hooks.token (install.sh step 8)",
    )


def check_agents_registered(config: dict) -> CheckResult:
    path = os.path.join(config.get("openclaw_root") or "", "openclaw.json")
    data, err = _load_openclaw_json(path)
    if data is None:
        return CheckResult(
            "agents_registered", "All 6 Lullabeast agents registered", "fail",
            f"cannot read openclaw.json ({err})",
            "fix openclaw.json first (see openclaw_json check)",
        )
    agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
    lst = agents.get("list") if isinstance(agents.get("list"), list) else []
    present = {e.get("id") for e in lst if isinstance(e, dict)}
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    allowed = hooks.get("allowedAgentIds")
    allowed_set = {x for x in allowed if isinstance(x, str)} if isinstance(allowed, list) else set()
    missing_list = [a for a in AUTODEV_AGENT_IDS if a not in present]
    missing_hooks = [a for a in AUTODEV_AGENT_IDS if a not in allowed_set]
    if missing_list or missing_hooks:
        bits = []
        if missing_list:
            bits.append("agents.list missing: " + ", ".join(missing_list))
        if missing_hooks:
            bits.append("hooks.allowedAgentIds missing: " + ", ".join(missing_hooks))
        return CheckResult(
            "agents_registered", "All 6 Lullabeast agents registered", "fail",
            "; ".join(bits),
            "python autodev/installer/register_agent.py <openclaw.json> <openclaw_root> --apply (or re-run ./install.sh)",
        )
    return CheckResult("agents_registered", "All 6 Lullabeast agents registered", "ok")


def check_context_limits(config: dict) -> CheckResult:
    path = os.path.join(config.get("openclaw_root") or "", "openclaw.json")
    issues = setup_helpers.audit_openclaw_context_limits(path)
    if issues:
        return CheckResult(
            "context_limits", "Bootstrap/compaction context limits", "fail",
            "issues: " + ", ".join(issues),
            "re-run ./install.sh (step 8 seeds bootstrapMaxChars=32000 and postCompaction caps/sections)",
        )
    return CheckResult("context_limits", "Bootstrap/compaction context limits", "ok")


def check_tools_profile(config: dict) -> CheckResult:
    path = os.path.join(config.get("openclaw_root") or "", "openclaw.json")
    data, err = _load_openclaw_json(path)
    if data is None:
        return CheckResult(
            "tools_profile", "tools.profile pipeline-capable", "fail",
            f"cannot read openclaw.json ({err})", "fix openclaw.json first",
        )
    tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
    profile = (tools.get("profile") or "").strip() if isinstance(tools.get("profile"), str) else tools.get("profile")
    if profile in ("coding", "full", "", None):
        return CheckResult(
            "tools_profile", "tools.profile pipeline-capable", "ok",
            f"profile={profile or 'unset (gateway default)'}",
        )
    return CheckResult(
        "tools_profile", "tools.profile pipeline-capable", "fail",
        f"tools.profile is {profile!r}; pipeline coding agents need coding or full",
        'set tools.profile to "coding" in openclaw.json (install.sh step 8 offers this)',
    )


def check_heartbeat_disabled(config: dict) -> CheckResult:
    path = os.path.join(config.get("openclaw_root") or "", "openclaw.json")
    data, err = _load_openclaw_json(path)
    if data is None:
        return CheckResult(
            "heartbeat_disabled", "Agent heartbeat disabled", "fail",
            f"cannot read openclaw.json ({err})", "fix openclaw.json first",
        )
    defaults = (data.get("agents") or {}).get("defaults") if isinstance(data.get("agents"), dict) else None
    hb = defaults.get("heartbeat") if isinstance(defaults, dict) else None
    every = hb.get("every") if isinstance(hb, dict) else None
    if every == "0m":
        return CheckResult("heartbeat_disabled", "Agent heartbeat disabled", "ok", 'agents.defaults.heartbeat.every = "0m"')
    if every is None:
        return CheckResult(
            "heartbeat_disabled", "Agent heartbeat disabled", "warn",
            "agents.defaults.heartbeat.every not set; a gateway-default heartbeat can interrupt pipeline runs mid-phase",
            'set agents.defaults.heartbeat.every to "0m" in openclaw.json (SETUP.md requirement)',
        )
    return CheckResult(
        "heartbeat_disabled", "Agent heartbeat disabled", "fail",
        f'agents.defaults.heartbeat.every = {every!r} (non-zero heartbeats interrupt pipeline runs mid-phase)',
        'set agents.defaults.heartbeat.every to "0m" in openclaw.json',
    )


def check_gateway_up(config: dict) -> CheckResult:
    host, port = _hooks_host_port(config.get("hooks_url") or "")
    if _tcp_reachable(host, port):
        return CheckResult("gateway_up", "OpenClaw gateway reachable", "ok", f"{host}:{port}")
    return CheckResult(
        "gateway_up", "OpenClaw gateway reachable", "fail",
        f"nothing listening at {host}:{port}",
        "start the OpenClaw gateway (systemctl --user start openclaw-gateway, or your usual launch)",
    )


def check_webhook_ping(config: dict, live: bool) -> CheckResult:
    if not live:
        return CheckResult(
            "webhook_ping", "Webhook POST ping (live)", "skipped",
            "run with --live to POST /hooks/agent (creates a real OpenClaw session)",
        )
    url = config.get("hooks_url") or "http://localhost:18789/hooks/agent"
    token = (config.get("hooks_token") or "").strip()
    if not token:
        oc_json = os.path.join(config.get("openclaw_root") or "", "openclaw.json")
        token = setup_helpers.read_openclaw_hooks_token(oc_json) or ""
    if not token:
        return CheckResult(
            "webhook_ping", "Webhook POST ping (live)", "fail",
            "no hooks token available (config hooks_token / .env AUTODEV_HOOKS_TOKEN / openclaw.json hooks.token)",
            "fix the secret_sync check first",
        )
    body = json.dumps(
        {
            "agentId": "prd-creator",
            "sessionKey": "ideas:doctor-check:0",
            "wakeMode": "now",
            "message": "doctor ping",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=probe_timeout()) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, OSError) as e:
        return CheckResult(
            "webhook_ping", "Webhook POST ping (live)", "fail",
            f"POST {url} failed: {e}", "check gateway_up first",
        )
    if code == 200:
        return CheckResult("webhook_ping", "Webhook POST ping (live)", "ok", "HTTP 200")
    if code in (401, 403):
        return CheckResult(
            "webhook_ping", "Webhook POST ping (live)", "fail",
            f"HTTP {code}: Bearer does not match openclaw.json hooks.token",
            "sync the webhook secret (secret_sync check / install.sh step 8)",
        )
    return CheckResult(
        "webhook_ping", "Webhook POST ping (live)", "fail",
        f"HTTP {code} from POST /hooks/agent",
        "inspect the gateway logs; verify hooks.enabled and allowedAgentIds include prd-creator",
    )


def check_plugin_deployed(config: dict) -> CheckResult:
    bundle = os.path.join(
        config.get("openclaw_root") or "",
        "extensions", "autodev-pipeline-signals", "dist", "index.js",
    )
    if not os.path.isfile(bundle):
        return CheckResult(
            "plugin_deployed", "Pipeline-signals plugin bundle deployed", "fail",
            f"bundle not found: {bundle}",
            'cd autodev/plugin && npm install && npm run build && openclaw plugins install --force "$PWD"',
        )
    try:
        with open(bundle, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        return CheckResult(
            "plugin_deployed", "Pipeline-signals plugin bundle deployed", "fail",
            f"bundle unreadable: {e}", "redeploy the plugin bundle",
        )
    if PLUGIN_BUNDLE_MARKER not in content:
        return CheckResult(
            "plugin_deployed", "Pipeline-signals plugin bundle deployed", "fail",
            "deployed bundle missing the current-source marker; likely STALE (activity stamps will not refresh)",
            "cd autodev/plugin && npm run build && openclaw plugins install --force . ; then restart the gateway",
        )
    return CheckResult("plugin_deployed", "Pipeline-signals plugin bundle deployed", "ok", bundle)


def check_plugin_hooks_registered(config: dict) -> CheckResult:
    rc, out, _err = _openclaw_cli("plugins", "inspect", "autodev-pipeline-signals", "--json")
    if rc is None:
        return CheckResult(
            "plugin_hooks_registered", "Plugin loaded with typed hooks", "skipped",
            f"cannot inspect: {out}",
            "openclaw plugins inspect autodev-pipeline-signals --json",
        )
    data = None
    try:
        data = json.loads(out)
    except Exception:
        # Tolerate CLI banner noise before the JSON document.
        idx = (out or "").find("{")
        if idx >= 0:
            try:
                data = json.loads(out[idx:])
            except Exception:
                data = None
    if not isinstance(data, dict):
        return CheckResult(
            "plugin_hooks_registered", "Plugin loaded with typed hooks", "fail",
            f"`openclaw plugins inspect` returned no parseable JSON (rc={rc})",
            'openclaw plugins install --force "<repo>/autodev/plugin" and restart the gateway',
        )
    plugin = data.get("plugin") if isinstance(data.get("plugin"), dict) else {}
    if plugin.get("status") != "loaded":
        return CheckResult(
            "plugin_hooks_registered", "Plugin loaded with typed hooks", "fail",
            f"status={plugin.get('status')!r}",
            'openclaw plugins install --force "<repo>/autodev/plugin" and restart the gateway',
        )
    hooks = {
        h.get("name")
        for h in (data.get("typedHooks") or [])
        if isinstance(h, dict)
    }
    if hooks:
        missing = REQUIRED_PLUGIN_HOOKS - hooks
        if missing:
            return CheckResult(
                "plugin_hooks_registered", "Plugin loaded with typed hooks", "fail",
                "missing typed hooks: " + ", ".join(sorted(missing)),
                'openclaw plugins install --force "<repo>/autodev/plugin" and restart the gateway',
            )
        return CheckResult("plugin_hooks_registered", "Plugin loaded with typed hooks", "ok")
    # OpenClaw 2026.6.x static inspection reports typedHooks=[] for plugins
    # that register via api.on(...) at runtime (verified live on 2026.6.11
    # with a working plugin). Fall back to a content check: the deployed
    # bundle must name every required hook.
    bundle = os.path.join(
        config.get("openclaw_root") or "",
        "extensions", "autodev-pipeline-signals", "dist", "index.js",
    )
    try:
        with open(bundle, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        content = ""
    missing = {h for h in REQUIRED_PLUGIN_HOOKS if f'"{h}"' not in content}
    if missing:
        return CheckResult(
            "plugin_hooks_registered", "Plugin loaded with typed hooks", "fail",
            "inspect reports no typed hooks and the deployed bundle does not name: "
            + ", ".join(sorted(missing)),
            'openclaw plugins install --force "<repo>/autodev/plugin" and restart the gateway',
        )
    return CheckResult(
        "plugin_hooks_registered", "Plugin loaded with typed hooks", "ok",
        "status loaded; hooks verified in the deployed bundle "
        "(2026.6.x inspect does not list runtime api.on registrations)",
    )


def check_exec_approvals(config: dict) -> CheckResult:
    path = os.path.join(config.get("openclaw_root") or "", "exec-approvals.json")
    repo = os.path.abspath(config.get("autodev_repo_path") or "")
    if not os.path.isfile(path):
        return CheckResult(
            "exec_approvals", "exec-approvals gate paths current", "warn",
            f"exec-approvals.json not found at {path}; gate scripts may need approval via the OpenClaw UI",
            "approve the gate scripts once via the OpenClaw UI when the pipeline first runs",
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        return CheckResult(
            "exec_approvals", "exec-approvals gate paths current", "warn",
            f"unreadable: {e}", "check file permissions",
        )
    stale = [
        s for s in re.findall(r'"([^"]*gate_scripts[^"]*)"', raw)
        if repo not in s
    ]
    if stale:
        return CheckResult(
            "exec_approvals", "exec-approvals gate paths current", "fail",
            "stale gate_scripts paths: " + ", ".join(sorted(set(stale))[:5]),
            "re-run ./install.sh or re-approve gate scripts under autodev/pipeline/gate_scripts/",
        )
    return CheckResult("exec_approvals", "exec-approvals gate paths current", "ok")


def check_symlink_consistency(config: dict) -> CheckResult:
    proot = config.get("autodev_pipeline_root") or ""
    autodev_link = config.get("project_dir_path") or os.path.join(proot, "pipeline-project")
    oc_link = os.path.join(config.get("openclaw_root") or "", "pipeline-project")
    a_exists = os.path.lexists(autodev_link)
    o_exists = os.path.lexists(oc_link)
    if not a_exists and not o_exists:
        return CheckResult(
            "symlink_consistency", "pipeline-project symlinks agree", "ok",
            "no project staged (neither symlink exists)",
        )
    if a_exists != o_exists:
        missing = oc_link if a_exists else autodev_link
        present = autodev_link if a_exists else oc_link
        return CheckResult(
            "symlink_consistency", "pipeline-project symlinks agree", "fail",
            f"only one side exists: {present} present, {missing} missing "
            "(agents write through one tree while the orchestrator polls the other)",
            "re-stage the project from the dashboard Setup screen (repoints both links atomically)",
        )
    a_target = os.path.realpath(autodev_link)
    o_target = os.path.realpath(oc_link)
    if a_target != o_target:
        return CheckResult(
            "symlink_consistency", "pipeline-project symlinks agree", "fail",
            f"links diverge: {autodev_link} -> {a_target} vs {oc_link} -> {o_target} "
            "(sentinels/verdicts land where the poller never looks; infinite retries)",
            "re-stage the project from the dashboard Setup screen (repoints both links atomically)",
        )
    state_path = config.get("pipeline_state_path") or os.path.join(proot, "pipeline_state.json")
    if os.path.isfile(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = None
        pp = state.get("project_path") if isinstance(state, dict) else None
        if isinstance(pp, str) and pp.strip():
            if os.path.realpath(pp) != a_target:
                return CheckResult(
                    "symlink_consistency", "pipeline-project symlinks agree", "fail",
                    f"links -> {a_target} but pipeline_state.json project_path = {pp}",
                    "re-stage the project from the dashboard Setup screen, or switch-project to the intended repo",
                )
    return CheckResult(
        "symlink_consistency", "pipeline-project symlinks agree", "ok",
        f"both -> {a_target}",
    )


def check_stale_lock(config: dict) -> CheckResult:
    proot = config.get("autodev_pipeline_root") or ""
    lock_path = config.get("lock_path") or os.path.join(proot, "pipeline.lock")
    if not os.path.isfile(lock_path):
        return CheckResult("stale_lock", "Pipeline lock sane", "ok", "no lock file (no orchestrator running)")
    try:
        import fcntl
    except ModuleNotFoundError:
        return CheckResult(
            "stale_lock", "Pipeline lock sane", "skipped",
            "fcntl unavailable (non-POSIX platform)",
        )
    try:
        # Open read-only: probing must never create or truncate the lock file.
        with open(lock_path, "r") as lf:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                held = False
            except BlockingIOError:
                held = True
    except OSError as e:
        return CheckResult(
            "stale_lock", "Pipeline lock sane", "warn",
            f"cannot probe {lock_path}: {e}", "check file permissions",
        )
    if held:
        return CheckResult("stale_lock", "Pipeline lock sane", "ok", "orchestrator alive (lock held)")
    return CheckResult(
        "stale_lock", "Pipeline lock sane", "warn",
        f"lock file exists but no process holds it (stale): {lock_path}",
        f"safe to remove: rm {lock_path}",
    )


def check_playwright(config: dict) -> CheckResult:
    cache = os.path.expanduser(os.path.join("~", ".cache", "ms-playwright"))
    chromium = sorted(glob.glob(os.path.join(cache, "chromium*")))
    path = os.path.join(config.get("openclaw_root") or "", "openclaw.json")
    data, _err = _load_openclaw_json(path)
    mcp = data.get("mcp") if isinstance(data, dict) and isinstance(data.get("mcp"), dict) else {}
    servers = mcp.get("servers") if isinstance(mcp.get("servers"), dict) else {}
    registered = isinstance(servers.get("playwright"), dict)
    problems = []
    if not chromium:
        problems.append(f"no chromium under {cache}")
    if not registered:
        problems.append("mcp.servers.playwright not registered in openclaw.json")
    if problems:
        return CheckResult(
            "playwright", "Playwright visual-review stack", "warn",
            "; ".join(problems) + " (only UI/INT phases need it; they will fail the reviewer gate without it)",
            "re-run ./install.sh without --skip-playwright (step 12)",
        )
    return CheckResult(
        "playwright", "Playwright visual-review stack", "ok",
        f"chromium: {os.path.basename(chromium[-1])}; mcp.servers.playwright registered",
    )


def check_ui_token(config: dict) -> CheckResult:
    token = (config.get("ui_token") or "").strip() if isinstance(config.get("ui_token"), str) else ""
    if not token:
        token = (os.environ.get("AUTODEV_UI_TOKEN") or "").strip()
    if not token:
        repo = config.get("autodev_repo_path") or ""
        token = setup_helpers.parse_dotenv_value(os.path.join(repo, ".env"), "AUTODEV_UI_TOKEN") or ""
    if token:
        return CheckResult("ui_token", "Dashboard access token set", "ok")
    return CheckResult(
        "ui_token", "Dashboard access token set", "warn",
        "AUTODEV_UI_TOKEN unset (legacy open mode: loopback unauthenticated, non-loopback refused)",
        "re-run ./install.sh (step 10 generates AUTODEV_UI_TOKEN into .env), then restart the UI server",
    )


def check_ports(config: dict) -> CheckResult:
    gw_host, gw_port = _hooks_host_port(config.get("hooks_url") or "")
    try:
        ui_port = int(config.get("port") or 18790)
    except (TypeError, ValueError):
        ui_port = 18790
    # Accumulate rather than early-return: a squatted UI port (warn-level) must
    # never mask a dead gateway (fail-level) detected in the same pass.
    problems: list[str] = []
    warnings: list[str] = []
    details: list[str] = []
    if _tcp_reachable(gw_host, gw_port):
        details.append(f"gateway {gw_host}:{gw_port} responding")
    else:
        problems.append(f"gateway port {gw_host}:{gw_port} not responding")
    if _tcp_reachable("127.0.0.1", ui_port):
        code, body = _http_get_json(f"http://127.0.0.1:{ui_port}/health")
        if code == 200 and isinstance(body, dict) and body.get("ok") is True:
            details.append(f"UI port {ui_port} already serving Lullabeast")
        else:
            warnings.append(
                f"port {ui_port} is occupied by something that is not the Lullabeast UI (HTTP {code})"
            )
    else:
        details.append(f"UI port {ui_port} free")
    if problems:
        return CheckResult(
            "ports", "Ports 18789/18790 sane", "fail",
            "; ".join(problems + warnings + details),
            "start the OpenClaw gateway; it must listen before agents can be invoked",
        )
    if warnings:
        return CheckResult(
            "ports", "Ports 18789/18790 sane", "warn",
            "; ".join(warnings + details),
            f"free port {ui_port} or change the UI port in ui/config.json",
        )
    return CheckResult("ports", "Ports 18789/18790 sane", "ok", "; ".join(details))


def check_template_conformance(config: dict) -> CheckResult:
    # Owned-OpenClaw mode only (DS-2b): the container's openclaw.json is
    # rendered from deploy/openclaw.template.json, so any divergence from the
    # template's requirements is drift (a hand-edit inside /data, or an
    # OpenClaw upgrade rewriting a key). Guest installs are never
    # template-rendered, so the check is meaningless there and skips.
    # install.sh exports OWNED_OPENCLAW=1 into the doctor run in owned mode.
    if (os.environ.get("OWNED_OPENCLAW") or "").strip() != "1":
        return CheckResult(
            "template_conformance", "Config matches golden template", "skipped",
            "owned-OpenClaw mode only (OWNED_OPENCLAW=1); guest installs are not template-rendered",
        )
    repo = config.get("autodev_repo_path") or ""
    tpath = template_path(repo)
    try:
        template = load_template(repo)
    except FileNotFoundError:
        return CheckResult(
            "template_conformance", "Config matches golden template", "fail",
            f"golden template not found: {tpath}",
            "restore deploy/openclaw.template.json from the repo (the image ships it)",
        )
    except (ValueError, OSError) as e:
        return CheckResult(
            "template_conformance", "Config matches golden template", "fail",
            f"golden template unreadable/invalid: {e}",
            "restore deploy/openclaw.template.json from the repo (the image ships it)",
        )
    live_path = os.path.join(config.get("openclaw_root") or "", "openclaw.json")
    live, err = _load_openclaw_json(live_path)
    if live is None:
        return CheckResult(
            "template_conformance", "Config matches golden template", "fail",
            f"cannot read openclaw.json ({err})",
            "fix openclaw.json first (see openclaw_json check)",
        )
    issues = template_conformance_issues(template, live)
    if issues:
        shown = "; ".join(issues[:6])
        if len(issues) > 6:
            shown += f"; ... {len(issues) - 6} more"
        return CheckResult(
            "template_conformance", "Config matches golden template", "fail",
            f"{len(issues)} audited key(s) drifted from the template: {shown}",
            "hand-edits inside an owned tree are overwritten by design; re-render "
            "openclaw.json from deploy/openclaw.template.json or mount a replacement template",
        )
    return CheckResult(
        "template_conformance", "Config matches golden template", "ok",
        f"live config satisfies every requirement in {tpath}",
    )


# Catalogue order matters only for display; keep it aligned with the DS-1 table.
def run_doctor(config: dict, *, live: bool = False) -> DoctorReport:
    """Run every check against ``config`` (the ui/server.py load_config() shape).

    Read-only. ``live=True`` additionally performs the webhook POST ping,
    which creates a real OpenClaw agent session.
    """
    checks = [
        check_env_paths(config),
        check_python_deps(config),
        check_git_identity(config),
        check_openclaw_json(config),
        check_openclaw_version(config),
        check_hooks_baseline(config),
        check_secret_sync(config),
        check_agents_registered(config),
        check_context_limits(config),
        check_tools_profile(config),
        check_heartbeat_disabled(config),
        check_gateway_up(config),
        check_webhook_ping(config, live),
        check_plugin_deployed(config),
        check_plugin_hooks_registered(config),
        check_exec_approvals(config),
        check_symlink_consistency(config),
        check_stale_lock(config),
        check_playwright(config),
        check_ui_token(config),
        check_ports(config),
        check_template_conformance(config),
    ]
    return DoctorReport(checks=checks)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# Keys the CLI honors from ui/config.json (mirrors the server's dual-source
# rule for the subset the doctor consumes). The CLI deliberately does NOT
# import ui.server: the doctor must run even when FastAPI/uvicorn are missing,
# because python_deps is one of its checks.
_CLI_CONFIG_KEYS = (
    "openclaw_root",
    "autodev_repo_path",
    "autodev_pipeline_root",
    "hooks_url",
    "hooks_token",
    "ui_token",
    "port",
    "project_dir_path",
    "pipeline_state_path",
    "lock_path",
)


def build_cli_config() -> dict:
    repo = (os.environ.get("AUTODEV_REPO_PATH") or "").strip() or _REPO_ROOT_DEFAULT
    repo = os.path.abspath(os.path.expanduser(repo))
    config: dict = {
        "autodev_repo_path": repo,
        "openclaw_root": resolve_openclaw_root(),
        "autodev_pipeline_root": "",
        "hooks_url": "http://localhost:18789/hooks/agent",
        "hooks_token": "",
        "ui_token": "",
        "port": 18790,
    }
    ui_cfg_path = os.path.join(repo, "ui", "config.json")
    if os.path.isfile(ui_cfg_path):
        try:
            with open(ui_cfg_path, "r", encoding="utf-8") as f:
                user = json.load(f)
        except Exception:
            user = None
        if isinstance(user, dict):
            for key in _CLI_CONFIG_KEYS:
                val = user.get(key)
                if isinstance(val, str) and val.strip():
                    config[key] = os.path.expanduser(val.strip())
                elif isinstance(val, (int, float)) and not isinstance(val, bool):
                    config[key] = val
    # Env wins for the secrets, same precedence as the server.
    for env_key, cfg_key in (
        ("AUTODEV_HOOKS_TOKEN", "hooks_token"),
        ("AUTODEV_UI_TOKEN", "ui_token"),
    ):
        v = (os.environ.get(env_key) or "").strip()
        if v:
            config[cfg_key] = v
    repo = config["autodev_repo_path"]
    proot = config.get("autodev_pipeline_root") or resolve_pipeline_root(repo)
    config["autodev_pipeline_root"] = proot
    config.setdefault("project_dir_path", os.path.join(proot, "pipeline-project"))
    config.setdefault("pipeline_state_path", os.path.join(proot, "pipeline_state.json"))
    config.setdefault("lock_path", os.path.join(proot, "pipeline.lock"))
    try:
        config["port"] = int(config.get("port") or 18790)
    except (TypeError, ValueError):
        config["port"] = 18790
    return config


_SYMBOLS = {"ok": "OK", "warn": "WARN", "fail": "FAIL", "skipped": "SKIP"}


def _print_report(report: DoctorReport, quiet: bool) -> None:
    use_color = sys.stdout.isatty()
    colors = {
        "ok": "\033[32m",
        "warn": "\033[33m",
        "fail": "\033[31m",
        "skipped": "\033[2m",
    }
    reset = "\033[0m"
    for c in report.checks:
        if quiet and c.status in ("ok", "skipped"):
            continue
        tag = _SYMBOLS.get(c.status, c.status.upper())
        if use_color:
            tag = f"{colors.get(c.status, '')}{tag:<4}{reset}"
        else:
            tag = f"{tag:<4}"
        line = f"  {tag} {c.id:<24} {c.title}"
        if c.detail:
            line += f" :: {c.detail}"
        print(line)
        if c.fix_hint and c.status in ("warn", "fail"):
            print(f"       fix: {c.fix_hint}")
    counts = report.counts()
    print(
        f"doctor: {counts['ok']} ok, {counts['warn']} warn, "
        f"{counts['fail']} fail, {counts['skipped']} skipped"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m autodev.installer.doctor",
        description="Lullabeast doctor: read-only health checks for every documented silent-failure mode.",
    )
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument(
        "--live", action="store_true",
        help="also POST a webhook ping (side-effectful: creates an OpenClaw session)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="print only warn/fail lines; warns do not affect the exit code",
    )
    args = parser.parse_args(argv)
    report = run_doctor(build_cli_config(), live=args.live)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_report(report, quiet=args.quiet)
    return report.exit_code(warns_ok=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
