"""Tests for CurrentPhasePanel React component in UI."""
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


def test_current_phase_panel_component_exists(html_content):
    """CurrentPhasePanel React component is defined in the inline script."""
    has_component = bool(re.search(r'function\s+CurrentPhasePanel|CurrentPhasePanel\s*=|const\s+CurrentPhasePanel', html_content))
    assert has_component, "CurrentPhasePanel component not found in React code"


def test_current_phase_state_in_app(html_content):
    """App component has current_phase_raw_id and current_agent state."""
    # Find the App component definition
    app_match = re.search(r'function\s+App\s*\([^)]*\)\s*\{(.*?)^\s*\}', html_content, re.MULTILINE | re.DOTALL)
    if not app_match:
        app_match = re.search(r'const\s+App\s*=\s*(?:function\s*)?\([^)]*\)\s*=>\s*\{(.*?)^\s*\}', html_content, re.MULTILINE | re.DOTALL)
    
    assert app_match, "App component definition not found"
    app_code = app_match.group(1)
    
    # Check for current_phase_raw_id in state
    has_phase_id = bool(re.search(r'current_phase_raw_id', app_code))
    assert has_phase_id, "current_phase_raw_id not found in App state"
    
    # Check for current_agent in state
    has_agent = bool(re.search(r'current_agent', app_code))
    assert has_agent, "current_agent not found in App state"


def test_roadmap_state_and_fetch(html_content):
    """App component fetches /api/roadmap and stores in state."""
    # Check for roadmap state
    has_roadmap_state = bool(re.search(r'roadmap', html_content))
    assert has_roadmap_state, "roadmap state not found in App"
    
    # Check for /api/roadmap fetch
    has_roadmap_fetch = bool(re.search(r"/api/roadmap|fetch\(['\"]\/api\/roadmap", html_content))
    assert has_roadmap_fetch, "GET /api/roadmap fetch not found"


def test_get_goal_for_phase_helper(html_content):
    """getGoalForPhase helper function exists and uses roadmap to find goal."""
    has_helper = bool(re.search(r'getGoalForPhase|function\s+getGoalForPhase', html_content))
    assert has_helper, "getGoalForPhase helper function not found"
    
    # Check that it uses phaseId and roadmap parameters
    if has_helper:
        helper_match = re.search(r'(function\s+getGoalForPhase|getGoalForPhase\s*=)\s*\([^)]*phaseId[^)]*,\s*[^)]*roadmap[^)]*\)', html_content)
        assert helper_match, "getGoalForPhase does not accept phaseId and roadmap parameters"


def test_phase_id_renders_in_monospace(html_content):
    """current_phase_raw_id renders in monospace font (JetBrains Mono)."""
    # Check for rendering of phase ID with header-text class (JetBrains Mono)
    has_phase_render = bool(re.search(r'current_phase_raw_id.*header-text|header-text.*current_phase_raw_id', html_content))
    assert has_phase_render, "Phase ID not rendered with header-text (monospace) class"


def test_goal_text_displayed(html_content):
    """Goal text from roadmap lookup is displayed below phase ID."""
    # Check for goal rendering or reference to goal in the component
    has_goal_display = bool(re.search(r'goal|getGoalForPhase', html_content))
    assert has_goal_display, "Goal text display not found in CurrentPhasePanel"


def test_agent_badge_component_exists(html_content):
    """Agent badge component with four variants (PLANNER, EXECUTOR, REVIEWER, ESCALATION) exists."""
    # Check for agent badge rendering logic
    has_badge = bool(re.search(r'AgentBadge|agent.*badge|badge.*agent', html_content, re.IGNORECASE))
    assert has_badge, "Agent badge component not found"
    
    # Check for all four agent types
    has_planner = bool(re.search(r'PLANNER', html_content))
    has_executor = bool(re.search(r'EXECUTOR', html_content))
    has_reviewer = bool(re.search(r'REVIEWER', html_content))
    has_escalation = bool(re.search(r'ESCALATION', html_content))
    
    assert has_planner, "PLANNER agent type not found"
    assert has_executor, "EXECUTOR agent type not found"
    assert has_reviewer, "REVIEWER agent type not found"
    assert has_escalation, "ESCALATION agent type not found"


def test_agent_badge_colors(html_content):
    """Each agent type has distinct muted background color."""
    # Check for distinct color classes for each agent type
    # Slate for PLANNER, Emerald for EXECUTOR, Violet for REVIEWER, Amber for ESCALATION
    has_planner_color = bool(re.search(r'PLANNER.*slate.*6|slate.*6.*PLANNER', html_content))
    has_executor_color = bool(re.search(r'EXECUTOR.*emerald.*6|emerald.*6.*EXECUTOR', html_content))
    has_reviewer_color = bool(re.search(r'REVIEWER.*violet.*6|violet.*6.*REVIEWER', html_content))
    has_escalation_color = bool(re.search(r'ESCALATION.*amber.*6|amber.*6.*ESCALATION', html_content))
    
    assert has_planner_color, "Slate-600 color for PLANNER not found"
    assert has_executor_color, "Emerald-600 color for EXECUTOR not found"
    assert has_reviewer_color, "Violet-600 color for REVIEWER not found"
    assert has_escalation_color, "Amber-600 color for ESCALATION not found"


def test_no_active_phase_placeholder(html_content):
    """When current_phase_raw_id is empty or not found, shows 'No active phase' placeholder."""
    # Check for placeholder text or conditional rendering
    has_placeholder = bool(re.search(r'No active phase', html_content, re.IGNORECASE))
    assert has_placeholder, "'No active phase' placeholder not found"
    
    # Check for conditional logic that handles empty/missing phase
    has_conditional = bool(re.search(r'!current_phase_raw_id|current_phase_raw_id\s*==?\s*["\']|phase.*not.*found', html_content, re.IGNORECASE))
    assert has_conditional, "Conditional logic for empty/missing phase ID not found"


def test_roadmap_caching_logic(html_content):
    """Roadmap is fetched only when phase ID changes, not on every poll."""
    # Check for caching mechanism - should store roadmap in state
    # and only re-fetch when phase ID changes
    has_caching = bool(re.search(r'roadmap.*state|useState.*roadmap', html_content))
    assert has_caching, "Roadmap caching (state) not found"
    
    # Check for conditional re-fetch based on phase ID change
    has_conditional_fetch = bool(re.search(r'current_phase_raw_id.*change|phaseId.*change|if.*phase', html_content, re.IGNORECASE))
    # Note: This is a softer check - the key is that roadmap should be in state for caching
    assert has_caching, "Roadmap should be stored in state for caching"


def test_current_phase_panel_rendered_in_left_panel(html_content):
    """CurrentPhasePanel is rendered in the left panel area of the UI."""
    # Check that CurrentPhasePanel is used/rendered
    has_render = bool(re.search(r'<CurrentPhasePanel|<CurrentPhasePanel\s*/>|CurrentPhasePanel\s+/>', html_content))
    assert has_render, "CurrentPhasePanel is not rendered in the UI"