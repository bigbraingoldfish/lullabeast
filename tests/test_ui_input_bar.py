"""Project Ideas chat input composer layout (mirrors IdeasScreen in ui/index.html)."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def input_composer_outer_class() -> str:
    """Outer wrapper: textarea stack above action row."""
    return "flex flex-col gap-2"


def input_action_row_class() -> str:
    """Row under textarea: Attach left, Send right."""
    return "flex items-center justify-between gap-2"


def send_disabled(current_idea_id: str | None, input_text: str, is_loading: bool) -> bool:
    return not current_idea_id or not (input_text or "").strip() or is_loading


def attach_disabled(current_idea_id: str | None, is_loading: bool) -> bool:
    return not current_idea_id or is_loading


def send_button_class_for_state(disabled: bool) -> str:
    """Tailwind class string for Send (matches index.html pattern)."""
    base = (
        "header-text px-3 rounded border border-[#e2b14c]/50 bg-[#e2b14c] "
        "text-[#100d1a] text-xs font-semibold hover:brightness-110 "
        "disabled:bg-[#2a2540] disabled:text-slate-600 disabled:border-[#2a2540] disabled:cursor-not-allowed"
    )
    return base


def test_composer_column_direction():
    s = input_composer_outer_class()
    assert "flex flex-col" in s
    assert "flex-row" not in s


def test_action_row_justify_between():
    s = input_action_row_class()
    assert "justify-between" in s
    assert "items-center" in s


def test_send_disabled_empty_text():
    assert send_disabled("idea-1", "", False) is True
    assert send_disabled("idea-1", "   ", False) is True


def test_send_disabled_no_idea():
    assert send_disabled(None, "hi", False) is True


def test_send_disabled_while_loading():
    assert send_disabled("idea-1", "hi", True) is True


def test_send_enabled():
    assert send_disabled("idea-1", "hello", False) is False


def test_attach_disabled_no_idea_or_loading():
    assert attach_disabled(None, False) is True
    assert attach_disabled("x", True) is True
    assert attach_disabled("x", False) is False


def test_send_button_has_accent_when_enabled():
    s = send_button_class_for_state(False)
    assert "bg-[#e2b14c]" in s
    assert "disabled:bg-[#2a2540]" in s


def test_index_html_input_bar_wiring():
    html = (_repo_root() / "ui" / "index.html").read_text(encoding="utf-8")
    assert "M2 UI-5: input composer" in html
    assert input_composer_outer_class() in html
    assert input_action_row_class() in html
    assert "Attach</span>" in html
    # Sanity: old single-row stretch layout removed from composer block
    idx = html.find("M2 UI-5: input composer")
    assert idx != -1
    window = html[idx : idx + 2500]
    assert "flex items-stretch gap-2" not in window
