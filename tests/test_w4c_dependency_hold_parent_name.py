"""W4-C: DEPENDENCY_HOLD parent name in QueueProjectSnapshot.

Static content checks — no server needed.
"""
from pathlib import Path


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def _snapshot_body(html: str) -> str:
    start = html.index("function QueueProjectSnapshot(")
    end = html.index("// ── Column 3:", start)
    return html[start:end]


def test_waiting_for_copy_present():
    """Snapshot shows 'Waiting for:' when in DEPENDENCY_HOLD."""
    snapshot = _snapshot_body(_html())
    assert "Waiting for:" in snapshot


def test_dependency_hold_guard():
    """Render gated on selected.state === 'DEPENDENCY_HOLD'."""
    snapshot = _snapshot_body(_html())
    assert "selected.state === 'DEPENDENCY_HOLD'" in snapshot


def test_parent_name_rendered():
    """Parent entry's name is shown."""
    snapshot = _snapshot_body(_html())
    assert "parentEntry.name" in snapshot


def test_parent_status_fallback():
    """live_pipeline_status falls back to parentEntry.state."""
    snapshot = _snapshot_body(_html())
    assert "parentEntry.live_pipeline_status || parentEntry.state" in snapshot


def test_graceful_missing_parent():
    """Gracefully handles case where parentEntry is null/undefined."""
    snapshot = _snapshot_body(_html())
    # The code should check parentEntry truthiness before accessing .name
    assert "parentEntry ?" in snapshot or "parentEntry &&" in snapshot
