"""
Tests for escalation advisory fields in GET /api/state response.

Validates:
  1. phase_state with advisory fields → all three fields passed through in /api/state
  2. phase_state without advisory fields → fields absent from /api/state response
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


class TestEscalationAdvisoryStatusInApiState:

    def test_api_state_returns_all_three_advisory_fields_when_set(
        self, test_client, temp_project_dir
    ):
        """phase_state with advisory fields → all three appear in /api/state response."""
        pipeline_art = os.path.join(temp_project_dir, ".autodev", "pipeline")
        pipeline_state = {
            "pipeline_status": "WAITING_FOR_HUMAN",
            "current_agent": "escalation",
        }
        phase_state = {
            "escalation_trigger_reason": "Impl blame cap reached (3x)",
            "escalation_message": "The executor failed on test assertions three times. The plan is valid.",
            "escalation_recommended_action": "Use Reset Execution to retry with fresh state.",
            "escalation_advisory_status": "ready",
        }

        pipeline_state_path = os.path.join(pipeline_art, "pipeline_state.json")
        phase_state_path = os.path.join(pipeline_art, "phase_state.json")

        with open(pipeline_state_path, "w") as f:
            json.dump(pipeline_state, f)
        with open(phase_state_path, "w") as f:
            json.dump(phase_state, f)

        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": pipeline_state_path,
                "phase_state_path": phase_state_path,
                "project_dir_path": temp_project_dir,
                "lock_path": os.path.join(pipeline_art, "pipeline.lock"),
                "events_path": os.path.join(pipeline_art, "pipeline_events.jsonl"),
            }

            response = test_client.get("/api/state")

        assert response.status_code == 200
        data = response.json()

        assert data.get("escalation_advisory_status") == "ready"
        assert data.get("escalation_message") == phase_state["escalation_message"]
        assert data.get("escalation_recommended_action") == phase_state["escalation_recommended_action"]

    def test_api_state_omits_advisory_fields_when_not_in_phase_state(
        self, test_client, temp_project_dir
    ):
        """phase_state without advisory fields → fields absent from /api/state response."""
        pipeline_art = os.path.join(temp_project_dir, ".autodev", "pipeline")
        pipeline_state = {
            "pipeline_status": "RUNNING",
            "current_agent": "executor",
        }
        phase_state = {
            "executor_retries": 1,
            "reviewer_retries": 0,
        }

        pipeline_state_path = os.path.join(pipeline_art, "pipeline_state.json")
        phase_state_path = os.path.join(pipeline_art, "phase_state.json")

        with open(pipeline_state_path, "w") as f:
            json.dump(pipeline_state, f)
        with open(phase_state_path, "w") as f:
            json.dump(phase_state, f)

        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": pipeline_state_path,
                "phase_state_path": phase_state_path,
                "project_dir_path": temp_project_dir,
                "lock_path": os.path.join(pipeline_art, "pipeline.lock"),
                "events_path": os.path.join(pipeline_art, "pipeline_events.jsonl"),
            }

            response = test_client.get("/api/state")

        assert response.status_code == 200
        data = response.json()

        assert "escalation_advisory_status" not in data
        assert "escalation_recommended_action" not in data
