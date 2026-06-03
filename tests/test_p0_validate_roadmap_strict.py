"""P0 Stage K — strict-mode regression pins for ``_validate_roadmap_content``.

Stage C (`tests/test_p0_validate_roadmap_behavioral.py`) pins the happy path,
each sub-bullet missing, the block-too-far case, the no-legacy-kwarg shape,
and a single mixed-phase case. Stage K closes six additional holes the
operator surfaced as cutover regression pins:

* multi-phase happy path (existing coverage is single-phase only)
* empty-body cases for **How we'll check** and **If this fails** (existing
  coverage pins only the **User-observable** empty body)
* phase-bounded BV-block detection — a complete block is accepted anywhere in
  its own phase section (the fixed 30-line ``_BV_SEARCH_WINDOW`` was retired
  because it conflicted with the canonical 'BV block last' structure on long
  phases), and a block belonging to the next phase does not satisfy this one
* duplicate phase IDs with BV blocks present (existing duplicate test in
  ``tests/test_api_setup_validate_roadmap.py`` is BV-block-free)
* phase ID appearing in an Entry Criteria body must not register as a
  duplicate (anchors the ``_PHASE_LINE_RE`` start-of-line guard)
* a pre-P0 legacy roadmap with three phases must produce a Behavioral
  Verification error for EVERY phase — the no-legacy-mode cutover gate

Plus a ``TestFormatCorrectionFixturePairs`` class that pairs a handcrafted
pre-P0 legacy roadmap with a structurally-correct post-correction shape
matching ``format-correction/SKILL.md`` lines 146-158. The fixtures are
module-level constants so the Stage K format-correction endpoint smoke
(``tests/test_p0_stage_k_format_correction_smoke.py``) can import them.

These tests are pure regression pins — they call ``_validate_roadmap_content``
directly with no mocks. Failures here flag a contradiction with the assumed
post-Stage-A–J implementation state.
"""

import inspect

import pytest

from ui.server import _validate_roadmap_content


# ---------------------------------------------------------------------------
# Shared phrase fragments — keep the test bodies focused on the assertion.
# ---------------------------------------------------------------------------

_PHASE_E1 = "- [ ] `UI-E1` | LOW | Render the scaffold"
_PHASE_E2 = "- [ ] `UI-E2` | MEDIUM | Add the second screen"
_TEST_E1 = "  > Test: Screen renders without errors."
_TEST_E2 = "  > Test: Second screen renders."


def _full_bv_block(user="A scaffold appears.", how="Open the page.", fail="Page broken.") -> str:
    return (
        "  **Behavioral Verification:**\n"
        f"  - **User-observable:** {user}\n"
        f"  - **How we'll check:** {how}\n"
        f"  - **If this fails, the user sees:** {fail}\n"
    )


def _phase_block(phase_line: str, test_line: str, bv: str | None = None) -> str:
    """Compose a phase: header + test line + optional BV block. Adds a trailing newline."""
    body = phase_line + "\n" + test_line + "\n"
    if bv is not None:
        body += bv
    return body


# ---------------------------------------------------------------------------
# Module-level fixture pair — pre-P0 vs post-correction roadmap shapes.
# Imported by tests/test_p0_stage_k_format_correction_smoke.py.
# The 5-phase shape mirrors a realistic CLI/web task-tracker roadmap and is
# big enough to exercise multi-phase iteration in the validator without being
# noisy.
# ---------------------------------------------------------------------------

_PRE_P0_ROADMAP_FIXTURE = """\
- [ ] `CORE-E1` | LOW | Scaffold project structure
  > Test: src/ and tests/ directories exist with __init__.py present.

  **Entry Criteria:** Fresh repo with .git/.
  **Exit Criteria:** Test runner discovers an empty test placeholder.

- [ ] `CORE-E2` | MEDIUM | Implement Task model
  > Test: Task instances serialize to and from JSON without loss.

  **Entry Criteria:** `CORE-E1` complete.
  **Exit Criteria:** test_task_model.py passes.

- [ ] `API-E1` | MEDIUM | Wire /tasks endpoint
  > Test: GET /tasks returns 200 with an empty list when no tasks exist.

  **Entry Criteria:** `CORE-E2` complete.
  **Exit Criteria:** test_api_tasks.py passes.

- [ ] `UI-E1` | HIGH | Render task list view
  > Test: Navigating to /tasks shows the list container with header.

  **Entry Criteria:** `API-E1` complete.
  **Exit Criteria:** test_task_list_view.py passes.

- [ ] `INT-E1` | HIGH | End-to-end task creation
  > Test: Submitting the new-task form persists a task to the backing store.

  **Entry Criteria:** `UI-E1` complete.
  **Exit Criteria:** test_e2e_task_creation.py passes.
"""


_POST_CORRECTION_ROADMAP_FIXTURE = """\
- [ ] `CORE-E1` | LOW | Scaffold project structure
  > Test: src/ and tests/ directories exist with __init__.py present.

  **Entry Criteria:** Fresh repo with .git/.
  **Exit Criteria:** Test runner discovers an empty test placeholder.

  **Behavioral Verification:**
  - **User-observable:** The developer runs the test command and sees no missing-module errors. <!-- TODO: human-review -->
  - **How we'll check:** src/ and tests/ directories exist with __init__.py present.
  - **If this fails, the user sees:** ImportError on every test invocation. <!-- TODO: human-review -->

- [ ] `CORE-E2` | MEDIUM | Implement Task model
  > Test: Task instances serialize to and from JSON without loss.

  **Entry Criteria:** `CORE-E1` complete.
  **Exit Criteria:** test_task_model.py passes.

  **Behavioral Verification:**
  - **User-observable:** Task records round-trip through storage without losing fields. <!-- TODO: human-review -->
  - **How we'll check:** Task instances serialize to and from JSON without loss.
  - **If this fails, the user sees:** Task data appears corrupted or fields go missing after save. <!-- TODO: human-review -->

- [ ] `API-E1` | MEDIUM | Wire /tasks endpoint
  > Test: GET /tasks returns 200 with an empty list when no tasks exist.

  **Entry Criteria:** `CORE-E2` complete.
  **Exit Criteria:** test_api_tasks.py passes.

  **Behavioral Verification:**
  - **User-observable:** A client calling GET /tasks receives a 200 response with a JSON list. <!-- TODO: human-review -->
  - **How we'll check:** GET /tasks returns 200 with an empty list when no tasks exist.
  - **If this fails, the user sees:** 5xx errors or malformed responses when listing tasks. <!-- TODO: human-review -->

- [ ] `UI-E1` | HIGH | Render task list view
  > Test: Navigating to /tasks shows the list container with header.

  **Entry Criteria:** `API-E1` complete.
  **Exit Criteria:** test_task_list_view.py passes.

  **Behavioral Verification:**
  - **User-observable:** The user opens /tasks and sees the list container with the header. <!-- TODO: human-review -->
  - **How we'll check:** Navigating to /tasks shows the list container with header.
  - **If this fails, the user sees:** A blank page or a stack trace at /tasks. <!-- TODO: human-review -->

- [ ] `INT-E1` | HIGH | End-to-end task creation
  > Test: Submitting the new-task form persists a task to the backing store.

  **Entry Criteria:** `UI-E1` complete.
  **Exit Criteria:** test_e2e_task_creation.py passes.

  **Behavioral Verification:**
  - **User-observable:** A user fills in the new-task form and the task appears in the list. <!-- TODO: human-review -->
  - **How we'll check:** Submitting the new-task form persists a task to the backing store.
  - **If this fails, the user sees:** The new task disappears on refresh. <!-- TODO: human-review -->
"""


# ---------------------------------------------------------------------------
# TestValidateRoadmapStrict — six gaps not covered by Stage C tests.
# ---------------------------------------------------------------------------


class TestValidateRoadmapStrict:
    """Strict-mode regression pins for behaviors Stage C left uncovered.

    Each test fills a precisely-scoped gap rather than re-asserting Stage C
    coverage. If a future change weakens any of these, the corresponding
    test fires.
    """

    def test_two_phases_both_with_block_pass(self):
        """Multi-phase happy path. Stage C only covered the single-phase shape;
        a regression that broke per-phase iteration could pass single-phase
        but fail here."""
        content = (
            _phase_block(_PHASE_E1, _TEST_E1, _full_bv_block())
            + "\n"
            + _phase_block(
                _PHASE_E2,
                _TEST_E2,
                _full_bv_block(user="Second screen visible.", how="Click next.", fail="Stuck on first."),
            )
        )
        result = _validate_roadmap_content(content)
        assert result["valid"] is True, f"Expected valid; errors: {result['errors']}"
        assert result["errors"] == []

    def test_empty_how_to_check_body_fails(self):
        """Empty body on **How we'll check** must fail. Stage C only pinned
        this for **User-observable**."""
        block = (
            "  **Behavioral Verification:**\n"
            "  - **User-observable:** Something visible.\n"
            "  - **How we'll check:** \n"
            "  - **If this fails, the user sees:** Something broken.\n"
        )
        content = _phase_block(_PHASE_E1, _TEST_E1, block)
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any("How we'll check" in e["message"] for e in result["errors"]), (
            f"Expected an error mentioning the missing How we'll check sub-bullet; "
            f"got: {result['errors']}"
        )

    def test_empty_failure_language_body_fails(self):
        """Empty body on **If this fails, the user sees** must fail. Stage C
        only pinned this for **User-observable**."""
        block = (
            "  **Behavioral Verification:**\n"
            "  - **User-observable:** Something visible.\n"
            "  - **How we'll check:** Look at it.\n"
            "  - **If this fails, the user sees:** \n"
        )
        content = _phase_block(_PHASE_E1, _TEST_E1, block)
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any(
            "If this fails" in e["message"] or "failure" in e["message"].lower()
            for e in result["errors"]
        ), f"Expected an error mentioning the missing failure-language sub-bullet; got: {result['errors']}"

    def test_bv_block_deep_in_phase_passes(self):
        """A full 4-line BV block whose LAST sub-bullet sits ~30 lines below the
        phase header is accepted — it is within the (single) phase's section.

        Construction: phase on line 1, test on line 2, 25 filler lines (3-27),
        BV header on line 28, sub-bullets on 29/30/31.
        """
        filler = "\n".join(["  > Note: filler"] * 25)
        content = (
            _PHASE_E1 + "\n"
            + _TEST_E1 + "\n"
            + filler + "\n"
            + _full_bv_block()
        )
        result = _validate_roadmap_content(content)
        assert result["valid"] is True, (
            f"a complete BV block within the phase section must be accepted; "
            f"got errors: {result['errors']}"
        )

    def test_bv_block_far_past_old_window_passes_within_phase(self):
        """A complete BV block ~31+ lines below the header — one past the retired
        30-line window — must now be ACCEPTED, because it is still within the
        phase's own section. This is the exact live Tick-Tac-Toe TEST-E1 shape (a
        long Done-Criteria list pushing the canonical 'BV block last' just past
        the old window). Pins the phase-bounded model that replaced the fixed
        window; the prior ``test_bv_block_ending_at_phase_plus_31_fails`` asserted
        the opposite and was retired with the window.
        """
        filler = "\n".join(["  - [ ] done criterion"] * 30)
        content = (
            _PHASE_E1 + "\n"
            + _TEST_E1 + "\n"
            + filler + "\n"
            + _full_bv_block()
        )
        result = _validate_roadmap_content(content)
        assert result["valid"] is True, (
            f"a complete BV block past the old 30-line window but within its phase "
            f"section must be accepted; got errors: {result['errors']}"
        )

    def test_bv_block_in_next_phase_does_not_satisfy_prior_phase(self):
        """Phase-boundary guard: the search is bounded by the phase section, so a
        block that belongs to the NEXT phase cannot be borrowed to satisfy a prior
        phase that has none of its own."""
        content = (
            _PHASE_E1 + "\n" + _TEST_E1 + "\n\n"
            + _PHASE_E2 + "\n" + _TEST_E2 + "\n" + _full_bv_block()
        )
        result = _validate_roadmap_content(content)
        assert result["valid"] is False, (
            "UI-E1 has no BV block in its own section; UI-E2's must not count"
        )
        assert any(
            "UI-E1" in e["message"] and "Behavioral Verification" in e["message"]
            for e in result["errors"]
        ), f"Expected a missing-BV error attributed to UI-E1; got: {result['errors']}"

    def test_duplicate_phase_ids_with_bv_blocks_returns_duplicate_error(self):
        """Two phases sharing a phase ID, both carrying full BV blocks.

        Existing duplicate-ID coverage in
        ``tests/test_api_setup_validate_roadmap.py`` predates Stage C and
        does not include BV blocks. This pin confirms the duplicate
        detector still fires when the BV machinery has no other complaint.
        """
        duplicate = "- [ ] `UI-E1` | HIGH | A different phase with the same ID"
        content = (
            _phase_block(_PHASE_E1, _TEST_E1, _full_bv_block())
            + "\n"
            + _phase_block(
                duplicate,
                "  > Test: Something else.",
                _full_bv_block(user="Different.", how="Different check.", fail="Different failure."),
            )
        )
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any(
            "Duplicate phase ID" in e["message"] and "UI-E1" in e["message"]
            for e in result["errors"]
        ), f"Expected duplicate-ID error for UI-E1; got: {result['errors']}"

    def test_phase_id_in_entry_criteria_body_not_treated_as_duplicate(self):
        """Phase ID appearing inside an Entry Criteria body must not count as a
        duplicate phase header.

        Anchors ``_PHASE_LINE_RE`` against a regression that loosened the
        start-of-line guard (e.g. removing the ``^- \\[.\\] `` anchor or
        switching to ``re.search``). Without the anchor, the inline
        ``\\`CORE-E1\\` complete`` reference would false-positive.
        """
        first = _phase_block(
            "- [ ] `CORE-E1` | LOW | First phase",
            "  > Test: First phase ok.",
            _full_bv_block(user="First visible.", how="First check.", fail="First broken."),
        )
        second_with_reference = (
            "- [ ] `CORE-E2` | LOW | Second phase\n"
            "  > Test: Second phase ok.\n"
            "  **Entry Criteria:** `CORE-E1` complete.\n"
            "  **Exit Criteria:** All `CORE-E1` deliverables remain green.\n"
            + _full_bv_block(user="Second visible.", how="Second check.", fail="Second broken.")
        )
        result = _validate_roadmap_content(first + "\n" + second_with_reference)
        # No duplicate-ID error for CORE-E1.
        duplicate_errors = [e for e in result["errors"] if "Duplicate phase ID" in e["message"]]
        assert duplicate_errors == [], (
            f"CORE-E1 reference inside Entry/Exit Criteria body must not be counted as a "
            f"duplicate phase header. Errors: {duplicate_errors}"
        )
        # And the overall roadmap is valid (no other errors expected).
        assert result["valid"] is True, f"Expected valid; errors: {result['errors']}"

    def test_pre_p0_legacy_roadmap_three_phases_all_flagged(self):
        """Cutover regression pin: a 3-phase pre-P0 roadmap (no BV blocks
        anywhere) must produce a BV error for EVERY phase.

        This is the no-legacy-mode gate. If anyone reintroduces a
        ``legacy=True`` opt-out (whether as a kwarg or as a per-phase skip),
        this test fires. The Stage C
        ``test_two_phases_one_missing_block_flags_only_offender`` pins the
        single-phase-failed case; this pins the all-phases-failed case so
        the gate's strictness is enforced at scale.
        """
        legacy = (
            _phase_block(_PHASE_E1, _TEST_E1)  # no BV
            + "\n"
            + _phase_block("- [ ] `UI-E2` | MEDIUM | Second phase", "  > Test: ok.")  # no BV
            + "\n"
            + _phase_block("- [ ] `UI-E3` | HIGH | Third phase", "  > Test: ok.")  # no BV
        )
        result = _validate_roadmap_content(legacy)
        assert result["valid"] is False
        bv_errors = [e for e in result["errors"] if "Behavioral Verification" in e["message"]]
        assert len(bv_errors) == 3, (
            f"Expected a BV error for each of 3 pre-P0 phases. Got {len(bv_errors)} "
            f"BV errors out of {len(result['errors'])} total: {result['errors']}"
        )
        # Each phase ID must appear in at least one error message.
        for pid in ("UI-E1", "UI-E2", "UI-E3"):
            assert any(pid in e["message"] for e in bv_errors), (
                f"Expected a BV error referencing phase {pid}; got: {bv_errors}"
            )


# ---------------------------------------------------------------------------
# TestFormatCorrectionFixturePairs — sample-fixture validator-only smoke
# for Stage K Gap C part 1.
# ---------------------------------------------------------------------------


class TestFormatCorrectionFixturePairs:
    """Pins what failure and success look like for the format-correction
    contract, without exercising the LLM. Pairs a pre-P0 legacy roadmap
    with a structurally-correct post-correction shape matching
    ``autodev/skill-library/roadmap-converter/format-correction/SKILL.md``
    lines 146-158 (How we'll check from > Test, the other two inferred and
    marked ``<!-- TODO: human-review -->``).

    These fixtures are imported by ``test_p0_stage_k_format_correction_smoke.py``
    as the canned input/output the endpoint smoke pumps through a mocked
    LLM. Keeping them here (not in conftest) keeps the validator-only
    assertions next to the fixture they pin.
    """

    def test_pre_p0_fixture_fails_validation(self):
        """The pre-P0 legacy fixture must fail validation with a BV error
        for each of its 5 phases. Documents what failure looks like for
        the real-world input the format-correction agent receives."""
        result = _validate_roadmap_content(_PRE_P0_ROADMAP_FIXTURE)
        assert result["valid"] is False
        bv_errors = [e for e in result["errors"] if "Behavioral Verification" in e["message"]]
        assert len(bv_errors) == 5, (
            f"Expected 5 BV errors (one per phase) for the pre-P0 fixture. "
            f"Got {len(bv_errors)}: {bv_errors}"
        )

    def test_post_correction_fixture_passes_validation(self):
        """The post-correction fixture must pass strict validation.

        Documents the success target the format-correction agent is asked
        to produce: each phase gets a Behavioral Verification block whose
        How-we'll-check field restates the existing > Test: line and whose
        other two fields are inferred from phase description + exit
        criteria, marked ``<!-- TODO: human-review -->``.
        """
        result = _validate_roadmap_content(_POST_CORRECTION_ROADMAP_FIXTURE)
        assert result["valid"] is True, (
            f"Post-correction fixture must pass strict validation; errors: {result['errors']}"
        )

    def test_post_correction_fixture_retains_todo_markers_for_human_review(self):
        """The post-correction fixture must mark every inferred field with
        ``<!-- TODO: human-review -->`` so a human can vet the inference
        before queueing.

        2 inferred fields per phase (User-observable, If this fails) ×
        5 phases = 10 markers minimum. Anything less means the fixture
        (or the SKILL.md contract it pins) silently dropped the
        human-review affordance.
        """
        marker_count = _POST_CORRECTION_ROADMAP_FIXTURE.count("<!-- TODO: human-review -->")
        assert marker_count >= 10, (
            f"Expected at least 10 <!-- TODO: human-review --> markers "
            f"(2 inferred fields × 5 phases). Got {marker_count}. "
            f"If this dropped, the operator-affordance for human review of "
            f"format-correction output is gone."
        )


# ---------------------------------------------------------------------------
# Defensive cross-check — ensure module-level fixtures are exported under
# stable names. The format-correction endpoint smoke imports these by name;
# if anyone renames them, the import would fail at module load (not at test
# collection time), so this guard surfaces the regression at the same site
# the fixtures are defined.
# ---------------------------------------------------------------------------


def test_module_exports_format_correction_fixtures():
    """The two module-level fixture constants must remain importable by name
    from this module. ``tests/test_p0_stage_k_format_correction_smoke.py``
    imports them; a rename would break that file silently."""
    import tests.test_p0_validate_roadmap_strict as mod
    assert hasattr(mod, "_PRE_P0_ROADMAP_FIXTURE")
    assert hasattr(mod, "_POST_CORRECTION_ROADMAP_FIXTURE")
    assert isinstance(mod._PRE_P0_ROADMAP_FIXTURE, str)
    assert isinstance(mod._POST_CORRECTION_ROADMAP_FIXTURE, str)
