"""PREREQ-5 — static contract test for the Ideas chat env-safety notice.

The notice is **contextual**, not a persistent bar: it renders inside an
assistant message only when that turn discusses environment variables / secrets
(gated by ``messageDiscussesEnv``), styled like an assumption note but compact
and non-collapsible. It reminds the user to share the variable NAME, never the
value (DEC-1/DEC-2: Lullabeast never ingests, transmits, stores, or logs an env
value).

Static checks only — they would catch the notice being removed, its copy
drifting from the names-only message, it losing the amber styling, or it
regressing to an always-on bar (the render must stay gated by the detector).
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"

NOTE_TESTID = 'data-testid="ideas-prereq-safety-banner"'


def _index_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _note_window() -> str:
    html = _index_text()
    assert NOTE_TESTID in html, (
        "Ideas chat must render an env-safety notice with "
        f"{NOTE_TESTID} so the user is reminded to share names, not values."
    )
    pos = html.index(NOTE_TESTID)
    # Window spans backward over the element's className and forward over its text.
    return html[max(0, pos - 400) : pos + 900]


def test_prereq_safety_note_present_with_testid():
    assert NOTE_TESTID in _index_text()


def test_prereq_safety_note_copy_is_names_only():
    window = _note_window()
    assert "Share only the variable" in window
    assert "Never the key value" in window
    assert "OPENAI_API_KEY" in window
    assert "your project's" in window


def test_prereq_safety_note_uses_amber_style():
    window = _note_window()
    assert "amber-700" in window or "amber-950" in window, (
        "The notice should reuse the existing amber callout styling."
    )


def test_prereq_safety_note_is_contextual_not_persistent():
    """The notice must render only when env vars are discussed — gated by the
    ``messageDiscussesEnv`` detector and emitted as the ``IdeasEnvSafetyNote``
    component in the message stream — not pinned above the composer."""
    html = _index_text()
    assert "function messageDiscussesEnv" in html, (
        "an env-topic detector must gate the notice"
    )
    assert "<IdeasEnvSafetyNote" in html, (
        "the notice must be rendered as a gated component in the message stream"
    )
    assert "messageDiscussesEnv(msg.parsed)" in html, (
        "the notice render must be guarded by the detector (contextual, not persistent)"
    )
