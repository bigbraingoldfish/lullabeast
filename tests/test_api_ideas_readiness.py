"""Tests for GET /api/ideas/{id}/readiness endpoint."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


FULL_PRD = "\n\n".join([
    "## Problem Statement\nWe need to solve X.",
    "## Goals & Success Metrics\nIncrease Y by Z.",
    "## User Stories\nAs a user I want to...",
    "## Functional Requirements\nThe system must...",
    "## Edge Cases\nWhen X happens...",
    "## Non-Functional Requirements\nMust support 1000 users.",
    "## Dependencies & Integrations\nRequires service A.",
    "## Risks & Mitigations\nRisk: X. Mitigation: Y.",
    "## Open Questions\nWhat about Z?",
    "## Glossary & Domain Terms\nPRD: Product Requirements Document.",
])


class TestApiIdeasReadiness:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def _mock_config(self):
        return {"ideas_dir": str(self.ideas_dir)}

    def _write_session(self, idea_id, prd_content):
        idea_dir = self.ideas_dir / idea_id
        idea_dir.mkdir(parents=True, exist_ok=True)
        session = {"messages": [], "prd_content": prd_content, "created": None, "updated": None}
        (idea_dir / "session.json").write_text(json.dumps(session))

    def test_returns_404_when_idea_not_found(self):
        client = load_server()
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/nonexistent/readiness")
        assert r.status_code == 404

    def test_ready_when_conversion_ready_marker_present(self):
        client = load_server()
        self._write_session("1", "> ✅ PRD CONVERSION-READY\n\nSome content.")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/1/readiness")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is True
        assert "conversion-ready marker" in body["reason"]

    def test_ready_when_all_10_sections_nonempty(self):
        client = load_server()
        self._write_session("2", FULL_PRD)
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/2/readiness")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is True

    def test_not_ready_when_prd_empty(self):
        client = load_server()
        self._write_session("3", "")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/3/readiness")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is False
        assert "reason" in body

    def test_not_ready_when_sections_missing(self):
        client = load_server()
        self._write_session("4", "## Problem Statement\nWe need to solve X.")
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/4/readiness")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is False
        assert "Missing" in body["reason"]

    def test_not_ready_when_section_is_header_only(self):
        """Section present but only contains header line (empty content)."""
        client = load_server()
        prd = FULL_PRD.replace(
            "## Problem Statement\nWe need to solve X.",
            "## Problem Statement"
        )
        self._write_session("5", prd)
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/5/readiness")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is False

    def test_response_has_ready_and_reason_fields(self):
        client = load_server()
        self._write_session("6", FULL_PRD)
        with patch("ui.server.load_config", return_value=self._mock_config()):
            r = client.get("/api/ideas/6/readiness")
        body = r.json()
        assert "ready" in body
        assert "reason" in body
