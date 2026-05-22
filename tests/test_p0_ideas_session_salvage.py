"""P0 Stage B6 + B7: salvage path covers ``verification_draft.md``.

If the /convert call times out at the API layer but the converter eventually
finishes writing both artefacts to disk, a subsequent ``GET /api/ideas/{id}/session``
must merge BOTH the roadmap and verification drafts into session.json.

The existing roadmap salvage block at ``ui/server.py:4086-4096`` handles the
roadmap side; P0 adds an analogous block for verification and exposes a new
helper ``_merge_verification_draft_into_session_data`` mirroring the existing
``_merge_roadmap_draft_into_session_data`` (line 3345).
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestMergeVerificationHelper:
    """Direct test of the new helper. Mirrors the existing
    ``_merge_roadmap_draft_into_session_data`` contract."""

    def test_merges_when_disk_and_session_differ(self, tmp_path):
        from ui.server import _merge_verification_draft_into_session_data

        idea_dir = tmp_path / "ideas" / "v1"
        idea_dir.mkdir(parents=True)
        (idea_dir / "verification_draft.md").write_text("# Verification\n\n## Project type\nweb-app\n")
        (idea_dir / "verification_draft.done").write_text("")

        session_data = {"verification_content": ""}
        changed = _merge_verification_draft_into_session_data(idea_dir, session_data)
        assert changed is True
        assert session_data["verification_content"].startswith("# Verification")

    def test_no_merge_when_sentinel_missing(self, tmp_path):
        from ui.server import _merge_verification_draft_into_session_data

        idea_dir = tmp_path / "ideas" / "v2"
        idea_dir.mkdir(parents=True)
        (idea_dir / "verification_draft.md").write_text("# Disk only")

        session_data = {"verification_content": ""}
        changed = _merge_verification_draft_into_session_data(idea_dir, session_data)
        assert changed is False, (
            "Without the .done sentinel, the disk doc is incomplete and "
            "must not be merged."
        )

    def test_no_merge_when_content_unchanged(self, tmp_path):
        from ui.server import _merge_verification_draft_into_session_data

        idea_dir = tmp_path / "ideas" / "v3"
        idea_dir.mkdir(parents=True)
        same = "# Verification\n\n## Project type\ncli\n"
        (idea_dir / "verification_draft.md").write_text(same)
        (idea_dir / "verification_draft.done").write_text("")

        session_data = {"verification_content": same}
        changed = _merge_verification_draft_into_session_data(idea_dir, session_data)
        assert changed is False


class TestSessionEndpointSalvage:
    """GET /api/ideas/{id}/session merges verification draft from disk after
    a post-timeout salvage scenario."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def _mock_config(self):
        return {"ideas_dir": str(self.ideas_dir)}

    def _seed_idea(self, idea_id, session_extra=None):
        idea_dir = self.ideas_dir / idea_id
        idea_dir.mkdir(parents=True, exist_ok=True)
        session = {
            "name": idea_id,
            "messages": [],
            "prd_content": "# PRD",
            "roadmap_content": "",
            "verification_content": "",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        if session_extra:
            session.update(session_extra)
        (idea_dir / "session.json").write_text(json.dumps(session))
        return idea_dir

    def test_session_get_merges_verification_when_disk_has_both_files(self):
        client = load_server()
        idea_dir = self._seed_idea("s1")
        verification_text = "# Verification\n\n## Project type\nweb-app\n"
        (idea_dir / "verification_draft.md").write_text(verification_text)
        (idea_dir / "verification_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/s1/session")

        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("verification_content") == verification_text, (
            "Session GET must merge verification_draft.md from disk when the "
            "sentinel is present and session.verification_content is empty."
        )

    def test_session_get_skips_verification_merge_when_sentinel_missing(self):
        client = load_server()
        idea_dir = self._seed_idea("s2")
        (idea_dir / "verification_draft.md").write_text("# Disk only")
        # No verification_draft.done — should NOT merge.

        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/s2/session")

        assert r.status_code == 200
        body = r.json()
        assert body.get("verification_content", "") == "", (
            "Without the sentinel, session.verification_content must stay empty."
        )

    def test_session_get_does_not_rewrite_session_when_unchanged(self):
        client = load_server()
        verification_text = "# Verification\n\n## Project type\ncli\n"
        idea_dir = self._seed_idea("s3", session_extra={"verification_content": verification_text})
        (idea_dir / "verification_draft.md").write_text(verification_text)
        (idea_dir / "verification_draft.done").write_text("")
        mtime_before = (idea_dir / "session.json").stat().st_mtime

        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/s3/session")

        assert r.status_code == 200
        mtime_after = (idea_dir / "session.json").stat().st_mtime
        assert mtime_after == mtime_before, (
            "When disk and session match, session.json must not be rewritten."
        )
