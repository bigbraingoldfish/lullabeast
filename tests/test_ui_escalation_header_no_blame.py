"""P1 Stage G1 — static-lint guard: neither escalation panel may render the raw
``escalation_trigger_reason`` (the blame-attribution string) as its headline.

Two panels exist (G3 consolidation is deferred), so the de-blame must be present
in BOTH:
  * ``EscalationCommandPanel`` (Pipeline Monitor)
  * ``QueueActionHub`` inline render (Queue view)

The headline shows the LLM advisory summary when the advisory is ``ready``,
otherwise the clean deterministic ``escalation_headline``. The raw trigger reason
is relocated into the collapsible "Internal reason" disclosure — preserved, not
deleted.
"""

import os

import pytest

INDEX_HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html"
)


@pytest.fixture
def html():
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


# ── The old blame-leaking bindings must be gone ──────────────────────────────

def test_escalation_panel_header_no_longer_binds_trigger_reason(html):
    """EscalationCommandPanel's collapsible header must not bind the raw
    escalation_trigger_reason as its primary text."""
    assert '{escalation_trigger_reason || last_action || "Awaiting escalation command"}' not in html, (
        "EscalationCommandPanel header still renders the raw blame string — it "
        "must render the advisory summary or the clean escalation_headline"
    )


def test_escalation_panel_footer_text_no_longer_binds_trigger_reason(html):
    """The confirm/footer headerText must not bind the raw blame string either."""
    assert 'escalation_trigger_reason || last_action || "Awaiting human command"' not in html, (
        "EscalationCommandPanel footer text still binds escalation_trigger_reason"
    )


def test_queue_hub_header_no_longer_falls_back_to_blame(html):
    """QueueActionHub's hubHeaderText must not fall back to escalationMsg (which
    resolves to escalation_trigger_reason when the advisory failed)."""
    assert "const hubHeaderText   = escalationMsg || 'Awaiting escalation command';" not in html, (
        "QueueActionHub header still falls back to the blame string via escalationMsg"
    )


# ── The clean replacements must be present in BOTH panels ────────────────────

def test_escalation_panel_header_binds_advisory_or_clean_headline(html):
    """EscalationCommandPanel header uses the advisory-ready gate + clean headline."""
    assert 'escalation_headline || "This phase needs your input"' in html, (
        "EscalationCommandPanel header must fall back to the clean escalation_headline"
    )
    assert 'escalation_advisory_status === "ready" && advisoryText' in html, (
        "header must show the advisory summary only when the advisory is ready"
    )


def test_queue_hub_header_binds_advisory_or_clean_headline(html):
    """QueueActionHub header uses the advisory-ready gate + clean headline."""
    assert "hubHeadline || 'Awaiting escalation command'" in html, (
        "QueueActionHub header must fall back to the clean hubHeadline"
    )
    assert 'hubAdvisoryStatus === "ready" && hubAdvisoryText' in html, (
        "QueueActionHub header must show the advisory summary only when ready"
    )


def test_escalation_headline_field_is_wired_through(html):
    """The new escalation_headline field is consumed by the panel (prop) and the
    queue hub (state)."""
    assert "escalation_headline={pState.escalation_headline}" in html, (
        "EscalationCommandPanel must receive escalation_headline as a prop"
    )
    assert "d.escalation_headline" in html, (
        "QueueActionHub useEffect must read escalation_headline from /api/state"
    )


# ── The raw trigger reason is relocated, not deleted ─────────────────────────

def test_raw_trigger_reason_relocated_to_internal_reason_disclosure(html):
    """The raw escalation_trigger_reason must still appear (in the collapsible
    "Internal reason" disclosure) in BOTH panels — demoted, not removed."""
    assert "escalation_trigger_reason" in html, (
        "escalation_trigger_reason must still be rendered somewhere (the disclosure)"
    )
    assert html.count("Internal reason") >= 2, (
        'both panels must carry an "Internal reason" disclosure line for the raw '
        "trigger string (found fewer than 2)"
    )
