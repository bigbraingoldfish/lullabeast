"""Tests for annotation CRUD endpoints and annotation injection into POST /message."""
import json
import os
import sys
import uuid
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from ui.server import app

client = TestClient(app)

FAKE_CONFIG = {
    "ideas_dir": "/tmp/test-annotations-ideas",
    "hooks_url": "http://localhost:19999/hooks/agent",
    "hooks_token": "test-token",
}


def _make_idea(ideas_dir: str) -> str:
    """Create a minimal idea directory with session.json + turns/1.done."""
    idea_id = str(uuid.uuid4())
    idea_dir = Path(ideas_dir) / idea_id
    idea_dir.mkdir(parents=True, exist_ok=True)
    turns_dir = idea_dir / "turns"
    turns_dir.mkdir()
    (turns_dir / "1.done").write_text("done")
    (turns_dir / "1.md").write_text("Hello, what are you building?")
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


# ── CREATE ──────────────────────────────────────────────────────────────────

def test_create_annotation_returns_id(idea_id, config_patch):
    resp = client.post(f"/api/ideas/{idea_id}/annotations", json={"section": "Functional Requirements", "comment": "Add SSO"})
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert len(data["id"]) > 0


def test_create_annotation_persisted(idea_id, config_patch, ideas_dir):
    client.post(f"/api/ideas/{idea_id}/annotations", json={"section": "Edge Cases", "comment": "Queue overflow"})
    ann_resp = client.get(f"/api/ideas/{idea_id}/annotations")
    annotations = ann_resp.json()
    assert len(annotations) == 1
    assert annotations[0]["section"] == "Edge Cases"
    assert annotations[0]["comment"] == "Queue overflow"
    assert annotations[0]["submitted"] is False


def test_create_annotation_missing_fields(idea_id, config_patch):
    resp = client.post(f"/api/ideas/{idea_id}/annotations", json={"section": "Foo"})
    assert resp.status_code == 422


def test_create_annotation_idea_not_found(config_patch):
    resp = client.post(f"/api/ideas/nonexistent/annotations", json={"section": "X", "comment": "Y"})
    assert resp.status_code == 404


# ── READ ─────────────────────────────────────────────────────────────────────

def test_get_annotations_empty(idea_id, config_patch):
    resp = client.get(f"/api/ideas/{idea_id}/annotations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_annotations_multiple(idea_id, config_patch):
    client.post(f"/api/ideas/{idea_id}/annotations", json={"section": "A", "comment": "note 1"})
    client.post(f"/api/ideas/{idea_id}/annotations", json={"section": "B", "comment": "note 2"})
    resp = client.get(f"/api/ideas/{idea_id}/annotations")
    assert len(resp.json()) == 2


# ── UPDATE ───────────────────────────────────────────────────────────────────

def test_patch_annotation_updates_comment(idea_id, config_patch):
    create_resp = client.post(f"/api/ideas/{idea_id}/annotations", json={"section": "NFR", "comment": "old"})
    ann_id = create_resp.json()["id"]

    patch_resp = client.patch(f"/api/ideas/{idea_id}/annotations/{ann_id}", json={"comment": "updated"})
    assert patch_resp.status_code == 200

    annotations = client.get(f"/api/ideas/{idea_id}/annotations").json()
    ann = next(a for a in annotations if a["id"] == ann_id)
    assert ann["comment"] == "updated"


def test_patch_annotation_409_if_submitted(idea_id, config_patch, ideas_dir):
    """Patching a submitted annotation returns 409."""
    create_resp = client.post(f"/api/ideas/{idea_id}/annotations", json={"section": "FR", "comment": "original"})
    ann_id = create_resp.json()["id"]

    # Manually mark as submitted
    session_path = Path(ideas_dir) / idea_id / "session.json"
    session_data = json.loads(session_path.read_text())
    for ann in session_data["annotations"]:
        if ann["id"] == ann_id:
            ann["submitted"] = True
    session_path.write_text(json.dumps(session_data))

    patch_resp = client.patch(f"/api/ideas/{idea_id}/annotations/{ann_id}", json={"comment": "new"})
    assert patch_resp.status_code == 409


def test_patch_annotation_not_found(idea_id, config_patch):
    resp = client.patch(f"/api/ideas/{idea_id}/annotations/nonexistent", json={"comment": "x"})
    assert resp.status_code == 404


# ── DELETE ───────────────────────────────────────────────────────────────────

def test_delete_annotation(idea_id, config_patch):
    create_resp = client.post(f"/api/ideas/{idea_id}/annotations", json={"section": "X", "comment": "del me"})
    ann_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/ideas/{idea_id}/annotations/{ann_id}")
    assert del_resp.status_code == 200

    annotations = client.get(f"/api/ideas/{idea_id}/annotations").json()
    assert all(a["id"] != ann_id for a in annotations)


def test_delete_annotation_not_found(idea_id, config_patch):
    resp = client.delete(f"/api/ideas/{idea_id}/annotations/nonexistent")
    assert resp.status_code == 404


# ── INJECTION ────────────────────────────────────────────────────────────────

def test_annotations_injected_into_message_turn(idea_id, config_patch, ideas_dir):
    """Unsubmitted annotations are prepended to the webhook message on next turn."""
    client.post(f"/api/ideas/{idea_id}/annotations", json={"section": "Functional Requirements", "comment": "Add SSO"})
    client.post(f"/api/ideas/{idea_id}/annotations", json={"section": "Edge Cases", "comment": "Queue overflow"})

    captured_payload = {}

    async def fake_post(url, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"")
        return mock_resp

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.post = AsyncMock(side_effect=fake_post)

    # Write a turn sentinel so the poll resolves immediately
    turns_dir = Path(ideas_dir) / idea_id / "turns"
    (turns_dir / "2.md").write_text("Agent replied")
    (turns_dir / "2.done").write_text("done")

    import ui.server as srv
    orig_timeout = srv.POLL_TIMEOUT
    srv.POLL_TIMEOUT = 5

    with patch("aiohttp.ClientSession", return_value=fake_session):
        with patch("asyncio.create_task"):
            resp = client.post(
                f"/api/ideas/{idea_id}/message",
                json={"content": "Tell me more", "turn": 2},
            )

    srv.POLL_TIMEOUT = orig_timeout

    assert resp.status_code == 200
    msg = captured_payload.get("message", "")
    assert "[USER ANNOTATIONS]" in msg
    assert 'Section "Functional Requirements": "Add SSO"' in msg
    assert 'Section "Edge Cases": "Queue overflow"' in msg


def test_annotations_removed_after_successful_turn(idea_id, config_patch, ideas_dir):
    """After a turn completes, consumed draft annotations are removed (fresh notes allowed)."""
    client.post(
        f"/api/ideas/{idea_id}/annotations",
        json={"section": "Functional Requirements", "comment": "Add SSO"},
    )

    async def fake_post(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"")
        return mock_resp

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.post = AsyncMock(side_effect=fake_post)

    turns_dir = Path(ideas_dir) / idea_id / "turns"
    (turns_dir / "2.md").write_text("Agent replied")
    (turns_dir / "2.done").write_text("done")

    import ui.server as srv

    orig_timeout = srv.POLL_TIMEOUT
    srv.POLL_TIMEOUT = 5

    with patch("aiohttp.ClientSession", return_value=fake_session):
        with patch("asyncio.create_task"):
            client.post(f"/api/ideas/{idea_id}/message", json={"content": "Go", "turn": 2})

    srv.POLL_TIMEOUT = orig_timeout

    annotations = client.get(f"/api/ideas/{idea_id}/annotations").json()
    assert annotations == []


def test_annotations_drafts_preserved_on_message_timeout(tmp_path):
    """408 timeout does not remove or submit draft annotations."""
    ideas_dir = tmp_path / "ideas"
    idea_id = str(uuid.uuid4())
    idea_dir = Path(ideas_dir) / idea_id
    idea_dir.mkdir(parents=True)
    (idea_dir / "turns").mkdir()
    session = {
        "name": "Timeout",
        "messages": [],
        "prd_content": "",
        "roadmap_content": "",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }
    (idea_dir / "session.json").write_text(json.dumps(session))
    cfg = {**FAKE_CONFIG, "ideas_dir": str(ideas_dir)}

    async def fake_post(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"")
        return mock_resp

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.post = AsyncMock(side_effect=fake_post)

    async def fake_sleep(seconds):
        return None

    with patch("ui.server.load_config", return_value=cfg):
        client.post(
            f"/api/ideas/{idea_id}/annotations",
            json={"section": "NFR", "comment": "preserve me"},
        )
        with patch("aiohttp.ClientSession", return_value=fake_session):
            with patch("asyncio.create_task"):
                with patch("ui.server.POLL_TIMEOUT", 2):
                    with patch("asyncio.sleep", side_effect=fake_sleep):
                        resp = client.post(
                            f"/api/ideas/{idea_id}/message",
                            json={"content": "slow", "turn": 1},
                        )

        assert resp.status_code == 408
        annotations = client.get(f"/api/ideas/{idea_id}/annotations").json()
        assert len(annotations) == 1
        assert annotations[0]["submitted"] is False
        assert annotations[0]["comment"] == "preserve me"


def test_late_done_reconciliation_after_poll_false(tmp_path):
    """Poll returns False but .done is newer than attempt start → 200 and drafts consumed."""
    ideas_dir = tmp_path / "ideas"
    idea_id = str(uuid.uuid4())
    idea_dir = Path(ideas_dir) / idea_id
    idea_dir.mkdir(parents=True)
    (idea_dir / "turns").mkdir()
    session = {
        "name": "Late",
        "messages": [],
        "prd_content": "",
        "roadmap_content": "",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }
    (idea_dir / "session.json").write_text(json.dumps(session))
    cfg = {**FAKE_CONFIG, "ideas_dir": str(ideas_dir)}

    real_gm = os.path.getmtime

    def fake_gm(p):
        if str(p).endswith("2.done"):
            return 2000.0
        return real_gm(p)

    async def fake_post(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"")
        return mock_resp

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.post = AsyncMock(side_effect=fake_post)

    turns_dir = idea_dir / "turns"
    (turns_dir / "2.md").write_text("Recovered response")
    (turns_dir / "2.done").write_text("done")

    import ui.server as srv

    orig_timeout = srv.POLL_TIMEOUT
    srv.POLL_TIMEOUT = 5

    with patch("ui.server.load_config", return_value=cfg):
        client.post(
            f"/api/ideas/{idea_id}/annotations",
            json={"section": "API", "comment": "note"},
        )
        with patch("aiohttp.ClientSession", return_value=fake_session):
            with patch("asyncio.create_task"):
                with patch(
                    "ui.server._poll_sentinel_with_idle_detect",
                    AsyncMock(return_value=(False, "poll_timeout")),
                ):
                    with patch("ui.server.time.time", return_value=1000.0):
                        with patch("ui.server.os.path.getmtime", side_effect=fake_gm):
                            resp = client.post(
                                f"/api/ideas/{idea_id}/message",
                                json={"content": "Go", "turn": 2},
                            )

        assert resp.status_code == 200, resp.text
        assert resp.json().get("response") == "Recovered response"
        assert client.get(f"/api/ideas/{idea_id}/annotations").json() == []

    srv.POLL_TIMEOUT = orig_timeout
