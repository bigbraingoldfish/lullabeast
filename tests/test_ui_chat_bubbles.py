"""
IdeasScreen chat bubble class contract (mirrors ui/index.html).

Keep helpers in sync with the user / assistant prose bubble className strings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


CHAT_BUBBLE_RADIUS = "rounded-xl"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def user_bubble_inner_class() -> str:
    """Class string for the user message content bubble (inner div)."""
    return (
        f"{CHAT_BUBBLE_RADIUS} px-3 py-2 text-sm bg-[#e2b14c]/20 "
        "text-slate-100 whitespace-pre-wrap leading-[1.6]"
    )


def assistant_reply_card_outer_class(msg: Mapping[str, Any]) -> str:
    """Outer assistant reply + assumptions shell (error/pending/default)."""
    if msg.get("error"):
        return (
            "rounded-xl border border-red-900/60 bg-red-950/40 overflow-hidden "
            "w-full min-w-0"
        )
    if msg.get("pending"):
        return (
            "rounded-xl border border-slate-600/50 bg-[#221b36] overflow-hidden "
            "w-full min-w-0"
        )
    return (
        "rounded-xl border border-[#2a2540] bg-[#221b36] overflow-hidden "
        "w-full min-w-0"
    )


def assistant_prose_inner_class(msg: Mapping[str, Any]) -> str:
    """Inner msg-md prose — single surface; semantic color on outer shell only."""
    base = "msg-md text-sm min-w-0 flex-1 leading-[1.6] "
    if msg.get("error"):
        return base + "text-red-100"
    if msg.get("pending"):
        return base + "text-slate-400"
    return base + "text-slate-200"


def test_user_bubble_uniform_radius_and_leading():
    s = user_bubble_inner_class()
    assert CHAT_BUBBLE_RADIUS in s
    assert "leading-[1.6]" in s


def test_assistant_default_inner_no_cyan_accent():
    s = assistant_prose_inner_class({"error": False, "pending": False})
    assert "border-l-2" not in s
    assert "border-[#e2b14c]" not in s
    assert "leading-[1.6]" in s
    assert "rounded-lg" not in s


def test_assistant_pending_inner_flat_no_nested_bubble():
    s = assistant_prose_inner_class({"error": False, "pending": True})
    assert "leading-[1.6]" in s
    assert "border-[#e2b14c]" not in s
    assert "rounded-lg" not in s


def test_assistant_error_inner_flat_no_nested_bubble():
    s = assistant_prose_inner_class({"error": True, "pending": False})
    assert "leading-[1.6]" in s
    assert "border-[#e2b14c]" not in s
    assert "rounded-lg" not in s
    assert "bg-red-950" not in s
    outer = assistant_reply_card_outer_class({"error": True, "pending": False})
    assert "bg-red-950/40" in outer


def test_index_html_chat_bubbles_match_helpers():
    """Guards drift between tests and ui/index.html (unique substrings)."""
    html = (_repo_root() / "ui" / "index.html").read_text(encoding="utf-8")
    assert CHAT_BUBBLE_RADIUS in html
    assert "border-[#2a2540] bg-[#221b36] overflow-hidden w-full min-w-0" in html.replace(
        "\n", " "
    )
    assert "border-red-900/60" in html
    assert "bg-red-950/40" in html
    assert "AssistantAssumptionsDisclosure" in html
    assert "border-l-2 border-[#e2b14c]" not in html
    assert html.count("leading-[1.6]") >= 2
    assert "rounded-lg bg-red-950/40 border border-red-900/60" not in html
