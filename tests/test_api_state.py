"""Tests for GET /api/state endpoint."""
import fcntl
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app, load_config


client = TestClient(app)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def mock_config(temp_dir):
    """Create a mock configuration with temp directory paths."""
    project_root = os.path.join(temp_dir, "pipeline_project")
    os.makedirs(project_root, exist_ok=True)
    return {
        "pipeline_state_path": os.path.join(temp_dir, "pipeline_state.json"),
        "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
        "lock_path": os.path.join(temp_dir, "pipeline.lock"),
        "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
        "project_dir_path": project_root,
    }


@pytest.fixture
def mock_pipeline_state(temp_dir):
    """Create a mock pipeline_state.json file."""
    state = {
        "pipeline_status": "RUNNING",
        "current_phase": "executor",
        "counters": {"success": 5, "failure": 2, "retry": 1},
    }
    path = os.path.join(temp_dir, "pipeline_state.json")
    with open(path, "w") as f:
        json.dump(state, f)
    return path


@pytest.fixture
def mock_phase_state(temp_dir):
    """Create a mock phase_state.json file."""
    state = {
        "phase": "executor",
        "last_error_code": "TIMEOUT",
        "escalation_resets": 3,
    }
    path = os.path.join(temp_dir, "phase_state.json")
    with open(path, "w") as f:
        json.dump(state, f)
    return path


@pytest.fixture
def mock_events_file(temp_dir):
    """Create a mock events file."""
    path = os.path.join(temp_dir, "pipeline_events.jsonl")
    with open(path, "w") as f:
        f.write('{"event": "test"}\n')
    return path


class TestReadJsonFile:
    """Tests for _read_json_file helper."""

    def test_read_existing_file(self, temp_dir):
        """Test reading a valid JSON file."""
        from ui.server import _read_json_file
        
        path = os.path.join(temp_dir, "test.json")
        with open(path, "w") as f:
            json.dump({"key": "value"}, f)
        
        result = _read_json_file(path)
        assert result == {"key": "value"}

    def test_read_missing_file(self, temp_dir):
        """Test reading a non-existent file returns None."""
        from ui.server import _read_json_file
        
        result = _read_json_file(os.path.join(temp_dir, "nonexistent.json"))
        assert result is None

    def test_read_invalid_json(self, temp_dir):
        """Test reading invalid JSON returns None."""
        from ui.server import _read_json_file
        
        path = os.path.join(temp_dir, "invalid.json")
        with open(path, "w") as f:
            f.write("{invalid json}")
        
        result = _read_json_file(path)
        assert result is None


class TestCheckOrchestratorLiveness:
    """Tests for _check_orchestrator_liveness helper."""

    def test_lock_acquirable_returns_false(self, temp_dir):
        """Test that when lock is acquirable, returns False."""
        from ui.server import _check_orchestrator_liveness
        
        lock_path = os.path.join(temp_dir, "pipeline.lock")
        result = _check_orchestrator_liveness(lock_path)
        assert result is False

    def test_lock_held_returns_true(self, temp_dir):
        """Test that when lock is held, returns True."""
        from ui.server import _check_orchestrator_liveness
        
        lock_path = os.path.join(temp_dir, "pipeline.lock")
        
        # Acquire lock in another process simulation
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        
        try:
            result = _check_orchestrator_liveness(lock_path)
            assert result is True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()


class TestOrchestratorSpawnLogTail:
    """Tests for B-04: _read_log_tail_lines and orchestrator spawn log path."""

    def test_read_log_tail_returns_last_n_lines(self, temp_dir):
        from ui.server import _read_log_tail_lines

        path = os.path.join(temp_dir, "spawn.log")
        with open(path, "w", encoding="utf-8") as f:
            for i in range(1, 8):
                f.write(f"line{i}\n")
        assert _read_log_tail_lines(path, 5) == ["line3", "line4", "line5", "line6", "line7"]

    def test_read_log_tail_missing_file(self, temp_dir):
        from ui.server import _read_log_tail_lines

        assert _read_log_tail_lines(os.path.join(temp_dir, "nope.log"), 5) == []

    def test_read_log_tail_empty_file(self, temp_dir):
        from ui.server import _read_log_tail_lines

        path = os.path.join(temp_dir, "empty.log")
        with open(path, "w"):
            pass
        assert _read_log_tail_lines(path, 5) == []

    def test_read_log_tail_invalid_utf8_does_not_raise(self, temp_dir):
        from ui.server import _read_log_tail_lines

        path = os.path.join(temp_dir, "badbytes.log")
        with open(path, "wb") as f:
            f.write(b"ok\n\xff\xfe\nlast\n")
        out = _read_log_tail_lines(path, 5)
        assert isinstance(out, list)
        assert len(out) >= 1
        assert out[-1] == "last"


class TestDetermineEventSource:
    """Tests for _determine_event_source helper."""

    def test_file_exists_returns_file(self, temp_dir):
        """Test that when events file exists, returns 'file'."""
        from ui.server import _determine_event_source
        
        events_path = os.path.join(temp_dir, "pipeline_events.jsonl")
        with open(events_path, "w") as f:
            f.write("test\n")
        
        result = _determine_event_source(events_path)
        assert result == "file"

    def test_file_missing_returns_synthetic(self, temp_dir):
        """Test that when events file missing, returns 'synthetic'."""
        from ui.server import _determine_event_source
        
        result = _determine_event_source(os.path.join(temp_dir, "nonexistent.jsonl"))
        assert result == "synthetic"


class TestApiStateEndpoint:
    """Tests for GET /api/state endpoint."""

    def test_returns_200_with_merged_state(self, mock_config, mock_pipeline_state, mock_phase_state, mock_events_file, temp_dir):
        """Test endpoint returns 200 with merged state when all files exist and lock is free."""
        orch_log = os.path.join(temp_dir, "orch_spawn.log")
        with open(orch_log, "w", encoding="utf-8") as f:
            f.write("x\ny\nz\n")
        with patch("ui.server.ORCHESTRATOR_SPAWN_LOG_PATH", orch_log):
            with patch("ui.server.load_config", return_value=mock_config):
                response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert data["pipeline_status"] == "RUNNING"
        assert data["current_phase"] == "executor"
        assert data["counters"]["success"] == 5
        assert data["counters"]["failure"] == 2
        assert data["counters"]["retry"] == 1
        assert data["last_error_code"] == "TIMEOUT"
        assert data["escalation_resets"] == 3
        assert data["orchestrator_alive"] is False
        assert data["event_source"] == "file"
        assert data.get("project_dir_ok") is True
        assert data.get("project_dir_message") is None
        assert data.get("orchestrator_spawn_log_tail") == ["x", "y", "z"]

    def test_orchestrator_spawn_log_tail_empty_when_alive(self, mock_config, mock_pipeline_state, mock_phase_state, mock_events_file, temp_dir):
        orch_log = os.path.join(temp_dir, "orch_spawn.log")
        with open(orch_log, "w", encoding="utf-8") as f:
            f.write("only\nwhen\n")
        lock_path = mock_config["lock_path"]
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            with patch("ui.server.ORCHESTRATOR_SPAWN_LOG_PATH", orch_log):
                with patch("ui.server.load_config", return_value=mock_config):
                    response = client.get("/api/state")
            assert response.status_code == 200
            assert response.json().get("orchestrator_spawn_log_tail") == []
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def test_orchestrator_spawn_log_tail_empty_when_not_mid_flight(self, temp_dir):
        """WAITING_FOR_HUMAN + dead orchestrator does not attach spawn log tail (B-04 panel gate)."""
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"pipeline_status": "WAITING_FOR_HUMAN", "project_path": "/tmp"}, f)
        orch_log = os.path.join(temp_dir, "orch_spawn.log")
        with open(orch_log, "w", encoding="utf-8") as f:
            f.write("secret\n")
        project_root = os.path.join(temp_dir, "pipeline_project")
        os.makedirs(project_root, exist_ok=True)
        cfg = {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": project_root,
        }
        with patch("ui.server.ORCHESTRATOR_SPAWN_LOG_PATH", orch_log):
            with patch("ui.server.load_config", return_value=cfg):
                response = client.get("/api/state")
        assert response.status_code == 200
        assert response.json().get("orchestrator_spawn_log_tail") == []

    def test_returns_unknown_when_pipeline_state_missing(self, mock_config, mock_phase_state):
        """Test endpoint returns defaults when pipeline_state.json is absent."""
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/state")
        
        assert response.status_code == 200
        data = response.json()
        assert data["pipeline_status"] == "UNKNOWN"
        assert data["counters"]["success"] == 0
        assert data["counters"]["failure"] == 0
        assert data["counters"]["retry"] == 0
        assert data["orchestrator_alive"] is False
        assert data.get("project_dir_ok") is True

    def test_project_dir_ok_false_when_symlink_dangling(self, temp_dir, mock_pipeline_state, mock_phase_state, mock_events_file):
        openclaw = os.path.join(temp_dir, ".openclaw")
        os.makedirs(openclaw, exist_ok=True)
        bad_link = os.path.join(openclaw, "pipeline-project")
        os.symlink("/tmp/nonexistent_autodev_target_xyz", bad_link)
        cfg = {
            "pipeline_state_path": os.path.join(temp_dir, "pipeline_state.json"),
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": bad_link,
        }
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")
        assert response.status_code == 200
        data = response.json()
        assert data.get("project_dir_ok") is False
        assert data.get("project_dir_message")
        assert "broken" in data["project_dir_message"].lower() or "missing" in data["project_dir_message"].lower()

    def test_omits_phase_fields_when_phase_state_missing(self, mock_config, mock_pipeline_state):
        """Test endpoint omits last_error_code and escalation_resets when phase_state.json is absent."""
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/state")
        
        assert response.status_code == 200
        data = response.json()
        assert "last_error_code" not in data
        assert "escalation_resets" not in data

    def test_returns_synthetic_when_events_file_missing(self, mock_config, mock_pipeline_state):
        """Test endpoint returns event_source: 'synthetic' when events file doesn't exist."""
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/state")
        
        assert response.status_code == 200
        data = response.json()
        assert data["event_source"] == "synthetic"

    def test_returns_orchestrator_alive_true_when_lock_held(self, mock_config, mock_pipeline_state, mock_phase_state):
        """Test endpoint returns orchestrator_alive: true when lock is held."""
        lock_path = mock_config["lock_path"]
        
        # Acquire lock to simulate orchestrator holding it
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        
        try:
            with patch("ui.server.load_config", return_value=mock_config):
                response = client.get("/api/state")
            
            assert response.status_code == 200
            data = response.json()
            assert data["orchestrator_alive"] is True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def test_handles_invalid_json_gracefully(self, mock_config, temp_dir):
        """Test endpoint handles invalid JSON without 500 error."""
        # Create invalid pipeline_state.json
        invalid_path = mock_config["pipeline_state_path"]
        with open(invalid_path, "w") as f:
            f.write("{invalid")
        
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/state")
        
        # Should return defaults, not 500
        assert response.status_code == 200
        data = response.json()
        assert data["pipeline_status"] == "UNKNOWN"

    def test_keeps_project_path_from_pipeline_state_when_symlink_differs(self, temp_dir):
        """Avoid mixing stale status with symlink-resolved project in one state payload."""
        state_project = os.path.join(temp_dir, "state_project")
        symlink_project = os.path.join(temp_dir, "symlink_project")
        os.makedirs(state_project, exist_ok=True)
        os.makedirs(symlink_project, exist_ok=True)

        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w") as f:
            json.dump(
                {
                    "pipeline_status": "PIPELINE_COMPLETE",
                    "project_path": state_project,
                    "current_agent": "planner",
                },
                f,
            )

        # project_dir_path resolves to a different project than pipeline_state.project_path
        project_link = os.path.join(temp_dir, "pipeline-project")
        os.symlink(symlink_project, project_link)

        cfg = {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": project_link,
        }

        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert os.path.realpath(data["project_path"]) == os.path.realpath(state_project)
        assert os.path.realpath(data["project_symlink_target"]) == os.path.realpath(symlink_project)

    def test_all_blocked_queue_halted_reason_gets_friendly_escalation_message(self, temp_dir):
        """Queue-halted escalation reason should be surfaced in human-readable form."""
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        phase_path = os.path.join(temp_dir, "phase_state.json")
        project_root = os.path.join(temp_dir, "project")
        os.makedirs(project_root, exist_ok=True)

        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "pipeline_status": "WAITING_FOR_HUMAN",
                    "queue_halted_reason": "all_blocked",
                    "project_path": project_root,
                },
                f,
            )
        with open(phase_path, "w", encoding="utf-8") as f:
            json.dump({"escalation_trigger_reason": "Queue halted: all_blocked"}, f)

        cfg = {
            "pipeline_state_path": state_path,
            "phase_state_path": phase_path,
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": project_root,
        }

        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        msg = data.get("escalation_message", "")
        assert "Queue halted" in msg
        assert "all queued projects are currently BLOCKED" in msg
