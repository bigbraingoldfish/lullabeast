"""Tests for PRD section parsing, diff endpoint, and prd_draft.previous snapshot."""
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from ui.server import (
    PRD_SECTION_TITLES,
    _build_prd_section_diff_payload,
    _parse_prd_sections,
    _slugify_section,
    _snapshot_prd_draft_before_agent_write,
    app,
)

client = TestClient(app)

FAKE_CONFIG = {
    "ideas_dir": "/tmp/test-prd-diff-ideas",
    "hooks_url": "http://localhost:19999/hooks/agent",
    "hooks_token": "test-token",
}


def _make_idea(ideas_dir: str) -> str:
    idea_id = str(uuid.uuid4())
    idea_dir = Path(ideas_dir) / idea_id
    idea_dir.mkdir(parents=True, exist_ok=True)
    turns_dir = idea_dir / "turns"
    turns_dir.mkdir()
    (turns_dir / "1.done").write_text("done")
    (turns_dir / "1.md").write_text("Hello")
    session = {
        "name": "Test Idea",
        "messages": [{"role": "assistant", "content": "Hello", "ts": "2026-01-01T00:00:00Z"}],
        "prd_content": "",
        "roadmap_content": "",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }
    (idea_dir / "session.json").write_text(json.dumps(session))
    return idea_id


@pytest.fixture
def ideas_dir(tmp_path):
    return str(tmp_path / "ideas")


@pytest.fixture
def idea_id(ideas_dir):
    Path(ideas_dir).mkdir(parents=True, exist_ok=True)
    return _make_idea(ideas_dir)


@pytest.fixture
def config_patch(ideas_dir):
    cfg = {**FAKE_CONFIG, "ideas_dir": ideas_dir}
    with patch("ui.server.load_config", return_value=cfg):
        yield cfg


# ── Parser / slug unit tests ────────────────────────────────────────────────


def test_slugify_problem_statement():
    assert _slugify_section("Problem Statement") == "problem-statement"


def test_slugify_goals_ampersand():
    assert _slugify_section("Goals & Success Metrics") == "goals-and-success-metrics"


def test_parse_prd_sections_extracts_twelve_canonical():
    md = """# Title

## Problem Statement
First line

## Goals & Success Metrics
G1

2. User Stories
- story a
"""
    out = _parse_prd_sections(md)
    assert len(PRD_SECTION_TITLES) == 12
    for t in PRD_SECTION_TITLES:
        assert t in out
    assert "First line" in (out["Problem Statement"] or "")
    assert "G1" in (out["Goals & Success Metrics"] or "")
    assert "story a" in (out["User Stories"] or "")


def test_parse_prd_sections_empty():
    out = _parse_prd_sections("")
    assert all((out[t] or "").strip() == "" for t in PRD_SECTION_TITLES)


# ── Diff payload builder ──────────────────────────────────────────────────────


def test_diff_payload_no_previous_all_non_empty_are_added():
    cur = """## Problem Statement\n\nHello\n\n## User Stories\n\nUS1\n"""
    data = _build_prd_section_diff_payload(cur, None)
    secs = data["sections"]
    assert secs[_slugify_section("Problem Statement")]["status"] == "added"
    assert secs[_slugify_section("Problem Statement")]["previous"] is None
    assert secs[_slugify_section("Problem Statement")]["current"] == "Hello"
    assert secs[_slugify_section("User Stories")]["status"] == "added"
    # Empty sections omitted
    assert _slugify_section("Edge Cases") not in secs


def test_diff_payload_unchanged_omitted():
    cur = """## Problem Statement\n\nSame\n"""
    prev = """## Problem Statement\n\nSame\n"""
    data = _build_prd_section_diff_payload(cur, prev)
    assert data["sections"] == {}


def test_diff_payload_modified():
    cur = """## Problem Statement\n\nNew text\n"""
    prev = """## Problem Statement\n\nOld text\n"""
    data = _build_prd_section_diff_payload(cur, prev)
    sk = _slugify_section("Problem Statement")
    assert data["sections"][sk]["status"] == "modified"
    assert data["sections"][sk]["previous"] == "Old text"
    assert data["sections"][sk]["current"] == "New text"


def test_diff_payload_removed():
    cur = """## Problem Statement\n\nStill here\n"""
    prev = """## Problem Statement\n\nStill here\n\n## User Stories\n\nGone story\n"""
    data = _build_prd_section_diff_payload(cur, prev)
    sk = _slugify_section("User Stories")
    assert data["sections"][sk]["status"] == "removed"
    assert data["sections"][sk]["previous"] == "Gone story"
    assert data["sections"][sk]["current"] is None


def test_diff_payload_added_with_previous_file():
    cur = """## Problem Statement\n\nA\n\n## Edge Cases\n\nE\n"""
    prev = """## Problem Statement\n\nA\n"""
    data = _build_prd_section_diff_payload(cur, prev)
    sk = _slugify_section("Edge Cases")
    assert data["sections"][sk]["status"] == "added"
    assert data["sections"][sk]["previous"] is None
    assert data["sections"][sk]["current"] == "E"


# ── GET endpoint ──────────────────────────────────────────────────────────────


def test_get_prd_section_diff_404_missing_idea(config_patch):
    resp = client.get("/api/ideas/nonexistent-uuid/prd-section-diff")
    assert resp.status_code == 404


def test_get_prd_section_diff_empty_when_no_files(idea_id, config_patch, ideas_dir):
    resp = client.get(f"/api/ideas/{idea_id}/prd-section-diff")
    assert resp.status_code == 200
    assert resp.json() == {"sections": {}}


def test_get_prd_section_diff_returns_added_without_previous(idea_id, config_patch, ideas_dir):
    idea_path = Path(ideas_dir) / idea_id
    (idea_path / "prd_draft.md").write_text("## Problem Statement\n\nX\n", encoding="utf-8")
    resp = client.get(f"/api/ideas/{idea_id}/prd-section-diff")
    assert resp.status_code == 200
    j = resp.json()
    assert j["sections"][_slugify_section("Problem Statement")]["status"] == "added"
    assert j["sections"][_slugify_section("Problem Statement")]["previous"] is None


def test_get_prd_section_diff_removed_section(idea_id, config_patch, ideas_dir):
    idea_path = Path(ideas_dir) / idea_id
    (idea_path / "prd_draft.md").write_text("## Problem Statement\n\nA\n", encoding="utf-8")
    (idea_path / "prd_draft.previous.md").write_text(
        "## Problem Statement\n\nA\n\n## User Stories\n\nOld US\n", encoding="utf-8"
    )
    resp = client.get(f"/api/ideas/{idea_id}/prd-section-diff")
    assert resp.status_code == 200
    sk = _slugify_section("User Stories")
    assert resp.json()["sections"][sk]["status"] == "removed"
    assert resp.json()["sections"][sk]["current"] is None


# ── Snapshot before message (mock webhook + poll) ────────────────────────────


def test_snapshot_prd_previous_written_before_agent_overwrites(ideas_dir):
    """prd_draft.previous.md must contain pre-webhook prd_draft.md."""
    Path(ideas_dir).mkdir(parents=True, exist_ok=True)
    idea_id = _make_idea(ideas_dir)
    idea_path = Path(ideas_dir) / idea_id
    (idea_path / "prd_draft.md").write_text("OLD PRD BODY", encoding="utf-8")

    cfg = {**FAKE_CONFIG, "ideas_dir": ideas_dir}

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=b"")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)
    mock_session = MagicMock()
    mock_session.post = AsyncMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    async def fake_poll(*_a, **_k):
        (idea_path / "prd_draft.md").write_text("NEW PRD BODY", encoding="utf-8")
        return True, ""

    turns_dir = idea_path / "turns"
    turn_n = 2
    while (turns_dir / f"{turn_n}.done").exists():
        turn_n += 1
    (turns_dir / f"{turn_n}.md").write_text("DRAFTING:\n\nOK", encoding="utf-8")
    (turns_dir / f"{turn_n}.done").write_text("", encoding="utf-8")

    with patch("ui.server.load_config", return_value=cfg):
        with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
            with patch("ui.server._poll_sentinel_with_idle_detect", new=fake_poll):
                r = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "hi", "turn": turn_n},
                )

    assert r.status_code == 200, r.text
    prev_path = idea_path / "prd_draft.previous.md"
    assert prev_path.exists()
    assert prev_path.read_text(encoding="utf-8") == "OLD PRD BODY"
    assert (idea_path / "prd_draft.md").read_text(encoding="utf-8") == "NEW PRD BODY"


def test_snapshot_function_writes_previous(tmp_path):
    idea_dir = tmp_path / "idea"
    idea_dir.mkdir()
    (idea_dir / "prd_draft.md").write_text("snapshot-old", encoding="utf-8")
    _snapshot_prd_draft_before_agent_write(idea_dir)
    assert (idea_dir / "prd_draft.previous.md").read_text(encoding="utf-8") == "snapshot-old"


def test_snapshot_skipped_when_no_prd_draft_yet(ideas_dir):
    Path(ideas_dir).mkdir(parents=True, exist_ok=True)
    idea_id = _make_idea(ideas_dir)
    idea_path = Path(ideas_dir) / idea_id
    cfg = {**FAKE_CONFIG, "ideas_dir": ideas_dir}

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=b"")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)
    mock_session = MagicMock()
    mock_session.post = AsyncMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    async def fake_poll(*_a, **_k):
        (idea_path / "prd_draft.md").write_text("FIRST", encoding="utf-8")
        return True, ""

    turns_dir = idea_path / "turns"
    turn_n = 2
    while (turns_dir / f"{turn_n}.done").exists():
        turn_n += 1
    (turns_dir / f"{turn_n}.md").write_text("ok", encoding="utf-8")
    (turns_dir / f"{turn_n}.done").write_text("", encoding="utf-8")

    with patch("ui.server.load_config", return_value=cfg):
        with patch("ui.server.aiohttp.ClientSession", return_value=mock_session):
            with patch("ui.server._poll_sentinel_with_idle_detect", new=fake_poll):
                r = client.post(
                    f"/api/ideas/{idea_id}/message",
                    json={"content": "hi", "turn": turn_n},
                )

    assert r.status_code == 200
    assert not (idea_path / "prd_draft.previous.md").exists()
