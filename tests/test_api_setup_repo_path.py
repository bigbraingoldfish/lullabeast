"""Tests for POST /api/setup/check-repo-path and POST /api/setup/create-repo-dir."""

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


def test_check_repo_path_existing_directory():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        r = client.post("/api/setup/check-repo-path", json={"path": str(p)})
        assert r.status_code == 200
        d = r.json()
        assert d["exists"] is True
        assert d["parent_exists"] is True
        assert d["path"] == str(p)


def test_check_repo_path_missing_child_parent_exists():
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        child = parent / "new_repo_dir"
        r = client.post("/api/setup/check-repo-path", json={"path": str(child)})
        assert r.status_code == 200
        d = r.json()
        assert d["exists"] is False
        assert d["parent_exists"] is True


def test_check_repo_path_neither_exists():
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp) / "does_not_exist"
        child = parent / "child"
        r = client.post("/api/setup/check-repo-path", json={"path": str(child)})
        assert r.status_code == 200
        d = r.json()
        assert d["exists"] is False
        assert d["parent_exists"] is False


def test_create_repo_dir_success():
    with tempfile.TemporaryDirectory() as tmp:
        new_dir = Path(tmp) / "created_repo"
        r = client.post("/api/setup/create-repo-dir", json={"path": str(new_dir)})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert new_dir.is_dir()


def test_create_repo_dir_parent_missing():
    with tempfile.TemporaryDirectory() as tmp:
        missing_parent = Path(tmp) / "nope" / "child"
        r = client.post("/api/setup/create-repo-dir", json={"path": str(missing_parent)})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert "error" in d
