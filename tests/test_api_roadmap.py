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


class TestRoadmapResolvesFromPipelineState:
    """GET /api/roadmap: prefer pipeline_state[project_path] + _canonical_roadmap_path;
    fall back to config[roadmap_path] when state has no usable project path."""

    def _roadmap_block(self, *phase_ids: str) -> str:
        lines = ["# Test Roadmap", ""]
        for pid in phase_ids:
            lines.append(f"- [ ] `{pid}` | LOW | {pid} goal")
        return "\n".join(lines) + "\n"

    def test_state_project_path_takes_precedence_over_config_roadmap_path(self, temp_dir):
        state_project = os.path.join(temp_dir, "state_project")
        config_project = os.path.join(temp_dir, "config_project")
        os.makedirs(state_project, exist_ok=True)
        os.makedirs(config_project, exist_ok=True)
        with open(os.path.join(state_project, "roadmap.md"), "w") as f:
            f.write(self._roadmap_block("STATE-1", "STATE-2"))
        with open(os.path.join(config_project, "roadmap.md"), "w") as f:
            f.write(self._roadmap_block("CONFIG-1", "CONFIG-2"))

        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w") as f:
            json.dump(
                {
                    "pipeline_status": "RUNNING",
                    "project_path": state_project,
                },
                f,
            )
        mock_config = {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "roadmap_path": os.path.join(config_project, "roadmap.md"),
        }
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")
        assert response.status_code == 200
        data = response.json()
        ids = [p["id"] for p in data]
        assert "STATE-1" in ids
        assert "CONFIG-1" not in ids

    def test_falls_back_to_config_when_pipeline_state_has_no_project_path(self, temp_dir):
        """When project_path is missing, /api/roadmap uses config roadmap_path (unchanged)."""
        fb_path = os.path.join(temp_dir, "fallback_roadmap.md")
        with open(fb_path, "w") as f:
            f.write(self._roadmap_block("FALLBACK-1", "FALLBACK-2"))
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w") as f:
            json.dump({"pipeline_status": "RUNNING"}, f)
        mock_config = {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "roadmap_path": fb_path,
        }
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")
        assert response.status_code == 200
        data = response.json()
        assert any(p["id"] == "FALLBACK-1" for p in data)

    def test_falls_back_to_config_when_pipeline_state_file_absent(self, temp_dir):
        """No pipeline_state.json — use config roadmap only."""
        fb_path = os.path.join(temp_dir, "only_roadmap.md")
        with open(fb_path, "w") as f:
            f.write(self._roadmap_block("FALLBACK-1"))
        mock_config = {
            "pipeline_state_path": os.path.join(temp_dir, "no_such_state.json"),
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "roadmap_path": fb_path,
        }
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")
        assert response.status_code == 200
        data = response.json()
        assert any(p["id"] == "FALLBACK-1" for p in data)

    def test_canonical_roadmap_path_glob_for_nonstandard_filename(self, temp_dir):
        state_project = os.path.join(temp_dir, "globs")
        other_project = os.path.join(temp_dir, "other")
        os.makedirs(state_project, exist_ok=True)
        os.makedirs(other_project, exist_ok=True)
        with open(os.path.join(state_project, "my-roadmap.md"), "w") as f:
            f.write(self._roadmap_block("GLOB-1"))
        with open(os.path.join(other_project, "roadmap.md"), "w") as f:
            f.write(self._roadmap_block("OTHER-1"))
        state_path = os.path.join(temp_dir, "pipeline_state.json")
        with open(state_path, "w") as f:
            json.dump(
                {
                    "pipeline_status": "RUNNING",
                    "project_path": state_project,
                },
                f,
            )
        mock_config = {
            "pipeline_state_path": state_path,
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "roadmap_path": os.path.join(other_project, "roadmap.md"),
        }
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")
        assert response.status_code == 200
        data = response.json()
        ids = [p["id"] for p in data]
        assert "GLOB-1" in ids
        assert "OTHER-1" not in ids


class TestRoadmapBehavioralVerification:
    """P0 Stage J — /api/roadmap must surface the per-phase Behavioral
    Verification block to the frontend.

    Stage D wired ``ui/roadmap_parser.parse_roadmap`` to emit a
    ``behavioral_verification`` field per phase. The endpoint at
    ``ui/server.py:get_roadmap`` returns the parser output unfiltered, so
    the field already reaches the frontend in practice. This test pins
    that contract — a future refactor that introduces a Pydantic response
    model, an explicit field allowlist, or any other accidental filter
    will fail here rather than silently break the new Stage J phase
    dropdown rendering.
    """

    def test_endpoint_round_trips_behavioral_verification(self, temp_dir):
        """Roadmap fixture with a full Behavioral Verification block →
        the endpoint response carries the structured dict on the
        corresponding phase, with all three sub-fields intact."""
        roadmap = (
            "# Round-trip fixture\n"
            "\n"
            "- [ ] `CORE-J1` | LOW | First phase exercising behavioral verification\n"
            "  **Behavioral Verification:**\n"
            "  - **User-observable:** A round-trip-fixture user sees the canary line.\n"
            "  - **How we'll check:** Visit /canary and assert the body equals \"OK\".\n"
            "  - **If this fails, the user sees:** \"Canary endpoint unreachable.\"\n"
            "- [ ] `CORE-J2` | LOW | Phase without the new block (pre-P0 shape)\n"
        )
        roadmap_path = os.path.join(temp_dir, "roadmap.md")
        with open(roadmap_path, "w") as f:
            f.write(roadmap)

        mock_config = {
            "pipeline_state_path": os.path.join(temp_dir, "pipeline_state.json"),
            "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
            "lock_path": os.path.join(temp_dir, "pipeline.lock"),
            "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
            "roadmap_path": roadmap_path,
        }
        with patch("ui.server.load_config", return_value=mock_config):
            response = client.get("/api/roadmap")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2

        # First phase: full BV block — must round-trip with exact field values.
        phase_one = data[0]
        assert phase_one["id"] == "CORE-J1"
        bv = phase_one.get("behavioral_verification")
        assert bv is not None, (
            "/api/roadmap dropped the behavioral_verification field for a "
            "phase that has a complete block in the source roadmap. The "
            "parser emits the dict; the endpoint must not filter it out."
        )
        assert bv == {
            "user_observable": "A round-trip-fixture user sees the canary line.",
            "how_to_check": 'Visit /canary and assert the body equals "OK".',
            "failure_language": '"Canary endpoint unreachable."',
        }, (
            "behavioral_verification round-trip is wrong-shaped. All three "
            "sub-fields must survive the parse → serialize → JSON path "
            "byte-for-byte."
        )

        # Second phase: no BV block — None must survive too (transitional
        # case for pre-P0 roadmaps; the UI subsection short-circuits on
        # null, so the contract is "exposed as null, not omitted").
        phase_two = data[1]
        assert phase_two["id"] == "CORE-J2"
        assert "behavioral_verification" in phase_two, (
            "phases without the block must still expose the key as null. "
            "Omitting it would force every UI consumer to defensive-check "
            "before reading, defeating the parser's contract."
        )
        assert phase_two["behavioral_verification"] is None
