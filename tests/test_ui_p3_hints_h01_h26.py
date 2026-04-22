"""P3 UX hints H-01–H-17, H-22, H-25, H-26 — native title / placeholder strings in ui/index.html."""

from pathlib import Path

import pytest

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

H01 = "Blocked until the parent project completes. Clears automatically."
H02 = "Preflight failed for this row (or a parent); clears automatically when checks pass."
H03 = "Gate failed; retries exhausted. See phase state for the specific gate."
H04 = "Awaiting your decision in the active phase."
H05 = "Overwrites any existing parent. Child enters Waiting on parent until the parent completes."
H06 = "Removes parent and restores the entry to READY."
H07 = "Required format check before Run Preflight."
H08 = "Required before Launch."
H09 = "Creates phase branch and starts the orchestrator. Use Stop Pipeline to halt."
H12 = (
    "Runs the full preflight (symlink, .gitignore, workspace files, roadmap). "
    "Not the same as the lightweight queue check."
)
H13 = (
    "~30–90s. Checks PRD vs. roadmap consistency. "
    "Produces commentary in the thread, does not edit the PRD."
)
H14 = (
    "~30–90s. Stress-tests the PRD for edge cases and missing assumptions. "
    "Produces commentary only."
)
H15 = "PRD-agent score. 8+ recommended before Generate Roadmap."
H16 = "Agent confidence before roadmap generation."
H17_PLACEHOLDER = "/absolute/path/to/existing/git/repo"
H22 = "Continues from the current agent in the current phase."
H25 = "Phase ID from roadmap.md."
H26_ACTIVITY = "Orchestrator + gate + webhook events on Activity tab; escalation-only on Escalation tab."


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


def test_preflight_button_titles_h07_h08_h09_h12(html_content):
    """H-07, H-08, H-09, H-12: Setup & Preflight primary actions."""
    assert H07 in html_content
    assert H08 in html_content
    assert H09 in html_content
    assert H12 in html_content
    assert "title={P3_PREFLIGHT_TITLE_VALIDATE_ROADMAP}" in html_content
    assert "title={P3_PREFLIGHT_TITLE_LAUNCH_NOW}" in html_content
    assert "P3_PREFLIGHT_TITLE_RERUN_PREFLIGHT" in html_content
    assert "P3_PREFLIGHT_TITLE_RUN_PREFLIGHT" in html_content


def test_alignment_adversarial_titles_h13_h14(html_content):
    assert H13 in html_content
    assert H14 in html_content
    assert "title={P3_PRD_TITLE_ALIGNMENT_CHECK}" in html_content
    assert "title={P3_PRD_TITLE_ADVERSARIAL_REVIEW}" in html_content


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
