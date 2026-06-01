"""Tests for POST /api/command endpoint."""
import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _pipeline_art(project_dir: str) -> str:
    return os.path.join(project_dir, ".autodev", "pipeline")


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
        escalation_output_path = os.path.join(_pipeline_art(temp_project_dir), "escalation_output.json")
        
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
        escalation_output_path = os.path.join(_pipeline_art(temp_project_dir), "escalation_output.json")
        escalation_done_path = os.path.join(_pipeline_art(temp_project_dir), "escalation_output.done")
        
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
                assert "not waiting for human input" in response.json()["detail"]

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
                assert "Reset cap reached" in response.json()["detail"]

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
                assert "Reset cap reached" in response.json()["detail"]

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
                detail = response.json()["detail"]
                assert "broken" in detail.lower() or "missing" in detail.lower()
                assert "Technical:" in detail

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
                detail = response.json()["detail"]
                assert "broken" in detail.lower() or "missing" in detail.lower()
                assert "Technical:" in detail
                assert "symlink" in detail.lower()

    @pytest.mark.parametrize("command", ["RETRY", "RESET_EXECUTION", "RESET_PHASE", "SKIP", "PROCEED", "STOP", "NUCLEAR_RESET"])
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

    # ── P1 Stage G2 — NUCLEAR_RESET command (operator escape hatch, cap 2) ──────
    def test_nuclear_reset_in_valid_commands_not_in_reset_cap_commands(self):
        """NUCLEAR_RESET is a valid command but is NOT subject to the escalation
        reset cap (escalation_resets >= 3) — it is governed by its own nuclear_resets
        cap, and must remain available precisely when the escalation budget is spent."""
        from ui.server import VALID_COMMANDS, RESET_CAP_COMMANDS
        assert "NUCLEAR_RESET" in VALID_COMMANDS
        assert "NUCLEAR_RESET" not in RESET_CAP_COMMANDS

    def test_nuclear_reset_rejected_when_nuclear_resets_ge_2(self, test_client, temp_project_dir):
        """The server enforces the nuclear cap: nuclear_resets >= 2 -> 409."""
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
                    {"escalation_resets": 3, "nuclear_resets": 2},
                ]
                response = test_client.post("/api/command", json={"command": "NUCLEAR_RESET"})
                assert response.status_code == 409
                assert "Nuclear reset cap reached" in response.json()["detail"]

    def test_nuclear_reset_accepted_when_escalation_resets_ge_3(self, test_client, temp_project_dir):
        """Spec-literal (Decision A): NUCLEAR_RESET is accepted even when the escalation
        reset cap is fully spent — that is exactly when it must be available. Proves the
        server does NOT gate it on escalation_resets."""
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
                    {"escalation_resets": 3, "nuclear_resets": 0},
                ]
                response = test_client.post("/api/command", json={"command": "NUCLEAR_RESET"})
                assert response.status_code == 200

    def test_command_uses_parked_escalation_fallback_when_queue_halted(self, test_client, temp_project_dir):
        """When active status is QUEUE_HALTED but queue row is ESCALATION, defer command to parked files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline_state_path = os.path.join(tmpdir, "pipeline_state.json")
            phase_state_path = os.path.join(tmpdir, "phase_state.json")
            queue_path = os.path.join(tmpdir, "pipeline_queue.json")

            with open(pipeline_state_path, "w", encoding="utf-8") as f:
                json.dump({"pipeline_status": "QUEUE_HALTED"}, f)
            with open(phase_state_path, "w", encoding="utf-8") as f:
                json.dump({"escalation_resets": 1}, f)
            with open(queue_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "queue": [
                            {
                                "id": "q1",
                                "project_path": temp_project_dir,
                                "state": "ESCALATION",
                                "parked_pipeline_status": "WAITING_FOR_HUMAN",
                            }
                        ]
                    },
                    f,
                )

            with patch("ui.server.load_config") as mock_config:
                mock_config.return_value = {
                    "pipeline_state_path": pipeline_state_path,
                    "phase_state_path": phase_state_path,
                    "pipeline_queue_path": queue_path,
                    "project_dir_path": temp_project_dir,
                    "lock_path": os.path.join(tmpdir, "pipeline.lock"),
                    "events_path": os.path.join(tmpdir, "pipeline_events.jsonl"),
                }

                response = test_client.post("/api/command", json={"command": "RESET_PHASE"})

            assert response.status_code == 200
            payload = response.json()
            assert payload.get("deferred") is True
            art = _pipeline_art(temp_project_dir)
            assert os.path.exists(os.path.join(art, "pending_escalation_command.json"))
            assert os.path.exists(os.path.join(art, "pending_escalation_command.done"))
