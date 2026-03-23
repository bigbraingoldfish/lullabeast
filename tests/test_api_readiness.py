"""Tests for readiness file-based API (Fix Pass 1)."""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _client(tmp_path, monkeypatch):
    ideas_dir = tmp_path / "ideas"
    ideas_dir.mkdir()

    def mock_load_config(self_config=None):
        return {
            "ideas_dir": str(ideas_dir),
            "port": 18790,
            "hooks_url": "http://127.0.0.1:9/hooks/agent",
            "hooks_token": "test",
        }

    monkeypatch.setattr("ui.server.load_config", mock_load_config)
    from ui.server import app

    return TestClient(app), ideas_dir


class TestReadinessGet:
    def test_returns_404_when_idea_dir_missing(self, tmp_path, monkeypatch):
        client, _ = _client(tmp_path, monkeypatch)
        r = client.get("/api/ideas/missing/readiness")
        assert r.status_code == 404

    def test_returns_updating_when_no_sentinel(self, tmp_path, monkeypatch):
        client, ideas_dir = _client(tmp_path, monkeypatch)
        iid = "idea-a"
        (ideas_dir / iid).mkdir()
        (ideas_dir / iid / "session.json").write_text(json.dumps({"prd_content": ""}))

        r = client.get(f"/api/ideas/{iid}/readiness")
        assert r.status_code == 200
        assert r.json() == {"status": "updating", "data": None}

    def test_returns_ready_when_sentinel_and_valid_json(self, tmp_path, monkeypatch):
        client, ideas_dir = _client(tmp_path, monkeypatch)
        iid = "idea-b"
        d = ideas_dir / iid
        d.mkdir()
        payload = {
            "overall_status": "approaching_ready",
            "score": 6.0,
            "conversion_confidence": "medium",
            "sections": {},
            "blocking_gaps": [],
            "ambiguities": [],
            "progression_note": "x",
            "recommendation": "y",
        }
        (d / "readiness.json").write_text(json.dumps(payload))
        (d / "readiness.done").write_text("done")

        r = client.get(f"/api/ideas/{iid}/readiness")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        assert body["data"]["score"] == 6.0

    def test_returns_unavailable_when_sentinel_but_bad_json(self, tmp_path, monkeypatch):
        client, ideas_dir = _client(tmp_path, monkeypatch)
        iid = "idea-c"
        d = ideas_dir / iid
        d.mkdir()
        (d / "readiness.json").write_text("{ not json")
        (d / "readiness.done").write_text("done")

        r = client.get(f"/api/ideas/{iid}/readiness")
        assert r.status_code == 200
        assert r.json() == {"status": "unavailable", "data": None}


class TestReadinessPoll:
    def test_poll_false_without_sentinel(self, tmp_path, monkeypatch):
        client, ideas_dir = _client(tmp_path, monkeypatch)
        iid = "p1"
        (ideas_dir / iid).mkdir()

        r = client.get(f"/api/ideas/{iid}/readiness/poll")
        assert r.status_code == 200
        assert r.json() == {"done": False}

    def test_poll_true_with_sentinel(self, tmp_path, monkeypatch):
        client, ideas_dir = _client(tmp_path, monkeypatch)
        iid = "p2"
        d = ideas_dir / iid
        d.mkdir()
        (d / "readiness.done").write_text("done")

        r = client.get(f"/api/ideas/{iid}/readiness/poll")
        assert r.status_code == 200
        assert r.json() == {"done": True}


class TestTriggerReadinessAssessment:
    def test_unlinks_sentinel_before_webhook(self, tmp_path, monkeypatch):
        from ui.server import _trigger_readiness_assessment

        ideas_root = tmp_path / "ideas"
        iid = "trig1"
        d = ideas_root / iid
        d.mkdir(parents=True)
        (d / "readiness.done").write_text("done")

        config = {
            "ideas_dir": str(ideas_root),
            "hooks_url": "http://127.0.0.1:9/hook",
            "hooks_token": "t",
        }

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def post(self, *a, **k):
                return None

        monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: FakeSession())

        asyncio.run(_trigger_readiness_assessment(iid, config))
        assert not (d / "readiness.done").exists()
