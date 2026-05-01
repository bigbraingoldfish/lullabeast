"""Tests for W5-C: GET /api/completion-report endpoint."""

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def load_client():
    from ui.server import app
    return TestClient(app)


def _base_config(tmp_path: Path) -> dict:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    return {
        "project_dir_path": str(project_dir),
        "openclaw_root": str(tmp_path),
        "autodev_pipeline_root": str(tmp_path),
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "lock_path": str(tmp_path / "pipeline.lock"),
        "pipeline_queue_path": str(tmp_path / "pipeline_queue.json"),
        "events_path": str(tmp_path / "pipeline_events.jsonl"),
    }


class TestGetCompletionReport:

    def test_returns_found_false_when_no_file(self, tmp_path):
        """If completion_report.md does not exist, found must be False."""
        config = _base_config(tmp_path)
        client = load_client()

        with patch("ui.server.load_config", return_value=config):
            resp = client.get("/api/completion-report")

        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False
        assert data["content"] == ""
        assert data["mtime"] is None

    def test_returns_content_when_file_exists(self, tmp_path):
        """If completion_report.md exists, return found=True, content, and mtime."""
        config = _base_config(tmp_path)
        report_path = Path(config["project_dir_path"]) / "completion_report.md"
        report_path.write_text("# Report\n\nAll done.\n", encoding="utf-8")
        mtime_before = time.time()

        client = load_client()
        with patch("ui.server.load_config", return_value=config):
            resp = client.get("/api/completion-report")

        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is True
        assert "All done." in data["content"]
        assert isinstance(data["mtime"], float)
        assert data["mtime"] > 0

    def test_returns_found_false_when_no_project_dir_configured(self, tmp_path):
        """If project_dir_path is empty/missing, return found=False gracefully."""
        config = _base_config(tmp_path)
        config["project_dir_path"] = ""

        client = load_client()
        with patch("ui.server.load_config", return_value=config):
            resp = client.get("/api/completion-report")

        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False

    def test_returns_found_false_when_project_dir_does_not_exist(self, tmp_path):
        """Non-existent project_dir_path yields found=False without error."""
        config = _base_config(tmp_path)
        config["project_dir_path"] = str(tmp_path / "nonexistent")

        client = load_client()
        with patch("ui.server.load_config", return_value=config):
            resp = client.get("/api/completion-report")

        assert resp.status_code == 200
        assert resp.json()["found"] is False

    def test_mtime_is_float_epoch_seconds(self, tmp_path):
        """mtime field must be a float (epoch seconds), not a string or int."""
        config = _base_config(tmp_path)
        report_path = Path(config["project_dir_path"]) / "completion_report.md"
        report_path.write_text("# Test\n", encoding="utf-8")

        client = load_client()
        with patch("ui.server.load_config", return_value=config):
            resp = client.get("/api/completion-report")

        data = resp.json()
        assert isinstance(data["mtime"], float)
        # mtime should be a plausible epoch value (after year 2020)
        assert data["mtime"] > 1_580_000_000

    def test_content_is_full_file_text(self, tmp_path):
        """content field must contain the entire file text, not a truncated version."""
        config = _base_config(tmp_path)
        long_text = "# Report\n" + "x" * 5000 + "\n"
        report_path = Path(config["project_dir_path"]) / "completion_report.md"
        report_path.write_text(long_text, encoding="utf-8")

        client = load_client()
        with patch("ui.server.load_config", return_value=config):
            resp = client.get("/api/completion-report")

        data = resp.json()
        assert data["found"] is True
        assert len(data["content"]) == len(long_text)
