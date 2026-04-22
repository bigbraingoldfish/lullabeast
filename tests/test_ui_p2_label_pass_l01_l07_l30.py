"""P2 UX labels L-01–L-07, L-30 — user-facing pill strings in ui/index.html."""
import re
from pathlib import Path

import pytest

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


def test_pipeline_live_pill_l02_l03_l04_l07_labels(html_content):
    """PIPELINE_LIVE_PILL uses operator-facing labels (not raw WAITING/HALTED/QUEUE)."""
    assert re.search(
        r"WAITING_FOR_SENTINEL:\s*\{[^}]*label:\s*['\"]Running['\"]",
        html_content,
    ), "WAITING_FOR_SENTINEL base label is Running; agent comes from formatWaitForSentinelLabel"
    assert "formatWaitForSentinelLabel" in html_content
    assert re.search(
        r"WAITING_FOR_HUMAN:\s*\{[^}]*label:\s*['\"]NEEDS YOUR INPUT['\"]",
        html_content,
    ), "WAITING_FOR_HUMAN should display NEEDS YOUR INPUT"
    assert re.search(
        r"HALTED_SILENT:\s*\{[^}]*label:\s*['\"]INTERVENTION REQUIRED['\"]",
        html_content,
    ), "HALTED_SILENT should display INTERVENTION REQUIRED"
    assert re.search(
        r"QUEUE_HALTED:\s*\{[^}]*label:\s*['\"]Queue stalled['\"]",
        html_content,
    ), "QUEUE_HALTED should display Queue stalled"


def test_halted_silent_has_escalation_hint_title(html_content):
    """HALTED_SILENT pill includes a title hint for operators (L-04)."""
    m = re.search(r"HALTED_SILENT:\s*\{([^}]+)\}", html_content)
    assert m, "HALTED_SILENT entry in PIPELINE_LIVE_PILL"
    block = m.group(1)
    assert "escalation_failed" in block.lower(), "HALTED_SILENT title should mention escalation_failed.json"


def test_queue_only_row_pill_l05_l06(html_content):
    """Queue-only states use friendly labels (L-05, L-06)."""
    assert re.search(
        r"DEPENDENCY_HOLD:\s*\{[^}]*label:\s*['\"]Waiting on parent['\"]",
        html_content,
    )
    assert re.search(
        r"SKIPPED_PENDING:\s*\{[^}]*label:\s*['\"]Preflight failed['\"]",
        html_content,
    )


def test_queue_halted_trigger_reason_map_present(html_content):
    """Client maps machine queue_halted_reason to short user copy (L-07)."""
    assert "queue_halted_reason" in html_content or "QUEUE_HALTED_TRIGGER" in html_content, (
        "handleTriggerNext should reference queue_halted_reason or a named reason map"
    )


def test_header_sentinel_uses_format_wait_for_sentinel(html_content):
    """Header WAITING_FOR_SENTINEL uses short Running {agent} via formatWaitForSentinelLabel."""
    assert html_content.find("if (status === 'WAITING_FOR_SENTINEL')") != -1
    assert "formatWaitForSentinelLabel(pState.current_agent)" in html_content
    assert "RUNNING (agent)" not in html_content, "Remove RUNNING (agent) copy from sentinel UI"
    assert "WAITING —" not in html_content, "No legacy WAITING — header"


def test_no_user_visible_current_queue_pill_label(html_content):
    """L-01: user-visible queue ACTIVE slot must not say CURRENT."""
    assert not re.search(r"label:\s*['\"]CURRENT['\"]", html_content), (
        "CURRENT should not be a queue pill label; use ACTIVE"
    )
