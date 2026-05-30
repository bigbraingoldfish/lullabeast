"""P1 Stage G1 (de-blame) invariants, re-expressed for the P1 Stage G3 single
shared component.

The escalation surface no longer renders the raw ``escalation_trigger_reason``
(the blame-attribution string) as its headline. The headline shows the LLM advisory
summary when the advisory is ``ready``, otherwise the clean deterministic
``escalation_headline``; the raw trigger reason is relocated into the collapsible
"Internal reason" disclosure (preserved, not deleted).

Before G3 these guards had to assert the de-blame in BOTH hand-maintained panels.
After G3 there is exactly ONE shared ``EscalationCommandPanel`` carrying the de-blame
logic; the Queue view feeds it the de-blamed fields as props rather than re-deriving
the header inline. So: the headline/disclosure logic is asserted once on the shared
component, and the Queue is asserted to pass the clean fields through as props.
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


# ── The old blame-leaking bindings must stay gone ────────────────────────────

def test_header_no_longer_binds_trigger_reason_as_headline(html):
    """No code path renders the raw escalation_trigger_reason as the primary headline."""
    assert '{escalation_trigger_reason || last_action || "Awaiting escalation command"}' not in html, (
        "the escalation header still renders the raw blame string — it must render the "
        "advisory summary or the clean escalation_headline"
    )
    assert "const hubHeaderText   = escalationMsg || 'Awaiting escalation command';" not in html, (
        "an old Queue header still falls back to the blame string via escalationMsg"
    )
    assert 'escalation_trigger_reason || last_action || "Awaiting human command"' not in html, (
        "the panel footer text still binds escalation_trigger_reason"
    )


# ── The shared component renders the de-blamed headline ───────────────────────

def test_shared_panel_header_binds_advisory_or_clean_headline(html):
    """The single shared component shows the advisory summary only when ready, else the
    clean escalation_headline — never the raw blame string."""
    assert 'escalation_advisory_status === "ready" && advisoryText' in html, (
        "header must show the advisory summary only when the advisory is ready"
    )
    assert 'escalation_headline || "This phase needs your input"' in html, (
        "header must fall back to the clean escalation_headline"
    )


def test_escalation_headline_field_is_wired_through(html):
    """The clean escalation_headline reaches the shared component in BOTH views: the
    Monitor passes it from pState; the Queue reads it from /api/state and passes it as a
    prop (it no longer derives a header inline)."""
    assert "escalation_headline={pState.escalation_headline}" in html, (
        "Pipeline Monitor must pass escalation_headline to the shared component"
    )
    assert "d.escalation_headline" in html, (
        "Queue useEffect must read escalation_headline from /api/state"
    )
    assert "escalation_headline={hubHeadline}" in html, (
        "Queue view must pass the clean headline to the shared component as a prop"
    )


# ── The raw trigger reason is relocated, not deleted — and now exactly once ───

def test_raw_trigger_reason_relocated_to_single_internal_reason_disclosure(html):
    """The raw escalation_trigger_reason still appears (in the collapsible "Internal
    reason" disclosure), but now in exactly ONE place — the single shared component."""
    assert "escalation_trigger_reason" in html, (
        "escalation_trigger_reason must still be rendered somewhere (the disclosure)"
    )
    assert html.count("Internal reason") == 1, (
        'the "Internal reason" disclosure must exist exactly once (one shared component); '
        "found a different count — consolidation incomplete or duplicated"
    )
    # The Queue must still feed the raw reason to the shared component as a prop so the
    # single disclosure has content in the Queue view too.
    assert "escalation_trigger_reason={hubTriggerReason}" in html, (
        "Queue view must pass the raw trigger reason to the shared component as a prop"
    )
