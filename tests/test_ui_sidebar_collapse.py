"""Unified sidebar collapse (mirrors App + Sidebar + IdeasScreen contracts in ui/index.html)."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def nav_rail_outer_class(sidebar_collapsed: bool) -> str:
    """Outer class string for the nav rail (Sidebar root div)."""
    return (
        "w-14 relative flex-shrink-0 bg-[#141618] border-r border-[#1a1d21] "
        "flex flex-col transition-[width] duration-200 ease-out"
        if sidebar_collapsed
        else "w-44 relative flex-shrink-0 bg-[#141618] border-r border-[#1a1d21] "
        "flex flex-col transition-[width] duration-200 ease-out"
    )


def chats_rail_outer_class(sidebar_collapsed: bool) -> str:
    """Outer class string for the chats rail column in IdeasScreen (unmounted when collapsed)."""
    if sidebar_collapsed:
        return "__chats_rail_absent_when_collapsed__"
    return (
        "w-56 sm:w-60 relative flex-shrink-0 flex flex-col bg-[#0d0f11] "
        "border-r border-[#1a1d21] transition-[width] duration-200 ease-out"
    )


def chats_rail_shows_project_chrome(sidebar_collapsed: bool) -> bool:
    """When True, render Chats header, filter, and project rows."""
    return not sidebar_collapsed


def test_nav_rail_width_tokens():
    assert "w-14" in nav_rail_outer_class(True)
    assert "w-44" in nav_rail_outer_class(False)
    assert "w-14" not in nav_rail_outer_class(False)


def test_chats_rail_collapsed_unmounted_no_reserved_strip():
    s = chats_rail_outer_class(True)
    assert s == "__chats_rail_absent_when_collapsed__"
    assert "w-16" not in chats_rail_outer_class(False)


def test_chats_rail_expanded_width():
    s = chats_rail_outer_class(False)
    assert "w-56" in s
    assert "sm:w-60" in s


def test_chats_rail_chrome_visibility():
    assert chats_rail_shows_project_chrome(True) is False
    assert chats_rail_shows_project_chrome(False) is True


def test_toggle_inverts_nav_and_chats():
    for collapsed in (True, False):
        nav = nav_rail_outer_class(collapsed)
        rail = chats_rail_outer_class(collapsed)
        chrome = chats_rail_shows_project_chrome(collapsed)
        inv = not collapsed
        assert nav != nav_rail_outer_class(inv)
        assert rail != chats_rail_outer_class(inv)
        assert chrome is not chats_rail_shows_project_chrome(inv)


def test_index_html_sidebar_unified_wiring():
    html = (_repo_root() / "ui" / "index.html").read_text(encoding="utf-8")
    assert "navCollapsed" not in html
    assert "chatsRailCollapsed" not in html
    assert "setNavCollapsed" not in html
    assert "setChatsRailCollapsed" not in html
    assert "sidebarCollapsed" in html
    assert "setSidebarCollapsed" in html
    assert "M2 ideas merged: one column" in html
    assert "PrimaryNavColumn" in html
