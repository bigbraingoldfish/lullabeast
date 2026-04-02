"""Tests for PATCH /api/queue/{entry_id}/parent with auto-reposition (Item 4)."""
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


def _read_queue(path):
    with open(path) as f:
        return json.load(f)


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


class TestPatchParent:
    def test_set_parent_ready_parent_keeps_child_ready(self, client):
        """PATCH parent to READY (in-progress org dependency) → child stays READY, not hold."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", state="READY", position=1)
        child = _make_entry("Child", state="READY", position=2)
        _write_queue(queue_file, [parent, child])

        resp = c.patch(f"/api/queue/{child['id']}/parent",
                       json={"parent_id": parent["id"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "READY"
        assert data["parent_id"] == parent["id"]

    def test_set_parent_blocked_transitions_to_dependency_hold(self, client):
        """PATCH parent to BLOCKED → child state = DEPENDENCY_HOLD."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", state="BLOCKED", position=1)
        child = _make_entry("Child", state="READY", position=2)
        _write_queue(queue_file, [parent, child])

        resp = c.patch(f"/api/queue/{child['id']}/parent",
                       json={"parent_id": parent["id"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "DEPENDENCY_HOLD"
        assert data["parent_id"] == parent["id"]

    def test_clear_parent_transitions_to_ready(self, client):
        """PATCH parent to None → 200, child state = READY, parent_id = None."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", state="READY", position=1)
        child = _make_entry("Child", state="DEPENDENCY_HOLD", position=2,
                             parent_id=parent["id"])
        _write_queue(queue_file, [parent, child])

        resp = c.patch(f"/api/queue/{child['id']}/parent", json={"parent_id": None})
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "READY"
        assert data["parent_id"] is None

    def test_circular_dependency_returns_400(self, client):
        """PATCH parent that creates a cycle → 400."""
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2, parent_id=a["id"])
        _write_queue(queue_file, [a, b])

        # Setting A's parent to B would create A→B→A cycle
        resp = c.patch(f"/api/queue/{a['id']}/parent", json={"parent_id": b["id"]})
        assert resp.status_code == 400
        assert "circular" in resp.json()["detail"].lower()

    def test_set_parent_to_completed_entry_keeps_ready(self, client):
        """PATCH parent to a COMPLETED parent → child state stays READY."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", state="COMPLETED", position=1)
        child = _make_entry("Child", state="READY", position=2)
        _write_queue(queue_file, [parent, child])

        resp = c.patch(f"/api/queue/{child['id']}/parent",
                       json={"parent_id": parent["id"]})
        assert resp.status_code == 200
        # Parent is COMPLETED so child should not go into DEPENDENCY_HOLD
        assert resp.json()["state"] == "READY"

    def test_set_parent_repositions_child_after_parent(self, client):
        """After PATCH parent, child's position is > parent's position."""
        c, queue_file, _ = client
        # Child is currently before parent in the queue
        child = _make_entry("Child", state="READY", position=1)
        parent = _make_entry("Parent", state="READY", position=2)
        d = _make_entry("D", state="READY", position=3)
        _write_queue(queue_file, [child, parent, d])

        resp = c.patch(f"/api/queue/{child['id']}/parent",
                       json={"parent_id": parent["id"]})
        assert resp.status_code == 200

        q = _read_queue(queue_file)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[child["id"]]["position"] > by_id[parent["id"]]["position"]

    def test_set_parent_child_placed_immediately_after_existing_siblings(self, client):
        """New child is placed after all existing siblings of the same parent."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", state="BLOCKED", position=1)
        existing_child = _make_entry("ExistingChild", state="DEPENDENCY_HOLD",
                                     position=2, parent_id=parent["id"])
        new_child = _make_entry("NewChild", state="READY", position=3)
        d = _make_entry("D", state="READY", position=4)
        _write_queue(queue_file, [parent, existing_child, new_child, d])

        resp = c.patch(f"/api/queue/{new_child['id']}/parent",
                       json={"parent_id": parent["id"]})
        assert resp.status_code == 200

        q = _read_queue(queue_file)
        by_id = {e["id"]: e for e in q["queue"]}
        new_pos = by_id[new_child["id"]]["position"]
        existing_pos = by_id[existing_child["id"]]["position"]
        parent_pos = by_id[parent["id"]]["position"]
        # NewChild must be after parent and after existing sibling
        assert new_pos > parent_pos
        assert new_pos > existing_pos

    def test_clear_parent_positions_remain_valid(self, client):
        """After clearing parent, all positions form a gap-free 1..N sequence."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", state="READY", position=1)
        child = _make_entry("Child", state="DEPENDENCY_HOLD", position=2,
                             parent_id=parent["id"])
        d = _make_entry("D", state="READY", position=3)
        _write_queue(queue_file, [parent, child, d])

        c.patch(f"/api/queue/{child['id']}/parent", json={"parent_id": None})

        q = _read_queue(queue_file)
        positions = sorted(e["position"] for e in q["queue"])
        assert positions == list(range(1, len(positions) + 1))
