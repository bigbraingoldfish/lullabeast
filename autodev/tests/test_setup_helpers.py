"""Installer helpers: exec-approvals refresh, .env merge."""

import json
from pathlib import Path

from autodev.installer import setup_helpers


def test_refresh_exec_approvals_rewrites_stale_gate_path(tmp_path):
    repo = tmp_path / "repo"
    gate_dir = repo / "autodev" / "pipeline" / "gate_scripts"
    gate_dir.mkdir(parents=True)
    g = gate_dir / "planner_gate.py"
    g.write_text("# gate")
    approvals = tmp_path / "exec-approvals.json"
    old = "/nope/gate_scripts/planner_gate.py"
    approvals.write_text(
        json.dumps({"version": 1, "agents": {"x": {old: {"approved": True}}}})
    )
    assert setup_helpers.refresh_exec_approvals_gate_paths(str(approvals), str(repo)) == "updated"
    data = json.loads(approvals.read_text())
    assert old not in json.dumps(data)
    assert str(g) in json.dumps(data)


def test_set_openclaw_global_tools_profile_updates(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"version": 1, "tools": {"profile": "minimal"}}))
    assert setup_helpers.set_openclaw_global_tools_profile(str(oc), "coding") == "updated"
    data = json.loads(oc.read_text())
    assert data["tools"]["profile"] == "coding"


def test_set_openclaw_global_tools_profile_unchanged(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"tools": {"profile": "coding"}}))
    assert setup_helpers.set_openclaw_global_tools_profile(str(oc), "coding") == "unchanged"


def test_patch_openclaw_hooks_creates_hooks_with_token(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"version": "1"}))
    r = setup_helpers.patch_openclaw_hooks_baseline(
        str(oc), token_if_missing="pipeline-secret-test"
    )
    assert r == "updated"
    data = json.loads(oc.read_text())
    h = data["hooks"]
    assert h["enabled"] is True
    assert h["token"] == "pipeline-secret-test"
    assert h["allowRequestSessionKey"] is True
    assert "pipeline:" in h["allowedSessionKeyPrefixes"]
    assert "ideas:" in h["allowedSessionKeyPrefixes"]


def test_patch_openclaw_hooks_preserves_existing_token(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(
        json.dumps(
            {
                "hooks": {
                    "enabled": True,
                    "token": "user-secret",
                    "allowRequestSessionKey": True,
                    "allowedSessionKeyPrefixes": ["pipeline:"],
                }
            }
        )
    )
    r = setup_helpers.patch_openclaw_hooks_baseline(
        str(oc), token_if_missing="would-not-use"
    )
    assert r in ("updated", "unchanged")
    data = json.loads(oc.read_text())
    assert data["hooks"]["token"] == "user-secret"
    assert "ideas:" in data["hooks"]["allowedSessionKeyPrefixes"]


def test_patch_openclaw_hooks_merges_prefixes_without_clobber(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(
        json.dumps(
            {
                "hooks": {
                    "token": "t",
                    "allowedSessionKeyPrefixes": ["custom:", "pipeline:"],
                }
            }
        )
    )
    assert setup_helpers.patch_openclaw_hooks_baseline(str(oc)) == "updated"
    prefs = json.loads(oc.read_text())["hooks"]["allowedSessionKeyPrefixes"]
    assert prefs[0] == "custom:"
    assert "pipeline:" in prefs
    assert "ideas:" in prefs


def test_patch_openclaw_hooks_without_token_skips_token_but_fixes_flags(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"hooks": {}}))
    r = setup_helpers.patch_openclaw_hooks_baseline(str(oc), token_if_missing=None)
    assert r == "updated"
    data = json.loads(oc.read_text())
    assert "token" not in data["hooks"] or data["hooks"].get("token") in (None, "")
    assert data["hooks"]["enabled"] is True
    assert data["hooks"]["allowRequestSessionKey"] is True


def test_patch_openclaw_hooks_missing_file(tmp_path):
    missing = tmp_path / "nope.json"
    assert setup_helpers.patch_openclaw_hooks_baseline(str(missing)).startswith("error:")


def test_openclaw_hooks_issues_detects_gaps(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"version": "1"}))
    iss = setup_helpers.openclaw_hooks_issues(str(oc))
    assert "no_hooks_object" in iss

    oc.write_text(
        json.dumps(
            {
                "hooks": {
                    "enabled": True,
                    "token": "x",
                    "allowRequestSessionKey": True,
                    "allowedSessionKeyPrefixes": ["pipeline:", "ideas:"],
                }
            }
        )
    )
    assert setup_helpers.openclaw_hooks_issues(str(oc)) == []


def test_merge_dotenv_missing_keys_appends(tmp_path):
    envp = tmp_path / ".env"
    envp.write_text("AUTODEV_ROOT=/a\n")
    r = setup_helpers.merge_dotenv_missing_keys(
        str(envp),
        {"AUTODEV_REPO_PATH": "/r", "AUTODEV_RUNTIME_ROOT": "/r/.autodev"},
    )
    assert r == "updated"
    text = envp.read_text()
    assert "AUTODEV_REPO_PATH=/r" in text
    assert "AUTODEV_RUNTIME_ROOT=" in text
