"""Tests for display_ranks field in GET /api/queue (Item 2 — execution order display)."""
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.server import app


def _make_entry(name, state="READY", position=1, parent_id=None, entry_id=None):
    return {
        "id": entry_id or str(uuid.uuid4()),
        "project_path": f"/tmp/project_{name}",
        "idea_id": None,
        "name": name,
        "state": state,
        "position": position,
        "parent_id": parent_id,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
        "blocked_at": None,
        "skip_count": 0,
        "preflight_validated_at": datetime.now(timezone.utc).isoformat(),
        "notes": "",
    }


def _write_queue(path, entries, queue_mode="auto"):
    data = {
        "queue": entries,
        "queue_mode": queue_mode,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return data


@pytest.fixture
def client(tmp_path, monkeypatch):
    queue_file = tmp_path / "pipeline_queue.json"
    pipeline_state_file = tmp_path / "pipeline_state.json"

    def mock_load_config(_config_path=None):
        return {
            "pipeline_queue_path": str(queue_file),
            "pipeline_state_path": str(pipeline_state_file),
            "phase_state_path": str(tmp_path / "phase_state.json"),
            "project_dir_path": str(tmp_path / "pipeline-project"),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "events_path": str(tmp_path / "pipeline_events.jsonl"),
            "ideas_dir": str(tmp_path / "ideas"),
            "port": 18790,
        }

    monkeypatch.setattr("ui.server.load_config", mock_load_config)
    return TestClient(app), queue_file, tmp_path


class TestDisplayRanks:
    def test_flat_queue_all_roots_ranked_sequentially(self, client):
        """Flat queue [A pos1, B pos2, C pos3] → display_ranks A=1, B=2, C=3."""
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2)
        c_entry = _make_entry("C", position=3)
        _write_queue(queue_file, [a, b, c_entry])

        resp = c.get("/api/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert "display_ranks" in data
        ranks = data["display_ranks"]
        assert ranks[a["id"]] == 1
        assert ranks[b["id"]] == 2
        assert ranks[c_entry["id"]] == 3

    def test_child_gets_none_rank(self, client):
        """Queue [A pos1, B pos2 parent=A, C pos3] → A=1, B=None, C=2."""
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2, parent_id=a["id"])
        c_entry = _make_entry("C", position=3)
        _write_queue(queue_file, [a, b, c_entry])

        resp = c.get("/api/queue")
        assert resp.status_code == 200
        ranks = resp.json()["display_ranks"]
        assert ranks[a["id"]] == 1
        assert ranks[b["id"]] is None
        assert ranks[c_entry["id"]] == 2

    def test_grandchild_also_gets_none_rank(self, client):
        """A→B→C chain: A=1, B=None, C=None."""
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2, parent_id=a["id"])
        c_entry = _make_entry("C", position=3, parent_id=b["id"])
        _write_queue(queue_file, [a, b, c_entry])

        resp = c.get("/api/queue")
        ranks = resp.json()["display_ranks"]
        assert ranks[a["id"]] == 1
        assert ranks[b["id"]] is None
        assert ranks[c_entry["id"]] is None

    def test_single_root_entry_ranks_as_1(self, client):
        """Single independent entry → display_rank 1."""
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        _write_queue(queue_file, [a])

        resp = c.get("/api/queue")
        ranks = resp.json()["display_ranks"]
        assert ranks[a["id"]] == 1

    def test_all_children_all_none(self, client):
        """All entries are children → all display_ranks None."""
        c, queue_file, _ = client
        # Orphaned children (parent_id set but parent absent)
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2, parent_id=a["id"])
        c_entry = _make_entry("C", position=3, parent_id=a["id"])
        _write_queue(queue_file, [b, c_entry])  # a not in queue

        resp = c.get("/api/queue")
        ranks = resp.json()["display_ranks"]
        assert ranks[b["id"]] is None
        assert ranks[c_entry["id"]] is None

    def test_ranks_reflect_position_order_not_insertion_order(self, client):
        """If entries are stored out of position order, ranks still follow position."""
        c, queue_file, _ = client
        # Write in reverse position order to file
        z = _make_entry("Z", position=3)
        y = _make_entry("Y", position=2)
        x = _make_entry("X", position=1)
        _write_queue(queue_file, [z, y, x])

        resp = c.get("/api/queue")
        ranks = resp.json()["display_ranks"]
        assert ranks[x["id"]] == 1
        assert ranks[y["id"]] == 2
        assert ranks[z["id"]] == 3

    def test_display_ranks_present_when_queue_empty(self, client):
        """Empty queue returns display_ranks as empty dict (not absent)."""
        c, queue_file, _ = client
        resp = c.get("/api/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert "display_ranks" in data
        assert data["display_ranks"] == {}
