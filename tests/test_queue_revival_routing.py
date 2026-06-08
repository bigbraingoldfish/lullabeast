"""Defect C (C4) — resume/switch route a PARKED target through the orchestrator's
--revive path instead of a bare ACTIVE promote.

When /api/resume-orchestrator or /api/setup/switch-project targets a project whose queue
entry is ESCALATION / ESCALATION_ANSWERED, the server must spawn with revive_entry_id (so
apply_cli_revive restores the escalated phase + applies any banked command) and must NOT:
  - write a phase-0 pipeline_state.json (switch), nor
  - bare-promote via _queue_mark_matching_entry_active.
For a non-parked (READY) target, behavior is unchanged. These assertions are RED against the
pre-fix endpoints (which always bare-spawned + marked-matching).

Hermetic: TestClient + patched load_config; all paths under tmp_path.
"""
import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from ui.server import app


def _entry(name, project_abs, state, position=1, eid=None):
    return {
        "id": eid or str(uuid.uuid4()),
        "project_path": project_abs,
        "name": name,
        "state": state,
        "position": position,
        "parent_id": None,
        "started_at": None,
        "skip_count": 0,
    }


def _write_queue(path, entries):
    with open(path, "w") as f:
        json.dump({"queue": entries, "queue_mode": "manual", "queue_version": 1}, f)


def _orch_dir(tmp_path):
    orch = tmp_path / "oc"
    orch.mkdir()
    (orch / "orchestrator.py").write_text("# mock\n", encoding="utf-8")
    return orch


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def _resume_cfg(tmp_path, project, state_status="STOPPED"):
    project.mkdir()
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(
        json.dumps({"project_path": str(project), "pipeline_status": state_status}),
        encoding="utf-8",
    )
    qf = tmp_path / "pipeline_queue.json"
    lock = tmp_path / "pipeline.lock"
    lock.write_text("", encoding="utf-8")
    cfg = {
        "pipeline_state_path": str(state_path),
        "pipeline_queue_path": str(qf),
        "autodev_repo_path": str(_orch_dir(tmp_path)),
        "lock_path": str(lock),
    }
    return cfg, qf


def test_resume_parked_escalation_routes_through_revive(tmp_path):
    project = tmp_path / "mc"
    cfg, qf = _resume_cfg(tmp_path, project)
    eid = str(uuid.uuid4())
    _write_queue(qf, [_entry("mc", str(project), "ESCALATION", 1, eid)])

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False), \
         patch("ui.server._spawn_orchestrator", return_value={"ok": True, "error": None}) as spawn, \
         patch("ui.server._queue_mark_matching_entry_active") as mark:
        r = TestClient(app).post("/api/resume-orchestrator")

    assert r.status_code == 200, r.text
    assert spawn.call_args.kwargs.get("revive_entry_id") == eid
    mark.assert_not_called()


def test_resume_answered_escalation_routes_through_revive(tmp_path):
    project = tmp_path / "mc"
    cfg, qf = _resume_cfg(tmp_path, project)
    eid = str(uuid.uuid4())
    _write_queue(qf, [_entry("mc", str(project), "ESCALATION_ANSWERED", 1, eid)])

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False), \
         patch("ui.server._spawn_orchestrator", return_value={"ok": True, "error": None}) as spawn, \
         patch("ui.server._queue_mark_matching_entry_active") as mark:
        r = TestClient(app).post("/api/resume-orchestrator")

    assert r.status_code == 200, r.text
    assert spawn.call_args.kwargs.get("revive_entry_id") == eid
    mark.assert_not_called()


def test_resume_ready_entry_does_not_revive_and_marks_matching(tmp_path):
    project = tmp_path / "mc"
    cfg, qf = _resume_cfg(tmp_path, project)
    _write_queue(qf, [_entry("mc", str(project), "READY", 1, str(uuid.uuid4()))])

    with patch("ui.server.load_config", return_value=cfg), \
         patch("ui.server._check_orchestrator_liveness", return_value=False), \
         patch("ui.server._spawn_orchestrator", return_value={"ok": True, "error": None}) as spawn, \
         patch("ui.server._queue_mark_matching_entry_active") as mark:
        r = TestClient(app).post("/api/resume-orchestrator")

    assert r.status_code == 200, r.text
    assert spawn.call_args.kwargs.get("revive_entry_id") is None
    mark.assert_called_once()


# ---------------------------------------------------------------------------
# Switch-project (mirrors test_queue_active_reconcile.test_switch_project_*)
# ---------------------------------------------------------------------------

VALID_ROADMAP = "- [ ] `TEST-E1` | LOW | Do the thing\n  > Test: It works.\n"
WORKSPACE_AGENTS = ["planner", "executor", "reviewer", "escalation"]
WORKSPACE_DOCS = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]


def _make_openclaw(tmp_path, repo_path):
    openclaw = tmp_path / ".openclaw"
    openclaw.mkdir(parents=True, exist_ok=True)
    link = openclaw / "pipeline-project"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(repo_path)
    for agent in WORKSPACE_AGENTS:
        ws = openclaw / f"workspace-{agent}"
        ws.mkdir(parents=True, exist_ok=True)
        for doc in WORKSPACE_DOCS:
            (ws / doc).write_text(f"# {doc}\n")
    return openclaw


def _switch_repo(tmp_path, name):
    repo = tmp_path / name
    repo.mkdir()
    (repo / "roadmap.md").write_text(VALID_ROADMAP, encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    return repo


def _switch_ctx(repo_abs, state_project, qf, state_path, openclaw):
    """The common patch stack for driving switch-project to the spawn point."""
    cfg = {
        "pipeline_state_path": str(state_path),
        "pipeline_queue_path": str(qf),
        "openclaw_root": str(openclaw),
        "project_dir_path": str(openclaw / "pipeline-project"),
    }
    return cfg


def test_switch_parked_escalation_skips_state_write_and_revives(tmp_path, monkeypatch):
    repo = _switch_repo(tmp_path, "mc")
    openclaw = _make_openclaw(tmp_path, repo)
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(json.dumps({"pipeline_status": "STOPPED", "project_path": str(repo)}), encoding="utf-8")
    qf = tmp_path / "pipeline_queue.json"
    eid = str(uuid.uuid4())
    _write_queue(qf, [_entry("mc", str(repo), "ESCALATION", 1, eid)])

    cfg = {
        "pipeline_state_path": str(state_path),
        "pipeline_queue_path": str(qf),
        "openclaw_root": str(openclaw),
        "project_dir_path": str(openclaw / "pipeline-project"),
    }
    monkeypatch.setattr("ui.server.load_config", lambda _p=None: cfg)

    with patch("ui.server._spawn_orchestrator", return_value={"ok": True, "error": None}) as spawn, \
         patch("ui.server._check_orchestrator_liveness", return_value=False), \
         patch("ui.server._preflight_materialize", return_value=[]), \
         patch("ui.server._run_preflight_checks", return_value=[{"status": "ok", "check": "symlink", "message": ""}]), \
         patch("ui.server._validate_project_coherence", return_value={"ok": True, "issues": []}), \
         patch("ui.server.append_recent_project", lambda *_a, **_k: None), \
         patch("ui.server._write_json_atomic") as wj, \
         patch("ui.server._queue_mark_matching_entry_active") as mark:
        r = TestClient(app).post(
            "/api/setup/switch-project",
            json={"repo_path": str(repo), "start_orchestrator": True},
        )

    assert r.status_code == 200, r.text
    # revive path: spawned with the entry id, no phase-0 state write, no bare promote.
    assert spawn.call_args.kwargs.get("revive_entry_id") == eid
    state_writes = [c for c in wj.call_args_list if str(state_path) in str(c.args[0])]
    assert not state_writes, "phase-0 pipeline_state.json must NOT be written on a parked-revival switch"
    mark.assert_not_called()


def test_switch_ready_entry_writes_state_and_marks_matching(tmp_path, monkeypatch):
    repo = _switch_repo(tmp_path, "fresh")
    openclaw = _make_openclaw(tmp_path, repo)
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(json.dumps({"pipeline_status": "STOPPED", "project_path": str(repo)}), encoding="utf-8")
    qf = tmp_path / "pipeline_queue.json"
    _write_queue(qf, [_entry("fresh", str(repo), "READY", 1, str(uuid.uuid4()))])

    cfg = {
        "pipeline_state_path": str(state_path),
        "pipeline_queue_path": str(qf),
        "openclaw_root": str(openclaw),
        "project_dir_path": str(openclaw / "pipeline-project"),
    }
    monkeypatch.setattr("ui.server.load_config", lambda _p=None: cfg)

    with patch("ui.server._spawn_orchestrator", return_value={"ok": True, "error": None}) as spawn, \
         patch("ui.server._check_orchestrator_liveness", return_value=False), \
         patch("ui.server._preflight_materialize", return_value=[]), \
         patch("ui.server._run_preflight_checks", return_value=[{"status": "ok", "check": "symlink", "message": ""}]), \
         patch("ui.server._validate_project_coherence", return_value={"ok": True, "issues": []}), \
         patch("ui.server.append_recent_project", lambda *_a, **_k: None), \
         patch("ui.server._write_json_atomic") as wj, \
         patch("ui.server._queue_mark_matching_entry_active") as mark:
        r = TestClient(app).post(
            "/api/setup/switch-project",
            json={"repo_path": str(repo), "start_orchestrator": True},
        )

    assert r.status_code == 200, r.text
    assert spawn.call_args.kwargs.get("revive_entry_id") is None
    state_writes = [c for c in wj.call_args_list if str(state_path) in str(c.args[0])]
    assert state_writes, "fresh-start switch must write phase-0 pipeline_state.json"
    mark.assert_called_once()
