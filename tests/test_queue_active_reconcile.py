"""Stale queue ACTIVE vs pipeline_state.project_path — reconcile on mark-matching and trigger-next."""

import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _entry(name, project_abs, state, position, eid=None):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": eid or str(uuid.uuid4()),
        "project_path": project_abs,
        "idea_id": None,
        "name": name,
        "state": state,
        "position": position,
        "parent_id": None,
        "added_at": now,
        "started_at": now if state == "ACTIVE" else None,
        "completed_at": None,
        "blocked_at": None,
        "skip_count": 0,
        "preflight_validated_at": now,
        "notes": "",
    }


def _write_queue(path, entries, queue_mode="manual"):
    data = {
        "queue": entries,
        "queue_mode": queue_mode,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def _write_state(path, project_path, status="STOPPED"):
    with open(path, "w") as f:
        json.dump(
            {
                "project_path": project_path,
                "pipeline_status": status,
                "current_phase": 0,
                "current_agent": "planner",
            },
            f,
        )


@pytest.fixture
def paths(tmp_path):
    proj_a = tmp_path / "calculator"
    proj_b = tmp_path / "prefproj"
    proj_a.mkdir()
    proj_b.mkdir()
    queue_file = tmp_path / "pipeline_queue.json"
    state_file = tmp_path / "pipeline_state.json"
    return {
        "proj_a": str(proj_a),
        "proj_b": str(proj_b),
        "queue_file": queue_file,
        "state_file": state_file,
        "tmp": tmp_path,
    }


def test_demote_stale_active_on_mark_matching(paths, monkeypatch):
    from ui import server as srv

    proj_a = paths["proj_a"]
    proj_b = paths["proj_b"]
    qf = paths["queue_file"]
    sf = paths["state_file"]

    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    entries = [
        _entry("calculator", proj_a, "ACTIVE", 1, id_a),
        _entry("prefproj", proj_b, "READY", 2, id_b),
    ]
    _write_queue(qf, entries)
    _write_state(sf, proj_b)

    cfg = {
        "pipeline_queue_path": str(qf),
        "pipeline_state_path": str(sf),
    }
    monkeypatch.setattr(srv, "load_config", lambda _p=None: cfg)

    srv._queue_mark_matching_entry_active(cfg, proj_b)

    with open(qf) as f:
        q = json.load(f)
    by_name = {e["name"]: e for e in q["queue"]}
    assert by_name["calculator"]["state"] == "READY"
    assert by_name["calculator"].get("started_at") is None
    assert by_name["prefproj"]["state"] == "ACTIVE"
    assert by_name["prefproj"].get("started_at")
    assert by_name["prefproj"]["position"] == 1
    assert by_name["calculator"]["position"] == 2


def test_active_row_is_pinned_to_position_1_after_mark_matching(paths, monkeypatch):
    from ui import server as srv

    proj_a = paths["proj_a"]
    proj_b = paths["proj_b"]
    qf = paths["queue_file"]
    sf = paths["state_file"]

    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    entries = [
        _entry("calculator", proj_a, "READY", 1, id_a),
        _entry("prefproj", proj_b, "READY", 2, id_b),
    ]
    _write_queue(qf, entries)
    _write_state(sf, proj_b)

    cfg = {"pipeline_queue_path": str(qf), "pipeline_state_path": str(sf)}
    monkeypatch.setattr(srv, "load_config", lambda _p=None: cfg)

    srv._queue_mark_matching_entry_active(cfg, proj_b)

    with open(qf) as f:
        q = json.load(f)
    by_name = {e["name"]: e for e in q["queue"]}
    assert by_name["prefproj"]["state"] == "ACTIVE"
    assert by_name["prefproj"]["position"] == 1
    assert by_name["calculator"]["state"] == "READY"
    assert by_name["calculator"]["position"] == 2


def test_active_pin_preserves_relative_order_of_non_matching_rows(tmp_path, monkeypatch):
    from ui import server as srv

    da = tmp_path / "a"
    db = tmp_path / "b"
    dc = tmp_path / "c"
    dd = tmp_path / "d"
    for d in (da, db, dc, dd):
        d.mkdir()
    qf = tmp_path / "pipeline_queue.json"
    sf = tmp_path / "pipeline_state.json"
    ida, idb, idc, idd = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    entries = [
        _entry("a", str(da), "READY", 1, ida),
        _entry("b", str(db), "READY", 2, idb),
        _entry("c", str(dc), "READY", 3, idc),
        _entry("d", str(dd), "READY", 4, idd),
    ]
    _write_queue(qf, entries)
    _write_state(sf, str(dc))

    cfg = {"pipeline_queue_path": str(qf), "pipeline_state_path": str(sf)}
    monkeypatch.setattr(srv, "load_config", lambda _p=None: cfg)

    srv._queue_mark_matching_entry_active(cfg, str(dc))

    with open(qf) as f:
        q = json.load(f)
    ordered = sorted(q["queue"], key=lambda e: e["position"])
    assert [e["name"] for e in ordered] == ["c", "a", "b", "d"]
    assert ordered[0]["state"] == "ACTIVE"
    for i, e in enumerate(ordered, start=1):
        assert e["position"] == i


VALID_ROADMAP_PIN = (
    "- [ ] `TEST-E1` | LOW | Do the thing\n"
    "  > Test: It works.\n"
)
WORKSPACE_AGENTS_PIN = ["planner", "executor", "reviewer", "escalation"]
WORKSPACE_DOCS_PIN = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]


def _make_workspace_pin(base_dir, agent):
    ws = base_dir / f"workspace-{agent}"
    ws.mkdir(parents=True, exist_ok=True)
    for doc in WORKSPACE_DOCS_PIN:
        (ws / doc).write_text(f"# {doc}\n")


def _make_openclaw_dir_pin(tmp_path, repo_path):
    openclaw = tmp_path / ".openclaw"
    openclaw.mkdir(parents=True, exist_ok=True)
    link = openclaw / "pipeline-project"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(repo_path)
    for agent in WORKSPACE_AGENTS_PIN:
        _make_workspace_pin(openclaw, agent)
    return openclaw


def _mock_subprocess_preflight_pass_pin():
    from unittest.mock import MagicMock

    def _inner(cmd, **kwargs):
        mock = MagicMock()
        mock.stderr = ""
        if not isinstance(cmd, list) or not cmd:
            mock.returncode = 0
            mock.stdout = ""
            return mock
        if cmd[0] == "git" and len(cmd) >= 2 and cmd[1] == "--version":
            mock.returncode = 0
            mock.stdout = "git version 2.40.0\n"
            return mock
        if "branch" in cmd and "--list" in cmd:
            mock.returncode = 0
            mock.stdout = "  main\n"
            return mock
        if "symbolic-ref" in cmd:
            mock.returncode = 0
            mock.stdout = "main\n"
            return mock
        mock.returncode = 0
        mock.stdout = ""
        return mock

    return _inner


def test_switch_project_moves_active_row_to_position_1(tmp_path, monkeypatch):
    from ui.server import app

    calculator_repo = tmp_path / "calculator"
    prefproj_repo = tmp_path / "prefproj"
    for rp in (calculator_repo, prefproj_repo):
        rp.mkdir()
        (rp / "roadmap.md").write_text(VALID_ROADMAP_PIN, encoding="utf-8")
        (rp / ".git").mkdir()
        (rp / ".gitignore").write_text("*.pyc\n", encoding="utf-8")

    openclaw = _make_openclaw_dir_pin(tmp_path, calculator_repo)
    state = tmp_path / "pipeline_state.json"
    state.write_text(
        json.dumps(
            {
                "pipeline_status": "STOPPED",
                "project_path": str(calculator_repo),
            }
        ),
        encoding="utf-8",
    )
    qf = tmp_path / "pipeline_queue.json"
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    _write_queue(
        qf,
        [
            _entry("calculator", str(calculator_repo), "ACTIVE", 1, id_a),
            _entry("prefproj", str(prefproj_repo), "READY", 2, id_b),
        ],
    )

    cfg = {
        "pipeline_state_path": str(state),
        "pipeline_queue_path": str(qf),
        "openclaw_root": str(openclaw),
        "project_dir_path": str(openclaw / "pipeline-project"),
    }

    _real_expanduser = os.path.expanduser

    def _expanduser_test(p):
        return str(openclaw) if "openclaw" in str(p) else _real_expanduser(p)

    monkeypatch.setattr("ui.server.os.path.expanduser", _expanduser_test)
    monkeypatch.setattr("ui.server.load_config", lambda _p=None: cfg)

    with patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass_pin()), patch(
        "ui.server._spawn_orchestrator", return_value={"ok": True, "error": None}
    ), patch("ui.server._check_orchestrator_liveness", return_value=False), patch(
        "ui.server._preflight_materialize", return_value=[]
    ), patch(
        "ui.server._run_preflight_checks",
        return_value=[{"status": "ok", "check": "symlink", "message": ""}],
    ), patch(
        "ui.server._validate_project_coherence", return_value={"ok": True, "issues": []}
    ), patch("ui.server.append_recent_project", lambda *_a, **_k: None):
        r = TestClient(app).post(
            "/api/setup/switch-project",
            json={"repo_path": str(prefproj_repo), "start_orchestrator": True},
        )

    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True
    with open(qf) as f:
        q = json.load(f)
    by_name = {e["name"]: e for e in q["queue"]}
    assert by_name["prefproj"]["state"] == "ACTIVE"
    assert by_name["prefproj"]["position"] == 1
    assert by_name["calculator"]["state"] == "READY"
    assert by_name["calculator"]["position"] == 2


def test_get_queue_defensive_sort_pins_active_first(paths, monkeypatch):
    from ui.server import app

    proj_a = paths["proj_a"]
    proj_b = paths["proj_b"]
    qf = paths["queue_file"]
    sf = paths["state_file"]
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    _write_queue(
        qf,
        [
            _entry("calculator", proj_a, "READY", 1, id_a),
            _entry("prefproj", proj_b, "ACTIVE", 2, id_b),
        ],
    )
    _write_state(sf, proj_b)

    cfg = {"pipeline_queue_path": str(qf), "pipeline_state_path": str(sf)}
    monkeypatch.setattr("ui.server.load_config", lambda _p=None: cfg)

    r = TestClient(app).get("/api/queue")
    assert r.status_code == 200
    first = r.json()["queue"][0]
    assert os.path.realpath(first["project_path"]) == os.path.realpath(proj_b)


def test_get_queue_matching_pipeline_project_first_when_ingested(paths, monkeypatch):
    """Synthetic ingest row must sort before on-disk rows (realpath match)."""
    from ui.server import app

    proj_a = paths["proj_a"]
    proj_b = paths["proj_b"]
    qf = paths["queue_file"]
    sf = paths["state_file"]
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    _write_queue(
        qf,
        [
            _entry("calculator", proj_a, "READY", 1, id_a),
            _entry("prefproj", proj_b, "READY", 2, id_b),
        ],
    )
    orphan = paths["tmp"] / "orphan_active"
    orphan.mkdir()
    _write_state(sf, str(orphan))

    cfg = {"pipeline_queue_path": str(qf), "pipeline_state_path": str(sf)}
    monkeypatch.setattr("ui.server.load_config", lambda _p=None: cfg)

    r = TestClient(app).get("/api/queue")
    assert r.status_code == 200
    first = r.json()["queue"][0]
    assert os.path.realpath(first["project_path"]) == os.path.realpath(str(orphan))
    assert first.get("ingested") is True


def test_mark_matching_noop_when_no_matching_row_exists(tmp_path, monkeypatch):
    from ui import server as srv

    da = tmp_path / "a"
    db = tmp_path / "b"
    dz = tmp_path / "zproj"
    for d in (da, db, dz):
        d.mkdir()
    qf = tmp_path / "pipeline_queue.json"
    sf = tmp_path / "pipeline_state.json"
    ida, idb = str(uuid.uuid4()), str(uuid.uuid4())
    entries = [
        _entry("a", str(da), "READY", 1, ida),
        _entry("b", str(db), "READY", 2, idb),
    ]
    _write_queue(qf, entries)
    with open(qf) as f:
        q_before = json.load(f)

    _write_state(sf, str(dz))
    cfg = {"pipeline_queue_path": str(qf), "pipeline_state_path": str(sf)}
    monkeypatch.setattr(srv, "load_config", lambda _p=None: cfg)

    srv._queue_mark_matching_entry_active(cfg, str(dz))

    with open(qf) as f:
        q_after = json.load(f)
    assert q_after["queue"] == q_before["queue"]


def test_no_demote_when_canonical_invalid(paths, monkeypatch):
    from ui import server as srv

    proj_a = paths["proj_a"]
    qf = paths["queue_file"]
    entries = [_entry("calculator", proj_a, "ACTIVE", 1)]
    _write_queue(qf, entries)
    cfg = {"pipeline_queue_path": str(qf), "pipeline_state_path": str(paths["state_file"])}
    monkeypatch.setattr(srv, "load_config", lambda _p=None: cfg)

    changed = srv._queue_demote_stale_active_entries(cfg, "")
    assert changed is False
    with open(qf) as f:
        q = json.load(f)
    assert q["queue"][0]["state"] == "ACTIVE"


def test_trigger_next_unblocked_after_stale_active_demote(paths, monkeypatch):
    from ui.server import app

    proj_a = paths["proj_a"]
    proj_b = paths["proj_b"]
    qf = paths["queue_file"]
    sf = paths["state_file"]

    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    # prefproj first so trigger-next selects it after stale ACTIVE on calculator is demoted.
    entries = [
        _entry("prefproj", proj_b, "READY", 1, id_b),
        _entry("calculator", proj_a, "ACTIVE", 2, id_a),
    ]
    _write_queue(qf, entries, queue_mode="manual")
    _write_state(sf, proj_b)

    orch = paths["tmp"] / "orch"
    orch.mkdir()
    (orch / "orchestrator.py").write_text("# mock\n", encoding="utf-8")

    cfg = {
        "pipeline_queue_path": str(qf),
        "pipeline_state_path": str(sf),
        "phase_state_path": str(paths["tmp"] / "phase_state.json"),
        "lock_path": str(paths["tmp"] / "pipeline.lock"),
        "events_path": str(paths["tmp"] / "events.jsonl"),
        "ideas_dir": str(paths["tmp"] / "ideas"),
        "port": 18790,
        "autodev_repo_path": str(orch),
    }
    monkeypatch.setattr("ui.server.load_config", lambda _p=None: cfg)

    with patch("ui.server._run_preflight_checks", return_value=[{"status": "ok", "name": "x"}]), patch(
        "ui.server._spawn_orchestrator", return_value={"ok": True, "error": None}
    ):
        r = TestClient(app).post("/api/queue/trigger-next")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    with open(qf) as f:
        q = json.load(f)
    by_name = {e["name"]: e for e in q["queue"]}
    assert by_name["calculator"]["state"] == "READY"
    assert by_name["prefproj"]["state"] == "ACTIVE"
    assert by_name["prefproj"]["position"] == 1


# ---------------------------------------------------------------------------
# Defect C (C3) — the server demote/promote reconcile must SCRUB stale parked_*
# fields, matching the orchestrator's selection hygiene. A drifted row carrying
# leftover park metadata (state=READY/ACTIVE + parked_state_snapshot) must be
# cleaned on any non-revival state transition, or it stays inconsistent forever
# (the Minecraft drift). RED against the pre-fix server (which scrubbed none).
# ---------------------------------------------------------------------------

_PARKED_FIELDS = (
    "parked_state_snapshot", "parked_at", "parked_reason",
    "parked_pipeline_status", "answered_at",
)


def _with_parked(entry):
    """Attach a full set of stale park metadata to an entry (simulating drift)."""
    entry["parked_state_snapshot"] = {"current_phase_raw_id": "CORE-E1", "phase_base_commit": "abc123"}
    entry["parked_at"] = "2026-06-08T05:08:42+00:00"
    entry["parked_reason"] = "escalation"
    entry["parked_pipeline_status"] = "WAITING_FOR_HUMAN"
    entry["answered_at"] = "2026-06-08T06:00:00+00:00"
    return entry


def test_mark_matching_scrubs_parked_on_promote(paths, monkeypatch):
    from ui import server as srv

    proj_b = paths["proj_b"]
    qf = paths["queue_file"]
    sf = paths["state_file"]
    # The matching row is READY but carries leftover park metadata (drifted).
    entries = [_with_parked(_entry("prefproj", proj_b, "READY", 1, str(uuid.uuid4())))]
    _write_queue(qf, entries)
    _write_state(sf, proj_b)
    cfg = {"pipeline_queue_path": str(qf), "pipeline_state_path": str(sf)}
    monkeypatch.setattr(srv, "load_config", lambda _p=None: cfg)

    srv._queue_mark_matching_entry_active(cfg, proj_b)

    with open(qf) as f:
        row = json.load(f)["queue"][0]
    assert row["state"] == "ACTIVE"
    for k in _PARKED_FIELDS:
        assert k not in row, f"{k} should be scrubbed on promote to ACTIVE"


def test_mark_matching_scrubs_parked_on_demote(paths, monkeypatch):
    from ui import server as srv

    proj_a = paths["proj_a"]
    proj_b = paths["proj_b"]
    qf = paths["queue_file"]
    sf = paths["state_file"]
    # proj_a is a stale ACTIVE row (state wants proj_b) carrying leftover park metadata.
    entries = [
        _with_parked(_entry("calculator", proj_a, "ACTIVE", 1, str(uuid.uuid4()))),
        _entry("prefproj", proj_b, "READY", 2, str(uuid.uuid4())),
    ]
    _write_queue(qf, entries)
    _write_state(sf, proj_b)
    cfg = {"pipeline_queue_path": str(qf), "pipeline_state_path": str(sf)}
    monkeypatch.setattr(srv, "load_config", lambda _p=None: cfg)

    srv._queue_mark_matching_entry_active(cfg, proj_b)

    with open(qf) as f:
        by_name = {e["name"]: e for e in json.load(f)["queue"]}
    assert by_name["calculator"]["state"] == "READY"
    for k in _PARKED_FIELDS:
        assert k not in by_name["calculator"], f"{k} should be scrubbed on demote to READY"


def test_demote_stale_active_entries_scrubs_parked(paths, monkeypatch):
    from ui import server as srv

    proj_a = paths["proj_a"]
    proj_b = paths["proj_b"]
    qf = paths["queue_file"]
    sf = paths["state_file"]
    entries = [_with_parked(_entry("calculator", proj_a, "ACTIVE", 1, str(uuid.uuid4())))]
    _write_queue(qf, entries)
    cfg = {"pipeline_queue_path": str(qf), "pipeline_state_path": str(sf)}
    monkeypatch.setattr(srv, "load_config", lambda _p=None: cfg)

    changed = srv._queue_demote_stale_active_entries(cfg, proj_b)

    assert changed is True
    with open(qf) as f:
        row = json.load(f)["queue"][0]
    assert row["state"] == "READY"
    for k in _PARKED_FIELDS:
        assert k not in row, f"{k} should be scrubbed when demoting a stale ACTIVE row"
