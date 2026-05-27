"""P0 Stage H — ui/index.html phase dropdown surfaces retry breakdown.

The phase dropdown's "Exec attempts" line previously showed a single
number. Stage H adds a breakdown helper that:

* Renders just the number when both new counters are 0 (clean common
  case — preserves existing "Exec attempts: 1" appearance).
* Renders ``"4 (1 self-failure, 2 reviewer rejections)"`` when either
  counter is > 0, with correct pluralisation.

These are grep-based assertions on the single-file React app — there is
no JS test runner in this repo. Mirrors the pattern in
``test_p0_ideas_screen_tab.py``.
"""

from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "ui" / "index.html"


def _read_index() -> str:
    assert INDEX_HTML.exists(), f"Expected {INDEX_HTML}"
    return INDEX_HTML.read_text()


def test_phase_dropdown_references_new_counters():
    """The phase dropdown JSX (around line 2338) must reference both new
    counter fields so they are wired into the rendered output."""
    body = _read_index()
    assert "executor_self_failures" in body, (
        "ui/index.html must reference executor_self_failures — without "
        "it the phase dropdown cannot render the self-failure breakdown."
    )
    assert "executor_reviewer_rejections" in body, (
        "ui/index.html must reference executor_reviewer_rejections — "
        "without it the phase dropdown cannot render the rejection "
        "breakdown."
    )


def test_phase_dropdown_breakdown_helper_exists():
    """A dedicated helper function (e.g. ``formatExecAttemptsBreakdown``)
    must exist so the breakdown logic is testable in isolation and
    reusable. Pins the contract that the implementation uses a real
    function rather than inline JSX, which would be hard to unit-test."""
    body = _read_index()
    assert "formatExecAttemptsBreakdown" in body, (
        "ui/index.html must define a formatExecAttemptsBreakdown helper "
        "(or equivalently named) so the breakdown formatting is a "
        "single source of truth. Inlining the conditional into JSX makes "
        "the logic untestable and harder to keep consistent if the same "
        "breakdown gets surfaced elsewhere later."
    )


def test_phase_dropdown_clean_common_case_branch_exists():
    """The helper must include a branch that renders ONLY the attempts
    number when both new counters are 0 — keeps the common case visually
    identical to today's output ("Exec attempts: 1")."""
    body = _read_index()
    # Accept multiple acceptable spellings:
    #   - "s === 0 && r === 0"   (the helper's local vars)
    #   - explicit feature-name reference
    has_clean_branch = (
        "s === 0 && r === 0" in body
        or "s == 0 && r == 0" in body
        or "selfFailures === 0 && rejections === 0" in body
    )
    assert has_clean_branch, (
        "The breakdown helper must short-circuit to just the attempts "
        "number when both new counters are 0. Without this, the common "
        "case 'Exec attempts: 1' regresses to 'Exec attempts: 1 ()' or "
        "similar noise."
    )


def test_phase_dropdown_pluralisation_present():
    """The helper must pluralise 'self-failure' and 'reviewer rejection'.
    Pins the pattern so a refactor doesn't accidentally drop the
    plural-s ternary."""
    body = _read_index()
    # Match the pattern used elsewhere in this file: ${n > 1 ? 's' : ''}
    # Look for the phrase + the ternary nearby.
    has_self_failure_plural = (
        "self-failure" in body
        and (
            "self-failure${s > 1 ? 's' : ''}" in body
            or "self-failure${selfFailures > 1 ? 's' : ''}" in body
        )
    )
    has_rejection_plural = (
        "reviewer rejection" in body
        and (
            "reviewer rejection${r > 1 ? 's' : ''}" in body
            or "reviewer rejection${rejections > 1 ? 's' : ''}" in body
        )
    )
    assert has_self_failure_plural, (
        "Breakdown helper must pluralise 'self-failure'. Match the "
        "existing inline-ternary pattern from humanizeSummary line 635."
    )
    assert has_rejection_plural, (
        "Breakdown helper must pluralise 'reviewer rejection'."
    )
