"""Tests for PUT /api/ideas/{id}/prd-section (manual operator section edit).

The operator can rewrite one canonical PRD section directly from the dashboard
without a chat turn. The endpoint must:
  - replace exactly the target section body in prd_draft.md (atomic write),
  - mirror the new document into session.json's prd_content,
  - snapshot prd_draft.previous.md first so the existing diff/revert UI works
    on manual edits,
  - record a breadcrumb in session.json's pending_system_events (drained into
    the next chat turn's [SYSTEM EVENTS] block) plus a durable note in
    conversation_log.md,
  - refuse edits while an agent turn is in flight (trailing pending assistant
    message) and when the client's base_content is stale.

Also covers the pending_system_events drain path (injection into the chat
webhook body and clearing after a successful turn): this endpoint is the
first writer of that key, and the drain infrastructure had no coverage.
"""
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from ui.server import _parse_prd_sections, _slugify_section, app

client = TestClient(app)

FAKE_CONFIG = {
    "ideas_dir": "/tmp/test-prd-edit-ideas",
    "hooks_url": "http://localhost:19999/hooks/agent",
    "hooks_token": "test-token",
}

SECTION_KEY = _slugify_section("Problem Statement")

TWO_SECTION_PRD = (
    "## Problem Statement\n\nOLD_BODY\n\n## User Stories\n\nKEEP_US\n"
)


def _make_idea(ideas_dir: str, prd: str = TWO_SECTION_PRD, messages=None) -> str:
    idea_id = str(uuid.uuid4())
    idea_dir = Path(ideas_dir) / idea_id
    idea_dir.mkdir(parents=True, exist_ok=True)
    session = {
        "name": "Test Idea",
        "messages": messages or [],
        "prd_content": prd or "",
        "roadmap_content": "",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }
    (idea_dir / "session.json").write_text(json.dumps(session))
    if prd is not None:
        (idea_dir / "prd_draft.md").write_text(prd, encoding="utf-8")
    return idea_id


@pytest.fixture
def ideas_dir(tmp_path):
    d = tmp_path / "ideas"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@pytest.fixture
def config_patch(ideas_dir):
    cfg = {**FAKE_CONFIG, "ideas_dir": ideas_dir}
    with patch("ui.server.load_config", return_value=cfg):
        yield cfg


def _put(idea_id: str, body: dict):
    return client.put(f"/api/ideas/{idea_id}/prd-section", json=body)


def _read_session(ideas_dir: str, idea_id: str) -> dict:
    return json.loads((Path(ideas_dir) / idea_id / "session.json").read_text())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_put_section_updates_prd_draft_and_session(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    r = _put(idea_id, {
        "section_key": SECTION_KEY,
        "content": "NEW_BODY",
        "base_content": "OLD_BODY",  # matches current → no stale 409
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["section_key"] == SECTION_KEY
    assert data["title"] == "Problem Statement"
    assert data["content"] == "NEW_BODY"
    assert data["breadcrumb_recorded"] is True

    new_doc = (Path(ideas_dir) / idea_id / "prd_draft.md").read_text(encoding="utf-8")
    parsed = _parse_prd_sections(new_doc)
    assert parsed["Problem Statement"].strip() == "NEW_BODY"
    assert parsed["User Stories"].strip() == "KEEP_US"

    session = _read_session(ideas_dir, idea_id)
    assert session["prd_content"] == new_doc
    assert session["updated"] != "2026-01-01T00:00:00Z"


def test_put_succeeds_without_base_content(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": "NO_BASE_CHECK"})
    assert r.status_code == 200, r.text
    new_doc = (Path(ideas_dir) / idea_id / "prd_draft.md").read_text(encoding="utf-8")
    assert "NO_BASE_CHECK" in new_doc


def test_put_empty_content_clears_section_body(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": ""})
    assert r.status_code == 200, r.text
    new_doc = (Path(ideas_dir) / idea_id / "prd_draft.md").read_text(encoding="utf-8")
    assert "## Problem Statement" in new_doc  # heading kept
    parsed = _parse_prd_sections(new_doc)
    assert parsed["Problem Statement"].strip() == ""
    assert parsed["User Stories"].strip() == "KEEP_US"


def test_put_section_absent_from_draft_appends_heading(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir, prd="## User Stories\n\nKEEP_US\n")
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": "APPENDED_BODY"})
    assert r.status_code == 200, r.text
    new_doc = (Path(ideas_dir) / idea_id / "prd_draft.md").read_text(encoding="utf-8")
    assert "## Problem Statement" in new_doc
    parsed = _parse_prd_sections(new_doc)
    assert parsed["Problem Statement"].strip() == "APPENDED_BODY"
    assert parsed["User Stories"].strip() == "KEEP_US"


def test_put_prd_draft_absent_creates_file(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir, prd=None)
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": "FIRST_BODY"})
    assert r.status_code == 200, r.text
    prd_path = Path(ideas_dir) / idea_id / "prd_draft.md"
    assert prd_path.exists()
    parsed = _parse_prd_sections(prd_path.read_text(encoding="utf-8"))
    assert parsed["Problem Statement"].strip() == "FIRST_BODY"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

def test_put_unknown_idea_404(config_patch):
    r = _put("no-such-idea", {"section_key": SECTION_KEY, "content": "X"})
    assert r.status_code == 404


def test_put_missing_section_key_422(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    r = _put(idea_id, {"content": "X"})
    assert r.status_code == 422


def test_put_unknown_section_key_422(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    r = _put(idea_id, {"section_key": "not-a-real-slug", "content": "X"})
    assert r.status_code == 422


def test_put_non_string_content_422(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": 123})
    assert r.status_code == 422
    r2 = _put(idea_id, {"section_key": SECTION_KEY})
    assert r2.status_code == 422


def test_put_prd_draft_absent_empty_content_422(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir, prd=None)
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": ""})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Conflict guards
# ---------------------------------------------------------------------------

def test_put_409_when_turn_pending(config_patch, ideas_dir):
    pending_messages = [
        {"role": "user", "content": "do something", "ts": "2026-01-01T00:01:00Z"},
        {"role": "assistant", "content": "Working on your request...",
         "ts": "2026-01-01T00:01:01Z", "pending": True},
    ]
    idea_id = _make_idea(ideas_dir, messages=pending_messages)
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": "RACE_BODY"})
    assert r.status_code == 409
    # File untouched
    doc = (Path(ideas_dir) / idea_id / "prd_draft.md").read_text(encoding="utf-8")
    assert "RACE_BODY" not in doc
    assert "OLD_BODY" in doc


def test_put_allows_edit_after_turn_resolved(config_patch, ideas_dir):
    resolved_messages = [
        {"role": "user", "content": "do something", "ts": "2026-01-01T00:01:00Z"},
        {"role": "assistant", "content": "Done.", "ts": "2026-01-01T00:01:30Z",
         "pending": False},
    ]
    idea_id = _make_idea(ideas_dir, messages=resolved_messages)
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": "AFTER_TURN"})
    assert r.status_code == 200, r.text


def test_put_409_when_base_content_stale(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    r = _put(idea_id, {
        "section_key": SECTION_KEY,
        "content": "NEW_BODY",
        "base_content": "SOMETHING_ELSE",
    })
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["current"] == "OLD_BODY"
    # File untouched
    doc = (Path(ideas_dir) / idea_id / "prd_draft.md").read_text(encoding="utf-8")
    assert "NEW_BODY" not in doc


# ---------------------------------------------------------------------------
# Snapshot + diff/revert integration
# ---------------------------------------------------------------------------

def test_put_snapshots_previous_before_write(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": "NEW_BODY"})
    assert r.status_code == 200, r.text

    prev = (Path(ideas_dir) / idea_id / "prd_draft.previous.md").read_text(encoding="utf-8")
    assert prev == TWO_SECTION_PRD

    diff = client.get(f"/api/ideas/{idea_id}/prd-section-diff")
    assert diff.status_code == 200
    sections = diff.json()["sections"]
    assert sections[SECTION_KEY]["status"] == "modified"
    assert sections[SECTION_KEY]["previous"] == "OLD_BODY"
    assert sections[SECTION_KEY]["current"] == "NEW_BODY"


# ---------------------------------------------------------------------------
# Breadcrumb: pending_system_events + conversation_log.md
# ---------------------------------------------------------------------------

def test_put_records_pending_system_event(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": "NEW_BODY"})
    assert r.status_code == 200, r.text
    events = _read_session(ideas_dir, idea_id).get("pending_system_events", [])
    assert len(events) == 1
    assert "Problem Statement" in events[0]
    assert "manually" in events[0]


def test_put_event_survives_multiple_edits(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    assert _put(idea_id, {"section_key": SECTION_KEY, "content": "ONE"}).status_code == 200
    assert _put(
        idea_id,
        {"section_key": _slugify_section("User Stories"), "content": "TWO"},
    ).status_code == 200
    events = _read_session(ideas_dir, idea_id).get("pending_system_events", [])
    assert len(events) == 2
    assert "Problem Statement" in events[0]
    assert "User Stories" in events[1]


def test_put_appends_operator_note_to_conversation_log(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    log_path = Path(ideas_dir) / idea_id / "conversation_log.md"
    log_path.write_text("## Turn 1\n### User\nhi\n\n### Assistant\nhello\n")
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": "NEW_BODY"})
    assert r.status_code == 200, r.text
    log = log_path.read_text(encoding="utf-8")
    assert "## Turn 1" in log  # existing content preserved
    assert "## Operator Edit" in log
    assert "Problem Statement" in log


def test_put_skips_conversation_log_when_absent(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": "NEW_BODY"})
    assert r.status_code == 200, r.text
    assert not (Path(ideas_dir) / idea_id / "conversation_log.md").exists()


def test_put_creates_session_when_absent(config_patch, ideas_dir):
    idea_id = _make_idea(ideas_dir)
    (Path(ideas_dir) / idea_id / "session.json").unlink()
    r = _put(idea_id, {"section_key": SECTION_KEY, "content": "NEW_BODY"})
    assert r.status_code == 200, r.text
    session = _read_session(ideas_dir, idea_id)
    assert "NEW_BODY" in session["prd_content"]
    assert len(session.get("pending_system_events", [])) == 1


# ---------------------------------------------------------------------------
# Drain path: the next chat turn must inject and then clear the breadcrumb.
# pending_system_events had no writer (and no coverage) before this feature;
# these tests pin the injection/clear behavior the breadcrumb relies on.
# Harness mirrors tests/test_api_ideas_history_window.py.
# ---------------------------------------------------------------------------

class TestBreadcrumbDrain:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def _mock_config(self):
        return {**FAKE_CONFIG, "ideas_dir": str(self.ideas_dir)}

    def _write_idea_with_event(self, idea_id, event_line):
        idir = self.ideas_dir / idea_id
        idir.mkdir(parents=True, exist_ok=True)
        (idir / "session.json").write_text(json.dumps({
            "messages": [],
            "prd_content": "",
            "pending_system_events": [event_line],
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }))
        turns = idir / "turns"
        turns.mkdir()
        (turns / "1.md").write_text("ok")
        (turns / "1.done").write_text("done")

    def _capture(self):
        payloads = []

        def post(url, **kwargs):
            payloads.append(kwargs.get("json", {}))
            r = MagicMock()
            r.status = 200
            r.read = AsyncMock(return_value=b"")
            r.__aenter__ = AsyncMock(return_value=r)
            r.__aexit__ = AsyncMock(return_value=None)
            return r

        s = MagicMock()
        s.post = AsyncMock(side_effect=post)
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=None)
        return payloads, s

    def _conv_msg(self, payloads):
        for p in payloads:
            if ":session-" in (p.get("sessionKey") or ""):
                return p.get("message", "")
        return ""

    def test_pending_events_injected_into_webhook_body(self):
        idea_id = "drain_inject"
        event = 'Operator manually edited PRD section "Problem Statement".'
        self._write_idea_with_event(idea_id, event)
        payloads, session = self._capture()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "next turn", "turn": 1})
        assert r.status_code == 200, r.text
        msg = self._conv_msg(payloads)
        assert "[SYSTEM EVENTS]" in msg
        assert event in msg

    def test_pending_events_cleared_after_successful_turn(self):
        idea_id = "drain_clear"
        self._write_idea_with_event(idea_id, "Operator manually edited a section.")
        payloads, session = self._capture()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            with patch("ui.server.aiohttp.ClientSession", return_value=session):
                with patch("asyncio.create_task"):
                    r = client.post(f"/api/ideas/{idea_id}/message",
                                    json={"content": "next turn", "turn": 1})
        assert r.status_code == 200, r.text
        after = json.loads((self.ideas_dir / idea_id / "session.json").read_text())
        assert "pending_system_events" not in after
