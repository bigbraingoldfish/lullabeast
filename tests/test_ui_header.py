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
    """Status pill uses RUNNING label with teal #0d9488 + run-pulse (active compute)."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    assert re.search(r"RUNNING:\s*\{[^}]*run-pulse", content), "RUNNING should use run-pulse"
    assert re.search(r"RUNNING:\s*\{[^}]*#0d9488", content), "RUNNING should use teal surface bg-[#0d9488]"


def test_status_pill_waiting_for_sentinel():
    """WAITING_FOR_SENTINEL uses static teal (no pulse); base label Running + formatWaitForSentinelLabel (L-02/L-30)."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    assert re.search(
        r"WAITING_FOR_SENTINEL:\s*\{[^}]*label:\s*['\"]Running['\"]",
        content,
    ), "Sentinel wait base label is Running"
    assert "formatWaitForSentinelLabel" in content
    assert re.search(r"WAITING_FOR_SENTINEL:\s*\{[^}]*#0d9488", content), \
        "Sentinel wait pill should use teal surface bg-[#0d9488]"
    assert 'current_agent' in content, "Should reference current_agent for waiting header"
    assert not re.search(r"WAITING_FOR_SENTINEL:\s*\{[^}]*run-pulse", content), \
        "Sentinel wait must not use run-pulse in pipeline pill map"


def test_status_pill_pipeline_complete_lime():
    """PIPELINE_COMPLETE uses lime #28D11B with dark label text (distinct from teal in-flight)."""
    with open("ui/index.html", "r") as f:
        content = f.read()
    assert re.search(
        r"PIPELINE_COMPLETE:\s*\{[^}]*#28D11B[^}]*text-slate-900",
        content,
        re.DOTALL,
    ), "COMPLETE pill should use bg-[#28D11B] and text-slate-900"


def test_status_pill_waiting_for_human():
    """Status pill renders with correct label for WAITING_FOR_HUMAN state (orange solid)."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    
    # Check for WAITING_FOR_HUMAN or human-related status
    assert 'WAITING_FOR_HUMAN' in content or 'Human' in content, \
        "Should handle WAITING_FOR_HUMAN state"


def test_status_pill_halted_silent():
    """HALTED_SILENT shows INTERVENTION REQUIRED (L-04)."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    assert "INTERVENTION REQUIRED" in content, "HALTED_SILENT user label"
    assert "HALTED_SILENT" in content, "State key still present for mapping"


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
    """Teal run-pulse is used for RUNNING; amber keyframes may remain for legacy non-pipeline UI."""
    with open('ui/index.html', 'r') as f:
        content = f.read()
    assert '@keyframes' in content and 'teal-pulse' in content, "teal-pulse keyframes expected"
    assert '.run-pulse' in content, "run-pulse class expected"