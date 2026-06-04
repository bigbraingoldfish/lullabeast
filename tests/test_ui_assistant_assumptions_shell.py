"""Assistant assumptions disclosure label and neutral expanded panel contract."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def assumptions_disclosure_label(count: int) -> str:
    if count == 1:
        return "1 AI assumption made"
    return f"{count} AI assumptions made"


def _disclosure_source_chunk(html: str) -> str:
    start = html.find("function AssistantAssumptionsDisclosure")
    end = html.find("function QuestionFlow", start)
    assert start != -1 and end != -1
    return html[start:end]


def test_label_singular():
    assert assumptions_disclosure_label(1) == "1 AI assumption made"


def test_label_plural():
    assert assumptions_disclosure_label(4) == "4 AI assumptions made"


def test_index_html_disclosure_and_neutral_expand():
    html = (_repo_root() / "ui" / "index.html").read_text(encoding="utf-8")
    assert "AssistantAssumptionsDisclosure" in html
    assert "1 AI assumption made" in html
    assert "AI assumptions made" in html
    chunk = _disclosure_source_chunk(html)
    assert "bg-[#100d1a]/90" in chunk
    assert "text-amber-300/90" in chunk
    assert "bg-amber-950" not in chunk
    assert "border-amber-700" not in chunk


def test_question_flow_uses_rounded_xl():
    html = (_repo_root() / "ui" / "index.html").read_text(encoding="utf-8")
    assert "function QuestionFlow" in html
    idx = html.find("function QuestionFlow")
    chunk = html[idx : idx + 4500]
    assert "rounded-xl" in chunk
