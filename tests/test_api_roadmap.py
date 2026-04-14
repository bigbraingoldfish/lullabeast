# Tests for GET /api/roadmap endpoint.

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
    }

@pytest.fixture
def mock_roadmap(temp_dir):
    content = """# Test Roadmap

- [x] `PHASE-1` | LOW | First phase (complete)
- [ ] `PHASE-2` | LOW | Second phase (pending)
- [x] `PHASE-3` | LOW | Third phase (complete)
"""
    path = os.path.join(temp_dir, "roadmap.md")
    with open(path, "w") as f:
        f.write(content)
    return path

@pytest.fixture
def mock_roadmap_with_in_progress(temp_dir):
    content = """# Test Roadmap

- [x] `PHASE-1` | LOW | First phase (complete)
- [ ] `PHASE-2` | LOW | Second phase (pending, should be in_progress)
- [x] `PHASE-3` | LOW | Third phase (complete)
"""
    path = os.path.join(temp_dir, "roadmap.md")
    with open(path, "w") as f:
        f.write(content)
    return path

@pytest.fixture
def mock_roadmap_with_checked_in_progress(temp_dir):
    content = """# Test Roadmap

- [x] `PHASE-1` | LOW | First phase (complete)
- [x] `PHASE-2` | LOW | Second phase (marked complete but should be in_progress)
- [x] `PHASE-3` | LOW | Third phase (complete)
"""
    path = os.path.join(temp_dir, "roadmap.md")
    with open(path, "w") as f:
        f.write(content)
    return path

@pytest.fixture
def mock_pipeline_state_with_phase(temp_dir):
    def _create(phase_id):
        state = {
            "pipeline_status": "RUNNING",
            "current_phase_raw_id": phase_id,
        }
        path = os.path.join(temp_dir, "pipeline_state.json")
        with open(path, "w") as f:
            json.dump(state, f)
        return path
    return _create

class TestApiRoadmapEndpoint:
    """Tests for GET /api/roadmap endpoint."""

    def test_returns_200_with_json_array(self, mock_config, mock_roadmap, mock_pipeline_state_with_phase):
        """Test endpoint returns 200 with JSON array when roadmap.md exists and pipeline_state.json has current_phase_raw_id."""
        mock_pipeline_state_with_phase("PHASE-999")

        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 3
        assert data[0]["id"] == "PHASE-1"
        assert data[0]["status"] == "complete"
        assert data[1]["id"] == "PHASE-2"
        assert data[1]["status"] == "pending"
        assert data[2]["id"] == "PHASE-3"
        assert data[2]["status"] == "complete"

    def test_phase_matching_current_phase_raw_id_has_in_progress_status(self, mock_config, mock_roadmap_with_in_progress, mock_pipeline_state_with_phase):
        """Test phase matching current_phase_raw_id has status in_progress regardless of checkbox state."""
        mock_pipeline_state_with_phase("PHASE-2")

        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")

        assert response.status_code == 200
        data = response.json()
        phase2 = next(p for p in data if p["id"] == "PHASE-2")
        assert phase2["status"] == "in_progress"

        phase1 = next(p for p in data if p["id"] == "PHASE-1")
        assert phase1["status"] == "complete"

        phase3 = next(p for p in data if p["id"] == "PHASE-3")
        assert phase3["status"] == "complete"

    def test_in_progress_takes_precedence_over_checkbox(self, mock_config, mock_roadmap_with_checked_in_progress, mock_pipeline_state_with_phase):
        """Test phase matching current_phase_raw_id but marked [x] shows in_progress."""
        mock_pipeline_state_with_phase("PHASE-2")

        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")

        assert response.status_code == 200
        data = response.json()
        phase2 = next(p for p in data if p["id"] == "PHASE-2")
        assert phase2["status"] == "in_progress"

    def test_returns_empty_array_when_roadmap_absent(self, mock_config, mock_pipeline_state_with_phase):
        """Test returns [] when roadmap_path is absent."""
        mock_pipeline_state_with_phase("PHASE-999")

        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")

        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_returns_empty_array_when_roadmap_empty(self, mock_config, temp_dir, mock_pipeline_state_with_phase):
        """Test returns [] when roadmap.md file is empty."""
        path = os.path.join(temp_dir, "roadmap.md")
        with open(path, "w") as f:
            f.write("")

        mock_pipeline_state_with_phase("PHASE-999")

        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")

        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_missing_pipeline_state_returns_checkbox_statuses(self, mock_config, mock_roadmap):
        """Test when pipeline_state.json is absent, returns all checkbox statuses without override."""
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")

        assert response.status_code == 200
        data = response.json()
        for phase in data:
            assert phase["status"] in ("complete", "pending", "skipped", "blocked")
            assert phase["status"] != "in_progress"

    def test_empty_current_phase_raw_id_skips_override(self, mock_config, mock_roadmap_with_in_progress, temp_dir):
        """Test when current_phase_raw_id is empty string, no phase is overridden to in_progress."""
        state = {
            "pipeline_status": "RUNNING",
            "current_phase_raw_id": "",
        }
        path = os.path.join(temp_dir, "pipeline_state.json")
        with open(path, "w") as f:
            json.dump(state, f)

        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")

        assert response.status_code == 200
        data = response.json()
        for phase in data:
            assert phase["status"] != "in_progress"


class TestTerminalStatusesDoNotShowInProgress:
    """F2: STOPPED and QUEUE_HALTED must be treated as terminal statuses so that the
    roadmap endpoint does not show an in_progress phase overlay while the pipeline
    is not running.

    Previously terminal_statuses = {"PIPELINE_COMPLETE", "HALTED_SILENT", "BLOCKED"}.
    STOPPED and QUEUE_HALTED were omitted, causing the last active phase to remain
    highlighted as in_progress on the UI even after the pipeline had halted.
    """

    @pytest.mark.parametrize("terminal_status", ["STOPPED", "QUEUE_HALTED"])
    def test_no_in_progress_overlay_when_pipeline_is_terminal(
        self, mock_config, mock_roadmap_with_in_progress, temp_dir, terminal_status
    ):
        """When pipeline_status is STOPPED or QUEUE_HALTED, no phase should show
        status='in_progress' — the pipeline has halted and the overlay is stale."""
        state = {
            "pipeline_status": terminal_status,
            "current_phase_raw_id": "PHASE-2",  # this phase was active when the pipeline stopped
        }
        path = os.path.join(temp_dir, "pipeline_state.json")
        with open(path, "w") as f:
            json.dump(state, f)

        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")

        assert response.status_code == 200
        data = response.json()
        for phase in data:
            assert phase["status"] != "in_progress", (
                f"Phase {phase['id']} shows 'in_progress' when pipeline_status={terminal_status!r}. "
                f"STOPPED and QUEUE_HALTED must be in terminal_statuses so the in_progress "
                f"overlay is suppressed when the pipeline is not running."
            )

    @pytest.mark.parametrize("running_status", ["RUNNING", "WAITING_FOR_SENTINEL", "WAITING_FOR_HUMAN"])
    def test_in_progress_overlay_present_when_pipeline_is_running(
        self, mock_config, mock_roadmap_with_in_progress, temp_dir, running_status
    ):
        """Sanity check: in_progress overlay must still appear for genuinely running states."""
        state = {
            "pipeline_status": running_status,
            "current_phase_raw_id": "PHASE-2",
        }
        path = os.path.join(temp_dir, "pipeline_state.json")
        with open(path, "w") as f:
            json.dump(state, f)

        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")

        assert response.status_code == 200
        data = response.json()
        phase2 = next((p for p in data if p["id"] == "PHASE-2"), None)
        assert phase2 is not None
        assert phase2["status"] == "in_progress", (
            f"Phase PHASE-2 should be 'in_progress' when pipeline_status={running_status!r}"
        )
