"""Tests for _extract_summary helper."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.server import _extract_summary


class TestExtractSummary:
    def test_has_problem_statement_with_sentence(self):
        """Returns first sentence after ## Problem Statement."""
        prd = """# Project Name

## Problem Statement
This is the first sentence. This is the second sentence.

## Goals
Goal one.
"""
        result = _extract_summary(prd)
        assert result == "This is the first sentence"

    def test_has_problem_statement_no_period(self):
        """Returns line up to end when no period present."""
        prd = """# Project

## Problem Statement
This is a summary line with no period

## Goals
Goal one.
"""
        result = _extract_summary(prd)
        assert result == "This is a summary line with no period"

    def test_missing_section(self):
        """Returns empty string when ## Problem Statement section is absent."""
        prd = """# Project Name

## Goals
Goal one.
"""
        result = _extract_summary(prd)
        assert result == ""

    def test_blank_section(self):
        """Returns empty string when ## Problem Statement is blank/empty."""
        prd = """# Project Name

## Problem Statement


## Goals
Goal one.
"""
        result = _extract_summary(prd)
        assert result == ""

    def test_empty_string(self):
        """Returns empty string for empty input."""
        result = _extract_summary("")
        assert result == ""

    def test_problem_statement_at_end_of_file(self):
        """Works when ## Problem Statement is the last content."""
        prd = """# Project

## Problem Statement
Final sentence here."""
        result = _extract_summary(prd)
        assert result == "Final sentence here"

    def test_whitespace_after_heading_stripped(self):
        """Leading whitespace after ## Problem Statement is stripped."""
        prd = """# Project

## Problem Statement
   Leading whitespace sentence.

## Goals
"""
        result = _extract_summary(prd)
        assert result == "Leading whitespace sentence"
