"""Tests for orchestrator queue logic (TDD — tests written before implementation)."""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

# Path setup (matches conftest.py pattern)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from orchestrator import Orchestrator


def _make_entry(name, state="READY", position=1, parent_id=None, entry_id=None, project_path=None):
    return {
        "id": entry_id or str(uuid.uuid4()),
        "project_path": project_path or f"/tmp/proj_{name}",
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


@pytest.fixture
def orch(tmp_path, monkeypatch):
    """Orchestrator instance with mocked filesystem paths."""
    queue_file = tmp_path / "pipeline_queue.json"
    state_file = tmp_path / "pipeline_state.json"

    monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))

    # Reload module to pick up patched AUTODEV_ROOT
    import importlib
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
    }
    inst.lock_fd = None

    # Patch QUEUE_FILE and STATE_FILE in the module
    monkeypatch.setattr(orch_mod, "QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(state_file))
    monkeypatch.setattr(orch_mod, "AUTODEV_ROOT", str(tmp_path))

    return inst, queue_file, state_file, tmp_path


def _write_queue(path, entries, queue_mode="auto"):
    data = {
        "queue": entries,
        "queue_mode": queue_mode,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    with open(str(path), "w") as f:
        json.dump(data, f)
    return data


# ---------------------------------------------------------------------------
# QUEUE_HALTED in VALID_STATES
# ---------------------------------------------------------------------------

class TestQueueHaltedInValidStates:
    def test_queue_halted_is_valid_state(self):
        import orchestrator as orch_mod
        assert "QUEUE_HALTED" in orch_mod.VALID_STATES


# ---------------------------------------------------------------------------
# _read_queue / _write_queue
# ---------------------------------------------------------------------------

class TestReadWriteQueue:
    def test_read_returns_empty_when_file_absent(self, orch):
        inst, queue_file, _, _ = orch
        result = inst._read_queue()
        assert result["queue"] == []
        assert result["queue_mode"] == "auto"

    def test_write_is_atomic(self, orch, monkeypatch):
        """_write_queue uses mkstemp+os.replace, not direct write."""
        inst, queue_file, _, tmp = orch
        replace_calls = []
        original_replace = os.replace

        def tracking_replace(src, dst):
            replace_calls.append((src, dst))
            return original_replace(src, dst)

        monkeypatch.setattr("os.replace", tracking_replace)
        data = {"queue": [], "queue_mode": "auto", "last_updated": ""}
        inst._write_queue(data)
        assert len(replace_calls) == 1
        assert replace_calls[0][1] == str(queue_file)

    def test_write_then_read_roundtrip(self, orch):
        inst, queue_file, _, _ = orch
        entries = [_make_entry("test")]
        data = {"queue": entries, "queue_mode": "manual", "last_updated": ""}
        inst._write_queue(data)

        result = inst._read_queue()
        assert len(result["queue"]) == 1
        assert result["queue"][0]["name"] == "test"
        assert result["queue_mode"] == "manual"


# ---------------------------------------------------------------------------
# _queue_preflight
# ---------------------------------------------------------------------------

class TestQueuePreflight:
    def test_passes_valid_project(self, orch, tmp_path):
        inst, _, _, _ = orch
        proj = tmp_path / "valid_proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / "roadmap.md").write_text("# Roadmap")

        ok, reason = inst._queue_preflight(str(proj))
        assert ok is True

    def test_fails_missing_directory(self, orch):
        inst, _, _, _ = orch
        ok, reason = inst._queue_preflight("/nonexistent/path/xyz")
        assert ok is False
        assert "directory" in reason.lower() or "exist" in reason.lower()

    def test_fails_missing_git(self, orch, tmp_path):
        inst, _, _, _ = orch
        proj = tmp_path / "no_git"
        proj.mkdir()
        (proj / "roadmap.md").write_text("# Roadmap")
        ok, reason = inst._queue_preflight(str(proj))
        assert ok is False

    def test_fails_missing_roadmap(self, orch, tmp_path):
        inst, _, _, _ = orch
        proj = tmp_path / "no_roadmap"
        proj.mkdir()
        (proj / ".git").mkdir()
        ok, reason = inst._queue_preflight(str(proj))
        assert ok is False


# ---------------------------------------------------------------------------
# _find_active_queue_entry
# ---------------------------------------------------------------------------

class TestFindActiveQueueEntry:
    def test_finds_by_symlink(self, orch, tmp_path, monkeypatch):
        inst, queue_file, _, _ = orch
        proj = tmp_path / "myproject"
        proj.mkdir()

        entry = {**_make_entry("myproject"), "state": "ACTIVE", "project_path": str(proj)}
        queue_data = {"queue": [entry], "queue_mode": "auto", "last_updated": ""}

        # Mock SYMLINK_TARGET to point to proj
        import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))

        idx, found = inst._find_active_queue_entry(queue_data)
        assert idx == 0
        assert found["name"] == "myproject"

    def test_falls_back_to_state_project_path(self, orch, tmp_path, monkeypatch):
        inst, queue_file, _, _ = orch
        proj = tmp_path / "fallback_proj"
        proj.mkdir()

        entry = {**_make_entry("fallback_proj"), "state": "ACTIVE", "project_path": str(proj)}
        queue_data = {"queue": [entry], "queue_mode": "auto", "last_updated": ""}

        # Symlink doesn't exist
        import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path / "nonexistent_link"))
        inst.state["project_path"] = str(proj)

        idx, found = inst._find_active_queue_entry(queue_data)
        assert idx == 0
        assert found["name"] == "fallback_proj"

    def test_returns_none_none_when_no_match(self, orch, tmp_path, monkeypatch):
        inst, _, _, _ = orch
        entry = {**_make_entry("other"), "state": "ACTIVE", "project_path": "/some/other/path"}
        queue_data = {"queue": [entry], "queue_mode": "auto", "last_updated": ""}

        import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path / "nonexistent_link"))
        inst.state.pop("project_path", None)

        idx, found = inst._find_active_queue_entry(queue_data)
        assert idx is None
        assert found is None


# ---------------------------------------------------------------------------
# _select_next_queue_project
# ---------------------------------------------------------------------------

class TestSelectNextQueueProject:
    def test_selects_first_ready_project(self, orch, tmp_path, monkeypatch):
        inst, queue_file, state_file, _ = orch
        proj = tmp_path / "readyproj"
        proj.mkdir()
        entry = {**_make_entry("readyproj", position=1), "project_path": str(proj)}
        _write_queue(queue_file, [entry])

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)

        result = inst._select_next_queue_project()
        assert result is True

        q = inst._read_queue()
        assert q["queue"][0]["state"] == "ACTIVE"
        assert q["queue"][0]["started_at"] is not None

    def test_skips_blocked_entries(self, orch, tmp_path, monkeypatch):
        inst, queue_file, state_file, _ = orch
        proj = tmp_path / "readyproj"
        proj.mkdir()
        blocked = _make_entry("blocked", state="BLOCKED", position=1)
        ready = {**_make_entry("ready", state="READY", position=2), "project_path": str(proj)}
        _write_queue(queue_file, [blocked, ready])

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)

        result = inst._select_next_queue_project()
        assert result is True

        q = inst._read_queue()
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[ready["id"]]["state"] == "ACTIVE"
        assert by_id[blocked["id"]]["state"] == "BLOCKED"  # unchanged

    def test_skip_and_requeue_moves_to_position_plus_one_not_end(self, orch, tmp_path, monkeypatch):
        """Entry at position 2 that fails preflight should move to position 3, not position 4."""
        inst, queue_file, state_file, _ = orch
        proj_c = tmp_path / "proj_c"
        proj_c.mkdir()

        e1 = _make_entry("a", state="READY", position=1)
        e2 = _make_entry("b", state="READY", position=2)  # will fail preflight
        e3 = _make_entry("c", state="READY", position=3, project_path=str(proj_c))
        e4 = _make_entry("d", state="READY", position=4)
        _write_queue(queue_file, [e1, e2, e3, e4])

        # e1 fails, e2 fails, e3 passes
        def preflight_side_effect(path):
            if "proj_c" in path:
                return True, "ok"
            return False, "fail"

        monkeypatch.setattr(inst, "_queue_preflight", preflight_side_effect)
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)

        result = inst._select_next_queue_project()
        assert result is True

        q = inst._read_queue()
        by_id = {e["id"]: e for e in q["queue"]}
        # e3 should be ACTIVE
        assert by_id[e3["id"]]["state"] == "ACTIVE"
        # e1, e2 should be SKIPPED_PENDING with skip_count=1
        assert by_id[e1["id"]]["state"] == "SKIPPED_PENDING"
        assert by_id[e2["id"]]["state"] == "SKIPPED_PENDING"
        # Positions should be 1..4 sequential (no gaps)
        positions = sorted([e["position"] for e in q["queue"]])
        assert positions == [1, 2, 3, 4]

    def test_skip_count_incremented(self, orch, tmp_path, monkeypatch):
        inst, queue_file, state_file, _ = orch
        proj = tmp_path / "goodproj"
        proj.mkdir()
        e1 = {**_make_entry("bad", state="SKIPPED_PENDING", position=1), "skip_count": 2}
        e2 = {**_make_entry("good", state="READY", position=2), "project_path": str(proj)}
        _write_queue(queue_file, [e1, e2])

        def preflight(path):
            if "goodproj" in path:
                return True, "ok"
            return False, "fail"

        monkeypatch.setattr(inst, "_queue_preflight", preflight)
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)

        inst._select_next_queue_project()
        q = inst._read_queue()
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[e1["id"]]["skip_count"] == 3

    def test_dependency_hold_when_parent_not_completed(self, orch, tmp_path, monkeypatch):
        inst, queue_file, state_file, _ = orch
        parent_id = str(uuid.uuid4())
        parent = {**_make_entry("parent", state="BLOCKED", position=1), "id": parent_id}
        child = _make_entry("child", state="READY", position=2, parent_id=parent_id)
        _write_queue(queue_file, [parent, child])

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)

        result = inst._select_next_queue_project()
        assert result is False  # QUEUE_HALTED

        q = inst._read_queue()
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[child["id"]]["state"] == "DEPENDENCY_HOLD"

    def test_queue_halted_written_when_all_exhausted(self, orch, tmp_path, monkeypatch):
        inst, queue_file, state_file, _ = orch
        entries = [
            _make_entry("a", state="BLOCKED", position=1),
            _make_entry("b", state="BLOCKED", position=2),
        ]
        _write_queue(queue_file, entries)

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)
        monkeypatch.setattr(inst, "write_state", lambda: None)

        result = inst._select_next_queue_project()
        assert result is False
        assert inst.state.get("pipeline_status") == "QUEUE_HALTED"
        assert "queue_halted_reason" in inst.state

    def test_queue_halted_reason_all_blocked(self, orch, tmp_path, monkeypatch):
        inst, queue_file, state_file, _ = orch
        entries = [
            _make_entry("a", state="BLOCKED", position=1),
            _make_entry("b", state="BLOCKED", position=2),
        ]
        _write_queue(queue_file, entries)

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)
        monkeypatch.setattr(inst, "write_state", lambda: None)

        inst._select_next_queue_project()
        assert inst.state["queue_halted_reason"] == "all_blocked"

    def test_visited_ids_prevents_infinite_loop(self, orch, tmp_path, monkeypatch):
        """All entries fail preflight — visited_ids ensures termination."""
        inst, queue_file, state_file, _ = orch
        entries = [_make_entry(f"proj_{i}", state="READY", position=i + 1) for i in range(5)]
        _write_queue(queue_file, entries)

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (False, "always fails"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)
        monkeypatch.setattr(inst, "write_state", lambda: None)

        result = inst._select_next_queue_project()
        assert result is False  # halted, didn't loop forever

    def test_no_entries_returns_false(self, orch, monkeypatch):
        inst, queue_file, state_file, _ = orch
        _write_queue(queue_file, [])

        monkeypatch.setattr(inst, "write_state", lambda: None)
        result = inst._select_next_queue_project()
        assert result is False


# ---------------------------------------------------------------------------
# Queue state transitions (COMPLETED, FAILED, BLOCKED)
# ---------------------------------------------------------------------------

class TestQueueStateTransitions:
    def test_active_entry_marked_completed(self, orch, tmp_path, monkeypatch):
        """_find_active_queue_entry is used to mark ACTIVE→COMPLETED."""
        inst, queue_file, _, _ = orch
        proj = tmp_path / "active_proj"
        proj.mkdir()
        entry = {**_make_entry("active_proj"), "state": "ACTIVE", "project_path": str(proj)}
        _write_queue(queue_file, [entry])

        import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))

        queue_data = inst._read_queue()
        idx, found = inst._find_active_queue_entry(queue_data)
        assert idx is not None
        queue_data["queue"][idx]["state"] = "COMPLETED"
        inst._write_queue(queue_data)

        q = inst._read_queue()
        assert q["queue"][0]["state"] == "COMPLETED"

    def test_active_entry_marked_failed(self, orch, tmp_path, monkeypatch):
        inst, queue_file, _, _ = orch
        proj = tmp_path / "fail_proj"
        proj.mkdir()
        entry = {**_make_entry("fail_proj"), "state": "ACTIVE", "project_path": str(proj)}
        _write_queue(queue_file, [entry])

        import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))

        queue_data = inst._read_queue()
        idx, _ = inst._find_active_queue_entry(queue_data)
        assert idx is not None
        queue_data["queue"][idx]["state"] = "FAILED"
        inst._write_queue(queue_data)

        q = inst._read_queue()
        assert q["queue"][0]["state"] == "FAILED"

    def test_active_entry_marked_blocked(self, orch, tmp_path, monkeypatch):
        inst, queue_file, _, _ = orch
        proj = tmp_path / "block_proj"
        proj.mkdir()
        entry = {**_make_entry("block_proj"), "state": "ACTIVE", "project_path": str(proj)}
        _write_queue(queue_file, [entry])

        import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))

        queue_data = inst._read_queue()
        idx, _ = inst._find_active_queue_entry(queue_data)
        assert idx is not None
        queue_data["queue"][idx]["state"] = "BLOCKED"
        queue_data["queue"][idx]["blocked_at"] = datetime.now(timezone.utc).isoformat()
        inst._write_queue(queue_data)

        q = inst._read_queue()
        assert q["queue"][0]["state"] == "BLOCKED"
        assert q["queue"][0]["blocked_at"] is not None


# ---------------------------------------------------------------------------
# _queue_update_active_entry (HALTED_SILENT → FAILED parity)
# ---------------------------------------------------------------------------


class TestQueueUpdateActiveEntry:
    def test_failed_includes_failed_at(self, orch, tmp_path, monkeypatch):
        inst, queue_file, _, _ = orch
        proj = tmp_path / "active_for_fail"
        proj.mkdir()
        entry = {**_make_entry("active_for_fail"), "state": "ACTIVE", "project_path": str(proj)}
        _write_queue(queue_file, [entry])

        import orchestrator as orch_mod

        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
        ts = datetime.now(timezone.utc).isoformat()
        inst._queue_update_active_entry("FAILED", {"failed_at": ts})

        q = inst._read_queue()
        assert q["queue"][0]["state"] == "FAILED"
        assert q["queue"][0]["failed_at"] == ts
