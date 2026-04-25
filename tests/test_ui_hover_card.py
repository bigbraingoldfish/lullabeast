"""Hover card + inline readiness badge logic (mirrors IdeasScreen helpers in ui/index.html)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Tuple


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def compute_show_card(hovered_id: Optional[str], hover_delay_fired: bool) -> bool:
    return hovered_id is not None and bool(hover_delay_fired)


def readiness_score_text_class(score: Any) -> str:
    if score is None:
        return "text-slate-500"
    s = int(score)
    if s >= 8:
        return "text-emerald-400"
    if s >= 5:
        return "text-amber-400"
    return "text-red-400"


def readiness_score_bar_class(score: Any) -> Optional[str]:
    if score is None:
        return None
    s = int(score)
    if s >= 8:
        return "bg-emerald-400"
    if s >= 5:
        return "bg-amber-400"
    return "bg-red-400"


def readiness_score_label(score: Any) -> str:
    if score is None:
        return "\u2014"
    return f"{int(score)}/10"


def doc_indicator(has_doc: bool) -> Tuple[str, str, str]:
    if has_doc:
        return ("text-slate-400", "text-emerald-400", "\u2713")
    return ("text-slate-600", "text-slate-600", "\u2014")


def empty_summary_text() -> str:
    return "New project \u2014 no documentation yet."


def inline_badge_visible(readiness_score: Any) -> bool:
    return readiness_score is not None and isinstance(readiness_score, (int, float))


def test_compute_show_card_requires_id_and_delay():
    assert compute_show_card(None, True) is False
    assert compute_show_card(None, False) is False
    assert compute_show_card("abc", False) is False
    assert compute_show_card("abc", True) is True


def test_readiness_score_text_class_tiers():
    assert readiness_score_text_class(None) == "text-slate-500"
    assert readiness_score_text_class(8) == "text-emerald-400"
    assert readiness_score_text_class(9) == "text-emerald-400"
    assert readiness_score_text_class(10) == "text-emerald-400"
    assert readiness_score_text_class(5) == "text-amber-400"
    assert readiness_score_text_class(7) == "text-amber-400"
    assert readiness_score_text_class(4) == "text-red-400"
    assert readiness_score_text_class(0) == "text-red-400"


def test_readiness_score_bar_class_tiers():
    assert readiness_score_bar_class(None) is None
    assert readiness_score_bar_class(8) == "bg-emerald-400"
    assert readiness_score_bar_class(4) == "bg-red-400"
    assert readiness_score_bar_class(6) == "bg-amber-400"


def test_readiness_score_label():
    assert readiness_score_label(8) == "8/10"
    assert readiness_score_label(None) == "\u2014"


def test_doc_indicator():
    assert doc_indicator(True) == ("text-slate-400", "text-emerald-400", "\u2713")
    assert doc_indicator(False) == ("text-slate-600", "text-slate-600", "\u2014")


def test_empty_summary_text_uses_em_dash():
    s = empty_summary_text()
    assert s == "New project \u2014 no documentation yet."
    assert "\u2014" in s
    assert "New project - no documentation yet." not in s


def test_inline_badge_visible():
    assert inline_badge_visible(None) is False
    assert inline_badge_visible(0) is True
    assert inline_badge_visible(7) is True


def test_index_html_hover_card_contract_strings():
    """Shipped index.html must contain hover card markers (fails before UI-8 implementation)."""
    html = (_repo_root() / "ui" / "index.html").read_text(encoding="utf-8")
    assert "New project \u2014 no documentation yet." in html
    assert "PRD Readiness" in html
    assert "ReactDOM.createPortal" in html
    assert "data-idea-hover-row" in html
