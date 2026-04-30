"""Parity tests: QueueActionHub escalation section must match Pipeline Monitor clarity.

All tests are regex-based against ui/index.html source — consistent with the existing
frontend test pattern in this project.
"""

import re
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html_content():
    if not INDEX_HTML.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML}")
    return INDEX_HTML.read_text(encoding="utf-8")


def _read_queue_action_hub_block(html_content):
    m = re.search(
        r"function QueueActionHub\(\)(.*?)(?=\n\s+function [A-Z])",
        html_content,
        re.DOTALL,
    )
    assert m, "QueueActionHub not found in index.html"
    return m.group(0)


# ── Advisory block ────────────────────────────────────────────────────────────

def test_hub_has_advisory_block_structure(html_content):
    """QueueActionHub has a collapsible advisory block with toggle state."""
    hub = _read_queue_action_hub_block(html_content)
    assert "setAdvisoryExpanded" in hub, \
        "QueueActionHub missing advisory toggle state (setAdvisoryExpanded)"
    assert "advisoryExpanded" in hub, \
        "QueueActionHub missing advisoryExpanded state usage"


def test_hub_advisory_shows_escalation_loops_counter(html_content):
    """QueueActionHub shows 'Escalation loops' counter inside the advisory block."""
    hub = _read_queue_action_hub_block(html_content)
    assert "Escalation loops" in hub, \
        "QueueActionHub does not render 'Escalation loops' counter"


def test_hub_advisory_shows_advisory_prefix(html_content):
    """QueueActionHub shows 'Advisory ·' prefix label inside the advisory block."""
    hub = _read_queue_action_hub_block(html_content)
    assert "Advisory" in hub, \
        "QueueActionHub does not render 'Advisory' prefix in advisory block"


# ── Group labels ──────────────────────────────────────────────────────────────

def test_hub_recover_group_label(html_content):
    """QueueActionHub renders a 'Recover' group label."""
    hub = _read_queue_action_hub_block(html_content)
    assert re.search(r"\bRecover\b", hub), \
        "QueueActionHub does not render a 'Recover' group label"


def test_hub_advance_manually_group_label(html_content):
    """QueueActionHub renders an 'Advance manually' group label."""
    hub = _read_queue_action_hub_block(html_content)
    assert "Advance manually" in hub, \
        "QueueActionHub does not render 'Advance manually' group label"


def test_hub_uses_info_group_label(html_content):
    """QueueActionHub uses InfoGroupLabel for group headers."""
    hub = _read_queue_action_hub_block(html_content)
    assert "InfoGroupLabel" in hub, \
        "QueueActionHub does not use InfoGroupLabel component"


# ── Button descriptions ───────────────────────────────────────────────────────

def test_hub_recover_buttons_have_descriptions(html_content):
    """Recover button descriptions exist in ESCALATION_CMD_DEFS and hub renders via that constant.

    Descriptions live in the top-level ESCALATION_CMD_DEFS constant (shared with monitor).
    The hub references ESCALATION_CMD_DEFS and renders desc via renderQueueEscalationButton.
    We verify both: the desc strings are in the source, and the hub uses the constant.
    """
    assert "Deletes branch" in html_content or "Keeps plan" in html_content, \
        "Recover button descriptions missing from source (ESCALATION_CMD_DEFS)"
    hub = _read_queue_action_hub_block(html_content)
    assert "ESCALATION_CMD_DEFS" in hub, \
        "QueueActionHub does not reference ESCALATION_CMD_DEFS (descriptions won't render)"
    assert "desc" in hub, \
        "QueueActionHub renderQueueEscalationButton does not render desc field"


def test_hub_advance_buttons_have_descriptions(html_content):
    """Advance button descriptions exist in ESCALATION_CMD_DEFS and hub renders via that constant."""
    assert "Merge confirmed" in html_content or "Marks skipped" in html_content, \
        "Advance button descriptions missing from source (ESCALATION_CMD_DEFS)"
    hub = _read_queue_action_hub_block(html_content)
    assert "ESCALATION_CMD_DEFS" in hub, \
        "QueueActionHub does not reference ESCALATION_CMD_DEFS"


# ── Stop Pipeline ─────────────────────────────────────────────────────────────

def test_hub_stop_pipeline_is_red(html_content):
    """QueueActionHub renders Stop Pipeline with red text styling."""
    hub = _read_queue_action_hub_block(html_content)
    assert "text-red-400" in hub, \
        "QueueActionHub does not use text-red-400 for Stop Pipeline"


def test_hub_stop_pipeline_has_description(html_content):
    """Stop Pipeline description exists in ESCALATION_CMD_DEFS and hub renders it via the constant."""
    assert "Halts all pipeline activity immediately" in html_content, \
        "Stop Pipeline description missing from source (ESCALATION_CMD_DEFS)"
    hub = _read_queue_action_hub_block(html_content)
    assert "hubStopDef" in hub, \
        "QueueActionHub does not reference hubStopDef for Stop Pipeline rendering"


# ── Cap-reached notice ────────────────────────────────────────────────────────

def test_hub_cap_reached_notice(html_content):
    """QueueActionHub renders a 'reset budget' cap-reached notice (mirrors monitor)."""
    hub = _read_queue_action_hub_block(html_content)
    assert re.search(r"reset budget", hub, re.IGNORECASE), \
        "QueueActionHub missing cap-reached notice ('reset budget')"


# ── Preserved queue-specific behaviour ───────────────────────────────────────

def test_hub_uses_confirmation_modal_not_inline_toggle(html_content):
    """QueueActionHub uses ConfirmationModal/StopConfirmModal, not an inline pendingCommand toggle."""
    hub = _read_queue_action_hub_block(html_content)
    # Modal components must be rendered inside the hub's escalation section
    assert "ConfirmationModal" in hub, \
        "QueueActionHub does not render ConfirmationModal for escalation confirmation"
    assert "StopConfirmModal" in hub, \
        "QueueActionHub does not render StopConfirmModal for Stop confirmation"
    # onConfirm must wire to the deferred dispatch, not the monitor's sendCommand
    assert "hubModalConfirm" in hub, \
        "QueueActionHub ConfirmationModal onConfirm not wired to hubModalConfirm"
    # The old inline toggle (button-becomes-"Confirm X?") must be gone
    assert "pendingCommand === command" not in hub, \
        "Old inline pendingCommand toggle still present — should be replaced by modal"


def test_hub_preserves_target_project_path_dispatch(html_content):
    """QueueActionHub still passes target_project_path for deferred escalation dispatch."""
    assert "payload.target_project_path = selected.project_path;" in html_content, \
        "target_project_path deferred dispatch removed from QueueActionHub"


# ── Shared constant ───────────────────────────────────────────────────────────

def test_escalation_cmd_defs_at_top_level(html_content):
    """ESCALATION_CMD_DEFS constant is defined before InfoGroupLabel (top-level scope)."""
    defs_pos = html_content.find("const ESCALATION_CMD_DEFS")
    info_pos = html_content.find("function InfoGroupLabel")
    assert defs_pos != -1, "ESCALATION_CMD_DEFS not found in index.html"
    assert info_pos != -1, "InfoGroupLabel not found in index.html"
    assert defs_pos < info_pos, \
        "ESCALATION_CMD_DEFS must be defined before InfoGroupLabel (top-level scope)"
