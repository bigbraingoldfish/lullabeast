"""Static contract tests for L-25: queue mode toggle group label (Progression)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def _index_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_queue_progression_label_has_testid():
    html = _index_text()
    assert 'data-testid="queue-progression-label"' in html


def test_progression_label_adjacent_to_mode_toggle():
    html = _index_text()
    marker = 'data-testid="queue-progression-label"'
    pos = html.index(marker)
    window = html[pos : pos + 1200]
    assert "Progression" in window
    assert "handleModeSelect" in window
    assert "['auto', 'manual'].map" in window
