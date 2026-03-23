"""Tests for RoadmapPanel expand/collapse functionality."""
import pytest
import re
from pathlib import Path

# Path to the index.html file
INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html_content():
    """Read the index.html content."""
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


def test_roadmap_panel_uses_usestate_for_expanded_phase(html_content):
    """RoadmapPanel uses useState to track expanded phase ID."""
    # Find RoadmapPanel component and check for useState with expandedPhaseId
    panel_match = re.search(r'function\s+RoadmapPanel\s*\([^)]*\)\s*\{(.*?)\n\s*\}', html_content, re.DOTALL)
    assert panel_match, "RoadmapPanel component not found"
    
    component_body = panel_match.group(1)
    # Check for useState with expanded or similar state variable
    has_expand_state = bool(re.search(r'useState\s*\(\s*(?:null|undefined)', component_body))
    assert has_expand_state, "RoadmapPanel does not use useState for expanded phase tracking"


def test_phase_row_onclick_handler_exists(html_content):
    """Phase rows have onClick handler for expand/collapse."""
    # Check for onClick on the phase row div
    has_onclick = bool(re.search(r'onClick\s*=', html_content))
    assert has_onclick, "No onClick handler found on phase rows"


def test_expand_collapse_toggles_state(html_content):
    """Clicking expanded row sets state to null (collapse)."""
    # Look for conditional logic that handles collapsing
    has_collapse_logic = bool(re.search(r'expandedPhaseId\s*===|expanded.*===.*null', html_content))
    assert has_collapse_logic, "No expand/collapse toggle logic found"


def test_only_one_phase_expanded_at_a_time(html_content):
    """Clicking a different row collapses previous and expands new."""
    # Toggle: setExpandedPhaseId(prev => prev === phaseId ? null : phaseId) or direct phase.id
    has_set_expanded = bool(
        re.search(r"setExpandedPhaseId\s*\(\s*(?:phase\.id|phaseId)", html_content)
        or re.search(r"setExpandedPhaseId\s*\(\s*prev\s*=>", html_content)
    )
    assert has_set_expanded, "No logic to set expandedPhaseId to phase ID"


def test_expanded_row_shows_full_goal_text(html_content):
    """Expanded row displays full (non-truncated) goal text."""
    # Check for conditional rendering based on expanded state
    # Should have logic to show full goal when expanded
    has_expanded_check = bool(re.search(r'\{.*expanded.*\?.*phase\.goal', html_content, re.DOTALL))
    assert has_expanded_check, "No conditional rendering of goal based on expanded state"


def test_expanded_row_shows_exit_criteria_when_present(html_content):
    """Expanded row displays exit_criteria items when exit_criteria array is non-empty."""
    # Check for exit_criteria rendering in expanded content
    has_exit_criteria = bool(re.search(r'exit_criteria|\.exit', html_content))
    assert has_exit_criteria, "No exit_criteria rendering found"


def test_exit_criteria_not_shown_when_empty(html_content):
    """Expanded row shows no exit_criteria section when array is empty."""
    # Check for conditional rendering of exit_criteria (length check or similar)
    has_conditional_exit = bool(re.search(r'exit_criteria.*(?:length|\.length|&&|\?\s*)', html_content))
    assert has_conditional_exit, "No conditional rendering of exit_criteria based on presence"


def test_collapsed_rows_show_truncated_goal(html_content):
    """Collapsed rows show truncated goal text with ellipsis."""
    # Check for truncate class or similar on goal text
    has_truncate = bool(re.search(r'truncate|ellipsis|text-overflow', html_content))
    assert has_truncate, "Collapsed rows do not show truncated goal text"


def test_visual_indicator_for_expanded_state(html_content):
    """Expanded rows have visual indicator (different background or rotated chevron)."""
    # Check for expanded-specific styling (different background, chevron, etc.)
    has_expanded_style = bool(re.search(r'expanded.*(?:bg|background|color|class)', html_content, re.DOTALL))
    assert has_expanded_style, "No visual indicator for expanded state"


def test_no_page_reload_on_click(html_content):
    """Clicking phase row does not cause page reload (no anchor tags with href)."""
    # The onclick should be a React handler, not a link
    # Check that there's no anchor tag wrapping the row with href
    has_anchor_in_row = bool(re.search(r'<a[^>]*href.*phase|<div[^>]*onClick[^>]*href', html_content))
    # We want this to be False - no anchors in the clickable row
    assert not has_anchor_in_row, "Found anchor tag with href that would cause page reload"


def test_phase_row_subcomponent_or_inline_handler(html_content):
    """PhaseRow sub-component exists OR inline click handler is properly implemented."""
    # Either there's a PhaseRow component, or the click handler is inlined
    has_phase_row = bool(re.search(r'function\s+PhaseRow|const\s+PhaseRow', html_content))
    has_inline_handler = bool(re.search(r'onClick\s*=\s*\{', html_content))
    
    assert has_phase_row or has_inline_handler, "No PhaseRow sub-component or inline click handler found"


def test_click_handler_receives_phase_id(html_content):
    """Click handler function receives or has access to phase ID."""
    # The click handler should reference phase.id
    has_phase_id_in_handler = bool(re.search(r'onClick\s*=\s*\{[^}]*phase\.id', html_content, re.DOTALL))
    assert has_phase_id_in_handler, "Click handler does not have access to phase ID"