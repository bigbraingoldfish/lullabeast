"""P0 Stage B12: ui/index.html Ideas screen exposes a Verification tab.

These are grep-based assertions on the single-file React app — there is no
JS test runner in this repo. The goal is to catch the most common regression
modes:

- the tab disappears from the array
- the active-tab branch goes missing
- verification content is not wired through from the session payload
"""
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "ui" / "index.html"


def _read_index() -> str:
    assert INDEX_HTML.exists(), f"Expected {INDEX_HTML}"
    return INDEX_HTML.read_text()


def test_ideas_screen_tab_array_includes_verification():
    body = _read_index()
    # The exact JSX shape is single-quoted: {id:'verification', label:'Verification'}.
    # Accept any whitespace between the keys to keep the test resilient to formatter runs.
    assert "id:'verification'" in body or 'id:"verification"' in body, (
        "Ideas-screen tab array must include the verification tab "
        "alongside the existing 'prd' and 'roadmap' tabs."
    )
    assert "label:'Verification'" in body or 'label:"Verification"' in body


def test_ideas_screen_active_doc_tab_branch_for_verification():
    body = _read_index()
    assert "activeDocTab === 'verification'" in body or \
           'activeDocTab === "verification"' in body, (
        "Render branch ``activeDocTab === 'verification'`` must exist so the "
        "Verification panel renders when the tab is selected."
    )


def test_verification_content_state_wired_from_session():
    body = _read_index()
    assert "verification_content" in body, (
        "The React component must read ``verification_content`` from the "
        "session payload so the Verification tab has content to render."
    )
