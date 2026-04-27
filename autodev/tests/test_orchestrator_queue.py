"""Tests for orchestrator queue logic (TDD — tests written before implementation)."""
import json
import os
import sys
import uuid
from pathlib import Path
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

    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    # Reload module to pick up patched OPENCLAW_ROOT (runtime files under tmp_path)
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
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))

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

    def test_child_stays_ready_when_parent_active_not_hold(self, orch, tmp_path, monkeypatch):
        inst, queue_file, state_file, _ = orch
        parent_id = str(uuid.uuid4())
        proj = tmp_path / "goodproj"
        proj.mkdir()
        parent = {**_make_entry("parent", state="ACTIVE", position=1, project_path=str(proj)), "id": parent_id}
        child = _make_entry("child", state="READY", position=2, parent_id=parent_id)
        _write_queue(queue_file, [parent, child])

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)
        monkeypatch.setattr(inst, "write_state", lambda: None)

        inst._select_next_queue_project()
        q = inst._read_queue()
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[child["id"]]["state"] == "READY"

    def test_dependency_hold_when_parent_escalation(self, orch, tmp_path, monkeypatch):
        inst, queue_file, state_file, _ = orch
        parent_id = str(uuid.uuid4())
        parent = {**_make_entry("parent", state="ESCALATION", position=1), "id": parent_id}
        child = _make_entry("child", state="READY", position=2, parent_id=parent_id)
        _write_queue(queue_file, [parent, child])

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)
        monkeypatch.setattr(inst, "write_state", lambda: None)

        inst._select_next_queue_project()
        q = inst._read_queue()
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[child["id"]]["state"] == "DEPENDENCY_HOLD"

    def test_promote_children_when_active_entry_completed(self, orch, tmp_path, monkeypatch):
        import orchestrator as orch_mod

        inst, queue_file, state_file, base = orch
        proj_parent = tmp_path / "p1"
        proj_parent.mkdir()
        (proj_parent / ".git").mkdir()
        (proj_parent / "roadmap.md").write_text("# x")
        proj_child = tmp_path / "p2"
        proj_child.mkdir()
        (proj_child / ".git").mkdir()
        (proj_child / "roadmap.md").write_text("# x")

        link = Path(orch_mod.SYMLINK_TARGET)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(proj_parent, target_is_directory=True)

        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        parent = {
            **_make_entry("p", state="ACTIVE", position=1, project_path=str(proj_parent)),
            "id": parent_id,
        }
        child = {
            **_make_entry("c", state="DEPENDENCY_HOLD", position=2, parent_id=parent_id, project_path=str(proj_child)),
            "id": child_id,
        }
        _write_queue(queue_file, [parent, child])
        inst.state["project_path"] = str(proj_parent)

        inst._queue_update_active_entry(
            "COMPLETED",
            {"completed_at": datetime.now(timezone.utc).isoformat()},
        )
        q = inst._read_queue()
        by_id = {e["id"]: e for e in q["queue"]}
        assert by_id[child_id]["state"] == "READY"

    def test_startup_pipeline_complete_auto_retries_startup(self, orch, tmp_path, monkeypatch):
        import orchestrator as orch_mod

        inst, queue_file, state_file, _ = orch
        nselect = {"n": 0}

        def sel(*args, **kwargs):
            nselect["n"] += 1
            return True

        monkeypatch.setattr(inst, "_select_next_queue_project", sel)
        monkeypatch.setattr(inst, "_queue_update_active_entry", lambda *a, **k: None)
        monkeypatch.setattr(inst, "transition_state", lambda *a, **k: None)
        monkeypatch.setattr(inst, "write_state", lambda: None)
        monkeypatch.setattr(
            inst,
            "_read_queue",
            lambda: {"queue": [{"id": "1"}], "queue_mode": "auto"},
        )

        def fake_run(cmd, **kwargs):
            m = MagicMock()
            m.stderr = ""
            exe = cmd[0] if cmd else ""
            if exe == sys.executable and cmd and "phase_resolver.py" in str(cmd[-1]):
                m.returncode = 0
                m.stdout = "PIPELINE_COMPLETE"
                return m
            m.returncode = 0
            m.stdout = ""
            return m

        monkeypatch.setattr(orch_mod.subprocess, "run", fake_run)
        inst.state["current_agent"] = "planner"
        inst.state["current_phase"] = 0
        rv = inst._run_startup_planner_phase_zero_and_branch()
        assert rv == "retry_startup"
        assert nselect["n"] == 1

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

    def test_select_next_halt_if_no_eligible_false_skips_queue_halted(self, orch, tmp_path, monkeypatch):
        """After PIPELINE_COMPLETE, no next project is normal — do not flip to QUEUE_HALTED."""
        inst, queue_file, state_file, _ = orch
        entries = [_make_entry("a", state="COMPLETED", position=1)]
        _write_queue(queue_file, entries)

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)
        monkeypatch.setattr(inst, "write_state", lambda: None)
        inst.state["pipeline_status"] = "PIPELINE_COMPLETE"

        result = inst._select_next_queue_project(halt_if_no_eligible=False)
        assert result is False
        assert inst.state.get("pipeline_status") == "PIPELINE_COMPLETE"
        assert "queue_halted_reason" not in inst.state

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

    def test_queue_halted_reason_all_blocked_includes_escalation(self, orch, tmp_path, monkeypatch):
        """Parked ESCALATION rows count as all_blocked per TASK-03."""
        inst, queue_file, state_file, _ = orch
        entries = [
            {**_make_entry("a", state="ESCALATION", position=1)},
            {**_make_entry("b", state="BLOCKED", position=2)},
        ]
        _write_queue(queue_file, entries)

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)
        monkeypatch.setattr(inst, "write_state", lambda: None)

        inst._select_next_queue_project()
        assert inst.state["queue_halted_reason"] == "all_blocked"

    def test_apply_pending_escalation_sets_waiting_for_human(self, orch, tmp_path, monkeypatch):
        inst, queue_file, state_file, _ = orch
        proj = tmp_path / "goodproj"
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / "roadmap.md").write_text("# x")
        art = proj / ".autodev" / "pipeline"
        art.mkdir(parents=True)
        pending = art / "pending_escalation_command.json"
        pending.write_text(json.dumps({"command": "RETRY"}))

        entry = {**_make_entry("goodproj", state="READY", position=1), "project_path": str(proj)}
        _write_queue(queue_file, [entry])

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)
        ws_calls = []

        def capture_write():
            ws_calls.append(dict(inst.state))

        monkeypatch.setattr(inst, "write_state", capture_write)

        assert inst._select_next_queue_project() is True
        assert not pending.exists()
        esc_done = art / "escalation_output.done"
        assert esc_done.exists()
        assert inst.state.get("pipeline_status") == "WAITING_FOR_HUMAN"
        assert inst.state.get("current_agent") == "escalation"

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

    def test_should_invoke_escalation_agent_treats_queue_halted_as_waiting(self, orch):
        """Escalation loop must not re-invoke webhook while queue-halted waiting for human."""
        inst, _queue_file, _state_file, _ = orch

        inst.state["pipeline_status"] = "WAITING_FOR_HUMAN"
        assert inst._should_invoke_escalation_agent() is False

        inst.state["pipeline_status"] = "QUEUE_HALTED"
        assert inst._should_invoke_escalation_agent() is False

        inst.state["pipeline_status"] = "RUNNING"
        assert inst._should_invoke_escalation_agent() is True


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


# ---------------------------------------------------------------------------
# apply_cli_project_path (--project-path vs stale state / pre-set symlink)
# ---------------------------------------------------------------------------


class TestApplyCliProjectPath:
    def test_resets_when_disk_state_project_differs_even_if_symlink_matches(
        self, tmp_path, monkeypatch
    ):
        """Preflight may point pipeline-project at B while state still references A."""
        import importlib

        monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
        monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))
        import orchestrator as orch_mod

        importlib.reload(orch_mod)
        from orchestrator import Orchestrator as FreshOrch, apply_cli_project_path as apply_pp

        proj_a = tmp_path / "proj_a"
        proj_b = tmp_path / "proj_b"
        proj_a.mkdir()
        proj_b.mkdir()
        (tmp_path / "openclaw.json").write_text('{"hooks_url": "http://localhost:18789/hooks/agent", "hooks_token": "test-token"}')
        for _role in ("planner", "executor", "reviewer"):
            (tmp_path / f"workspace-{_role}").mkdir()

        state_path = Path(orch_mod.STATE_FILE)
        link = Path(orch_mod.SYMLINK_TARGET)
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(proj_b, target_is_directory=True)

        state_path.write_text(
            json.dumps(
                {
                    "current_phase": 1,
                    "current_phase_raw_id": "OLD-E1",
                    "current_agent": "executor",
                    "pipeline_status": "PIPELINE_COMPLETE",
                    "project_path": str(proj_a),
                    "last_action": "prior project done",
                }
            )
        )

        orch = FreshOrch()
        apply_pp(orch, str(proj_b))

        assert orch.state["pipeline_status"] == "RUNNING"
        assert orch.state["current_agent"] == "planner"
        assert orch.state["current_phase"] == 0
        assert orch.state["current_phase_raw_id"] == ""
        assert os.path.realpath(orch.state["project_path"]) == os.path.realpath(str(proj_b))

        loaded = json.loads(state_path.read_text())
        assert loaded["pipeline_status"] == "RUNNING"
        assert os.path.realpath(loaded["project_path"]) == os.path.realpath(str(proj_b))


# ---------------------------------------------------------------------------
# Main loop: stale PIPELINE_COMPLETE vs queue ACTIVE
# ---------------------------------------------------------------------------


class TestMainLoopStaleCompleteSyncsQueue:
    def test_pipeline_complete_at_loop_entrance_marks_active_completed(
        self, tmp_path, monkeypatch
    ):
        """When global state is already COMPLETE but the queue row is ACTIVE (UI spawn),
        startup may skip the phase_resolver PIPELINE_COMPLETE branch; the main loop
        must still sync the queue row before exiting — only if phase_resolver agrees.
        """
        import importlib

        monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
        monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))
        import orchestrator as orch_mod

        importlib.reload(orch_mod)

        queue_file = tmp_path / "pipeline_queue.json"
        state_file = tmp_path / "pipeline_state.json"
        (tmp_path / "openclaw.json").write_text('{"hooks_url": "http://localhost:18789/hooks/agent", "hooks_token": "test-token"}')
        for _role in ("planner", "executor", "reviewer"):
            (tmp_path / f"workspace-{_role}").mkdir(exist_ok=True)

        proj = tmp_path / "active_proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / "roadmap.md").write_text("# r\n- [ ] `X-E1` | LOW | t\n")

        link = Path(orch_mod.SYMLINK_TARGET)
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(proj, target_is_directory=True)

        eid = str(uuid.uuid4())
        _write_queue(
            str(queue_file),
            [
                {
                    **_make_entry(
                        "active",
                        state="ACTIVE",
                        position=1,
                        project_path=str(proj),
                        entry_id=eid,
                    )
                }
            ],
        )

        state_file.write_text(
            json.dumps(
                {
                    "current_phase": 0,
                    "current_phase_raw_id": "X-E1",
                    "current_agent": "planner",
                    "pipeline_status": "PIPELINE_COMPLETE",
                    "project_path": str(proj),
                    "last_action": "prior complete",
                }
            )
        )

        monkeypatch.setattr(orch_mod, "SkillManager", lambda _ad: MagicMock())

        inst = orch_mod.Orchestrator()

        monkeypatch.setattr(inst, "acquire_lock", lambda: setattr(inst, "lock_fd", None))
        monkeypatch.setattr(inst, "release_lock", lambda: None)
        monkeypatch.setattr(orch_mod, "cleanup_stranded_temp_files", lambda _root: None)
        monkeypatch.setattr(inst, "run_repo_init_check", lambda: (True, ""))
        monkeypatch.setattr(
            inst,
            "_run_startup_planner_phase_zero_and_branch",
            lambda: "enter_main_loop",
        )
        monkeypatch.setattr(inst, "_phase_resolver_indicates_pipeline_complete", lambda: True)

        inst.run()


# ---------------------------------------------------------------------------
# T1: _queue_restore_parked_entry_to_active
# ---------------------------------------------------------------------------


class TestQueueRestoreParkedEntryToActive:
    """T1: After _queue_park_active_entry sets row to ESCALATION or BLOCKED,
    _queue_restore_parked_entry_to_active must find the row (by project_path match
    across those states) and reset it to ACTIVE, clearing park metadata."""

    def test_restores_escalation_row_to_active(self, orch, tmp_path, monkeypatch):
        """Row parked as ESCALATION is found and restored to ACTIVE with park fields cleared."""
        inst, queue_file, _, _ = orch
        proj = tmp_path / "esc_proj"
        proj.mkdir()

        entry = {
            **_make_entry("esc_proj"),
            "state": "ESCALATION",
            "project_path": str(proj),
            "parked_at": datetime.now(timezone.utc).isoformat(),
            "parked_reason": "ERR_VALIDATION_FAILED",
            "parked_pipeline_status": "WAITING_FOR_HUMAN",
        }
        _write_queue(queue_file, [entry])

        import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
        inst.state["project_path"] = str(proj)

        inst._queue_restore_parked_entry_to_active()

        q = inst._read_queue()
        row = q["queue"][0]
        assert row["state"] == "ACTIVE"
        assert row.get("parked_at") is None
        assert row.get("parked_reason") is None
        assert row.get("parked_pipeline_status") is None

    def test_restores_blocked_row_to_active(self, orch, tmp_path, monkeypatch):
        """Row parked as BLOCKED is also found and restored to ACTIVE."""
        inst, queue_file, _, _ = orch
        proj = tmp_path / "blocked_proj"
        proj.mkdir()

        entry = {
            **_make_entry("blocked_proj"),
            "state": "BLOCKED",
            "project_path": str(proj),
            "parked_at": datetime.now(timezone.utc).isoformat(),
            "parked_reason": "ERR_ROADMAP_BLOCKED",
            "parked_pipeline_status": "BLOCKED",
        }
        _write_queue(queue_file, [entry])

        import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
        inst.state["project_path"] = str(proj)

        inst._queue_restore_parked_entry_to_active()

        q = inst._read_queue()
        row = q["queue"][0]
        assert row["state"] == "ACTIVE"
        assert row.get("parked_at") is None

    def test_completed_after_restore_updates_queue(self, orch, tmp_path, monkeypatch):
        """After restore, _queue_update_active_entry('COMPLETED') finds the row and updates it."""
        inst, queue_file, _, _ = orch
        proj = tmp_path / "comp_proj"
        proj.mkdir()

        entry = {
            **_make_entry("comp_proj"),
            "state": "ESCALATION",
            "project_path": str(proj),
            "parked_at": datetime.now(timezone.utc).isoformat(),
            "parked_reason": "test",
            "parked_pipeline_status": "WAITING_FOR_HUMAN",
        }
        _write_queue(queue_file, [entry])

        import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
        inst.state["project_path"] = str(proj)

        # Restore then update — simulates the RETRY command path
        inst._queue_restore_parked_entry_to_active()
        ts = datetime.now(timezone.utc).isoformat()
        inst._queue_update_active_entry("COMPLETED", {"completed_at": ts})

        q = inst._read_queue()
        row = q["queue"][0]
        assert row["state"] == "COMPLETED"
        assert row["completed_at"] == ts

    def test_noop_when_no_matching_entry(self, orch, tmp_path, monkeypatch):
        """Restore is a no-op when no ESCALATION/BLOCKED row matches the project path."""
        inst, queue_file, _, _ = orch
        proj = tmp_path / "other_proj"
        proj.mkdir()

        # Entry for a different project
        entry = {
            **_make_entry("other"),
            "state": "ESCALATION",
            "project_path": "/some/completely/different/path",
        }
        _write_queue(queue_file, [entry])

        import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
        inst.state["project_path"] = str(proj)

        inst._queue_restore_parked_entry_to_active()  # must not raise

        q = inst._read_queue()
        assert q["queue"][0]["state"] == "ESCALATION"  # unchanged

    def test_stale_complete_with_pending_phases_does_not_mark_queue_completed(
        self, tmp_path, monkeypatch
    ):
        """phase_resolver must confirm completion before ACTIVE→COMPLETED sync."""
        import importlib

        monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
        monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))
        import orchestrator as orch_mod

        importlib.reload(orch_mod)

        queue_file = tmp_path / "pipeline_queue.json"
        state_file = tmp_path / "pipeline_state.json"
        (tmp_path / "openclaw.json").write_text('{"hooks_url": "http://localhost:18789/hooks/agent", "hooks_token": "test-token"}')
        for _role in ("planner", "executor", "reviewer"):
            (tmp_path / f"workspace-{_role}").mkdir(exist_ok=True)

        proj = tmp_path / "active_proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / "roadmap.md").write_text("# r\n- [ ] `X-E1` | LOW | t\n")

        link = Path(orch_mod.SYMLINK_TARGET)
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(proj, target_is_directory=True)

        eid = str(uuid.uuid4())
        _write_queue(
            str(queue_file),
            [
                {
                    **_make_entry(
                        "active",
                        state="ACTIVE",
                        position=1,
                        project_path=str(proj),
                        entry_id=eid,
                    )
                }
            ],
        )

        state_file.write_text(
            json.dumps(
                {
                    "current_phase": 0,
                    "current_phase_raw_id": "X-E1",
                    "current_agent": "planner",
                    "pipeline_status": "PIPELINE_COMPLETE",
                    "project_path": str(proj),
                    "last_action": "stale complete",
                }
            )
        )

        monkeypatch.setattr(orch_mod, "SkillManager", lambda _ad: MagicMock())

        inst = orch_mod.Orchestrator()

        monkeypatch.setattr(inst, "acquire_lock", lambda: setattr(inst, "lock_fd", None))
        monkeypatch.setattr(inst, "release_lock", lambda: None)
        monkeypatch.setattr(orch_mod, "cleanup_stranded_temp_files", lambda _root: None)
        monkeypatch.setattr(inst, "run_repo_init_check", lambda: (True, ""))
        monkeypatch.setattr(
            inst,
            "_run_startup_planner_phase_zero_and_branch",
            lambda: "enter_main_loop",
        )
        monkeypatch.setattr(inst, "_phase_resolver_indicates_pipeline_complete", lambda: False)
        monkeypatch.setattr(inst, "_check_stop_requested", lambda: True)

        inst.run()

        q = json.loads(queue_file.read_text())
        assert q["queue"][0]["state"] == "ACTIVE"
        assert q["queue"][0].get("completed_at") is None
