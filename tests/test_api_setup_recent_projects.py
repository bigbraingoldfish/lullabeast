"""Tests for DELETE /api/setup/recent-projects and POST /api/setup/recent-projects/prune.

These endpoints support per-test cleanup of the recents list so test runs do not
accumulate stale ``/tmp/...`` entries in the user's UI dropdown (P1 Stage B).

- ``DELETE`` removes a single entry by absolute path (idempotent — removing a
  path not in the list is a no-op success, not an error).
- ``POST /prune`` removes every entry whose ``path`` no longer exists on disk.
  Used by tests in tear-down so the test's tmpdir entry self-cleans once the
  tmpdir is gone, and exposed for ad-hoc operator cleanup of accumulated stale
  entries.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def recents_file(tmp_path):
    """Sandboxed recents JSON file, isolated from ~/.openclaw/."""
    path = tmp_path / "ui_recent_projects.json"
    with patch("ui.server._ui_recent_projects_path", return_value=str(path)):
        yield path


def _seed(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps(entries), encoding="utf-8")


def _read(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# DELETE /api/setup/recent-projects
# ---------------------------------------------------------------------------


class TestDeleteRecentProject:
    def test_delete_removes_existing_entry(self, client, recents_file, tmp_path):
        """An entry whose path matches is removed; ``removed=True`` is reported."""
        a = str(tmp_path / "a")
        b = str(tmp_path / "b")
        _seed(recents_file, [
            {"path": a, "last_used": "2020-01-01T00:00:00Z"},
            {"path": b, "last_used": "2020-01-02T00:00:00Z"},
        ])
        r = client.request("DELETE", "/api/setup/recent-projects", json={"path": a})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["removed"] is True
        assert [e["path"] for e in body["projects"]] == [b]
        assert [e["path"] for e in _read(recents_file)] == [b]

    def test_delete_nonexistent_path_is_idempotent(self, client, recents_file, tmp_path):
        """Removing a path not in the list returns ``removed=False`` with 200, list unchanged."""
        a = str(tmp_path / "a")
        _seed(recents_file, [{"path": a, "last_used": "2020-01-01T00:00:00Z"}])
        r = client.request("DELETE", "/api/setup/recent-projects",
                           json={"path": str(tmp_path / "never-added")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["removed"] is False
        assert [e["path"] for e in body["projects"]] == [a]
        assert [e["path"] for e in _read(recents_file)] == [a]

    def test_delete_normalizes_path_via_realpath(self, client, recents_file, tmp_path):
        """Submitted path is realpath-normalized to match how ``append_recent_project`` stored it.

        ``append_recent_project`` canonicalizes via ``os.path.realpath(os.path.expanduser(...))``.
        DELETE must use the same normalization so a user supplying a path with
        ``./`` or ``~`` resolves to the same entry that was stored.
        """
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        canonical = os.path.realpath(str(real_dir))
        _seed(recents_file, [{"path": canonical, "last_used": "2020-01-01T00:00:00Z"}])
        # Submit a non-canonical form that realpath() collapses to ``canonical``.
        denormalized = os.path.join(str(tmp_path), ".", "real")
        r = client.request("DELETE", "/api/setup/recent-projects",
                           json={"path": denormalized})
        assert r.status_code == 200, r.text
        assert r.json()["removed"] is True
        assert _read(recents_file) == []

    def test_delete_requires_path_in_body(self, client, recents_file):
        """Empty / malformed body → 422, no file modification."""
        _seed(recents_file, [{"path": "/some/path", "last_used": "2020-01-01T00:00:00Z"}])
        r = client.request("DELETE", "/api/setup/recent-projects", json={})
        assert r.status_code == 422
        # File untouched.
        assert [e["path"] for e in _read(recents_file)] == ["/some/path"]

    def test_delete_preserves_order_of_remaining(self, client, recents_file, tmp_path):
        """Removing a middle entry leaves the surrounding entries in their original order."""
        a = str(tmp_path / "a")
        b = str(tmp_path / "b")
        c = str(tmp_path / "c")
        _seed(recents_file, [
            {"path": a, "last_used": "2020-01-03T00:00:00Z"},
            {"path": b, "last_used": "2020-01-02T00:00:00Z"},
            {"path": c, "last_used": "2020-01-01T00:00:00Z"},
        ])
        r = client.request("DELETE", "/api/setup/recent-projects", json={"path": b})
        assert r.status_code == 200, r.text
        assert [e["path"] for e in r.json()["projects"]] == [a, c]


# ---------------------------------------------------------------------------
# POST /api/setup/recent-projects/prune
# ---------------------------------------------------------------------------


class TestPruneRecentProjects:
    def test_prune_removes_entries_with_missing_directories(
        self, client, recents_file, tmp_path
    ):
        """Entries whose ``path`` is not a directory on disk are swept; existing dirs are kept."""
        existing = tmp_path / "still-here"
        existing.mkdir()
        missing = tmp_path / "deleted-already"  # NOT created
        _seed(recents_file, [
            {"path": str(existing), "last_used": "2020-01-01T00:00:00Z"},
            {"path": str(missing), "last_used": "2020-01-02T00:00:00Z"},
        ])
        r = client.post("/api/setup/recent-projects/prune")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["removed_count"] == 1
        assert str(missing) in body["removed_paths"]
        assert [e["path"] for e in body["projects"]] == [str(existing)]
        assert [e["path"] for e in _read(recents_file)] == [str(existing)]

    def test_prune_keeps_entries_with_existing_directories(
        self, client, recents_file, tmp_path
    ):
        """Recents containing only live directories → no removals, idempotent."""
        d = tmp_path / "live"
        d.mkdir()
        _seed(recents_file, [{"path": str(d), "last_used": "2020-01-01T00:00:00Z"}])
        r = client.post("/api/setup/recent-projects/prune")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["removed_count"] == 0
        assert body["removed_paths"] == []
        assert [e["path"] for e in body["projects"]] == [str(d)]

    def test_prune_empty_recents_is_noop(self, client, recents_file):
        """Empty / missing recents file → ``removed_count=0`` with empty projects list."""
        # File doesn't exist (we never seeded).
        r = client.post("/api/setup/recent-projects/prune")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["removed_count"] == 0
        assert body["projects"] == []

    def test_prune_idempotent(self, client, recents_file, tmp_path):
        """Calling prune twice — second call has nothing to remove."""
        existing = tmp_path / "live"
        existing.mkdir()
        missing = tmp_path / "gone"
        _seed(recents_file, [
            {"path": str(existing), "last_used": "2020-01-01T00:00:00Z"},
            {"path": str(missing), "last_used": "2020-01-02T00:00:00Z"},
        ])
        r1 = client.post("/api/setup/recent-projects/prune")
        assert r1.status_code == 200
        assert r1.json()["removed_count"] == 1

        r2 = client.post("/api/setup/recent-projects/prune")
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["removed_count"] == 0
        assert body2["removed_paths"] == []
        assert [e["path"] for e in body2["projects"]] == [str(existing)]

    def test_prune_removes_malformed_entries(self, client, recents_file):
        """Entries that are not dicts or lack a ``path`` key are also removed (treated as dead)."""
        _seed(recents_file, [
            "just-a-string",
            {"no_path_key": "value"},
            {"path": "", "last_used": "2020-01-01T00:00:00Z"},
        ])
        r = client.post("/api/setup/recent-projects/prune")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["removed_count"] == 3
        assert body["projects"] == []


# ---------------------------------------------------------------------------
# GET /api/setup/recent-projects  (4-B — real projects only)
# ---------------------------------------------------------------------------

class TestGetRecentProjects:
    """The GET endpoint returns only entries whose ``path`` is a real directory on disk, so a
    stale/dead recents file never surfaces dead paths in the UI. (The ``/tmp`` exclusion is a
    separate frontend display backstop, so a real ``tmp_path`` dir here is intentionally kept.)"""

    def test_get_filters_out_nonexistent_dirs(self, client, recents_file, tmp_path):
        real = tmp_path / "real_proj"
        real.mkdir()
        dead = tmp_path / "gone"  # never created
        _seed(recents_file, [
            {"path": str(real), "last_used": "2026-01-01T00:00:00Z"},
            {"path": str(dead), "last_used": "2026-01-01T00:00:00Z"},
        ])
        r = client.get("/api/setup/recent-projects")
        assert r.status_code == 200, r.text
        paths = [e["path"] for e in r.json()["projects"]]
        assert str(real) in paths
        assert str(dead) not in paths

    def test_get_keeps_existing_dir(self, client, recents_file, tmp_path):
        real = tmp_path / "p"
        real.mkdir()
        _seed(recents_file, [{"path": str(real), "last_used": "2026-01-01T00:00:00Z"}])
        r = client.get("/api/setup/recent-projects")
        assert r.status_code == 200, r.text
        assert [e["path"] for e in r.json()["projects"]] == [str(real)]

    def test_get_drops_malformed_entries(self, client, recents_file):
        _seed(recents_file, ["just-a-string", {"no_path_key": 1}, {"path": ""}])
        r = client.get("/api/setup/recent-projects")
        assert r.status_code == 200, r.text
        assert r.json()["projects"] == []


def test_conftest_wires_session_prune_teardown():
    """4-B: the suite self-cleans real dead recents via a session-scoped teardown that calls the
    prune endpoint logic, so test runs against real preflight/switch don't accumulate dead /tmp
    entries in the operator's recents file."""
    conftest_src = (Path(__file__).resolve().parent / "conftest.py").read_text(encoding="utf-8")
    assert 'scope="session"' in conftest_src, "a session-scoped fixture must exist"
    assert "post_setup_recent_projects_prune" in conftest_src, "teardown must call the prune logic"
