"""Defensive Hardening Phase 6 — Group 1: queue-file parsing + CAS exhaustion.

TDD (tests written before implementation):
  * T6.7 — a malformed / wrong-shape ``pipeline_queue.json`` (or a single bad row) must
    NOT crash the whole selection walk for every project in a restart-resistant loop.
  * T6.6 — a ``QueueVersionConflict`` raised inside the selection path must degrade to
    "couldn't commit this cycle — retry next cycle" (return False), not propagate to the
    top-level ``run()`` handler and escalate.

These exercise ``_read_queue``, ``_select_next_queue_project`` and
``_promote_answered_escalations`` hermetically (``Orchestrator.__new__`` + monkeypatched
module path constants), mirroring ``test_orchestrator_queue.py``.
"""
import importlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_entry(name, state="READY", position=1, parent_id=None, entry_id=None, project_path=None):
    return {
        "id": entry_id or str(uuid.uuid4()),
        "project_path": project_path or f"/tmp/proj_{name}",
        "name": name,
        "state": state,
        "position": position,
        "parent_id": parent_id,
        "skip_count": 0,
    }


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))
    import orchestrator as orch_mod
    importlib.reload(orch_mod)

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {
        "current_phase": 0,
        "current_phase_raw_id": "",
        "current_agent": "planner",
        "pipeline_status": "RUNNING",
        "project_path": "/tmp/current_project",
    }
    inst.lock_fd = None
    inst._current_attempt_retry_class = "initial_attempt"

    queue_file = tmp_path / "pipeline_queue.json"
    state_file = tmp_path / "pipeline_state.json"
    monkeypatch.setattr(orch_mod, "QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(state_file))
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))
    # Module-level side-effect helpers we don't want firing in unit context.
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())
    monkeypatch.setattr(orch_mod, "_write_run_manifest", MagicMock())
    return inst, orch_mod, queue_file, tmp_path


def _write_raw(path, obj):
    with open(str(path), "w") as f:
        json.dump(obj, f)


# ---------------------------------------------------------------------------
# T6.7 — _read_queue shape validation
# ---------------------------------------------------------------------------

class TestReadQueueShapeGuard:
    @pytest.mark.parametrize("bad", [{"queue_mode": "auto"}, [], {}, "garbage", 42])
    def test_wrong_shape_quarantines_and_raises(self, orch, bad):
        inst, _mod, queue_file, tmp = orch
        _write_raw(queue_file, bad)

        with pytest.raises(RuntimeError):
            inst._read_queue()

        # Original is quarantined (renamed away), so the next read self-heals.
        assert not queue_file.exists(), "wrong-shape file should be renamed to *.corrupt.*"
        corrupt = list(tmp.glob("pipeline_queue.json.corrupt.*"))
        assert corrupt, "expected a quarantined copy"

    def test_next_read_after_quarantine_returns_empty(self, orch):
        inst, _mod, queue_file, _tmp = orch
        _write_raw(queue_file, {"queue_mode": "auto"})  # no "queue" list
        with pytest.raises(RuntimeError):
            inst._read_queue()
        # File renamed away → absent → empty structure (no crash loop).
        result = inst._read_queue()
        assert result["queue"] == []

    def test_valid_shape_passes_through(self, orch):
        inst, _mod, queue_file, _tmp = orch
        _write_raw(queue_file, {"queue": [_make_entry("a")], "queue_mode": "manual"})
        result = inst._read_queue()
        assert len(result["queue"]) == 1
        assert result["queue_mode"] == "manual"


# ---------------------------------------------------------------------------
# T6.7 — selection walk tolerates malformed rows
# ---------------------------------------------------------------------------

def _arm_start(inst, orch_mod, monkeypatch):
    """Mock the side effects of actually starting a row so the walk can reach activation
    without touching the real FS/git."""
    monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, ""))
    monkeypatch.setattr(inst, "update_symlink", MagicMock(return_value=True))
    monkeypatch.setattr(inst, "_apply_pending_escalation_command", lambda p: None)
    monkeypatch.setattr(inst, "write_state", MagicMock())
    monkeypatch.setattr(inst, "_mutate_queue", MagicMock(return_value=True))


class TestSelectionToleratesMalformedRows:
    def test_skips_rows_missing_id_or_state_and_starts_valid(self, orch, monkeypatch):
        inst, orch_mod, queue_file, _tmp = orch
        good = _make_entry("good", state="READY", position=2, project_path="/tmp/good")
        no_state = {"id": "x1", "position": 1, "project_path": "/tmp/x1"}      # missing state
        no_id = {"state": "READY", "position": 3, "project_path": "/tmp/x2"}   # missing id
        _write_raw(queue_file, {"queue": [no_state, good, no_id], "queue_mode": "auto"})
        _arm_start(inst, orch_mod, monkeypatch)

        # Must NOT raise KeyError on the sort / state_by_id comprehension.
        started = inst._select_next_queue_project(halt_if_no_eligible=False)
        assert started is True
        inst.update_symlink.assert_called_once()
        assert inst.update_symlink.call_args[0][0].endswith("/good")

    def test_sort_tolerates_missing_position(self, orch, monkeypatch):
        inst, orch_mod, queue_file, _tmp = orch
        no_pos = _make_entry("nopos", state="READY", project_path="/tmp/nopos")
        del no_pos["position"]
        _write_raw(queue_file, {"queue": [no_pos], "queue_mode": "auto"})
        _arm_start(inst, orch_mod, monkeypatch)

        started = inst._select_next_queue_project(halt_if_no_eligible=False)
        assert started is True  # did not crash on the position sort key


# ---------------------------------------------------------------------------
# T6.6 — CAS exhaustion in the selection path degrades to retry-next-cycle
# ---------------------------------------------------------------------------

class TestSelectionCasExhaustion:
    def test_select_returns_false_on_queue_version_conflict(self, orch, monkeypatch):
        inst, orch_mod, queue_file, _tmp = orch
        _write_raw(queue_file, {"queue": [_make_entry("a", state="READY")], "queue_mode": "auto"})
        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, ""))
        monkeypatch.setattr(inst, "update_symlink", MagicMock(return_value=True))

        def _boom(*a, **k):
            raise orch_mod.QueueVersionConflict("exhausted")
        monkeypatch.setattr(inst, "_mutate_queue", _boom)

        # Must be caught and degraded to False, NOT propagate to the run() escalation handler.
        assert inst._select_next_queue_project(halt_if_no_eligible=False) is False

    def test_promote_answered_returns_false_on_conflict(self, orch, monkeypatch, tmp_path):
        inst, orch_mod, _queue_file, _tmp = orch
        proj = tmp_path / "escproj"
        (proj / ".autodev" / "pipeline").mkdir(parents=True)
        (proj / ".autodev" / "pipeline" / "pending_escalation_command.json").write_text("{}")
        entry = _make_entry("esc", state="ESCALATION", project_path=str(proj))
        queue_data = {"queue": [entry], "queue_mode": "auto"}

        def _boom(*a, **k):
            raise orch_mod.QueueVersionConflict("exhausted")
        monkeypatch.setattr(inst, "_mutate_queue", _boom)

        # The banked answer makes the row eligible → reaches the CAS → must NOT propagate.
        assert inst._promote_answered_escalations(queue_data) is False
