"""Tests for collapsible Activity Feed panel."""
import pytest
import re
from pathlib import Path

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


def test_feed_collapsed_state_exists(html_content):
    assert bool(re.search(r"feedCollapsed|activityFeedCollapsed", html_content)), \
        "Collapsed state variable not found for Activity Feed panel"

def test_collapsed_header_shows_chevron(html_content):
    assert bool(re.search(r"▾|▸|▼|▶|chevron|ChevronDown|ChevronRight", html_content)), \
        "Chevron toggle indicator not found in Activity Feed header"

def test_collapsed_header_clickable(html_content):
    assert bool(re.search(r"onClick.*feedCollapsed|onClick.*activityFeedCollapsed|feedCollapsed.*onClick", html_content, re.DOTALL)), \
        "Activity Feed header should be clickable to toggle collapse"

def test_collapsed_panel_hides_feed_content(html_content):
    assert bool(re.search(r"feedCollapsed|activityFeedCollapsed", html_content)), \
        "Collapsed state should conditionally hide the feed content"
