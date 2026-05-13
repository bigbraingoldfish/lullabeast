"""Tests for _tail_events_file, _read_log_tail_lines(max_bytes), and GET /api/log/tail.

TDD: tests are written before implementation. Run with pytest; all should fail
until Steps 1 and 2 of the implementation plan are complete.
"""
import inspect
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import ui.server as server
from ui.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_events_file_offset():
    """Reset _events_file_offset to -1 before each test and restore after."""
    original = server._events_file_offset
    server._events_file_offset = -1
    yield
    server._events_file_offset = original


@pytest.fixture
def events_file(tmp_path):
    """Return path to an empty JSONL events file in tmp_path."""
    p = tmp_path / "pipeline_events.jsonl"
    p.write_text("")
    return p


@pytest.fixture
def events_file_with_lines(tmp_path):
    """JSONL file with 5 valid event lines."""
    p = tmp_path / "pipeline_events.jsonl"
    lines = [
        {"ts": f"2026-01-01T00:0{i}:00Z", "event": "gate_pass", "agent": "executor",
         "phase": f"CORE-{i}", "detail": f"attempt {i}"}
        for i in range(5)
    ]
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return p


# ---------------------------------------------------------------------------
# Tests for _tail_events_file
# ---------------------------------------------------------------------------

class TestTailEventsFile:

    def test_first_call_returns_empty_and_parks_at_eof(self, events_file_with_lines):
        """On first call (_events_file_offset == -1), no history is replayed."""
        result = server._poll_pipeline_events_file(str(events_file_with_lines))
        assert result == []
        assert server._events_file_offset == events_file_with_lines.stat().st_size

    def test_returns_new_lines_after_first_call(self, events_file_with_lines):
        """After parking at EOF, new lines appended to the file are returned."""
        # First call parks
        server._poll_pipeline_events_file(str(events_file_with_lines))
        # Append 2 new events
        new_event1 = {"ts": "2026-01-01T01:00:00Z", "event": "gate_fail", "agent": "executor", "phase": "CORE-5", "detail": "d"}
        new_event2 = {"ts": "2026-01-01T01:01:00Z", "event": "escalation_trigger", "agent": "executor", "phase": "CORE-5", "detail": "e"}
        with open(events_file_with_lines, "a") as f:
            f.write(json.dumps(new_event1) + "\n")
            f.write(json.dumps(new_event2) + "\n")
        result = server._poll_pipeline_events_file(str(events_file_with_lines))
        assert len(result) == 2
        assert result[0]["event_type"] == "gate_fail"
        assert result[1]["event_type"] == "escalation_trigger"

    def test_handles_file_rotation(self, tmp_path):
        """When file shrinks (rotation), offset is reset and new content is read."""
        p = tmp_path / "events.jsonl"
        # Write a large initial file and park at its EOF.
        p.write_text("x" * 300 + "\n")
        server._poll_pipeline_events_file(str(p))
        assert server._events_file_offset == 301

        # Overwrite with shorter content (simulate log rotation — new file, smaller size).
        new_event = {"ts": "2026-01-01T02:00:00Z", "event": "gate_pass", "agent": "executor", "phase": "CORE-1", "detail": "r"}
        p.write_text(json.dumps(new_event) + "\n")

        result = server._poll_pipeline_events_file(str(p))
        assert len(result) == 1
        assert result[0]["event_type"] == "gate_pass"

    def test_skips_malformed_json(self, tmp_path):
        """Non-JSON lines are skipped; valid lines are still returned."""
        p = tmp_path / "events.jsonl"
        p.write_text("")
        server._poll_pipeline_events_file(str(p))  # park at EOF (size 0)
        with open(p, "a") as f:
            f.write('{"ts": "2026-01-01T03:00:00Z", "event": "gate_pass", "agent": "a", "phase": "P1", "detail": null}\n')
            f.write('this is not valid json\n')
            f.write('{"ts": "2026-01-01T03:01:00Z", "event": "gate_fail", "agent": "b", "phase": "P2", "detail": null}\n')
        result = server._poll_pipeline_events_file(str(p))
        assert len(result) == 2
        types = {e["event_type"] for e in result}
        assert types == {"gate_pass", "gate_fail"}

    def test_missing_file_returns_empty(self, tmp_path):
        """A path that does not exist returns []."""
        result = server._poll_pipeline_events_file(str(tmp_path / "nonexistent.jsonl"))
        assert result == []

    def test_empty_path_returns_empty(self):
        """An empty path string returns []."""
        assert server._poll_pipeline_events_file("") == []

    def test_event_shape_normalises_field_aliases(self, tmp_path):
        """Lines using 'event' and 'timestamp' aliases are normalised to the standard shape."""
        p = tmp_path / "events.jsonl"
        p.write_text("")
        server._poll_pipeline_events_file(str(p))  # park
        raw = {"timestamp": "2026-01-01T04:00:00Z", "event": "gate_pass",
               "agent": "executor", "phase": "CORE-1", "detail": "d1"}
        with open(p, "a") as f:
            f.write(json.dumps(raw) + "\n")
        result = server._poll_pipeline_events_file(str(p))
        assert len(result) == 1
        evt = result[0]
        assert set(evt.keys()) >= {"id", "ts", "event_type", "agent", "phase", "detail"}
        assert evt["event_type"] == "gate_pass"
        assert evt["agent"] == "executor"
        assert evt["phase"] == "CORE-1"
        assert evt["id"]  # non-empty UUID

    def test_nothing_new_returns_empty(self, events_file_with_lines):
        """Calling twice without new content returns [] on the second call."""
        server._poll_pipeline_events_file(str(events_file_with_lines))  # park
        result = server._poll_pipeline_events_file(str(events_file_with_lines))
        assert result == []

    def test_polling_loop_wires_poll_pipeline_events_file(self):
        """The polling loop body must call _poll_pipeline_events_file (not the old pass stub)."""
        src = inspect.getsource(server._polling_loop)
        assert "_poll_pipeline_events_file" in src, (
            "_polling_loop must call _poll_pipeline_events_file for live gate-event delivery"
        )


# ---------------------------------------------------------------------------
# Tests for _read_log_tail_lines with max_bytes parameter
# ---------------------------------------------------------------------------

class TestReadLogTailLines:

    def test_existing_call_signature_unchanged(self, tmp_path):
        """Positional call _read_log_tail_lines(path, 5) still works (backward compat)."""
        p = tmp_path / "test.log"
        p.write_text("line1\nline2\nline3\n")
        result = server._read_log_tail_lines(str(p), 5)
        assert result == ["line1", "line2", "line3"]

    def test_custom_max_bytes_reads_more_content(self, tmp_path):
        """With max_bytes=524288, 600 lines of ~100 bytes each are returned correctly."""
        p = tmp_path / "big.log"
        all_lines = [f"log line number {i:04d} with some padding text here" for i in range(600)]
        p.write_text("\n".join(all_lines) + "\n")
        result = server._read_log_tail_lines(str(p), max_lines=500, max_bytes=524288)
        assert len(result) == 500
        assert result[-1] == all_lines[-1]
        assert result[0] == all_lines[100]  # last 500 of 600


# ---------------------------------------------------------------------------
# Tests for GET /api/log/tail endpoint
# ---------------------------------------------------------------------------

class TestLogTailEndpoint:

    @pytest.fixture
    def mock_cfg(self, tmp_path):
        return {
            "autodev_pipeline_root": str(tmp_path),
            "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
            "events_path": str(tmp_path / "pipeline_events.jsonl"),
        }

    def test_returns_200_when_log_absent(self, mock_cfg, tmp_path):
        """Returns 200 with empty lines when orchestrator.spawn.log does not exist."""
        with patch("ui.server.load_config", return_value=mock_cfg):
            r = client.get("/api/log/tail")
        assert r.status_code == 200
        data = r.json()
        assert data["lines"] == []
        assert data["path"].endswith("orchestrator.spawn.log")

    def test_returns_lines_from_log_file(self, mock_cfg, tmp_path):
        """Returns actual log lines when orchestrator.spawn.log exists."""
        log = tmp_path / "orchestrator.spawn.log"
        log.write_text("\n".join(f"line {i}" for i in range(10)) + "\n")
        with patch("ui.server.load_config", return_value=mock_cfg):
            r = client.get("/api/log/tail")
        assert r.status_code == 200
        data = r.json()
        assert len(data["lines"]) == 10
        assert data["lines"][0] == "line 0"

    def test_respects_lines_query_param(self, mock_cfg, tmp_path):
        """?lines=20 returns at most 20 lines even when log has more."""
        log = tmp_path / "orchestrator.spawn.log"
        log.write_text("\n".join(f"entry {i}" for i in range(600)) + "\n")
        with patch("ui.server.load_config", return_value=mock_cfg):
            r = client.get("/api/log/tail?lines=20")
        assert r.status_code == 200
        assert len(r.json()["lines"]) == 20

    def test_empty_pipeline_root_returns_empty(self, tmp_path):
        """When autodev_pipeline_root is empty, returns lines=[] and path=''."""
        cfg = {"autodev_pipeline_root": "", "pipeline_state_path": str(tmp_path / "s.json")}
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/log/tail")
        assert r.status_code == 200
        data = r.json()
        assert data["lines"] == []
        assert data["path"] == ""

    def test_path_is_in_pipeline_root_not_tmp(self, mock_cfg, tmp_path):
        """The response path must be inside pipeline_root, not /tmp/orchestrator.log."""
        with patch("ui.server.load_config", return_value=mock_cfg):
            r = client.get("/api/log/tail")
        path = r.json()["path"]
        assert path != "/tmp/orchestrator.log"
        assert str(tmp_path) in path
