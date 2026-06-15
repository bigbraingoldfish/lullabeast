"""PREREQ-5 — static contract test for the Ideas chat env-safety notice.

Mirror of ``test_ui_p0_02_orchestrator_recovery_banner.py``: asserts the notice
and its safety copy are present in ``ui/index.html``. The banner reminds the user
to share the variable NAME, never the value (DEC-1/DEC-2: Lullabeast never
ingests, transmits, stores, or logs an env value).

These are static-content checks — they do not render React. They would catch the
banner being removed, its copy drifting away from the names-only message, or it
losing the shared amber notice styling.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"

BANNER_TESTID = 'data-testid="ideas-prereq-safety-banner"'


def _index_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _banner_window() -> str:
    html = _index_text()
    assert BANNER_TESTID in html, (
        "Ideas chat must render an env-safety notice with "
        f"{BANNER_TESTID} so the user is reminded to share names, not values."
    )
    pos = html.index(BANNER_TESTID)
    # Window spans backward over the element's className and forward over its
    # text content (the testid attribute sits between the two).
    return html[max(0, pos - 400) : pos + 900]


def test_prereq_safety_banner_present_with_testid():
    assert BANNER_TESTID in _index_text()


def test_prereq_safety_banner_copy_is_names_only():
    window = _banner_window()
    assert "Share the variable" in window
    assert "not the value" in window
    assert "Lullabeast never sees them" in window
    assert "OPENAI_API_KEY" in window


def test_prereq_safety_banner_uses_amber_notice_style():
    window = _banner_window()
    assert "amber-700" in window or "amber-950" in window, (
        "The notice should reuse the existing amber callout styling."
    )
