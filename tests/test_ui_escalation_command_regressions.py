"""Regression guards for escalation command UX wiring in ui/index.html."""

from pathlib import Path

import pytest


INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text(encoding="utf-8")


def test_escalation_panel_stays_mounted_during_confirm_modal(html_content):
    """A 3s state poll should not unmount the panel while confirm modal is open."""
    assert "const keepPanelMounted = canIssueCommands || showConfirmModal || commandSent;" in html_content
    assert "if (!keepPanelMounted) return null;" in html_content


def test_escalation_panel_supports_queue_halted_escalation_commands(html_content):
    """Queue-halted escalation context should still offer command buttons."""
    assert "const queueHaltedEscalation =" in html_content
    assert "const canIssueCommands = humanWait || queueHaltedEscalation;" in html_content


def test_queue_escalation_command_posts_target_project_path(html_content):
    """Queue Action Hub must route parked ESCALATION commands to target_project_path."""
    assert "if (selected && selected.state === 'ESCALATION' && selected.project_path)" in html_content
    assert "payload.target_project_path = selected.project_path;" in html_content


def test_queue_escalation_message_effect_is_not_tied_to_queue_identity(html_content):
    """Avoid refetch loops caused by useEffect dependency on queue array identity."""
    assert "const selectedLiveForEscalationMsg = selectedId" in html_content
    assert "selectedLiveForEscalationMsg," in html_content
