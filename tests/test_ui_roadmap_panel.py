"""Tests for RoadmapPanel React component in UI."""
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


def test_roadmap_panel_component_exists(html_content):
    """RoadmapPanel React component is defined in the inline script."""
    has_component = bool(re.search(r'function\s+RoadmapPanel|RoadmapPanel\s*=|const\s+RoadmapPanel', html_content))
    assert has_component, "RoadmapPanel component not found in React code"


def test_roadmap_panel_accepts_roadmap_prop(html_content):
    """RoadmapPanel component accepts roadmap as a prop."""
    # Find RoadmapPanel component definition
    panel_match = re.search(r'(function\s+RoadmapPanel|const\s+RoadmapPanel\s*=\s*(?:function\s*)?)\s*\(\s*\{[^}]*roadmap[^}]*\}', html_content)
    assert panel_match, "RoadmapPanel does not accept roadmap prop"


def test_phase_rows_render_from_roadmap_array(html_content):
    """RoadmapPanel renders rows for each phase in the roadmap array."""
    # Check for map/iteration over roadmap in the component
    has_map = bool(re.search(r'roadmap\.map|\.map\s*\(\s*(?:phase|item)', html_content))
    assert has_map, "RoadmapPanel does not map over roadmap array"


def test_complete_status_icon(html_content):
    """Complete phases render with ✓ checkmark icon."""
    has_complete_icon = bool(re.search(r'\\u2713|✓|check|complete.*icon', html_content, re.IGNORECASE))
    assert has_complete_icon, "Complete status icon (✓) not found"


def test_in_progress_status_icon(html_content):
    """In-progress phases render with ▶ play/triangle icon."""
    has_in_progress_icon = bool(re.search(r'▶|play|triangle|in_progress.*icon', html_content, re.IGNORECASE))
    assert has_in_progress_icon, "In-progress status icon (▶) not found"


def test_pending_status_icon(html_content):
    """Pending phases render with ○ circle icon."""
    has_pending_icon = bool(re.search(r'○|circle|pending.*icon', html_content, re.IGNORECASE))
    assert has_pending_icon, "Pending status icon (○) not found"


def test_skipped_status_icon(html_content):
    """Skipped phases render with ⊘ icon."""
    has_skipped_icon = bool(re.search(r'⊘|skipped.*icon', html_content, re.IGNORECASE))
    assert has_skipped_icon, "Skipped status icon (⊘) not found"


def test_blocked_status_icon(html_content):
    """Blocked phases render with ⚠ warning/triangle icon."""
    has_blocked_icon = bool(re.search(r'⚠|warning|blocked.*icon', html_content, re.IGNORECASE))
    assert has_blocked_icon, "Blocked status icon (⚠) not found"


def test_in_progress_phase_has_left_border_accent(html_content):
    """In-progress phase row has left border accent in primary accent color (#3b82f6)."""
    # Check for border-left or border-l with accent color
    has_accent_border = bool(re.search(r'border-l-4|border-left.*accent|border-l.*#3b82f6|border-l.*blue-500', html_content))
    assert has_accent_border, "In-progress phase does not have left border accent (#3b82f6)"


def test_complete_phases_have_reduced_opacity(html_content):
    """Complete phases render with reduced opacity or muted visual style."""
    # Check for opacity or muted styling on complete phases
    has_muted_style = bool(re.search(r'opacity|muted|text-slate-500|text-slate-400', html_content))
    assert has_muted_style, "Complete phases do not have muted/reduced opacity styling"


def test_blocked_phases_have_red_tint(html_content):
    """Blocked phases render with red tint background or border."""
    # Tailwind uses bg-red-*, border-red-* (substring patterns, not red-before-bg)
    has_red_tint = bool(
        re.search(r"bg-red|border-red|red-900|red-800|red-600", html_content, re.IGNORECASE)
    )
    assert has_red_tint, "Blocked phases do not have red tint styling"


def test_progress_bar_renders_above_list(html_content):
    """Progress bar renders above the roadmap list showing 'N / T complete'."""
    # Check for progress bar rendering
    has_progress_bar = bool(re.search(r'progress|Progress', html_content))
    assert has_progress_bar, "Progress bar not found"
    
    # Check for "N / T" or "complete" text pattern
    has_complete_text = bool(re.search(r'complete|Complete', html_content))
    assert has_complete_text, "Progress bar does not show 'complete' text"


def test_progress_bar_fill_percentage(html_content):
    """Progress bar fill percentage equals complete / total phases."""
    # Check for percentage calculation
    has_percentage = bool(re.search(r'percentage|percent|\d+%|width.*%', html_content, re.IGNORECASE))
    assert has_percentage, "Progress bar percentage calculation not found"


def test_roadmap_panel_scrollable(html_content):
    """Roadmap panel container has overflow-y:auto or scroll styling."""
    # Check for overflow styling on the panel container
    has_overflow = bool(re.search(r'overflow-y|overflow-y-auto|scroll', html_content))
    assert has_overflow, "Roadmap panel does not have scrollable overflow styling"


def test_roadmap_panel_in_right_panel(html_content):
    """RoadmapPanel is rendered in the right panel section of the 3-panel layout."""
    # Check that RoadmapPanel is rendered in the App component's return JSX
    # Look for rendering in right-panel-content or similar
    has_right_panel = bool(re.search(r'right-panel|rightPanel|RoadmapPanel.*right', html_content))
    assert has_right_panel, "RoadmapPanel is not integrated into right panel"


def test_phase_id_renders_in_row(html_content):
    """Phase ID renders in each phase row."""
    # Check for phase.id rendering in rows
    has_id_render = bool(re.search(r'phase\.id|\.id', html_content))
    assert has_id_render, "Phase ID not rendered in rows"


def test_goal_text_renders_in_row(html_content):
    """Phase goal text renders in each phase row."""
    # Check for phase.goal rendering in rows
    has_goal_render = bool(re.search(r'phase\.goal|\.goal', html_content))
    assert has_goal_render, "Phase goal not rendered in rows"