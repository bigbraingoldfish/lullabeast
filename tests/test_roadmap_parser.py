"""Tests for roadmap_parser.py - TDD test structure for CORE-1."""
import tempfile
from pathlib import Path


from ui.roadmap_parser import parse_roadmap


class TestParseRoadmapEmptyAbsent:
    """Tests for empty or absent file handling."""

    def test_empty_file_returns_empty_list(self):
        """parse_roadmap() returns [] for empty roadmap.md without raising."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            roadmap_path.write_text("")
            result = parse_roadmap(str(roadmap_path))
            assert result == []

    def test_absent_file_returns_empty_list(self):
        """parse_roadmap() returns [] for absent roadmap.md without raising."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "nonexistent.md"
            result = parse_roadmap(str(roadmap_path))
            assert result == []

    def test_file_with_only_whitespace(self):
        """parse_roadmap() returns [] for file with only whitespace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            roadmap_path.write_text("   \n\n   \n")
            result = parse_roadmap(str(roadmap_path))
            assert result == []


class TestParseRoadmapCheckboxStates:
    """Tests for checkbox state parsing."""

    def test_complete_status_x(self):
        """Line `- [x] `INFRA-1` | LOW | Goal text` parses to complete status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            roadmap_path.write_text("- [x] `INFRA-1` | LOW | Goal text")
            result = parse_roadmap(str(roadmap_path))
            assert len(result) == 1
            assert result[0]["id"] == "INFRA-1"
            assert result[0]["goal"] == "Goal text"
            assert result[0]["status"] == "complete"
            assert result[0]["exit_criteria"] == []

    def test_pending_status_space(self):
        """Line `- [ ] `API-1` | HIGH | Goal` parses with status='pending'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            roadmap_path.write_text("- [ ] `API-1` | HIGH | Goal")
            result = parse_roadmap(str(roadmap_path))
            assert len(result) == 1
            assert result[0]["id"] == "API-1"
            assert result[0]["status"] == "pending"

    def test_skipped_status_dash(self):
        """Line `- [-] `CORE-2` | LOW | Goal` parses with status='skipped'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            roadmap_path.write_text("- [-] `CORE-2` | LOW | Goal")
            result = parse_roadmap(str(roadmap_path))
            assert len(result) == 1
            assert result[0]["id"] == "CORE-2"
            assert result[0]["status"] == "skipped"

    def test_blocked_status_exclamation(self):
        """Line `- [!] `UI-1` | MEDIUM | Goal` parses with status='blocked'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            roadmap_path.write_text("- [!] `UI-1` | MEDIUM | Goal")
            result = parse_roadmap(str(roadmap_path))
            assert len(result) == 1
            assert result[0]["id"] == "UI-1"
            assert result[0]["status"] == "blocked"


class TestParseRoadmapExitCriteria:
    """Tests for exit criteria extraction."""

    def test_single_exit_criteria_line(self):
        """Exit criteria lines following a phase are collected into exit_criteria list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            content = """- [ ] `PHASE-1` | LOW | Do something
  > Test: This is a test criterion"""
            roadmap_path.write_text(content)
            result = parse_roadmap(str(roadmap_path))
            assert len(result) == 1
            assert result[0]["exit_criteria"] == ["This is a test criterion"]

    def test_multiple_exit_criteria_lines(self):
        """Multiple > lines produce multiple entries in exit_criteria array."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            content = """- [ ] `PHASE-1` | LOW | Do something
  > Test: First test
  > Notes: Some notes"""
            roadmap_path.write_text(content)
            result = parse_roadmap(str(roadmap_path))
            assert len(result) == 1
            assert len(result[0]["exit_criteria"]) == 2
            assert "First test" in result[0]["exit_criteria"]
            assert "Some notes" in result[0]["exit_criteria"]


class TestParseRoadmapNonPhaseLines:
    """Tests for filtering non-phase lines."""

    def test_header_lines_filtered(self):
        """Non-phase lines (headers) produce no output in returned list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            content = """# This is a header

## Another header

- [x] `PHASE-1` | LOW | Real phase"""
            roadmap_path.write_text(content)
            result = parse_roadmap(str(roadmap_path))
            assert len(result) == 1
            assert result[0]["id"] == "PHASE-1"

    def test_blank_lines_filtered(self):
        """Blank lines produce no output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            content = """

- [x] `PHASE-1` | LOW | Real phase

"""
            roadmap_path.write_text(content)
            result = parse_roadmap(str(roadmap_path))
            assert len(result) == 1

    def test_comment_lines_filtered(self):
        """Comment lines produce no output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            content = """> This is a comment
- [x] `PHASE-1` | LOW | Real phase"""
            roadmap_path.write_text(content)
            result = parse_roadmap(str(roadmap_path))
            assert len(result) == 1


class TestParseRoadmapBackticksAndRisk:
    """Tests for backtick stripping and risk segment removal."""

    def test_backticks_stripped_from_id(self):
        """Backticks around phase ID are stripped from output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            roadmap_path.write_text("- [x] `INFRA-1` | LOW | Goal text")
            result = parse_roadmap(str(roadmap_path))
            assert result[0]["id"] == "INFRA-1"
            assert "`" not in result[0]["id"]

    def test_risk_segment_discarded_from_goal(self):
        """| RISK | segment is discarded from goal text."""
        with tempfile.TemporaryDirectory() as tmpdir:
            roadmap_path = Path(tmpdir) / "roadmap.md"
            roadmap_path.write_text("- [x] `PHASE-1` | LOW | Goal text with risk")
            result = parse_roadmap(str(roadmap_path))
            assert result[0]["goal"] == "Goal text with risk"
            assert "LOW" not in result[0]["goal"]
            assert "|" not in result[0]["goal"]
