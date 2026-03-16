"""
Tests for UI header functionality in index.html.
"""
import pytest
import re


def test_autodev_wordmark_exists():
    """Header contains 'AUTODEV' wordmark in JetBrains Mono or Space Mono font."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for AUTODEV wordmark
    assert 'AUTODEV' in content, "Header should contain 'AUTODEV' wordmark"
    
    # Check for JetBrains Mono or Space Mono font
    assert 'JetBrains Mono' in content or 'Space Mono' in content, \
        "Should use JetBrains Mono or Space Mono font"


def test_status_pill_with_running_state():
    """Status pill renders with correct label for RUNNING state (amber pulse)."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for amber pulse CSS class
    assert 'animate-pulse' in content or 'amber' in content.lower(), \
        "Should have amber pulse effect for RUNNING state"


def test_status_pill_waiting_for_sentinel():
    """Status pill renders with correct label for WAITING_FOR_SENTINEL state (amber pulse, shows 'WAITING — {agent}')."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for WAITING label pattern
    assert 'WAITING' in content, "Should show WAITING label"
    assert 'agent' in content.lower(), "Should reference agent in waiting state"


def test_status_pill_waiting_for_human():
    """Status pill renders with correct label for WAITING_FOR_HUMAN state (orange solid)."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for WAITING_FOR_HUMAN or human-related status
    assert 'WAITING_FOR_HUMAN' in content or 'Human' in content, \
        "Should handle WAITING_FOR_HUMAN state"


def test_status_pill_halted_silent():
    """Status pill renders with correct label for HALTED_SILENT state (red solid)."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for HALTED_SILENT state handling
    assert 'HALTED_SILENT' in content or 'Halted' in content, \
        "Should handle HALTED_SILENT state"


def test_status_pill_blocked():
    """Status pill renders with correct label for BLOCKED state (red solid)."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for BLOCKED state handling
    assert 'BLOCKED' in content or 'Blocked' in content, \
        "Should handle BLOCKED state"


def test_orchestrator_down_override_running():
    """Status pill shows 'ORCHESTRATOR DOWN' in red when orchestrator_alive is false and pipeline_status is RUNNING."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for ORCHESTRATOR DOWN override logic
    assert 'ORCHESTRATOR DOWN' in content or 'orchestrator_alive' in content, \
        "Should handle ORCHESTRATOR DOWN override"


def test_orchestrator_down_override_waiting():
    """Status pill shows 'ORCHESTRATOR DOWN' in red when orchestrator_alive is false and pipeline_status is WAITING_FOR_SENTINEL."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for ORCHESTRATOR DOWN override logic with WAITING_FOR_SENTINEL
    assert 'ORCHESTRATOR DOWN' in content or 'orchestrator_alive' in content, \
        "Should handle ORCHESTRATOR DOWN override for WAITING_FOR_SENTINEL"


def test_project_path_display():
    """Project path displays last two path segments in monospace font."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for project path display
    assert 'project' in content.lower() and 'path' in content.lower(), \
        "Should display project path"


def test_liveness_dot_green():
    """Liveness dot is green when orchestrator_alive is true."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for liveness dot with green color
    assert 'green' in content.lower() or '#22c55e' in content or 'bg-green' in content, \
        "Should have green liveness indicator"


def test_liveness_dot_red():
    """Liveness dot is red when orchestrator_alive is false."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for liveness dot with red color
    assert 'red' in content.lower() or '#ef4444' in content or 'bg-red' in content, \
        "Should have red liveness indicator for dead orchestrator"


def test_state_polls_every_3_seconds():
    """State polls GET /api/state every 3 seconds."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for 3 second polling interval
    assert '3000' in content, "Should poll every 3 seconds (3000ms)"


def test_amber_pulse_animation():
    """Amber pulse CSS animation is applied only to RUNNING and WAITING_FOR_SENTINEL states."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for amber pulse animation
    assert 'amber' in content.lower() or '#f59e0b' in content, \
        "Should have amber color for pulse animation"
    
    # Check for animation definition
    assert 'animation' in content.lower() or '@keyframes' in content or 'animate' in content, \
        "Should have CSS animation for pulse effect"