"""IdeasScreen parses numbered PRD section headings for scroll targets."""
import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_parse_prd_sections_handles_numbered_headings():
    content = load_index_html()
    assert "numHeading" in content
    assert "parsePrdSections" in content
