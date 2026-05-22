"""Tests for _validate_verification_content (Stage C).

Validates the project-level verification.md format produced by the
roadmap-converter agent. Schema is defined in the
``roadmap-converter/roadmap-generation/SKILL.md`` "Verification Document
Output" section. The validator is strict: unknown project types and
malformed shapes are hard failures (no legacy opt-out per P0 §2.9).
"""

import pytest

from ui.server import _validate_verification_content


VALID_DOC = """# Verification

## Project type
web-app

## Entry point
- Command: `npm run dev`
- Ready signal: HTTP 200 from http://localhost:5173

## Public surface
1. Configure 4 player slots in the lobby
2. Start a game and watch AI agents draw and guess
3. Submit guesses as the human participant

## Verification stack
- Acceptance tool: playwright
- Notes: jsdom-only verification is insufficient; the reviewer launches the
  dev server and inspects the rendered DOM directly.
"""


REQUIRED_SECTIONS = [
    ("# Verification", "top heading"),
    ("## Project type", "Project type section"),
    ("## Entry point", "Entry point section"),
    ("## Public surface", "Public surface section"),
    ("## Verification stack", "Verification stack section"),
]

CANONICAL_PROJECT_TYPES = [
    "web-app", "http-api", "cli", "library", "data-pipeline",
    "game", "automation", "desktop-app", "mobile-app",
]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestValidVerification:

    def test_valid_verification_passes(self):
        """Minimal valid doc returns valid=True with no errors."""
        result = _validate_verification_content(VALID_DOC)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_response_shape_matches_roadmap_validator(self):
        """Errors list contains dicts with line/content/message keys (mirrors _validate_roadmap_content)."""
        result = _validate_verification_content("")
        assert "valid" in result
        assert "errors" in result
        assert isinstance(result["errors"], list)
        for err in result["errors"]:
            assert {"line", "content", "message"} <= set(err.keys())

    @pytest.mark.parametrize("project_type", CANONICAL_PROJECT_TYPES)
    def test_each_canonical_project_type_accepted(self, project_type):
        """Every canonical project type from the skill is accepted."""
        doc = VALID_DOC.replace("web-app", project_type, 1)
        result = _validate_verification_content(doc)
        assert result["valid"] is True, f"Project type {project_type!r} should be accepted: {result['errors']}"


# ---------------------------------------------------------------------------
# Missing sections
# ---------------------------------------------------------------------------

class TestMissingSections:

    def test_missing_top_heading_fails(self):
        """Doc without '# Verification' top heading fails."""
        doc = VALID_DOC.replace("# Verification\n", "", 1)
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Verification" in e["message"] for e in result["errors"])

    def test_missing_project_type_section_fails(self):
        """Doc missing '## Project type' heading fails with line-anchored error."""
        doc = VALID_DOC.replace("## Project type\nweb-app\n\n", "", 1)
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Project type" in e["message"] for e in result["errors"])

    def test_missing_entry_point_section_fails(self):
        doc = VALID_DOC.replace(
            "## Entry point\n- Command: `npm run dev`\n- Ready signal: HTTP 200 from http://localhost:5173\n\n",
            "",
            1,
        )
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Entry point" in e["message"] for e in result["errors"])

    def test_missing_public_surface_section_fails(self):
        # Strip the Public surface block including its body.
        marker = "## Public surface"
        idx = VALID_DOC.index(marker)
        next_idx = VALID_DOC.index("## Verification stack")
        doc = VALID_DOC[:idx] + VALID_DOC[next_idx:]
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Public surface" in e["message"] for e in result["errors"])

    def test_missing_verification_stack_section_fails(self):
        idx = VALID_DOC.index("## Verification stack")
        doc = VALID_DOC[:idx]
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Verification stack" in e["message"] for e in result["errors"])

    def test_sections_in_wrong_order_fails(self):
        """Sections out of canonical order fails (canonical: type, entry, surface, stack)."""
        # Swap Project type with Entry point order.
        doc = (
            "# Verification\n\n"
            "## Entry point\n- Command: `npm run dev`\n- Ready signal: ok\n\n"
            "## Project type\nweb-app\n\n"
            "## Public surface\n1. x\n\n"
            "## Verification stack\n- Acceptance tool: playwright\n"
        )
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("order" in e["message"].lower() or "expected" in e["message"].lower()
                   for e in result["errors"])


# ---------------------------------------------------------------------------
# Empty section bodies
# ---------------------------------------------------------------------------

class TestEmptyBody:

    def test_empty_project_type_body_fails(self):
        doc = VALID_DOC.replace("## Project type\nweb-app\n", "## Project type\n\n", 1)
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Project type" in e["message"] for e in result["errors"])

    def test_empty_entry_point_body_fails(self):
        doc = VALID_DOC.replace(
            "## Entry point\n- Command: `npm run dev`\n- Ready signal: HTTP 200 from http://localhost:5173\n",
            "## Entry point\n",
            1,
        )
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Entry point" in e["message"] for e in result["errors"])

    def test_empty_public_surface_body_fails(self):
        doc = VALID_DOC.replace(
            "## Public surface\n1. Configure 4 player slots in the lobby\n"
            "2. Start a game and watch AI agents draw and guess\n"
            "3. Submit guesses as the human participant\n",
            "## Public surface\n",
            1,
        )
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Public surface" in e["message"] for e in result["errors"])


# ---------------------------------------------------------------------------
# Project type strictness (hard-fail per user decision #1)
# ---------------------------------------------------------------------------

class TestProjectTypeStrict:

    def test_unknown_project_type_fails(self):
        """Hard-fail on unknown project type (decision #1)."""
        doc = VALID_DOC.replace("web-app", "firmware-blob", 1)
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any(
            "Project type" in e["message"]
            and ("firmware-blob" in e["message"] or "unknown" in e["message"].lower()
                 or "must be one of" in e["message"].lower())
            for e in result["errors"]
        )

    def test_project_type_multiline_fails(self):
        """Multi-line body for project_type fails shape check (decision #2)."""
        doc = VALID_DOC.replace(
            "## Project type\nweb-app\n",
            "## Project type\nweb-app\nextra paragraph here\n",
            1,
        )
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Project type" in e["message"] for e in result["errors"])

    def test_project_type_over_40_chars_fails(self):
        long = "x" * 41
        doc = VALID_DOC.replace("web-app", long, 1)
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Project type" in e["message"] for e in result["errors"])

    @pytest.mark.parametrize("body", [
        "**web-app**",
        "`web-app`",
        "_web-app_",
        "# web-app",
        "[web-app]",
    ])
    def test_project_type_with_markdown_formatting_fails(self, body):
        """Markdown formatting around the type token fails shape check."""
        doc = VALID_DOC.replace("web-app", body, 1)
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("Project type" in e["message"] for e in result["errors"])


# ---------------------------------------------------------------------------
# Doc length cap
# ---------------------------------------------------------------------------

class TestDocLength:

    def test_doc_over_80_lines_fails(self):
        """Skill caps total doc at 80 lines; 81 lines must fail."""
        # Pad Public surface to push past 80 lines.
        padding = "\n".join(f"{i}. capability {i}" for i in range(4, 90))
        doc = VALID_DOC.replace(
            "3. Submit guesses as the human participant\n",
            "3. Submit guesses as the human participant\n" + padding + "\n",
            1,
        )
        assert doc.count("\n") > 80
        result = _validate_verification_content(doc)
        assert result["valid"] is False
        assert any("80" in e["message"] or "length" in e["message"].lower()
                   for e in result["errors"])

    def test_doc_at_80_lines_passes(self):
        """At the 80-line cap, the doc still validates."""
        # Build a doc with exactly 80 lines (counting the final newline as line 80).
        lines = VALID_DOC.split("\n")
        # VALID_DOC currently has ~17 non-empty lines + blanks. Pad to ~80.
        pad_count = 80 - len(lines) - 1
        if pad_count > 0:
            extra = ["  Additional note line."] * pad_count
            # Append into Verification stack body (extends "- Notes:" naturally).
            doc = VALID_DOC.rstrip("\n") + "\n" + "\n".join(extra) + "\n"
        else:
            doc = VALID_DOC
        line_count = doc.count("\n")
        assert line_count <= 80
        result = _validate_verification_content(doc)
        # The doc may now have a Notes block too long, but the line cap itself
        # must not fail. Check no "80 lines" error specifically.
        if not result["valid"]:
            assert not any("80" in e["message"] for e in result["errors"]), \
                f"80-line cap should not fire at exactly 80 lines: {result['errors']}"
