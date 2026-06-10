"""W4-C: "Waiting for parent" rendering in QueueProjectSnapshot.

Phase 5 (R1) generalized this surface from the literal ``DEPENDENCY_HOLD`` state to ANY
incomplete parent (``parent.state !== 'COMPLETED'``), mirroring the orchestrator's
selection-skip rule. The snapshot "Waiting for:" line is now gated on the shared
``isWaitingForParent(parentEntry)`` helper, NOT on ``selected.state === 'DEPENDENCY_HOLD'``.

Static content checks — no server needed.
"""
from pathlib import Path


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def _snapshot_body(html: str) -> str:
    start = html.index("function QueueProjectSnapshot(")
    end = html.index("// ── Row expansion: Action Hub", start)
    return html[start:end]


def test_waiting_for_copy_present():
    """Snapshot still shows the 'Waiting for:' copy."""
    snapshot = _snapshot_body(_html())
    assert "Waiting for:" in snapshot


def test_waiting_for_gated_on_incomplete_parent():
    """The 'Waiting for:' line is gated on the shared ``isWaitingForParent(parentEntry)``
    helper (any incomplete parent), NOT on the literal ``DEPENDENCY_HOLD`` state.

    Catches a regression to the old state-only gate, which hid the wait whenever the parent
    was READY/ACTIVE (the R1 bug — the child stays READY but is still skipped at selection).
    """
    snapshot = _snapshot_body(_html())
    assert "isWaitingForParent(parentEntry)" in snapshot
    assert "selected.state === 'DEPENDENCY_HOLD'" not in snapshot


def test_parent_name_rendered():
    """Parent entry's name is shown."""
    snapshot = _snapshot_body(_html())
    assert "parentEntry.name" in snapshot


def test_parent_status_fallback():
    """live_pipeline_status falls back to parentEntry.state."""
    snapshot = _snapshot_body(_html())
    assert "parentEntry.live_pipeline_status || parentEntry.state" in snapshot


def test_no_dead_missing_parent_fallback():
    """The helper gate guarantees a truthy parent inside the block, so the old
    '(parent not found)' dead fallback must be removed (no dead code per house standard)."""
    snapshot = _snapshot_body(_html())
    assert "(parent not found)" not in snapshot
