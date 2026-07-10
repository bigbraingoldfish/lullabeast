"""Hermetic unit tests for the doctor (autodev/installer/doctor.py).

Everything runs against tmp_path fixtures: a fake OpenClaw root with
good/bad openclaw.json variants, a fake extensions dir with/without the
bundle marker, a fake repo tree, and a fake ``openclaw`` CLI on PATH.
Never touches the real ~/.openclaw or the live .autodev symlinks.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import autodev.installer.doctor as doctor
from autodev.installer.doctor import (
    KNOWN_BAD_OPENCLAW_VERSIONS,
    MIN_OPENCLAW_VERSION,
    DoctorReport,
    CheckResult,
    run_doctor,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

AGENT_IDS = (
    "planner", "executor", "reviewer", "escalation", "prd-creator", "roadmap-converter",
)
HOOK_NAMES = (
    "agent_end", "before_agent_finalize", "model_call_started",
    "model_call_ended", "after_tool_call",
)
ALWAYS_APPLY = (
    "Always-Apply: Integration Wiring",
    "Always-Apply: Testing Quality",
    "Always-Apply: Orchestrator Control",
)

EXPECTED_CHECK_IDS = [
    "env_paths", "python_deps", "git_identity", "conversion_prompt",
    "openclaw_json",
    "openclaw_version", "hooks_baseline", "secret_sync", "agents_registered",
    "context_limits", "tools_profile", "heartbeat_disabled", "gateway_up",
    "webhook_ping", "plugin_deployed", "plugin_hooks_registered",
    "exec_approvals", "symlink_consistency", "stale_lock", "playwright",
    "ui_token", "ports", "provider_key", "local_model_completeness",
    "model_modality", "template_conformance",
]


class _NotFoundHandler(BaseHTTPRequestHandler):
    """A non-Lullabeast HTTP service (404s /health) for port-squat tests."""

    def do_GET(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _good_openclaw_json(token: str = "tok-123") -> dict:
    entries = []
    for aid in AGENT_IDS:
        e = {"id": aid, "bootstrapMaxChars": 32000}
        if aid in ("planner", "executor", "reviewer"):
            e["contextLimits"] = {"postCompactionMaxChars": 8000}
        entries.append(e)
    return {
        "hooks": {
            "enabled": True,
            "token": token,
            "allowRequestSessionKey": True,
            "allowedSessionKeyPrefixes": ["pipeline:", "ideas:"],
            "allowedAgentIds": list(AGENT_IDS),
        },
        "tools": {"profile": "coding"},
        "agents": {
            "defaults": {
                "heartbeat": {"every": "0m"},
                "compaction": {
                    "postCompactionSections": list(ALWAYS_APPLY)
                    + ["Session Startup", "Red Lines"]
                },
            },
            "list": entries,
        },
        "mcp": {"servers": {"playwright": {"command": "npx", "args": []}}},
    }


def _bundle_content() -> str:
    hooks = " ".join(f'api.on("{h}", handler)' for h in HOOK_NAMES)
    return f"// bundled\nconst m = /agent:[a-z0-9_-]+:ideas:/;\n{hooks}\n"


_INSPECT_LOADED_TYPED = json.dumps(
    {
        "plugin": {"status": "loaded"},
        "typedHooks": [{"name": h} for h in HOOK_NAMES],
    }
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fully green doctor environment under tmp_path. Returns the config dict."""
    oc = tmp_path / "openclaw"
    repo = tmp_path / "repo"
    proot = repo / ".autodev"
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    for d in (oc, repo / "autodev" / "pipeline", repo / "ui", proot, home, bin_dir):
        d.mkdir(parents=True)

    (repo / "autodev" / "pipeline" / "orchestrator.py").write_text("# stub\n")
    (repo / "autodev" / "prompts").mkdir(parents=True)
    (repo / "autodev" / "prompts" / "prd-to-roadmap-conversion.txt").write_text(
        "Convert the PRD into a canonical roadmap.\n"
    )
    (oc / "openclaw.json").write_text(json.dumps(_good_openclaw_json()))
    bundle = oc / "extensions" / "autodev-pipeline-signals" / "dist"
    bundle.mkdir(parents=True)
    (bundle / "index.js").write_text(_bundle_content())
    (oc / "exec-approvals.json").write_text(
        json.dumps({"cmd": str(repo / "autodev/pipeline/gate_scripts/executor_gate.py")})
    )
    (repo / ".env").write_text("AUTODEV_HOOKS_TOKEN=tok-123\nAUTODEV_UI_TOKEN=ui-tok\n")
    (repo / "ui" / "config.json").write_text(json.dumps({"hooks_token": "tok-123"}))

    # Fake openclaw CLI: --version and plugins inspect.
    fake = bin_dir / "openclaw"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--version" ]; then echo "OpenClaw 2026.6.11 (test)"; exit 0; fi\n'
        f"cat <<'EOF'\n{_INSPECT_LOADED_TYPED}\nEOF\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

    # Hermetic git identity via GIT_CONFIG_GLOBAL (git >= 2.32).
    gitcfg = tmp_path / "gitconfig"
    gitcfg.write_text("[user]\n\tname = Doc Tor\n\temail = doc@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitcfg))

    # Playwright cache under a fake HOME.
    (home / ".cache" / "ms-playwright" / "chromium-1000").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    # Guest posture by default: template_conformance (owned mode only) skips.
    monkeypatch.delenv("OWNED_OPENCLAW", raising=False)

    # A live listener standing in for the gateway.
    gw = socket.socket()
    gw.bind(("127.0.0.1", 0))
    gw.listen(5)
    gw_port = gw.getsockname()[1]

    config = {
        "openclaw_root": str(oc),
        "autodev_repo_path": str(repo),
        "autodev_pipeline_root": str(proot),
        "hooks_url": f"http://127.0.0.1:{gw_port}/hooks/agent",
        "hooks_token": "tok-123",
        "ui_token": "ui-tok",
        "port": _free_port(),
        "project_dir_path": str(proot / "pipeline-project"),
        "pipeline_state_path": str(proot / "pipeline_state.json"),
        "lock_path": str(proot / "pipeline.lock"),
    }
    yield config
    gw.close()


def _rewrite_openclaw(config, mutate):
    path = os.path.join(config["openclaw_root"], "openclaw.json")
    data = json.loads(open(path).read())
    mutate(data)
    with open(path, "w") as f:
        json.dump(data, f)


def _by_id(report: DoctorReport) -> dict:
    return {c.id: c for c in report.checks}


# ── the all-green fixture ────────────────────────────────────────────────────

class TestAllGreen:
    def test_all_green(self, env):
        report = run_doctor(env)
        by_id = _by_id(report)
        assert [c.id for c in report.checks] == EXPECTED_CHECK_IDS
        bad = {
            cid: (c.status, c.detail)
            for cid, c in by_id.items()
            if c.status not in ("ok", "skipped")
        }
        assert bad == {}, f"non-green checks in the green fixture: {bad}"
        # Exactly five checks skip in the bare-metal green fixture:
        # webhook_ping without --live, template_conformance outside
        # owned-OpenClaw mode, provider_key with no container setup paths
        # configured, local_model_completeness with no local/ role model, and
        # model_modality with no reviewer/executor model.primary configured.
        skipped = {cid for cid, c in by_id.items() if c.status == "skipped"}
        assert skipped == {
            "webhook_ping", "template_conformance", "provider_key",
            "local_model_completeness", "model_modality",
        }
        assert report.overall() == "ok"
        assert report.exit_code() == 0


# ── per-check fail modes ─────────────────────────────────────────────────────

class TestEnvPaths:
    def test_missing_orchestrator(self, env):
        os.remove(os.path.join(env["autodev_repo_path"], "autodev/pipeline/orchestrator.py"))
        c = doctor.check_env_paths(env)
        assert c.status == "fail"
        assert "orchestrator.py" in c.detail

    def test_missing_openclaw_root(self, env):
        env["openclaw_root"] = env["openclaw_root"] + "-nope"
        c = doctor.check_env_paths(env)
        assert c.status == "fail"
        assert "OPENCLAW_ROOT" in c.detail


class TestGitIdentity:
    def test_no_identity(self, env, monkeypatch, tmp_path):
        empty = tmp_path / "empty-gitconfig"
        empty.write_text("")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
        c = doctor.check_git_identity(env)
        assert c.status == "fail"
        assert "user.name" in c.fix_hint


class TestConversionPrompt:
    _BUNDLED = "autodev/prompts/prd-to-roadmap-conversion.txt"

    def test_bundled_ok(self, env):
        c = doctor.check_conversion_prompt(env)
        assert c.status == "ok"
        assert self._BUNDLED in c.detail

    def test_bundled_missing_fails(self, env):
        os.remove(os.path.join(env["autodev_repo_path"], self._BUNDLED))
        c = doctor.check_conversion_prompt(env)
        assert c.status == "fail"
        assert "prd-to-roadmap-conversion.txt" in c.detail
        assert c.fix_hint

    def test_override_readable_ok(self, env, tmp_path):
        custom = tmp_path / "my-prompt.txt"
        custom.write_text("custom instructions\n")
        env["conversion_prompt_path"] = str(custom)
        c = doctor.check_conversion_prompt(env)
        assert c.status == "ok"
        assert str(custom) in c.detail

    def test_override_missing_warns(self, env, tmp_path):
        env["conversion_prompt_path"] = str(tmp_path / "gone.txt")
        c = doctor.check_conversion_prompt(env)
        assert c.status == "warn"
        assert "conversion_prompt_path" in c.detail

    def test_override_missing_and_no_bundled_fails(self, env, tmp_path):
        os.remove(os.path.join(env["autodev_repo_path"], self._BUNDLED))
        env["conversion_prompt_path"] = str(tmp_path / "gone.txt")
        c = doctor.check_conversion_prompt(env)
        assert c.status == "fail"

    def test_whitespace_padded_override_warns_like_the_server_ignores_it(self, env, tmp_path):
        """load_config never strips, so the server treats a padded path as
        not-a-file and falls back. The doctor must not strip either, or it
        would greenlight an override the server silently ignores."""
        custom = tmp_path / "my-prompt.txt"
        custom.write_text("custom instructions\n")
        env["conversion_prompt_path"] = f"  {custom}  "
        c = doctor.check_conversion_prompt(env)
        assert c.status == "warn"

    def test_tilde_override_resolves_like_load_config_expands_it(self, env, monkeypatch, tmp_path):
        """load_config expands ~ on every string value before the server probes
        the path, so a ~ override IS honored at runtime; the doctor's own
        expanduser keeps the CLI's raw ui/config.json read consistent."""
        home = tmp_path / "home2"
        home.mkdir()
        (home / "my-prompt.txt").write_text("custom instructions\n")
        monkeypatch.setenv("HOME", str(home))
        env["conversion_prompt_path"] = "~/my-prompt.txt"
        c = doctor.check_conversion_prompt(env)
        assert c.status == "ok"
        assert str(home / "my-prompt.txt") in c.detail


class TestOpenclawJson:
    def test_corrupt(self, env):
        with open(os.path.join(env["openclaw_root"], "openclaw.json"), "w") as f:
            f.write("{nope")
        c = doctor.check_openclaw_json(env)
        assert c.status == "fail"
        assert "not valid JSON" in c.detail

    def test_missing(self, env):
        os.remove(os.path.join(env["openclaw_root"], "openclaw.json"))
        c = doctor.check_openclaw_json(env)
        assert c.status == "fail"


class TestOpenclawVersion:
    def _fake_cli(self, monkeypatch, text, rc=0):
        monkeypatch.setattr(doctor, "_openclaw_cli", lambda *a: (rc, text, ""))

    def test_below_floor(self, env, monkeypatch):
        self._fake_cli(monkeypatch, "OpenClaw 2026.5.17 (old)")
        c = doctor.check_openclaw_version(env)
        assert c.status == "fail"
        assert MIN_OPENCLAW_VERSION in c.detail

    def test_known_bad(self, env, monkeypatch):
        self._fake_cli(monkeypatch, "OpenClaw 2026.6.8 (bad)")
        c = doctor.check_openclaw_version(env)
        assert c.status == "fail"
        assert "known-bad" in c.detail

    def test_cli_absent_is_skipped(self, env, monkeypatch):
        monkeypatch.setattr(doctor, "_openclaw_cli", lambda *a: (None, "openclaw CLI not on PATH", ""))
        c = doctor.check_openclaw_version(env)
        assert c.status == "skipped"

    def test_current_ok(self, env):
        c = doctor.check_openclaw_version(env)  # fake PATH CLI prints 2026.6.11
        assert c.status == "ok"
        assert "2026.6.11" in c.detail

    def test_version_on_stderr_still_guards(self, env, monkeypatch):
        # A CLI build that prints the version to stderr (stdout carries banner
        # noise) must NOT degrade the known-bad guard to "skipped".
        monkeypatch.setattr(
            doctor, "_openclaw_cli",
            lambda *a: (0, "some banner noise\n", "OpenClaw 2026.6.8 (bad)\n"),
        )
        c = doctor.check_openclaw_version(env)
        assert c.status == "fail"
        assert "known-bad" in c.detail


class TestHooksBaseline:
    def test_blank_token(self, env):
        _rewrite_openclaw(env, lambda d: d["hooks"].__setitem__("token", ""))
        c = doctor.check_hooks_baseline(env)
        assert c.status == "fail"
        assert "token" in c.detail

    def test_missing_prefix(self, env):
        _rewrite_openclaw(
            env, lambda d: d["hooks"].__setitem__("allowedSessionKeyPrefixes", ["pipeline:"])
        )
        c = doctor.check_hooks_baseline(env)
        assert c.status == "fail"
        assert "prefix_ideas" in c.detail


class TestSecretSync:
    def test_env_mismatch(self, env):
        with open(os.path.join(env["autodev_repo_path"], ".env"), "w") as f:
            f.write("AUTODEV_HOOKS_TOKEN=wrong\n")
        c = doctor.check_secret_sync(env)
        assert c.status == "fail"
        assert "mismatch" in c.detail

    def test_no_hooks_token(self, env):
        _rewrite_openclaw(env, lambda d: d["hooks"].__setitem__("token", ""))
        c = doctor.check_secret_sync(env)
        assert c.status == "fail"
        assert "hooks.token" in c.detail


class TestAgentsRegistered:
    def test_missing_agent(self, env):
        def drop(d):
            d["agents"]["list"] = [e for e in d["agents"]["list"] if e["id"] != "reviewer"]
        _rewrite_openclaw(env, drop)
        c = doctor.check_agents_registered(env)
        assert c.status == "fail"
        assert "reviewer" in c.detail

    def test_missing_allowlist_id(self, env):
        _rewrite_openclaw(
            env, lambda d: d["hooks"].__setitem__("allowedAgentIds", ["planner"])
        )
        c = doctor.check_agents_registered(env)
        assert c.status == "fail"
        assert "allowedAgentIds" in c.detail


class TestContextLimits:
    def test_low_bootstrap_cap(self, env):
        def lower(d):
            d["agents"]["list"][0]["bootstrapMaxChars"] = 12000
        _rewrite_openclaw(env, lower)
        c = doctor.check_context_limits(env)
        assert c.status == "fail"
        assert "bootstrapMaxChars" in c.detail

    def test_missing_section(self, env):
        def strip(d):
            d["agents"]["defaults"]["compaction"]["postCompactionSections"] = [
                "Session Startup"
            ]
        _rewrite_openclaw(env, strip)
        c = doctor.check_context_limits(env)
        assert c.status == "fail"
        assert "missing_section" in c.detail


class TestToolsProfile:
    def test_wrong_profile(self, env):
        _rewrite_openclaw(env, lambda d: d["tools"].__setitem__("profile", "messaging"))
        c = doctor.check_tools_profile(env)
        assert c.status == "fail"

    def test_unset_is_ok(self, env):
        _rewrite_openclaw(env, lambda d: d.pop("tools"))
        c = doctor.check_tools_profile(env)
        assert c.status == "ok"


class TestHeartbeat:
    def test_nonzero_fails(self, env):
        _rewrite_openclaw(
            env, lambda d: d["agents"]["defaults"].__setitem__("heartbeat", {"every": "5m"})
        )
        c = doctor.check_heartbeat_disabled(env)
        assert c.status == "fail"
        assert '"0m"' in c.fix_hint

    def test_missing_warns(self, env):
        _rewrite_openclaw(env, lambda d: d["agents"]["defaults"].pop("heartbeat"))
        c = doctor.check_heartbeat_disabled(env)
        assert c.status == "warn"


class TestGatewayAndPorts:
    def test_gateway_down(self, env):
        env["hooks_url"] = f"http://127.0.0.1:{_free_port()}/hooks/agent"
        assert doctor.check_gateway_up(env).status == "fail"
        assert doctor.check_ports(env).status == "fail"

    def test_gateway_up_ok(self, env):
        assert doctor.check_gateway_up(env).status == "ok"
        assert doctor.check_ports(env).status == "ok"

    def test_foreign_ui_port_warns(self, env):
        # Something that is not the Lullabeast UI squats the UI port.
        srv = HTTPServer(("127.0.0.1", 0), _NotFoundHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            env["port"] = srv.server_port
            c = doctor.check_ports(env)
            assert c.status == "warn"
            assert "not the Lullabeast UI" in c.detail
        finally:
            srv.shutdown()
            srv.server_close()

    def test_gateway_down_wins_over_squatted_ui_port(self, env):
        # A warn-level squatted UI port must never mask the fail-level dead
        # gateway detected in the same check (review finding: early return).
        srv = HTTPServer(("127.0.0.1", 0), _NotFoundHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            env["hooks_url"] = f"http://127.0.0.1:{_free_port()}/hooks/agent"
            env["port"] = srv.server_port
            c = doctor.check_ports(env)
            assert c.status == "fail"
            assert "not responding" in c.detail
            assert "not the Lullabeast UI" in c.detail
        finally:
            srv.shutdown()
            srv.server_close()


class TestWebhookPing:
    def test_not_live_is_skipped(self, env):
        c = doctor.check_webhook_ping(env, live=False)
        assert c.status == "skipped"

    def test_live_unreachable_fails(self, env):
        env["hooks_url"] = f"http://127.0.0.1:{_free_port()}/hooks/agent"
        c = doctor.check_webhook_ping(env, live=True)
        assert c.status == "fail"

    def test_live_200_ok_and_401_fail(self, env):
        codes = [200, 401]

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(codes[0])
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            env["hooks_url"] = f"http://127.0.0.1:{srv.server_port}/hooks/agent"
            ok = doctor.check_webhook_ping(env, live=True)
            assert ok.status == "ok"
            codes[0] = 401
            denied = doctor.check_webhook_ping(env, live=True)
            assert denied.status == "fail"
            assert "Bearer" in denied.detail
        finally:
            srv.shutdown()
            srv.server_close()


class TestPluginDeployed:
    def _bundle_path(self, env):
        return os.path.join(
            env["openclaw_root"], "extensions", "autodev-pipeline-signals", "dist", "index.js"
        )

    def test_marker_missing_is_stale(self, env):
        with open(self._bundle_path(env), "w") as f:
            f.write("// truncated bundle, no marker\n")
        c = doctor.check_plugin_deployed(env)
        assert c.status == "fail"
        assert "STALE" in c.detail

    def test_bundle_missing(self, env):
        os.remove(self._bundle_path(env))
        c = doctor.check_plugin_deployed(env)
        assert c.status == "fail"


class TestPluginHooksRegistered:
    def test_typed_hooks_ok(self, env):
        assert doctor.check_plugin_hooks_registered(env).status == "ok"

    def test_cli_absent_is_skipped(self, env, monkeypatch):
        monkeypatch.setattr(doctor, "_openclaw_cli", lambda *a: (None, "openclaw CLI not on PATH", ""))
        assert doctor.check_plugin_hooks_registered(env).status == "skipped"

    def test_not_loaded_fails(self, env, monkeypatch):
        out = json.dumps({"plugin": {"status": "error"}, "typedHooks": []})
        monkeypatch.setattr(doctor, "_openclaw_cli", lambda *a: (0, out, ""))
        c = doctor.check_plugin_hooks_registered(env)
        assert c.status == "fail"

    def test_runtime_registration_fallback(self, env, monkeypatch):
        # 2026.6.x shape: loaded but typedHooks=[]; bundle content decides.
        out = json.dumps({"plugin": {"status": "loaded"}, "typedHooks": []})
        monkeypatch.setattr(doctor, "_openclaw_cli", lambda *a: (0, out, ""))
        c = doctor.check_plugin_hooks_registered(env)
        assert c.status == "ok"
        assert "bundle" in c.detail

    def test_fallback_fails_without_hook_names(self, env, monkeypatch):
        out = json.dumps({"plugin": {"status": "loaded"}, "typedHooks": []})
        monkeypatch.setattr(doctor, "_openclaw_cli", lambda *a: (0, out, ""))
        bundle = os.path.join(
            env["openclaw_root"], "extensions", "autodev-pipeline-signals", "dist", "index.js"
        )
        with open(bundle, "w") as f:
            f.write("// marker only: agent:[a-z0-9_-]+:ideas:\n")
        c = doctor.check_plugin_hooks_registered(env)
        assert c.status == "fail"


class TestExecApprovals:
    def test_missing_warns(self, env):
        os.remove(os.path.join(env["openclaw_root"], "exec-approvals.json"))
        assert doctor.check_exec_approvals(env).status == "warn"

    def test_stale_path_fails(self, env):
        with open(os.path.join(env["openclaw_root"], "exec-approvals.json"), "w") as f:
            json.dump({"cmd": "/old/home/.openclaw/gate_scripts/executor_gate.py"}, f)
        c = doctor.check_exec_approvals(env)
        assert c.status == "fail"
        assert "stale" in c.detail


class TestSymlinkConsistency:
    def test_no_project_staged(self, env):
        assert doctor.check_symlink_consistency(env).status == "ok"

    def test_agreeing_links_ok(self, env, tmp_path):
        target = tmp_path / "proj"
        target.mkdir()
        os.symlink(target, env["project_dir_path"])
        os.symlink(target, os.path.join(env["openclaw_root"], "pipeline-project"))
        assert doctor.check_symlink_consistency(env).status == "ok"

    def test_divergent_links_fail(self, env, tmp_path):
        t1 = tmp_path / "proj1"; t1.mkdir()
        t2 = tmp_path / "proj2"; t2.mkdir()
        os.symlink(t1, env["project_dir_path"])
        os.symlink(t2, os.path.join(env["openclaw_root"], "pipeline-project"))
        c = doctor.check_symlink_consistency(env)
        assert c.status == "fail"
        assert "diverge" in c.detail

    def test_one_side_missing_fails(self, env, tmp_path):
        t1 = tmp_path / "proj1"; t1.mkdir()
        os.symlink(t1, env["project_dir_path"])
        c = doctor.check_symlink_consistency(env)
        assert c.status == "fail"
        assert "only one side" in c.detail

    def test_state_mismatch_fails(self, env, tmp_path):
        t1 = tmp_path / "proj1"; t1.mkdir()
        t2 = tmp_path / "proj2"; t2.mkdir()
        os.symlink(t1, env["project_dir_path"])
        os.symlink(t1, os.path.join(env["openclaw_root"], "pipeline-project"))
        with open(env["pipeline_state_path"], "w") as f:
            json.dump({"project_path": str(t2)}, f)
        c = doctor.check_symlink_consistency(env)
        assert c.status == "fail"
        assert "project_path" in c.detail


class TestStaleLock:
    def test_no_lock_ok(self, env):
        c = doctor.check_stale_lock(env)
        assert c.status == "ok"

    def test_stale_lock_warns(self, env):
        Path(env["lock_path"]).write_text("")
        c = doctor.check_stale_lock(env)
        assert c.status == "warn"
        assert "rm " in c.fix_hint

    def test_held_lock_ok(self, env):
        import fcntl

        Path(env["lock_path"]).write_text("")
        with open(env["lock_path"], "r+") as holder:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            c = doctor.check_stale_lock(env)
        assert c.status == "ok"
        assert "alive" in c.detail


class TestPlaywright:
    def test_missing_chromium_warns(self, env, monkeypatch, tmp_path):
        bare_home = tmp_path / "bare-home"
        bare_home.mkdir()
        monkeypatch.setenv("HOME", str(bare_home))
        c = doctor.check_playwright(env)
        assert c.status == "warn"
        assert "chromium" in c.detail

    def test_unregistered_mcp_warns(self, env):
        _rewrite_openclaw(env, lambda d: d.pop("mcp"))
        c = doctor.check_playwright(env)
        assert c.status == "warn"
        assert "mcp.servers.playwright" in c.detail


class TestUiToken:
    def test_unset_warns(self, env):
        env["ui_token"] = ""
        with open(os.path.join(env["autodev_repo_path"], ".env"), "w") as f:
            f.write("AUTODEV_HOOKS_TOKEN=tok-123\n")
        c = doctor.check_ui_token(env)
        assert c.status == "warn"
        assert "legacy open mode" in c.detail

    def test_dotenv_fallback_ok(self, env):
        env["ui_token"] = ""
        c = doctor.check_ui_token(env)  # .env still carries AUTODEV_UI_TOKEN
        assert c.status == "ok"


class TestProviderKey:
    """The container/setup-mode provider_key check.

    The check applies only when the entrypoint-seeded container paths are
    configured; a bare-metal install (neither path set) skips it.
    """

    @pytest.fixture
    def paths(self, env, tmp_path, monkeypatch):
        # A clean container-shaped config: key file + marker paths set, and no
        # provider key inherited from the test runner's own environment.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("LOCAL_MODEL_URL", raising=False)
        monkeypatch.delenv("PROVIDER_SETUP_SKIPPED", raising=False)
        secrets = tmp_path / "secrets"
        secrets.mkdir()
        env["provider_key_path"] = str(secrets / "provider.env")
        env["setup_marker_path"] = str(tmp_path / ".setup-mode")
        return env

    def test_unconfigured_skips(self, env, monkeypatch):
        # Bare metal: neither container path present.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("LOCAL_MODEL_URL", raising=False)
        env.pop("provider_key_path", None)
        env.pop("setup_marker_path", None)
        c = doctor.check_provider_key(env)
        assert c.status == "skipped"
        assert "container" in c.detail

    def test_setup_marker_warns(self, paths):
        Path(paths["setup_marker_path"]).write_text("")
        c = doctor.check_provider_key(paths)
        assert c.status == "warn"
        assert "setup mode" in c.detail
        assert "dashboard" in c.fix_hint

    def test_presence_wins_over_marker(self, paths):
        # DELIBERATE reorder (v1.0.0 Phase 3): a present key file now wins over a
        # stale setup marker. During the watch-loop unlock window the file exists
        # while the marker is not yet cleared, and "a provider is available" is
        # the honest read (the old behaviour let the marker win here).
        Path(paths["setup_marker_path"]).write_text("")
        Path(paths["provider_key_path"]).write_text("OPENROUTER_API_KEY=sk-abc\n")
        c = doctor.check_provider_key(paths)
        assert c.status == "ok"
        assert "key file" in c.detail

    def test_env_key_ok(self, paths, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret-value")
        c = doctor.check_provider_key(paths)
        assert c.status == "ok"
        assert c.detail  # reports presence
        assert "environment" in c.detail

    def test_anthropic_env_key_ok(self, paths, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
        c = doctor.check_provider_key(paths)
        assert c.status == "ok"

    def test_env_local_model_url_ok(self, paths, monkeypatch):
        # A LOCAL_MODEL_URL in the environment satisfies the gate like a key.
        monkeypatch.setenv("LOCAL_MODEL_URL", "http://host.docker.internal:11434")
        c = doctor.check_provider_key(paths)
        assert c.status == "ok"
        assert "local model provider" in c.detail
        assert "environment" in c.detail

    def test_key_file_ok(self, paths):
        Path(paths["provider_key_path"]).write_text("OPENROUTER_API_KEY=sk-secret-value\n")
        c = doctor.check_provider_key(paths)
        assert c.status == "ok"
        assert "key file" in c.detail

    def test_file_local_model_url_ok(self, paths):
        # A persisted provider.env carrying only a LOCAL_MODEL_URL reads healthy
        # with the local label.
        Path(paths["provider_key_path"]).write_text(
            "LOCAL_MODEL_URL=http://host.docker.internal:1234\n"
        )
        c = doctor.check_provider_key(paths)
        assert c.status == "ok"
        assert "local model provider" in c.detail
        assert "persisted key file" in c.detail

    def test_cloud_wins_label_when_both_present(self, paths):
        # A file with both a cloud key and a local URL: cloud wins the label.
        Path(paths["provider_key_path"]).write_text(
            "OPENROUTER_API_KEY=sk-abc\nLOCAL_MODEL_URL=http://h:11434\n"
        )
        c = doctor.check_provider_key(paths)
        assert c.status == "ok"
        assert "provider key present" in c.detail
        assert "local model provider" not in c.detail

    def test_blank_valued_lines_are_not_a_provider(self, paths):
        # A file with the keys present but blank (the .env.example shape) is not
        # a provider: warn, not ok.
        Path(paths["provider_key_path"]).write_text(
            "OPENROUTER_API_KEY=\nLOCAL_MODEL_URL=\n"
        )
        c = doctor.check_provider_key(paths)
        assert c.status == "warn"

    def test_empty_key_file_is_not_a_key(self, paths):
        # An empty file (0 bytes) is not a key: same as no file.
        Path(paths["provider_key_path"]).write_text("")
        c = doctor.check_provider_key(paths)
        assert c.status == "warn"

    def test_neither_warns(self, paths):
        # Configured container, no marker, no env key, no key file: keyless
        # boot (OFFLINE CI). Honest warn, not ok, not fail.
        c = doctor.check_provider_key(paths)
        assert c.status == "warn"
        assert "keyless boot" in c.detail
        assert "LOCAL_MODEL_URL" in c.fix_hint

    def test_skip_marker_in_file_warns_with_openclaw_pointer(self, paths):
        # A deliberate skip (welcome-screen "manage models in OpenClaw myself")
        # is an honest warn: the doctor cannot verify a hand-managed provider.
        Path(paths["provider_key_path"]).write_text("PROVIDER_SETUP_SKIPPED=1\n")
        c = doctor.check_provider_key(paths)
        assert c.status == "warn"
        assert "skipped" in c.detail and "OpenClaw" in c.detail
        assert "gateway" in c.fix_hint

    def test_skip_marker_in_env_warns(self, paths, monkeypatch):
        monkeypatch.setenv("PROVIDER_SETUP_SKIPPED", "1")
        c = doctor.check_provider_key(paths)
        assert c.status == "warn"
        assert "skipped" in c.detail

    def test_real_provider_wins_over_skip_marker(self, paths):
        # A key added later (mixed file) upgrades the skip to ok.
        Path(paths["provider_key_path"]).write_text(
            "PROVIDER_SETUP_SKIPPED=1\nOPENROUTER_API_KEY=sk-abc\n"
        )
        c = doctor.check_provider_key(paths)
        assert c.status == "ok"

    def test_key_value_never_in_detail(self, paths, monkeypatch):
        # The check must report presence only, never the value or its length.
        secret = "sk-super-secret-0123456789abcdef"
        monkeypatch.setenv("OPENROUTER_API_KEY", secret)
        env_c = doctor.check_provider_key(paths)
        assert secret not in env_c.detail and secret not in env_c.fix_hint
        # And the same for the key-file path.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        Path(paths["provider_key_path"]).write_text(f"OPENROUTER_API_KEY={secret}\n")
        file_c = doctor.check_provider_key(paths)
        assert secret not in file_c.detail and secret not in file_c.fix_hint
        # The length must not leak either.
        assert str(len(secret)) not in file_c.detail


class TestLocalModelCompleteness:
    """A role-assigned local/ model whose provider entry is under-specified
    (bare {id, name}) runs on truncating defaults; the check warns."""

    def _write(self, env, *, agents=None, local_models_list=None):
        data = _good_openclaw_json()
        if agents is not None:
            for entry in data["agents"]["list"]:
                if entry["id"] in agents:
                    entry["model"] = {"primary": agents[entry["id"]]}
        if local_models_list is not None:
            data["models"] = {"providers": {"local": {"models": local_models_list}}}
        with open(os.path.join(env["openclaw_root"], "openclaw.json"), "w") as f:
            json.dump(data, f)

    def test_no_local_roles_skips(self, env):
        self._write(env, agents={"planner": "openrouter/some/model"})
        c = doctor.check_local_model_completeness(env)
        assert c.status == "skipped"
        assert "no agent role" in c.detail

    def test_string_model_agent_does_not_crash(self, env):
        # OpenClaw also accepts model: "provider/id" as a plain string (older
        # or hand-written configs). A migrated openclaw.json carrying that
        # shape crashed this check with AttributeError and failed the whole
        # container boot (observed live 2026-07-09). Both this check and
        # model_modality must tolerate it.
        data = _good_openclaw_json()
        data["agents"]["list"].append({"id": "legacy-agent", "model": "llamacpp/qwen3.6-27b"})
        for entry in data["agents"]["list"]:
            if entry["id"] == "executor":
                entry["model"] = "local/qwen3.5"  # string form on a local role
        data["models"] = {"providers": {"local": {"models": [{"id": "qwen3.5", "name": "q"}]}}}
        with open(os.path.join(env["openclaw_root"], "openclaw.json"), "w") as f:
            json.dump(data, f)
        c = doctor.check_local_model_completeness(env)
        assert c.status in ("warn", "ok")  # no crash; string local role is seen
        m = doctor.check_model_modality(env)
        assert m.status in ("ok", "warn", "fail", "skipped")  # no crash

    def test_missing_openclaw_json_skips(self, env):
        os.remove(os.path.join(env["openclaw_root"], "openclaw.json"))
        c = doctor.check_local_model_completeness(env)
        assert c.status == "skipped"

    def test_bare_entry_warns_with_fields(self, env):
        self._write(
            env,
            agents={"executor": "local/qwen3.5", "reviewer": "local/qwen3.5"},
            local_models_list=[{"id": "qwen3.5", "name": "qwen3.5"}],
        )
        c = doctor.check_local_model_completeness(env)
        assert c.status == "warn"
        for field in ("maxTokens", "contextWindow", "reasoning"):
            assert field in c.detail
        assert "executor" in c.detail and "reviewer" in c.detail
        assert "LOCAL_MODEL_MAX_TOKENS" in c.fix_hint

    def test_unlisted_model_warns(self, env):
        self._write(
            env,
            agents={"executor": "local/ghost"},
            local_models_list=[{"id": "other", "name": "other"}],
        )
        c = doctor.check_local_model_completeness(env)
        assert c.status == "warn"
        assert "not in models.providers.local" in c.detail

    def test_complete_entry_ok(self, env):
        self._write(
            env,
            agents={"executor": "local/qwen3.5"},
            local_models_list=[
                {
                    "id": "qwen3.5",
                    "name": "qwen3.5",
                    "maxTokens": 16384,
                    "contextWindow": 131072,
                    "reasoning": True,
                    "input": ["text", "image"],
                }
            ],
        )
        c = doctor.check_local_model_completeness(env)
        assert c.status == "ok"

    def test_reasoning_false_is_complete(self, env):
        # An explicit reasoning:false (non-reasoning model) is a valid answer;
        # only an ABSENT reasoning key warns.
        self._write(
            env,
            agents={"executor": "local/llama3"},
            local_models_list=[
                {
                    "id": "llama3",
                    "name": "llama3",
                    "maxTokens": 8192,
                    "contextWindow": 16384,
                    "reasoning": False,
                    "input": ["text"],
                }
            ],
        )
        c = doctor.check_local_model_completeness(env)
        assert c.status == "ok"

    def test_non_local_roles_ignored(self, env):
        # Cloud roles never drag the check in; only the local/ ref is validated.
        self._write(
            env,
            agents={
                "planner": "openrouter/vendor/model",
                "executor": "local/qwen3.5",
            },
            local_models_list=[
                {
                    "id": "qwen3.5",
                    "name": "qwen3.5",
                    "maxTokens": 16384,
                    "contextWindow": 131072,
                    "reasoning": True,
                    "input": ["text", "image"],
                }
            ],
        )
        c = doctor.check_local_model_completeness(env)
        assert c.status == "ok"
        assert "1 role-assigned" in c.detail


class TestModelModality:
    """A text-only model on a vision-dependent role (reviewer/executor/
    prd-creator) rejects every image-bearing turn with HTTP 400 — at the LAST
    phase of a first run, after real spend, or on the first Ideas attachment.
    The check resolves modality from the config-local provider entry first and
    only falls back to the live OpenRouter probe."""

    def _write(self, env, *, agents=None, openrouter_models=None):
        data = _good_openclaw_json()
        if agents is not None:
            for entry in data["agents"]["list"]:
                if entry["id"] in agents:
                    entry["model"] = {"primary": agents[entry["id"]]}
        if openrouter_models is not None:
            data["models"] = {"providers": {"openrouter": {"models": openrouter_models}}}
        with open(os.path.join(env["openclaw_root"], "openclaw.json"), "w") as f:
            json.dump(data, f)

    def _no_probe(self, monkeypatch):
        def _boom(model_id):
            raise AssertionError(f"unexpected live OpenRouter probe for {model_id}")
        monkeypatch.setattr(doctor, "_openrouter_input_modalities", _boom)

    def test_no_models_configured_skips(self, env):
        c = doctor.check_model_modality(env)
        assert c.status == "skipped"
        assert "no reviewer/executor/prd-creator model" in c.detail

    def test_missing_openclaw_json_skips(self, env):
        os.remove(os.path.join(env["openclaw_root"], "openclaw.json"))
        c = doctor.check_model_modality(env)
        assert c.status == "skipped"

    def test_text_only_reviewer_fails_config_local(self, env, monkeypatch):
        # The likely misconfiguration path: REVIEWER_MODEL pointed at a shipped
        # text-only model. The provider entry declares input, so no network probe.
        self._no_probe(monkeypatch)
        self._write(
            env,
            agents={
                "reviewer": "openrouter/z-ai/glm-5.2",
                "executor": "openrouter/moonshotai/kimi-k2.7-code",
            },
            openrouter_models=[
                {"id": "z-ai/glm-5.2", "input": ["text"]},
                {"id": "moonshotai/kimi-k2.7-code", "input": ["text", "image"]},
            ],
        )
        c = doctor.check_model_modality(env)
        assert c.status == "fail"
        assert "reviewer=openrouter/z-ai/glm-5.2" in c.detail
        assert "REVIEWER_MODEL" in c.fix_hint

    def test_text_only_executor_warns(self, env, monkeypatch):
        self._no_probe(monkeypatch)
        self._write(
            env,
            agents={
                "reviewer": "openrouter/moonshotai/kimi-k2.7-code",
                "executor": "openrouter/z-ai/glm-5.2",
            },
            openrouter_models=[
                {"id": "z-ai/glm-5.2", "input": ["text"]},
                {"id": "moonshotai/kimi-k2.7-code", "input": ["text", "image"]},
            ],
        )
        c = doctor.check_model_modality(env)
        assert c.status == "warn"
        assert "executor=openrouter/z-ai/glm-5.2" in c.detail

    def test_text_only_prd_creator_fails(self, env, monkeypatch):
        # Ideas chat attachments are sent to the prd-creator as images; a
        # text-only PRD_MODEL is broken functionality, not a degraded mode
        # (Stage B of the per-role model selection roadmap).
        self._no_probe(monkeypatch)
        self._write(
            env,
            agents={
                "reviewer": "openrouter/moonshotai/kimi-k2.7-code",
                "executor": "openrouter/moonshotai/kimi-k2.7-code",
                "prd-creator": "openrouter/z-ai/glm-5.2",
            },
            openrouter_models=[
                {"id": "z-ai/glm-5.2", "input": ["text"]},
                {"id": "moonshotai/kimi-k2.7-code", "input": ["text", "image"]},
            ],
        )
        c = doctor.check_model_modality(env)
        assert c.status == "fail"
        assert "prd-creator=openrouter/z-ai/glm-5.2" in c.detail
        assert "PRD_MODEL" in c.fix_hint

    def test_multimodal_prd_creator_ok(self, env, monkeypatch):
        self._no_probe(monkeypatch)
        self._write(
            env,
            agents={
                "reviewer": "openrouter/moonshotai/kimi-k2.7-code",
                "executor": "openrouter/moonshotai/kimi-k2.7-code",
                "prd-creator": "openrouter/moonshotai/kimi-k2.7-code",
            },
            openrouter_models=[
                {"id": "moonshotai/kimi-k2.7-code", "input": ["text", "image"]},
            ],
        )
        c = doctor.check_model_modality(env)
        assert c.status == "ok"
        assert "prd-creator" in c.detail

    def test_both_multimodal_ok_without_probe(self, env, monkeypatch):
        self._no_probe(monkeypatch)
        self._write(
            env,
            agents={
                "reviewer": "openrouter/moonshotai/kimi-k2.7-code",
                "executor": "openrouter/minimax/minimax-m3",
            },
            openrouter_models=[
                {"id": "moonshotai/kimi-k2.7-code", "input": ["text", "image"]},
                {"id": "minimax/minimax-m3", "input": ["text", "image", "video"]},
            ],
        )
        c = doctor.check_model_modality(env)
        assert c.status == "ok"
        assert "reviewer" in c.detail and "executor" in c.detail

    def test_probe_fallback_confirms_text_only(self, env, monkeypatch):
        # No provider entry for the reviewer's model → live probe decides.
        self._write(
            env,
            agents={"reviewer": "openrouter/vendor/textmodel"},
            openrouter_models=[],
        )
        probed = []
        monkeypatch.setattr(
            doctor, "_openrouter_input_modalities",
            lambda mid: probed.append(mid) or ["text"],
        )
        c = doctor.check_model_modality(env)
        assert c.status == "fail"
        assert probed == ["vendor/textmodel"]

    def test_probe_unreachable_skips(self, env, monkeypatch):
        self._write(
            env,
            agents={"reviewer": "openrouter/vendor/mystery"},
            openrouter_models=[],
        )
        monkeypatch.setattr(doctor, "_openrouter_input_modalities", lambda mid: None)
        c = doctor.check_model_modality(env)
        assert c.status == "skipped"
        assert "could not verify" in c.detail
        assert "reviewer=openrouter/vendor/mystery" in c.detail

    def test_offline_never_probes(self, env, monkeypatch):
        # OFFLINE=1 (CI/smoke): the network fallback must not fire; an
        # undeclared model reads unverified → skipped, never fail.
        monkeypatch.setenv("OFFLINE", "1")
        self._no_probe(monkeypatch)
        self._write(
            env,
            agents={"reviewer": "openrouter/vendor/mystery"},
            openrouter_models=[],
        )
        c = doctor.check_model_modality(env)
        assert c.status == "skipped"

    def test_local_provider_entry_is_honored(self, env, monkeypatch):
        # A local/ model with a declared text-only input on the reviewer is the
        # same contract violation; no network involved.
        self._no_probe(monkeypatch)
        data = _good_openclaw_json()
        for entry in data["agents"]["list"]:
            if entry["id"] == "reviewer":
                entry["model"] = {"primary": "local/qwen3.5"}
        data["models"] = {"providers": {"local": {"models": [
            {"id": "qwen3.5", "input": ["text"]},
        ]}}}
        with open(os.path.join(env["openclaw_root"], "openclaw.json"), "w") as f:
            json.dump(data, f)
        c = doctor.check_model_modality(env)
        assert c.status == "fail"
        assert "reviewer=local/qwen3.5" in c.detail

    def test_local_without_input_defers(self, env, monkeypatch):
        # A local/ entry with no input declaration belongs to
        # local_model_completeness; this check reports unverified, not a guess.
        self._no_probe(monkeypatch)
        data = _good_openclaw_json()
        for entry in data["agents"]["list"]:
            if entry["id"] == "reviewer":
                entry["model"] = {"primary": "local/qwen3.5"}
        data["models"] = {"providers": {"local": {"models": [
            {"id": "qwen3.5", "name": "qwen3.5"},
        ]}}}
        with open(os.path.join(env["openclaw_root"], "openclaw.json"), "w") as f:
            json.dump(data, f)
        c = doctor.check_model_modality(env)
        assert c.status == "skipped"


# ── report / CLI surface ─────────────────────────────────────────────────────

class TestReportAndCli:
    def test_exit_codes(self):
        ok = DoctorReport([CheckResult("a", "t", "ok")])
        warn = DoctorReport([CheckResult("a", "t", "warn")])
        failing = DoctorReport([CheckResult("a", "t", "warn"), CheckResult("b", "t", "fail")])
        assert ok.exit_code() == 0
        assert warn.exit_code() == 2
        assert warn.exit_code(warns_ok=True) == 0
        assert failing.exit_code() == 1
        assert failing.exit_code(warns_ok=True) == 1

    def test_json_shape_and_exit_code(self, env, monkeypatch, capsys):
        monkeypatch.setenv("OPENCLAW_ROOT", env["openclaw_root"])
        monkeypatch.setenv("AUTODEV_REPO_PATH", env["autodev_repo_path"])
        monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", env["autodev_pipeline_root"])
        monkeypatch.setenv("AUTODEV_HOOKS_TOKEN", "tok-123")
        monkeypatch.setenv("AUTODEV_UI_TOKEN", "ui-tok")
        rc = doctor.main(["--json"])
        out = capsys.readouterr().out
        body = json.loads(out)
        assert set(body.keys()) == {"status", "counts", "checks"}
        assert [c["id"] for c in body["checks"]] == EXPECTED_CHECK_IDS
        for c in body["checks"]:
            assert set(c.keys()) == {"id", "title", "status", "detail", "fix_hint"}
            assert c["status"] in ("ok", "warn", "fail", "skipped")
        # CLI config defaults hooks_url to the real 18789; the gateway checks may
        # legitimately differ per machine, so only assert code consistency.
        statuses = {c["status"] for c in body["checks"]}
        if "fail" in statuses:
            assert rc == 1
        elif "warn" in statuses:
            assert rc == 2
        else:
            assert rc == 0

    def test_quiet_flag_prints_only_problems(self, env, monkeypatch, capsys):
        monkeypatch.setenv("OPENCLAW_ROOT", env["openclaw_root"])
        monkeypatch.setenv("AUTODEV_REPO_PATH", env["autodev_repo_path"])
        monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", env["autodev_pipeline_root"])
        doctor.main(["--quiet"])
        out = capsys.readouterr().out
        assert "doctor:" in out  # summary always prints
        for line in out.splitlines():
            if line.startswith("  OK") or line.startswith("  SKIP"):
                pytest.fail(f"--quiet printed a non-problem line: {line!r}")

    def test_probe_timeout_env(self, monkeypatch):
        monkeypatch.setenv("DOCTOR_PROBE_TIMEOUT", "2.5")
        assert doctor.probe_timeout() == 2.5
        monkeypatch.setenv("DOCTOR_PROBE_TIMEOUT", "garbage")
        assert doctor.probe_timeout() == 5.0
        monkeypatch.setenv("DOCTOR_PROBE_TIMEOUT", "-3")
        assert doctor.probe_timeout() == 5.0


# ── drift guards ─────────────────────────────────────────────────────────────

class TestDriftGuards:
    def test_floor_version_matches_setup_md(self):
        setup_md = (_REPO_ROOT / "SETUP.md").read_text(encoding="utf-8")
        m = re.search(
            r"## Known Compatible OpenClaw Version.*?Requires OpenClaw v(\d{4}\.\d+\.\d+) or newer",
            setup_md,
            re.DOTALL,
        )
        assert m, "SETUP.md 'Known Compatible OpenClaw Version' section not found"
        assert m.group(1) == MIN_OPENCLAW_VERSION, (
            "doctor.MIN_OPENCLAW_VERSION and SETUP.md's documented floor disagree; "
            "change them together"
        )

    def test_known_bad_table_seeded(self):
        assert "2026.6.8" in KNOWN_BAD_OPENCLAW_VERSIONS

    def test_bundle_marker_matches_install_sh(self):
        install_sh = (_REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert doctor.PLUGIN_BUNDLE_MARKER in install_sh, (
            "doctor.PLUGIN_BUNDLE_MARKER and install.sh step 11's marker drifted; "
            "all marker sites change together"
        )
