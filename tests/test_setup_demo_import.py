"""Tests for the demo-info endpoint (welcome walkthrough).

GET /api/setup/demo-info describes the bundled demo project: the three
artifact contents plus the suggested destination path. Works keyless and
hides on bare metal with no examples dir. The tour stages the artifacts
through the normal Setup & Preflight seed flow, so there is no import
endpoint.
"""
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


def _make_repo_with_demo(tmp_path: Path) -> Path:
    """Build a fake repo checkout with examples/first-run-snake artifacts."""
    repo = tmp_path / "app"
    demo = repo / "examples" / "first-run-snake"
    demo.mkdir(parents=True)
    (demo / "prd.md").write_text("# Snake PRD\n")
    (demo / "roadmap.md").write_text("# Snake roadmap\n")
    (demo / "verification.md").write_text("# Snake verification\n")
    return repo


def _cfg(repo: Path, projects_dir=None) -> dict:
    cfg = {"autodev_repo_path": str(repo)}
    if projects_dir is not None:
        cfg["projects_dir"] = str(projects_dir)
    return cfg


class TestDemoInfo:
    def test_available_true_with_artifacts(self, tmp_path):
        repo = _make_repo_with_demo(tmp_path)
        cfg = _cfg(repo, projects_dir=tmp_path / "projects")
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/demo-info")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert "Snake PRD" in data["artifacts"]["prd"]
        assert "Snake roadmap" in data["artifacts"]["roadmap"]
        assert "Snake verification" in data["artifacts"]["verification"]
        assert data["default_dest"] == str(tmp_path / "projects" / "first-run-snake")

    def test_available_false_bare_metal_no_examples(self, tmp_path):
        repo = tmp_path / "app"
        repo.mkdir()  # no examples dir
        cfg = _cfg(repo)  # no projects_dir
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/demo-info")
        data = r.json()
        assert data["available"] is False
        assert data["default_dest"] is None

    def test_default_dest_null_without_projects_dir(self, tmp_path):
        repo = _make_repo_with_demo(tmp_path)
        cfg = _cfg(repo)  # projects_dir unset
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/demo-info")
        assert r.json()["default_dest"] is None

    def test_import_endpoint_removed(self, tmp_path):
        repo = _make_repo_with_demo(tmp_path)
        cfg = _cfg(repo, projects_dir=tmp_path / "projects")
        with patch("ui.server.load_config", return_value=cfg):
            r = client.post("/api/setup/import-demo", json={})
        assert r.status_code in (404, 405)


class TestSuggestedDest:
    """suggested_dest keeps the tour non-blocking: reuse the default folder
    while it is free or still pristine, else draft a fresh numbered sibling
    (a built demo's roadmap diverges and would fail the preflight conflict
    check)."""

    def test_equals_default_when_folder_absent(self, tmp_path):
        repo = _make_repo_with_demo(tmp_path)
        cfg = _cfg(repo, projects_dir=tmp_path / "projects")
        with patch("ui.server.load_config", return_value=cfg):
            data = client.get("/api/setup/demo-info").json()
        assert data["suggested_dest"] == data["default_dest"]

    def test_reuses_folder_with_pristine_roadmap(self, tmp_path):
        repo = _make_repo_with_demo(tmp_path)
        projects = tmp_path / "projects"
        dest = projects / "first-run-snake"
        dest.mkdir(parents=True)
        (dest / "roadmap.md").write_text("# Snake roadmap\n")
        cfg = _cfg(repo, projects_dir=projects)
        with patch("ui.server.load_config", return_value=cfg):
            data = client.get("/api/setup/demo-info").json()
        assert data["suggested_dest"] == str(dest)

    def test_suffixes_when_roadmap_diverged(self, tmp_path):
        repo = _make_repo_with_demo(tmp_path)
        projects = tmp_path / "projects"
        dest = projects / "first-run-snake"
        dest.mkdir(parents=True)
        (dest / "roadmap.md").write_text("# Snake roadmap\n- [x] Phase 1 built\n")
        cfg = _cfg(repo, projects_dir=projects)
        with patch("ui.server.load_config", return_value=cfg):
            data = client.get("/api/setup/demo-info").json()
        assert data["suggested_dest"] == str(projects / "first-run-snake-2")

    def test_skips_existing_suffixed_siblings(self, tmp_path):
        repo = _make_repo_with_demo(tmp_path)
        projects = tmp_path / "projects"
        for name in ("first-run-snake", "first-run-snake-2"):
            d = projects / name
            d.mkdir(parents=True)
            (d / "roadmap.md").write_text("# built\n")
        cfg = _cfg(repo, projects_dir=projects)
        with patch("ui.server.load_config", return_value=cfg):
            data = client.get("/api/setup/demo-info").json()
        assert data["suggested_dest"] == str(projects / "first-run-snake-3")

    def test_none_without_projects_dir(self, tmp_path):
        repo = _make_repo_with_demo(tmp_path)
        cfg = _cfg(repo)
        with patch("ui.server.load_config", return_value=cfg):
            data = client.get("/api/setup/demo-info").json()
        assert data["suggested_dest"] is None
