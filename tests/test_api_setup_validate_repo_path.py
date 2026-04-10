"""Tests for POST /api/setup/validate-repo-path."""

import pytest
from fastapi.testclient import TestClient

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from ui.server import app

client = TestClient(app)


class TestValidateRepoPath:
    def test_empty_path_invalid(self):
        r = client.post("/api/setup/validate-repo-path", json={"path": ""})
        assert r.status_code == 200
        d = r.json()
        assert d["valid"] is False
        assert d["error"]

    def test_whitespace_only_invalid(self):
        r = client.post("/api/setup/validate-repo-path", json={"path": "   "})
        assert r.status_code == 200
        assert r.json()["valid"] is False

    def test_valid_path_string(self, tmp_path):
        r = client.post(
            "/api/setup/validate-repo-path",
            json={"path": str(tmp_path)},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["valid"] is True
        assert d["error"] is None

    def test_null_byte_invalid(self):
        r = client.post(
            "/api/setup/validate-repo-path",
            json={"path": "/bad\x00path"},
        )
        assert r.status_code == 200
        assert r.json()["valid"] is False

    def test_too_long_invalid(self):
        r = client.post(
            "/api/setup/validate-repo-path",
            json={"path": "x" * 600},
        )
        assert r.status_code == 200
        assert r.json()["valid"] is False

    def test_relative_path_invalid(self):
        r = client.post(
            "/api/setup/validate-repo-path",
            json={"path": "path/to/your-project/my-app"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["valid"] is False
        assert "absolute" in (d.get("error") or "").lower()
