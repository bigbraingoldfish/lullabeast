"""Tests that GET /api/state includes setup_complete field based on ~/.autodev_setup_complete."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Minimal config that satisfies get_state() without real files
# ---------------------------------------------------------------------------

def _min_config(tmp_path: Path) -> dict:
    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps({"pipeline_status": "IDLE", "current_phase": 0}))
    return {
        "pipeline_state_path": str(state_file),
        "phase_state_path": str(tmp_path / "phase_state.json"),
        "lock_path": str(tmp_path / "pipeline.lock"),
        "events_path": str(tmp_path / "events.jsonl"),
        "project_dir_path": str(tmp_path / "pipeline-project"),
        "autodev_repo_path": str(tmp_path),
    }


SETUP_MARKER = os.path.expanduser("~/.autodev_setup_complete")


class TestSetupCompleteField:
    def test_setup_complete_true_when_marker_exists(self, tmp_path):
        cfg = _min_config(tmp_path)
        marker = tmp_path / "marker"
        marker.touch()

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", side_effect=lambda p: str(p) == str(marker) or ".autodev_setup_complete" in str(p)):
            r = client.get("/api/state")

        assert r.status_code == 200
        assert r.json()["setup_complete"] is True

    def test_setup_complete_false_when_marker_absent(self, tmp_path):
        cfg = _min_config(tmp_path)

        # os.path.exists returns False for the setup marker path
        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/state")

        assert r.status_code == 200
        assert r.json()["setup_complete"] is False

    def test_setup_complete_key_always_present(self, tmp_path):
        """Key is present even when pipeline_state.json is missing."""
        cfg = {
            "pipeline_state_path": str(tmp_path / "missing.json"),
            "phase_state_path": str(tmp_path / "missing_phase.json"),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "events_path": str(tmp_path / "events.jsonl"),
            "project_dir_path": str(tmp_path / "pipeline-project"),
            "autodev_repo_path": str(tmp_path),
        }
        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/state")

        assert r.status_code == 200
        assert "setup_complete" in r.json()

    def test_setup_complete_is_boolean(self, tmp_path):
        cfg = _min_config(tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=True):
            r = client.get("/api/state")

        assert r.status_code == 200
        value = r.json()["setup_complete"]
        assert isinstance(value, bool), f"Expected bool, got {type(value)}: {value!r}"

    def test_setup_complete_false_returns_bool_not_none(self, tmp_path):
        cfg = _min_config(tmp_path)

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server.os.path.exists", return_value=False):
            r = client.get("/api/state")

        assert r.status_code == 200
        value = r.json()["setup_complete"]
        assert value is False, f"Expected False, got {value!r}"
