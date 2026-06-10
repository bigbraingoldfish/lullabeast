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


def test_queue_header_progression_leads_run_next_trails():
    """Round 3c: the Progression (Auto/Manual) toggle anchors the top-LEFT of the
    top bar; the conditional Run-next / Resume-banked cluster stays right
    (trailing). Run-next must NOT move into the leading cluster."""
    html = _index_text()
    assert 'data-testid="queue-header-leading"' in html
    assert 'data-testid="queue-header-trailing"' in html
    lead = html.index('data-testid="queue-header-leading"')
    trail = html.index('data-testid="queue-header-trailing"')
    assert lead < trail, "leading (progression) cluster renders before the trailing cluster"
    lead_window = html[lead:trail]
    assert "queue-progression-label" in lead_window, "Progression toggle lives top-left"
    assert "queue-trigger-next" not in lead_window, "Run next project stays out of the leading cluster"
    trail_window = html[trail : trail + 2500]
    assert "queue-trigger-next" in trail_window, "Run next project stays in the trailing cluster"


def test_queue_title_in_content_header_with_actions():
    """Queue redesign round 2: the title + Add Project + Reorder live in a content
    header above the filter chips; the slim top bar keeps only the trailing
    Progression / Run-next / Resume cluster (so in source order the trailing
    cluster comes FIRST, then the content header, then the chips)."""
    html = _index_text()
    trailing = html.index('data-testid="queue-header-trailing"')
    title = html.index("Project Queue</h1>")
    assert trailing < title, "top bar (trailing cluster) renders before the content-header title"
    window = html[title : title + 3600]
    assert "+ Add Project" in window, "Add Project belongs to the content header"
    assert "beginReorder" in window, "Reorder belongs to the content header"
    chips = html.index("{/* Filter chips */}")
    assert title < chips, "the content header sits above the filter chips"
