"""Static contract tests for P1-02 + L-26: queue manual Run next project button."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def _index_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_queue_run_next_has_testid():
    html = _index_text()
    assert 'data-testid="queue-trigger-next"' in html


def test_run_next_label_not_trigger_next():
    html = _index_text()
    assert "Run next project" in html
    assert "Trigger Next" not in html


def test_helper_and_title_binding():
    html = _index_text()
    assert "getRunNextProjectDisabledReason" in html
    assert "title={getRunNextProjectDisabledReason()" in html


def test_button_block_includes_run_pulse():
    html = _index_text()
    marker = 'data-testid="queue-trigger-next"'
    pos = html.index(marker)
    window = html[pos : pos + 900]
    assert "run-pulse" in window


def test_tooltip_branch_strings():
    html = _index_text()
    assert "Starting the next project" in html
    assert "A project is already active in the queue." in html
    assert "Save or cancel reorder first." in html
    assert "Pipeline is running." in html
    assert "Pipeline is waiting on an agent." in html
    assert "Pipeline is waiting for your input." in html
