"""ElapsedTimer uses sentinelWaitStartedAt, orchestratorAlive, and offline suffix."""

import re
from pathlib import Path

import pytest

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail("index.html not found")
    return INDEX_HTML_PATH.read_text(encoding="utf-8")


def test_elapsed_timer_accepts_sentinel_wait_started_at_prop(html_content):
    assert re.search(
        r"function\s+ElapsedTimer\s*\(\s*\{[^}]*sentinelWaitStartedAt",
        html_content,
        re.DOTALL,
    ), "ElapsedTimer should accept sentinelWaitStartedAt"


def test_elapsed_timer_accepts_orchestrator_alive_prop(html_content):
    assert re.search(
        r"function\s+ElapsedTimer\s*\(\s*\{[^}]*orchestratorAlive",
        html_content,
        re.DOTALL,
    ), "ElapsedTimer should accept orchestratorAlive"


def test_elapsed_timer_shows_offline_indicator(html_content):
    assert "orchestrator offline" in html_content


def test_current_phase_panel_passes_elapsed_timer_new_props(html_content):
    assert "sentinel_wait_started_at" in html_content and "orchestrator_alive" in html_content
    assert re.search(
        r"sentinelWaitStartedAt=\{sentinel_wait_started_at\}",
        html_content,
    )
