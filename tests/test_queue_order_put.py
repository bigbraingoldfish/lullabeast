"""Tests for PUT /api/queue/order — atomic full-order replace (reorder MVP)."""
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


class TestPutQueueOrderHappyPath:
    def test_three_roots_reorder(self, client):
        """Full permutation of three roots updates positions and file order."""
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2)
        c_entry = _make_entry("C", position=3)
        _write_queue(queue_file, [a, b, c_entry])

        resp = c.put(
            "/api/queue/order",
            json={"entry_ids": [c_entry["id"], a["id"], b["id"]]},
        )
        assert resp.status_code == 200
        assert resp.json().get("ok") is True

        q = _read_queue(queue_file)
        ids = [e["id"] for e in q["queue"]]
        assert ids == [c_entry["id"], a["id"], b["id"]]
        for i, e in enumerate(q["queue"], 1):
            assert e["position"] == i

    def test_get_queue_matches_after_put(self, client):
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2)
        _write_queue(queue_file, [a, b])

        c.put("/api/queue/order", json={"entry_ids": [b["id"], a["id"]]})
        r = c.get("/api/queue")
        assert r.status_code == 200
        names = [e["name"] for e in r.json()["queue"]]
        assert names == ["B", "A"]


class TestPutQueueOrderParentChild:
    def test_child_before_parent_rejected(self, client):
        c, queue_file, _ = client
        p = _make_entry("P", position=1)
        ch = _make_entry("C", position=2, parent_id=p["id"])
        _write_queue(queue_file, [p, ch])

        resp = c.put("/api/queue/order", json={"entry_ids": [ch["id"], p["id"]]})
        assert resp.status_code == 400
        assert "detail" in resp.json()

    def test_parent_then_child_then_root_valid(self, client):
        c, queue_file, _ = client
        p = _make_entry("P", position=1)
        ch = _make_entry("C", position=2, parent_id=p["id"])
        d = _make_entry("D", position=3)
        _write_queue(queue_file, [p, ch, d])

        resp = c.put(
            "/api/queue/order",
            json={"entry_ids": [d["id"], p["id"], ch["id"]]},
        )
        assert resp.status_code == 200
        q = _read_queue(queue_file)
        ids = [e["id"] for e in q["queue"]]
        assert ids == [d["id"], p["id"], ch["id"]]


class TestPutQueueOrderMultiset:
    def test_wrong_length_rejected(self, client):
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2)
        _write_queue(queue_file, [a, b])

        resp = c.put("/api/queue/order", json={"entry_ids": [a["id"]]})
        assert resp.status_code == 400

    def test_unknown_id_rejected(self, client):
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        _write_queue(queue_file, [a])

        resp = c.put(
            "/api/queue/order",
            json={"entry_ids": [a["id"], str(uuid.uuid4())]},
        )
        assert resp.status_code == 400

    def test_duplicate_id_rejected(self, client):
        c, queue_file, _ = client
        a = _make_entry("A", position=1)
        b = _make_entry("B", position=2)
        _write_queue(queue_file, [a, b])

        resp = c.put("/api/queue/order", json={"entry_ids": [a["id"], a["id"]]})
        assert resp.status_code == 400
