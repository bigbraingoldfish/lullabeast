"""UI REVIEW Phase 6 — orphan endpoints & cleanup (1-H copy buttons + 4-D dead prop).

Static substring/regex contracts on ``ui/index.html`` — the house idiom, since no JSX
transpiler runs in CI (render gates are pinned by source content; the operator reviews
the actual wording visually).

1-H — the PRD-All / Roadmap / Verification copy buttons called ``navigator.clipboard``
directly with no fallback and no user feedback (silent when the browser blocks the
clipboard). The fix routes every copy through one shared ``copyToClipboard`` helper
(clipboard → execCommand textarea fallback) and shows ``Copied!`` / ``Copy failed``.

4-D — ``seedRoadmap`` was a dead prop (destructured in ``PreflightScreen`` but never
read; a redundant shadow of the live ``roadmapSeed``). The whole 6-site chain is removed.

1-C / 1-D are documentation-only (comments) and carry no behaviour test here; the
clarity-check endpoint stays guarded by ``tests/test_api_ideas_clarity_check.py``.
"""
import re
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


# ── 1-H: shared copy helper ───────────────────────────────────────────────────

def test_copy_to_clipboard_helper_exists(html):
    """A single ``copyToClipboard`` helper is the source of truth for clipboard access.
    It must carry both the legacy ``execCommand`` fallback and a ``catch`` so a blocked
    clipboard degrades gracefully instead of throwing/ silently no-op'ing.

    Catches: the helper being deleted, or the fallback/error-handling being stripped back
    to a naked ``navigator.clipboard.writeText`` (the original 1-H bug)."""
    assert "function copyToClipboard" in html, (
        "the shared copyToClipboard(text) helper must exist"
    )
    i = html.index("function copyToClipboard")
    body = html[i:i + 900]
    assert 'execCommand("copy")' in body, (
        "copyToClipboard must keep the execCommand textarea fallback"
    )
    assert "catch" in body, "copyToClipboard must catch clipboard failures"


def test_doc_copy_buttons_have_no_naked_writeText(html):
    """The three document-footer copy buttons must no longer call the clipboard API raw.

    Catches: a regression back to the unprotected, feedback-less call."""
    for naked in (
        "navigator.clipboard.writeText(prdContent)",
        "navigator.clipboard.writeText(roadmapContent)",
        "navigator.clipboard.writeText(verificationContent)",
    ):
        assert naked not in html, (
            f"doc copy button must route through the helper, not call {naked!r} directly"
        )


def test_doc_copy_buttons_use_handler(html):
    """Each doc-footer copy button dispatches through the shared handleDocCopy handler."""
    for call in (
        "handleDocCopy('prd'",
        "handleDocCopy('roadmap'",
        "handleDocCopy('verification'",
    ):
        assert call in html, f"expected {call!r} wiring for a doc copy button"


def test_doc_copy_failure_feedback_present(html):
    """The actual 1-H ask: surface failure, not just success. Both the success and the
    total-failure labels must be present.

    Catches: dropping the failure surface and reverting to a success-only / silent button."""
    assert "Copied!" in html, "doc copy buttons must show a success label"
    assert "Copy failed" in html, (
        "doc copy buttons must show 'Copy failed' when the clipboard is blocked (1-H)"
    )


def test_per_section_copy_migrated_to_helper(html):
    """DRY: the per-section '⎘' button (which already had its own inline clipboard →
    textarea fallback) must now call the shared helper, deleting the duplication.

    Catches: the migration regressing to the duplicated inline fallback."""
    assert "copyToClipboard(copySource)" in html, (
        "the per-section ⎘ button must call the shared copyToClipboard helper"
    )
    assert "navigator.clipboard.writeText(copySource)" not in html, (
        "the per-section ⎘ button's inline clipboard call must be gone (migrated to helper)"
    )


# ── 4-D: dead seedRoadmap prop removed ────────────────────────────────────────

def test_seedRoadmap_fully_removed(html):
    """The dead ``seedRoadmap`` chain (destructure + state + 2 setters + ctx export +
    prop pass) is gone. Case-insensitive so it also catches the ``setSeedRoadmap`` setter.

    Catches: any of the 6 sites being left behind (a dangling reference)."""
    assert re.search(r"seedRoadmap", html, re.IGNORECASE) is None, (
        "no seedRoadmap / setSeedRoadmap reference may remain (4-D removes all 6 sites)"
    )


def test_roadmapSeed_still_present(html):
    """Guard: we removed the dead ``seedRoadmap``, NOT its live twin ``roadmapSeed`` (the
    prop PreflightScreen actually reads). This fails loudly if the wrong identifier was
    nuked."""
    assert "roadmapSeed" in html, "the live roadmapSeed prop must remain intact"
