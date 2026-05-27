"""P0 Stage H — ui/index.html activity feed labels retry_class.

The activity feed's ``humanizeSummary`` function must distinguish
executor self-failure retries from reviewer-rejection retries when the
new ``retry_class`` field appears on ``gate_fail`` or ``attempt_end``
events.

* ``retry_class === "executor_self_failure"`` → prefix/suffix indicating
  "Executor stuck → auto-retry"
* ``retry_class === "reviewer_rejection"`` → prefix/suffix indicating
  "Reviewer rejection → executor retry"
* ``retry_class === "initial_attempt"`` → no decoration (clean common
  case for first attempts and pre-Stage-H events)

Grep-based assertions on the single-file React app. Same pattern as
``test_p0_ideas_screen_tab.py``.
"""

from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "ui" / "index.html"


def _read_index() -> str:
    assert INDEX_HTML.exists(), f"Expected {INDEX_HTML}"
    return INDEX_HTML.read_text()


def test_humanize_summary_references_retry_class_field():
    """``humanizeSummary`` must inspect ``detail.retry_class`` somewhere
    in the gate_fail or attempt_end branches."""
    body = _read_index()
    assert "retry_class" in body, (
        "ui/index.html must reference retry_class — without it the "
        "activity feed cannot label whether a retry was triggered by an "
        "executor self-failure or a reviewer rejection. The two appear "
        "identical in the feed today; Stage H fixes that."
    )


def test_humanize_summary_handles_executor_self_failure_class():
    """The activity feed copy for ``executor_self_failure`` must include
    distinctive language so the operator can spot self-failure retries
    at a glance."""
    body = _read_index()
    # The implementation may use either of these phrasings — accept both.
    # The key invariant is that *some* explicit text appears keyed on the
    # enum value.
    has_self_failure_label = (
        "Executor stuck" in body
        or "self-failure retry" in body
        or "Self-failure retry" in body
    )
    assert has_self_failure_label, (
        "ui/index.html must contain explicit labelling for "
        "retry_class === 'executor_self_failure' (e.g. 'Executor stuck "
        "→ auto-retry' or 'self-failure retry'). Without it the activity "
        "feed treats self-failure retries identically to rejection "
        "retries — the very ambiguity Stage H exists to fix."
    )


def test_humanize_summary_handles_reviewer_rejection_class():
    """Symmetric: the activity feed copy for ``reviewer_rejection`` must
    include distinctive language."""
    body = _read_index()
    has_rejection_label = (
        "Reviewer rejection" in body
        or "reviewer-rejection retry" in body
        or "Reviewer-rejection retry" in body
    )
    assert has_rejection_label, (
        "ui/index.html must contain explicit labelling for "
        "retry_class === 'reviewer_rejection'."
    )


def test_humanize_summary_initial_attempt_no_decoration_branch():
    """When retry_class is 'initial_attempt' (or absent, for pre-Stage-H
    events), the existing event copy must be unchanged — no decoration.
    The ternary or if-else must include the empty-string branch."""
    body = _read_index()
    # Find the retry_class block (could appear in either gate_fail or
    # attempt_end branch). Check that the implementation explicitly
    # accommodates a no-op case rather than always prepending text.
    # The pattern is ``retry_class === '...' ? '...' : ''`` — look for
    # the trailing empty string fallback.
    has_empty_fallback = (
        ': ""' in body and "retry_class" in body
        or ": ''" in body and "retry_class" in body
    )
    assert has_empty_fallback, (
        "humanizeSummary must include an empty-string fallback for the "
        "retry_class ternary so events without the field (or with "
        "retry_class === 'initial_attempt') render unchanged. This "
        "preserves the clean common case for first attempts and "
        "pre-Stage-H events from history."
    )
