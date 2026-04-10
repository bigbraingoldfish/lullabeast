"""Static contract tests for Pipeline Monitor orchestrator-down panel (ui/index.html)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def _index_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_orchestrator_down_panel_has_testid_and_restart_label():
    html = _index_text()
    assert 'data-testid="pipeline-orchestrator-down-panel"' in html
    marker = 'data-testid="pipeline-orchestrator-down-panel"'
    pos = html.index(marker)
    window = html[pos : pos + 3500]
    assert "Restart Orchestrator" in window


def test_header_liveness_dot_removed():
    html = _index_text()
    assert 'alive ? "bg-green-500" : "bg-red-600"' not in html
    assert "w-2.5 h-2.5 rounded-full flex-shrink-0" not in html


def test_restart_not_between_resume_and_stop_in_header_row():
    """Restart Orchestrator control must not sit in the header button cluster."""
    html = _index_text()
    start = html.find("{showResumeButton &&")
    assert start != -1
    end = html.find("{showStopButton && !stopRequested", start)
    assert end != -1
    header_action_slice = html[start:end]
    assert "showRestartOrchestratorButton" not in header_action_slice
