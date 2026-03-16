"""Tests for responsive layout at 768px breakpoint."""
import pytest
import re
from pathlib import Path

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()

def test_md_breakpoint_768px_for_two_column_layout(html_content):
    """Verify md: breakpoint (768px) is used for two-column layout."""
    has_md_breakpoint = bool(re.search(r"md:grid-cols-2", html_content))
    assert has_md_breakpoint, "md: breakpoint (768px) for two-column layout not found"

def test_grid_cols_1_for_vertical_stacking(html_content):
    """Verify grid-cols-1 for vertical stacking at narrow viewports."""
    has_grid_cols_1 = bool(re.search(r"grid-cols-1", html_content))
    assert has_grid_cols_1, "grid-cols-1 for vertical stacking at narrow viewports not found"

def test_responsive_grid_on_main_element(html_content):
    """Verify responsive grid is applied to main content area."""
    has_responsive_grid = bool(re.search(r"<main[^>]*class=.*grid.*grid-cols-1.*md:grid-cols-2", html_content, re.DOTALL))
    assert has_responsive_grid, "Responsive grid not applied to main element"
