"""Tests for autodev/installer/register_agent.py — register_roadmap_converter function."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "autodev", "installer"))
from register_agent import register_roadmap_converter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRD_CREATOR_ENTRY = {
    "id": "prd-creator",
    "workspace": "~/.openclaw/workspace-prd-creator",
    "model": {"primary": "openrouter/minimax/minimax-m2.7", "fallbacks": []},
    "tools": {
        "allow": ["read", "write"],
        "deny": ["edit", "apply_patch", "exec", "process", "browser"],
    },
}

_ROADMAP_CONVERTER_ENTRY = {
    "id": "roadmap-converter",
    "workspace": "/fake/openclaw/workspace-roadmap-converter",
    "model": {"primary": "openrouter/minimax/minimax-m2.7", "fallbacks": []},
    "tools": {
        "allow": ["read", "write"],
        "deny": ["edit", "apply_patch", "exec", "process", "browser"],
    },
}


def _base_openclaw_json(agents_list=None):
    """Return a minimal but realistic openclaw.json dict."""
    if agents_list is None:
        agents_list = [_PRD_CREATOR_ENTRY]
    return {
        "version": "1.2.0",
        "auth": {"profile": "anthropic:default"},
        "agents": {
            "defaults": {"model": "openrouter/minimax/minimax-m2.7"},
            "list": agents_list,
        },
        "tools": {"profile": "coding"},
    }


def _write_openclaw_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAlreadyRegistered:
    def test_already_registered_returns_already_registered(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        data = _base_openclaw_json(agents_list=[_PRD_CREATOR_ENTRY, _ROADMAP_CONVERTER_ENTRY])
        _write_openclaw_json(oc_json, data)

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result == "already_registered"

    def test_already_registered_does_not_write(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        data = _base_openclaw_json(agents_list=[_PRD_CREATOR_ENTRY, _ROADMAP_CONVERTER_ENTRY])
        _write_openclaw_json(oc_json, data)
        mtime_before = oc_json.stat().st_mtime

        register_roadmap_converter(str(oc_json), str(tmp_path))
        assert oc_json.stat().st_mtime == mtime_before


class TestMissingPrdCreator:
    def test_missing_prd_creator_returns_error_code(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        data = _base_openclaw_json(agents_list=[])  # no prd-creator
        _write_openclaw_json(oc_json, data)

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result == "missing_prd_creator"

    def test_missing_prd_creator_does_not_write(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json(agents_list=[]))
        mtime_before = oc_json.stat().st_mtime

        register_roadmap_converter(str(oc_json), str(tmp_path))
        assert oc_json.stat().st_mtime == mtime_before


class TestSuccessfulRegistration:
    def test_registers_when_missing_returns_registered(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result == "registered"

    def test_registered_entry_has_correct_id(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())
        register_roadmap_converter(str(oc_json), str(tmp_path))

        updated = json.loads(oc_json.read_text())
        ids = [a["id"] for a in updated["agents"]["list"]]
        assert "roadmap-converter" in ids

    def test_registered_entry_copies_model_from_prd_creator(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())
        register_roadmap_converter(str(oc_json), str(tmp_path))

        updated = json.loads(oc_json.read_text())
        rc = next(a for a in updated["agents"]["list"] if a["id"] == "roadmap-converter")
        assert rc["model"] == _PRD_CREATOR_ENTRY["model"]

    def test_registered_entry_has_correct_workspace(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())
        register_roadmap_converter(str(oc_json), str(tmp_path))

        updated = json.loads(oc_json.read_text())
        rc = next(a for a in updated["agents"]["list"] if a["id"] == "roadmap-converter")
        assert rc["workspace"] == str(tmp_path) + "/workspace-roadmap-converter"

    def test_registered_entry_copies_tools_from_prd_creator(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())
        register_roadmap_converter(str(oc_json), str(tmp_path))

        updated = json.loads(oc_json.read_text())
        rc = next(a for a in updated["agents"]["list"] if a["id"] == "roadmap-converter")
        assert rc["tools"] == _PRD_CREATOR_ENTRY["tools"]

    def test_preserves_all_existing_top_level_keys(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        data = _base_openclaw_json()
        data["extra_key"] = "must_survive"
        data["tools"] = {"profile": "coding", "custom": True}
        _write_openclaw_json(oc_json, data)

        register_roadmap_converter(str(oc_json), str(tmp_path))
        updated = json.loads(oc_json.read_text())

        assert updated["extra_key"] == "must_survive"
        assert updated["tools"] == {"profile": "coding", "custom": True}
        assert updated["version"] == "1.2.0"
        assert updated["auth"] == {"profile": "anthropic:default"}

    def test_preserves_existing_agents_list_entries(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        planner = {"id": "planner", "workspace": "~/.openclaw/workspace-planner"}
        data = _base_openclaw_json(agents_list=[_PRD_CREATOR_ENTRY, planner])
        _write_openclaw_json(oc_json, data)

        register_roadmap_converter(str(oc_json), str(tmp_path))
        updated = json.loads(oc_json.read_text())

        ids = [a["id"] for a in updated["agents"]["list"]]
        assert "prd-creator" in ids
        assert "planner" in ids
        assert "roadmap-converter" in ids

    def test_atomic_write_calls_os_replace(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())

        # Capture original BEFORE entering the patch context
        original_replace = os.replace

        with patch("register_agent.os.replace") as mock_replace:
            mock_replace.side_effect = original_replace
            register_roadmap_converter(str(oc_json), str(tmp_path))

        mock_replace.assert_called_once()
        # Destination must be the original json path
        assert mock_replace.call_args[0][1] == str(oc_json)


class TestDryRun:
    def test_dry_run_returns_dry_run(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())

        result = register_roadmap_converter(str(oc_json), str(tmp_path), dry_run=True)
        assert result == "dry_run"

    def test_dry_run_does_not_write(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())
        original_text = oc_json.read_text()

        register_roadmap_converter(str(oc_json), str(tmp_path), dry_run=True)
        assert oc_json.read_text() == original_text

    def test_dry_run_already_registered_returns_already_registered(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        data = _base_openclaw_json(agents_list=[_PRD_CREATOR_ENTRY, _ROADMAP_CONVERTER_ENTRY])
        _write_openclaw_json(oc_json, data)

        result = register_roadmap_converter(str(oc_json), str(tmp_path), dry_run=True)
        assert result == "already_registered"


class TestErrorHandling:
    def test_missing_file_returns_error(self, tmp_path):
        result = register_roadmap_converter(str(tmp_path / "nonexistent.json"), str(tmp_path))
        assert result.startswith("error:")

    def test_invalid_json_returns_error(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        oc_json.write_text("this is not valid json {{{")

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result.startswith("error:")

    def test_missing_agents_key_returns_error(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        oc_json.write_text(json.dumps({"version": "1.0"}))

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result.startswith("error:")
