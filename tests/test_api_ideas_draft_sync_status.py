"""Tests for GET /api/ideas/{id}/draft-sync-status."""
import os
import pytest
from pathlib import Path
from unittest.mock import patch


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiIdeasDraftSyncStatus:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        monkeypatch.setenv("OPENCLAW_IDEAS_DIR", str(self.ideas_dir))

    def _mock_config(self):
        return {"ideas_dir": str(self.ideas_dir)}

    def _idea_dir(self, idea_id: str) -> Path:
        d = self.ideas_dir / idea_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_returns_404_when_idea_not_found(self):
        client = load_server()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/nonexistent-uuid/draft-sync-status")
        assert r.status_code == 404

    def test_behind_false_when_prd_missing(self):
        client = load_server()
        idea_id = "a1"
        d = self._idea_dir(idea_id)
        (d / "roadmap_draft.md").write_text("# Roadmap")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get(f"/api/ideas/{idea_id}/draft-sync-status")
        assert r.status_code == 200
        body = r.json()
        assert body["roadmap_behind_prd"] is False
        assert body["prd_draft_mtime"] is None
        assert isinstance(body["roadmap_draft_mtime"], float)

    def test_behind_false_when_roadmap_missing(self):
        client = load_server()
        idea_id = "a2"
        d = self._idea_dir(idea_id)
        (d / "prd_draft.md").write_text("# PRD")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get(f"/api/ideas/{idea_id}/draft-sync-status")
        assert r.status_code == 200
        body = r.json()
        assert body["roadmap_behind_prd"] is False
        assert isinstance(body["prd_draft_mtime"], float)
        assert body["roadmap_draft_mtime"] is None

    def test_behind_false_when_prd_mtime_not_after_roadmap(self):
        client = load_server()
        idea_id = "a3"
        d = self._idea_dir(idea_id)
        prd = d / "prd_draft.md"
        rm = d / "roadmap_draft.md"
        prd.write_text("# PRD")
        rm.write_text("# RM")
        base = 1_700_000_000
        os.utime(prd, (base, base))
        os.utime(rm, (base, base + 100))
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get(f"/api/ideas/{idea_id}/draft-sync-status")
        assert r.status_code == 200
        body = r.json()
        assert body["roadmap_behind_prd"] is False
        assert body["prd_draft_mtime"] == pytest.approx(base)
        assert body["roadmap_draft_mtime"] == pytest.approx(base + 100)

    def test_behind_true_when_prd_newer_than_roadmap(self):
        client = load_server()
        idea_id = "a4"
        d = self._idea_dir(idea_id)
        prd = d / "prd_draft.md"
        rm = d / "roadmap_draft.md"
        prd.write_text("# PRD")
        rm.write_text("# RM")
        base = 1_700_000_000
        os.utime(rm, (base, base))
        os.utime(prd, (base, base + 50))
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get(f"/api/ideas/{idea_id}/draft-sync-status")
        assert r.status_code == 200
        body = r.json()
        assert body["roadmap_behind_prd"] is True
        assert body["prd_draft_mtime"] == pytest.approx(base + 50)
        assert body["roadmap_draft_mtime"] == pytest.approx(base)

    def test_cache_control_no_store(self):
        client = load_server()
        idea_id = "a5"
        self._idea_dir(idea_id)
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get(f"/api/ideas/{idea_id}/draft-sync-status")
        assert r.status_code == 200
        assert r.headers.get("cache-control") == "no-store"
