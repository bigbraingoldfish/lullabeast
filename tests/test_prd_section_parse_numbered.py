"""
TDD tests for numbered markdown heading support in PRD section parsing.

The prd-creator model frequently emits headings like:

    ## 1. Problem Statement
    ## 2. Goals & Success Metrics
    ### 3. User Stories

Both the Python parser (_parse_prd_sections) and the frontend parsePrdSections
must normalise the leading "N." so these map to canonical section titles.

Run before implementing the fix to confirm these fail:
    pytest tests/test_prd_section_parse_numbered.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.server import (
    PRD_SECTION_TITLES,
    _build_prd_section_diff_payload,
    _parse_prd_sections,
    _replace_prd_section_body,
    _slugify_section,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NUMBERED_PRD = """\
# LLN Lab — Law of Large Numbers Interactive Visualizer

## 1. Problem Statement
Probability theory is abstract. People struggle to build intuition.

## 2. Goals & Success Metrics
Primary goal: users understand LLN viscerally.

### 3. User Stories
- US-1: As a student I can run 10,000 coin flips and watch convergence.

## 4. Functional Requirements
4.1 Core layout with three panels.

## 5. Edge Cases
What if the user runs 0 trials?

## 6. Non-Functional Requirements
Fast mode sustains 60 fps.

## 7. Dependencies & Integrations
None — pure HTML/JS.

## 8. Milestones & Timeline
Target 10 phases.

## 9. Risks & Mitigations
Animation jank on low-end hardware.

## 10. Open Questions
Coin face style TBD.

## 11. Glossary & Domain Terms
LLN: Law of Large Numbers.

## 12. Revision History
2026-04-24 Initial draft.
"""

NUMBERED_PRD_V2 = """\
# LLN Lab v2

## 1. Problem Statement
Updated problem description.

## 2. Goals & Success Metrics
Updated goals.

## 3. User Stories
- US-1 updated story.

## 4. Functional Requirements
Updated requirements.

## 5. Edge Cases
Updated edge cases.

## 6. Non-Functional Requirements
Updated NFR.

## 7. Dependencies & Integrations
Updated deps.

## 8. Milestones & Timeline
Updated milestones.

## 9. Risks & Mitigations
Updated risks.

## 10. Open Questions
No open questions now.

## 11. Glossary & Domain Terms
Updated glossary.

## 12. Revision History
2026-04-25 Second draft.
"""


# ---------------------------------------------------------------------------
# _parse_prd_sections — numbered headings
# ---------------------------------------------------------------------------

class TestParsePrdSectionsNumberedHeadings:
    def test_numbered_h2_problem_statement(self):
        out = _parse_prd_sections(NUMBERED_PRD)
        body = (out.get("Problem Statement") or "").strip()
        assert body, (
            "_parse_prd_sections returned empty body for 'Problem Statement' "
            "when heading was '## 1. Problem Statement'"
        )
        assert "abstract" in body

    def test_numbered_h2_goals(self):
        out = _parse_prd_sections(NUMBERED_PRD)
        body = (out.get("Goals & Success Metrics") or "").strip()
        assert body, "Empty 'Goals & Success Metrics' for '## 2. Goals & Success Metrics'"
        assert "viscerally" in body

    def test_numbered_h3_user_stories(self):
        """H3 depth is in #{1,3} range — must also normalise numbered prefix."""
        out = _parse_prd_sections(NUMBERED_PRD)
        body = (out.get("User Stories") or "").strip()
        assert body, "Empty 'User Stories' for '### 3. User Stories'"
        assert "US-1" in body

    def test_all_twelve_sections_populated(self):
        out = _parse_prd_sections(NUMBERED_PRD)
        missing = [t for t in PRD_SECTION_TITLES if not (out.get(t) or "").strip()]
        assert not missing, f"Sections still empty after numbered-heading fix: {missing}"

    def test_unnumbered_headings_still_work(self):
        """Regression: existing unnumbered format must keep working."""
        md = (
            "## Problem Statement\nSome problem\n\n"
            "## Goals & Success Metrics\nSome goal\n"
        )
        out = _parse_prd_sections(md)
        assert "Some problem" in (out.get("Problem Statement") or "")
        assert "Some goal" in (out.get("Goals & Success Metrics") or "")

    def test_mixed_numbered_and_unnumbered(self):
        """A PRD that mixes styles (some numbered, some not) should parse both."""
        md = (
            "## 1. Problem Statement\nMixed problem\n\n"
            "## Goals & Success Metrics\nUnnumbered goal\n"
        )
        out = _parse_prd_sections(md)
        assert "Mixed problem" in (out.get("Problem Statement") or "")
        assert "Unnumbered goal" in (out.get("Goals & Success Metrics") or "")

    def test_extra_spaces_between_number_and_title_normalised(self):
        """Handle '##  1.  Problem Statement' (extra spaces) gracefully."""
        md = "## 1.  Problem Statement\nSpacey\n"
        out = _parse_prd_sections(md)
        assert "Spacey" in (out.get("Problem Statement") or "")

    def test_case_insensitive_after_strip(self):
        """After stripping N. the canonical match is still case-insensitive."""
        md = "## 1. problem statement\nlower case\n"
        out = _parse_prd_sections(md)
        assert "lower case" in (out.get("Problem Statement") or "")


# ---------------------------------------------------------------------------
# _build_prd_section_diff_payload — numbered headings
# ---------------------------------------------------------------------------

class TestDiffPayloadNumberedHeadings:
    def test_numbered_sections_show_as_added_when_no_previous(self):
        data = _build_prd_section_diff_payload(NUMBERED_PRD, None)
        secs = data["sections"]
        sk = _slugify_section("Problem Statement")
        assert sk in secs, "Problem Statement not in diff sections for numbered PRD"
        assert secs[sk]["status"] == "added"
        assert secs[sk]["current"] is not None
        assert "abstract" in secs[sk]["current"]

    def test_numbered_sections_diff_shows_modified(self):
        data = _build_prd_section_diff_payload(NUMBERED_PRD_V2, NUMBERED_PRD)
        secs = data["sections"]
        sk = _slugify_section("Problem Statement")
        assert sk in secs, "Problem Statement missing from modified diff"
        assert secs[sk]["status"] == "modified"
        assert "abstract" in (secs[sk]["previous"] or "")
        assert "Updated problem" in (secs[sk]["current"] or "")

    def test_all_twelve_sections_appear_in_added_diff(self):
        data = _build_prd_section_diff_payload(NUMBERED_PRD, None)
        secs = data["sections"]
        missing = [
            t for t in PRD_SECTION_TITLES
            if _slugify_section(t) not in secs
        ]
        assert not missing, f"Diff missing sections for numbered PRD: {missing}"


# ---------------------------------------------------------------------------
# _replace_prd_section_body — numbered headings (for revert)
# ---------------------------------------------------------------------------

class TestReplacePrdSectionBodyNumberedHeadings:
    def test_replace_finds_numbered_section(self):
        """Revert must locate and replace a numbered heading's body."""
        original = (
            "## 1. Problem Statement\nOld body\n\n"
            "## 2. Goals & Success Metrics\nGoals body\n"
        )
        result = _replace_prd_section_body(original, "Problem Statement", "New body")
        assert "New body" in result
        # Other sections must be preserved
        assert "Goals body" in result

    def test_replace_preserves_original_heading_line(self):
        """The heading line format (numbered) should be preserved as-is."""
        original = "## 1. Problem Statement\nOld\n"
        result = _replace_prd_section_body(original, "Problem Statement", "New")
        assert "## 1. Problem Statement" in result
