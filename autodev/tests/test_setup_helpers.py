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
