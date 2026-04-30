"""W4-F: last_action subtitle below goal text in CurrentPhasePanel.

Static content checks — no server needed.
"""
from pathlib import Path


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def _panel_body(html: str) -> str:
    start = html.index("function CurrentPhasePanel(")
    end = html.index("const RoadmapPanel", start)
    return html[start:end]


def test_last_action_in_prop_list():
    """last_action appears in CurrentPhasePanel's prop destructure."""
    panel = _panel_body(_html())
    assert "last_action," in panel


def test_last_action_truncation():
    """last_action is sliced to 80 chars."""
    panel = _panel_body(_html())
    assert "last_action.slice(0, 80)" in panel


def test_last_action_italic_styling():
    """Subtitle uses italic + slate-500 styling."""
    panel = _panel_body(_html())
    assert "italic" in panel
    assert "text-slate-500" in panel


def test_last_action_conditional_render():
    """Subtitle only renders when last_action is truthy."""
    panel = _panel_body(_html())
    assert "{last_action &&" in panel


def test_last_action_wired_at_call_site():
    """last_action is passed from PipelineScreen to CurrentPhasePanel."""
    html = _html()
    assert "last_action={pState.last_action}" in html
