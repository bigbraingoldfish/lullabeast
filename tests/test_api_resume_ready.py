"""F11 — POST /api/resume-ready accepts STOPPED *and* HALTED_SILENT.

Before F11, the only clean recovery from HALTED_SILENT was the phase-destroying
git-recover (or hand-editing state). F11 generalises ``post_resume_ready``
(previously STOPPED-only) to also accept HALTED_SILENT, transitioning the
pipeline to WAITING_FOR_HUMAN + current_agent="escalation" so the operator can
issue a recovery command (RETRY / RESET_* / PROCEED / …) from the dashboard.
``git-recover`` remains the heavy fallback; ``post_stop`` is intentionally NOT
changed (resume, not stop, is the designed recovery from a silent halt).
"""

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def load_client():
    from ui.server import app
    return TestClient(app)


def _config(tmp_path: Path) -> dict:
    return {"pipeline_state_path": str(tmp_path / "pipeline_state.json")}


def _write_state(tmp_path: Path, status: str) -> Path:
    p = tmp_path / "pipeline_state.json"
    p.write_text(json.dumps({
        "pipeline_status": status,
        "current_agent": "executor",
        "current_phase": 3,
        "current_phase_raw_id": "CORE-3",
    }))
    return p


def test_resume_ready_accepts_halted_silent(tmp_path):
    """F11: HALTED_SILENT must be resumable → 200, file rewritten to
    WAITING_FOR_HUMAN + current_agent="escalation", other fields preserved so
    the operator's recovery command lands on the same phase context."""
    state_path = _write_state(tmp_path, "HALTED_SILENT")
    client = load_client()
    with patch("ui.server.load_config", return_value=_config(tmp_path)):
        resp = client.post("/api/resume-ready")
    assert resp.status_code == 200, resp.text
    after = json.loads(state_path.read_text())
    assert after["pipeline_status"] == "WAITING_FOR_HUMAN"
    assert after["current_agent"] == "escalation"
    assert after["current_phase"] == 3  # preserved
    assert after["current_phase_raw_id"] == "CORE-3"  # preserved


def test_resume_ready_still_accepts_stopped(tmp_path):
    """Regression guard: the original STOPPED → WAITING_FOR_HUMAN+escalation path
    must keep working (widening the precondition must not drop STOPPED)."""
    state_path = _write_state(tmp_path, "STOPPED")
    client = load_client()
    with patch("ui.server.load_config", return_value=_config(tmp_path)):
        resp = client.post("/api/resume-ready")
    assert resp.status_code == 200, resp.text
    after = json.loads(state_path.read_text())
    assert after["pipeline_status"] == "WAITING_FOR_HUMAN"
    assert after["current_agent"] == "escalation"


def test_resume_ready_rejects_running(tmp_path):
    """A non-resumable status (RUNNING) must 409, and the detail must name BOTH
    resumable states so the operator knows what's allowed."""
    _write_state(tmp_path, "RUNNING")
    client = load_client()
    with patch("ui.server.load_config", return_value=_config(tmp_path)):
        resp = client.post("/api/resume-ready")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "STOPPED" in detail and "HALTED_SILENT" in detail, (
        f"409 detail must name both resumable states; got: {detail!r}"
    )


def test_resume_ready_docstring_mentions_halted_silent():
    """The endpoint docstring must document the widened precondition so the
    contract is discoverable from the source."""
    from ui.server import post_resume_ready
    assert "HALTED_SILENT" in (post_resume_ready.__doc__ or ""), (
        "post_resume_ready docstring must mention HALTED_SILENT"
    )
