"""W4-B: Phase progress secondary text on ACTIVE queue rows.

Static content checks — no server needed.
"""
from pathlib import Path


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def _queue_row_region(html: str) -> str:
    start = html.index("return queue.map(entry =>")
    end = html.index("{/* Column 2: Project snapshot */}", start)
    return html[start:end]


def test_phases_total_guard():
    """Render only when phases_total is defined and > 0."""
    region = _queue_row_region(_html())
    assert "entry.phases_total !== undefined" in region
    assert "entry.phases_total > 0" in region


def test_phase_progress_copy():
    """Shows 'N/M phases' format."""
    region = _queue_row_region(_html())
    assert "phases_complete" in region
    assert "phases_total" in region
    assert "phases" in region


def test_phase_progress_active_only():
    """Phase progress is gated on entry.state === 'ACTIVE'."""
    region = _queue_row_region(_html())
    # The phases_total check must co-occur with an ACTIVE state guard
    phases_idx = region.index("entry.phases_total !== undefined")
    surrounding = region[max(0, phases_idx - 100):phases_idx + 200]
    assert "ACTIVE" in surrounding
