"""Tests for the Behavioral Verification extension to _validate_roadmap_content (Stage C).

Every phase in the roadmap must declare a Behavioral Verification block within
30 lines of its checkbox header. The block has exactly three sub-bullets, in
any order, each with non-empty body. Strict from day one — no legacy opt-out.
"""

import inspect

import pytest

from ui.server import _validate_roadmap_content


PHASE_LINE = "- [ ] `UI-E1` | LOW | Render the scaffold"
TEST_LINE = "  > Test: Screen renders without errors."

BEHAVIORAL_BLOCK = (
    "  **Behavioral Verification:**\n"
    "  - **User-observable:** The user sees the scaffold rendered on /home.\n"
    "  - **How we'll check:** Navigate to /home, expect the scaffold container present.\n"
    "  - **If this fails, the user sees:** The home screen does not load.\n"
)


def _phase_with_full_block() -> str:
    """A canonical roadmap entry with both the > Test: line and the Behavioral Verification block."""
    return PHASE_LINE + "\n" + TEST_LINE + "\n" + BEHAVIORAL_BLOCK


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestBehavioralPresent:

    def test_full_block_accepted(self):
        result = _validate_roadmap_content(_phase_with_full_block())
        assert result["valid"] is True, f"Expected valid; errors: {result['errors']}"
        assert result["errors"] == []

    def test_block_within_30_lines_accepted(self):
        """Block 25 lines after phase header is still found (within the 30-line window)."""
        filler = "\n".join(["  > Note: filler"] * 20)
        content = PHASE_LINE + "\n" + TEST_LINE + "\n" + filler + "\n" + BEHAVIORAL_BLOCK
        result = _validate_roadmap_content(content)
        assert result["valid"] is True, f"Expected valid; errors: {result['errors']}"

    def test_block_beyond_30_lines_fails(self):
        """Block placed past the 30-line window is missed and fails."""
        filler = "\n".join(["  > Note: filler"] * 31)
        content = PHASE_LINE + "\n" + TEST_LINE + "\n" + filler + "\n" + BEHAVIORAL_BLOCK
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any("Behavioral Verification" in e["message"] for e in result["errors"])

    def test_subbullets_in_any_order_accepted(self):
        """Sub-bullets do not need to appear in a fixed order."""
        reordered = (
            "  **Behavioral Verification:**\n"
            "  - **If this fails, the user sees:** Nothing loads.\n"
            "  - **User-observable:** A green button.\n"
            "  - **How we'll check:** Click the button; expect 200.\n"
        )
        content = PHASE_LINE + "\n" + TEST_LINE + "\n" + reordered
        result = _validate_roadmap_content(content)
        assert result["valid"] is True, f"Expected valid; errors: {result['errors']}"


# ---------------------------------------------------------------------------
# Missing block / sub-bullets
# ---------------------------------------------------------------------------

class TestMissingPieces:

    def test_phase_missing_behavioral_block_fails(self):
        """Old-format roadmap entry (no Behavioral Verification block) fails."""
        content = PHASE_LINE + "\n" + TEST_LINE + "\n"
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any("Behavioral Verification" in e["message"] for e in result["errors"])
        # Error must reference the phase ID so the operator can find it.
        assert any("UI-E1" in e["message"] for e in result["errors"])

    def test_phase_missing_user_observable_subbullet_fails(self):
        block_missing = (
            "  **Behavioral Verification:**\n"
            "  - **How we'll check:** Navigate to /home.\n"
            "  - **If this fails, the user sees:** The page does not load.\n"
        )
        content = PHASE_LINE + "\n" + TEST_LINE + "\n" + block_missing
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any("User-observable" in e["message"] for e in result["errors"])

    def test_phase_missing_how_to_check_subbullet_fails(self):
        block_missing = (
            "  **Behavioral Verification:**\n"
            "  - **User-observable:** Something.\n"
            "  - **If this fails, the user sees:** Something else.\n"
        )
        content = PHASE_LINE + "\n" + TEST_LINE + "\n" + block_missing
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any("How we'll check" in e["message"] for e in result["errors"])

    def test_phase_missing_failure_language_subbullet_fails(self):
        block_missing = (
            "  **Behavioral Verification:**\n"
            "  - **User-observable:** Something.\n"
            "  - **How we'll check:** Something.\n"
        )
        content = PHASE_LINE + "\n" + TEST_LINE + "\n" + block_missing
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any(
            "If this fails" in e["message"] or "failure" in e["message"].lower()
            for e in result["errors"]
        )

    def test_subbullet_with_empty_body_fails(self):
        """A sub-bullet header with no body content after the colon fails."""
        block_empty = (
            "  **Behavioral Verification:**\n"
            "  - **User-observable:** \n"
            "  - **How we'll check:** ok\n"
            "  - **If this fails, the user sees:** ok\n"
        )
        content = PHASE_LINE + "\n" + TEST_LINE + "\n" + block_empty
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any("User-observable" in e["message"] for e in result["errors"])


# ---------------------------------------------------------------------------
# Strict mode — no legacy opt-out (operator decision §2.9)
# ---------------------------------------------------------------------------

class TestStrictNoLegacyMode:

    def test_no_legacy_kwarg_exists(self):
        """Regression guard: no opt-out kwarg may be added to _validate_roadmap_content."""
        sig = inspect.signature(_validate_roadmap_content)
        params = sig.parameters
        # Validator takes exactly one positional `content` argument and nothing else.
        forbidden = {"legacy", "strict", "allow_legacy", "skip_behavioral"}
        assert not (forbidden & set(params.keys())), (
            f"Validator must not have a legacy opt-out kwarg. Found: {set(params.keys()) & forbidden}"
        )
        assert len(params) == 1, f"Expected single positional arg; got: {list(params)}"

    def test_two_phases_one_missing_block_flags_only_offender(self):
        """Mixed roadmap reports the offending phase but accepts the compliant one."""
        compliant = _phase_with_full_block()
        non_compliant = (
            "- [ ] `UI-E2` | MEDIUM | Second phase\n"
            "  > Test: Something.\n"
        )
        result = _validate_roadmap_content(compliant + non_compliant)
        assert result["valid"] is False
        ui_e2 = [e for e in result["errors"] if "UI-E2" in e["message"]]
        ui_e1 = [e for e in result["errors"] if "UI-E1" in e["message"]]
        assert len(ui_e2) >= 1
        # UI-E1 must NOT appear in errors related to Behavioral Verification
        # (it does have a full block).
        for e in ui_e1:
            assert "Behavioral Verification" not in e["message"]
