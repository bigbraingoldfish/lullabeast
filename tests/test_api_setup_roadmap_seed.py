"""Tests for POST /api/setup/roadmap-seed endpoint."""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "ui"))

from fastapi.testclient import TestClient
from ui.server import app


class TestSetupRoadmapSeed:
    """pass_criteria: POST /api/setup/roadmap-seed returns HTTP 200 with {"ok": true}
    when given {"content": "test content"}"""

    def test_returns_200_ok(self, tmp_path):
        """POST with {"content": "# My Roadmap"} returns 200 with {"ok": true}."""
        setup_path = tmp_path / "setup_session.json"
        with patch.object(Path, 'expanduser', lambda self: setup_path):
            client = TestClient(app)
            response = client.post("/api/setup/roadmap-seed", json={"content": "# My Roadmap"})
            assert response.status_code == 200
            assert response.json() == {"ok": True}

    def test_stores_roadmap_seed(self, tmp_path):
        """Content is stored in setup_session.json with key 'roadmap_seed'."""
        setup_path = tmp_path / "setup_session.json"
        content = "# My Roadmap"
        with patch.object(Path, 'expanduser', lambda self: setup_path):
            client = TestClient(app)
            response = client.post("/api/setup/roadmap-seed", json={"content": content})
            assert response.status_code == 200
        stored = json.loads(setup_path.read_text())
        assert stored.get("roadmap_seed") == content

    def test_empty_content_is_accepted(self, tmp_path):
        """POST with {"content": ""} returns 200 (empty string is valid)."""
        setup_path = tmp_path / "setup_session.json"
        with patch.object(Path, 'expanduser', lambda self: setup_path):
            client = TestClient(app)
            response = client.post("/api/setup/roadmap-seed", json={"content": ""})
            assert response.status_code == 200

    def test_overwrites_previous_content(self, tmp_path):
        """Two consecutive POSTs: second overwrites first; file contains only latest content."""
        setup_path = tmp_path / "setup_session.json"
        with patch.object(Path, 'expanduser', lambda self: setup_path):
            client = TestClient(app)
            client.post("/api/setup/roadmap-seed", json={"content": "First"})
            client.post("/api/setup/roadmap-seed", json={"content": "Second"})
        stored = json.loads(setup_path.read_text())
        assert stored.get("roadmap_seed") == "Second"

    def test_creates_parent_dir_if_missing(self, tmp_path):
        """Endpoint creates parent directory if it doesn't exist."""
        setup_path = tmp_path / "nonexistent_dir" / "setup_session.json"
        assert not setup_path.parent.exists()
        with patch.object(Path, 'expanduser', lambda self: setup_path):
            client = TestClient(app)
            response = client.post("/api/setup/roadmap-seed", json={"content": "test"})
            assert response.status_code == 200
        assert setup_path.exists()

    def test_missing_content_returns_422(self, tmp_path):
        """POST with no content field returns 422."""
        setup_path = tmp_path / "setup_session.json"
        with patch.object(Path, 'expanduser', lambda self: setup_path):
            client = TestClient(app)
            response = client.post("/api/setup/roadmap-seed", json={})
            assert response.status_code == 422

    def test_atomic_write_with_tmp_file(self, tmp_path):
        """Endpoint uses .tmp intermediate file + os.replace for atomic write."""
        setup_path = tmp_path / "setup_session.json"
        replace_calls = []
        original_replace = os.replace
        def mock_replace(src, dst):
            replace_calls.append((src, dst))
            return original_replace(src, dst)
        with patch.object(Path, 'expanduser', lambda self: setup_path):
            with patch('os.replace', mock_replace):
                client = TestClient(app)
                response = client.post("/api/setup/roadmap-seed", json={"content": "test"})
                assert response.status_code == 200
        assert len(replace_calls) >= 1, "os.replace should be called"
        assert any(dst == str(setup_path) for _, dst in replace_calls)
