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


def test_queue_escalation_command_routes_target_project_path(html_content):
    """The Queue still routes parked ESCALATION commands via target_project_path (the
    deferred hold). After G3 consolidation the ESCALATION guard lives at the Queue call
    site (as the targetProjectPath prop) and the shared EscalationCommandPanel dispatcher
    appends target_project_path to the /api/command body."""
    # Queue call site selects the deferred target with the ESCALATION guard preserved.
    assert "selected.state === 'ESCALATION'" in html_content
    assert "selected.project_path" in html_content
    assert "targetProjectPath=" in html_content
    # Shared dispatcher carries it onto the request so the server defers (holds) the command.
    assert "target_project_path" in html_content


def test_queue_escalation_advisory_is_snapshot_sourced_not_api_state(html_content):
    """Migrated from ``…_effect_is_not_tied_to_queue_identity``.

    The original guarded a ``/api/state`` useEffect (+ ``selectedLiveForEscalationMsg``)
    against re-triggering on queue-array identity. That whole effect is gone: the Queue
    advisory is now derived from the per-entry snapshot, which already refreshes on
    ``[selectedId, snapshotVersion]``. Assert the new world: the snapshot effect carries
    that dependency, and the removed ``/api/state`` machinery (the effect's
    ``selectedLiveForEscalationMsg`` helper and the ``setEscalationMsg`` setter) is gone.
    """
    assert "}, [selectedId, snapshotVersion]);" in html_content, (
        "the snapshot effect must depend on [selectedId, snapshotVersion]"
    )
    assert "selectedLiveForEscalationMsg" not in html_content, (
        "the /api/state effect's live-status helper must be removed"
    )
    assert "setEscalationMsg" not in html_content, (
        "the /api/state-fed escalation message setter must be removed"
    )
