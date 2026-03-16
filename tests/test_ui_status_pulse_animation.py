"""Tests for status pulse animation on different pipeline states."""
import pytest
import re
from pathlib import Path

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()

def test_running_state_has_status_pulse_class(html_content):
    """Verify RUNNING state has status-pulse class or amber-pulse animation."""
    # Check that RUNNING uses status-pulse class
    has_pulse = bool(re.search(r"['\"]RUNNING['\"]:\s*['\"].*status-pulse", html_content))
    assert has_pulse, "RUNNING state does not have status-pulse class"

def test_waiting_for_sentinel_state_has_status_pulse_class(html_content):
    """Verify WAITING_FOR_SENTINEL state has status-pulse class or amber-pulse animation."""
    # Check that WAITING_FOR_SENTINEL uses status-pulse class
    has_pulse = bool(re.search(r"['\"]WAITING_FOR_SENTINEL['\"]:\s*['\"].*status-pulse", html_content))
    assert has_pulse, "WAITING_FOR_SENTINEL state does not have status-pulse class"

def test_waiting_for_human_state_has_no_pulse_animation(html_content):
    """Verify WAITING_FOR_HUMAN state has NO pulse animation (static orange)."""
    # Check that WAITING_FOR_HUMAN does NOT have status-pulse class
    has_no_pulse = not bool(re.search(r"['\"]WAITING_FOR_HUMAN['\"]:\s*['\"].*status-pulse", html_content))
    assert has_no_pulse, "WAITING_FOR_HUMAN state should NOT have status-pulse class"
    # Verify it uses static orange (bg-orange-500)
    has_static_orange = bool(re.search(r"['\"]WAITING_FOR_HUMAN['\"]:\s*['\"].*bg-orange-500", html_content))
    assert has_static_orange, "WAITING_FOR_HUMAN should use static orange (bg-orange-500)"

def test_halted_silent_state_has_no_pulse_animation(html_content):
    """Verify HALTED_SILENT state has NO pulse animation (static red)."""
    # Check that HALTED_SILENT does NOT have status-pulse class
    has_no_pulse = not bool(re.search(r"['\"]HALTED_SILENT['\"]:\s*['\"].*status-pulse", html_content))
    assert has_no_pulse, "HALTED_SILENT state should NOT have status-pulse class"
    # Verify it uses static red (bg-red-600)
    has_static_red = bool(re.search(r"['\"]HALTED_SILENT['\"]:\s*['\"].*bg-red-600", html_content))
    assert has_static_red, "HALTED_SILENT should use static red (bg-red-600)"

def test_blocked_state_has_no_pulse_animation(html_content):
    """Verify BLOCKED state has NO pulse animation (static red)."""
    # Check that BLOCKED does NOT have status-pulse class
    has_no_pulse = not bool(re.search(r"['\"]BLOCKED['\"]:\s*['\"].*status-pulse", html_content))
    assert has_no_pulse, "BLOCKED state should NOT have status-pulse class"
    # Verify it uses static red (bg-red-600)
    has_static_red = bool(re.search(r"['\"]BLOCKED['\"]:\s*['\"].*bg-red-600", html_content))
    assert has_static_red, "BLOCKED should use static red (bg-red-600)"

def test_status_pulse_css_class_exists(html_content):
    """Verify status-pulse CSS class is defined with animation."""
    has_pulse_css = bool(re.search(r"\.status-pulse\s*\{", html_content))
    assert has_pulse_css, "status-pulse CSS class not defined"
    has_animation = bool(re.search(r"animation:.*amber-pulse", html_content))
    assert has_animation, "status-pulse does not have amber-pulse animation"

def test_amber_pulse_keyframes_exists(html_content):
    """Verify amber-pulse keyframes animation is defined."""
    has_keyframes = bool(re.search(r"@keyframes\s+amber-pulse", html_content))
    assert has_keyframes, "amber-pulse keyframes not defined"
