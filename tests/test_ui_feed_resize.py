"""Tests for the resizable Activity Feed panel in PipelineScreen.

TDD: these tests are written before implementation and must fail until
Step 5 of the implementation plan is complete.
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


def test_feed_height_state_in_pipeline_screen(html_content):
    """PipelineScreen must declare feedHeight state."""
    assert "feedHeight" in html_content, (
        "feedHeight state not found in index.html"
    )


def test_feed_height_ref_in_pipeline_screen(html_content):
    """PipelineScreen must use feedHeightRef to avoid stale-closure drift in drag handlers."""
    assert "feedHeightRef" in html_content, (
        "feedHeightRef not found in index.html"
    )


def test_feed_resize_handle_element_exists(html_content):
    """A drag-handle element with data-testid='feed-resize-handle' must be present."""
    assert 'data-testid="feed-resize-handle"' in html_content or \
           "data-testid='feed-resize-handle'" in html_content, (
        "feed-resize-handle element not found in index.html"
    )


def test_feed_resize_handle_has_cursor_style(html_content):
    """The resize handle must have cursor-row-resize for discoverability."""
    assert "cursor-row-resize" in html_content, (
        "cursor-row-resize class not found — resize handle needs drag cursor"
    )


def test_feed_mousedown_handler_exists(html_content):
    """handleResizeMouseDown must be defined."""
    assert "handleResizeMouseDown" in html_content, (
        "handleResizeMouseDown handler not found in index.html"
    )


def test_feed_mousemove_handler_exists(html_content):
    """handleResizeMouseMove must be defined."""
    assert "handleResizeMouseMove" in html_content, (
        "handleResizeMouseMove handler not found in index.html"
    )


def test_feed_mouseup_handler_exists(html_content):
    """handleResizeMouseUp must be defined."""
    assert "handleResizeMouseUp" in html_content, (
        "handleResizeMouseUp handler not found in index.html"
    )


def test_feed_resize_clamp_min_80(html_content):
    """Resize must clamp to a minimum of 80 px."""
    assert re.search(r"minH\s*=\s*80|Math\.max\s*\(\s*80", html_content), (
        "Resize minimum of 80 px not found in index.html"
    )


def test_feed_resize_clamp_max_50vh(html_content):
    """Resize must clamp to a maximum of 50% of window height."""
    assert re.search(r"window\.innerHeight\s*\*\s*0\.5", html_content), (
        "Resize maximum of window.innerHeight * 0.5 not found in index.html"
    )


def test_feed_height_default_is_25vh(html_content):
    """feedHeight default must be window.innerHeight * 0.25 (≈ 25vh)."""
    assert re.search(r"window\.innerHeight\s*\*\s*0\.25", html_content), (
        "Default feedHeight of window.innerHeight * 0.25 not found in index.html"
    )


def test_feed_height_not_persisted_to_localstorage(html_content):
    """feedHeight must NOT be written to localStorage (no persistence across reloads)."""
    # Reject any localStorage.setItem call that includes 'feedHeight'
    assert not re.search(r"localStorage\.setItem.*feedHeight", html_content), (
        "feedHeight must not be persisted to localStorage"
    )


def test_feed_uses_inline_style_for_dynamic_height(html_content):
    """The feed container must use an inline style for dynamic height (not a fixed CSS class)."""
    assert re.search(r"gridTemplateRows.*feedHeight|feedHeight.*px", html_content), (
        "Feed container must use feedHeight in an inline style for dynamic height"
    )
