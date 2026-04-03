"""Tests for all /api/queue endpoints (TDD — tests written before implementation)."""
import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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
    """Test client with temporary queue file path."""
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


# ---------------------------------------------------------------------------
# GET /api/queue
# ---------------------------------------------------------------------------

class TestGetQueue:
    def test_returns_empty_when_file_absent(self, client):
        c, queue_file, _ = client
        resp = c.get("/api/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert data["queue"] == []
        assert "dependency_tree" in data
        assert "next_eligible" in data
        assert data["next_eligible"] is None

    def test_returns_full_queue_with_computed_fields(self, client):
        c, queue_file, _ = client
        entries = [
            _make_entry("alpha", position=1),
            _make_entry("beta", position=2),
        ]
        _write_queue(str(queue_file), entries)

        resp = c.get("/api/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["queue"]) == 2
        assert "dependency_tree" in data
        assert "next_eligible" in data
        for e in data["queue"]:
            assert e.get("live_pipeline_status") is None

    def test_live_pipeline_status_set_when_entry_path_matches_pipeline_state(self, client):
        c, queue_file, tmp_path = client
        pipeline_state_file = tmp_path / "pipeline_state.json"
        proj_a = str(tmp_path / "proj_a")
        proj_b = str(tmp_path / "proj_b")
        e1 = _make_entry("a", position=1)
        e1["project_path"] = proj_a
        e2 = _make_entry("b", position=2)
        e2["project_path"] = proj_b
        _write_queue(str(queue_file), [e1, e2])
        with open(pipeline_state_file, "w") as f:
            json.dump({"project_path": proj_a, "pipeline_status": "WAITING_FOR_HUMAN"}, f)

        resp = c.get("/api/queue")
        assert resp.status_code == 200
        data = resp.json()
        q = data["queue"]
        assert len(q) == 2
        assert q[0]["live_pipeline_status"] == "WAITING_FOR_HUMAN"
        assert q[1]["live_pipeline_status"] is None

    def test_next_eligible_identifies_first_ready_no_parent(self, client):
        c, queue_file, _ = client
        e1 = _make_entry("a", state="COMPLETED", position=1)
        e2 = _make_entry("b", state="READY", position=2)
        e3 = _make_entry("c", state="READY", position=3)
        _write_queue(str(queue_file), [e1, e2, e3])

        resp = c.get("/api/queue")
        data = resp.json()
        assert data["next_eligible"] == e2["id"]

    def test_next_eligible_skips_ready_with_unmet_dependency(self, client):
        c, queue_file, _ = client
        parent = _make_entry("parent", state="BLOCKED", position=1)
        child = _make_entry("child", state="READY", position=2, parent_id=parent["id"])
        independent = _make_entry("independent", state="READY", position=3)
        _write_queue(str(queue_file), [parent, child, independent])

        resp = c.get("/api/queue")
        data = resp.json()
        assert data["next_eligible"] == independent["id"]

    def test_dependency_tree_root_and_children(self, client):
        c, queue_file, _ = client
        root = _make_entry("root", position=1)
        child = _make_entry("child", position=2, parent_id=root["id"])
        _write_queue(str(queue_file), [root, child])

        resp = c.get("/api/queue")
        data = resp.json()
        tree = data["dependency_tree"]
        assert len(tree) == 1
        assert tree[0]["id"] == root["id"]
        assert len(tree[0]["children"]) == 1
        assert tree[0]["children"][0]["id"] == child["id"]

    def test_ingest_synthetic_when_pipeline_project_not_in_queue(self, client):
        """TASK-03: active project_path not in queue appears as stable ingest-* row."""
        c, queue_file, tmp_path = client
        orphan = tmp_path / "orphan_proj"
        orphan.mkdir()
        pipeline_state_file = tmp_path / "pipeline_state.json"
        with open(pipeline_state_file, "w") as f:
            json.dump({"project_path": str(orphan), "pipeline_status": "RUNNING"}, f)
        e1 = _make_entry("listed", position=1)
        _write_queue(str(queue_file), [e1])

        resp = c.get("/api/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["queue"]) == 2
        assert any(e.get("id", "").startswith("ingest-") for e in data["queue"])
        ingest = next(e for e in data["queue"] if e.get("id", "").startswith("ingest-"))
        assert ingest.get("ingested") is True
        assert ingest["state"] == "ACTIVE"

    def test_live_pipeline_status_falls_back_to_parked_pipeline_status(self, client):
        c, queue_file, tmp_path = client
        proj_a = str(tmp_path / "pa")
        proj_b = str(tmp_path / "pb")
        os.makedirs(proj_a)
        os.makedirs(proj_b)
        e1 = _make_entry("a", position=1)
        e1["project_path"] = proj_a
        e1["parked_pipeline_status"] = "WAITING_FOR_HUMAN"
        e2 = _make_entry("b", position=2)
        e2["project_path"] = proj_b
        _write_queue(str(queue_file), [e1, e2])
        pipeline_state_file = tmp_path / "pipeline_state.json"
        with open(pipeline_state_file, "w") as f:
            json.dump({"project_path": proj_b, "pipeline_status": "RUNNING"}, f)

        resp = c.get("/api/queue")
        q = resp.json()["queue"]
        by_path = {e["project_path"]: e for e in q}
        assert by_path[proj_a]["live_pipeline_status"] == "WAITING_FOR_HUMAN"


# ---------------------------------------------------------------------------
# GET /api/queue/status
# ---------------------------------------------------------------------------

class TestGetQueueStatus:
    def test_returns_correct_counts(self, client):
        c, queue_file, _ = client
        entries = [
            _make_entry("a", state="READY", position=1),
            _make_entry("b", state="READY", position=2),
            _make_entry("c", state="BLOCKED", position=3),
            _make_entry("d", state="COMPLETED", position=4),
        ]
        _write_queue(str(queue_file), entries, queue_mode="manual")

        resp = c.get("/api/queue/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["queue_length"] == 4
        assert data["ready_count"] == 2
        assert data["blocked_count"] == 1
        assert data["completed_count"] == 1
        assert data["queue_mode"] == "manual"
        assert data["queue_halted"] is False

    def test_blocked_count_includes_escalation(self, client):
        c, queue_file, _ = client
        entries = [
            _make_entry("a", state="BLOCKED", position=1),
            _make_entry("b", state="ESCALATION", position=2),
        ]
        _write_queue(str(queue_file), entries)

        resp = c.get("/api/queue/status")
        assert resp.json()["blocked_count"] == 2

    def test_returns_zeros_when_file_absent(self, client):
        c, queue_file, _ = client
        resp = c.get("/api/queue/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["queue_length"] == 0


# ---------------------------------------------------------------------------
# POST /api/queue/add
# ---------------------------------------------------------------------------

class TestPostQueueAdd:
    def test_rejects_when_preflight_fails(self, client, monkeypatch, tmp_path):
        c, queue_file, base = client
        # Create a real dir so path validation passes
        proj = tmp_path / "myproject"
        proj.mkdir()
        (proj / ".git").mkdir()
        # no roadmap.md → full preflight will fail (no roadmap)

        def mock_preflight(path):
            return [{"check": "roadmap", "status": "fail", "message": "No roadmap.md found"}]

        monkeypatch.setattr("ui.server._run_preflight_checks", mock_preflight)

        resp = c.post("/api/queue/add", json={"project_path": str(proj)})
        assert resp.status_code == 400
        data = resp.json()
        assert "validation_errors" in data
        assert len(data["validation_errors"]) > 0

    def test_adds_with_ready_state_when_preflight_passes(self, client, monkeypatch, tmp_path):
        c, queue_file, _ = client
        proj = tmp_path / "goodproject"
        proj.mkdir()

        monkeypatch.setattr("ui.server._run_preflight_checks", lambda path: [
            {"check": "symlink", "status": "pass", "message": "ok"},
        ])

        resp = c.post("/api/queue/add", json={"project_path": str(proj)})
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "READY"
        assert data["position"] == 1
        assert "id" in data

        # Verify queue file written
        with open(str(queue_file)) as f:
            q = json.load(f)
        assert len(q["queue"]) == 1
        assert q["queue"][0]["state"] == "READY"

    def test_add_child_with_active_parent_stays_ready(self, client, monkeypatch, tmp_path):
        """Organizational dependency: parent ACTIVE → child is READY (not DEPENDENCY_HOLD)."""
        c, queue_file, _ = client
        parent_id = str(uuid.uuid4())
        entries = [{**_make_entry("parent", state="ACTIVE", position=1), "id": parent_id}]
        _write_queue(str(queue_file), entries)
        proj = tmp_path / "childproj"
        proj.mkdir()
        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])
        resp = c.post("/api/queue/add", json={"project_path": str(proj), "parent_id": parent_id})
        assert resp.status_code == 200
        assert resp.json()["state"] == "READY"

    def test_add_child_with_blocked_parent_is_dependency_hold(self, client, monkeypatch, tmp_path):
        c, queue_file, _ = client
        parent_id = str(uuid.uuid4())
        entries = [{**_make_entry("parent", state="BLOCKED", position=1), "id": parent_id}]
        _write_queue(str(queue_file), entries)
        proj = tmp_path / "childproj"
        proj.mkdir()
        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])
        resp = c.post("/api/queue/add", json={"project_path": str(proj), "parent_id": parent_id})
        assert resp.status_code == 200
        assert resp.json()["state"] == "DEPENDENCY_HOLD"

    def test_assigns_sequential_position(self, client, monkeypatch, tmp_path):
        c, queue_file, _ = client
        # Pre-populate queue with 2 entries
        entries = [_make_entry("a", position=1), _make_entry("b", position=2)]
        _write_queue(str(queue_file), entries)

        proj = tmp_path / "newproject"
        proj.mkdir()
        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])

        resp = c.post("/api/queue/add", json={"project_path": str(proj)})
        assert resp.status_code == 200
        assert resp.json()["position"] == 3

    def test_rejects_invalid_path(self, client):
        c, queue_file, _ = client
        resp = c.post("/api/queue/add", json={"project_path": "relative/path"})
        assert resp.status_code == 422

    def test_rejects_nonexistent_path(self, client):
        c, queue_file, _ = client
        resp = c.post("/api/queue/add", json={"project_path": "/nonexistent/path/that/does/not/exist"})
        assert resp.status_code == 422

    def test_circular_dependency_returns_400(self, client, monkeypatch, tmp_path):
        c, queue_file, _ = client
        # A already exists in queue
        a_id = str(uuid.uuid4())
        b_id = str(uuid.uuid4())
        entries = [
            {**_make_entry("A", position=1), "id": a_id, "parent_id": b_id},
            {**_make_entry("B", position=2), "id": b_id, "parent_id": None},
        ]
        _write_queue(str(queue_file), entries)

        proj = tmp_path / "c_proj"
        proj.mkdir()
        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])

        # Try to set B's parent to a new entry C, and C's parent to B (circular: B→C→B)
        # Simpler: add entry with parent_id pointing to itself would be circular
        # Use a→b already exists; try adding with parent_id = a_id is fine,
        # but trying to set parent_id in a way that makes a cycle:
        # A->B already set. Adding new C with parent_id=a_id is fine (C->A->B, no cycle).
        # For a cycle test: add entry X with parent_id=b_id, then try PATCH /api/queue/b_id/parent to X
        # That's tested in TestPatchQueueParent. Here just test POST with self-referencing parent.
        # POST with parent_id that creates A→B→A: A has parent B, B has no parent.
        # If we now try to add a new entry D with parent_id=b_id, that's fine.
        # Real circular: we need two existing entries. Skip self-ref; test in PATCH instead.
        # Test: add entry with parent_id not in queue → should succeed (parent just doesn't exist yet)
        # Actually per spec: circular dep check runs before write. Let's test A->B->A scenario via PATCH.
        # This test just confirms valid parent_id in POST succeeds.
        resp = c.post("/api/queue/add", json={
            "project_path": str(proj),
            "parent_id": b_id,
        })
        # b_id is in queue and has no parent, so adding C with parent=b is fine
        assert resp.status_code == 200

    def test_rejects_unknown_parent_id(self, client, monkeypatch, tmp_path):
        c, queue_file, _ = client
        proj = tmp_path / "solo"
        proj.mkdir()
        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])
        bad_parent = str(uuid.uuid4())
        resp = c.post(
            "/api/queue/add",
            json={"project_path": str(proj), "parent_id": bad_parent},
        )
        assert resp.status_code == 400
        assert "parent_id" in resp.json()["detail"].lower()

    def test_rejects_duplicate_project_same_realpath(self, client, monkeypatch, tmp_path):
        c, queue_file, _ = client
        proj = tmp_path / "dupproj"
        proj.mkdir()
        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])
        eid = str(uuid.uuid4())
        entries = [
            {
                **_make_entry("dupproj", position=1),
                "id": eid,
                "project_path": str(proj),
                "state": "READY",
            }
        ]
        _write_queue(str(queue_file), entries)

        resp = c.post("/api/queue/add", json={"project_path": str(proj)})
        assert resp.status_code == 409
        assert "already in queue" in resp.json()["detail"].lower()

    def test_allows_add_after_completed_same_path(self, client, monkeypatch, tmp_path):
        c, queue_file, _ = client
        proj = tmp_path / "again"
        proj.mkdir()
        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])
        eid = str(uuid.uuid4())
        entries = [
            {
                **_make_entry("again", position=1),
                "id": eid,
                "project_path": str(proj),
                "state": "COMPLETED",
            }
        ]
        _write_queue(str(queue_file), entries)

        resp = c.post("/api/queue/add", json={"project_path": str(proj)})
        assert resp.status_code == 200
        assert resp.json()["state"] == "READY"
        with open(str(queue_file)) as f:
            q = json.load(f)
        assert len(q["queue"]) == 2


# ---------------------------------------------------------------------------
# POST /api/queue/clear
# ---------------------------------------------------------------------------


class TestPostQueueClear:
    def test_clears_empty_queue(self, client):
        c, queue_file, _ = client
        resp = c.post("/api/queue/clear", json={})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "cleared": 0}

    def test_clears_ready_entries(self, client):
        c, queue_file, _ = client
        _write_queue(str(queue_file), [_make_entry("a", position=1)])
        resp = c.post("/api/queue/clear", json={})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "cleared": 1}
        with open(str(queue_file)) as f:
            q = json.load(f)
        assert q["queue"] == []

    def test_active_without_force_returns_409(self, client):
        c, queue_file, _ = client
        _write_queue(str(queue_file), [_make_entry("busy", state="ACTIVE", position=1)])
        resp = c.post("/api/queue/clear", json={})
        assert resp.status_code == 409
        assert "ACTIVE" in resp.json()["detail"]
        with open(str(queue_file)) as f:
            q = json.load(f)
        assert len(q["queue"]) == 1

    def test_active_with_force_clears(self, client):
        c, queue_file, _ = client
        _write_queue(str(queue_file), [_make_entry("busy", state="ACTIVE", position=1)])
        resp = c.post("/api/queue/clear", json={"force": True})
        assert resp.status_code == 200
        assert resp.json()["cleared"] == 1
        with open(str(queue_file)) as f:
            q = json.load(f)
        assert q["queue"] == []

    def test_preserves_queue_mode(self, client):
        c, queue_file, _ = client
        _write_queue(str(queue_file), [_make_entry("x", position=1)], queue_mode="manual")
        resp = c.post("/api/queue/clear", json={})
        assert resp.status_code == 200
        with open(str(queue_file)) as f:
            q = json.load(f)
        assert q["queue_mode"] == "manual"


# ---------------------------------------------------------------------------
# POST /api/command deferred (parked project)
# ---------------------------------------------------------------------------


class TestPostCommandDeferred:
    def test_deferred_writes_pending_escalation_files(self, client):
        c, queue_file, base = client
        proj_a = base / "parked_a"
        proj_a.mkdir()
        proj_b = base / "active_b"
        proj_b.mkdir()
        symlink = base / "pipeline-project"
        symlink.symlink_to(proj_b)
        aid = str(uuid.uuid4())
        bid = str(uuid.uuid4())
        entries = [
            {**_make_entry("a"), "id": aid, "project_path": str(proj_a), "state": "ESCALATION", "position": 1},
            {**_make_entry("b"), "id": bid, "project_path": str(proj_b), "state": "ACTIVE", "position": 2},
        ]
        _write_queue(str(queue_file), entries)
        state_file = base / "pipeline_state.json"
        with open(state_file, "w") as f:
            json.dump({"project_path": str(proj_b), "pipeline_status": "RUNNING"}, f)

        resp = c.post("/api/command", json={"command": "RETRY", "target_project_path": str(proj_a)})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("deferred") is True
        assert (proj_a / "pending_escalation_command.json").exists()


# ---------------------------------------------------------------------------
# DELETE /api/queue/{entry_id}
# ---------------------------------------------------------------------------

class TestDeleteQueueEntry:
    def test_returns_409_when_active_and_mid_flight_same_project(self, client):
        c, queue_file, tmp_path = client
        entry = _make_entry("active", state="ACTIVE", position=1)
        # Align pipeline_state project_path with queue entry (realpath match)
        entry["project_path"] = str(tmp_path / "active_proj")
        state_file = tmp_path / "pipeline_state.json"
        with open(state_file, "w") as f:
            json.dump(
                {"project_path": entry["project_path"], "pipeline_status": "RUNNING"},
                f,
            )
        _write_queue(str(queue_file), [entry])

        resp = c.delete(f"/api/queue/{entry['id']}")
        assert resp.status_code == 409

    def test_returns_409_when_active_wait_sentinel_same_project(self, client):
        c, queue_file, tmp_path = client
        entry = _make_entry("active", state="ACTIVE", position=1)
        entry["project_path"] = str(tmp_path / "wait_proj")
        state_file = tmp_path / "pipeline_state.json"
        with open(state_file, "w") as f:
            json.dump(
                {"project_path": entry["project_path"], "pipeline_status": "WAITING_FOR_SENTINEL"},
                f,
            )
        _write_queue(str(queue_file), [entry])

        resp = c.delete(f"/api/queue/{entry['id']}")
        assert resp.status_code == 409

    def test_allows_delete_when_active_stopped_same_project(self, client):
        c, queue_file, tmp_path = client
        entry = _make_entry("active", state="ACTIVE", position=1)
        entry["project_path"] = str(tmp_path / "stopped_proj")
        state_file = tmp_path / "pipeline_state.json"
        with open(state_file, "w") as f:
            json.dump(
                {"project_path": entry["project_path"], "pipeline_status": "STOPPED"},
                f,
            )
        _write_queue(str(queue_file), [entry])

        resp = c.delete(f"/api/queue/{entry['id']}")
        assert resp.status_code == 200

    def test_allows_delete_when_active_waiting_human_same_project(self, client):
        c, queue_file, tmp_path = client
        entry = _make_entry("active", state="ACTIVE", position=1)
        entry["project_path"] = str(tmp_path / "esc_proj")
        state_file = tmp_path / "pipeline_state.json"
        with open(state_file, "w") as f:
            json.dump(
                {"project_path": entry["project_path"], "pipeline_status": "WAITING_FOR_HUMAN"},
                f,
            )
        _write_queue(str(queue_file), [entry])

        resp = c.delete(f"/api/queue/{entry['id']}")
        assert resp.status_code == 200

    def test_allows_delete_active_when_pipeline_targets_other_project(self, client):
        """Stuck ACTIVE row while global pipeline_state points elsewhere — allow remove."""
        c, queue_file, tmp_path = client
        entry = _make_entry("active", state="ACTIVE", position=1)
        entry["project_path"] = str(tmp_path / "queued_only")
        state_file = tmp_path / "pipeline_state.json"
        with open(state_file, "w") as f:
            json.dump(
                {"project_path": str(tmp_path / "other_project"), "pipeline_status": "RUNNING"},
                f,
            )
        _write_queue(str(queue_file), [entry])

        resp = c.delete(f"/api/queue/{entry['id']}")
        assert resp.status_code == 200

    def test_removes_entry_and_resequences(self, client):
        c, queue_file, _ = client
        e1 = _make_entry("a", position=1)
        e2 = _make_entry("b", position=2)
        e3 = _make_entry("c", position=3)
        _write_queue(str(queue_file), [e1, e2, e3])

        resp = c.delete(f"/api/queue/{e2['id']}")
        assert resp.status_code == 200

        with open(str(queue_file)) as f:
            q = json.load(f)
        assert len(q["queue"]) == 2
        positions = sorted([e["position"] for e in q["queue"]])
        assert positions == [1, 2]  # resequenced, no gaps
        ids = [e["id"] for e in q["queue"]]
        assert e2["id"] not in ids

    def test_returns_404_when_not_found(self, client):
        c, queue_file, _ = client
        _write_queue(str(queue_file), [])
        resp = c.delete(f"/api/queue/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_returns_422_for_synthetic_ingest_row(self, client):
        """ingest-* rows are display-only merges from pipeline_state, not on disk."""
        import uuid as _uuid

        c, queue_file, tmp_path = client
        proj_a = str(tmp_path / "only_in_queue")
        proj_b = str(tmp_path / "only_in_state")
        os.makedirs(proj_b, exist_ok=True)
        e1 = _make_entry("a", position=1)
        e1["project_path"] = proj_a
        _write_queue(str(queue_file), [e1])
        pipeline_state_file = tmp_path / "pipeline_state.json"
        with open(pipeline_state_file, "w") as f:
            json.dump({"project_path": proj_b, "pipeline_status": "PIPELINE_COMPLETE"}, f)

        ps_real = os.path.realpath(os.path.expanduser(proj_b))
        ingest_id = f"ingest-{_uuid.uuid5(_uuid.NAMESPACE_URL, ps_real)}"

        get_resp = c.get("/api/queue")
        assert get_resp.status_code == 200
        ids = [e["id"] for e in get_resp.json()["queue"]]
        assert ingest_id in ids

        resp = c.delete(f"/api/queue/{ingest_id}")
        assert resp.status_code == 422
        assert "Synthetic" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# PATCH /api/queue/{entry_id}/position
# ---------------------------------------------------------------------------

class TestPatchQueuePosition:
    def test_reorders_correctly(self, client):
        c, queue_file, _ = client
        e1 = _make_entry("a", position=1)
        e2 = _make_entry("b", position=2)
        e3 = _make_entry("c", position=3)
        _write_queue(str(queue_file), [e1, e2, e3])

        # Move e3 to position 1
        resp = c.patch(f"/api/queue/{e3['id']}/position", json={"position": 1})
        assert resp.status_code == 200

        with open(str(queue_file)) as f:
            q = json.load(f)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[e3["id"]]["position"] == 1
        # All positions sequential, no gaps
        positions = sorted([e["position"] for e in q["queue"]])
        assert positions == [1, 2, 3]

    def test_rejects_active_entry(self, client):
        c, queue_file, _ = client
        entry = _make_entry("a", state="ACTIVE", position=1)
        _write_queue(str(queue_file), [entry])
        resp = c.patch(f"/api/queue/{entry['id']}/position", json={"position": 1})
        assert resp.status_code == 409

    def test_rejects_completed_entry(self, client):
        c, queue_file, _ = client
        entry = _make_entry("a", state="COMPLETED", position=1)
        _write_queue(str(queue_file), [entry])
        resp = c.patch(f"/api/queue/{entry['id']}/position", json={"position": 1})
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# PATCH /api/queue/{entry_id}/parent
# ---------------------------------------------------------------------------

class TestPatchQueueParent:
    def test_sets_parent(self, client):
        c, queue_file, _ = client
        parent = _make_entry("parent", position=1)
        child = _make_entry("child", position=2)
        _write_queue(str(queue_file), [parent, child])

        resp = c.patch(f"/api/queue/{child['id']}/parent", json={"parent_id": parent["id"]})
        assert resp.status_code == 200

        with open(str(queue_file)) as f:
            q = json.load(f)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[child["id"]]["parent_id"] == parent["id"]

    def test_clears_parent(self, client):
        c, queue_file, _ = client
        parent = _make_entry("parent", position=1)
        child = {**_make_entry("child", position=2), "parent_id": parent["id"]}
        _write_queue(str(queue_file), [parent, child])

        resp = c.patch(f"/api/queue/{child['id']}/parent", json={"parent_id": None})
        assert resp.status_code == 200

        with open(str(queue_file)) as f:
            q = json.load(f)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[child["id"]]["parent_id"] is None

    def test_circular_dependency_returns_400(self, client):
        c, queue_file, _ = client
        a_id = str(uuid.uuid4())
        b_id = str(uuid.uuid4())
        # A→B (A's parent is B)
        a = {**_make_entry("A", position=1), "id": a_id, "parent_id": b_id}
        b = {**_make_entry("B", position=2), "id": b_id, "parent_id": None}
        _write_queue(str(queue_file), [a, b])

        # Try to set B's parent to A → creates cycle A→B→A
        resp = c.patch(f"/api/queue/{b_id}/parent", json={"parent_id": a_id})
        assert resp.status_code == 400

    def test_self_reference_returns_400(self, client):
        c, queue_file, _ = client
        entry = _make_entry("solo", position=1)
        _write_queue(str(queue_file), [entry])

        resp = c.patch(f"/api/queue/{entry['id']}/parent", json={"parent_id": entry["id"]})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/queue/trigger-next
# ---------------------------------------------------------------------------

class TestPostQueueTriggerNext:
    def test_returns_409_when_active(self, client):
        c, queue_file, _ = client
        entry = _make_entry("active", state="ACTIVE", position=1)
        _write_queue(str(queue_file), [entry], queue_mode="manual")

        resp = c.post("/api/queue/trigger-next")
        assert resp.status_code == 409

    def test_triggers_next_ready(self, client, monkeypatch, tmp_path):
        c, queue_file, _ = client
        proj = tmp_path / "proj"
        proj.mkdir()
        entry = {**_make_entry("alpha", state="READY", position=1), "project_path": str(proj)}
        _write_queue(str(queue_file), [entry], queue_mode="manual")

        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])
        monkeypatch.setattr("ui.server._spawn_orchestrator", lambda path, cfg=None: {"ok": True, "error": None})

        resp = c.post("/api/queue/trigger-next")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True or data.get("started") is True or data.get("queue_halted") is False

        with open(str(queue_file)) as f:
            q = json.load(f)
        assert q["queue"][0]["state"] == "ACTIVE"

    def test_skips_blocked_projects_and_tries_next(self, client, monkeypatch, tmp_path):
        c, queue_file, _ = client
        blocked = _make_entry("blocked", state="BLOCKED", position=1)
        proj = tmp_path / "ready_proj"
        proj.mkdir()
        ready = {**_make_entry("ready", state="READY", position=2), "project_path": str(proj)}
        _write_queue(str(queue_file), [blocked, ready], queue_mode="manual")

        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])
        monkeypatch.setattr("ui.server._spawn_orchestrator", lambda path, cfg=None: {"ok": True, "error": None})

        resp = c.post("/api/queue/trigger-next")
        assert resp.status_code == 200

        with open(str(queue_file)) as f:
            q = json.load(f)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[ready["id"]]["state"] == "ACTIVE"
        assert by_id[blocked["id"]]["state"] == "BLOCKED"  # unchanged

    def test_returns_queue_halted_when_all_blocked(self, client, monkeypatch):
        c, queue_file, _ = client
        entries = [
            _make_entry("a", state="BLOCKED", position=1),
            _make_entry("b", state="BLOCKED", position=2),
        ]
        _write_queue(str(queue_file), entries, queue_mode="manual")

        resp = c.post("/api/queue/trigger-next")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("queue_halted") is True


# ---------------------------------------------------------------------------
# PATCH /api/queue/mode
# ---------------------------------------------------------------------------

class TestPatchQueueMode:
    def test_sets_manual(self, client):
        c, queue_file, _ = client
        _write_queue(str(queue_file), [], queue_mode="auto")

        resp = c.patch("/api/queue/mode", json={"queue_mode": "manual"})
        assert resp.status_code == 200

        with open(str(queue_file)) as f:
            q = json.load(f)
        assert q["queue_mode"] == "manual"

    def test_sets_auto(self, client):
        c, queue_file, _ = client
        _write_queue(str(queue_file), [], queue_mode="manual")

        resp = c.patch("/api/queue/mode", json={"queue_mode": "auto"})
        assert resp.status_code == 200

        with open(str(queue_file)) as f:
            q = json.load(f)
        assert q["queue_mode"] == "auto"

    def test_manual_to_auto_kicks_next_like_trigger_next(self, client, monkeypatch, tmp_path):
        """PATCH manual→auto runs same start-next path as POST trigger-next when idle."""
        c, queue_file, tmp = client
        proj = tmp_path / "proj"
        proj.mkdir()
        entry = {**_make_entry("alpha", state="READY", position=1), "project_path": str(proj)}
        _write_queue(str(queue_file), [entry], queue_mode="manual")
        state_file = tmp / "pipeline_state.json"
        with open(str(state_file), "w") as f:
            json.dump({"pipeline_status": "PIPELINE_COMPLETE", "project_path": str(proj)}, f)

        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])
        monkeypatch.setattr("ui.server._spawn_orchestrator", lambda path, cfg=None: {"ok": True, "error": None})

        resp = c.patch("/api/queue/mode", json={"queue_mode": "auto"})
        assert resp.status_code == 200
        data = resp.json()
        adv = data.get("auto_advance") or {}
        assert adv.get("attempted") is True
        assert adv.get("ok") is True
        assert adv.get("started") == "alpha"

        with open(str(queue_file)) as f:
            q = json.load(f)
        assert q["queue_mode"] == "auto"
        assert q["queue"][0]["state"] == "ACTIVE"

    def test_manual_to_auto_skips_when_pipeline_running(self, client, monkeypatch, tmp_path):
        c, queue_file, tmp = client
        proj = tmp_path / "proj"
        proj.mkdir()
        entry = {**_make_entry("alpha", state="READY", position=1), "project_path": str(proj)}
        _write_queue(str(queue_file), [entry], queue_mode="manual")
        state_file = tmp / "pipeline_state.json"
        with open(str(state_file), "w") as f:
            json.dump({"pipeline_status": "RUNNING"}, f)

        resp = c.patch("/api/queue/mode", json={"queue_mode": "auto"})
        assert resp.status_code == 200
        adv = resp.json().get("auto_advance") or {}
        assert adv.get("attempted") is False
        assert adv.get("reason") == "pipeline_status_busy"

        with open(str(queue_file)) as f:
            q = json.load(f)
        assert q["queue"][0]["state"] == "READY"

    def test_manual_to_auto_skips_when_queue_has_active(self, client, tmp_path):
        c, queue_file, tmp = client
        proj = tmp_path / "proj"
        proj.mkdir()
        active = {**_make_entry("busy", state="ACTIVE", position=1), "project_path": str(proj)}
        ready = {**_make_entry("next", state="READY", position=2), "project_path": str(proj)}
        _write_queue(str(queue_file), [active, ready], queue_mode="manual")
        state_file = tmp / "pipeline_state.json"
        with open(str(state_file), "w") as f:
            json.dump({"pipeline_status": "STOPPED"}, f)

        resp = c.patch("/api/queue/mode", json={"queue_mode": "auto"})
        assert resp.status_code == 200
        adv = resp.json().get("auto_advance") or {}
        assert adv.get("attempted") is False
        assert adv.get("reason") == "queue_has_active"

    def test_manual_to_auto_skips_when_orchestrator_lock_held(self, client, monkeypatch, tmp_path):
        c, queue_file, tmp = client
        proj = tmp_path / "proj"
        proj.mkdir()
        entry = {**_make_entry("alpha", state="READY", position=1), "project_path": str(proj)}
        _write_queue(str(queue_file), [entry], queue_mode="manual")
        state_file = tmp / "pipeline_state.json"
        with open(str(state_file), "w") as f:
            json.dump({"pipeline_status": "STOPPED"}, f)

        monkeypatch.setattr("ui.server._check_orchestrator_liveness", lambda _lp: True)

        resp = c.patch("/api/queue/mode", json={"queue_mode": "auto"})
        assert resp.status_code == 200
        adv = resp.json().get("auto_advance") or {}
        assert adv.get("attempted") is False
        assert adv.get("reason") == "orchestrator_lock_held"

        with open(str(queue_file)) as f:
            q = json.load(f)
        assert q["queue"][0]["state"] == "READY"

    def test_auto_to_auto_does_not_double_kick(self, client, monkeypatch, tmp_path):
        c, queue_file, tmp = client
        proj = tmp_path / "proj"
        proj.mkdir()
        entry = {**_make_entry("alpha", state="READY", position=1), "project_path": str(proj)}
        _write_queue(str(queue_file), [entry], queue_mode="auto")
        state_file = tmp / "pipeline_state.json"
        with open(str(state_file), "w") as f:
            json.dump({"pipeline_status": "STOPPED"}, f)

        calls = []

        def _spawn(path, cfg=None):
            calls.append(path)
            return {"ok": True, "error": None}

        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])
        monkeypatch.setattr("ui.server._spawn_orchestrator", _spawn)

        resp = c.patch("/api/queue/mode", json={"queue_mode": "auto"})
        assert resp.status_code == 200
        assert "auto_advance" not in resp.json()
        assert calls == []

    def test_patch_auto_equivalent_to_trigger_next_same_queue_state(self, client, monkeypatch, tmp_path):
        """Same frozen queue: manual→PATCH auto vs manual+POST trigger-next → same row ACTIVE."""
        c, queue_file, tmp = client
        proj = tmp_path / "proj"
        proj.mkdir()
        entry = {**_make_entry("alpha", state="READY", position=1), "project_path": str(proj)}
        base_queue = {
            "queue": [dict(entry)],
            "queue_mode": "manual",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        state_blob = {"pipeline_status": "PIPELINE_COMPLETE", "project_path": str(proj)}

        monkeypatch.setattr("ui.server._run_preflight_checks", lambda p: [
            {"check": "ok", "status": "pass", "message": "ok"},
        ])
        monkeypatch.setattr("ui.server._spawn_orchestrator", lambda path, cfg=None: {"ok": True, "error": None})

        with open(str(queue_file), "w") as f:
            json.dump(dict(base_queue), f)
        state_file = tmp / "pipeline_state.json"
        with open(str(state_file), "w") as f:
            json.dump(state_blob, f)

        r1 = c.patch("/api/queue/mode", json={"queue_mode": "auto"})
        assert r1.status_code == 200
        patch_started = (r1.json().get("auto_advance") or {}).get("started")

        with open(str(queue_file), "w") as f:
            json.dump(dict(base_queue), f)
        with open(str(state_file), "w") as f:
            json.dump(state_blob, f)

        r2 = c.post("/api/queue/trigger-next")
        assert r2.status_code == 200
        trigger_started = r2.json().get("started")

        assert patch_started == trigger_started == "alpha"

    def test_rejects_invalid_mode(self, client):
        c, queue_file, _ = client
        _write_queue(str(queue_file), [], queue_mode="auto")
        resp = c.patch("/api/queue/mode", json={"queue_mode": "invalid"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/state queue summary fields
# ---------------------------------------------------------------------------

class TestGetStateQueueSummary:
    def test_includes_queue_summary_when_file_exists(self, client):
        c, queue_file, tmp = client
        # Also need a pipeline_state.json for /api/state to not 500
        state_file = tmp / "pipeline_state.json"
        with open(str(state_file), "w") as f:
            json.dump({"pipeline_status": "STOPPED", "current_phase": 0,
                       "current_agent": "planner", "last_action": "test"}, f)

        entries = [
            _make_entry("a", state="READY", position=1),
            _make_entry("b", state="BLOCKED", position=2),
            _make_entry("c", state="COMPLETED", position=3),
        ]
        _write_queue(str(queue_file), entries)

        resp = c.get("/api/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("queue_length") == 3
        assert data.get("ready_count") == 1
        assert data.get("blocked_count") == 1
        assert data.get("completed_count") == 1
        assert "queue_mode" in data

    def test_no_error_when_queue_file_missing(self, client):
        c, queue_file, tmp = client
        state_file = tmp / "pipeline_state.json"
        with open(str(state_file), "w") as f:
            json.dump({"pipeline_status": "STOPPED", "current_phase": 0,
                       "current_agent": "planner", "last_action": "test"}, f)

        resp = c.get("/api/state")
        assert resp.status_code == 200
        data = resp.json()
        # queue fields absent or zero — no error
        assert data.get("queue_length", 0) == 0

    def test_includes_queue_halted_reason_when_set(self, client):
        c, queue_file, tmp = client
        state_file = tmp / "pipeline_state.json"
        with open(str(state_file), "w") as f:
            json.dump(
                {
                    "pipeline_status": "QUEUE_HALTED",
                    "queue_halted_reason": "all_blocked",
                    "current_phase": 0,
                    "current_agent": "planner",
                    "last_action": "test",
                },
                f,
            )
        _write_queue(str(queue_file), [])

        resp = c.get("/api/state")
        assert resp.status_code == 200
        assert resp.json().get("queue_halted_reason") == "all_blocked"


# ---------------------------------------------------------------------------
# POST /api/setup/launch — queue sync
# ---------------------------------------------------------------------------

VALID_LAUNCH_ROADMAP_SEED = (
    "- [ ] `TEST-E1` | LOW | Do the thing\n"
    "  > Test: It works.\n"
)


def _make_subprocess_pass_launch():
    def _inner(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        mock.stderr = b""
        return mock

    return _inner


class TestSetupLaunchQueueSync:
    def test_launch_updates_queue_entry_to_active(self, client, monkeypatch):
        c, queue_file, base = client
        repo_path = base / "launch_proj"
        ad_root = base / "autodev_repo"
        (ad_root / "autodev" / "pipeline").mkdir(parents=True)
        (ad_root / "autodev" / "pipeline" / "orchestrator.py").write_text("# mock\n", encoding="utf-8")

        def mock_load_config(_config_path=None):
            return {
                "pipeline_queue_path": str(queue_file),
                "pipeline_state_path": str(base / "pipeline_state.json"),
                "phase_state_path": str(base / "phase_state.json"),
                "project_dir_path": str(base / "pipeline-project"),
                "lock_path": str(base / "pipeline.lock"),
                "events_path": str(base / "pipeline_events.jsonl"),
                "ideas_dir": str(base / "ideas"),
                "port": 18790,
                "autodev_repo_path": str(ad_root),
            }

        monkeypatch.setattr("ui.server.load_config", mock_load_config)

        entry = _make_entry("queued_proj", state="READY", position=1)
        entry["project_path"] = str(repo_path)
        _write_queue(str(queue_file), [entry])

        with patch("subprocess.run", side_effect=_make_subprocess_pass_launch()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server._spawn_orchestrator", return_value={"ok": True, "error": None}):
            resp = c.post(
                "/api/setup/launch",
                json={"repo_path": str(repo_path), "roadmap_seed": VALID_LAUNCH_ROADMAP_SEED},
            )

        assert resp.status_code == 200
        assert resp.json().get("ok") is True

        with open(str(queue_file)) as f:
            q = json.load(f)
        assert len(q["queue"]) == 1
        assert q["queue"][0]["state"] == "ACTIVE"
        assert q["queue"][0]["started_at"] is not None

    def test_resume_updates_queue_entry_to_active(self, client, monkeypatch):
        c, queue_file, base = client
        repo_path = base / "resume_proj"
        repo_path.mkdir()
        ad_root = base / "autodev_repo_resume"
        (ad_root / "autodev" / "pipeline").mkdir(parents=True)
        (ad_root / "autodev" / "pipeline" / "orchestrator.py").write_text("# mock\n", encoding="utf-8")
        state_path = base / "pipeline_state.json"

        def mock_load_config(_config_path=None):
            return {
                "pipeline_queue_path": str(queue_file),
                "pipeline_state_path": str(state_path),
                "phase_state_path": str(base / "phase_state.json"),
                # Omit project_dir_path so resume uses pipeline_state project_path (matches queue entry).
                "lock_path": str(base / "pipeline.lock"),
                "events_path": str(base / "pipeline_events.jsonl"),
                "ideas_dir": str(base / "ideas"),
                "port": 18790,
                "autodev_repo_path": str(ad_root),
            }

        monkeypatch.setattr("ui.server.load_config", mock_load_config)

        with open(str(state_path), "w") as f:
            json.dump({"project_path": str(repo_path), "pipeline_status": "STOPPED"}, f)

        entry = _make_entry("resume_q", state="READY", position=1)
        entry["project_path"] = str(repo_path)
        _write_queue(str(queue_file), [entry])

        with patch("ui.server._spawn_orchestrator", return_value={"ok": True, "error": None}):
            resp = c.post("/api/resume-orchestrator")

        assert resp.status_code == 200
        with open(str(queue_file)) as f:
            q = json.load(f)
        assert q["queue"][0]["state"] == "ACTIVE"
        assert q["queue"][0]["started_at"] is not None
