"""Tests for group-move behavior in PATCH /api/queue/{entry_id}/position (Item 3)."""
import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

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


class TestPatchPositionChildRejected:
    def test_patching_position_of_child_returns_409(self, client):
        """PATCH position on a child entry (has parent_id) → 409."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", position=1)
        child = _make_entry("Child", position=2, parent_id=parent["id"])
        _write_queue(queue_file, [parent, child])

        resp = c.patch(f"/api/queue/{child['id']}/position",
                       json={"position": 1})
        assert resp.status_code == 409
        assert "parent" in resp.json()["detail"].lower()

    def test_patching_position_of_root_succeeds(self, client):
        """PATCH position on a root entry (no parent_id) → 200."""
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2)
        _write_queue(queue_file, [a, b])

        resp = c.patch(f"/api/queue/{a['id']}/position", json={"position": 2})
        assert resp.status_code == 200


class TestGroupMoveAtomically:
    def test_parent_with_one_child_moves_together(self, client):
        """Moving a parent also moves its child; both end up at new position."""
        c, queue_file, _ = client
        # Queue: [Parent(1), Child(2), D(3), E(4)]
        parent = _make_entry("Parent", position=1)
        child = _make_entry("Child", position=2, parent_id=parent["id"])
        d = _make_entry("D", position=3)
        e = _make_entry("E", position=4)
        _write_queue(queue_file, [parent, child, d, e])

        # Move parent to position 3 → group occupies 3,4; D,E shift to 1,2
        resp = c.patch(f"/api/queue/{parent['id']}/position", json={"position": 3})
        assert resp.status_code == 200

        q = _read_queue(queue_file)
        by_id = {e["id"]: e for e in q["queue"]}
        parent_pos = by_id[parent["id"]]["position"]
        child_pos = by_id[child["id"]]["position"]
        # Parent must come before child, child must be immediately after parent
        assert parent_pos < child_pos
        assert child_pos == parent_pos + 1
        # D and E must be before the group
        assert by_id[d["id"]]["position"] < parent_pos
        assert by_id[e["id"]]["position"] < parent_pos

    def test_group_with_two_children_moves_together(self, client):
        """Parent + 2 children: all 3 move as a block."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", position=1)
        child1 = _make_entry("Child1", position=2, parent_id=parent["id"])
        child2 = _make_entry("Child2", position=3, parent_id=parent["id"])
        d = _make_entry("D", position=4)
        _write_queue(queue_file, [parent, child1, child2, d])

        resp = c.patch(f"/api/queue/{parent['id']}/position", json={"position": 4})
        assert resp.status_code == 200

        q = _read_queue(queue_file)
        by_id = {e["id"]: e for e in q["queue"]}
        parent_pos = by_id[parent["id"]]["position"]
        # D should come first (shifted up)
        assert by_id[d["id"]]["position"] < parent_pos
        # Both children should follow parent
        assert by_id[child1["id"]]["position"] > parent_pos
        assert by_id[child2["id"]]["position"] > parent_pos

    def test_grandchild_chain_moves_together(self, client):
        """A→B→C chain: moving A also brings B and C."""
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2, parent_id=a["id"])
        c_entry = _make_entry("C", position=3, parent_id=b["id"])
        d = _make_entry("D", position=4)
        e = _make_entry("E", position=5)
        _write_queue(queue_file, [a, b, c_entry, d, e])

        # Move A (the root) to position 3 in the overall list
        resp = c.patch(f"/api/queue/{a['id']}/position", json={"position": 3})
        assert resp.status_code == 200

        q = _read_queue(queue_file)
        by_id = {e["id"]: e for e in q["queue"]}
        a_pos = by_id[a["id"]]["position"]
        b_pos = by_id[b["id"]]["position"]
        c_pos = by_id[c_entry["id"]]["position"]
        # A,B,C are a block; D,E come before or after
        # Key invariant: A < B < C and they are contiguous
        assert a_pos < b_pos < c_pos
        # D and E must be before A (they were shifted)
        assert by_id[d["id"]]["position"] < a_pos
        assert by_id[e["id"]]["position"] < a_pos

    def test_all_positions_are_contiguous_1_to_n(self, client):
        """After any group move, all positions form a gap-free 1..N sequence."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", position=1)
        child = _make_entry("Child", position=2, parent_id=parent["id"])
        d = _make_entry("D", position=3)
        _write_queue(queue_file, [parent, child, d])

        c.patch(f"/api/queue/{parent['id']}/position", json={"position": 3})

        q = _read_queue(queue_file)
        positions = sorted(e["position"] for e in q["queue"])
        assert positions == list(range(1, len(positions) + 1))

    def test_lone_root_no_children_moves_normally(self, client):
        """A root entry with no children uses the normal (non-group) move path."""
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2)
        c_entry = _make_entry("C", position=3)
        _write_queue(queue_file, [a, b, c_entry])

        resp = c.patch(f"/api/queue/{a['id']}/position", json={"position": 3})
        assert resp.status_code == 200

        q = _read_queue(queue_file)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[a["id"]]["position"] == 3
        assert by_id[b["id"]]["position"] == 1
        assert by_id[c_entry["id"]]["position"] == 2

    def test_single_write_call_per_patch(self, client, monkeypatch):
        """Atomic guarantee: queue file is written exactly once per PATCH position."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", position=1)
        child = _make_entry("Child", position=2, parent_id=parent["id"])
        d = _make_entry("D", position=3)
        _write_queue(queue_file, [parent, child, d])

        write_calls = []
        original_write = __import__("ui.server", fromlist=["_write_queue_file"])._write_queue_file

        def counting_write(path, data):
            write_calls.append(path)
            original_write(path, data)

        monkeypatch.setattr("ui.server._write_queue_file", counting_write)

        resp = c.patch(f"/api/queue/{parent['id']}/position", json={"position": 3})
        assert resp.status_code == 200
        assert len(write_calls) == 1

    def test_queue_file_array_matches_position_order_after_group_move(self, client):
        """Persisted queue list order matches execution order (sorted by position)."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", position=1)
        child = _make_entry("Child", position=2, parent_id=parent["id"])
        d = _make_entry("D", position=3)
        e = _make_entry("E", position=4)
        _write_queue(queue_file, [parent, child, d, e])

        c.patch(f"/api/queue/{parent['id']}/position", json={"position": 3})

        q = _read_queue(queue_file)
        assert q["queue"] == sorted(q["queue"], key=lambda x: x["position"])
        # Parent row immediately precedes child in stored array
        ids = [x["id"] for x in q["queue"]]
        pi, ci = ids.index(parent["id"]), ids.index(child["id"])
        assert ci == pi + 1

    def test_get_queue_returns_entries_sorted_by_position(self, client):
        """GET /api/queue lists entries by position even if file array order is wrong."""
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2)
        c_entry = _make_entry("C", position=3)
        _write_queue(queue_file, [c_entry, a, b])

        resp = c.get("/api/queue")
        assert resp.status_code == 200
        names = [e["name"] for e in resp.json()["queue"]]
        assert names == ["A", "B", "C"]
