"""P1 Stage H — UI surface for parked-escalation revival.

Static-source lint (the index.html React app has no build step). Covers:
  * the ESCALATION_ANSWERED queue pill ("Answer banked"),
  * the QUEUE_HALTED "Resume banked answer" recovery button (gated + relaunch-wired),
  * the per-entry "Answer banked" detail card,
  * the answered_pending_revival trigger-toast copy.
"""

import re
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


# ── Pill ──────────────────────────────────────────────────────────────────────

def test_escalation_answered_pill_defined(html):
    """queueOnlyRowPill must map ESCALATION_ANSWERED to the 'Answer banked' sky pill.
    The ``ESCALATION_ANSWERED: {`` literal (object, not the title-map string) is unique to the pill."""
    assert "ESCALATION_ANSWERED: {" in html, "ESCALATION_ANSWERED pill object missing from queueOnlyRowPill"
    entry = html[html.index("ESCALATION_ANSWERED: {"):][:250]
    assert "Answer banked" in entry
    assert "bg-sky-700" in entry, "answered pill must be visually distinct (sky), not amber ESCALATION"


def test_answered_pill_title_present(html):
    assert "ESCALATION_ANSWERED: 'Your answer is banked." in html, (
        "P3_QUEUE_ONLY_PILL_TITLES needs an ESCALATION_ANSWERED tooltip"
    )


# ── QUEUE_HALTED recovery button ───────────────────────────────────────────────

def test_revive_stalled_button_exists_and_gated(html):
    """The Resume button appears only when the queue is halted AND an answered entry exists,
    and it relaunches the orchestrator (whose QUEUE_HALTED hook does the revival)."""
    assert 'data-testid="queue-revive-stalled"' in html, "missing queue-revive-stalled button"
    # Gating derived state must be the halted+answered conjunction.
    assert "const showReviveStalled = queueHalted && answeredEntries.length > 0;" in html
    assert "const queueHalted = pipelineStatus === 'QUEUE_HALTED';" in html
    # answeredEntries gate is asserted in detail by test_answered_entries_include_banked_escalation
    # (it must cover both ESCALATION_ANSWERED and ESCALATION + has_banked_answer).
    # The button is rendered behind that gate and wired to relaunch.
    gate_idx = html.index("showReviveStalled && (")
    btn = html[gate_idx:gate_idx + 1000]
    assert "handleRelaunch(answeredEntries[0].id)" in btn, "Resume button must relaunch the answered entry"
    assert "Resume banked answer" in btn


def test_answered_pending_revival_toast_copy(html):
    """trigger-next's answered_pending_revival reason maps to a recover-forward toast."""
    assert "answered_pending_revival:" in html
    m = re.search(r"answered_pending_revival:\s*'([^']+)'", html)
    assert m and "Resume" in m.group(1), "answered_pending_revival toast should point to Resume"


# ── B1 follow-up: the answered pill must WIN over a stale live_pipeline_status ──

def test_answered_pill_wins_over_live_status(html):
    """queueRowDisplay must return the "Answer banked" pill for an answered/banked parked entry
    BEFORE it consults live_pipeline_status — otherwise a parked WAITING_FOR_HUMAN or global
    QUEUE_HALTED masks it (the B1 finding). Assert the short-circuit precedes the `const live` read."""
    start = html.index("function queueRowDisplay(entry)")
    end = html.index("function queueEntriesHaveBusyLivePipeline")
    block = html[start:end]
    assert "ESCALATION_ANSWERED" in block, "queueRowDisplay must special-case the answered state"
    sc_idx = block.index("ESCALATION_ANSWERED")
    live_idx = block.index("entry.live_pipeline_status")
    assert sc_idx < live_idx, (
        "the answered-state short-circuit must come BEFORE the live_pipeline_status override"
    )
    # The short-circuit returns the Answer-banked pill and also covers ESCALATION + banked answer.
    sc = block[sc_idx:live_idx]
    assert "queueOnlyRowPill('ESCALATION_ANSWERED')" in sc
    assert "has_banked_answer" in sc, "ESCALATION + has_banked_answer must also read as answered"


# ── B3(ii) follow-up: Resume affordance covers ESCALATION + banked answer ──────

def test_answered_entries_include_banked_escalation(html):
    """answeredEntries (gates the Resume button) must include a parked ESCALATION row that has a
    banked answer — the common QUEUE_HALTED-then-bank case the orchestrator hasn't promoted yet."""
    m = re.search(r"const answeredEntries = queue\.filter\((.*?)\);", html, re.DOTALL)
    assert m, "answeredEntries filter not found"
    f = m.group(1)
    assert "ESCALATION_ANSWERED" in f
    assert "has_banked_answer" in f, "answeredEntries must include ESCALATION + has_banked_answer"


def test_answered_detail_card_covers_banked_escalation(html):
    """The QueueActionHub 'Answer banked' card must render for an ESCALATION row with a banked
    answer too, not only the promoted ESCALATION_ANSWERED state."""
    hub_start = html.index("function QueueActionHub()")
    branch_idx = html.index("state === 'ESCALATION_ANSWERED'", hub_start)
    branch = html[branch_idx:branch_idx + 200]
    assert "has_banked_answer" in branch, (
        "the answered detail-card branch must also fire for ESCALATION + has_banked_answer"
    )
