"""P1 Stage G3 — the Queue view consumes the shared escalation panel.

Replaces the former ``test_ui_queue_escalation_parity.py``, whose entire premise —
two hand-maintained panels that must be kept matching by hand — no longer exists
after consolidation. Parity is now structural: the Queue renders the same
``EscalationCommandPanel`` the Pipeline Monitor does. The one behaviour that is
genuinely Queue-specific and must be preserved is the deferred dispatch: a command
issued for a parked ``ESCALATION`` project carries ``target_project_path`` so the
server HOLDS it (``pending_escalation_command.json``) and it fires when the
orchestrator picks that project back up.
"""

import re
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


def _queue_action_hub_block(html):
    m = re.search(r"function QueueActionHub\(\)(.*?)(?=\n\s+function [A-Z])", html, re.DOTALL)
    assert m, "QueueActionHub not found in index.html"
    return m.group(0)


def _queue_screen_block(html):
    """The whole QueueScreen component (includes the nested QueueActionHub), bounded by
    the next top-level screen function. ``/api/state`` legitimately appears in other
    components (Pipeline Monitor, App bootstrap), so escalation-source assertions must be
    scoped to this block."""
    m = re.search(r"function QueueScreen\(\)(.*?)(?=\n\s+function PipelineScreen)", html, re.DOTALL)
    assert m, "QueueScreen not found in index.html"
    return m.group(0)


# ── Structural consolidation: Queue renders the shared component ──────────────

def test_queue_renders_shared_escalation_panel(html):
    hub = _queue_action_hub_block(html)
    assert "<EscalationCommandPanel" in hub, (
        "the Queue escalation branch must render the shared <EscalationCommandPanel>"
    )


def test_queue_has_no_forked_inline_escalation_render(html):
    """None of the forked render machinery survives in QueueActionHub."""
    hub = _queue_action_hub_block(html)
    for forked in (
        "renderQueueEscalationButton",
        "hubRecoverCmds",
        "hubAdvanceCmds",
        "hubStopDef",
        "hubHeaderText",
        "hubCounterText",
        "hubModalConfirm",
    ):
        assert forked not in hub, f"forked Queue escalation symbol still present: {forked}"


def test_queue_feeds_deblamed_fields_as_props(html):
    """The Queue passes the per-entry-snapshot-sourced de-blamed fields to the shared
    component instead of rendering a header/advisory inline. (Source changed from
    /api/state to the snapshot in the G3 follow-up; the prop names are unchanged.)"""
    hub = _queue_action_hub_block(html)
    for prop in (
        "escalation_headline={hubHeadline}",
        "escalation_trigger_reason={hubTriggerReason}",
        "escalation_message={escalationMsg}",
        "escalation_advisory_status={hubAdvisoryStatus}",
        "escalation_recommended_action={hubRecommendedAction}",
    ):
        assert prop in hub, f"Queue must pass {prop} to the shared component"


def test_queue_feeds_eligibility_fields_as_props(html):
    hub = _queue_action_hub_block(html)
    assert "escalation_resets={hubEscalationResets}" in hub
    assert "executor_output_exists={hubExecutorOutputExists}" in hub
    assert "merge_probe_passed={hubMergeProbePassed}" in hub


# ── Deferred-hold preserved (the load-bearing Queue behaviour) ────────────────

def test_queue_preserves_target_project_path_deferred_dispatch(html):
    """A parked ESCALATION project's command must be routed with target_project_path so
    the server defers it (held until the orchestrator activates that project)."""
    hub = _queue_action_hub_block(html)
    assert "targetProjectPath=" in hub, "Queue must pass targetProjectPath to the shared component"
    # The guard that selects deferred-vs-immediate must be preserved exactly.
    assert "selected.state === 'ESCALATION'" in hub, (
        "the ESCALATION guard for deferred dispatch must be preserved"
    )
    assert "selected.project_path" in hub, "the target project path source must be preserved"


def test_queue_onDispatched_refreshes_queue(html):
    hub = _queue_action_hub_block(html)
    assert "onDispatched={fetchQueue}" in hub, (
        "Queue must pass onDispatched={fetchQueue} so the queue refreshes after dispatch"
    )


# ── P1 Stage H: answered entries do NOT re-show the command panel ─────────────

def test_answered_branch_renders_compact_card_not_command_panel(html):
    """A parked project whose answer is already banked (ESCALATION_ANSWERED) must show a
    compact 'Answer banked' status card — NOT the full EscalationCommandPanel (the operator
    already answered; re-prompting would invite a conflicting second command). The branch
    must also precede the isEscalationWaiting block so it wins for an answered entry."""
    hub = _queue_action_hub_block(html)
    assert "state === 'ESCALATION_ANSWERED'" in hub, "QueueActionHub needs an ESCALATION_ANSWERED branch"
    answered_idx = hub.index("state === 'ESCALATION_ANSWERED'")
    waiting_idx = hub.index("if (isEscalationWaiting)")
    assert answered_idx < waiting_idx, (
        "the ESCALATION_ANSWERED branch must come BEFORE the isEscalationWaiting block"
    )
    # The answered branch body (up to the isEscalationWaiting block) must not mount the panel.
    answered_branch = hub[answered_idx:waiting_idx]
    assert "Answer banked" in answered_branch
    assert "<EscalationCommandPanel" not in answered_branch, (
        "answered entries must not re-render the full command panel"
    )


# ── Lifecycle chrome stays OFF in the Queue (decision: 'Queue stays light') ───

def test_queue_does_not_enable_orchestrator_lifecycle_chrome(html):
    """The Queue must not opt into the Monitor-only orchestrator-lifecycle chrome."""
    hub = _queue_action_hub_block(html)
    assert "showOrchestratorLifecycle" not in hub, (
        "Queue must NOT enable showOrchestratorLifecycle (chrome stays Monitor-only)"
    )
    assert "showCommandSentScreen" not in hub, (
        "Queue must NOT enable showCommandSentScreen (chrome stays Monitor-only)"
    )


# ── Advisory is single-sourced from the per-entry snapshot (G3 follow-up) ─────

def test_queuescreen_does_not_fetch_api_state_for_escalation(html):
    """The Queue advisory is now sourced from the per-entry snapshot, which targets the
    SELECTED project — the same project the command dispatch targets. The old
    ``/api/state`` fetch read the ACTIVE symlink project's advisory (the bug), so the
    QueueScreen must no longer fetch ``/api/state`` at all, and the state machinery that
    fed it (``setEscalationMsg`` / ``selectedLiveForEscalationMsg``) must be gone.

    Scoped to the QueueScreen block because ``/api/state`` is legitimately fetched by the
    Pipeline Monitor and App bootstrap.
    """
    block = _queue_screen_block(html)
    # Target the actual network call (either quote style), not bare prose — an explanatory
    # comment mentioning the removed /api/state fetch is legitimate documentation.
    assert 'fetch("/api/state")' not in block and "fetch('/api/state')" not in block, (
        "QueueScreen must not fetch /api/state — read the per-entry snapshot, which "
        "describes the SELECTED project (the dispatch target), not the active one"
    )
    assert "setEscalationMsg" not in block, (
        "the /api/state-fed escalation message setter must be removed from QueueScreen"
    )
    assert "selectedLiveForEscalationMsg" not in block, (
        "the /api/state effect's live-status helper must be removed from QueueScreen"
    )
