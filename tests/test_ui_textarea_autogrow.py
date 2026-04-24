"""Chat textarea autogrow height cap (mirrors adjustTa in ui/index.html)."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


TEXTAREA_MAX_HEIGHT_PX = 160


def cap_height(scroll_height: int, max_h: int = TEXTAREA_MAX_HEIGHT_PX) -> int:
    return min(scroll_height, max_h)


def test_cap_height_at_ceiling():
    assert cap_height(200) == 160
    assert cap_height(500) == 160


def test_cap_height_below_ceiling_passthrough():
    assert cap_height(100) == 100
    assert cap_height(160) == 160


def test_index_html_textarea_autogrow_ceiling():
    html = (_repo_root() / "ui" / "index.html").read_text(encoding="utf-8")
    assert "max-h-[160px]" in html
    assert "max-h-[110px]" not in html
    assert "const maxH = 160" in html
