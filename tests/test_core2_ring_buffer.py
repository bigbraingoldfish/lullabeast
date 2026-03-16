"""Tests for core2 ring buffer and polling functionality."""
import asyncio
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ui.server as server


class TestRingBuffer:
    def test_ring_buffer_max_size(self):
        buffer = server._ring_buffer
        assert len(buffer) == 0
        for i in range(50):
            buffer.append({"id": i})
        assert len(buffer) == 50
        buffer.append({"id": 50})
        assert len(buffer) == 50
        ids = [e["id"] for e in buffer]
        assert 0 not in ids
        assert 1 in ids
        assert 50 in ids


class TestSyntheticEventCreation:
    def test_create_synthetic_event_with_all_fields(self):
        event = server._create_synthetic_event(
            event_type="status_changed",
            agent="planner",
            phase="planning",
            detail="started new planning cycle"
        )
        assert "ts" in event
        assert "event" in event
        assert "agent" in event
        assert "phase" in event
        assert "detail" in event
        assert event["event"] == "status_changed"
        assert event["agent"] == "planner"
        assert event["phase"] == "planning"
        assert event["detail"] == "started new planning cycle"
        from datetime import datetime
        datetime.fromisoformat(event["ts"])

    def test_create_synthetic_event_minimal(self):
        event = server._create_synthetic_event(event_type="status_changed")
        assert "ts" in event
        assert event["event"] == "status_changed"
        assert event.get("agent") is None
        assert event.get("phase") is None
        assert event.get("detail") is None


class TestPollState:
    @pytest.fixture
    def temp_state_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(json.dumps({
                "pipeline_status": "RUNNING",
                "current_agent": "planner",
                "current_phase_raw_id": "planning",
                "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
            }))
            temp_path = f.name
        yield temp_path
        os.unlink(temp_path)

    def test_poll_state_detects_status_change(self, temp_state_file):
        prev_state = {
            "pipeline_status": "IDLE",
            "current_agent": None,
            "current_phase_raw_id": None,
            "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
        }
        with patch.object(server, 'load_config', return_value={'pipeline_state_path': temp_state_file}):
            event = asyncio.run(server._poll_state(prev_state))
        assert event is not None
        assert event["event"] == "status_changed"
        assert event["agent"] == "planner"

    def test_poll_state_detects_agent_change(self, temp_state_file):
        prev_state = {
            "pipeline_status": "RUNNING",
            "current_agent": "planner",
            "current_phase_raw_id": "planning",
            "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
        }
        with open(temp_state_file, 'w') as f:
            json.dump({
                "pipeline_status": "RUNNING",
                "current_agent": "executor",
                "current_phase_raw_id": "planning",
                "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
            }, f)
        with patch.object(server, 'load_config', return_value={'pipeline_state_path': temp_state_file}):
            event = asyncio.run(server._poll_state(prev_state))
        assert event is not None
        assert event["agent"] == "executor"

    def test_poll_state_detects_phase_change(self, temp_state_file):
        prev_state = {
            "pipeline_status": "RUNNING",
            "current_agent": "planner",
            "current_phase_raw_id": "planning",
            "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
        }
        with open(temp_state_file, 'w') as f:
            json.dump({
                "pipeline_status": "RUNNING",
                "current_agent": "planner",
                "current_phase_raw_id": "execution",
                "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
            }, f)
        with patch.object(server, 'load_config', return_value={'pipeline_state_path': temp_state_file}):
            event = asyncio.run(server._poll_state(prev_state))
        assert event is not None
        assert event["phase"] == "execution"

    def test_poll_state_detects_retry_counter_change(self, temp_state_file):
        prev_state = {
            "pipeline_status": "RUNNING",
            "current_agent": "planner",
            "current_phase_raw_id": "planning",
            "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
        }
        with open(temp_state_file, 'w') as f:
            json.dump({
                "pipeline_status": "RUNNING",
                "current_agent": "planner",
                "current_phase_raw_id": "planning",
                "counters": {"planner_retries": 1, "executor_retries": 0, "reviewer_retries": 0}
            }, f)
        with patch.object(server, 'load_config', return_value={'pipeline_state_path': temp_state_file}):
            event = asyncio.run(server._poll_state(prev_state))
        assert event is not None

    def test_no_duplicate_events_on_identical_state(self):
        # Create a temp file with consistent state
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "pipeline_status": "RUNNING",
                "current_agent": "planner",
                "current_phase_raw_id": "planning",
                "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
            }, f)
            temp_path = f.name
        
        try:
            # Reset global polling state to match our test state
            server._polling_state = {
                "pipeline_status": "RUNNING",
                "current_agent": "planner",
                "current_phase_raw_id": "planning",
                "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
            }
            prev_state = {
                "pipeline_status": "RUNNING",
                "current_agent": "planner",
                "current_phase_raw_id": "planning",
                "counters": {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0}
            }
            with patch.object(server, 'load_config', return_value={'pipeline_state_path': temp_path}):
                event1 = asyncio.run(server._poll_state(prev_state))
                event2 = asyncio.run(server._poll_state(prev_state))
            assert event1 is None
            assert event2 is None
        finally:
            os.unlink(temp_path)
