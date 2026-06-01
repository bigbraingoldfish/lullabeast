"""P1 Stage G1 — GET /api/state must serve the clean ``escalation_headline``
field from phase_state so the UI can render a non-blame headline.

Mirrors the pattern in ``test_ui_escalation_advisory_status.py``.
"""

import json
import os
import tempfile
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_lifespan():
    @asynccontextmanager
    async def mock_context(app):
        yield

    return mock_context


@pytest.fixture
def test_client(mock_lifespan):
    from ui.server import app

    app.router.lifespan_context = mock_lifespan
    with TestClient(app) as client:
        yield client


@pytest.fixture
def temp_project_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        pipeline_art = os.path.join(tmpdir, ".autodev", "pipeline")
        os.makedirs(pipeline_art, exist_ok=True)
        yield tmpdir


def _state_paths(temp_project_dir):
    pipeline_art = os.path.join(temp_project_dir, ".autodev", "pipeline")
    return {
        "pipeline_state_path": os.path.join(pipeline_art, "pipeline_state.json"),
        "phase_state_path": os.path.join(pipeline_art, "phase_state.json"),
        "project_dir_path": temp_project_dir,
        "lock_path": os.path.join(pipeline_art, "pipeline.lock"),
        "events_path": os.path.join(pipeline_art, "pipeline_events.jsonl"),
    }


class TestEscalationHeadlineInApiState:

    def test_api_state_serves_escalation_headline(self, test_client, temp_project_dir):
        """phase_state with escalation_headline → present in /api/state response.
        Fails today: server.py does not pass the field through."""
        paths = _state_paths(temp_project_dir)
        pipeline_state = {
            "pipeline_status": "WAITING_FOR_HUMAN",
            "current_agent": "escalation",
        }
        phase_state = {
            "escalation_trigger_reason": "Impl blame cap reached (4x): [L3] defaulting to impl.",
            "escalation_headline": "Phase REND-E1 needs your input",
            "escalation_advisory_status": "fallback",
        }
        with open(paths["pipeline_state_path"], "w") as f:
            json.dump(pipeline_state, f)
        with open(paths["phase_state_path"], "w") as f:
            json.dump(phase_state, f)

        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = paths
            response = test_client.get("/api/state")

        assert response.status_code == 200
        data = response.json()
        assert data.get("escalation_headline") == "Phase REND-E1 needs your input", (
            "GET /api/state must pass escalation_headline through from phase_state "
            "so the UI can render a clean headline without re-deriving it"
        )

    def test_api_state_omits_escalation_headline_when_absent(self, test_client, temp_project_dir):
        """phase_state without escalation_headline → field absent from response."""
        paths = _state_paths(temp_project_dir)
        pipeline_state = {
            "pipeline_status": "RUNNING",
            "current_agent": "executor",
        }
        phase_state = {"executor_retries": 1, "reviewer_retries": 0}
        with open(paths["pipeline_state_path"], "w") as f:
            json.dump(pipeline_state, f)
        with open(paths["phase_state_path"], "w") as f:
            json.dump(phase_state, f)

        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = paths
            response = test_client.get("/api/state")

        assert response.status_code == 200
        assert "escalation_headline" not in response.json()
