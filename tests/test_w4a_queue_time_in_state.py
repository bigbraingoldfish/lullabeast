"""W4-A: Time-in-state secondary text on queue rows.

Static content checks — no server needed.
"""
from pathlib import Path


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def _queue_row_region(html: str) -> str:
    """Extract the queue row rendering block (the map callback)."""
    start = html.index("return queue.map(entry =>")
    end = html.index("{/* Column 2: Project snapshot */}", start)
    return html[start:end]


def test_blocked_time_secondary_text():
    """BLOCKED rows show 'Blocked {relTime(blocked_at)}' secondary text."""
    region = _queue_row_region(_html())
    assert "entry.state === 'BLOCKED'" in region
    assert "entry.blocked_at" in region
    assert "Blocked " in region


def test_active_time_secondary_text():
    """ACTIVE rows show 'Running {relTime(started_at)}' secondary text."""
    region = _queue_row_region(_html())
    assert "entry.state === 'ACTIVE'" in region
    assert "entry.started_at" in region
    assert "Running " in region


def test_name_wrapped_in_div():
    """entry.name is wrapped in a div to allow secondary text below it."""
    region = _queue_row_region(_html())
    assert "flex-1 min-w-0" in region


def test_reltime_used_for_time_in_state():
    """relTime() helper is used to format the timestamps."""
    region = _queue_row_region(_html())
    assert "relTime(entry.blocked_at)" in region
    assert "relTime(entry.started_at)" in region
