"""Tests for POST /api/setup/repo-roadmap-hint and roadmap completion stats."""

from pathlib import Path

from fastapi.testclient import TestClient

from ui.server import app, _roadmap_phase_checkbox_stats


def _client() -> TestClient:
    return TestClient(app)


def _roadmap_line(checked: bool, pid: str, desc: str = "Do thing") -> str:
    box = "x" if checked else " "
    return f"- [{box}] `{pid}` | LOW | {desc}\n  > Test: Works.\n"


def test_roadmap_phase_checkbox_stats_counts_total_and_completed():
    content = (
        _roadmap_line(False, "CORE-E1")
        + _roadmap_line(True, "CORE-E2")
        + _roadmap_line(True, "UI-E1")
    )
    total, completed = _roadmap_phase_checkbox_stats(content)
    assert total == 3
    assert completed == 2


def test_repo_roadmap_hint_rejects_invalid_path():
    c = _client()
    r = c.post("/api/setup/repo-roadmap-hint", json={"path": "relative/path"})
    assert r.status_code == 422
    assert "Invalid path" in r.json().get("detail", "")


def test_repo_roadmap_hint_returns_not_found_for_missing_roadmap(tmp_path: Path):
    repo = tmp_path / "proj"
    repo.mkdir()

    c = _client()
    r = c.post("/api/setup/repo-roadmap-hint", json={"path": str(repo)})
    assert r.status_code == 200
    assert r.json() == {"found": False}


def test_repo_roadmap_hint_returns_content_for_incomplete_roadmap(tmp_path: Path):
    repo = tmp_path / "proj"
    repo.mkdir()
    roadmap_content = _roadmap_line(False, "CORE-E1") + _roadmap_line(True, "CORE-E2")
    (repo / "roadmap.md").write_text(roadmap_content)

    c = _client()
    r = c.post("/api/setup/repo-roadmap-hint", json={"path": str(repo)})
    assert r.status_code == 200
    data = r.json()
    assert data["found"] is True
    assert data["ambiguous"] is False
    assert data["filename"] == "roadmap.md"
    assert data["content"] == roadmap_content
    assert data["all_phases_complete"] is False


def test_repo_roadmap_hint_marks_all_complete(tmp_path: Path):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "roadmap.md").write_text(_roadmap_line(True, "CORE-E1") + _roadmap_line(True, "UI-E1"))

    c = _client()
    r = c.post("/api/setup/repo-roadmap-hint", json={"path": str(repo)})
    assert r.status_code == 200
    assert r.json()["all_phases_complete"] is True


def test_repo_roadmap_hint_returns_ambiguous_for_multiple_roadmaps(tmp_path: Path):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "roadmap.md").write_text(_roadmap_line(False, "CORE-E1"))
    (repo / "myroadmap-v2.md").write_text(_roadmap_line(False, "CORE-E2"))

    c = _client()
    r = c.post("/api/setup/repo-roadmap-hint", json={"path": str(repo)})
    assert r.status_code == 200
    data = r.json()
    assert data["found"] is True
    assert data["ambiguous"] is True
    assert sorted(data["roadmap_files"]) == ["myroadmap-v2.md", "roadmap.md"]
    assert "content" not in data

