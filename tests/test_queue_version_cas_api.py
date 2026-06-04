"""F9 — queue optimistic-concurrency (version-CAS) tests, UI-server side.

TDD: written before the implementation. These pin the version-stamping contract on
``_write_queue_file`` and the read→apply→compare-and-swap→retry behaviour shared with the
orchestrator (wired into the server as ``_mutate_queue_file`` / ``_peek_queue_version``),
including the spawn-exactly-once guard on ``_queue_run_trigger_next_logic`` and the bounded
retry → HTTP 503 surface.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.server import app  # noqa: E402
import ui.server as srv  # noqa: E402
from autodev.pipeline.queue_semantics import read_queue_version  # noqa: E402


def _make_entry(name, state="READY", position=1, parent_id=None, entry_id=None, project_path=None):
    return {
        "id": entry_id or str(uuid.uuid4()),
        "project_path": project_path or f"/tmp/project_{name}",
        "idea_id": None,
        "name": name,
        "state": state,
        "position": position,
        "parent_id": parent_id,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
        "blocked_at": None,
        "skip_count": 0,
        "preflight_validated_at": datetime.now(timezone.utc).isoformat(),
        "notes": "",
    }


def _write_queue(path, entries, queue_mode="auto", queue_version=None):
    """Seed a queue file. Omits queue_version by default to model a legacy file."""
    data = {
        "queue": entries,
        "queue_mode": queue_mode,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    if queue_version is not None:
        data["queue_version"] = queue_version
    with open(str(path), "w") as f:
        json.dump(data, f)
    return data


@pytest.fixture
def client(tmp_path, monkeypatch):
    queue_file = tmp_path / "pipeline_queue.json"
    pipeline_state_file = tmp_path / "pipeline_state.json"

    def mock_load_config(_config_path=None):
        return {
            "pipeline_queue_path": str(queue_file),
            "pipeline_state_path": str(pipeline_state_file),
            "phase_state_path": str(tmp_path / "phase_state.json"),
            "project_dir_path": str(tmp_path / "pipeline-project"),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "events_path": str(tmp_path / "pipeline_events.jsonl"),
            "ideas_dir": str(tmp_path / "ideas"),
            "port": 18790,
        }

    monkeypatch.setattr("ui.server.load_config", mock_load_config)
    monkeypatch.setattr("ui.server.append_recent_project", lambda _repo_abs: None)
    return TestClient(app), queue_file, tmp_path


# ---------------------------------------------------------------------------
# S1/S2/S3 — version stamping + legacy tolerance
# ---------------------------------------------------------------------------

class TestServerVersionStamping:
    def test_write_queue_file_stamps_version_from_zero(self, tmp_path):
        """S1: a write to a versionless (legacy) payload lands queue_version == 1."""
        p = tmp_path / "pipeline_queue.json"
        srv._write_queue_file(str(p), {"queue": [], "queue_mode": "auto", "last_updated": ""})
        assert json.loads(p.read_text())["queue_version"] == 1

    def test_write_queue_file_increments_version(self, tmp_path):
        """S2: monotonic increment — base 7 -> 8."""
        p = tmp_path / "pipeline_queue.json"
        srv._write_queue_file(str(p), {"queue": [], "queue_mode": "auto", "queue_version": 7})
        assert json.loads(p.read_text())["queue_version"] == 8

    def test_endpoints_tolerate_legacy_file_without_version(self, client):
        """S3: a legacy file (no queue_version) is readable, and a mutation stamps v1."""
        c, queue_file, _ = client
        _write_queue(str(queue_file), [_make_entry("a", position=1)])  # no queue_version
        assert c.get("/api/queue").status_code == 200
        resp = c.post("/api/queue/clear", json={})
        assert resp.status_code == 200
        on_disk = json.loads(queue_file.read_text())
        assert on_disk["queue"] == []
        assert on_disk["queue_version"] == 1


# ---------------------------------------------------------------------------
# S4 — no lost update across a UI add interleaved with an orchestrator COMPLETED
# ---------------------------------------------------------------------------

class TestServerNoLostUpdate:
    def test_add_cas_conflict_preserves_both(self, client, monkeypatch, tmp_path):
        """S4: while POST /api/queue/add is writing, a concurrent orchestrator marks an
        existing row COMPLETED and bumps the version. The CAS retry must re-apply the add
        onto the fresh base so BOTH the COMPLETED transition and the new row survive."""
        c, queue_file, _ = client
        existing = _make_entry("existing", state="ACTIVE", position=1)
        _write_queue(str(queue_file), [existing], queue_version=0)
        proj = tmp_path / "newproj"
        proj.mkdir()
        monkeypatch.setattr(
            "ui.server._run_preflight_checks",
            lambda p: [{"check": "symlink", "status": "pass", "message": "ok"}],
        )

        calls = {"n": 0}
        real_peek = srv._peek_queue_version

        def fake_peek(config):
            calls["n"] += 1
            if calls["n"] == 1:
                cur = json.loads(queue_file.read_text())
                cur["queue"][0]["state"] = "COMPLETED"  # the concurrent orchestrator write
                cur["queue_version"] = read_queue_version(cur) + 1
                queue_file.write_text(json.dumps(cur))
                return cur["queue_version"]  # != base -> conflict once
            return real_peek(config)

        monkeypatch.setattr(srv, "_peek_queue_version", fake_peek)

        resp = c.post("/api/queue/add", json={"project_path": str(proj)})
        assert resp.status_code == 200

        final = json.loads(queue_file.read_text())
        by_name = {e["name"]: e for e in final["queue"]}
        assert by_name["existing"]["state"] == "COMPLETED"  # concurrent update survived
        assert "newproj" in by_name                          # our add survived
        assert final["queue_version"] == 2                   # 0 -> (concurrent) 1 -> (ours) 2


# ---------------------------------------------------------------------------
# S5 — trigger-next: spawn exactly once, AFTER a committed ACTIVE write, under conflict
# ---------------------------------------------------------------------------

class TestTriggerNextSpawnOnce:
    def test_spawns_once_after_active_commit_under_conflict(self, tmp_path, monkeypatch):
        queue_file = tmp_path / "pipeline_queue.json"
        config = {
            "pipeline_queue_path": str(queue_file),
            "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
            "lock_path": str(tmp_path / "pipeline.lock"),
        }
        proj = tmp_path / "p"
        proj.mkdir()
        entry = _make_entry("p", state="READY", position=1, project_path=str(proj))
        _write_queue(str(queue_file), [entry], queue_version=0)

        monkeypatch.setattr(
            srv, "_run_preflight_checks",
            lambda p: [{"check": "x", "status": "pass", "message": "ok"}],
        )
        monkeypatch.setattr(srv, "_queue_demote_stale_active_from_pipeline_state", lambda config: False)

        spawn_calls = {"n": 0}

        def fake_spawn(path, cfg=None):
            spawn_calls["n"] += 1
            # The ACTIVE write must already be committed on disk before spawning.
            assert json.loads(queue_file.read_text())["queue"][0]["state"] == "ACTIVE"
            return {"ok": True, "error": None}

        monkeypatch.setattr(srv, "_spawn_orchestrator", fake_spawn)

        calls = {"n": 0}
        real_peek = srv._peek_queue_version

        def fake_peek(config):
            calls["n"] += 1
            if calls["n"] == 1:
                cur = json.loads(queue_file.read_text())
                cur["queue_version"] = read_queue_version(cur) + 1
                queue_file.write_text(json.dumps(cur))
                return cur["queue_version"]  # conflict once at the set-ACTIVE commit
            return real_peek(config)

        monkeypatch.setattr(srv, "_peek_queue_version", fake_peek)

        result = srv._queue_run_trigger_next_logic(config)
        assert result.get("ok") is True
        assert result.get("started") == "p"
        assert spawn_calls["n"] == 1


# ---------------------------------------------------------------------------
# S6 — read path is untouched (mirror of the existing 187/757 byte-equality guards)
# ---------------------------------------------------------------------------

class TestReadPathDoesNotWrite:
    def test_get_queue_does_not_rewrite(self, client):
        c, queue_file, _ = client
        _write_queue(str(queue_file), [_make_entry("a", position=1)], queue_version=2)
        before = queue_file.read_bytes()
        assert c.get("/api/queue").status_code == 200
        assert queue_file.read_bytes() == before


# ---------------------------------------------------------------------------
# S7 — bounded retry → HTTP 503 (not a hang)
# ---------------------------------------------------------------------------

class TestBoundedRetryHttpSurface:
    def test_perpetual_conflict_returns_503(self, client, monkeypatch):
        c, queue_file, _ = client
        _write_queue(str(queue_file), [_make_entry("a", position=1)], queue_version=0)
        # Every pre-write version check disagrees with the read base -> never commits.
        monkeypatch.setattr(srv, "_peek_queue_version", lambda config: 10 ** 9)
        resp = c.post("/api/queue/clear", json={})
        assert resp.status_code == 503
