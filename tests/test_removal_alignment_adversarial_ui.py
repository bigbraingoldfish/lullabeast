"""Absence tests: alignment and adversarial UI elements must NOT exist in index.html.

TDD red-state: these tests FAIL against the current codebase (elements exist).
They PASS after the removal is implemented.
"""
from pathlib import Path

HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


def _html():
    return HTML_PATH.read_text()


def test_no_alignment_check_fetch_in_html():
    """/alignment-check fetch call must not appear in index.html."""
    assert "/alignment-check" not in _html(), (
        "Found '/alignment-check' in index.html — doAlignmentCheck fetch not yet removed."
    )


def test_no_adversarial_check_fetch_in_html():
    """/adversarial-check fetch call must not appear in index.html."""
    assert "/adversarial-check" not in _html(), (
        "Found '/adversarial-check' in index.html — doAdversarialCheck fetch not yet removed."
    )


def test_no_quality_check_modal_in_html():
    """Post-roadmap quality check modal testid must not appear in index.html."""
    assert "ideas-roadmap-generated-modal" not in _html(), (
        "Found 'ideas-roadmap-generated-modal' in index.html — modal not yet removed."
    )


def test_no_alignment_tab_in_html():
    """Alignment tab definition must not appear in index.html."""
    assert "id:'alignment'" not in _html(), (
        "Found \"id:'alignment'\" in index.html — alignment tab not yet removed."
    )


def test_no_adversarial_tab_in_html():
    """Adversarial tab definition must not appear in index.html."""
    assert "id:'adversarial'" not in _html(), (
        "Found \"id:'adversarial'\" in index.html — adversarial tab not yet removed."
    )


def test_no_show_quality_check_modal_state():
    """showQualityCheckModal React state must not appear in index.html."""
    assert "showQualityCheckModal" not in _html(), (
        "Found 'showQualityCheckModal' in index.html — state not yet removed."
    )


def test_no_alignment_loading_state():
    """alignmentLoading React state must not appear in index.html."""
    assert "alignmentLoading" not in _html(), (
        "Found 'alignmentLoading' in index.html — state not yet removed."
    )


def test_no_adversarial_loading_state():
    """adversarialLoading React state must not appear in index.html."""
    assert "adversarialLoading" not in _html(), (
        "Found 'adversarialLoading' in index.html — state not yet removed."
    )
