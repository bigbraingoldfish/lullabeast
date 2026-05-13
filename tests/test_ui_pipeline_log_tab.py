"""Tests for the PipelineLogPanel component and the third 'Pipeline log' tab.

TDD: these tests are written before implementation and must fail until
Steps 3 and 4 of the implementation plan are complete.
"""
import re
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture(scope="module")
def html_content():
    if not INDEX_HTML.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML}")
    return INDEX_HTML.read_text()


# ---------------------------------------------------------------------------
# PipelineLogPanel component structure
# ---------------------------------------------------------------------------

def test_pipeline_log_panel_component_exists(html_content):
    """PipelineLogPanel function component must be defined in index.html."""
    assert re.search(r"function\s+PipelineLogPanel\s*\(", html_content), (
        "PipelineLogPanel component not found in index.html"
    )


def test_pipeline_log_fetches_api_log_tail(html_content):
    """PipelineLogPanel must fetch from /api/log/tail."""
    assert re.search(r"fetch\s*\(\s*['\"]\/api\/log\/tail", html_content), (
        "PipelineLogPanel must call fetch('/api/log/tail')"
    )


def test_pipeline_log_polls_every_3_seconds(html_content):
    """PipelineLogPanel must set up a 3000 ms polling interval that calls fetchLog."""
    # Must have setInterval calling fetchLog with 3000 ms — not just any setInterval
    assert re.search(r"setInterval\s*\(\s*fetchLog\s*,\s*3000", html_content), (
        "PipelineLogPanel must use setInterval(fetchLog, 3000)"
    )


def test_pipeline_log_has_scroll_ref(html_content):
    """PipelineLogPanel must use a scrollRef for auto-scroll behaviour."""
    assert "scrollRef" in html_content, (
        "PipelineLogPanel must have a scrollRef for auto-scroll"
    )


def test_pipeline_log_has_scroll_to_bottom_button(html_content):
    """PipelineLogPanel must have a 'Scroll to bottom' button."""
    assert "Scroll to bottom" in html_content, (
        "'Scroll to bottom' button not found in index.html"
    )


def test_pipeline_log_tracks_user_scroll_state(html_content):
    """PipelineLogPanel must track whether the user has scrolled up."""
    assert "userScrolledUp" in html_content, (
        "PipelineLogPanel must track userScrolledUp state"
    )


def test_pipeline_log_empty_state_message(html_content):
    """PipelineLogPanel must show a message when the log file is absent or empty."""
    assert "orchestrator.log not found or empty" in html_content, (
        "PipelineLogPanel empty-state message not found"
    )


# ---------------------------------------------------------------------------
# Third tab wired into ActivityFeedPanel
# ---------------------------------------------------------------------------

def test_activity_feed_has_pipeline_log_tab_button(html_content):
    """ActivityFeedPanel must render a 'Pipeline log' TabButton (label prop, not just help text)."""
    assert re.search(r"""label\s*=\s*["']Pipeline log["']""", html_content), (
        "TabButton with label='Pipeline log' not found in index.html"
    )


def test_activity_feed_has_three_tab_buttons(html_content):
    """ActivityFeedPanel must have all three tab labels as TabButton label props."""
    for label in ("Activity", "Escalation", "Pipeline log"):
        assert re.search(rf"""label\s*=\s*["']{label}["']""", html_content), (
            f"TabButton with label='{label}' not found in index.html"
        )


def test_pipeline_log_tab_key_is_log(html_content):
    """The Pipeline log TabButton must use tab='log' or tab=\"log\"."""
    assert re.search(r"""tab=['"](log)['"]""", html_content), (
        "Pipeline log tab must have tab='log' key"
    )


def test_pipeline_log_content_renders_pipeline_log_panel(html_content):
    """When activityTab === 'log', PipelineLogPanel must be rendered."""
    assert re.search(
        r"""activityTab\s*===\s*['"]log['"]""",
        html_content,
    ), "Content render must branch on activityTab === 'log'"

    assert re.search(r"<PipelineLogPanel\s*/?>", html_content), (
        "<PipelineLogPanel /> must appear in the content render block"
    )
