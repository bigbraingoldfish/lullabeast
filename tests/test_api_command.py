"""Tests for POST /api/command endpoint."""
import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


@pytest.fixture
def mock_lifespan():
    from contextlib import asynccontextmanager
    
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
        yield tmpdir


class TestPostApiCommand:
    def test_command_retry_when_waiting_for_human_returns_200(self, test_client, temp_project_dir):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": temp_project_dir,
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }
            
            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "WAITING_FOR_HUMAN"},
                    {"escalation_resets": 0},
                ]
                
                response = test_client.post("/api/command", json={"command": "RETRY"})
                
                assert response.status_code == 200

    def test_command_creates_escalation_output_json(self, test_client, temp_project_dir):
        escalation_output_path = os.path.join(temp_project_dir, "escalation_output.json")
        
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": temp_project_dir,
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }
            
            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "WAITING_FOR_HUMAN"},
                    {"escalation_resets": 0},
                ]
                
                response = test_client.post("/api/command", json={"command": "RETRY"})
                
                assert response.status_code == 200
                assert os.path.exists(escalation_output_path)
                
                with open(escalation_output_path, "r") as f:
                    content = json.load(f)
                
                assert content["command"] == "RETRY"
                assert content["source"] == "ui"
                assert "timestamp" in content

    def test_command_creates_escalation_output_done_after_json(self, test_client, temp_project_dir):
        escalation_output_path = os.path.join(temp_project_dir, "escalation_output.json")
        escalation_done_path = os.path.join(temp_project_dir, "escalation_output.done")
        
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": temp_project_dir,
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }
            
            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "WAITING_FOR_HUMAN"},
                    {"escalation_resets": 0},
                ]
                
                response = test_client.post("/api/command", json={"command": "RETRY"})
                
                assert response.status_code == 200
                assert os.path.exists(escalation_output_path)
                assert os.path.exists(escalation_done_path)
                
                json_mtime = os.path.getmtime(escalation_output_path)
                done_mtime = os.path.getmtime(escalation_done_path)
                assert done_mtime >= json_mtime

    def test_command_when_pipeline_running_returns_409(self, test_client, temp_project_dir):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": temp_project_dir,
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }
            
            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "RUNNING"},
                    {},
                ]
                
                response = test_client.post("/api/command", json={"command": "RETRY"})
                
                assert response.status_code == 409
                assert response.json()["detail"] == "Pipeline is not waiting for human input"

    def test_command_unknown_returns_400(self, test_client, temp_project_dir):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": temp_project_dir,
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }
            
            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "WAITING_FOR_HUMAN"},
                    {},
                ]
                
                response = test_client.post("/api/command", json={"command": "UNKNOWN_CMD"})
                
                assert response.status_code == 400

    def test_command_reset_phase_when_escalation_resets_ge_3_returns_409(self, test_client, temp_project_dir):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": temp_project_dir,
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }
            
            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "WAITING_FOR_HUMAN"},
                    {"escalation_resets": 3},
                ]
                
                response = test_client.post("/api/command", json={"command": "RESET_PHASE"})
                
                assert response.status_code == 409
                assert response.json()["detail"] == "Reset cap reached"

    def test_command_reset_execution_when_escalation_resets_ge_3_returns_409(self, test_client, temp_project_dir):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": temp_project_dir,
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }
            
            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "WAITING_FOR_HUMAN"},
                    {"escalation_resets": 5},
                ]
                
                response = test_client.post("/api/command", json={"command": "RESET_EXECUTION"})
                
                assert response.status_code == 409
                assert response.json()["detail"] == "Reset cap reached"

    def test_command_non_reset_ignores_reset_cap(self, test_client, temp_project_dir):
        """RETRY/SKIP/PROCEED/STOP are not subject to the reset cap."""
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": temp_project_dir,
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }

            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "WAITING_FOR_HUMAN"},
                    {"escalation_resets": 3},
                ]

                response = test_client.post("/api/command", json={"command": "RETRY"})

                # Must return 200 even when escalation_resets >= 3
                assert response.status_code == 200

    def test_command_when_project_dir_absent_returns_503(self, test_client):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": "/tmp/nonexistent_project_dir",
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }
            
            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "WAITING_FOR_HUMAN"},
                    {"escalation_resets": 0},
                ]
                
                response = test_client.post("/api/command", json={"command": "RETRY"})
                
                assert response.status_code == 503
                assert "not found" in response.json()["detail"].lower()

    def test_command_when_project_dir_symlink_dangling_returns_503(self, test_client, temp_project_dir):
        dangling_link = os.path.join(temp_project_dir, "dangling_link")
        os.symlink("/tmp/nonexistent_target", dangling_link)
        
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": dangling_link,
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }
            
            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "WAITING_FOR_HUMAN"},
                    {"escalation_resets": 0},
                ]
                
                response = test_client.post("/api/command", json={"command": "RETRY"})
                
                assert response.status_code == 503
                assert "dangling" in response.json()["detail"].lower()

    @pytest.mark.parametrize("command", ["RETRY", "RESET_EXECUTION", "RESET_PHASE", "SKIP", "PROCEED", "STOP"])
    def test_all_valid_commands_return_200(self, test_client, temp_project_dir, command):
        with patch("ui.server.load_config") as mock_config:
            mock_config.return_value = {
                "pipeline_state_path": "/tmp/nonexistent_pipeline.json",
                "phase_state_path": "/tmp/nonexistent_phase.json",
                "project_dir_path": temp_project_dir,
                "lock_path": "/tmp/nonexistent.lock",
                "events_path": "/tmp/nonexistent_events.jsonl",
            }
            
            with patch("ui.server._read_json_file") as mock_read:
                mock_read.side_effect = [
                    {"pipeline_status": "WAITING_FOR_HUMAN"},
                    {"escalation_resets": 0},
                ]
                
                response = test_client.post("/api/command", json={"command": command})
                
                assert response.status_code == 200, f"Command {command} should return 200"
