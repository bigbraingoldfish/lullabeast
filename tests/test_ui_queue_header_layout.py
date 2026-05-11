"""Static contract tests: Project Queue header — no aggregate summary; trailing progression cluster."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def _index_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_queue_header_aggregate_summary_removed():
    html = _index_text()
    assert "on hold (blocked parent)" not in html
    assert "{readyCt} ready" not in html


def test_queue_header_count_helpers_removed():
    html = _index_text()
    for name in ("readyCt", "blockedCt", "holdCt", "completedCt", "dependentSoftCt"):
        assert name not in html, f"unexpected leftover queue count helper: {name}"


def test_queue_header_trailing_wraps_progression_and_run_next():
    html = _index_text()
    assert 'data-testid="queue-header-trailing"' in html
    idx = html.index('data-testid="queue-header-trailing"')
    window = html[idx : idx + 2500]
    assert "queue-progression-label" in window
    assert "queue-trigger-next" in window


def test_queue_header_title_before_trailing_cluster():
    html = _index_text()
    assert html.index("Project Queue</h1>") < html.index('data-testid="queue-header-trailing"')
