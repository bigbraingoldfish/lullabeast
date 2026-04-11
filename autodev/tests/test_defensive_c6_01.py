"""C6-01: Corrupt pipeline_queue.json must be quarantined, not silently overwritten."""
import json
import os
import sys
import importlib
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    """Orchestrator instance with all filesystem paths pointing to tmp_path."""
    monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")

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
    return inst, tmp_path, fresh_mod


def test_corrupt_queue_raises_not_returns_empty(orch):
    """When pipeline_queue.json exists but is corrupt JSON, _read_queue must
    raise (not return an empty dict) so that callers cannot silently overwrite
    the queue file with an empty structure."""
    inst, tmp_path, mod = orch
    queue_file = tmp_path / "pipeline_queue.json"
    queue_file.write_text("{ this is not valid json !!!}", encoding="utf-8")

    with pytest.raises(Exception):
        inst._read_queue()


def test_corrupt_queue_file_is_quarantined(orch):
    """When _read_queue encounters corrupt JSON, the original file must be
    renamed to pipeline_queue.json.corrupt.<timestamp> so operator can inspect it."""
    inst, tmp_path, mod = orch
    queue_file = tmp_path / "pipeline_queue.json"
    original_content = "{ this is not valid json !!!}"
    queue_file.write_text(original_content, encoding="utf-8")

    try:
        inst._read_queue()
    except Exception:
        pass

    # Original file should be gone (renamed / quarantined)
    assert not queue_file.exists(), (
        "Corrupt queue file was NOT renamed; it could be overwritten by the next _write_queue call."
    )

    # A quarantine file should exist nearby
    quarantine_files = list(tmp_path.glob("pipeline_queue.json.corrupt.*"))
    assert len(quarantine_files) == 1, (
        f"Expected exactly one .corrupt.* file, found: {quarantine_files}"
    )
    assert quarantine_files[0].read_text() == original_content, (
        "Quarantined file content does not match original corrupt content."
    )


def test_missing_queue_file_still_returns_empty(orch):
    """When queue file does not exist at all, _read_queue returns empty (no exception)."""
    inst, tmp_path, mod = orch
    queue_file = tmp_path / "pipeline_queue.json"
    assert not queue_file.exists()

    result = inst._read_queue()
    assert result == {"queue": [], "queue_mode": "auto", "last_updated": ""}


def test_valid_queue_file_returns_content(orch):
    """Sanity: valid JSON queue returns parsed content."""
    inst, tmp_path, mod = orch
    queue_file = tmp_path / "pipeline_queue.json"
    content = {"queue": [{"id": "abc"}], "queue_mode": "auto", "last_updated": "2026-01-01"}
    queue_file.write_text(json.dumps(content), encoding="utf-8")

    result = inst._read_queue()
    assert result["queue"][0]["id"] == "abc"
