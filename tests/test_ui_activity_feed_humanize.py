"""Tests for humanizeSummary() and display map cleanup in Activity Feed."""
import pytest
import re
from pathlib import Path

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


# --- humanizeSummary function exists ---

def test_humanize_summary_function_exists(html_content):
    assert bool(re.search(r"function\s+humanizeSummary", html_content)), \
        "humanizeSummary function not found"


# --- Human-readable summary strings per event type ---

def test_humanize_gate_pass_readable(html_content):
    assert "Review passed" in html_content, \
        "gate_pass should produce 'Review passed' summary"

def test_humanize_gate_fail_route_executor(html_content):
    assert "sending back to executor" in html_content, \
        "gate_fail ROUTE_EXECUTOR should mention sending back to executor"

def test_humanize_gate_fail_route_escalate(html_content):
    assert "escalating for human input" in html_content, \
        "gate_fail ROUTE_ESCALATE should mention escalating for human input"

def test_humanize_gate_fail_missing_artifacts(html_content):
    assert bool(re.search(r"output files missing", html_content, re.IGNORECASE)), \
        "gate_fail MISSING_ARTIFACTS should mention output files missing"

def test_humanize_gate_fail_exit_code(html_content):
    assert bool(re.search(r"exit code|exited with error", html_content, re.IGNORECASE)), \
        "gate_fail with exit_code should mention exit code"

def test_humanize_phase_complete_first_attempt(html_content):
    assert "first attempt" in html_content, \
        "phase_complete with 1 attempt should say 'first attempt'"

def test_humanize_phase_complete_multiple_attempts(html_content):
    assert "executor attempts" in html_content, \
        "phase_complete with N attempts should say 'N executor attempts'"

def test_humanize_phase_complete_blame_cycles(html_content):
    assert "blame cycle" in html_content, \
        "phase_complete with blame_fires > 0 should mention blame cycles"

def test_humanize_escalation_trigger_planner(html_content):
    assert "ran out of retries" in html_content, \
        "escalation_trigger for planner should say 'ran out of retries'"

def test_humanize_escalation_trigger_blame_cap(html_content):
    assert bool(re.search(r"blame cap hit|blame cap reached", html_content, re.IGNORECASE)), \
        "escalation_trigger for impl blame should mention blame cap"

def test_humanize_escalation_trigger_reviewer_timeout(html_content):
    assert bool(re.search(r"[Rr]eviewer timed out", html_content)), \
        "escalation_trigger for reviewer timeout should say 'Reviewer timed out'"

def test_humanize_escalation_resolve_retry(html_content):
    assert bool(re.search(r"retry current agent", html_content, re.IGNORECASE)), \
        "escalation_resolve RETRY should say 'retry current agent'"

def test_humanize_escalation_resolve_reset_phase(html_content):
    assert bool(re.search(r"restart entire phase", html_content, re.IGNORECASE)), \
        "escalation_resolve RESET_PHASE should say 'restart entire phase'"

def test_humanize_escalation_resolve_stop(html_content):
    assert bool(re.search(r"stop pipeline", html_content, re.IGNORECASE)), \
        "escalation_resolve STOP should say 'stop pipeline'"

def test_humanize_status_changed(html_content):
    assert bool(re.search(r"[Pp]ipeline state updated", html_content)), \
        "status_changed should say 'Pipeline state updated'"


# --- Dead event types removed from display map ---

def test_event_type_display_no_dead_entries(html_content):
    display_block = re.search(r"EVENT_TYPE_DISPLAY\s*=\s*\{.*?\}", html_content, re.DOTALL)
    assert display_block, "EVENT_TYPE_DISPLAY map not found"
    block = display_block.group(0)
    dead_types = ["phase_skip", "skill_inject", "pipeline_start", "pipeline_complete",
                  "orchestrator_crash", "heartbeat_resume", "phase_start", "gate_verdict",
                  "agent_retry", "webhook_invoke", "state_transition"]
    for dead in dead_types:
        assert dead not in block, f"Dead event type '{dead}' should be removed from EVENT_TYPE_DISPLAY"


# --- Badge labels shortened ---

def test_badge_label_gate_pass_shortened(html_content):
    display_block = re.search(r"EVENT_TYPE_DISPLAY\s*=\s*\{.*?\}", html_content, re.DOTALL)
    assert display_block, "EVENT_TYPE_DISPLAY map not found"
    block = display_block.group(0)
    assert bool(re.search(r"gate_pass.*?['\"]Passed['\"]", block)), \
        "gate_pass label should be 'Passed'"

def test_badge_label_gate_fail_shortened(html_content):
    display_block = re.search(r"EVENT_TYPE_DISPLAY\s*=\s*\{.*?\}", html_content, re.DOTALL)
    assert display_block, "EVENT_TYPE_DISPLAY map not found"
    block = display_block.group(0)
    assert bool(re.search(r"gate_fail.*?['\"]Failed['\"]", block)), \
        "gate_fail label should be 'Failed'"

def test_badge_label_phase_complete_shortened(html_content):
    display_block = re.search(r"EVENT_TYPE_DISPLAY\s*=\s*\{.*?\}", html_content, re.DOTALL)
    assert display_block, "EVENT_TYPE_DISPLAY map not found"
    block = display_block.group(0)
    assert bool(re.search(r"phase_complete.*?['\"]Complete['\"]", block)), \
        "phase_complete label should be 'Complete'"

def test_badge_label_escalation_trigger_shortened(html_content):
    display_block = re.search(r"EVENT_TYPE_DISPLAY\s*=\s*\{.*?\}", html_content, re.DOTALL)
    assert display_block, "EVENT_TYPE_DISPLAY map not found"
    block = display_block.group(0)
    assert bool(re.search(r"escalation_trigger.*?['\"]Escalated['\"]", block)), \
        "escalation_trigger label should be 'Escalated'"

def test_badge_label_escalation_resolve_shortened(html_content):
    display_block = re.search(r"EVENT_TYPE_DISPLAY\s*=\s*\{.*?\}", html_content, re.DOTALL)
    assert display_block, "EVENT_TYPE_DISPLAY map not found"
    block = display_block.group(0)
    assert bool(re.search(r"escalation_resolve.*?['\"]Resolved['\"]", block)), \
        "escalation_resolve label should be 'Resolved'"

def test_badge_label_status_changed_shortened(html_content):
    display_block = re.search(r"EVENT_TYPE_DISPLAY\s*=\s*\{.*?\}", html_content, re.DOTALL)
    assert display_block, "EVENT_TYPE_DISPLAY map not found"
    block = display_block.group(0)
    assert bool(re.search(r"status_changed.*?['\"]Updated['\"]", block)), \
        "status_changed label should be 'Updated'"
