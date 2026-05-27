"""P0 Stage E — reviewer/AGENTS.md doc-contract tests.

Pins the additions Stage E makes to ``autodev/agents/reviewer/AGENTS.md``:
PRD-first reading order, structured ``behavioral_verification`` object
replacing the now-removed ``phase_intent_validated`` boolean, and the
three-evidence-anchor minimum on a pass verdict.

The reviewer's runtime behaviour is driven by what this doc says about
its output schema — silent drift = silently weakening the verifier.
"""

import os

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir)
)
REVIEWER_AGENTS_MD = os.path.join(
    REPO_ROOT, "autodev", "agents", "reviewer", "AGENTS.md"
)


@pytest.fixture(scope="module")
def agents_md_text():
    with open(REVIEWER_AGENTS_MD, "r", encoding="utf-8") as f:
        return f.read()


def test_reading_order_prd_first(agents_md_text):
    """The reviewer's reading order shifts from impl-first to PRD-first.
    PRD must be listed before current_phase.json — that ordering is what
    flips the reviewer's bias from 'did impl match planner spec' to
    'did impl match user requirement'."""
    prd_idx = agents_md_text.find("prd.md")
    cp_idx = agents_md_text.find("current_phase.json")
    assert prd_idx != -1, "reviewer/AGENTS.md must reference prd.md"
    assert cp_idx != -1, "reviewer/AGENTS.md must reference current_phase.json"
    assert prd_idx < cp_idx, (
        "reviewer/AGENTS.md must list prd.md BEFORE current_phase.json in the "
        "reading order — PRD-first is the bias shift Stage E delivers"
    )


def test_verification_md_in_reading_order(agents_md_text):
    """verification.md carries the public surface and acceptance tool — the
    reviewer needs it to know what to verify and how."""
    assert "verification.md" in agents_md_text, (
        "reviewer/AGENTS.md must list verification.md in its reading order"
    )


def test_output_contract_has_structured_behavioral_verification(agents_md_text):
    """behavioral_verification replaces the bare phase_intent_validated
    boolean with a structured object carrying verdict + evidence +
    how_to_check_followed. All three sub-fields must be documented."""
    assert "behavioral_verification" in agents_md_text, (
        "reviewer/AGENTS.md Output Contract must document the structured "
        "behavioral_verification object"
    )
    for key in ("verdict", "evidence", "how_to_check_followed"):
        assert key in agents_md_text, (
            f"reviewer/AGENTS.md behavioral_verification object must "
            f"document the {key!r} sub-field"
        )


def test_evidence_minimum_three_documented(agents_md_text):
    """A 'pass' verdict requires at least three evidence anchors. This
    rule must be stated in the doc — a reviewer that thinks one anchor is
    sufficient produces shallow verifications that the gate then rejects."""
    # Look for "three" or "3" within 400 chars of "evidence".
    ev_idx = agents_md_text.find("evidence")
    assert ev_idx != -1
    # Multiple mentions — scan all and check at least one has the
    # three-anchor rule nearby.
    found = False
    start = 0
    while True:
        idx = agents_md_text.find("evidence", start)
        if idx == -1:
            break
        window = agents_md_text[max(0, idx - 400) : idx + 400].lower()
        if "three" in window or "at least 3" in window or "3 " in window:
            found = True
            break
        start = idx + 1
    assert found, (
        "reviewer/AGENTS.md must state the three-evidence-anchor minimum "
        "on a verdict='pass' near the evidence field — without this rule "
        "the reviewer can pass with a single weak anchor"
    )


def test_phase_intent_validated_removed(agents_md_text):
    """The legacy phase_intent_validated boolean is replaced by the
    structured behavioral_verification object in Stage F. Any lingering
    mention in this doc would re-introduce the dead field as a
    drift-back risk."""
    assert "phase_intent_validated" not in agents_md_text, (
        "reviewer/AGENTS.md must NOT mention phase_intent_validated — the "
        "field was removed in P0 Stage F and replaced by the structured "
        "behavioral_verification object. A lingering reference here would "
        "instruct the reviewer to write a field the gate no longer reads."
    )
