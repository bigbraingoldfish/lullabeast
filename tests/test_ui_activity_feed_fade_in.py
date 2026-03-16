"""Tests for activity feed fade-in animation."""
import pytest
import re
from pathlib import Path

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()

def test_fade_in_animation_duration_is_300ms(html_content):
    """Verify fade-in animation duration is approximately 300ms (0.3s)."""
    # Check for 0.3s or 300ms animation duration
    has_duration = bool(re.search(r"animation:.*fade-in-row.*0\.3s|fade-in-row.*300ms", html_content))
    assert has_duration, "Fade-in animation duration is not approximately 300ms (0.3s)"

def test_fade_in_uses_opacity_transition(html_content):
    """Verify fade-in uses opacity transition from 0 to 1."""
    # Check for keyframes that animate opacity from 0 to 1
    has_opacity_from_0 = bool(re.search(r"@keyframes\s+fade-in-row.*?0%\s*\{[^}]*opacity:\s*0", html_content, re.DOTALL))
    assert has_opacity_from_0, "Fade-in animation does not start from opacity 0"
    has_opacity_to_1 = bool(re.search(r"100%\s*\{[^}]*opacity:\s*1", html_content))
    assert has_opacity_to_1, "Fade-in animation does not end at opacity 1"

def test_event_row_fade_in_class_exists(html_content):
    """Verify event-row-fade-in CSS class is defined."""
    has_fade_in_class = bool(re.search(r"\.event-row-fade-in\s*\{", html_content))
    assert has_fade_in_class, "event-row-fade-in CSS class not defined"

def test_fade_in_animation_applied_to_new_events(html_content):
    """Verify fade-in animation is applied to new event rows."""
    # Check that new events get the fade-in class
    has_fade_in_applied = bool(re.search(r"isNew.*event-row-fade-in|event-row-fade-in.*isNew", html_content))
    assert has_fade_in_applied, "Fade-in animation not applied to new event rows"
