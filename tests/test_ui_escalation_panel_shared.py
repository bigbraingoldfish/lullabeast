"""P1 Stage G3 — the escalation panel is ONE shared component.

After consolidation, the single ``EscalationCommandPanel`` is rendered by BOTH the
Pipeline Monitor and the Queue view; the forked inline render that ``QueueActionHub``
used to carry is gone. These static-lint guards assert the consolidation invariants:

* one component, rendered at both call sites
* the two advisory states (ready / fallback) preserved — "generating" was retired
  with the orchestrator's synchronous LLM advisory call (the deterministic
  fallback message now appears immediately and upgrades to the escalation
  agent's summary when escalation_summary.json lands)
* the two previously-divergent fallback strings collapsed into one canonical message
* STOP confirmed via the dedicated ``StopConfirmModal`` (not the generic modal)
* a single ``/api/command`` dispatcher that carries ``target_project_path`` so the
  Queue's deferred command is HELD and fires when the orchestrator picks the parked
  project back up
* the Monitor's button set/visibility reproduced exactly off ``ESCALATION_CMD_DEFS``
* the general header "Stop Pipeline" kill-switch still on ``/api/stop`` (the dead-code
  sweep must not remove the last caller of the RUNNING/WAITING_FOR_SENTINEL path)
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


def _escalation_panel_block(html):
    m = re.search(
        r"// ─── EscalationCommandPanel.*?(?=// ─── PipelineCompletePanel)", html, re.DOTALL
    )
    assert m, "EscalationCommandPanel block not found in index.html"
    return m.group(0)


# ── One component, both views ────────────────────────────────────────────────

def test_single_panel_component_used_in_both_views(html):
    """The shared <EscalationCommandPanel> is rendered at BOTH call sites, and the
    Queue view no longer carries a forked inline escalation render."""
    assert html.count("<EscalationCommandPanel") >= 2, (
        "the shared EscalationCommandPanel must be rendered at BOTH call sites "
        "(Pipeline Monitor + Queue view)"
    )
    hub = _queue_action_hub_block(html)
    assert "<EscalationCommandPanel" in hub, "Queue view must render the shared component"
    # The forked Queue-only render machinery must be gone.
    assert "renderQueueEscalationButton" not in hub, "forked Queue button renderer still present"
    assert "hubRecoverCmds" not in hub, "forked Queue recover-cmd list still present"
    assert "hubStopDef" not in hub, "forked Queue stop def still present"
    assert "hubHeaderText" not in hub, "forked Queue header text still present"


# ── Advisory states preserved (ready / fallback; generating retired) ─────────

def test_shared_panel_renders_advisory_states(html):
    block = _escalation_panel_block(html)
    # ready
    assert 'escalation_advisory_status === "ready"' in block
    assert "Advisory · " in block
    assert "Suggested action · " in block
    # fallback
    assert 'escalation_advisory_status === "fallback"' in block
    # generating is retired: the orchestrator records the deterministic
    # fallback immediately, so nothing synchronous remains to spin on.
    assert 'escalation_advisory_status === "generating"' not in block, (
        "the 'generating' loader branch is dead — no writer emits that status"
    )


# ── Fallback copy collapsed to one canonical string ──────────────────────────

def test_fallback_copy_is_unified(html):
    assert html.count("Automated review unavailable") == 1, (
        "the two divergent fallback strings must collapse to exactly ONE canonical message"
    )
    # The Queue's self-referential wording (wrong when already in the monitor) is gone.
    assert "Open Pipeline Monitor for full context and recovery options." not in html, (
        "the Queue's self-referential fallback wording must be removed"
    )
    assert (
        "Automated review unavailable — the analysis could not be generated. "
        "See the pipeline log for full context." in html
    ), "canonical context-neutral fallback string missing"


# ── STOP uses the dedicated modal, in the shared component ────────────────────

def test_stop_uses_stop_confirm_modal_in_shared_component(html):
    block = _escalation_panel_block(html)
    assert "StopConfirmModal" in block, "shared panel must render StopConfirmModal for STOP"
    assert re.search(r"modalCommand === ['\"]STOP['\"]", block), (
        "shared panel must branch the STOP modal on modalCommand === 'STOP'"
    )


# ── Unified dispatch carries the deferred-hold target ────────────────────────

def test_unified_dispatch_posts_api_command_with_optional_target(html):
    block = _escalation_panel_block(html)
    assert '"/api/command"' in block, "shared dispatcher must POST /api/command"
    assert "targetProjectPath" in block, "shared panel must accept the targetProjectPath prop"
    assert "target_project_path" in block, (
        "shared dispatcher must append target_project_path so the Queue's command is HELD "
        "and fires when the orchestrator picks the parked project back up"
    )
    # The escalation STOP no longer hits /api/stop — it unifies onto /api/command.
    assert '"/api/stop"' not in block, (
        "the panel's /api/stop STOP branch must be removed (escalation STOP unifies to /api/command)"
    )


# ── Monitor button set reproduced exactly off the shared constant ────────────

def test_monitor_button_set_unchanged_after_defs_migration(html):
    """Buttons now come from ESCALATION_CMD_DEFS with the exact Monitor visibility rules:
    RESET_REVIEWER gated on executor output, PROCEED gated on merge AND not-cap, recover
    group hidden at cap. Hard-coded per-button copy must no longer live in the panel block."""
    block = _escalation_panel_block(html)
    assert "ESCALATION_CMD_DEFS" in block, "shared panel must source buttons from ESCALATION_CMD_DEFS"
    assert re.search(r"showRerunReviewer|RESET_REVIEWER", block), "RESET_REVIEWER gating missing"
    assert re.search(r"showMarkComplete|PROCEED", block), "PROCEED gating missing"
    assert "capReached" in block, "capReached gating must remain in the shared panel"
    # Button copy moved into ESCALATION_CMD_DEFS (defined before the panel block).
    assert "Deletes branch · wipes all artifacts · reruns from top." not in block, (
        "per-button copy must live in ESCALATION_CMD_DEFS, not hard-coded in the panel"
    )


# ── Fallback pulse cue: the agent may still upgrade the advisory ─────────────

def test_fallback_shows_reviewing_pulse_cue_while_waiting(html):
    """While the advisory is still the deterministic fallback AND the pipeline is
    WAITING_FOR_HUMAN, the escalation agent's escalation_summary.json may still
    land (the WAITING_FOR_HUMAN poll loop promotes it in place) — a subtle pulse
    cue tells the operator the text may upgrade. A tiny hint next to the fallback
    text, NOT a loader state: the retired "generating" status must not return."""
    block = _escalation_panel_block(html)
    cue_pos = block.find("Escalation agent is reviewing")
    assert cue_pos != -1, "fallback branch must carry the reviewing pulse cue"
    window = block[max(0, cue_pos - 700):cue_pos]
    # Gated on fallback status + WAITING_FOR_HUMAN (humanWait) — once the queue
    # auto-advanced or the escalation resolved, the cue would be a lie.
    assert '"fallback"' in window and "humanWait" in window, (
        "the cue must render only while status is 'fallback' and the pipeline is "
        "WAITING_FOR_HUMAN (the summary can still land and promote)"
    )
    # Subtle pulse dot, reusing the existing animation idiom — not a spinner.
    assert "pulse" in window, "the cue must use the existing pulse animation idiom"
    assert 'escalation_advisory_status === "generating"' not in block, (
        "the cue must not reintroduce the retired 'generating' loader state"
    )


# ── The general header Stop kill-switch survives the dead-code sweep ──────────

def test_general_stop_control_retains_api_stop_caller(html):
    """The general 'Stop Pipeline' header control (halts a RUNNING pipeline via the
    pipeline_stop_requested sentinel) stays on /api/stop. Removing the panel's escalation
    STOP branch must not delete the last /api/stop caller in the UI."""
    m = re.search(r"const handleStopConfirm\s*=\s*async", html)
    assert m, "handleStopConfirm (general header Stop) not found"
    region = html[m.start(): m.start() + 1200]
    assert "/api/stop" in region, "the general header Stop control must still POST /api/stop"
    assert html.count('"/api/stop"') >= 1, (
        "at least one /api/stop caller must remain so a RUNNING pipeline stays stoppable"
    )
