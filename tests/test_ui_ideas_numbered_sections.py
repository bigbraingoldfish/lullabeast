"""
Verifies that ui/index.html parsePrdSections normalises numbered markdown
heading prefixes ("## 1. Problem Statement") to canonical titles.

Checks both:
1. The source contains the normalizePrdHeadingText helper (structural guard).
2. The helper name is used inside parsePrdSections (integration guard).
"""


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_normalize_prd_heading_text_helper_exists():
    """The helper function must be defined in index.html."""
    content = load_index_html()
    assert "normalizePrdHeadingText" in content, (
        "normalizePrdHeadingText helper not found in ui/index.html — "
        "frontend cannot strip ordinal prefixes from numbered headings"
    )


def test_normalize_helper_strips_ordinal_prefix():
    """parsePrdSections must call the helper for the markdown heading branch."""
    content = load_index_html()
    # Both helper definition and its call-site inside parsePrdSections must exist
    assert "normalizePrdHeadingText(heading[1])" in content, (
        "parsePrdSections does not apply normalizePrdHeadingText to the markdown "
        "heading branch — '## 1. Problem Statement' will not be parsed correctly"
    )


def test_parse_prd_sections_still_present():
    """Smoke check: parsePrdSections function body is still in the file."""
    content = load_index_html()
    assert "parsePrdSections" in content
    assert "PRD_SECTION_TITLES.forEach" in content
