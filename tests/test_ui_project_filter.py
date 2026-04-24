"""Project list title filter (mirrors IdeasScreen filter logic in ui/index.html)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Mapping


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def filter_ideas_by_title(
    ideas: List[Mapping[str, Any]], filter_text: str
) -> List[Mapping[str, Any]]:
    """Case-insensitive substring match on idea name; empty filter returns all."""
    q = (filter_text or "").strip().lower()
    if not q:
        return list(ideas)
    return [it for it in ideas if q in (it.get("name") or "").lower()]


def test_filter_lln_matches_single_project():
    ideas = [
        {"id": "a", "name": "LLN Lab"},
        {"id": "b", "name": "Chaos Timer"},
        {"id": "c", "name": "Vehicle City"},
    ]
    out = filter_ideas_by_title(ideas, "lln")
    assert out == [{"id": "a", "name": "LLN Lab"}]


def test_filter_empty_returns_all():
    ideas = [
        {"id": "a", "name": "LLN Lab"},
        {"id": "b", "name": "Chaos Timer"},
        {"id": "c", "name": "Vehicle City"},
    ]
    out = filter_ideas_by_title(ideas, "")
    assert out == ideas


def test_filter_no_match_returns_empty():
    ideas = [
        {"id": "a", "name": "LLN Lab"},
        {"id": "b", "name": "Chaos Timer"},
        {"id": "c", "name": "Vehicle City"},
    ]
    out = filter_ideas_by_title(ideas, "xyz")
    assert out == []


def test_index_html_project_filter_wired():
    """Controlled filter input present above chats rail list."""
    html = (_repo_root() / "ui" / "index.html").read_text(encoding="utf-8")
    assert "filterText" in html
    assert "filteredIdeas" in html
    assert "Filter projects" in html
