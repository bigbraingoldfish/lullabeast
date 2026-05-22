"""P0 Stage B8: /api/ideas/{id}/draft-sync-status exposes verification mtime.

Today the endpoint returns ``prd_draft_mtime`` and ``roadmap_draft_mtime``.
P0 adds a parallel ``verification_draft_mtime`` so the Ideas-screen drift
indicator can distinguish stale roadmap from stale verification.
"""
import pytest
from pathlib import Path
from unittest.mock import patch


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestDraftSyncStatusVerification:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def _mock_config(self):
        return {"ideas_dir": str(self.ideas_dir)}

    def _idea_dir(self, idea_id):
        d = self.ideas_dir / idea_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_response_includes_verification_draft_mtime_field(self):
        client = load_server()
        idea_id = "v1"
        d = self._idea_dir(idea_id)
        (d / "verification_draft.md").write_text("# Verification")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get(f"/api/ideas/{idea_id}/draft-sync-status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "verification_draft_mtime" in body, (
            "draft-sync-status must report verification_draft_mtime so the UI "
            "can detect verification staleness independently of the roadmap."
        )
        assert isinstance(body["verification_draft_mtime"], float)

    def test_verification_mtime_none_when_file_absent(self):
        client = load_server()
        idea_id = "v2"
        d = self._idea_dir(idea_id)
        (d / "roadmap_draft.md").write_text("# RM")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get(f"/api/ideas/{idea_id}/draft-sync-status")
        assert r.status_code == 200
        body = r.json()
        assert body.get("verification_draft_mtime") is None

    def test_existing_fields_still_present(self):
        """Regression guard: existing keys must remain after the additive change."""
        client = load_server()
        idea_id = "v3"
        self._idea_dir(idea_id)
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get(f"/api/ideas/{idea_id}/draft-sync-status")
        assert r.status_code == 200
        body = r.json()
        for key in ("roadmap_behind_prd", "prd_draft_mtime", "roadmap_draft_mtime"):
            assert key in body
