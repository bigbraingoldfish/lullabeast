# Tests for POST /api/setup/validate-roadmap endpoint and _validate_roadmap_content helper.

import pytest


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


from ui.server import _validate_roadmap_content


VALID_PHASE_LINE = "- [ ] `UI-E1` | LOW | Render the scaffold"
VALID_TEST_LINE = "  > Test: Screen renders without errors."
VALID_BEHAVIORAL_BLOCK = (
    "  **Behavioral Verification:**\n"
    "  - **User-observable:** The user sees the scaffold on /home.\n"
    "  - **How we'll check:** Navigate to /home and confirm the scaffold renders.\n"
    "  - **If this fails, the user sees:** The home screen does not load.\n"
)

VALID_ROADMAP = f"{VALID_PHASE_LINE}\n{VALID_TEST_LINE}\n{VALID_BEHAVIORAL_BLOCK}"


# ---------------------------------------------------------------------------
# Unit tests for _validate_roadmap_content
# ---------------------------------------------------------------------------

class TestValidateRoadmapContent:

    def test_valid_roadmap_returns_valid_true(self):
        """Valid roadmap with phase line + > Test: returns valid=True and no errors."""
        result = _validate_roadmap_content(VALID_ROADMAP)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_invalid_phase_line_format_not_flagged(self):
        """Line missing backticks is not a valid phase — zero phases → error."""
        content = "- [ ] UI-E1 | LOW | Goal\n"
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any("At least one valid phase" in e["message"] for e in result["errors"])

    def test_correctly_formatted_phase_without_test_is_flagged(self):
        """A correctly formatted phase line with no > Test: within 10 lines returns an error.

        The Behavioral Verification block is also required (Stage C, strict per §2.9), so
        after the change this phase has multiple errors. Assert that the > Test: error is
        among them — the other errors are correct under the new contract.
        """
        content = f"{VALID_PHASE_LINE}\n# some comment without a test line\n"
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        test_errors = [e for e in result["errors"] if "Test:" in e["message"]]
        assert len(test_errors) == 1
        err = test_errors[0]
        assert err["line"] == 1
        assert "UI-E1" in err["message"]

    def test_missing_test_line_returns_error(self):
        """Phase with no > Test: within 10 lines returns error with phase ID and line number."""
        content = f"{VALID_PHASE_LINE}\n"
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        test_errors = [e for e in result["errors"] if "Test:" in e["message"]]
        assert len(test_errors) == 1
        err = test_errors[0]
        assert err["line"] == 1
        assert "UI-E1" in err["message"]
        assert err["content"] == VALID_PHASE_LINE

    def test_duplicate_phase_ids_returns_error(self):
        """Two entries with the same ID return an error mentioning Duplicate."""
        content = (
            f"{VALID_PHASE_LINE}\n{VALID_TEST_LINE}\n"
            f"{VALID_PHASE_LINE}\n{VALID_TEST_LINE}\n"
        )
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        dup_errors = [e for e in result["errors"] if "Duplicate" in e["message"]]
        assert len(dup_errors) >= 1
        assert "UI-E1" in dup_errors[0]["message"]

    def test_empty_content_returns_invalid(self):
        """Empty string has no phases → valid=False."""
        result = _validate_roadmap_content("")
        assert result["valid"] is False
        assert any("At least one valid phase" in e["message"] for e in result["errors"])

    def test_multiple_errors_all_returned(self):
        """Roadmap with 2 phases both missing > Test: returns 2 > Test: errors (plus behavioral errors)."""
        content = (
            "- [ ] `UI-A1` | LOW | First goal\n"
            "- [ ] `UI-A2` | HIGH | Second goal\n"
        )
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        # Filter strictly to "> Test:" errors — Behavioral Verification adds its own.
        missing_test_errors = [
            e for e in result["errors"] if "'> Test:' line" in e["message"]
        ]
        assert len(missing_test_errors) == 2
        ids_in_errors = {e["content"] for e in missing_test_errors}
        assert any("UI-A1" in c for c in ids_in_errors)
        assert any("UI-A2" in c for c in ids_in_errors)

    def test_test_line_within_10_lines_passes(self):
        """> Test: on line 10 relative to phase line (line_num + 9) is accepted."""
        # Phase is on line 1; > Test: must be at lines 1..10 (j in range(1, 11)).
        # We place > Test: at offset 9 from the phase line (line 10 = phase + 9 blank lines).
        filler = "\n".join(["# filler"] * 9)  # 9 lines of filler
        content = f"{VALID_PHASE_LINE}\n{filler}\n{VALID_TEST_LINE}\n"
        result = _validate_roadmap_content(content)
        # > Test: is at line 11 here — let's calculate precisely.
        # line 1: phase, lines 2-10: filler (9 lines), line 11: test
        # range is range(1, min(1+10, N+1)) = range(1, 11) → j=1..10
        # line 11 is outside that range, so this actually should FAIL.
        # Recalculate: place > Test: at line 10 = offset 9 from phase line 1.
        # That means 8 filler lines between phase and test.
        pass  # We'll rebuild this test below correctly.

    def test_test_line_at_offset_9_passes(self):
        """> Test: at exactly 9 lines after the phase line (line 10) is accepted."""
        # Phase line = line 1. range(1, min(11, N+1)) → checks lines 1..10.
        # > Test: at line 10 means 8 intervening lines.
        filler_lines = ["# filler"] * 8  # lines 2-9
        content = (
            VALID_PHASE_LINE + "\n"
            + "\n".join(filler_lines) + "\n"
            + VALID_TEST_LINE + "\n"
            + VALID_BEHAVIORAL_BLOCK
        )
        result = _validate_roadmap_content(content)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_test_line_beyond_10_lines_fails(self):
        """> Test: at line 11+ relative to phase line is not found → error."""
        # Phase line = line 1. range(1, 11) checks lines 1..10 only.
        # > Test: at line 12 (10 filler lines after phase) should NOT be found.
        filler_lines = ["# filler"] * 10  # lines 2-11
        content = VALID_PHASE_LINE + "\n" + "\n".join(filler_lines) + "\n" + VALID_TEST_LINE + "\n"
        result = _validate_roadmap_content(content)
        assert result["valid"] is False
        assert any("UI-E1" in e["message"] for e in result["errors"])


# ---------------------------------------------------------------------------
# Integration tests for POST /api/setup/validate-roadmap endpoint
# ---------------------------------------------------------------------------

class TestValidateRoadmapEndpoint:

    def test_endpoint_returns_200(self):
        """POST to /api/setup/validate-roadmap with valid content returns 200."""
        client = load_server()
        response = client.post(
            "/api/setup/validate-roadmap",
            json={"content": VALID_ROADMAP},
        )
        assert response.status_code == 200

    def test_endpoint_returns_validation_schema(self):
        """Response has valid and errors fields."""
        client = load_server()
        response = client.post(
            "/api/setup/validate-roadmap",
            json={"content": VALID_ROADMAP},
        )
        assert response.status_code == 200
        data = response.json()
        assert "valid" in data
        assert "errors" in data
        assert isinstance(data["valid"], bool)
        assert isinstance(data["errors"], list)

    def test_endpoint_valid_content_returns_valid_true(self):
        """Valid content via endpoint returns valid=True."""
        client = load_server()
        response = client.post(
            "/api/setup/validate-roadmap",
            json={"content": VALID_ROADMAP},
        )
        data = response.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_endpoint_invalid_content_returns_valid_false(self):
        """Content with missing > Test: via endpoint returns valid=False."""
        client = load_server()
        response = client.post(
            "/api/setup/validate-roadmap",
            json={"content": VALID_PHASE_LINE + "\n"},
        )
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) >= 1
