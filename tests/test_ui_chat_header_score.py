"""Ideas chat header: no PRD readiness score in header bar (UI-9)."""

from __future__ import annotations

import os


def _index_html_text() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "ui", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _html() -> str:
    return _index_html_text()


def test_header_bar_no_assessing_branch():
    html = _html()
    assert 'activeDocTab === "prd" && currentIdeaId && readinessStatus === "updating"' not in html


def test_header_bar_no_ready_score_branch():
    html = _html()
    assert 'activeDocTab === "prd" && currentIdeaId && readinessStatus === "ready" && readinessData &&' not in html


def test_readiness_state_still_wired():
    html = _html()
    assert "readinessData" in html
    assert "setReadinessData" in html
    assert "readinessStatus" in html


def test_prd_panel_readiness_block_still_present():
    html = _html()
    assert 'readinessStatus === "ready" && readinessData &&' in html
