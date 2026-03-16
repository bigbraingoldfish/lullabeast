"""Tests for GET /api/events endpoint."""
import json
import os
import tempfile
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from ui.server import app, load_config

client = TestClient(app)

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

@pytest.fixture
def mock_config(temp_dir):
    return {
        "pipeline_state_path": os.path.join(temp_dir, "pipeline_state.json"),
        "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
        "lock_path": os.path.join(temp_dir, "pipeline.lock"),
        "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
        "roadmap_path": os.path.join(temp_dir, "roadmap.md"),
        "project_dir_path": os.path.join(temp_dir, "project"),
    }

@pytest.fixture
def mock_events_jsonl(temp_dir):
    path = os.path.join(temp_dir, "pipeline_events.jsonl")
    events = [
        {"ts": "2026-03-16T10:00:00Z", "event": "started", "agent": "planner", "phase": "planning", "detail": "phase started"},
        {"ts": "2026-03-16T10:01:00Z", "event": "status_changed", "agent": "planner", "phase": "planning", "detail": "changes: status"},
        {"ts": "2026-03-16T10:02:00Z", "event": "completed", "agent": "planner", "phase": "planning", "detail": "phase done"},
        {"ts": "2026-03-16T10:03:00Z", "event": "started", "agent": "executor", "phase": "executing", "detail": "phase started"},
        {"ts": "2026-03-16T10:04:00Z", "event": "status_changed", "agent": "executor", "phase": "executing", "detail": "changes: agent"},
    ]
    with open(path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
    return path

@pytest.fixture
def mock_malformed_jsonl(temp_dir):
    path = os.path.join(temp_dir, "pipeline_events.jsonl")
    with open(path, "w") as f:
        f.write('{"ts": "2026-03-16T10:00:00Z", "event": "valid1"}\n')
        f.write('this is not valid json\n')
        f.write('{"ts": "2026-03-16T10:01:00Z", "event": "valid2"}\n')
        f.write('\n')
        f.write('{"ts": "2026-03-16T10:02:00Z", "event": "valid3"}\n')
    return path

class TestApiEvents:
    def test_events_returns_200(self, mock_config):
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/events")
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert "source" in data
        assert "total" in data
        assert isinstance(data["events"], list)

    def test_events_default_limit(self, mock_config):
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/events")
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "synthetic"
        assert data["total"] == 0
        assert data["events"] == []

    def test_events_empty_buffer(self, mock_config):
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/events")
        assert response.status_code == 200
        data = response.json()
        assert data["events"] == []
        assert data["source"] == "synthetic"
        assert data["total"] == 0

    def test_events_with_limit_and_offset(self, mock_config):
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/events?limit=2&offset=0")
            assert response.status_code == 200
            data = response.json()
            assert len(data["events"]) <= 2

    def test_events_offset_pagination(self, mock_config):
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/events?limit=2&offset=2")
            assert response.status_code == 200

    def test_events_from_file_source(self, mock_config, mock_events_jsonl):
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/events")
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "file"
        assert len(data["events"]) == 5
        assert data["events"][0]["ts"] >= data["events"][-1]["ts"]

    def test_events_file_reverse_order(self, mock_config, mock_events_jsonl):
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/events")
        data = response.json()
        events = data["events"]
        for i in range(len(events) - 1):
            assert events[i]["ts"] >= events[i + 1]["ts"]

    def test_events_file_limit(self, mock_config, mock_events_jsonl):
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/events?limit=3")
        data = response.json()
        assert len(data["events"]) == 3
        assert data["total"] == 5

    def test_events_file_offset(self, mock_config, mock_events_jsonl):
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/events?limit=2&offset=2")
        data = response.json()
        assert len(data["events"]) == 2

    def test_events_malformed_lines_skipped(self, mock_config, mock_malformed_jsonl):
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/events")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
