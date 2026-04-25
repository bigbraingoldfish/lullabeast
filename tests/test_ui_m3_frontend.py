"""M3 frontend contract: sidebar width, chats rail scrollbar."""
import os


def _index_html_text() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "ui", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_sidebar_expanded_width_is_240px_not_220px_for_rails():
    """Primary nav default + Sidebar + IdeasScreen use w-[240px]; annotation pill keeps max-w-[220px]."""
    html = _index_html_text()
    assert 'const expW = expandedWidthClass || "w-[240px]";' in html
    assert html.count('expandedWidthClass="w-[240px]"') == 2
    assert "max-w-[220px]" in html
    assert 'const expW = expandedWidthClass || "w-[220px]";' not in html


def test_chats_list_uses_sidebar_scroll_overlay_class():
    """Chats project list scroll area uses .sidebar-scroll (not overflow-y-auto on that row)."""
    html = _index_html_text()
    assert ".sidebar-scroll" in html
    assert "overflow-y: overlay" in html
    assert 'className="flex-1 sidebar-scroll py-1 px-3 space-y-1 min-h-0"' in html
    assert 'className="flex-1 overflow-y-auto py-1 px-3 space-y-1 min-h-0"' not in html


def test_hover_card_empty_summary_string_present():
    """M4 hover card empty summary uses em dash (U+2014), not hyphen."""
    html = _index_html_text()
    assert "New project \u2014 no documentation yet." in html