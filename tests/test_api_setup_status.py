"""Tests for GET /api/setup/status endpoint."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AGENTS = ["planner", "executor", "reviewer", "escalation", "prd-creator"]

_PRD_CREATOR_ENTRY = {
    "id": "prd-creator",
    "workspace": "~/.openclaw/workspace-prd-creator",
    "model": {"primary": "openrouter/minimax/minimax-m2.7", "fallbacks": []},
    "tools": {"allow": ["read", "write"], "deny": ["edit", "apply_patch", "exec"]},
}

_ROADMAP_CONVERTER_ENTRY = {
    "id": "roadmap-converter",
    "workspace": "~/.openclaw/workspace-roadmap-converter",
    "model": {"primary": "openrouter/minimax/minimax-m2.7", "fallbacks": []},
    "tools": {"allow": ["read", "write"], "deny": ["edit", "apply_patch", "exec"]},
}


def _make_openclaw_dir(tmp_path: Path, with_openclaw_json=True, agents_list=None) -> Path:
    """Build a fake ~/.openclaw directory structure."""
    openclaw = tmp_path / ".openclaw"
    openclaw.mkdir(parents=True, exist_ok=True)

    if with_openclaw_json:
        if agents_list is None:
            agents_list = [_PRD_CREATOR_ENTRY, _ROADMAP_CONVERTER_ENTRY]
        data = {
            "version": "1.2.0",
            "agents": {"defaults": {}, "list": agents_list},
        }
        (openclaw / "openclaw.json").write_text(json.dumps(data, indent=2))

    return openclaw


def _make_workspaces(openclaw: Path, agents=None):
    """Create workspace-{agent} directories."""
    if agents is None:
        agents = _AGENTS
    for agent in agents:
        (openclaw / f"workspace-{agent}").mkdir(exist_ok=True)


def _make_conversion_prompt(openclaw: Path) -> Path:
    prompt_dir = openclaw / "deployment-package" / "Updates"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompt_dir / "PRD to Roadmap (sonnet 4.5 ideal).txt"
    prompt_file.write_text("# Prompt\n")
    return prompt_file


def _make_config(openclaw: Path, tmp_path: Path, prompt_path: str = None) -> dict:
    """Build a config dict pointing into tmp_path."""
    if prompt_path is None:
        prompt_path = str(openclaw / "deployment-package" / "Updates" / "PRD to Roadmap (sonnet 4.5 ideal).txt")
    return {
        "openclaw_root": str(openclaw),
        "autodev_repo_path": str(openclaw),
        "pipeline_state_path": str(openclaw / "pipeline_state.json"),
        "phase_state_path": str(openclaw / "pipeline-project" / "phase_state.json"),
        "lock_path": str(openclaw / "pipeline.lock"),
        "events_path": str(openclaw / "pipeline_events.jsonl"),
        "project_dir_path": str(openclaw / "pipeline-project"),
        "conversion_prompt_path": prompt_path,
    }


def _make_setup_marker(home: Path) -> Path:
    marker = home / ".autodev_setup_complete"
    marker.write_text("2026-03-28T00:00:00Z\n")
    return marker


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResponseSchema:
    def test_returns_200(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path)
        _make_workspaces(openclaw)
        cfg = _make_config(openclaw, tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        assert r.status_code == 200

    def test_response_has_setup_complete_bool(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path)
        cfg = _make_config(openclaw, tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        data = r.json()
        assert "setup_complete" in data
        assert isinstance(data["setup_complete"], bool)

    def test_response_has_missing_items_list(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path)
        cfg = _make_config(openclaw, tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        data = r.json()
        assert "missing_items" in data
        assert isinstance(data["missing_items"], list)


class TestMissingItems:
    def test_missing_openclaw_dir_adds_openclaw_root(self, tmp_path):
        # Point to a nonexistent openclaw dir
        cfg = _make_config(tmp_path / ".nonexistent", tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        assert "openclaw_root" in r.json()["missing_items"]

    def test_missing_openclaw_json_adds_item(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path, with_openclaw_json=False)
        _make_workspaces(openclaw)
        cfg = _make_config(openclaw, tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        assert "openclaw_json" in r.json()["missing_items"]

    def test_missing_roadmap_converter_agent_adds_item(self, tmp_path):
        # openclaw.json exists but has no roadmap-converter entry
        openclaw = _make_openclaw_dir(tmp_path, agents_list=[_PRD_CREATOR_ENTRY])
        _make_workspaces(openclaw)
        cfg = _make_config(openclaw, tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        assert "roadmap_converter_agent" in r.json()["missing_items"]

    def test_roadmap_converter_registered_no_item(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path, agents_list=[_PRD_CREATOR_ENTRY, _ROADMAP_CONVERTER_ENTRY])
        _make_workspaces(openclaw)
        _make_conversion_prompt(openclaw)
        cfg = _make_config(openclaw, tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        assert "roadmap_converter_agent" not in r.json()["missing_items"]

    def test_missing_workspace_planner_adds_item(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path)
        # Create all workspaces except planner
        _make_workspaces(openclaw, agents=[a for a in _AGENTS if a != "planner"])
        cfg = _make_config(openclaw, tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        assert "workspace-planner" in r.json()["missing_items"]

    def test_missing_workspace_prd_creator_adds_item(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path)
        _make_workspaces(openclaw, agents=[a for a in _AGENTS if a != "prd-creator"])
        cfg = _make_config(openclaw, tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        assert "workspace-prd-creator" in r.json()["missing_items"]

    def test_missing_conversion_prompt_adds_item(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path)
        _make_workspaces(openclaw)
        cfg = _make_config(openclaw, tmp_path,
                           prompt_path=str(tmp_path / "nonexistent_prompt.txt"))
        real_isfile = os.path.isfile

        def isfile_no_bundle(p):
            s = os.path.abspath(str(p))
            if s.endswith("prd-to-roadmap-conversion.txt"):
                return False
            return real_isfile(p)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False), \
             patch("ui.server.os.path.isfile", side_effect=isfile_no_bundle):
            r = client.get("/api/setup/status")

        assert "conversion_prompt" in r.json()["missing_items"]


class TestSetupComplete:
    def test_setup_complete_false_when_marker_absent(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path)
        _make_workspaces(openclaw)
        _make_conversion_prompt(openclaw)
        cfg = _make_config(openclaw, tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        assert r.json()["setup_complete"] is False

    def test_setup_complete_false_when_missing_items_present(self, tmp_path):
        # Marker exists but openclaw_root missing → not fully complete
        cfg = _make_config(tmp_path / ".nonexistent", tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", side_effect=lambda p: ".autodev_setup_complete" in str(p)):
            r = client.get("/api/setup/status")

        data = r.json()
        # missing_items non-empty → setup_complete False
        assert len(data["missing_items"]) > 0
        assert data["setup_complete"] is False

    def test_setup_complete_true_when_marker_and_all_clear(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path)
        _make_workspaces(openclaw)
        _make_conversion_prompt(openclaw)
        cfg = _make_config(openclaw, tmp_path)

        # Mock os.path.exists: return True for marker, delegate to real fs for others
        real_exists = os.path.exists

        def exists_side_effect(p):
            if ".autodev_setup_complete" in str(p):
                return True
            return real_exists(p)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", side_effect=exists_side_effect):
            r = client.get("/api/setup/status")

        data = r.json()
        assert data["missing_items"] == [], f"Unexpected missing items: {data['missing_items']}"
        assert data["setup_complete"] is True


class TestExecApprovals:
    def test_stale_exec_approvals_adds_item(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path)
        _make_workspaces(openclaw)
        # Write exec-approvals.json with a stale path (not under current autodev_repo_path)
        approvals = {
            "version": 1,
            "agents": {
                "pipeline:phase-1": {
                    "/old/path/gate_scripts/planner_gate.py": {"approved": True}
                }
            },
        }
        (openclaw / "exec-approvals.json").write_text(json.dumps(approvals))
        cfg = _make_config(openclaw, tmp_path)
        # autodev_repo_path (openclaw dir) doesn't contain "/old/path/" so it's stale
        cfg["autodev_repo_path"] = str(openclaw)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        assert "exec_approvals_stale_paths" in r.json()["missing_items"]

    def test_no_exec_approvals_file_no_stale_item(self, tmp_path):
        openclaw = _make_openclaw_dir(tmp_path)
        _make_workspaces(openclaw)
        cfg = _make_config(openclaw, tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/setup/status")

        # Missing exec-approvals.json is not itself a blocking missing_item
        assert "exec_approvals_stale_paths" not in r.json()["missing_items"]


class TestOpenclawRootVsRepo:
    def test_openclaw_layout_not_required_under_repo_path(self, tmp_path):
        """Agent workspaces and openclaw.json live under openclaw_root, not the git repo."""
        openclaw = _make_openclaw_dir(tmp_path)
        _make_workspaces(openclaw)
        _make_conversion_prompt(openclaw)
        repo_only = tmp_path / "git_repo"
        repo_only.mkdir()
        cfg = {
            "openclaw_root": str(openclaw),
            "autodev_repo_path": str(repo_only),
            "conversion_prompt_path": str(
                openclaw / "deployment-package" / "Updates" / "PRD to Roadmap (sonnet 4.5 ideal).txt"
            ),
        }
        real_exists = os.path.exists

        def exists_side_effect(p):
            if ".autodev_setup_complete" in str(p):
                return True
            return real_exists(p)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", side_effect=exists_side_effect):
            r = client.get("/api/setup/status")

        assert r.json()["missing_items"] == []
        assert r.json()["setup_complete"] is True
