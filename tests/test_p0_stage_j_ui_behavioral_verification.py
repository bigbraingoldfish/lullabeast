"""P0 Stage J — ui/index.html phase dropdown renders the Behavioral Verification block.

Stage D wired ``ui/roadmap_parser.py`` to emit
``behavioral_verification: {user_observable, how_to_check, failure_language} | None``
per phase, and ``/api/roadmap`` passes the field through unchanged. Stage J
turns that field into a user-visible subsection inside the expanded phase row
on the pipeline screen.

These are grep-based static assertions on the single-file React app — there
is no JS test runner in this repo. Mirrors the pattern in
``test_p0_stage_h_ui_phase_dropdown.py`` and ``test_p0_ideas_screen_tab.py``.

Pinned:
- the heading "Behavioral Verification:" literal renders
- all three sub-fields are referenced
- the section renders only when ``phase.behavioral_verification`` is truthy
  (no "undefined" leakage for pre-P0 phases the orchestrator still reads
  transitionally)
- the three user-facing labels match the labels the converter writes into
  the roadmap and the parser extracts
"""

from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "ui" / "index.html"


def _read_index() -> str:
    assert INDEX_HTML.exists(), f"Expected {INDEX_HTML}"
    return INDEX_HTML.read_text()


def test_phase_dropdown_renders_behavioral_verification_block():
    """The literal heading "Behavioral Verification:" must appear in
    ui/index.html. Catches a refactor that removes the new subsection
    entirely — without this assertion the dropdown silently regresses
    to its pre-Stage-J state."""
    body = _read_index()
    assert "Behavioral Verification:" in body, (
        "ui/index.html must render the 'Behavioral Verification:' heading "
        "inside the expanded phase block. The data is available on every "
        "phase object served by /api/roadmap since Stage D — the JSX has to "
        "consume it."
    )


def test_phase_dropdown_references_three_bv_subfields():
    """All three sub-field accessors must appear in the JSX. Catches a
    refactor that drops one of the three lines (user_observable,
    how_to_check, failure_language) — each carries a different user
    contract and silently losing any of them defeats the point of the
    subsection."""
    body = _read_index()
    for accessor in (
        "phase.behavioral_verification.user_observable",
        "phase.behavioral_verification.how_to_check",
        "phase.behavioral_verification.failure_language",
    ):
        assert accessor in body, (
            f"ui/index.html must reference {accessor!r}. Stage J specifies "
            "all three sub-fields render in the expanded phase block. If "
            "any go missing, the operator sees only part of the verification "
            "contract."
        )


def test_phase_dropdown_bv_section_conditional_on_non_null():
    """The Behavioral Verification subsection must be conditional on
    ``phase.behavioral_verification`` being truthy. Without this guard, a
    pre-P0 phase (where the field is ``None``) would render "User-observable:
    undefined" — the parser uses ``None`` for partial/missing blocks
    precisely so the UI can short-circuit cleanly."""
    body = _read_index()
    # Accept the two natural truthy-check spellings: either alongside the
    # isExpanded gate or chained as `phase.behavioral_verification &&`.
    has_isexpanded_guard = "isExpanded && phase.behavioral_verification" in body
    has_chained_guard = "phase.behavioral_verification &&" in body
    assert has_isexpanded_guard or has_chained_guard, (
        "ui/index.html must guard the Behavioral Verification subsection "
        "with a truthy check on phase.behavioral_verification (either "
        "'isExpanded && phase.behavioral_verification' or "
        "'phase.behavioral_verification &&'). Pre-P0 phases have the field "
        "set to null and must skip rendering, not show 'undefined'."
    )


def test_phase_dropdown_bv_section_uses_user_facing_labels():
    """The three user-facing labels must match the labels the converter
    writes into the roadmap and the parser extracts. Drift between any
    pair (JSX vs. parser vs. skill spec) means the operator sees one
    phrasing on the Ideas screen and a different one on the pipeline
    screen — confusing at best, contract-breaking at worst."""
    body = _read_index()
    for label in ("User-observable:", "How we'll check:", "If this fails, you see:"):
        assert label in body, (
            f"ui/index.html must render the user-facing label {label!r} "
            "inside the Behavioral Verification subsection. The label "
            "vocabulary is fixed by the roadmap-generation skill so the "
            "operator sees a single consistent phrasing across all "
            "surfaces."
        )
