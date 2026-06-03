"""Tests for GET /api/state endpoint."""
import fcntl
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import subprocess
from fastapi.testclient import TestClient

from ui.server import app, load_config, _detect_base_branch


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

    def test_git_recover_suggested_branch_matches_detect_for_repo(self, temp_dir):
        """L-32: /api/state exposes branch prefilled for Recover Git modal (config + repo heuristics)."""
        repo = os.path.join(temp_dir, "git_repo")
        os.makedirs(repo, exist_ok=True)
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "develop"], cwd=repo, check=True, capture_output=True)
        with open(os.path.join(repo, "f.txt"), "w") as f:
            f.write("x")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        expected = _detect_base_branch(repo, "")
        assert expected == "develop"

        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"pipeline_status": "RUNNING", "project_path": repo}, f)

        cfg = {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": repo,
            "base_branch": "",
        }
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")
        assert response.status_code == 200
        assert response.json().get("git_recover_suggested_branch") == "develop"

    def test_git_recover_suggested_branch_respects_config_base_branch(self, temp_dir):
        repo = os.path.join(temp_dir, "git_repo2")
        os.makedirs(repo, exist_ok=True)
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, check=True, capture_output=True)
        with open(os.path.join(repo, "f.txt"), "w") as f:
            f.write("x")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"pipeline_status": "IDLE", "project_path": repo}, f)

        cfg = {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": repo,
            "base_branch": "develop",
        }
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")
        assert response.status_code == 200
        assert response.json().get("git_recover_suggested_branch") == "develop"

    def test_git_recover_suggested_branch_null_when_project_dir_missing(self, temp_dir):
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"pipeline_status": "IDLE"}, f)
        cfg = {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": os.path.join(temp_dir, "nonexistent_project_dir"),
        }
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")
        assert response.status_code == 200
        assert response.json().get("git_recover_suggested_branch") is None


# ──────────────────────────────────────────────────────────────────────────────
# Step 1.4 — executor_output_exists in GET /api/state
# ──────────────────────────────────────────────────────────────────────────────

class TestExecutorOutputExists:
    """Step 1.4 — GET /api/state must include executor_output_exists boolean."""

    def _make_cfg(self, temp_dir, project_root):
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"pipeline_status": "WAITING_FOR_HUMAN"}, f)
        return {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": project_root,
        }

    def test_executor_output_exists_true_when_file_present(self, temp_dir):
        """When executor_output.json is present in project dir, response has executor_output_exists=true."""
        project_root = os.path.join(temp_dir, "pipeline_project")
        os.makedirs(project_root, exist_ok=True)
        # Create executor_output.json under .autodev/pipeline/
        art = os.path.join(project_root, ".autodev", "pipeline")
        os.makedirs(art, exist_ok=True)
        with open(os.path.join(art, "executor_output.json"), "w") as f:
            json.dump({"status": "done"}, f)

        cfg = self._make_cfg(temp_dir, project_root)
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "executor_output_exists" in data, \
            "GET /api/state response does not include 'executor_output_exists'"
        assert data["executor_output_exists"] is True, \
            "executor_output_exists should be True when executor_output.json exists"

    def test_executor_output_exists_false_when_file_absent(self, temp_dir):
        """When executor_output.json is absent, response has executor_output_exists=false."""
        project_root = os.path.join(temp_dir, "pipeline_project")
        os.makedirs(project_root, exist_ok=True)
        # Do NOT create executor_output.json

        cfg = self._make_cfg(temp_dir, project_root)
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "executor_output_exists" in data, \
            "GET /api/state response does not include 'executor_output_exists'"
        assert data["executor_output_exists"] is False, \
            "executor_output_exists should be False when executor_output.json is absent"

    def test_executor_output_exists_false_when_project_dir_path_not_configured(self, temp_dir):
        """When project_dir_path is not configured, executor_output_exists is False."""
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"pipeline_status": "IDLE"}, f)
        cfg = {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            # No project_dir_path key
        }
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "executor_output_exists" in data, \
            "GET /api/state response does not include 'executor_output_exists'"
        assert data["executor_output_exists"] is False


# ──────────────────────────────────────────────────────────────────────────────
# Step 2.1 — Phase 2 eligibility fields in GET /api/state
# ──────────────────────────────────────────────────────────────────────────────

def _make_git_repo(temp_dir, branch_name="main"):
    """Create a minimal git repo in temp_dir with one commit on branch_name."""
    repo = os.path.join(temp_dir, "git_project")
    os.makedirs(repo, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo, check=True, capture_output=True)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("init")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


class TestPlannerOutputExists:
    """Step 2.1a — GET /api/state must include planner_output_exists boolean."""

    def _make_cfg(self, temp_dir, project_root):
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"pipeline_status": "WAITING_FOR_HUMAN"}, f)
        return {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": project_root,
        }

    def test_planner_output_exists_true_when_file_present(self, temp_dir):
        """When planner_output.json is in project dir, response has planner_output_exists=true."""
        project_root = os.path.join(temp_dir, "pp")
        os.makedirs(project_root, exist_ok=True)
        art = os.path.join(project_root, ".autodev", "pipeline")
        os.makedirs(art, exist_ok=True)
        with open(os.path.join(art, "planner_output.json"), "w") as f:
            json.dump({"status": "done"}, f)

        cfg = self._make_cfg(temp_dir, project_root)
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "planner_output_exists" in data, \
            "GET /api/state response does not include 'planner_output_exists'"
        assert data["planner_output_exists"] is True

    def test_planner_output_exists_false_when_file_absent(self, temp_dir):
        """When planner_output.json is absent, response has planner_output_exists=false."""
        project_root = os.path.join(temp_dir, "pp")
        os.makedirs(project_root, exist_ok=True)

        cfg = self._make_cfg(temp_dir, project_root)
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "planner_output_exists" in data
        assert data["planner_output_exists"] is False


class TestPhaseBranchExists:
    """Step 2.1b — GET /api/state must include phase_branch_exists boolean."""

    def _make_cfg(self, temp_dir, project_root, raw_id="CORE-E1"):
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"pipeline_status": "WAITING_FOR_HUMAN", "current_phase_raw_id": raw_id}, f)
        phase_path = os.path.join(temp_dir, "phase_state.json")
        with open(phase_path, "w", encoding="utf-8") as f:
            json.dump({"escalation_resets": 1}, f)
        return {
            "pipeline_state_path": state_path,
            "phase_state_path": phase_path,
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": project_root,
        }

    def test_phase_branch_exists_true_when_branch_present(self, temp_dir):
        """When phase/<raw_id> branch exists in the repo, phase_branch_exists=true."""
        repo = _make_git_repo(temp_dir)
        subprocess.run(
            ["git", "checkout", "-b", "phase/CORE-E1"], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

        cfg = self._make_cfg(temp_dir, repo, raw_id="CORE-E1")
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "phase_branch_exists" in data, \
            "GET /api/state response does not include 'phase_branch_exists'"
        assert data["phase_branch_exists"] is True

    def test_phase_branch_exists_false_when_branch_missing(self, temp_dir):
        """When phase/<raw_id> branch does not exist, phase_branch_exists=false."""
        repo = _make_git_repo(temp_dir)
        cfg = self._make_cfg(temp_dir, repo, raw_id="CORE-E1")
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "phase_branch_exists" in data
        assert data["phase_branch_exists"] is False

    def test_phase_branch_exists_false_when_raw_id_empty(self, temp_dir):
        """When current_phase_raw_id is empty, phase_branch_exists=false (no subprocess)."""
        repo = _make_git_repo(temp_dir)
        cfg = self._make_cfg(temp_dir, repo, raw_id="")
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "phase_branch_exists" in data
        assert data["phase_branch_exists"] is False


class TestEscalationMessageTruncation:
    """Step 3.4/3.5 — GET /api/state must truncate escalation_message at 500 chars."""

    def _make_cfg(self, temp_dir, escalation_message):
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"pipeline_status": "WAITING_FOR_HUMAN"}, f)
        phase_path = os.path.join(temp_dir, "phase_state.json")
        with open(phase_path, "w", encoding="utf-8") as f:
            json.dump({"escalation_message": escalation_message}, f)
        return {
            "pipeline_state_path": state_path,
            "phase_state_path": phase_path,
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": temp_dir,
        }

    def test_escalation_message_over_500_truncated(self, temp_dir):
        """phase_state.json escalation_message > 500 chars → response capped at 500."""
        long_msg = "X" * 600
        cfg = self._make_cfg(temp_dir, long_msg)
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "escalation_message" in data
        assert len(data["escalation_message"]) == 500
        assert data["escalation_message"] == "X" * 500

    def test_escalation_message_under_500_unchanged(self, temp_dir):
        """phase_state.json escalation_message ≤ 500 chars → returned unchanged."""
        short_msg = "Executor failed due to missing env var."
        cfg = self._make_cfg(temp_dir, short_msg)
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert data["escalation_message"] == short_msg


class TestMergeProbePassed:
    """Step 2.1c — GET /api/state must include merge_probe_passed boolean."""

    def _make_cfg(self, temp_dir, project_root, raw_id="CORE-E1", base_branch="main"):
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"pipeline_status": "WAITING_FOR_HUMAN", "current_phase_raw_id": raw_id}, f)
        phase_path = os.path.join(temp_dir, "phase_state.json")
        with open(phase_path, "w", encoding="utf-8") as f:
            json.dump({"escalation_resets": 1}, f)
        return {
            "pipeline_state_path": state_path,
            "phase_state_path": phase_path,
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": project_root,
            "base_branch": base_branch,
        }

    def test_merge_probe_passed_true_when_branch_merged(self, temp_dir):
        """When phase/<raw_id> is an ancestor of base branch, merge_probe_passed=true."""
        repo = _make_git_repo(temp_dir)
        # Create phase branch from main commit (same HEAD = is-ancestor)
        subprocess.run(
            ["git", "checkout", "-b", "phase/CORE-E1"], cwd=repo, check=True, capture_output=True
        )
        # Merge it into main
        subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "merge", "--no-ff", "phase/CORE-E1", "-m", "Merge phase"],
            cwd=repo, check=True, capture_output=True
        )

        cfg = self._make_cfg(temp_dir, repo, raw_id="CORE-E1", base_branch="main")
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "merge_probe_passed" in data, \
            "GET /api/state response does not include 'merge_probe_passed'"
        assert data["merge_probe_passed"] is True

    def test_merge_probe_passed_false_when_branch_not_merged(self, temp_dir):
        """When phase/<raw_id> has commits not in base branch, merge_probe_passed=false."""
        repo = _make_git_repo(temp_dir)
        # Create phase branch with a new commit not in main
        subprocess.run(
            ["git", "checkout", "-b", "phase/CORE-E1"], cwd=repo, check=True, capture_output=True
        )
        with open(os.path.join(repo, "work.txt"), "w") as f:
            f.write("work")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "phase work"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

        cfg = self._make_cfg(temp_dir, repo, raw_id="CORE-E1", base_branch="main")
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "merge_probe_passed" in data
        assert data["merge_probe_passed"] is False

    def test_merge_probe_passed_false_when_subprocess_error(self, temp_dir):
        """When git subprocess fails, merge_probe_passed=false (never raises)."""
        project_root = os.path.join(temp_dir, "notarepo")
        os.makedirs(project_root, exist_ok=True)

        cfg = self._make_cfg(temp_dir, project_root, raw_id="CORE-E1", base_branch="main")
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "merge_probe_passed" in data
        assert data["merge_probe_passed"] is False

    def test_merge_probe_passed_false_when_raw_id_empty(self, temp_dir):
        """When current_phase_raw_id is empty, merge_probe_passed=false."""
        repo = _make_git_repo(temp_dir)
        cfg = self._make_cfg(temp_dir, repo, raw_id="", base_branch="main")
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert "merge_probe_passed" in data
        assert data["merge_probe_passed"] is False


class TestGetStateReviewerContractRetries:
    """Phase 5 (UI attempts honesty): GET /api/state must surface
    reviewer_contract_retries so the attempt dots can render reviewer contract
    failures honestly (red), not green. reviewer_retries comes from
    pipeline_state, but reviewer_contract_retries lives ONLY in phase_state — so
    without this plumbing the frontend never sees it and a reviewer that failed to
    emit a verdict (then escalated) renders as a green 'passed' slot."""

    def test_api_state_exposes_reviewer_contract_retries(self, temp_dir):
        project_root = os.path.join(temp_dir, "pipeline_project")
        os.makedirs(project_root, exist_ok=True)
        with open(os.path.join(temp_dir, "pipeline_state.json"), "w") as f:
            json.dump(
                {
                    "pipeline_status": "RUNNING",
                    "current_agent": "escalation",
                    "current_phase": 2,
                    "reviewer_retries": 0,
                },
                f,
            )
        with open(os.path.join(temp_dir, "phase_state.json"), "w") as f:
            json.dump({"reviewer_contract_retries": 2, "escalation_resets": 1}, f)
        cfg = {
            "pipeline_state_path": os.path.join(temp_dir, "pipeline_state.json"),
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": project_root,
        }
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        assert response.json().get("reviewer_contract_retries") == 2, (
            "GET /api/state must surface reviewer_contract_retries from phase_state "
            "so the UI attempt dots can render reviewer contract failures (not green)."
        )

    def test_api_state_reviewer_contract_retries_defaults_zero(self, temp_dir):
        """When phase_state has no reviewer_contract_retries, the field defaults to 0
        (so the UI's gate-on-revContract>0 honesty branch is a no-op — preserving all
        existing attempt-dot behaviour)."""
        project_root = os.path.join(temp_dir, "pipeline_project")
        os.makedirs(project_root, exist_ok=True)
        with open(os.path.join(temp_dir, "pipeline_state.json"), "w") as f:
            json.dump({"pipeline_status": "RUNNING", "current_agent": "reviewer"}, f)
        with open(os.path.join(temp_dir, "phase_state.json"), "w") as f:
            json.dump({"escalation_resets": 0}, f)
        cfg = {
            "pipeline_state_path": os.path.join(temp_dir, "pipeline_state.json"),
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "project_dir_path": project_root,
        }
        with patch("ui.server.load_config", return_value=cfg):
            response = client.get("/api/state")

        assert response.status_code == 200
        assert response.json().get("reviewer_contract_retries") == 0
