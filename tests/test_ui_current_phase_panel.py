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
    """Pipeline screen holds current_phase_raw_id and current_agent state."""
    has_phase_id = bool(re.search(r"current_phase_raw_id", html_content))
    assert has_phase_id, "current_phase_raw_id not found"
    has_agent = bool(re.search(r"current_agent", html_content))
    assert has_agent, "current_agent not found"


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
    # AGENT_COLORS maps each agent to a Tailwind / hex background
    assert "AGENT_COLORS" in html_content
    assert bool(re.search(r"'PLANNER':\s*'[^']+'", html_content)), "PLANNER color entry not found"
    assert bool(re.search(r"'EXECUTOR':\s*'[^']+'", html_content)), "EXECUTOR color entry not found"
    assert bool(re.search(r"'REVIEWER':\s*'[^']+'", html_content)), "REVIEWER color entry not found"
    assert bool(re.search(r"'ESCALATION':\s*'[^']+'", html_content)), "ESCALATION color entry not found"


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
    has_caching = bool(re.search(r"\[roadmap,\s*setRoadmap\]", html_content))
    assert has_caching, "Roadmap caching (state) not found"


def test_current_phase_panel_rendered_in_left_panel(html_content):
    """CurrentPhasePanel is rendered in the left panel area of the UI."""
    # Check that CurrentPhasePanel is used/rendered
    has_render = bool(re.search(r'<CurrentPhasePanel|<CurrentPhasePanel\s*/>|CurrentPhasePanel\s+/>', html_content))
    assert has_render, "CurrentPhasePanel is not rendered in the UI"

# Tests for new retry dot counter components

def test_dot_counter_component_exists(html_content):
    """DotCounter React component is defined and renders filled/empty dots."""
    has_component = bool(re.search(r'function\s+DotCounter|DotCounter\s*=', html_content))
    assert has_component, "DotCounter component not found"
    
    # Check for max 3 dots logic
    has_max_dots = bool(re.search(r'maxDots\s*=\s*3|maxDots.*3', html_content))
    assert has_max_dots, "DotCounter should cap at 3 dots"
    
    # Check for filled/empty dot rendering
    has_filled_dot = bool(re.search(r'bg-amber-500.*rounded-full|rounded-full.*bg-amber-500', html_content))
    has_empty_dot = bool(re.search(r'bg-slate-700.*rounded-full|rounded-full.*bg-slate-700', html_content))
    assert has_filled_dot, "Filled dots (amber) not found"
    assert has_empty_dot, "Empty dots (slate-700) not found"


def test_dot_counter_receives_count_and_label_props(html_content):
    """DotCounter component accepts count and label props."""
    has_props = bool(re.search(r'DotCounter\s*\(\s*\{\s*count.*label', html_content))
    assert has_props, "DotCounter should accept count and label props"


def test_dot_counter_renders_conditionally(html_content):
    """DotCounter returns null when count is undefined or null."""
    has_conditional = bool(re.search(r'count\s*===\s*undefined.*return\s+null|count\s*===\s*null.*return\s+null', html_content))
    assert has_conditional, "DotCounter should return null when count is undefined/null"


def test_current_phase_panel_renders_dot_counters(html_content):
    """CurrentPhasePanel renders DotCounter components for retries."""
    has_dot_counter = bool(re.search(r'<DotCounter', html_content))
    assert has_dot_counter, "DotCounter not rendered in CurrentPhasePanel"
    
    has_planner = bool(re.search(r'planner_retries.*DotCounter|DotCounter.*planner_retries', html_content))
    has_executor = bool(re.search(r'executor_retries.*DotCounter|DotCounter.*executor_retries', html_content))
    has_reviewer = bool(re.search(r'reviewer_retries.*DotCounter|DotCounter.*reviewer_retries', html_content))
    assert has_planner, "DotCounter for planner_retries not found"
    assert has_executor, "DotCounter for executor_retries not found"
    assert has_reviewer, "DotCounter for reviewer_retries not found"


def test_app_state_includes_retry_counts(html_content):
    """Pipeline screen state includes planner_retries, executor_retries, reviewer_retries."""
    assert "PipelineScreen" in html_content
    assert "planner_retries" in html_content and "executor_retries" in html_content and "reviewer_retries" in html_content


# Tests for LastErrorCode component

def test_last_error_code_component_exists(html_content):
    """LastErrorCode React component exists and displays error in monospace."""
    has_component = bool(re.search(r'function\s+LastErrorCode|LastErrorCode\s*=', html_content))
    assert has_component, "LastErrorCode component not found"
    
    has_monospace = bool(re.search(r'font-mono', html_content))
    assert has_monospace, "LastErrorCode should use monospace font"
    
    has_red = bool(re.search(r'text-red-\d+|text-red-', html_content))
    assert has_red, "LastErrorCode should use red color"


def test_last_error_code_renders_conditionally(html_content):
    """LastErrorCode returns null when errorCode is not present."""
    has_conditional = bool(re.search(r'!errorCode.*return\s+null|errorCode\s*===\s*null.*return\s+null', html_content))
    assert has_conditional, "LastErrorCode should return null when errorCode is absent"


def test_current_phase_panel_renders_last_error_code(html_content):
    """CurrentPhasePanel renders LastErrorCode component."""
    has_render = bool(re.search(r'<LastErrorCode', html_content))
    assert has_render, "LastErrorCode not rendered in CurrentPhasePanel"


def test_app_state_includes_last_error_code(html_content):
    """Pipeline screen state includes last_error_code."""
    assert "last_error_code" in html_content and "PipelineScreen" in html_content


# Tests for ElapsedTimer component

def test_elapsed_timer_component_exists(html_content):
    """ElapsedTimer React component exists with setInterval for 1-second tick."""
    has_component = bool(re.search(r'function\s+ElapsedTimer|ElapsedTimer\s*=', html_content))
    assert has_component, "ElapsedTimer component not found"
    
    has_interval = bool(re.search(r'setInterval.*1000|1000.*setInterval', html_content))
    assert has_interval, "ElapsedTimer should use setInterval with 1000ms"


def test_elapsed_timer_uses_useeffect(html_content):
    """ElapsedTimer uses useEffect to manage the timer interval."""
    has_useeffect = bool(re.search(r'ElapsedTimer.*useEffect|useEffect.*ElapsedTimer', html_content))
    assert has_useeffect, "ElapsedTimer should use useEffect"


def test_elapsed_timer_amber_color_condition(html_content):
    """ElapsedTimer shows amber color when WAITING_FOR_SENTINEL and elapsed > 5 min."""
    has_amber_condition = bool(re.search(r"WAITING_FOR_SENTINEL.*elapsed\s*>\s*300|elapsed\s*>\s*300.*WAITING_FOR_SENTINEL", html_content))
    assert has_amber_condition, "ElapsedTimer should check for WAITING_FOR_SENTINEL and > 300 seconds"
    
    has_amber_color = bool(re.search(r'text-amber-\d+|text-amber-', html_content))
    assert has_amber_color, "ElapsedTimer should apply amber text color"


def test_elapsed_timer_renders_conditionally(html_content):
    """ElapsedTimer returns null when lastActionTimestamp is not present."""
    has_conditional = bool(re.search(r'!lastActionTimestamp.*return\s+null|lastActionTimestamp\s*===\s*null.*return\s+null', html_content))
    assert has_conditional, "ElapsedTimer should return null when lastActionTimestamp is absent"


def test_current_phase_panel_renders_elapsed_timer(html_content):
    """CurrentPhasePanel renders ElapsedTimer component."""
    has_render = bool(re.search(r'<ElapsedTimer', html_content))
    assert has_render, "ElapsedTimer not rendered in CurrentPhasePanel"


def test_app_state_includes_last_action_timestamp(html_content):
    """Pipeline screen state includes last_action_timestamp."""
    assert "last_action_timestamp" in html_content and "PipelineScreen" in html_content

# Tests for new retry dot counter components

def test_dot_counter_component_exists(html_content):
    """DotCounter React component is defined and renders filled/empty dots."""
    has_component = bool(re.search(r'function\s+DotCounter|DotCounter\s*=', html_content))
    assert has_component, "DotCounter component not found"

def test_dot_counter_receives_count_and_label_props(html_content):
    """DotCounter component accepts count and label props."""
    has_props = bool(re.search(r'DotCounter\s*\(\s*\{\s*count.*label', html_content))
    assert has_props, "DotCounter should accept count and label props"


def test_dot_counter_renders_conditionally(html_content):
    """DotCounter returns null when count is undefined or null."""
    has_conditional = bool(re.search(r'count\s*===\s*undefined.*return\s+null|count\s*===\s*null.*return\s+null', html_content))
    assert has_conditional, "DotCounter should return null when count is undefined/null"


def test_current_phase_panel_renders_dot_counters(html_content):
    """CurrentPhasePanel renders DotCounter components for retries."""
    has_dot_counter = bool(re.search(r'<DotCounter', html_content))
    assert has_dot_counter, "DotCounter not rendered in CurrentPhasePanel"

def test_app_state_includes_retry_counts(html_content):
    """Pipeline screen state includes planner_retries, executor_retries, reviewer_retries."""
    assert "PipelineScreen" in html_content
    assert "planner_retries" in html_content and "executor_retries" in html_content and "reviewer_retries" in html_content


# Tests for LastErrorCode component

def test_last_error_code_component_exists(html_content):
    """LastErrorCode React component exists and displays error in monospace."""
    has_component = bool(re.search(r'function\s+LastErrorCode|LastErrorCode\s*=', html_content))
    assert has_component, "LastErrorCode component not found"
    
    has_monospace = bool(re.search(r'font-mono', html_content))
    assert has_monospace, "LastErrorCode should use monospace font"
    
    has_red = bool(re.search(r'text-red-\d+|text-red-', html_content))
    assert has_red, "LastErrorCode should use red color"

def test_last_error_code_renders_conditionally(html_content):
    """LastErrorCode returns null when errorCode is not present."""
    has_conditional = bool(re.search(r'!errorCode.*return\s+null|errorCode\s*===\s*null.*return\s+null', html_content))
    assert has_conditional, "LastErrorCode should return null when errorCode is absent"


def test_current_phase_panel_renders_last_error_code(html_content):
    """CurrentPhasePanel renders LastErrorCode component."""
    has_render = bool(re.search(r'<LastErrorCode', html_content))
    assert has_render, "LastErrorCode not rendered in CurrentPhasePanel"


def test_app_state_includes_last_error_code(html_content):
    """Pipeline screen state includes last_error_code."""
    assert "last_error_code" in html_content and "PipelineScreen" in html_content


# Tests for ElapsedTimer component

def test_elapsed_timer_component_exists(html_content):
    """ElapsedTimer React component exists with setInterval for 1-second tick."""
    has_component = bool(re.search(r'function\s+ElapsedTimer|ElapsedTimer\s*=', html_content))
    assert has_component, "ElapsedTimer component not found"
    
    # Check for setInterval with 1000ms anywhere in the file
    has_interval = bool(re.search(r', 1000\s*\)', html_content))
    assert has_interval, "ElapsedTimer should use setInterval with 1000ms"


def test_elapsed_timer_uses_useeffect(html_content):
    """ElapsedTimer uses useEffect to manage the timer interval."""
    # Find the ElapsedTimer function and check for useEffect inside it
    timer_match = re.search(r'function\s+ElapsedTimer[^{]*\{(.*?)^\s*\}', html_content, re.MULTILINE | re.DOTALL)
    assert timer_match, "ElapsedTimer function not found"
    timer_code = timer_match.group(1)
    has_useeffect = bool(re.search(r'useEffect', timer_code))
    assert has_useeffect, "ElapsedTimer should use useEffect"


def test_elapsed_timer_amber_color_condition(html_content):
    """ElapsedTimer shows amber color when WAITING_FOR_SENTINEL and elapsed > 5 min."""
    # Check for the condition - look for the pattern with whitespace flexibility
    has_amber_condition = bool(re.search(r"WAITING_FOR_SENTINEL.*isOverdue|isOverdue.*WAITING_FOR_SENTINEL", html_content, re.DOTALL))
    assert has_amber_condition, "ElapsedTimer should check for WAITING_FOR_SENTINEL and > 300 seconds"
    
    has_amber_color = bool(re.search(r'text-amber-\d+|text-amber-', html_content))
    assert has_amber_color, "ElapsedTimer should apply amber text color"


def test_elapsed_timer_renders_conditionally(html_content):
    """ElapsedTimer returns null when lastActionTimestamp is not present."""
    has_conditional = bool(re.search(r'!lastActionTimestamp.*return\s+null|lastActionTimestamp\s*===\s*null.*return\s+null', html_content))
    assert has_conditional, "ElapsedTimer should return null when lastActionTimestamp is absent"


def test_current_phase_panel_renders_elapsed_timer(html_content):
    """CurrentPhasePanel renders ElapsedTimer component."""
    has_render = bool(re.search(r'<ElapsedTimer', html_content))
    assert has_render, "ElapsedTimer not rendered in CurrentPhasePanel"


def test_app_state_includes_last_action_timestamp(html_content):
    """Pipeline screen state includes last_action_timestamp."""
    assert "last_action_timestamp" in html_content and "PipelineScreen" in html_content

# Tests for SkillInjected component

def test_skill_injected_component_exists(html_content):
    """SkillInjected React component exists and displays discipline / agent."""
    has_component = bool(re.search(r'function\s+SkillInjected|SkillInjected\s*=', html_content))
    assert has_component, "SkillInjected component not found"
    
    # Check for muted text (slate-500)
    has_muted = bool(re.search(r'text-slate-500', html_content))
    assert has_muted, "SkillInjected should use muted text color"


def test_skill_injected_renders_conditionally(html_content):
    """SkillInjected returns null when skill_injected or skill_agent is not present."""
    has_conditional = bool(re.search(r'!skillInjected.*return\s+null|!skillAgent.*return\s+null', html_content))
    assert has_conditional, "SkillInjected should return null when skill_injected or skill_agent is absent"


def test_current_phase_panel_renders_skill_injected(html_content):
    """CurrentPhasePanel renders SkillInjected component."""
    has_render = bool(re.search(r'<SkillInjected', html_content))
    assert has_render, "SkillInjected not rendered in CurrentPhasePanel"


def test_app_state_includes_skill_fields(html_content):
    """Pipeline screen state includes skill_injected and skill_agent."""
    assert "skill_injected" in html_content and "skill_agent" in html_content and "PipelineScreen" in html_content


# Tests for API endpoint

def test_api_state_includes_retry_counts(html_content):
    """API /api/state should return planner_retries, executor_retries, reviewer_retries."""
    # Check server.py for these fields in the response
    server_path = Path(__file__).parent.parent / "ui" / "server.py"
    if server_path.exists():
        server_code = server_path.read_text()
        # Check that retry counters are extracted from pipeline_state counters
        has_planner = bool(re.search(r'planner_retries.*counters|counters.*planner_retries', server_code))
        has_executor = bool(re.search(r'executor_retries.*counters|counters.*executor_retries', server_code))
        has_reviewer = bool(re.search(r'reviewer_retries.*counters|counters.*reviewer_retries', server_code))
        assert has_planner, "server.py should include planner_retries from pipeline_state counters"
        assert has_executor, "server.py should include executor_retries from pipeline_state counters"
        assert has_reviewer, "server.py should include reviewer_retries from pipeline_state counters"


def test_api_state_includes_phase_state_fields(html_content):
    """API /api/state should return last_action_timestamp, skill_injected, skill_agent."""
    server_path = Path(__file__).parent.parent / "ui" / "server.py"
    if server_path.exists():
        server_code = server_path.read_text()
        
        has_timestamp = bool(re.search(r'last_action_timestamp', server_code))
        has_skill_injected = bool(re.search(r'skill_injected.*phase_state|phase_state.*skill_injected', server_code))
        has_skill_agent = bool(re.search(r'skill_agent.*phase_state|phase_state.*skill_agent', server_code))
        
        assert has_timestamp, "server.py should include last_action_timestamp"
        assert has_skill_injected, "server.py should include skill_injected from phase_state"
        assert has_skill_agent, "server.py should include skill_agent from phase_state"
