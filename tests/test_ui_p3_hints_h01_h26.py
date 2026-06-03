"""P3 UX hints H-01–H-17, H-22, H-23, H-25, H-26 — native title / placeholder strings in ui/index.html."""

from pathlib import Path

import pytest

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

H01 = "Blocked until the parent project completes. Clears automatically."
H02 = "Preflight failed for this row (or a parent); clears automatically when checks pass."
H03 = "Gate failed; retries exhausted. See phase state for the specific gate."
H04 = "Awaiting your decision in the active phase."
H05 = "Overwrites any existing parent. Child enters Waiting on parent until the parent completes."
H06 = "Removes parent and restores the entry to READY."
# H07 — was "Required format check before Run Preflight." — title on the Step 2
# "Validate roadmap" button. P0 Stage J.4 removed that button (Setup-screen
# Step 2 became a read-only summary card of the linked Project Idea), so the
# title constant and its assertion are gone with it.
H08 = "Required before Launch."
H09 = "Creates phase branch and starts the orchestrator. Use Stop Pipeline to halt."
H12 = (
    "Runs the full preflight (symlink, .gitignore, workspace files, roadmap). "
    "Not the same as the lightweight queue check."
)
H15 = "PRD-agent score. 8+ recommended before Generate Roadmap."
H16 = "Agent confidence before roadmap generation."
H17_PLACEHOLDER = "/absolute/path/to/existing/git/repo"
H22 = "Continues from the current agent in the current phase."
H25 = "Phase ID from roadmap.md."
H26_ACTIVITY = "Orchestrator + gate + webhook events on Activity tab; escalation-only on Escalation tab; live orchestrator log on Pipeline log tab."


@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


def test_queue_only_row_pill_hints_h01_h04(html_content):
    """H-01–H-04: queue row pills use P3_QUEUE_ONLY_PILL_TITLES (non-empty hints)."""
    assert H01 in html_content, "H-01 DEPENDENCY_HOLD tooltip"
    assert H02 in html_content, "H-02 SKIPPED_PENDING tooltip"
    assert H03 in html_content, "H-03 FAILED tooltip"
    assert H04 in html_content, "H-04 ESCALATION tooltip"
    assert "P3_QUEUE_ONLY_PILL_TITLES" in html_content
    assert "title: P3_QUEUE_ONLY_PILL_TITLES.DEPENDENCY_HOLD" in html_content
    assert "title: P3_QUEUE_ONLY_PILL_TITLES.SKIPPED_PENDING" in html_content
    assert "title: P3_QUEUE_ONLY_PILL_TITLES.FAILED" in html_content
    assert "title: P3_QUEUE_ONLY_PILL_TITLES.ESCALATION" in html_content


def test_depends_on_select_merged_parent_hints_h05_h06(html_content):
    """H-05/H-06: Depends on select documents set + clear parent (single native title)."""
    assert H05 in html_content and H06 in html_content
    assert 'label className="header-text text-xs text-slate-400 mb-1 block">Depends on' in html_content
    assert "Depends on" in html_content
    idx = html_content.find("Depends on")
    assert idx != -1
    window = html_content[idx : idx + 1200]
    assert "handleSetParent" in window or "onChange={e => handleSetParent" in html_content
    assert H05 in window and H06 in window, "Merged parent hint should appear near Depends on control"


def test_preflight_button_titles_h08_h09_h12(html_content):
    """H-08, H-09, H-12: Setup & Preflight primary actions.

    Originally also covered H-07 ("Required format check before Run Preflight.")
    — the native title on the Step 2 "Validate roadmap" button. P0 Stage J.4
    removed that button when it replaced the free-text textarea with a
    read-only summary card of the linked Project Idea. H-07 stays in the
    docstring index as a historical anchor; the assertion is gone."""
    assert H08 in html_content
    assert H09 in html_content
    assert H12 in html_content
    assert "title={P3_PREFLIGHT_TITLE_LAUNCH_NOW}" in html_content
    assert "P3_PREFLIGHT_TITLE_RERUN_PREFLIGHT" in html_content
    assert "P3_PREFLIGHT_TITLE_RUN_PREFLIGHT" in html_content


def test_readiness_strip_titles_h15_h16(html_content):
    assert H15 in html_content
    assert H16 in html_content
    assert "title={P3_PRD_TITLE_READINESS_SCORE}" in html_content
    assert "title={P3_PRD_TITLE_ROADMAP_CONFIDENCE}" in html_content


def test_queue_add_placeholder_h17(html_content):
    assert H17_PLACEHOLDER in html_content
    assert 'id="queue-add-path"' in html_content
    q_idx = html_content.find('id="queue-add-path"')
    qw = html_content[q_idx : q_idx + 800]
    assert "placeholder={P3_QUEUE_ADD_PATH_PLACEHOLDER}" in qw


def test_stopped_recovery_resume_title_h22(html_content):
    assert H22 in html_content
    assert "StoppedRecoveryPanel" in html_content
    assert "command: 'RETRY'" in html_content or 'command: "RETRY"' in html_content


def test_current_phase_raw_id_title_h25(html_content):
    assert H25 in html_content
    assert "P3_PIPELINE_PHASE_RAW_ID_TITLE" in html_content
    assert "title={P3_PIPELINE_PHASE_RAW_ID_TITLE}" in html_content


def test_activity_escalation_tab_titles_h26(html_content):
    assert H26_ACTIVITY in html_content
    assert 'tab="activity"' in html_content
    assert "TabButton" in html_content


def test_server_path_input_accepts_placeholder_prop(html_content):
    assert "function ServerPathInput" in html_content
    start = html_content.find("function ServerPathInput")
    block = html_content[start : start + 450]
    assert "placeholder" in block
    assert 'placeholder = "/path/to/your-project/my-app"' in block


# H-23: getLastErrorCodeTitle — long-form copy for gate last_error_code (activity feed gate_fail + tooltips)

H23_ERR_UNACCOUNTED = (
    "Executor Error: files were removed that weren’t part of the phase’s declared work. "
    "Discarded work. Attempting retry."
)
H23_ERR_TESTS = "Executor Error: tests failed. Discarded work. Attempting retry."
H23_ERR_GIT_DIFF = (
    "Executor Error: a required Git check could not run in the project. Action required: fix Git in the "
    "working tree (corrupt repo, lock, or permissions) — the pipeline will not get past this step until "
    "Git commands succeed. Automatic retries alone will not fix a broken repository."
)
H23_ERR_REVIEWER_CONTRACT = (
    "Reviewer produced no usable review output (the session ended without a "
    "readable reviewer_output.json). Retrying in a fresh session with a "
    "corrective directive."
)
H23_ERR_PROVIDER_REJECTED = (
    "Inference provider rejected the request \\u2014 check your API key, credits, or rate limits, "
    "then restart the pipeline."
)
H23_ERR_SESSION_DEAD_ON_ARRIVAL = (
    "Inference provider rejected the session before it started \\u2014 check your API key and provider status."
)
H23_V_PLANNER = "Planner Error: output didn’t pass validation. Retrying if attempts remain."
H23_V_REVIEWER = (
    "Reviewer Error: checks or review output didn’t pass. "
    "Next step is picked automatically (executor, planner, or escalation)."
)
H23_V_FALLBACK = "Error: validation failed. See logs for what happens next."
H23_DEFAULT = "Something in the last pipeline step failed. See orchestrator or gate logs for details."


def test_last_error_code_titles_h23(html_content):
    """H-23 string corpus + title map remain; chip removed; feed gate_fail uses titles."""
    assert H23_ERR_UNACCOUNTED in html_content
    assert H23_ERR_TESTS in html_content
    assert H23_ERR_GIT_DIFF in html_content
    assert H23_ERR_REVIEWER_CONTRACT in html_content
    assert H23_ERR_PROVIDER_REJECTED in html_content
    assert H23_ERR_SESSION_DEAD_ON_ARRIVAL in html_content
    assert H23_V_PLANNER in html_content
    assert H23_V_REVIEWER in html_content
    assert H23_V_FALLBACK in html_content
    assert H23_DEFAULT in html_content
    assert "function getLastErrorCodeTitle" in html_content
    assert "P3_LAST_ERROR_CODE_DEFAULT_TITLE" in html_content
    assert 'data-testid="last-error-code"' not in html_content
    assert "<LastErrorCode" not in html_content
    gf = html_content.find("function humanizeSummary(event)")
    assert gf != -1
    block = html_content[gf : gf + 3500]
    assert "case 'gate_fail': {" in block
    gf_case = block.index("case 'gate_fail': {")
    sub = block[gf_case : gf_case + 2000]
    assert "d.last_error_code" in sub or "d['last_error_code']" in sub
    assert "getLastErrorCodeTitle" in sub
