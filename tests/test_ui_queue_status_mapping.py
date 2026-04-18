"""Guardrails: queue row pills prefer live_pipeline_status; queue BLOCKED vs pipeline BLOCKED."""
import re
from pathlib import Path

import pytest

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


def test_queue_row_display_checks_live_first(html_content):
    """queueRowDisplay must read live_pipeline_status before queue state fallbacks."""
    idx = html_content.find("function queueRowDisplay")
    assert idx != -1, "queueRowDisplay not found"
    snippet = html_content[idx : idx + 3500]
    live_pos = snippet.find("live_pipeline_status")
    assert live_pos != -1, "queueRowDisplay should use live_pipeline_status"
    state_pos = snippet.find("entry.state", live_pos)
    assert state_pos > live_pos, "queue fallbacks should come after live resolution"


def test_queue_blocked_label_and_tooltip(html_content):
    assert "QUEUE BLOCKED" in html_content
    assert "Row is blocked in queue" in html_content


def test_pipeline_blocked_tooltip_phrase(html_content):
    assert "Pipeline execution is blocked" in html_content


def test_terminal_pipeline_status_not_teal_pulse(html_content):
    """STOPPED / terminal states must not use run-pulse in pipeline live map."""
    for term in ("STOPPED", "HALTED_SILENT", "PIPELINE_COMPLETE", "QUEUE_HALTED"):
        for m in re.finditer(rf"['\"]{re.escape(term)}['\"]\s*:", html_content):
            frag = html_content[m.start() : m.start() + 220]
            if "run-pulse" in frag:
                pytest.fail(f"{term} mapping must not include run-pulse near key")


def test_active_queue_slot_label(html_content):
    """Queue-only ACTIVE slot uses ACTIVE label (matches queue/API enum, L-01)."""
    assert re.search(
        r"ACTIVE:\s*\{[^}]*label:\s*['\"]ACTIVE['\"]",
        html_content,
    ), "ACTIVE label for queue-only ACTIVE slot expected (queueOnlyRowPill)"
