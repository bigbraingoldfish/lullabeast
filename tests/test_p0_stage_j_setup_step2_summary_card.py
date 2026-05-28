"""P0 Stage J.4 — Setup-screen Step 2 free-text roadmap surface removed,
replaced by "From Project Ideas" summary card + empty-state CTA.

Plan §3 J.4 of ``plans/Active/p0-behavioral-verification-runtime-profile.md``
specifies that the Step 2 free-text roadmap textarea, the upload/paste
buttons, and the Fix-Format CTA must be removed. Authoring lives in
Project Ideas; Setup is purely staging. Operator-confirmed during plan
review: J.4 ships with the rest of P0 because shipping P0 without it
means every user who previously used the Step 2 textbox hits a red
"verification doc missing" row in Step 4 with no UI to provide one.

These are grep-based static assertions on the single-file React app —
mirrors the pattern in ``test_p0_stage_h_ui_phase_dropdown.py`` and
``test_p0_ideas_screen_tab.py``.

The test helper ``_preflight_slice()`` extracts the
``function PreflightScreen(props)`` body by slicing between consecutive
top-level ``function`` declarations. This keeps assertions scoped to the
component under test even though ``ui/index.html`` is a single 8000-line
file — for example, the "Upload file" literal might still appear in some
other component (the Ideas screen has its own upload flow), but the
Step 2 assertion specifically checks the PreflightScreen slice.
"""

from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "ui" / "index.html"


def _read_index() -> str:
    assert INDEX_HTML.exists(), f"Expected {INDEX_HTML}"
    return INDEX_HTML.read_text()


def _preflight_slice() -> str:
    """Return the substring covering the PreflightScreen component body.

    Uses the next top-level ``function`` declaration as the upper bound.
    Resilient to formatter passes because it does not depend on exact
    line numbers — only on the declaration anchors that have been
    stable across the ui/index.html refactors of Stages A–I.
    """
    body = _read_index()
    start = body.find("function PreflightScreen")
    assert start != -1, (
        "ui/index.html must still define a top-level PreflightScreen "
        "component. Stage J does not delete the component itself — only "
        "the Step 2 textarea/upload/paste/Fix-Format surface inside it."
    )
    # The next top-level function declaration is the upper bound. The
    # known successor today is ``function QueueScreen()``; the test
    # accepts any top-level component declaration so a future rename
    # does not break the slice.
    upper = body.find("\n        function ", start + len("function PreflightScreen"))
    if upper == -1:
        # Fall back to end of file — safer than asserting failure here,
        # the individual assertions below will still catch the real
        # regressions.
        upper = len(body)
    return body[start:upper]


def test_step2_no_longer_renders_free_text_roadmap_textarea():
    """The Step 2 ``<textarea>`` bound to ``roadmapSeed`` must not exist
    inside the PreflightScreen body. The textarea is the entire reason
    Step 2 today can stage a project without a verification doc — its
    removal is the operator-stated cutover.

    The assertion is intentionally narrow: it does not forbid every
    ``<textarea`` in the file (the Ideas screen still uses textareas
    for PRD authoring). It forbids the *combination* of a textarea and
    the ``roadmapSeed`` state inside PreflightScreen — that is the
    unambiguous fingerprint of the surface being removed."""
    slice_ = _preflight_slice()

    # Walk every <textarea occurrence in the slice; assert none of them
    # bind to roadmapSeed within a 200-char window. The two-step pattern
    # keeps the test resilient to JSX formatter changes.
    pos = 0
    found_offending = False
    while True:
        i = slice_.find("<textarea", pos)
        if i == -1:
            break
        window = slice_[i:i + 400]
        if "roadmapSeed" in window:
            found_offending = True
            break
        pos = i + 1

    assert not found_offending, (
        "PreflightScreen still renders a <textarea> bound to roadmapSeed. "
        "Stage J.4 removes the free-text roadmap surface in Step 2. The "
        "summary card / empty-state CTA replace this — see the other "
        "assertions in this file."
    )


def test_step2_no_longer_offers_upload_or_paste_buttons():
    """The old "Upload file" + "Paste content" empty-state buttons
    inside PreflightScreen Step 2 are gone. Catches a partial removal
    that drops the textarea but leaves the upload/paste affordances
    pointing at no-longer-bound state."""
    slice_ = _preflight_slice()
    for literal in ("Upload file", "Paste content"):
        assert literal not in slice_, (
            f"PreflightScreen must no longer expose the {literal!r} "
            "button. Stage J.4 collapses Step 2 to a Project-Ideas "
            "summary card + empty-state CTA. Free-text paste lives on "
            "the Ideas screen now."
        )


def test_step2_no_longer_offers_fix_format_cta():
    """The Step 2 "Fix Format" button and its ``onFixRoadmapSeedFormat``
    handler are both removed. The endpoint
    ``/api/ideas/{id}/fix-roadmap-format`` itself is preserved — only its
    Setup-screen entry point is gone (the Project Ideas screen still
    calls it during PRD-driven authoring)."""
    body = _read_index()
    assert "onFixRoadmapSeedFormat" not in body, (
        "The onFixRoadmapSeedFormat handler must be removed entirely. "
        "Step 2 was its only caller; leaving the handler around would "
        "be dead code (engineering-standards: removal must take the "
        "dependency chain with it)."
    )
    slice_ = _preflight_slice()
    assert "Fix Format" not in slice_, (
        "PreflightScreen must no longer render a 'Fix Format' button. "
        "Stage J.4 removes the Setup-screen entry point to "
        "/api/ideas/{id}/fix-roadmap-format. The endpoint itself stays "
        "callable from the Project Ideas screen."
    )


def test_step2_renders_from_project_ideas_summary_card():
    """The replacement Step 2 surface must render a summary card with
    the idea name, phase count, three readiness pills, and a back-link
    to Project Ideas. Pins the four data points the card MUST display
    so a refactor cannot accidentally drop one."""
    slice_ = _preflight_slice()
    assert "From Project Ideas" in slice_, (
        "PreflightScreen must render a 'From Project Ideas' summary "
        "heading. The label was a badge string in pre-J.4 code; J.4 "
        "promotes it to the card heading."
    )
    # Phase-count indicator — accept either 'phase' or 'phases' literal
    # so pluralisation in the card is allowed to vary.
    assert "phase" in slice_.lower(), (
        "PreflightScreen summary card must show a phase count. Operator "
        "needs to see at a glance whether the carried-in roadmap matches "
        "what they remember from Project Ideas."
    )
    # Three readiness pills — match the operator-supplied wording.
    for pill in ("PRD ready", "Roadmap ready", "Verification ready"):
        assert pill in slice_, (
            f"PreflightScreen summary card must render the {pill!r} pill. "
            "All three readiness signals must show — verification ready "
            "is the new one in P0 and silently dropping it defeats the "
            "purpose of the J.4 rework."
        )


def test_step2_renders_empty_state_cta_when_no_idea_linked():
    """When no Project Idea is linked to the current Setup session
    (direct entry to Setup, no navigateToPreflightWithSeed in this
    flow), PreflightScreen must render a CTA pointing the user at
    Project Ideas — with no editable controls in Step 2. Pins the
    operator's stated empty-state copy."""
    slice_ = _preflight_slice()
    assert "No project idea linked to this repo path" in slice_, (
        "PreflightScreen must render the empty-state CTA copy 'No "
        "project idea linked to this repo path.' when no idea has been "
        "carried in. Operator wording in P0 plan §3 J.4 — pinned "
        "verbatim so future copy drift surfaces in review."
    )
    assert "Create one in Project Ideas" in slice_, (
        "Empty-state CTA must include the 'Create one in Project Ideas' "
        "call-to-action linking the user back to authoring."
    )


def test_step2_preserves_open_in_project_ideas_link():
    """The summary card must include a back-link to Project Ideas (the
    operator wording is 'Open in Project Ideas →' or 'Edit in Project
    Ideas →' — both are accepted because the card may differentiate
    between expand/collapse states). Without this link the card is a
    read-only dead-end."""
    slice_ = _preflight_slice()
    has_open = "Open in Project Ideas" in slice_
    has_edit = "Edit in Project Ideas" in slice_
    assert has_open or has_edit, (
        "PreflightScreen summary card must provide a back-link to "
        "Project Ideas — either 'Open in Project Ideas →' (collapsed "
        "card) or 'Edit in Project Ideas →' (expanded read-only doc "
        "view). Without it the operator has no UI path back to "
        "authoring after staging."
    )
