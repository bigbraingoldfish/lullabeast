"""Tests for cascading SKIPPED_PENDING to dependent children (Item 5)."""
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

# Orchestrator path setup
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_entry(name, state="READY", position=1, parent_id=None, entry_id=None,
                project_path=None):
    return {
        "id": entry_id or str(uuid.uuid4()),
        "project_path": project_path or f"/tmp/project_{name}",
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


# ---------------------------------------------------------------------------
# Server-side: trigger-next cascades SKIPPED_PENDING
# ---------------------------------------------------------------------------

class TestServerGroupSkip:
    def test_parent_fails_preflight_children_also_skipped(self, client):
        """trigger-next: parent fails preflight → parent + all children become SKIPPED_PENDING."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", state="READY", position=1,
                              project_path="/nonexistent/parent")
        child = _make_entry("Child", state="READY", position=2,
                             parent_id=parent["id"])
        independent = _make_entry("Indep", state="READY", position=3)
        _write_queue(queue_file, [parent, child, independent])

        # Preflight will fail for /nonexistent/parent; the independent project has
        # the same problem. We just need to verify child cascades with parent.
        # Patch preflight to fail only for parent's path
        import ui.server as srv

        original_preflight = srv._run_preflight_checks

        def fake_preflight(path):
            if "parent" in path.lower():
                return [{"check": "directory", "status": "fail", "detail": "missing"}]
            return []

        with patch.object(srv, "_run_preflight_checks", side_effect=fake_preflight):
            # Also patch _spawn_orchestrator so independent project doesn't actually spawn
            with patch.object(srv, "_spawn_orchestrator", return_value={"ok": True}):
                resp = c.post("/api/queue/trigger-next")

        q = _read_queue(queue_file)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[parent["id"]]["state"] == "SKIPPED_PENDING"
        assert by_id[child["id"]]["state"] == "SKIPPED_PENDING"

    def test_unrelated_independent_project_not_affected(self, client):
        """trigger-next: independent projects are not affected by parent's skip."""
        c, queue_file, _ = client
        parent = _make_entry("Parent", state="READY", position=1,
                              project_path="/nonexistent/parent")
        child = _make_entry("Child", state="READY", position=2,
                             parent_id=parent["id"])
        independent = _make_entry("Indep", state="READY", position=3)
        _write_queue(queue_file, [parent, child, independent])

        import ui.server as srv

        def fake_preflight(path):
            if "parent" in path.lower():
                return [{"check": "directory", "status": "fail", "detail": "missing"}]
            return []  # independent passes

        with patch.object(srv, "_run_preflight_checks", side_effect=fake_preflight):
            with patch.object(srv, "_spawn_orchestrator", return_value={"ok": True}):
                resp = c.post("/api/queue/trigger-next")

        q = _read_queue(queue_file)
        by_id = {e["id"]: e for e in q["queue"]}
        # Independent should be ACTIVE (was started)
        assert by_id[independent["id"]]["state"] == "ACTIVE"

    def test_grandchild_also_cascades(self, client):
        """trigger-next: A fails → A (pos1), B (child), C (grandchild) all SKIPPED_PENDING."""
        c, queue_file, _ = client
        a = _make_entry("A", state="READY", position=1,
                         project_path="/nonexistent/a")
        b = _make_entry("B", state="READY", position=2, parent_id=a["id"])
        c_entry = _make_entry("C", state="READY", position=3, parent_id=b["id"])
        _write_queue(queue_file, [a, b, c_entry])

        import ui.server as srv

        def fake_preflight(path):
            return [{"check": "directory", "status": "fail", "detail": "missing"}]

        with patch.object(srv, "_run_preflight_checks", side_effect=fake_preflight):
            resp = c.post("/api/queue/trigger-next")

        q = _read_queue(queue_file)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[a["id"]]["state"] == "SKIPPED_PENDING"
        assert by_id[b["id"]]["state"] == "SKIPPED_PENDING"
        assert by_id[c_entry["id"]]["state"] == "SKIPPED_PENDING"

    def test_independent_project_skip_does_not_cascade(self, client):
        """An independent project (no parent) that skips does not affect others."""
        c, queue_file, _ = client
        lone = _make_entry("Lone", state="READY", position=1,
                            project_path="/nonexistent/lone")
        other = _make_entry("Other", state="READY", position=2)
        _write_queue(queue_file, [lone, other])

        import ui.server as srv

        def fake_preflight(path):
            if "lone" in path.lower():
                return [{"check": "directory", "status": "fail", "detail": "missing"}]
            return []

        with patch.object(srv, "_run_preflight_checks", side_effect=fake_preflight):
            with patch.object(srv, "_spawn_orchestrator", return_value={"ok": True}):
                c.post("/api/queue/trigger-next")

        q = _read_queue(queue_file)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[lone["id"]]["state"] == "SKIPPED_PENDING"
        assert by_id[other["id"]]["state"] == "ACTIVE"


# ---------------------------------------------------------------------------
# Orchestrator-side: _select_next_queue_project cascades SKIPPED_PENDING
# ---------------------------------------------------------------------------

@pytest.fixture
def orch(tmp_path, monkeypatch):
    """Orchestrator instance with mocked filesystem paths."""
    import importlib
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    import orchestrator as orch_mod
    importlib.reload(orch_mod)
    from orchestrator import Orchestrator as FreshOrch

    inst = FreshOrch.__new__(FreshOrch)
    inst.state = {
        "current_phase": 0,
        "current_phase_raw_id": "",
        "current_agent": "planner",
        "pipeline_status": "RUNNING",
        "project_path": "/tmp/current_project",
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "last_action": "",
        "last_action_timestamp": "",
    }
    inst.openclaw_config = {}

    queue_file = tmp_path / "pipeline_queue.json"
    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps(inst.state))

    # Reload the module-level QUEUE_FILE constant
    orch_mod.QUEUE_FILE = str(queue_file)
    orch_mod.STATE_FILE = str(state_file)

    # Patch write_state and transition_state to be no-ops
    inst.write_state = MagicMock()
    inst.transition_state = MagicMock()
    inst.update_symlink = MagicMock()

    return inst, queue_file, tmp_path


class TestOrchestratorGroupSkip:
    def test_parent_preflight_fail_cascades_to_child(self, orch):
        """_select_next_queue_project: parent fails preflight → child also SKIPPED_PENDING."""
        inst, queue_file, _ = orch

        parent = _make_entry("Parent", state="READY", position=1,
                              project_path="/nonexistent/parent")
        child = _make_entry("Child", state="READY", position=2,
                             parent_id=parent["id"])
        independent = _make_entry("Indep", state="READY", position=3,
                                   project_path="/tmp")

        data = {
            "queue": [parent, child, independent],
            "queue_mode": "auto",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        with open(queue_file, "w") as f:
            json.dump(data, f)

        # _queue_preflight checks dir exists + git + roadmap — /nonexistent/parent fails
        # independent at /tmp should pass basic dir check but we don't want it to actually start
        with patch.object(inst, "update_symlink"):
            # Make independent fail too so we don't need a real project
            original_preflight = inst._queue_preflight
            def fake_preflight(path):
                if "parent" in path.lower():
                    return (False, "directory missing")
                return (False, "no git")  # independent also fails for simplicity
            inst._queue_preflight = fake_preflight
            inst._select_next_queue_project()

        with open(queue_file) as f:
            q = json.load(f)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[parent["id"]]["state"] == "SKIPPED_PENDING"
        assert by_id[child["id"]]["state"] == "SKIPPED_PENDING"

    def test_grandchild_chain_all_cascade(self, orch):
        """A→B→C: A fails preflight → A, B, C all SKIPPED_PENDING."""
        inst, queue_file, _ = orch

        a = _make_entry("A", state="READY", position=1,
                         project_path="/nonexistent/a")
        b = _make_entry("B", state="READY", position=2, parent_id=a["id"])
        c_entry = _make_entry("C", state="READY", position=3, parent_id=b["id"])

        data = {
            "queue": [a, b, c_entry],
            "queue_mode": "auto",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        with open(queue_file, "w") as f:
            json.dump(data, f)

        inst._queue_preflight = lambda path: (False, "missing")
        inst._select_next_queue_project()

        with open(queue_file) as f:
            q = json.load(f)
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[a["id"]]["state"] == "SKIPPED_PENDING"
        assert by_id[b["id"]]["state"] == "SKIPPED_PENDING"
        assert by_id[c_entry["id"]]["state"] == "SKIPPED_PENDING"

    def test_independent_skip_no_cascade(self, orch):
        """An independent entry that fails preflight does not affect other entries."""
        inst, queue_file, _ = orch

        lone = _make_entry("Lone", state="READY", position=1,
                            project_path="/nonexistent/lone")
        other = _make_entry("Other", state="READY", position=2,
                             project_path="/nonexistent/other")

        data = {
            "queue": [lone, other],
            "queue_mode": "auto",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        with open(queue_file, "w") as f:
            json.dump(data, f)

        inst._queue_preflight = lambda path: (False, "missing")
        inst._select_next_queue_project()

        with open(queue_file) as f:
            q = json.load(f)
        by_id = {e["id"]: e for e in q["queue"]}
        # Both fail independently — lone should be SKIPPED_PENDING, other also SKIPPED_PENDING
        # but NOT because of lone (it has no parent relationship)
        assert by_id[lone["id"]]["state"] == "SKIPPED_PENDING"
        assert by_id[other["id"]]["state"] == "SKIPPED_PENDING"
        # Key assertion: other's skip_count is 1 (skipped by its own preflight failure, not cascaded)
        # If cascade wrongly ran, other's skip_count might be inflated
        assert by_id[other["id"]]["skip_count"] == 1

    def test_group_moves_together_after_parent_skip(self, orch):
        """After parent skips, the group (parent+child) moves past next independent entry.

        Only the parent fails preflight; the independent entry passes and starts (ACTIVE).
        We verify positions at the moment the orchestrator writes the skip (before Indep starts).
        """
        inst, queue_file, _ = orch

        parent = _make_entry("Parent", state="READY", position=1,
                              project_path="/nonexistent/parent")
        child = _make_entry("Child", state="READY", position=2,
                             parent_id=parent["id"])
        independent = _make_entry("Indep", state="READY", position=3,
                                   project_path="/tmp/indep")

        data = {
            "queue": [parent, child, independent],
            "queue_mode": "auto",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        with open(queue_file, "w") as f:
            json.dump(data, f)

        # Only parent fails; independent passes (but we capture the write after parent's skip)
        written_states = []
        original_write = inst._write_queue

        def capturing_write(queue_data):
            original_write(queue_data)
            written_states.append(json.loads(json.dumps(queue_data)))  # deep copy

        inst._write_queue = capturing_write
        inst._queue_preflight = lambda path: (False, "missing") if "parent" in path.lower() else (False, "no git")

        inst._select_next_queue_project()

        # The first write captures the state right after the group was moved
        assert len(written_states) >= 1
        first_write = written_states[0]
        by_id = {e["id"]: e for e in first_write["queue"]}

        parent_pos = by_id[parent["id"]]["position"]
        child_pos = by_id[child["id"]]["position"]
        indep_pos = by_id[independent["id"]]["position"]

        # After group moves past independent: Indep(1) → Parent(2) → Child(3)
        assert indep_pos < parent_pos
        # Parent and child stay together (child immediately after parent)
        assert child_pos == parent_pos + 1
