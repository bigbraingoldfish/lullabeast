"""Static contract tests for P0-02: page-level orchestrator recovery banner (ui/index.html)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def _index_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_recovery_banner_has_testid_and_restart():
    html = _index_text()
    assert "showHeaderOrchestratorRecoveryBanner" in html
    assert 'data-testid="pipeline-orchestrator-recovery-banner"' in html
    marker = 'data-testid="pipeline-orchestrator-recovery-banner"'
    pos = html.index(marker)
    window = html[pos : pos + 2800]
    assert "Restart Orchestrator" in window


def test_recovery_banner_not_between_resume_and_stop_in_header_row():
    """Restart must not sit in the header button cluster (same rule as down-panel)."""
    html = _index_text()
    start = html.find("{showResumeButton &&")
    assert start != -1
    end = html.find("{showStopButton && !stopRequested", start)
    assert end != -1
    header_action_slice = html[start:end]
    assert "showHeaderOrchestratorRecoveryBanner" not in header_action_slice


def test_escalation_panel_uses_orchestrator_down_blocks_commands():
    html = _index_text()
    assert "orchestratorDownBlocksCommands" in html
    # Grid renders only when commands are allowed and orchestrator is not blocking
    assert "canIssueCommands && !orchestratorDownBlocksCommands" in html


def test_escalation_restart_first_copy_points_to_banner():
    html = _index_text()
    assert "banner above" in html.lower()
