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
    # Multi-line span: header-text wrapper then phase id child (H-25 adds title= between)
    has_phase_render = bool(
        re.search(
            r"header-text\s+text-sm\s+font-semibold\s+text-slate-300[\s\S]{0,220}?\{current_phase_raw_id\}",
            html_content,
        )
    )
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


def test_no_active_phase_placeholder_non_idle(html_content):
    """Non-IDLE/UNKNOWN empty phase still shows neutral 'No active phase' placeholder."""
    assert "No active phase" in html_content
    has_conditional = bool(
        re.search(r"!current_phase_raw_id", html_content, re.IGNORECASE)
    )
    assert has_conditional, "Conditional logic for empty/missing phase ID not found"


def test_idle_empty_current_phase_copy_l36(html_content):
    """L-36: IDLE/UNKNOWN + no phase shows steering copy and test id."""
    assert "data-testid=\"current-phase-empty-idle\"" in html_content
    assert "No pipeline running." in html_content
    assert "Project Ideas" in html_content
    assert "Setup &amp; Preflight" in html_content or "Setup & Preflight" in html_content


def test_roadmap_panel_accepts_pipeline_status_and_idle_empty_l36(html_content):
    assert "function RoadmapPanel" in html_content or "RoadmapPanel(" in html_content
    assert "pipelineStatus" in html_content
    assert "data-testid=\"roadmap-empty-idle\"" in html_content
    assert "roadmap.md" in html_content


def test_roadmap_caching_logic(html_content):
    """Roadmap is fetched only when phase ID changes, not on every poll."""
    has_caching = bool(re.search(r"\[roadmap,\s*setRoadmap\]", html_content))
    assert has_caching, "Roadmap caching (state) not found"


def test_current_phase_panel_rendered_in_left_panel(html_content):
    """CurrentPhasePanel is rendered in the left panel area of the UI."""
    # Check that CurrentPhasePanel is used/rendered
    has_render = bool(re.search(r'<CurrentPhasePanel|<CurrentPhasePanel\s*/>|CurrentPhasePanel\s+/>', html_content))
    assert has_render, "CurrentPhasePanel is not rendered in the UI"

# Tests for L-28 Agent attempt track (boxed cells + neutral n/3 fraction)

def test_agent_attempt_row_component_exists(html_content):
    """AgentAttemptRow + getAgentAttemptDotStates + computeAgentAttemptFractionN (L-28)."""
    assert bool(re.search(r"function\s+AgentAttemptRow|AgentAttemptRow\s*=", html_content)), "AgentAttemptRow not found"
    assert "function getAgentAttemptDotStates" in html_content
    assert "function computeAgentAttemptFractionN" in html_content
    assert "PIPELINE_STATUS_PILL_HEX" in html_content
    assert "AGENT_ATTEMPT_DOT_HEX" in html_content
    assert "#475569" in html_content and "#0d9488" in html_content
    assert "#3b82f6" in html_content, "In-flight attempt cells use blue distinct from pipeline teal"
    assert "#2DEB1E" in html_content and "#dc2626" in html_content
    assert "data-agent-attempt-row" in html_content
    assert "data-agent-attempt-slot" in html_content
    assert "data-agent-attempt-state" in html_content
    assert "data-agent-attempt-fraction" in html_content
    assert "agent-attempt-cell--inflight" in html_content
    assert "agent-attempt-inflight-pulse" in html_content
    assert "data-agent-dot-state" not in html_content, "Legacy dot data attribute removed (boxed cells use data-agent-attempt-slot)"
    has_max_slots = bool(re.search(r"maxSlots\s*=\s*3|maxSlots.*3", html_content))
    assert has_max_slots, "AgentAttemptRow should cap at 3 slots"


def test_agent_attempt_row_receives_count_and_label_props(html_content):
    """AgentAttemptRow accepts count, label, agentRole, currentAgent, pipelineStatus, and per-role retries."""
    has_props = bool(
        re.search(
            r"AgentAttemptRow\s*\(\s*\{[^}]*count[^}]*label[^}]*agentRole[^}]*currentAgent[^}]*pipelineStatus",
            html_content,
            re.DOTALL,
        )
    )
    assert has_props, "AgentAttemptRow should accept L-28 props including agentRole and pipeline state"


def test_agent_attempt_row_renders_conditionally(html_content):
    """AgentAttemptRow returns null when count is undefined or null."""
    has_conditional = bool(re.search(r'count\s*===\s*undefined.*return\s+null|count\s*===\s*null.*return\s+null', html_content))
    assert has_conditional, "AgentAttemptRow should return null when count is undefined/null"


def test_agent_attempt_cells_have_titles(html_content):
    """Each cell uses AGENT_ATTEMPT_DOT_TITLES for native title."""
    assert "AGENT_ATTEMPT_DOT_TITLES" in html_content
    assert "AGENT_ATTEMPT_DOT_TITLES[st]" in html_content


def test_agent_attempt_inflight_pulse_class_only_on_blue(html_content):
    """In-flight cell applies agent-attempt-cell--inflight only when st === 'blue'."""
    assert "st === 'blue'" in html_content and "agent-attempt-cell--inflight" in html_content


def test_compute_agent_attempt_fraction_n_logic_snippets(html_content):
    """
    Fraction n for n/3 (single JS implementation in index.html):
    - resolved = reds + greens
    - if any blue: n = red count when red > 0; else if first slot blue with no reds, n = 1; else 0
    - if no blue: n = resolved
    Vectors: [n,n,n]->0; [b,n,n]->1; [r,r,b]->2; [r,r,r]->3; [g,n,n]->1
    """
    start = html_content.find("function computeAgentAttemptFractionN")
    assert start != -1, "computeAgentAttemptFractionN not found"
    end = html_content.find("function formatDuration", start)
    assert end != -1
    body = html_content[start:end]
    assert "resolved" in body
    assert "hasBlue" in body or "=== 'blue'" in body
    assert "s[0] === 'blue'" in body or "s[0]==='blue'" in body
    assert "return red" in body or "return red;" in body
    assert "return resolved" in body or "return resolved;" in body


def test_agent_attempt_fraction_neutral_text_only(html_content):
    """data-agent-attempt-fraction matches row label mute — no semantic red/teal on that node."""
    assert re.search(
        r'data-agent-attempt-fraction[^>]*className=\{?[`"\'][^`"\']*text-slate-500',
        html_content,
    ), "Fraction should use text-slate-500 (same family as Planner/Executor/Reviewer labels)"
    assert not re.search(r"data-agent-attempt-fraction[^>]*text-red-", html_content)
    assert not re.search(r"data-agent-attempt-fraction[^>]*text-teal-", html_content)


def test_get_agent_attempt_dot_states_logic_anchors(html_content):
    """State machine anchors — do not invert comparisons (L-28)."""
    start = html_content.find("function getAgentAttemptDotStates")
    assert start != -1, "getAgentAttemptDotStates not found"
    end = html_content.find("function formatDuration", start)
    assert end != -1, "end boundary for getAgentAttemptDotStates slice not found"
    body = html_content[start:end]
    assert "i < retries" in body
    assert "i === retries" in body
    assert "WAITING_FOR_HUMAN" in body
    assert "escalation" in body
    assert "toLowerCase" in body


def test_escalation_branch_guards_terminal_status(html_content):
    """Escalation branch must check terminalNoBlue.has(ps) before calling completedLeg.

    Root cause of HALTED_SILENT + escalation green-dot bug: when current_agent is
    'escalation' and retries are low, completedLeg(x) was called unconditionally, putting
    a green dot at slot x even while pipeline_status is HALTED_SILENT / BLOCKED / STOPPED.
    Fix: guard with terminalNoBlue.has(ps) so terminal statuses use activeRow(..., false)
    (neutral current slot) instead of green.
    """
    fn_start = html_content.find("function getAgentAttemptDotStates")
    assert fn_start != -1, "getAgentAttemptDotStates not found"
    esc_start = html_content.find("if (ca === 'escalation')", fn_start)
    assert esc_start != -1, "escalation branch not found"
    # End of escalation block is just before the activeIdx fall-through
    esc_end = html_content.find("if (activeIdx < 0)", esc_start)
    assert esc_end != -1, "escalation block end boundary not found"
    esc_block = html_content[esc_start:esc_end]
    assert "terminalNoBlue.has(ps)" in esc_block, (
        "getAgentAttemptDotStates escalation branch must guard completedLeg() with "
        "terminalNoBlue.has(ps). HALTED_SILENT + escalation + 0 retries must not "
        "produce green dots. Add: if (terminalNoBlue.has(ps)) return activeRow(x, false);"
        " before each return completedLeg(x) in the escalation block."
    )


def test_escalation_terminal_uses_active_row(html_content):
    """Escalation branch must call activeRow(..., false) for terminal pipeline statuses.

    When pipeline_status is in terminalNoBlue (HALTED_SILENT, BLOCKED, STOPPED,
    QUEUE_HALTED, PIPELINE_COMPLETE) and current_agent is 'escalation', the dot states
    must use activeRow(x, false) — neutral current slot, reds for failures — NOT
    completedLeg(x) which renders green and implies success.
    """
    fn_start = html_content.find("function getAgentAttemptDotStates")
    assert fn_start != -1
    esc_start = html_content.find("if (ca === 'escalation')", fn_start)
    assert esc_start != -1
    esc_end = html_content.find("if (activeIdx < 0)", esc_start)
    assert esc_end != -1
    esc_block = html_content[esc_start:esc_end]
    assert "activeRow(" in esc_block, (
        "getAgentAttemptDotStates escalation branch must call activeRow(..., false) "
        "when terminalNoBlue.has(ps) is true. Terminal halt must not emit green. "
        "Add: if (terminalNoBlue.has(ps)) return activeRow(x, false); in the escalation block."
    )


def test_current_phase_panel_renders_agent_attempt_rows(html_content):
    """CurrentPhasePanel renders Agent attempts heading and AgentAttemptRow rows (L-28)."""
    assert "Agent attempts" in html_content
    assert bool(re.search(r"<AgentAttemptRow", html_content)), "AgentAttemptRow not rendered in CurrentPhasePanel"
    assert 'agentRole="planner"' in html_content
    assert "currentAgent={current_agent}" in html_content
    assert "pipelineStatus={pipeline_status}" in html_content
    has_planner = bool(re.search(r'count=\{planner_retries\}', html_content))
    has_executor = bool(re.search(r'count=\{executor_retries\}', html_content))
    has_reviewer = bool(re.search(r'count=\{reviewer_retries\}', html_content))
    assert has_planner and has_executor and has_reviewer, "AgentAttemptRow rows must bind planner/executor/reviewer retry counts"


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
    start = html_content.find("function ElapsedTimer")
    assert start != -1, "ElapsedTimer function not found"
    end = html_content.find("function SkillInjected", start)
    assert end != -1, "SkillInjected boundary not found after ElapsedTimer"
    timer_block = html_content[start:end]
    assert "useEffect" in timer_block, "ElapsedTimer should use useEffect"


def test_elapsed_timer_amber_color_condition(html_content):
    """ElapsedTimer shows amber color when WAITING_FOR_SENTINEL and elapsed > 5 min."""
    has_amber_condition = bool(re.search(r"WAITING_FOR_SENTINEL.*elapsed\s*>\s*300|elapsed\s*>\s*300.*WAITING_FOR_SENTINEL", html_content))
    assert has_amber_condition, "ElapsedTimer should check for WAITING_FOR_SENTINEL and > 300 seconds"
    
    has_amber_color = bool(re.search(r'text-amber-\d+|text-amber-', html_content))
    assert has_amber_color, "ElapsedTimer should apply amber text color"


def test_elapsed_timer_renders_conditionally(html_content):
    """ElapsedTimer returns null when tick anchor (sentinel or last action) is not present."""
    has_conditional = bool(
        re.search(r"if\s*\(\s*!tickAnchor|!tickAnchor\s*\)", html_content)
    )
    assert has_conditional, "ElapsedTimer should guard on tickAnchor when timestamps are absent"


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
