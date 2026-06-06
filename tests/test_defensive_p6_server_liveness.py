"""Defensive Hardening Phase 6 — Group 3: server spawn hardening + liveness guards + write-before-spawn.

TDD (tests before implementation):
  * T6.1 (server side) — ``_spawn_orchestrator`` wraps Popen in try/except OSError (no uncaught
    500), closes the parent's log fd (no per-spawn leak), and supports an opt-in ``confirm_lock``
    that polls ``_check_orchestrator_liveness`` post-spawn. ``confirm_lock`` defaults False so the
    fire-and-forget autostart path is never blocked/failed by a slow-but-healthy child (B1).
  * T6.2 — liveness-409 guards on trigger-next, git-recover, delete-ACTIVE and clear-while-live.
  * T6.3 — launch / switch-project write pipeline_state.json BEFORE spawning (write-then-act); a
    pre-spawn write failure aborts the spawn.
"""
import json
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui import server
from ui.server import app


# ===========================================================================
# T6.1 — _spawn_orchestrator hardening + confirm_lock
# ===========================================================================

def _mk_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "autodev" / "pipeline").mkdir(parents=True)
    (repo / "autodev" / "pipeline" / "orchestrator.py").write_text("# mock\n")
    return repo


def _spawn_cfg(tmp_path, repo, lock_path=None):
    (tmp_path / "oc").mkdir(exist_ok=True)
    (tmp_path / "ppl").mkdir(exist_ok=True)  # log dir must exist for the real log open()
    cfg = {
        "autodev_repo_path": str(repo),
        "openclaw_root": str(tmp_path / "oc"),
        "autodev_pipeline_root": str(tmp_path / "ppl"),
        "orchestrator_log_path": str(tmp_path / "orch.log"),
    }
    if lock_path is not None:
        cfg["lock_path"] = str(lock_path)
    return cfg


class _FakePopen:
    """Captures the stdout file object passed to Popen so the test can assert it gets closed."""
    last_stdout = None

    def __init__(self, *a, stdout=None, **k):
        _FakePopen.last_stdout = stdout


class TestSpawnHardening:
    def test_success_closes_log_fd_and_returns_ok(self, tmp_path):
        repo = _mk_repo(tmp_path)
        cfg = _spawn_cfg(tmp_path, repo)
        with patch("subprocess.Popen", _FakePopen):
            result = server._spawn_orchestrator(str(tmp_path / "proj"), config=cfg)
        assert result["ok"] is True
        assert _FakePopen.last_stdout is not None
        assert _FakePopen.last_stdout.closed is True, "parent log fd must be closed (no leak)"

    def test_oserror_returns_not_ok_and_does_not_raise(self, tmp_path):
        repo = _mk_repo(tmp_path)
        cfg = _spawn_cfg(tmp_path, repo)
        with patch("subprocess.Popen", side_effect=OSError("EAGAIN: cannot fork")):
            result = server._spawn_orchestrator(str(tmp_path / "proj"), config=cfg)
        assert result["ok"] is False
        assert "EAGAIN" in (result.get("error") or "") or "start orchestrator" in (result.get("error") or "")

    def test_confirm_lock_polls_until_alive(self, tmp_path, monkeypatch):
        repo = _mk_repo(tmp_path)
        lock = tmp_path / "pipeline.lock"
        cfg = _spawn_cfg(tmp_path, repo, lock_path=lock)
        monkeypatch.setattr(server, "_SPAWN_LOCK_CONFIRM_POLL", 0.0)
        live = MagicMock(side_effect=[False, False, True])
        monkeypatch.setattr(server, "_check_orchestrator_liveness", live)
        with patch("subprocess.Popen", _FakePopen):
            result = server._spawn_orchestrator(str(tmp_path / "proj"), config=cfg, confirm_lock=True)
        assert result["ok"] is True
        assert live.call_count >= 3  # polled until the child took the lock

    def test_confirm_lock_timeout_returns_not_ok(self, tmp_path, monkeypatch):
        repo = _mk_repo(tmp_path)
        lock = tmp_path / "pipeline.lock"
        cfg = _spawn_cfg(tmp_path, repo, lock_path=lock)
        monkeypatch.setattr(server, "_SPAWN_LOCK_CONFIRM_POLL", 0.0)
        monkeypatch.setattr(server, "_SPAWN_LOCK_CONFIRM_TIMEOUT", 0.15)
        monkeypatch.setattr(server, "_check_orchestrator_liveness", lambda _lp: False)
        with patch("subprocess.Popen", _FakePopen):
            result = server._spawn_orchestrator(str(tmp_path / "proj"), config=cfg, confirm_lock=True)
        assert result["ok"] is False
        assert "lock" in (result.get("error") or "").lower()

    def test_default_confirm_lock_is_false_no_polling(self, tmp_path, monkeypatch):
        """The default path (autostart) must NOT poll liveness — confirm_lock defaults False."""
        repo = _mk_repo(tmp_path)
        cfg = _spawn_cfg(tmp_path, repo, lock_path=tmp_path / "pipeline.lock")
        live = MagicMock(return_value=False)
        monkeypatch.setattr(server, "_check_orchestrator_liveness", live)
        with patch("subprocess.Popen", _FakePopen):
            result = server._spawn_orchestrator(str(tmp_path / "proj"), config=cfg)
        assert result["ok"] is True
        live.assert_not_called()


# ===========================================================================
# T6.2 — liveness-409 guards
# ===========================================================================

def _queue_cfg(tmp_path):
    qf = tmp_path / "pipeline_queue.json"
    sf = tmp_path / "pipeline_state.json"
    return {
        "pipeline_queue_path": str(qf),
        "pipeline_state_path": str(sf),
        "lock_path": str(tmp_path / "pipeline.lock"),
    }, qf, sf


def _write_q(path, entries):
    path.write_text(json.dumps({"queue": entries, "queue_mode": "manual", "last_updated": ""}))


def _entry(name, state="READY", position=1, project_path="/tmp/p"):
    return {"id": name, "project_path": project_path, "name": name, "state": state,
            "position": position, "parent_id": None, "skip_count": 0}


class TestLivenessGuards:
    def test_trigger_next_409_when_orchestrator_live(self, tmp_path):
        cfg, qf, _sf = _queue_cfg(tmp_path)
        _write_q(qf, [_entry("a", state="READY")])
        client = TestClient(app)
        spawn = MagicMock(return_value={"ok": True, "error": None})
        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=True), \
             patch("ui.server._spawn_orchestrator", spawn):
            resp = client.post("/api/queue/trigger-next")
        assert resp.status_code == 409
        spawn.assert_not_called()

    def test_git_recover_409_when_orchestrator_live(self, tmp_path):
        proj = tmp_path / "project"
        proj.mkdir()
        cfg = {
            "project_dir_path": str(proj),
            "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
            "base_branch": "",
            "lock_path": str(tmp_path / "pipeline.lock"),
        }
        client = TestClient(app)
        ran = MagicMock()
        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=True), \
             patch("ui.server.subprocess.run", ran):
            resp = client.post("/api/pipeline/git-recover", json={})
        assert resp.status_code == 409
        ran.assert_not_called()  # no git stash/checkout while an orchestrator is live

    def test_delete_active_409_when_live(self, tmp_path):
        cfg, qf, _sf = _queue_cfg(tmp_path)
        _write_q(qf, [_entry("a", state="ACTIVE")])
        client = TestClient(app)
        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=True):
            resp = client.request("DELETE", "/api/queue/a")
        assert resp.status_code == 409

    def test_clear_409_when_live_without_force(self, tmp_path):
        cfg, qf, _sf = _queue_cfg(tmp_path)
        # READY (not ACTIVE) isolates the NEW liveness guard from the existing ACTIVE-row check.
        _write_q(qf, [_entry("a", state="READY")])
        client = TestClient(app)
        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=True):
            resp = client.post("/api/queue/clear", json={})
        assert resp.status_code == 409
        # force still clears even while live
        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=True):
            resp2 = client.post("/api/queue/clear", json={"force": True})
        assert resp2.status_code == 200


# ===========================================================================
# T6.1 (B1) — autostart path must not request confirmation
# ===========================================================================

class TestAutostartDoesNotConfirm:
    def test_trigger_next_logic_spawns_without_confirm_lock(self, tmp_path):
        cfg, qf, sf = _queue_cfg(tmp_path)
        _write_q(qf, [_entry("a", state="READY", project_path=str(tmp_path / "a"))])
        (tmp_path / "a").mkdir()
        spawn = MagicMock(return_value={"ok": True, "error": None})
        with patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server._run_preflight_checks", return_value=[{"check": "x", "status": "pass", "message": ""}]), \
             patch("ui.server._spawn_orchestrator", spawn):
            out = server._queue_run_trigger_next_logic(cfg)
        assert out.get("ok") is True
        # B1 — the fire-and-forget autostart spawn must NOT pass confirm_lock=True.
        assert spawn.call_args.kwargs.get("confirm_lock") is not True


# ===========================================================================
# T6.3 — write state before spawn (launch / switch); confirm_lock=True wiring
# ===========================================================================

class TestWriteBeforeSpawn:
    def test_launch_writes_state_before_spawn_and_confirms(self, tmp_path):
        order = []
        repo = tmp_path / "myproject"
        cfg = {
            "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "autodev_repo_path": str(tmp_path / "orch"),
            "pipeline_queue_path": str(tmp_path / "pipeline_queue.json"),
        }
        (tmp_path / "orch").mkdir()

        def _rec_write(path, data):
            order.append("write")

        def _rec_spawn(*a, **k):
            order.append("spawn")
            assert "write" in order, "state must be written BEFORE spawn"
            assert k.get("confirm_lock") is True, "launch must request post-spawn lock confirmation"
            return {"ok": True, "error": None}

        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._run_init_project", return_value={"ok": True}), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server._clean_pipeline_state_for_project", return_value={"pipeline_status": "RUNNING"}), \
             patch("ui.server._write_json_atomic", side_effect=_rec_write), \
             patch("ui.server._queue_mark_matching_entry_active"), \
             patch("ui.server._spawn_orchestrator", side_effect=_rec_spawn):
            client = TestClient(app)
            resp = client.post("/api/setup/launch", json={"repo_path": str(repo), "roadmap_seed": "x"})
        assert resp.status_code == 200
        assert order[:2] == ["write", "spawn"]

    def test_launch_pre_spawn_write_failure_aborts_spawn(self, tmp_path):
        repo = tmp_path / "myproject"
        cfg = {
            "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "autodev_repo_path": str(tmp_path / "orch"),
        }
        (tmp_path / "orch").mkdir()
        spawn = MagicMock(return_value={"ok": True, "error": None})
        with patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._run_init_project", return_value={"ok": True}), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server._clean_pipeline_state_for_project", return_value={"pipeline_status": "RUNNING"}), \
             patch("ui.server._write_json_atomic", side_effect=OSError("disk full")), \
             patch("ui.server._spawn_orchestrator", spawn):
            client = TestClient(app)
            resp = client.post("/api/setup/launch", json={"repo_path": str(repo), "roadmap_seed": "x"})
        body = resp.json()
        assert body.get("ok") is False
        spawn.assert_not_called()  # pre-spawn write failure must abort the spawn
