"""C6-05: When orchestrator sets an entry to SKIPPED_PENDING due to preflight failure
(missing/unreadable path), it must also write a 'skip_reason' onto the entry dict
so operators can see WHY the item was skipped.

Without the fix, the entry silently moves to SKIPPED_PENDING with no reason recorded.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    """Minimal Orchestrator with all filesystem paths in tmp_path."""
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    import orchestrator as orch_mod
    importlib.reload(orch_mod)
    from orchestrator import Orchestrator as FreshOrch

    inst = FreshOrch.__new__(FreshOrch)
    inst.state = {
        "current_phase": 0,
        "current_phase_raw_id": "",
        "current_agent": "planner",
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "last_action": "test",
        "last_action_timestamp": "2026-01-01T00:00:00Z",
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path / "proj"),
        "status": "RUNNING",
    }
    inst.openclaw_config = {}
    inst.skill_manager = MagicMock()
    inst.logger = MagicMock()

    import orchestrator as fresh_mod
    inst._read_queue = lambda: fresh_mod.Orchestrator._read_queue(inst)
    inst._write_queue = lambda data: fresh_mod.Orchestrator._write_queue(inst, data)
    inst._queue_preflight = lambda path: fresh_mod.Orchestrator._queue_preflight(inst, path)
    inst._get_all_descendants = lambda entries, eid: fresh_mod.Orchestrator._get_all_descendants(inst, entries, eid)
    inst._move_group_atomically = lambda entries, eid, pos: fresh_mod.Orchestrator._move_group_atomically(inst, entries, eid, pos)
    inst._select_next_queue_project = lambda halt=True: fresh_mod.Orchestrator._select_next_queue_project(inst, halt_if_no_eligible=halt)
    return inst, tmp_path, fresh_mod


def _write_queue(tmp_path, entries):
    queue_file = tmp_path / "pipeline_queue.json"
    queue_file.write_text(json.dumps({
        "queue": entries,
        "queue_mode": "auto",
        "last_updated": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")


def test_skipped_pending_entry_has_skip_reason_on_missing_path(orch):
    """When _select_next_queue_project skips an entry because its path fails preflight,
    the entry dict must include a 'skip_reason' field."""
    inst, tmp_path, mod = orch

    # A project path that does NOT exist → preflight will fail
    missing_path = str(tmp_path / "does-not-exist")
    _write_queue(tmp_path, [
        {
            "id": "entry-1",
            "name": "missing-project",
            "project_path": missing_path,
            "state": "READY",
            "position": 0,
            "skip_count": 0,
        },
    ])

    # Run select — preflight will fail → entry moves to SKIPPED_PENDING
    inst._select_next_queue_project(halt=False)

    # Read queue back and check for skip_reason
    q = inst._read_queue()
    entry = q["queue"][0]
    assert entry["state"] == "SKIPPED_PENDING", f"Expected SKIPPED_PENDING, got {entry['state']}"
    assert "skip_reason" in entry, (
        f"'skip_reason' not found in SKIPPED_PENDING entry — operator cannot see why it was skipped "
        f"(C6-05 unfixed). Entry: {entry}"
    )
    assert entry["skip_reason"], "skip_reason must be a non-empty string"


def test_skip_reason_not_set_when_entry_completes_normally(orch, monkeypatch):
    """Sanity: when an entry is not skipped (e.g. DEPENDENCY_HOLD), skip_reason is not injected."""
    inst, tmp_path, mod = orch

    missing_path = str(tmp_path / "does-not-exist")
    _write_queue(tmp_path, [
        {
            "id": "child-1",
            "name": "child-project",
            "project_path": missing_path,
            "state": "READY",
            "position": 0,
            "parent_id": "parent-99",  # parent not in queue → treated as no parent since state_by_id won't find it
            "skip_count": 0,
        },
    ])

    # With a parent_id that resolves to COMPLETED (simulate by having the parent absent)
    # The entry has parent_id="parent-99" but parent-99 is not in the queue.
    # state_by_id.get("parent-99") → None, which is != "COMPLETED", so with parent_blocks_child(None)...
    # Let's check: this test just verifies skip_reason is not injected on un-skipped entries.
    # Since parent is absent → entry skipped via dependency, not via preflight.
    # We just need to ensure the skip_reason field is not falsely injected.
    inst._select_next_queue_project(halt=False)

    q = inst._read_queue()
    for entry in q["queue"]:
        if entry.get("state") == "DEPENDENCY_HOLD":
            assert "skip_reason" not in entry or entry.get("skip_reason") is None or entry.get("skip_reason") == "", \
                "skip_reason should not be set for DEPENDENCY_HOLD entries (only for preflight skips)"
